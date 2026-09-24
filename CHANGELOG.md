# Changelog

All notable changes to lirts are documented here.  The format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- The daemon: `lirts daemon` (`lirts -d`) runs one engine for the whole machine over a Unix
  socket in the state directory; the dashboard, every CLI command and `lirts mcp` attach to it
  when it runs (`--standalone` opts out), so there is one collector, one history and one star
  map that keeps learning all day. `lirts daemon install` starts it at login (launchd agent on
  macOS, systemd user unit on Linux); `status`, `stop`, `start`, `uninstall`. Idle refresh
  every `daemon.idle_interval` seconds (10; `--idle` overrides), an attached dashboard drives
  faster refreshes; health checks still only on request, results kept in the daemon; the title
  bar says `via daemon (PID …)`; `lirts doctor` reports it. New setting in Settings
  (Collection tab) and the wizard.
- MCP server for coding agents: `lirts mcp` speaks the Model Context Protocol over stdio
  (Claude Code: `claude mcp add lirts -- lirts mcp`). It holds one engine open and refreshes it
  at `refresh_interval` while the client keeps it running, like an open dashboard; history, port
  memory and the star map keep learning and everything stops with the client. Read-only tools
  `list_listeners`, `who`, `explain`, `graph`, `topology`, `events`, `patterns`, `routes`,
  `stacks`, `kube`, `health` (a check on demand) and `doctor`; `free_port`, `fix` and `restart`
  only with `--allow-actions`, refused in replay. The `mcp` SDK is an optional extra
  (`lirts[mcp]`); `lirts doctor` reports whether it is installed.
- Ports in use: `lirts who <port>` names the holder (service, role, PID, user, since when,
  project, container) and what usually runs there; `lirts free <port>` kills the holder or
  stops the container after a confirmation. New insights on the row: a squatter ("3000 is
  usually shop/web; now node (blog)"), a leftover holder (parent process gone, or a container
  of a stopped stack) and a port shared by two projects; the side panel shows a "Usually" line.
  All from what lirts has already seen (history and the star map memory); `F` fixes a squatter
  or a leftover. lirts cannot see a failed start itself, it names the culprit.
- Roles sharper: the HTTP probe records the content kind and the dev server it recognises
  (Vite, webpack, Next.js, Nuxt, Angular, Create React App, SvelteKit, Astro, Parcel, Django,
  Flask, FastAPI, Rails, Phoenix, Laravel); HTML says frontend, JSON or an OpenAPI path says
  backend; compose service names (`web`, `ui`, `api`, `worker`, ...) and more process command
  lines count; a low-confidence identity shows a `?` in the table.
