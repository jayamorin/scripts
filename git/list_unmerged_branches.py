#!/usr/bin/env python3
"""List branches that are not merged into main across a GitHub or Gitea organization,
a GitLab group or a Bitbucket workspace.

A branch is unmerged when it has commits that main doesn't, unless a merged pull (or
merge) request came from it: squash and rebase merges leave a branch "ahead" of main even
though its work is there.

Read-only: it lists branches, it never changes them. Standard library only.

Sign-in, from the environment:
  github     GITHUB_TOKEN, or the token of `gh auth login`
  gitlab     GITLAB_TOKEN
  bitbucket  BITBUCKET_EMAIL + BITBUCKET_API_TOKEN (Atlassian API token),
             or BITBUCKET_ACCESS_TOKEN (workspace or repository access token)
  gitea      GITEA_TOKEN
"""

import argparse
import base64
import http.client
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass

TIMEOUT = 30
ATTEMPTS = 3

# The option that names where to look, per provider.
NAMESPACE_OPTION = {
    "github": "organization",
    "gitlab": "groups",
    "bitbucket": "workspaces",
    "gitea": "organization",
}


class ApiError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Repo:
    key: str  # what the API addresses it by
    name: str  # what is printed


@dataclass
class Branch:
    name: str
    date: str = ""
    author: str = ""


@dataclass
class Unmerged:
    repo: str
    branch: str
    ahead: int
    date: str
    author: str


def path(value, safe=""):
    return urllib.parse.quote(value, safe=safe)


