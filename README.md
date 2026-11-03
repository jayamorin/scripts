# scripts

Personal utility scripts.

| Folder | What's in it |
|---|---|
| `git/` | `list_unmerged_branches.py`: branches not yet merged into `main` across a GitHub/Gitea organization, GitLab group or Bitbucket workspace |
| `ollama/` | Ollama modelfiles and `update-models.sh` (pull base models, rebuild custom ones) |
| `vm/` | VirtualBox VM creation |
| `misc/` | One-off experiments: `readme_generator.py` (README from the current folder via Ollama), `bitcoin_mining.py`, `language_translator.py` |

Decisions, open items and things to verify are in [`context/progress-tracker.md`](context/progress-tracker.md).

## list_unmerged_branches.py

Python 3, standard library only. Read-only: it lists branches and never changes them.

```bash
python3 git/list_unmerged_branches.py --provider github    --organization ORG [ORG ...]
python3 git/list_unmerged_branches.py --provider gitlab    --groups GROUP [GROUP ...] [--url https://gitlab.example.com]
python3 git/list_unmerged_branches.py --provider bitbucket --workspaces WORKSPACE [WORKSPACE ...]
python3 git/list_unmerged_branches.py --provider gitea     --organization ORG [ORG ...] --url https://gitea.example.com
```

| Option | Default | |
|---|---|---|
| `--prefix` | `feature/` | Which branches to check; `--prefix ""` checks every branch |
| `--base` | `main` | Branch to compare against; repos without it are skipped and listed on stderr |
| `--json` | off | JSON (`unmerged`, `skipped`, `errors`) instead of a table |

A branch is listed when it has commits that `main` doesn't **and** no merged pull/merge request came from it. The second check hides branches that were squash- or rebase-merged, which otherwise still look "ahead".

The exit code is 1 if any repo couldn't be read (the others are still listed), and 2 for wrong arguments.

### Sign-in

Set these in the environment. Read-only scopes are enough.

| Provider | Variables | Scopes |
|---|---|---|
| GitHub | `GITHUB_TOKEN`, or nothing if `gh auth login` is done | `repo` (private repos), `read:org` |
| GitLab | `GITLAB_TOKEN` | `read_api` |
| Bitbucket | `BITBUCKET_EMAIL` (your Atlassian account email) + `BITBUCKET_API_TOKEN`, or `BITBUCKET_ACCESS_TOKEN` (workspace/repository access token) | `read:repository:bitbucket`, `read:pullrequest:bitbucket`, `read:workspace:bitbucket` |
| Gitea | `GITEA_TOKEN` | `read:organization`, `read:repository` |

Example, without leaving the token in shell history:

```bash
export BITBUCKET_EMAIL="you@example.com"
read -rs BITBUCKET_API_TOKEN && export BITBUCKET_API_TOKEN
python3 git/list_unmerged_branches.py --provider bitbucket --workspaces my-workspace
```

### Tests

```bash
python3 -m unittest discover -s git
```
