# CLAUDE.md

## Upgrade migrations (every release)

"Update now" runs the **old** version's `cli._upgrade`; new code gets control only on its next
run. Any change to installed state on a user's machine ships with a migration the new code runs
by itself. Never "tell users to run install".

- **Triggers:** add/rename/remove a launchd job or change its plist (`launchd.render`); changes
  to `run.sh` / `MonitaskCenter.app` / `pylibs` (`install.render_runner`,
  `install.prepare_runtime`); renamed/removed/re-meant `config.json` keys; moved or reshaped
  files under `~/.monitask-center/`.
- **Runs from:** `engine.cmd_check` (hourly, unattended) and `cli._main` (every command except
  `install`/`uninstall`); patterns `launchd.install_missing`, `install.migrate`. A change that
  breaks `run.sh` itself is repairable only from `cli._main` or `_upgrade`'s migrate step.
- **Contract:** idempotent; a few stats when nothing to do; never boots out the running `check`
  job; never raises: returns a message to log/print, on failure telling the user to run
  `monitask-center install`.
- **Already covered:** new launchd job (`install_missing`); new settings key with a default
  (`config.load`); moved runtime or stale `run.sh` (`install.migrate`). A changed plist of an
  existing job is synced by `_upgrade` only from versions that have the sync step; older ones
  need a migration.
- **Done =** a test that rebuilds the pre-upgrade state, runs the migration, asserts it is fixed
  and a no-op the second time; then recreate the old state on the real machine and run
  `monitask-center status`; note the upgrade behaviour in README.
