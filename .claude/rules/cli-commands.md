---
paths:
  - src/monitask_center/cli.py
  - src/monitask_center/locales/**
  - tests/test_cli.py
  - Makefile
---
# Adding a CLI command

One entry in `cli.COMMANDS` is the registration; everything else derives from it. Never
hand-copy a command list or alias; when a command doesn't fit the shared code, refactor the
shared code instead of special-casing.

- **Derived:** subparser and `--help` line (`cli.help.<name>` in `locales/*/cli.json`, guarded
  by `test_i18n_catalog`), the `--<name>` alias (`_normalize_argv`) and its epilog note. Don't
  repeat "(同 --x)" in help strings.
- **By hand:** a `case` in `_main`; `EXEC_COMMANDS` if it execs (skips update check and
  migrations); a row in README's「其他指令」table; a Makefile target + `make help` line for
  user-facing ones.
- **Reuse:** `launchd.runner(job)` to read installed plists, `cli._exec` to exec.
- **Test:** a `tests/test_cli.py` dispatch case; the parametrized alias/help tests pick the
  name up by themselves.
