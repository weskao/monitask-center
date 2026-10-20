# CLAUDE.md

## Upgrade migrations (required for every release)

"Update now" runs the **old** version's `cli._upgrade` steps; the new code only gets control on
its next run. So any change that needs the installed state on a user's machine to change must
ship with a migration that the new code runs by itself — never "tell users to run install".

- **What counts:** adding/renaming/removing a launchd job or changing a job's plist
  (`launchd.render`), changes to `run.sh` / `MonitaskCenter.app` / `pylibs`
  (`install.render_runner`, `install.prepare_runtime`), renamed/removed/re-meant keys in
  `config.json`, moved or reshaped files under `~/.monitask-center/`.
- **Where it runs:** `cmd_check` in `engine.py` (hourly, unattended) and `cli._main` (every
  command except `install`/`uninstall`). Patterns: `launchd.install_missing`, `install.migrate`.
  A change that breaks `run.sh` itself can only be fixed from `cli._main` or `_upgrade`'s
  migrate step: the hourly job cannot start to repair it.
- **Rules:** idempotent; costs only a few stats when there is nothing to do; never boots out the
  running `check` job; never raises — returns a message to log/print, and on failure that message
  tells the user to run `monitask-center install`.
- **Already covered:** a new launchd job (`install_missing`); a new settings key with a default
  (`config.load` falls back); a moved runtime or a stale `run.sh` (`install.migrate`). A changed
  plist of an existing job is synced by `_upgrade` only for updates started from a version that
  has the sync step — older ones need a migration.
- **Done means:** a test that rebuilds the pre-upgrade state, runs the migration, and asserts it is
  fixed and a no-op the second time; then recreate the old state on the real machine and run
  `monitask-center status`. Note the upgrade behavior in README.

## Adding a CLI command

One entry in `cli.COMMANDS` is the whole registration; everything else derives from it — never
hand-copy a command list or an alias. When a new command doesn't fit the shared code, refactor
that code instead of special-casing the command.

- **Derived automatically:** the subparser and its `--help` line (`cli.help.<name>` in
  `locales/*/cli.json`, guarded by `test_i18n_catalog`), the `--<name>` alias
  (`_normalize_argv`), and the help note on that alias (`cli.epilog`). Don't repeat
  "(同 --x)" in help strings.
- **Add by hand:** a `case` in `_main`; `EXEC_COMMANDS` if it execs (skips the update check and
  migrations); a row in README's「其他指令」table; a Makefile target + `make help` line for
  user-facing ones.
- **Reuse:** read installed plists through `launchd.runner(job)`, exec through `cli._exec`.
- **Test:** a `tests/test_cli.py` case for its dispatch; the parametrized alias/help tests pick
  up the new name by themselves.
