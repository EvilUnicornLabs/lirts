# Contributing to lirts

lirts is maintained by Evil Unicorn Labs. These are the rules every change follows, whether a
human or an AI agent makes it. The project rules (what lirts is and is not) are in
[CLAUDE.md](CLAUDE.md) and [.claude/rules/](.claude/rules/); this file is about how a change
gets into `master`.

## The plan

`TODO.md` is the only plan. A change is the first open item there, nothing else. Wishes and
ideas become TODO items first; they are not built on the spot.

## Branches

`master` is protected: no direct pushes, no force pushes, no deletions, for admins too. Every
change lives on a branch named with its type:

| Prefix | For |
|---|---|
| `feature/` | something new: a command, a key, a setting, a tool |
| `fix/` | a bug |
| `docs/` | documentation only |
| `chores/` | tooling, CI, dependencies, repository housekeeping |
| `improvement/` | an existing feature made better without changing what it is |

The rest of the name says what the branch does, in kebab-case: `feature/mcp-server`,
`fix/history-panel-placement`, `chores/git-conventions`. One branch per finished TODO item.

## Commits

- One item, one branch, one pull request, one commit on `master`. Pull requests are
  squash-merged, so the branch may hold as many commits as you like; the squash is what lands.
- The commit subject (and the PR title, which becomes it) is `<type>: <summary>` where `type`
  is the branch prefix without the slash: `feature: MCP server for coding agents`,
  `fix: history box vanished when the traffic box was toggled`. Lower case after the colon,
  no trailing full stop, the summary says what changed for the user.
- The body, when there is one, says why and what to look at. AI-assisted commits carry the
  `Co-Authored-By:` trailer of the agent.
- The commit contains everything the item needs: code, tests, README, CHANGELOG (under
  `Unreleased`), `config.example.yaml`, the local docs and the ticked TODO line. Never a commit
  that only edits `TODO.md`.

## Before pushing a branch

`make ci-local` must pass. It runs exactly what CI runs: `ruff check`, `ruff format --check`,
`mypy`, `pytest`, a smoke run (`lirts --version`, `lirts explain --demo`), the 400-line file
check and the gitleaks secret scan. `make hooks` installs it as the pre-push hook so a failing
branch is never pushed. An avoidable CI failure is a process bug.

## Pull requests

- Open the PR from the branch to `master` with the template: what changed, why, and **How to
  test** (the exact commands or keys, and what should happen). Every PR says how to test it.
- The one CI job (`ubuntu / py3.12`) is a required check. Nothing merges red.
- Review: this is a one-person repository. The maintainer reviews their own PR by reading the
  diff on GitHub once, top to bottom, after CI is green, and then squash-merges. An AI agent
  never merges; it opens the PR and reports. External contributors get a review from the
  maintainer.
- The branch is deleted on merge.

## CI cost

CI minutes are billed. A pull request runs the job once on the branch (again on every push to
it) and once more on the merge to `master`: about two jobs, roughly four billed minutes, per
item. Push a branch when the item is finished and `make ci-local` passed, not after every
commit. Never add a runner, trigger, matrix entry or job without stating what it costs per
push. A pull request always runs the job, documentation included, because the required check
needs a run to pass; the merge of a documentation-only change to `master` skips it (`**.md`,
`docs/**`, `config.example.yaml`). The full matrix (three Python versions, macOS at 10× the
price) runs only by hand from the Actions tab.

## Versions and releases

Versions follow [Semantic Versioning](https://semver.org/) and the CHANGELOG follows
[Keep a Changelog](https://keepachangelog.com/): every change is written under `Unreleased`
in the commit that makes it, removals with the reason. The release flow (version bump,
CHANGELOG cut, `vX.Y.Z` tag, GitHub release with sdist and wheel, PyPI and the Homebrew tap) is
defined with the first release; until then there are no tags.

## Reporting a problem

Open an issue with the template: what you did, what happened, what you expected, the output of
`lirts doctor` and `lirts --version`. Never paste secrets; the Docker tab masks environment
variables for a reason.
