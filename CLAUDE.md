# lirts — rules for anyone (human or AI agent) working in this repository

lirts is a standalone terminal dashboard that observes what runs on this machine: ports,
processes, containers, stacks, Kubernetes, ssh, proxies, traffic, health and history. It is
Python 3.11+, Typer for the CLI, Textual for the TUI. Read this file first, then the rule files
under `.claude/rules/` (each applies to the paths in its frontmatter).

## The plan

- `TODO.md` is the only plan. Work on its first open item; nothing else is started. New wishes
  become TODO items, they are not executed on the spot. Answer questions in text before doing
  anything.
- A commit marks a finished TODO item: code, tests, README, CHANGELOG, `config.example.yaml`,
  the local docs and the ticked TODO line go into that one commit. Never a commit that only edits
  `TODO.md`.
- `master` is protected; nothing is pushed to it directly. Every item lives on a branch named
  `feature/`, `fix/`, `docs/`, `chores/` or `improvement/` and lands through one squash-merged
  pull request whose title is the commit subject, `<type>: <summary>`. The branch is pushed
  when the item is finished and `make ci-local` passed; the agent opens the PR and reports, the
  maintainer merges. Details in `CONTRIBUTING.md`.
- Ideas, design drafts, the cheat sheet and the testing walkthrough live in the gitignored
  `local/` folder and are updated with every change. The public tree holds no idea lists, no
  specifications of things not built and no history of finished phases.

## What lirts is and is not

The dividing rule is in `.claude/rules/project.md`: lirts observes, it never reads the user's
folders or files, never runs work in the background without being asked, never writes the config
file on its own, and has no link to any other tool. A removed feature is removed completely.

## Quality bar (details in the rule files)

- Style: `.claude/rules/code-style.md`. Quality: `.claude/rules/code-quality.md`.
  Tests: `.claude/rules/testing.md`.
- Code files are at most 400 lines. Split before you cross the line.
- `make ci-local` mirrors CI exactly (lint, format check, types, tests, smoke run, file length,
  secret scan) and must pass before every push; `make hooks` installs it as the pre-push hook.
  An avoidable CI failure is a process bug.
- CI is one Ubuntu job per run; minutes are billed. A pull request runs it on the branch and
  once more on the merge. Never add a runner, trigger or job without stating what it costs.
- Every feature, command, key and setting has a test and a walkthrough step in
  `local/TESTING.md`. Every pull request says how to test it.

## Documentation that must stay true

README (commands, keys, settings, where things show up), CHANGELOG (Keep a Changelog, every
change under the coming version, removals with the reason), `config.example.yaml` (every key,
annotated), `lirts doctor` output for anything that needs an external tool.