- Looks and layout: `ui` settings with a Layout tab in Settings and Layout steps in the wizard.
  Styles `classic`, `compact`, `tight`, `cracktro` and `phosphor` (`T` cycles; borders,
  density, glyph set, scroller and panel set; never colours, those are the theme's), layouts
  `default`, `containers` and `hosts` (`U` cycles; columns, side panel sections, which panels
  and where they go: side panel right, left or at the bottom, order of the boxes under the
  table; the saved column list untouched), a history box under the table (`Y`) with the
  selected row's sparklines, independent of the traffic box, glyph sets `block` / `braille` /
  `demoscene`, rounded or square corners, role and source icons in the rows, an optional ROLE
  column, lirts's own clock with a format setting, truecolor on / off at start, colours kept
  when CLI output is piped.
- Settings in tabs (General, Layout, Collection, Memory, Insights); `← →` switch tabs only;
  Enter on a choice opens a picker that previews themes live and shows each glyph set; the
  VALUE column is cut with an ellipsis. `ctrl+f` focuses the filter; `/` with the filter
  focused enters command mode with a dropdown of the palette commands (prefix search, `↑ ↓`,
  Tab completes, Enter runs). Esc with nothing to clear opens a small menu (Settings, Help,
  Star map, Quit) instead of quitting. `F` is hidden from the footer when the row has nothing
  to fix.
- Star map polish: labels shorten before they are dropped, `0` resets the ring spacing, `z`
  zooms into the selected star's wedge, the folded panel between 100 and 140 columns becomes a
  one-line footer for the selected star, the listing marks the selected star, and the map
  takes its colours from the active theme.
- Smart fix (`F`): the row's worst insight proposes one action and `F` runs it after a
  confirmation that shows exactly what will happen: port conflict, stale dev server and zombie
  kill the process the insight names; unhealthy or stopped container restarts it; a port that
  stops accepting connections restarts the container or the process; a crash-looping container,
  HTTP 5xx or a failing health endpoint open the container's logs. Same history note as `k` and
  `t`, dimmed when there is nothing to fix, refused in replay.
- README screenshots of the dashboard, the star map, Explain, who talks to whom, Kubernetes and
  the row details, taken from the demo machine by `scripts/screenshots.py` (nothing real in
  them). CODE_OF_CONDUCT.md (Contributor Covenant 2.1).
- Star map, step 4: memory. The map merges every refresh into `topology.json` in the state
  directory (only when history persistence is on, never in demo or replay): a star or line
  seen before is drawn dim while idle, a line only once it is "usual" (seen on
  `topology.usual_days` different days, default 3), everything not seen for `topology.days`
  (default 14) is forgotten. The panel shows on how many days a star was seen; `h` hides the
  idle ones; `lirts topology --clear` forgets; both numbers are in Settings.
- Star map, step 3: interaction. Arrows and Tab select a star (its lines light up, the rest
  dims, the panel lists what it is and every relation), Enter closes the map with the table
  cursor on its row, `i` opens its details, `f` focuses one project or the cluster's services,
  `h` hides stars that are not running, `l` toggles labels, `+` / `-` change the ring spacing.
  Stars keep their positions across refreshes; new ones take a free cell.
- Star map, step 2: the picture. `M` opens the map: rings by kind (databases, caches and
  queues in the centre, backends and tunnels next, frontends, proxies and tools, then hosts,
  the cluster and system services on the rim), one wedge per project ordered by name so the
  layout is stable, braille lines for relations, glyphs and colours by kind with problems in
  red or yellow, labels pushed away from the centre, a panel with counts, projects and
  problems, a legend. Below about 100×30 the screen shows the text listing instead.
- Star map, step 1: the topology graph. One node per service, container, tunnel, ssh host
  and Kubernetes cluster with a stable id (`svc:<project>/<service>`, `ctr:<stack>/<service>`,
  `proc:<service>:<port>`, `tunnel:<port>`, `host:<name>`, `k8s:<context>`), relations for
  observed client connections, proxy routes, tunnels and ssh sessions. `lirts topology` prints
  it grouped by project (`--json` for machines). Nothing is read from files; the docker proxy
  process is never a node. Design and decisions in the local design note.
- Project rules and code conventions: `CLAUDE.md` (the baseline for anyone working in the
  repository) and `.claude/rules/` with the project rules (observe only, no file scanning, no
  background work, no config writes, complete removals, standalone, CI cost), Python code style,
  code quality and testing rules. Code files are limited to 400 lines, `make ci-local` mirrors
  CI and is mandatory before a push; both are enforced by the next change.

### Changed
- Git conventions: `master` is protected and only changes through squash-merged pull requests
  from `feature/`, `fix/`, `docs/`, `chores/` or `improvement/` branches, one per finished TODO
  item, commit subject `<type>: <summary>`, the one CI job as a required check; `CONTRIBUTING.md`,
  a pull request template and issue templates. Pull requests always run CI (a required check
  needs a run); documentation-only merges still skip it. The git history was reset to a single
  initial commit on 2026-09-23; this changelog is the record of what came before.
- Licence: Apache License 2.0 (was MIT); LICENSE and NOTICE added, pyproject updated.
- The code base follows the rules: no file over 400 lines. `lirts/cli.py` is the package
  `lirts/cli/` (one module per command area), `lirts/engine.py` is `lirts/engine/` (base and
  mixins per step), `lirts/demo.py` is `lirts/demo/`, the dashboard app is composed of mixins
  (`lirts/tui/app_*.py`), every screen has its own module under `lirts/tui/screens/`, the
  collectors keep their parsers in `*_parse.py` modules, and insights, identity, models,
  history and config are split by responsibility. Every public import path still works.
- Status, activity, level and role values are `StrEnum`s (`Activity`, `Status`, `Level` in
  `lirts.models`, `Role` in `lirts.identity`); the old constants remain as aliases.
- Configuration is read through `lirts.config_view.ConfigView` (typed, defaults from the
  schema) instead of nested dict lookups; fixed numbers live in `lirts/constants.py`.
- Tooling: `ruff format` replaces black; `make ci-local` runs exactly what CI runs (lint,
  format check, mypy, pytest, smoke run, file-length check, gitleaks) and `make hooks` installs
  it as the pre-push hook; CI runs the same list in its one Ubuntu job.

### Fixed
- `lirts config set` took its value as YAML, so `bandwidth.mode off` (and any `on`, `yes`, `no`
  text) was written as a boolean. Known settings now go through the same coercion and
  validation as the Settings screen (minimums, known columns, known sources are reported
  instead of written); YAML remains only for free-form keys such as aliases.

### Removed
- Profiles (`lirts profile save | check | list`, the `profiles:` config section, the Profiles tab
  in Explain, the `P` key, the profile suggestion in patterns) and user-facing snapshots
  (`lirts snapshot save | diff | list`, the `snapshots/` state folder), with the Compose-file
  parsing that only profiles used. Reason: lirts observes what runs; declaring what a project
  should look like is not its job (dividing rule, 2026-09-21). "Changes since last session"
  stays: it is observed runtime memory, kept in `last.json` in the state directory. A
  `profiles:` section left in a config file is ignored and reported like any obsolete key.
- `docs/spec/` and `docs/history/`: they described profiles and time travel as planned
  features; the README and the rules describe what lirts is.
- `docs/best_practices.md`: folded into `CLAUDE.md` and `.claude/rules/`; it was stale
  (profiles, snapshots, spec and history folders) and a second source of truth.

## [0.6.0] - 2026-09-20

### Added
- STARTED FROM (`origin`) and HEALTH are optional columns.
- Tab switches tabs in the Kubernetes screen and focuses the visible table.
- Who-talks-to-whom map (`w`, `lirts graph`): local client processes per listening port from
  established loopback connections, plus proxy chains, tunnels and open SSH sessions; clients
  appear in the Connections tab and the kill blast radius.
- Launch origin per process: shell, terminal application, tty, tmux window and git branch,
  shown in the Processes tab (and an optional column).
- Health checker, on demand (`H` in the dashboard, `lirts health [--json]`): a TCP connect
  check with latency for every listener and a learned health endpoint for HTTP services
  (conventional paths tried once, per-port overrides). `H` opens a results screen (failing
  rows first, TCP latency, endpoint status, Docker health, why there is no endpoint result;
  `f` failing only, `r` again, `Enter` filters by port); the last result stays in the optional
  HEALTH column and the side panel until the next check. Row problems for ports that stop
  accepting connections or endpoints that fail. There is no background health checking and no
  option for it.
- Outbound ssh sessions are rows of the main table (STATE `SSH`, SRC `ssh`, `⇄ ssh user@host`,
  forwards listed, `k` closes the session).
- Kubernetes: `← →` switch tabs (Tab still works), `N` picks the namespace from the cluster's
  list, `K` is shown dimmed when kubectl is missing and says so when there is no context.
- Fixed: a refresh interval shorter than one collection cycle left the table empty forever
  (each tick cancelled the running refresh); a running refresh is now left alone.
- Demo mode (`lirts --demo`): the dashboard over a synthetic machine with a scripted
  timeline, for developing and trying lirts without the real thing. Record and replay
  (`lirts record 5m -o FILE`, `lirts --replay FILE`): frames carry the whole snapshot, actions
  are disabled while replaying. `list` and `explain` accept both flags.
- `editor` setting: `O` opens the project folder in that editor (cursor, code, idea…) instead
  of the file manager; `lirts doctor` checks the command exists.
- Per-container traffic: the Docker tab shows the rate, the totals since the container started
  and a history; the stacks screen (`g`) has a TRAFFIC column per stack naming the busiest
  container; `lirts stack list` prints the rates. From the Docker stats already collected.
- Traffic panel: a btop-style net box under the table (own address and interface, download
  above the midline, upload below, current / top / total per direction). `N` switches between
  the whole machine and the selected row; `net_panel` turns it off, and then nothing is
  computed for it.
- Traffic history per row: in / out sparklines in the History tab (from the rates lirts
  already samples), persisted with the rest of the history.
- Per-connection traffic, opt-in (`bandwidth.connections`): every client → port link in `w` and
  in the Connections tab shows its rate in / out, totals and a small history. macOS samples
  with `nettop -t loopback`, Linux with `ss -ti`; nothing runs unless it is switched on.
- Memory of recurring issues: per port and kind (conflict, stopped, restarted, replaced,
  failing, degraded, killed or restarted from lirts) counted for `insights.pattern_days`
  (30) in lirts' own state dir; involved services and last time remembered. Shown as a
  Patterns tab in Explain, in the Events screen summary, in the History tab of the row, and
  by `lirts patterns` (`--clear` forgets). Learned only from what lirts observes.
- Proxy routes: virtual hosts and upstreams of local nginx (`nginx -T`) and httpd (`httpd -S`,
  plus `ProxyPass` from the config files the binary names), shown in `w`, Explain and
  `lirts routes`; upstream ports get the domain names, proxies get the targets. Asked once at
  start and on `r` in `w`; `proxies.enabled` turns it off. Hosts-file names listed in `w`.
- Setup wizard (`W`, `lirts setup`, and by itself on the first start without a config file):
  every setting step by step with arrow keys, saved at the end.
- `sources` setting: which sources appear (local, docker, ssh, kubernetes), in Settings and
  the wizard.
- Reachability check (`R` on an ssh row, `R` in the Kubernetes screen for the API server,
  `lirts reach HOST [PORT]`): DNS, one ping, TCP connect. On demand only.
- `lirts --version` prints the directory the code runs from.
- Health check scope: `H` checks the marked rows when any are marked, otherwise every visible
  row; the screen title says which, and each row shows when it was checked.
- Explain screen: tabs per topic (Summary, Stack, Network, Warnings, Changes, Kubernetes,
  Events, Profiles) switched with `← →`, coloured headings, warnings and events; the title
  says it covers every row.
- Helper processes joining or leaving a port (an updater, a child that inherited the socket)
  are no longer counted as restarts; the main PID staying alive is reported as a change.
- Kills and local restarts done from lirts are recorded in the event log, so a port that
  stops or comes back on another port afterwards is explained.
- `docker exec` that fails keeps the terminal on the error until Enter, and reports the exit code.
- CI: a push runs one Ubuntu job (about two billed minutes); the full matrix with macOS runs
  only by hand (workflow_dispatch). Docs-only changes skip CI; a newer push cancels a running one.
- Config schema 3. Keys that no longer exist are ignored on load and reported (dashboard
  notice, Settings, `lirts doctor`); `lirts config upgrade` rewrites the file on request.
  lirts never modifies the config file by itself.
- `lirts doctor`: Python, config file and schema, state directory, socket access, Docker,
  kubectl reachability, nettop, notification backend and terminal size, with hints.
- Command palette (Ctrl+P) with every action and filter field.
- Structured filters: `key:value`, ranges (`port:3000-3999`) and negation (`-system`).
- The config file is hot-reloaded when edited; sort column, direction and grouping are
  remembered in it.
- Settings screen (`,`): shows the lirts version, the config file in use and whether it is
  outdated, and lets every option be changed in place with immediate effect (theme, refresh,
  columns via a checklist, grouping, panel, UDP, system services, thresholds, probes, Docker,
  Kubernetes, history); `s` saves, `R` resets. Config files now carry a `version` and old
  ones are flagged. A one-time welcome hint points at `?` and `,`.
- Kubernetes support through `kubectl`, generic and read-only towards the kubeconfig: a `K`
  screen with pods, services and port-forwards (logs, previous logs, shell, rollout restart,
  delete pod, start / stop port-forwards, switch namespace for the session), background
  refresh with timeouts, `kubectl port-forward` and `ssh -L` tunnels identified as rows with
  a warning when the forward's target no longer exists, a cluster summary in `explain`, and
  `lirts kube pods|services|logs|exec|restart|forward|forwards|stop|contexts`.
- Freshly started services are highlighted (cyan, ✚) for `insights.highlight_new_minutes`.
- The sort column shows a ▲ / ▼ arrow in its header and the sort notification says which end
  is which; equal values no longer shuffle between refreshes (port is the tiebreak).
- The table keeps its scroll position when rows are rebuilt after a refresh.
- Column lists written by older `config init` runs are upgraded to the current default, so
  PROJECT (the compose stack) appears without editing the file.
- Grouped view (`G`, or `group_by_stack: true`): rows sit under collapsible headers per compose
  stack or project, drawn as a tree (chevron and name on the header, `├─` / `└─` connectors on
  the members) with summed CPU / memory and the worst status; the side panel summarises the
  group and `x` / `t` / `l` on a header act on the whole stack. PROJECT is now a default column.
- Events screen: an ongoing / resolved state per event and a summary of ongoing conditions
  (with duration and suggested fix) and recurring ones (restart loops, flapping ports).
- Row emphasis: error rows bold red with ✖, warning rows bold yellow with ⚠ on the port,
  service, process and status cells; `!` toggles a problems-only view.
- Recently stopped services stay visible as dimmed `STOPPED` rows for
  `insights.show_stopped_minutes` (default 5) with a "stopped N ago" warning.
- `Esc` on the main screen clears the filter, then the marks, and otherwise quits.
- A loading indicator over the table and a "starting…" subtitle until the first
  collection has finished.
- Notification centre (`n`): the full event log with timestamps, levels, a legend explaining
  the `[+] [-] [~] [!] [✓]` markers, an unread counter in the top bar, jump-to-port and clear.
  Toasts are now shown only for error-level events that happen during the session; events
  recorded before launch are never replayed as toasts, and stored events older than the
  history retention are dropped on load.
- Compose stack view (`g`) and `lirts stack list|restart|stop|logs`: restart or stop a whole
  project, read its interleaved logs, open its folder; `Enter` filters the table by stack.
- `O` opens the project folder of the selected row (compose working dir or process cwd).
- Profiles from live state or a compose file: `lirts profile save NAME [--from-compose PATH]
  [--project P] [--dry-run]` and `P` in the TUI (saves the visible rows).
- Multi-select: `Space` marks rows, `a` marks all visible, `Esc` clears; kill, stop and
  restart act on every marked row, with a combined blast-radius preview.
- `lirts watch [--notify] [--filter WORDS] [--interval S]`: streams start / stop / restart /
  degraded / failing / recovered events, optionally as desktop notifications
  (macOS `osascript` / `terminal-notifier`, Linux `notify-send`).
- Health-transition events in the history (degraded, failing, recovered) shown in the top bar.
- Restart of local dev servers: `t` on a local row (or `lirts restart PORT`) kills the process
  and re-runs its recorded command in its recorded directory with its environment, after a
  confirmation that shows the command and the blast radius. Output lands in
  `~/.local/state/lirts/restarts/<port>.log`.
- Shell completion documented (`lirts --install-completion`, `make completion`).
- Distribution: `install.sh` one-liner, Homebrew formula template, GitHub Actions CI matrix
  (macOS + Linux, Python 3.11 to 3.13) and a release workflow that builds sdist and wheel.

## [0.5.0] - 2026-09-20

The Phase 2 release: the Python tool is now complete, installable and tested.

### Added
- Installable `lirts` package with a `lirts` console script (`pip install -e .` / `pipx`).
- btop-style layout: top bar with CPU / memory / network graphs, service summary and
  event stream; main table; contextual side panel; footer.
- Extended details view (`i` / Enter) with Overview, Processes, Docker, Connections,
  History and Identity tabs, updated live while open.
- Service identity engine v2 with roles, confidence scores and human-readable
  reasoning, covering ~90 process, image, header and port rules plus user aliases.
- Project auto-tagging from working directories and compose projects.
- Anomaly detection: port conflicts (local vs Docker included), zombies, crash loops,
  unhealthy containers, unreachable web services, 5xx responses, slow responses,
  CPU / memory thresholds and stale dev servers, each with a suggested action.
- Kill confirmation with a blast-radius preview (dependents, proxies, shared processes,
  the Docker port proxy), with SIGTERM or SIGKILL.
- Real `docker exec` shells from the TUI (`e`), stop / restart with confirmation,
  log viewer with reload and follow.
- Per-container CPU, memory, network and PID counts from the Docker stats API.
- Docker environment variables (masked), volumes / bind mounts, health, restart count,
  compose project and service, and docker-only rows for ports psutil cannot see.
- Silent HTTP health probe with status, latency and headers; HEAD with GET fallback,
  HTTPS fallback, non-HTTP detection, per-port caching.
- Rolling history with persistence: CPU / connection / latency / activity sparklines,
  restart detection, start / stop / restart / conflict events, uptime.
- Multiple PIDs per port with shared-port explanations (dual-stack, prefork workers,
  SO_REUSEPORT, conflicts).
- Activity levels (idle / active / hot) and best-effort per-process bandwidth on macOS.
- Reverse proxy chain detection (`443 → Nginx → 9000 → PHP-FPM`).
- `/etc/hosts` awareness and smart "open in browser" URLs.
- "Explain my machine" (`E`) with inferred stack, warnings, recommendations,
  profile checks and the diff since the last session.
- Dev-environment profiles and snapshot diffs (`lirts profile check`, `lirts snapshot`).
- Scriptable CLI: `lirts list [--json]`, `lirts kill`, `lirts explain`, `lirts config`.
- UDP sockets (`u`), hide system services (`h`), side panel toggle (`p`), sorting by
  any column (`s`, `S`, or click a header), configurable columns, theme cycling (`T`).
- XDG config (`~/.config/lirts/config.yaml`) merged over documented defaults, and
  state in `~/.local/state/lirts` (history, snapshots, log).
- 93 tests covering collectors, identity, insights, history, engine, TUI and CLI;
  ruff, black and mypy clean.

### Fixed
- CPU usage was always 0.0 because a fresh psutil handle was created every refresh.
- Kill read the PID from the wrong column and crashed on non-numeric cells.
- Row selection by port alone picked the wrong row for dual-stack / multi-PID ports.
- Async refresh was invoked without `await` after kill / stop / restart.
- Invalid `textual` theme name crashed theme cycling.
- Every TCP port (including databases) received an HTTP GET every two seconds.
- Config was loaded relative to the working directory and not merged with defaults.
- Metrics leaked between rows through a `locals()` lookup.

### Changed
- `src/` replaced by the `lirts` package; `main.py` is now a thin wrapper.
- `config.yaml` in the repository became `config.example.yaml`.

## [0.1.0] - 2026-04

Initial Python MVP: port listing, Docker detection, filtering, kill, details, logs.
