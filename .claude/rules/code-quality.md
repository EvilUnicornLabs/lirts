---
paths:
  - "**/*.py"
---

# Python code quality

- **No magic numbers or hard-coded values in logic.** Ports, timeouts, limits, window sizes,
  retry counts, paths, glyphs used in several places: they come from the config (when the user
  may tune them) or from one constants module (when they are fixed). A literal is fine only
  where it is the definition itself.
- **Single source of truth.** A constant, a type, a default, a key name is defined once and
  imported everywhere. The config defaults, the settings registry and `config.example.yaml`
  describe the same keys.
- **DRY.** Two functions doing the same job become one. Rendering helpers shared by the TUI and
  the CLI live in one place.
- **Typed data between functions.** Dataclasses for every structured value. No `dict[str, Any]`
  passed around as a record. The parsed config is accessed through typed accessors, not by
  digging into nested dicts at the call site.
- **No hidden state.** No module-level mutable state and no implicit singletons. Caches,
  stores and clients are created explicitly, owned by the engine or the app, and closed by it.
- **Fail fast on internal invariants, degrade on the outside world.** A missing internal thing
  (a required config key after merging defaults, a node id that must exist, a frame in the
  wrong shape) raises immediately with a specific exception. A missing or failing external tool
  (docker daemon, kubectl, nettop, ss, ssh, a proxy binary) is handled: the feature is off, the
  reason is recorded for `lirts doctor` and the UI, and the refresh continues.
- **Never catch and rethrow without adding value.** Catch to add context, to degrade or to log
  once. Never a bare `except:`, never `except Exception: pass`. Suppressed exceptions are
  named (`contextlib.suppress(FileNotFoundError)`).
- **Logging, not printing.** Library code uses `logging.getLogger(__name__)` into lirts.log.
  `print` and `console.print` only in the CLI layer that renders output for the user.
- **No ignored warnings.** Lint, type and runtime warnings are fixed or justified in place with
  the reason (`# type: ignore[code]  # why`). No blanket ignores in `pyproject.toml`.
- **Bounded module responsibility, 400 lines at most.** One module, one job: collectors
  collect, the engine assembles, insights reason, the TUI renders. A file that grows past 400
  lines is split by responsibility, not by line count.
- **No speculative abstractions and no unrequested features.** Build what the TODO item says.
  Validate that it works (test, walkthrough) before adding to it.
- **No architecture-breaking shortcuts.** If a shortcut would need a refactor to undo, it is
  not acceptable. Temporary code is not committed.
- **pyproject.toml for everything.** Dependencies, build, ruff, mypy, pytest. No
  `requirements.txt`, `setup.py` or scattered config files.