class Http:
    """GET JSON with fixed headers. Errors name the URL and status, never the headers: they carry the token."""

    def __init__(self, headers):
        self.headers = {"Accept": "application/json", "User-Agent": "list-unmerged-branches", **headers}

    def get(self, url, params=None):
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=self.headers)
        for attempt in range(1, ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return json.load(response), response.headers
            except urllib.error.HTTPError as error:
                if error.code < 500 or attempt == ATTEMPTS:
                    raise ApiError(f"GET {url} failed: HTTP {error.code} {error.reason}", error.code) from None
            except urllib.error.URLError as error:
                if attempt == ATTEMPTS:
                    raise ApiError(f"GET {url} failed: {error.reason}") from None
            except (http.client.HTTPException, OSError) as error:
                # Dropped connections and timeouts: worth another try.
                if attempt == ATTEMPTS:
                    raise ApiError(f"GET {url} failed: {error!r}") from None
            time.sleep(attempt)


def next_link(headers):
    for part in (headers.get("Link") or "").split(","):
        match = re.match(r'\s*<([^>]+)>\s*;.*rel="next"', part)
        if match:
            return match.group(1)
    return None


def paginate_link(http, url, params):
    """Every item of a list API that pages with a `Link: <…>; rel="next"` header (GitHub, GitLab, Gitea)."""
    data, headers = http.get(url, params)
    items = list(data)
    while url := next_link(headers):
        data, headers = http.get(url)
        items.extend(data)
    return items


def paginate_next(http, url, params):
    """Every item of a Bitbucket list API, which pages with a `next` URL in the body."""
    data, _ = http.get(url, params)
    items = list(data.get("values", []))
    while url := data.get("next"):
        data, _ = http.get(url)
        items.extend(data.get("values", []))
    return items


class GitHub:
    def __init__(self, http, api="https://api.github.com"):
        self.http = http
        self.api = api

    def repos(self, org):
        try:
            repos = paginate_link(self.http, f"{self.api}/orgs/{path(org)}/repos", {"per_page": 100, "type": "all"})
        except ApiError as error:
            if error.status != 404:
                raise
            # Not an organization: try it as a user.
            repos = paginate_link(self.http, f"{self.api}/users/{path(org)}/repos", {"per_page": 100, "type": "all"})
        return [Repo(r["full_name"], r["full_name"]) for r in repos]

    def branches(self, repo):
        branches = paginate_link(self.http, f"{self.api}/repos/{path(repo.key, '/')}/branches", {"per_page": 100})
        return [Branch(b["name"]) for b in branches]

    def ahead(self, repo, branch, base):
        data, _ = self.http.get(f"{self.api}/repos/{path(repo.key, '/')}/compare/{path(base, '/')}...{path(branch, '/')}")
        commits = data.get("commits") or []
        author = commits[-1]["commit"]["author"] if commits else {}
        return data["ahead_by"], author.get("date", ""), author.get("name", "")

    def merged(self, repo, branch):
        owner = repo.key.split("/")[0]
        pulls, _ = self.http.get(
            f"{self.api}/repos/{path(repo.key, '/')}/pulls",
            {"state": "closed", "head": f"{owner}:{branch}", "per_page": 100},
        )
        return any(p.get("merged_at") for p in pulls)


class GitLab:
    def __init__(self, http, url="https://gitlab.com"):
        self.http = http
        self.api = f"{url.rstrip('/')}/api/v4"

    def repos(self, group):
        projects = paginate_link(
            self.http, f"{self.api}/groups/{path(group)}/projects", {"per_page": 100, "include_subgroups": "true"}
        )
        return [Repo(str(p["id"]), p["path_with_namespace"]) for p in projects]

    def branches(self, repo):
        branches = paginate_link(self.http, f"{self.api}/projects/{repo.key}/repository/branches", {"per_page": 100})
        return [
            Branch(b["name"], b["commit"].get("committed_date", ""), b["commit"].get("author_name", ""))
            for b in branches
        ]

    def ahead(self, repo, branch, base):
        data, _ = self.http.get(f"{self.api}/projects/{repo.key}/repository/compare", {"from": base, "to": branch})
        return len(data.get("commits") or []), "", ""

    def merged(self, repo, branch):
        requests, _ = self.http.get(
            f"{self.api}/projects/{repo.key}/merge_requests",
            {"state": "merged", "source_branch": branch, "per_page": 1},
        )
        return bool(requests)


class Bitbucket:
    def __init__(self, http, api="https://api.bitbucket.org/2.0"):
        self.http = http
        self.api = api

    def repos(self, workspace):
        repos = paginate_next(self.http, f"{self.api}/repositories/{path(workspace)}", {"pagelen": 100})
        return [Repo(f"{path(workspace)}/{path(r['slug'])}", r["full_name"]) for r in repos]

    def branches(self, repo):
        branches = paginate_next(self.http, f"{self.api}/repositories/{repo.key}/refs/branches", {"pagelen": 100})
        return [
            Branch(b["name"], b["target"].get("date", ""), b["target"].get("author", {}).get("raw", ""))
            for b in branches
        ]

    def ahead(self, repo, branch, base):
        commits = paginate_next(
            self.http, f"{self.api}/repositories/{repo.key}/commits", {"include": branch, "exclude": base, "pagelen": 100}
        )
        return len(commits), "", ""

    def merged(self, repo, branch):
        name = branch.replace("\\", "\\\\").replace('"', '\\"')
        data, _ = self.http.get(
            f"{self.api}/repositories/{repo.key}/pullrequests",
            {"state": "MERGED", "q": f'source.branch.name="{name}"', "pagelen": 1},
        )
        return bool(data.get("values"))


class Gitea:
    def __init__(self, http, url):
        self.http = http
        self.api = f"{url.rstrip('/')}/api/v1"
        self.merged_heads = {}  # repo key -> head branches of its merged pulls

    def repos(self, org):
        try:
            repos = paginate_link(self.http, f"{self.api}/orgs/{path(org)}/repos", {"limit": 50})
        except ApiError as error:
            if error.status != 404:
                raise
            # Not an organization: try it as a user.
            repos = paginate_link(self.http, f"{self.api}/users/{path(org)}/repos", {"limit": 50})
        return [Repo(r["full_name"], r["full_name"]) for r in repos]

    def branches(self, repo):
        branches = paginate_link(self.http, f"{self.api}/repos/{path(repo.key, '/')}/branches", {"limit": 50})
        return [
            Branch(b["name"], b["commit"].get("timestamp", ""), b["commit"].get("author", {}).get("name", ""))
            for b in branches
        ]

    def ahead(self, repo, branch, base):
        data, _ = self.http.get(f"{self.api}/repos/{path(repo.key, '/')}/compare/{path(base, '/')}...{path(branch, '/')}")
        return data.get("total_commits", len(data.get("commits") or [])), "", ""

    def merged(self, repo, branch):
        # The pulls API can't filter by head branch: read the closed pulls once per repo.
        if repo.key not in self.merged_heads:
            pulls = paginate_link(self.http, f"{self.api}/repos/{path(repo.key, '/')}/pulls", {"state": "closed", "limit": 50})
            self.merged_heads[repo.key] = {p["head"]["ref"] for p in pulls if p.get("merged")}
        return branch in self.merged_heads[repo.key]


def scan(provider, namespaces, prefix, base):
    """Unmerged branches, repos without the base branch, and repos that failed to read."""
    unmerged, skipped, errors = [], [], []
    for namespace in namespaces:
        for repo in provider.repos(namespace):
            try:
                branches = provider.branches(repo)
                if not any(b.name == base for b in branches):
                    skipped.append(repo.name)
                    continue
                for branch in branches:
                    if branch.name == base or not branch.name.startswith(prefix):
                        continue
                    ahead, date, author = provider.ahead(repo, branch.name, base)
                    if ahead and not provider.merged(repo, branch.name):
                        unmerged.append(
                            Unmerged(repo.name, branch.name, ahead, date or branch.date, author or branch.author)
                        )
            except ApiError as error:
                errors.append(f"{repo.name}: {error}")
    return unmerged, skipped, errors


def github_token():
    if token := os.environ.get("GITHUB_TOKEN"):
        return token
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def build_provider(args, env=os.environ):
    if args.provider == "github":
        token = github_token()
        if not token:
            sys.exit("error: set GITHUB_TOKEN or sign in with `gh auth login`")
        return GitHub(Http({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }))

    if args.provider == "gitlab":
        if not env.get("GITLAB_TOKEN"):
            sys.exit("error: set GITLAB_TOKEN")
        return GitLab(Http({"PRIVATE-TOKEN": env["GITLAB_TOKEN"]}), args.url or "https://gitlab.com")

    if args.provider == "bitbucket":
        email, api_token = env.get("BITBUCKET_EMAIL"), env.get("BITBUCKET_API_TOKEN")
        if email and api_token:
            basic = base64.b64encode(f"{email}:{api_token}".encode()).decode()
            return Bitbucket(Http({"Authorization": f"Basic {basic}"}))
        if email or api_token:
            sys.exit("error: set both BITBUCKET_EMAIL and BITBUCKET_API_TOKEN")
        if env.get("BITBUCKET_ACCESS_TOKEN"):
            return Bitbucket(Http({"Authorization": f"Bearer {env['BITBUCKET_ACCESS_TOKEN']}"}))
        sys.exit("error: set BITBUCKET_EMAIL + BITBUCKET_API_TOKEN, or BITBUCKET_ACCESS_TOKEN")

    if not env.get("GITEA_TOKEN"):
        sys.exit("error: set GITEA_TOKEN")
    return Gitea(Http({"Authorization": f"token {env['GITEA_TOKEN']}"}), args.url)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="List branches not merged into main.",
        epilog="Sign-in comes from the environment; see the top of this file.",
    )
    parser.add_argument("--provider", required=True, choices=list(NAMESPACE_OPTION))
    parser.add_argument("--organization", nargs="+", metavar="ORG", help="github, gitea: organizations (or users)")
    parser.add_argument("--groups", nargs="+", metavar="GROUP", help="gitlab: groups, subgroups included")
    parser.add_argument("--workspaces", nargs="+", metavar="WORKSPACE", help="bitbucket: workspaces")
    parser.add_argument("--url", help="gitlab, gitea: server URL (gitlab defaults to https://gitlab.com)")
    parser.add_argument("--prefix", default="feature/", help='branches to check (default: feature/; "" for all)')
    parser.add_argument("--base", default="main", help="branch to compare against (default: main)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    wanted = NAMESPACE_OPTION[args.provider]
    if not getattr(args, wanted):
        parser.error(f"--provider {args.provider} needs --{wanted}")
    for option in sorted(set(NAMESPACE_OPTION.values()) - {wanted}):
        if getattr(args, option):
            parser.error(f"--{option} doesn't apply to --provider {args.provider}; use --{wanted}")
    if args.url and args.provider not in ("gitlab", "gitea"):
        parser.error("--url applies to gitlab and gitea only")
    if args.provider == "gitea" and not args.url:
        parser.error("--provider gitea needs --url")
    return args


def print_table(rows):
    if not rows:
        print("No unmerged branches.")
        return
    lines = [("REPOSITORY", "BRANCH", "AHEAD", "LAST COMMIT", "AUTHOR")]
    lines += [(r.repo, r.branch, str(r.ahead), r.date[:10], r.author) for r in rows]
    widths = [max(len(line[i]) for line in lines) for i in range(len(lines[0]))]
    for line in lines:
        print("  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip())


def main(argv=None):
    args = parse_args(argv)
    provider = build_provider(args)
    try:
        rows, skipped, errors = scan(provider, getattr(args, NAMESPACE_OPTION[args.provider]), args.prefix, args.base)
    except ApiError as error:
        sys.exit(f"error: {error}")

    if args.json:
        print(json.dumps({"unmerged": [asdict(r) for r in rows], "skipped": skipped, "errors": errors}, indent=2))
    else:
        print_table(rows)
        if skipped:
            print(f"\nSkipped, no '{args.base}' branch: {', '.join(skipped)}", file=sys.stderr)
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
