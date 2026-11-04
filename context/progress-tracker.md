# Progress Tracker

## Current State

- 2026-11-03: repo initialized. Existing scripts moved into `vm/` and `misc/`; `ollama/` unchanged.
- 2026-11-03: `git/list_unmerged_branches.py` added for GitHub, GitLab, Bitbucket and Gitea, with 18 unit tests. Tested live against GitHub only.

## Architecture Decisions

1. 2026-11-03: **Standard library only** (`urllib`), so the script runs on any Python 3 without installing anything. Rejected: `requests` and the provider SDKs (PyGithub, python-gitlab), which are extra dependencies for a handful of GET calls.
2. 2026-11-03: **Unmerged = commits ahead of `--base` and no merged PR/MR from the branch.** Without the PR check, squash- and rebase-merged branches would still be listed. Rejected: ahead-count only, which gives false positives after squash merges. Cost: one extra API call per ahead branch (Gitea: one per repo, since its pulls API can't filter by head branch).
3. 2026-11-03: **`--prefix feature/` by default**, with `--prefix ""` for all branches. Matches the request; the prefix keeps it useful for other branch types.
4. 2026-11-03: **`--base main` by default, and repos without it are skipped and listed**, rather than silently falling back to each repo's default branch.
5. 2026-11-03: **Sign-in from environment variables only, never from arguments**, so tokens don't end up in shell history or `ps`. GitHub falls back to `gh auth token`. Bitbucket uses an Atlassian API token (`BITBUCKET_EMAIL` + `BITBUCKET_API_TOKEN`, Basic auth) or an access token (`BITBUCKET_ACCESS_TOKEN`, Bearer); app passwords are not supported because Bitbucket has retired them.
6. 2026-11-03: **A repo that fails to read is reported and the scan goes on**, exit code 1. Failing to list a namespace's repos stops the run. Dropped connections, timeouts and HTTP 5xx are retried up to 3 times.
7. 2026-11-03: **`--url` only for GitLab (default gitlab.com) and Gitea (required).** GitHub Enterprise and Bitbucket Data Center are out of scope.
8. 2026-11-03: **Layout:** one folder per topic (`git/`, `ollama/`, `vm/`, `misc/`). Rejected: keeping scripts in the root.
9. 2026-11-04: **Remote: private `jayamorin/scripts` on the personal account**, since these are personal scripts. Rejected: the SpaghettiCodeChefs org, which holds the team's projects; a public repo.

## Known Issues

1. [minor] On GitHub, the last-commit date and author come from the compare API's commit list. For a branch more than 250 commits ahead, that's the 250th commit, not the tip. `git/list_unmerged_branches.py`, `GitHub.ahead`.
2. [minor] 26 SpaghettiCodeChefs repos have no `main` branch (empty, or on another default branch) and are skipped. To cover them, pass `--base`, or see Suggestion 1.
3. [minor] No git remote: the repo is local only. 2026-11-04: creating `jayamorin/scripts` (private) and pushing was approved, but Claude Code's permission check blocked `gh repo create`. Needs the maintainer to run Next Step 1.

## To Verify

1. [minor] GitLab, Bitbucket and Gitea have only been tested with recorded sample responses, not live servers. Run each once against a real group, workspace or organization, including the Bitbucket API-token sign-in.
2. [minor] Gitea's compare endpoint (`/repos/{owner}/{repo}/compare/{base}...{head}`) needs Gitea 1.22 or later. Check the server version on the first live run.

## Verification Log

- 2026-11-03: unit tests, 18 passed (`python3 -m unittest discover -s git`).
- 2026-11-03: live GitHub run, `--organization SpaghettiCodeChefs`. Default prefix: no unmerged `feature/` branches, which is correct (the last one, cicd-api #22, was merged and deleted). With `--prefix ""`: 23 unmerged `chore/` branches listed, exit 0.

## Next Steps

1. [minor] Create the remote and push (decided 2026-11-04: private repo `jayamorin/scripts` on the personal account): `gh repo create jayamorin/scripts --private --description "Personal utility scripts" --source . --remote origin --push`

## Suggestions / Future Suggestions

1. `--base default`: compare against each repo's own default branch instead of a fixed name.
2. Scan repos in parallel (a small thread pool) for large organizations; the 51-repo SpaghettiCodeChefs org takes about 80 seconds sequentially.
3. Optional `--include-archived` / skip archived repos.

## Open Questions

None.
