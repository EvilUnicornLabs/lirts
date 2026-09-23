---
paths:
  - "**/*.py"
  - "**/*.md"
  - "**/*.yaml"
  - "**/*.yml"
  - "**/*.toml"
---

# lirts project rules

These are the decisions that define the tool. They are not up for reinterpretation in a task.

- **lirts observes.** It shows what runs on the machine right now and what it has seen run. It
  never declares what a project should look like: no profiles, no named snapshots, no expected
  versions, no lockfile or `.env` inspection, no restore, no time travel. The runtime memory
  (history windows, events, patterns, "changes since last session") stays; it is derived from
  observation only.
- **Never read the user's folders or files.** No scanning, indexing or walking of directories,
  no parsing of project files. The only files lirts reads are its own config and state, the
  files a running program names about itself (`nginx -T`, `httpd -S`, `ssh -G`, a container's
  labels) and the hosts file. Never comment on the user's folders or data.
- **No background work without being asked.** Collection runs on the refresh timer the user
  configured; everything else (health checks, reachability, routes reload, recording) runs only
  when the user presses the key or runs the command. No periodic probing, no auto checks, no
  option that turns them on.
- **Never write the config file on its own.** No auto-migration, no auto-upgrade, no defaults
  written back. Unknown or obsolete keys are ignored in memory and reported; the upgrade is a
  user action (Settings `s` or `lirts config upgrade`).
- **A removed feature is removed completely.** Code, config keys, settings entries, keys,
  commands, docs, tests, state files. A stale config key must never change behaviour.
- **Standalone.** No link, dependency, shared format, export designed for, or integration with
  any other tool. "Constellate" or "topology" means the lirts-internal star map.
- **Every change updates the documentation** in the same commit: README, CHANGELOG,
  `config.example.yaml`, `local/CHEATSHEET.md`, `local/TESTING.md`, `local/TODO.md`.
- **The UI stays minimal.** The side panel shows the essentials; details go to screens and
  tabs. New keys are documented in the README keybinding table and in the footer.
- **Degrade, do not fail, on missing external tools.** docker, kubectl, nettop, ss, ssh,
  nginx, httpd, terminal-notifier are optional. When one is missing the feature is off, the
  footer key is dimmed and `lirts doctor` says what is missing and why it matters.
- **CI cost.** One Ubuntu job per push, docs-only pushes skip CI, the full matrix only by hand
  or on a release tag. Pushes are batched and happen only when the maintainer says so.
