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
