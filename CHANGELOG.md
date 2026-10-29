## [1.10.0] - 2026-10-29

### 🚀 Features

- **config:** Add on/off switch for activity alerts
## [1.9.7] - 2026-10-28

### 🚀 Features

- **launchd:** Add sync_drifted to auto-reload stale jobs on cli
## [1.9.6] - 2026-10-27

### 🐛 Bug Fixes

- **install:** Refresh the activity job's copy on upgrade
## [1.9.5] - 2026-10-27

### 🚀 Features

- **status:** Refetch activity rate when stale
## [1.9.4] - 2026-10-27

### 🚀 Features

- **activity:** Add a record-only fetch mode
- **config:** Fetch today's activity in the background
## [1.9.3] - 2026-10-27

### 🚀 Features

- **activity:** Show today's rate in status

### 🐛 Bug Fixes

- **launchd:** Clarify activity migration errors
- **status:** Add a space before the activity update time
## [1.9.2] - 2026-10-27

### 🚀 Features

- **activity:** Prefer the keychain telegram bot token

### 🐛 Bug Fixes

- **activity:** Run the job outside the protected checkout
- **install:** Run background jobs from a package copy
## [1.9.1] - 2026-10-27

### 🚀 Features

- **activity:** Alert when daily activity is low
## [1.9.0] - 2026-10-22

### 🚀 Features

- **cli:** Add activity command, alias every command
- **activity:** Print a coloured report in the terminal

### 🐛 Bug Fixes

- **activity:** Report latest activity slot

### 📚 Documentation

- Describe how to add a cli command
- **todo:** Use 分 for minutes in total-time note
- Scope cli command guidance

### ⚙️ Miscellaneous Tasks

- **lint:** Add ruff with pinned rule set
## [1.8.0] - 2026-10-19

### 🚀 Features

- **install:** Check and repair accessibility grant

### 🐛 Bug Fixes

- **engine:** Log outcomes and prompt for access

### 📚 Documentation

- Describe the install accessibility check
## [1.7.0] - 2026-10-16

### 🚀 Features

- **cli:** Add `update` / `--update` command

### 🐛 Bug Fixes

- **update:** End the migration notice with a newline
## [1.6.3] - 2026-10-15

### 🐛 Bug Fixes

- **engine:** Infer timing state from DateEnd
## [1.6.2] - 2026-10-14

### 🐛 Bug Fixes

- **overtime:** Notify every dialog outcome
## [1.6.1] - 2026-10-13

### 🐛 Bug Fixes

- **update:** Match config menu cursor handling
- **engine:** Treat empty/zero-byte periods dir as paused

### 📚 Documentation

- Explain setting workdays and vacation
- Clarify supported operating systems

### 🧪 Testing

- **i18n:** Skip binary files in control-string scan
## [1.6.0] - 2026-10-12

### 🚀 Features

- Install runtime without a checkout

### 🚜 Refactor

- Set comments to english

### 📚 Documentation

- Add setup and telegram screenshots
- Add config menu screenshot
## [1.5.0] - 2026-10-12

### 🚀 Features

- Add Node.js support for activity rate notifications
- **overtime:** Ask at 18:30 whether to stop timing

### 🐛 Bug Fixes

- **activity:** Read authenticated report data
- **install:** Run the checkout, not a stale venv copy
- **launchd:** Install jobs added by an upgrade

### 📚 Documentation

- Require upgrade migrations for each release
## [1.4.0] - 2026-10-08

### 🚀 Features

- **config:** Add auto play/pause toggle

### 🐛 Bug Fixes

- **activity:** Align reporting with shared workday settings
- **i18n:** Clarify update skip shortcut
## [1.3.0] - 2026-10-07

### 🚀 Features

- Show vacation storage path

### 🐛 Bug Fixes

- **install:** Clear bundle quarantine before probes
## [1.2.0] - 2026-10-06

### 🚀 Features

- **update:** Offer newer releases after a command

### 🐛 Bug Fixes

- **version:** Read __version__ from package metadata
- **tui:** Mention ctrl+c and autosave in key hints
## [1.1.0] - 2026-10-06

### 🧪 Testing

- **tui:** Follow installed version

### ⚙️ Miscellaneous Tasks

- Add Dependabot config (uv)
## [1.0.0] - 2026-10-05

### 🚀 Features

- **check:** [**breaking**] Auto play and pause monitask
- **engine:** Read state from local period files

### 📚 Documentation

- **todo:** Add high-priority task for local-file-based Monitask state detection
## [0.3.0] - 2026-10-04

### 🚀 Features

- **install:** Verify launchd status
- **tui:** Add solid thick wordmark as preferred logo tier
- **tui:** Redraw big logo tier as solid blocks
- **telegram:** Store the bot token in the OS credential store

### 🐛 Bug Fixes

- Center logo and tagline in TUI panel
- **tui:** Widen pointer glyph to three columns
## [0.2.0] - 2026-10-02

### 🚀 Features

- Add monitask_center package

### 🚜 Refactor

- [**breaking**] Run monitask.py on the package, drop shell installers

### 📚 Documentation

- Rewrite readme for the monitask-center cli

### 🧪 Testing

- Add suite for monitask_center package

### ⚙️ Miscellaneous Tasks

- **release:** Bump version to 0.1.0
- Ignore local agent memory
- **release:** Bump version to 0.2.0
## [0.1.0] - 2026-09-30

### 🚀 Features

- Add monitask center automation
- Add interactive setup tooling
- **install:** Automate first-time setup

### 🐛 Bug Fixes

- Update launchd labels for new bundle ids

### ⚙️ Miscellaneous Tasks

- Add project ignore rules
- Ignore codegraph index
