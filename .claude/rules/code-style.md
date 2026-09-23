---
paths:
  - "**/*.py"
---

# Python code style

- **snake_case everywhere.** Files, modules, functions, variables. PascalCase for classes only.
  CLI commands and options are hyphenated (`config upgrade`, `--no-docker`).
- **Docstrings on modules and public functions.** Brief and factual. The module docstring says
  what the module is for. Function docstrings use Args / Returns / Raises only when the
  signature does not already say it. No docstrings on obvious one-liners.
- **Comments only for a non-obvious why.** Code is self-documenting through names. Never a
  comment that restates what the next line does.
- **Return early.** Edge cases and error conditions at the top; the main logic at the end,
  unindented.
- **Meaningful, specific names.** `listener_port` not `p`, `started_at` not `start`. No bare
  `handler`, `data`, `utils`, `helpers` without a domain prefix, in files or in symbols.
- **Keyword-only arguments from three parameters up.** `def f(a, *, b, c)`. Positional calls
  with three or more arguments are not readable at the call site.
- **Str enums for every status and kind field.** `class Level(enum.StrEnum)` (its `str()` and
  `format()` give the value, unlike `(str, Enum)` on 3.11+). The members are what the code
  compares against; no raw string comparisons such as `level == "warn"` in logic.
- **Type hints on everything.** Parameters, return types, and variables where the type is not
  obvious. `X | None`, `list[X]`, `dict[K, V]`; never `Optional`, `List`, `Dict`.
  `from __future__ import annotations` at the top of every module.
- **Modern Python.** f-strings, `pathlib.Path`, pattern matching where it reads better,
  dataclasses with `slots=True` for value objects.
- **Imports.** Grouped stdlib, third-party, `lirts`; absolute (`from lirts.models import ...`);
  no wildcard imports; no imports inside functions except to break a real import cycle or to
  keep an optional dependency optional, with a comment saying which.
- **One formatter, one linter.** `ruff format` and `ruff check`, configured in `pyproject.toml`,
  100 columns. No other formatter or lint config file.
- **Textual.** Bindings are declared in `BINDINGS` with a description, and every binding that
  can be unavailable implements `check_action` so the footer dims it. Styles live in
  `styles.tcss`, not in Python strings.
