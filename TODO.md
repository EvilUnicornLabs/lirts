# TODO

Current work, in this order. Nothing outside this list is started. Ideas and history live in the
gitignored `local/` folder, not here.

1. [x] **Git conventions and GitOps** (2026-09-23). master protected, no direct pushes; every change on a
   branch named `feature/`, `fix/`, `docs/`, `chores/` or `improvement/` and merged through a pull
   request that passes the one CI job; one branch, one PR and one squash-merged commit per finished
   TODO item; commit subject `<type>: <summary>` with the branch's type; PR body with "How to test";
   `make ci-local` before a branch is pushed; review rules for a one-person repo; the CI cost rule
   (a PR runs the job on the branch and once more on the merge); `CONTRIBUTING.md`, PR template,
   CLAUDE.md and the rules updated. Tags and the release flow (version bump, CHANGELOG cut, `vX.Y.Z`
   tag, GitHub release with sdist / wheel) come with the first release, item 4.
2. [ ] Port the core logic to Go; TUI with Bubble Tea; optimise for very high port / process counts.
3. [ ] Single-binary distribution for macOS and Linux.
4. [ ] Publish to PyPI and finish the Homebrew tap (release flow, tags, GitHub releases).
5. [ ] Per-process bandwidth on Linux (needs eBPF or root).

Keep unchanged throughout: ports, processes, containers, stacks, identity, health, anomalies,
blast radius, kill / restart / stop, traffic, who-talks-to-whom, proxies, Kubernetes, ssh, reach,
history, events, patterns, explain, doctor, demo and replay, the star map, smart fix, port memory,
the MCP server.
