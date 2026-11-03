"""Tests for list_unmerged_branches.py. Run: python3 -m unittest discover -s git"""

import base64
import contextlib
import io
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import list_unmerged_branches as lub


class FakeHttp:
    """Answers GETs from a dict keyed by the full URL (query included). A missing URL is a 404."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        self.calls.append(url)
        if url not in self.routes:
            raise lub.ApiError(f"GET {url} failed: HTTP 404", 404)
        answer = self.routes[url]
        return answer if isinstance(answer, tuple) else (answer, {})


def query(url, **params):
    return f"{url}?{urllib.parse.urlencode(params)}"


def parse_error(argv):
    with contextlib.redirect_stderr(io.StringIO()) as stderr, expect_exit():
        lub.parse_args(argv)
    return stderr.getvalue()


@contextlib.contextmanager
def expect_exit():
    try:
        yield
    except SystemExit:
        return
    raise AssertionError("expected SystemExit")


class ParseArgsTest(unittest.TestCase):
    def test_each_provider_needs_its_own_option(self):
        self.assertIn("--provider github needs --organization", parse_error(["--provider", "github"]))
        self.assertIn("--provider gitlab needs --groups", parse_error(["--provider", "gitlab"]))
        self.assertIn("--provider bitbucket needs --workspaces", parse_error(["--provider", "bitbucket"]))

    def test_another_providers_option_is_refused(self):
        error = parse_error(["--provider", "gitlab", "--groups", "g", "--organization", "o"])
        self.assertIn("--organization doesn't apply to --provider gitlab", error)

    def test_gitea_needs_url_and_github_refuses_it(self):
        self.assertIn("needs --url", parse_error(["--provider", "gitea", "--organization", "o"]))
        self.assertIn("gitlab and gitea only", parse_error(["--provider", "github", "--organization", "o", "--url", "x"]))

    def test_defaults_and_several_namespaces(self):
        args = lub.parse_args(["--provider", "bitbucket", "--workspaces", "a", "b"])
        self.assertEqual(args.workspaces, ["a", "b"])
        self.assertEqual((args.prefix, args.base, args.json), ("feature/", "main", False))
        self.assertEqual(lub.parse_args(["--provider", "github", "--organization", "o", "--prefix", ""]).prefix, "")


class PaginationTest(unittest.TestCase):
    def test_next_link(self):
        header = '<https://x/2>; rel="next", <https://x/9>; rel="last"'
        self.assertEqual(lub.next_link({"Link": header}), "https://x/2")
        self.assertIsNone(lub.next_link({"Link": '<https://x/1>; rel="prev"'}))
        self.assertIsNone(lub.next_link({}))

    def test_paginate_link_follows_every_page(self):
        http = FakeHttp({
            "https://x/items?per_page=2": ([1, 2], {"Link": '<https://x/items?page=2>; rel="next"'}),
            "https://x/items?page=2": ([3], {}),
        })
        self.assertEqual(lub.paginate_link(http, "https://x/items", {"per_page": 2}), [1, 2, 3])

    def test_paginate_next_follows_every_page(self):
        http = FakeHttp({
            "https://x/items?pagelen=2": {"values": [1, 2], "next": "https://x/items?page=2"},
            "https://x/items?page=2": {"values": [3]},
        })
        self.assertEqual(lub.paginate_next(http, "https://x/items", {"pagelen": 2}), [1, 2, 3])


GH = "https://api.github.com"


class GitHubTest(unittest.TestCase):
    def routes(self):
        def compare(branch, ahead, name="Ana"):
            url = f"{GH}/repos/org/app/compare/main...{branch}"
            commits = [{"commit": {"author": {"date": "2026-10-30T10:00:00Z", "name": name}}}] if ahead else []
            return url, {"ahead_by": ahead, "commits": commits}

        def pulls(branch, merged):
            url = query(f"{GH}/repos/org/app/pulls", state="closed", head=f"org:{branch}", per_page=100)
            return url, [{"merged_at": "2026-10-31T00:00:00Z" if merged else None}]

        return dict([
            (query(f"{GH}/orgs/org/repos", per_page=100, type="all"),
             [{"full_name": "org/app"}, {"full_name": "org/empty"}]),
            (query(f"{GH}/repos/org/app/branches", per_page=100),
             [{"name": n} for n in ("main", "feature/open", "feature/squashed", "feature/done", "fix/other")]),
            (query(f"{GH}/repos/org/empty/branches", per_page=100), [{"name": "develop"}]),
            compare("feature/open", 2),
            compare("feature/squashed", 3),
            compare("feature/done", 0),
            pulls("feature/open", merged=False),
            pulls("feature/squashed", merged=True),
        ])

    def test_lists_only_unmerged_prefixed_branches(self):
        http = FakeHttp(self.routes())
        rows, skipped, errors = lub.scan(lub.GitHub(http), ["org"], "feature/", "main")
        self.assertEqual(rows, [lub.Unmerged("org/app", "feature/open", 2, "2026-10-30T10:00:00Z", "Ana")])
        self.assertEqual((skipped, errors), (["org/empty"], []))
        self.assertFalse(any("fix/other" in call for call in http.calls))

    def test_falls_back_to_user_repos(self):
        http = FakeHttp({
            query(f"{GH}/users/someone/repos", per_page=100, type="all"): [{"full_name": "someone/app"}],
        })
        self.assertEqual(lub.GitHub(http).repos("someone"), [lub.Repo("someone/app", "someone/app")])

    def test_a_failing_repo_is_reported_and_the_rest_still_scanned(self):
        routes = self.routes()
        del routes[query(f"{GH}/repos/org/empty/branches", per_page=100)]
        rows, _, errors = lub.scan(lub.GitHub(FakeHttp(routes)), ["org"], "feature/", "main")
        self.assertEqual([r.branch for r in rows], ["feature/open"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("org/empty: "))


class GitLabTest(unittest.TestCase):
    def test_scan(self):
        api = "https://gitlab.example.com/api/v4"
        http = FakeHttp({
            query(f"{api}/groups/team%2Fsub/projects", per_page=100, include_subgroups="true"):
                [{"id": 7, "path_with_namespace": "team/sub/app"}],
            query(f"{api}/projects/7/repository/branches", per_page=100): [
                {"name": "main", "commit": {}},
                {"name": "feature/a", "commit": {"committed_date": "2026-10-01T00:00:00Z", "author_name": "Bo"}},
                {"name": "feature/b", "commit": {}},
            ],
            query(f"{api}/projects/7/repository/compare", **{"from": "main", "to": "feature/a"}): {"commits": [{}, {}]},
            query(f"{api}/projects/7/repository/compare", **{"from": "main", "to": "feature/b"}): {"commits": [{}]},
            query(f"{api}/projects/7/merge_requests", state="merged", source_branch="feature/a", per_page=1): [],
            query(f"{api}/projects/7/merge_requests", state="merged", source_branch="feature/b", per_page=1): [{}],
        })
        rows, _, _ = lub.scan(lub.GitLab(http, "https://gitlab.example.com/"), ["team/sub"], "feature/", "main")
        self.assertEqual(rows, [lub.Unmerged("team/sub/app", "feature/a", 2, "2026-10-01T00:00:00Z", "Bo")])


class BitbucketTest(unittest.TestCase):
    def test_scan_counts_commits_across_pages(self):
        api = "https://api.bitbucket.org/2.0"
        commits = query(f"{api}/repositories/ws/app/commits", include="feature/a", exclude="main", pagelen=100)
        http = FakeHttp({
            query(f"{api}/repositories/ws", pagelen=100): {"values": [{"slug": "app", "full_name": "ws/app"}]},
            query(f"{api}/repositories/ws/app/refs/branches", pagelen=100): {"values": [
                {"name": "main", "target": {}},
                {"name": "feature/a", "target": {"date": "2026-09-09T00:00:00+00:00", "author": {"raw": "Cy <c@x>"}}},
            ]},
            commits: {"values": [{}, {}], "next": "https://next/page2"},
            "https://next/page2": {"values": [{}]},
            query(f"{api}/repositories/ws/app/pullrequests",
                  state="MERGED", q='source.branch.name="feature/a"', pagelen=1): {"values": []},
        })
        rows, _, _ = lub.scan(lub.Bitbucket(http), ["ws"], "feature/", "main")
        self.assertEqual(rows, [lub.Unmerged("ws/app", "feature/a", 3, "2026-09-09T00:00:00+00:00", "Cy <c@x>")])


class GiteaTest(unittest.TestCase):
    def test_scan_reads_closed_pulls_once_per_repo(self):
        api = "https://gitea.example.com/api/v1"
        http = FakeHttp({
            query(f"{api}/orgs/org/repos", limit=50): [{"full_name": "org/app"}],
            query(f"{api}/repos/org/app/branches", limit=50): [
                {"name": "main", "commit": {}},
                {"name": "feature/a", "commit": {"timestamp": "2026-08-08T00:00:00Z", "author": {"name": "Di"}}},
                {"name": "feature/b", "commit": {}},
            ],
            f"{api}/repos/org/app/compare/main...feature/a": {"total_commits": 4},
            f"{api}/repos/org/app/compare/main...feature/b": {"total_commits": 1},
            query(f"{api}/repos/org/app/pulls", state="closed", limit=50):
                [{"head": {"ref": "feature/b"}, "merged": True}, {"head": {"ref": "feature/a"}, "merged": False}],
        })
        rows, _, _ = lub.scan(lub.Gitea(http, "https://gitea.example.com"), ["org"], "feature/", "main")
        self.assertEqual(rows, [lub.Unmerged("org/app", "feature/a", 4, "2026-08-08T00:00:00Z", "Di")])
        self.assertEqual(sum("/pulls" in call for call in http.calls), 1)


class AuthTest(unittest.TestCase):
    def bitbucket(self, env):
        return lub.build_provider(lub.parse_args(["--provider", "bitbucket", "--workspaces", "ws"]), env)

    def test_bitbucket_api_token_is_basic_auth_with_the_email(self):
        provider = self.bitbucket({"BITBUCKET_EMAIL": "me@x.com", "BITBUCKET_API_TOKEN": "tok"})
        expected = "Basic " + base64.b64encode(b"me@x.com:tok").decode()
        self.assertEqual(provider.http.headers["Authorization"], expected)

    def test_bitbucket_access_token_is_bearer(self):
        provider = self.bitbucket({"BITBUCKET_ACCESS_TOKEN": "tok"})
        self.assertEqual(provider.http.headers["Authorization"], "Bearer tok")

    def test_bitbucket_without_credentials_names_the_variables(self):
        with self.assertRaises(SystemExit) as raised:
            self.bitbucket({})
        self.assertIn("BITBUCKET_API_TOKEN", str(raised.exception.code))
        with self.assertRaises(SystemExit) as raised:
            self.bitbucket({"BITBUCKET_EMAIL": "me@x.com"})
        self.assertIn("both", str(raised.exception.code))

    def test_http_errors_never_include_the_token(self):
        error = urllib.error.HTTPError("https://x/y", 401, "Unauthorized", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(lub.ApiError) as raised:
                lub.Http({"Authorization": "Bearer secret-token"}).get("https://x/y")
        self.assertEqual(raised.exception.status, 401)
        self.assertNotIn("secret-token", str(raised.exception))


class HttpRetryTest(unittest.TestCase):
    def test_a_dropped_connection_is_retried_then_reported(self):
        dropped = lub.http.client.RemoteDisconnected("Remote end closed connection without response")
        with mock.patch("urllib.request.urlopen", side_effect=dropped) as urlopen, mock.patch("time.sleep"):
            with self.assertRaises(lub.ApiError):
                lub.Http({}).get("https://x/y")
        self.assertEqual(urlopen.call_count, lub.ATTEMPTS)


if __name__ == "__main__":
    unittest.main()
