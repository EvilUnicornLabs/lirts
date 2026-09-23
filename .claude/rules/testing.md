---
paths:
  - "tests/**/*.py"
---

# Testing

- **pytest functions with plain `assert`.** No `unittest.TestCase`. Async tests run under
  `asyncio_mode = "auto"`.
- **Tests never touch the real machine.** No real docker, kubectl, nettop, ssh, network or the
  user's config and state directories. Collectors are tested on captured command output, the
  engine and the TUI on the demo engine, recorded frames or fakes, and every test uses a temp
  config and state directory.
- **Arrange, act, assert.** Each test reads top to bottom: build the input, call the function,
  assert the result. No jumping between helpers and assertions.
- **Self-contained tests.** Inline the data when it makes the test clearer. Fixtures are for
  shared infrastructure (temp dirs, the demo engine, a pilot app), not for hiding test data.
- **Behaviour-describing names.** `test_restart_is_not_counted_when_a_helper_pid_leaves`, not
  `test_history_3`.
- **Literal expectations.** Assert against raw values. Never build the expected value with the
  function under test or with production constants.
- **Parametrize repeated shapes** with `@pytest.mark.parametrize` and descriptive ids.
- **No mock spaghetti.** A test that needs five mocks points at production code that does too
  much; split the code instead.
- **Every command, key and setting has a test.** A Typer runner test per CLI command, a Textual
  pilot test per key and screen, a settings round-trip per setting. A feature is not done
  without its tests and its step in `local/TESTING.md`.
- **Fast and deterministic.** No sleeps for timing, no dependence on wall-clock or on the order
  of tests. The whole suite runs in well under a minute.
