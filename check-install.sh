#!/bin/bash
# Exercise the real installer with isolated files and simulated macOS services.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CHECK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/monitask-install.XXXXXX")"
PROJECT="$CHECK_DIR/project space"
export CHECK_DIR

cleanup() {
  local result=$? name
  if [ "$result" -ne 0 ] && [ -f "$CHECK_DIR/output" ]; then cat "$CHECK_DIR/output" >&2; fi
  for name in morning lunch afternoon evening night-start night-stop watch; do
    rm -f "$CHECK_DIR/home/Library/LaunchAgents/com.dannylin.monitask-center.$name.plist"
  done
  rm -f "$PROJECT/Makefile" "$PROJECT/setup.sh" "$PROJECT/install.sh" "$PROJECT/run.sh" \
    "$PROJECT/schedule.json" "$PROJECT/.env" "$PROJECT/MonitaskCenter.app/Contents/Info.plist" \
    "$PROJECT/MonitaskCenter.app/Contents/MacOS/python-runner" \
    "$CHECK_DIR/bin/uname" "$CHECK_DIR/bin/brew" "$CHECK_DIR/bin/codesign" \
    "$CHECK_DIR/bin/launchctl" "$CHECK_DIR/bin/open" "$CHECK_DIR/python-fixture" \
    "$CHECK_DIR/python/bin/python3.13" "$CHECK_DIR/deps" "$CHECK_DIR/calls" "$CHECK_DIR/output" \
    "$CHECK_DIR/home/.claude/scripts/tg-send.sh" "$CHECK_DIR/env-before"
  for name in "$PROJECT/MonitaskCenter.app/Contents/MacOS" "$PROJECT/MonitaskCenter.app/Contents" \
    "$PROJECT/MonitaskCenter.app" "$PROJECT" \
    "$CHECK_DIR/home/Library/LaunchAgents" "$CHECK_DIR/home/Library/Application Support/MonitaskCenter" \
    "$CHECK_DIR/home/Library/Application Support" "$CHECK_DIR/home/Library" \
    "$CHECK_DIR/home/.claude/scripts" "$CHECK_DIR/home/.claude" "$CHECK_DIR/home" \
    "$CHECK_DIR/python/bin" "$CHECK_DIR/python" "$CHECK_DIR/bin" "$CHECK_DIR"; do
    if [ -d "$name" ]; then rmdir "$name"; fi
  done
}
trap cleanup EXIT
mkdir -p "$PROJECT/MonitaskCenter.app/Contents/MacOS" "$CHECK_DIR/bin" \
  "$CHECK_DIR/python/bin" "$CHECK_DIR/home/.claude/scripts"
cp "$HERE/Makefile" "$HERE/setup.sh" "$HERE/install.sh" "$HERE/run.sh" "$PROJECT/"
cp "$HERE/MonitaskCenter.app/Contents/Info.plist" "$PROJECT/MonitaskCenter.app/Contents/"
printf '{"days":{}}\n' > "$PROJECT/schedule.json"
printf 'OTHER_SETTING=preserved\n' > "$PROJECT/.env"
cp "$PROJECT/.env" "$CHECK_DIR/env-before"

cat > "$CHECK_DIR/python-fixture" <<'SH'
#!/bin/bash
set -eu
case "$*" in
  --version) echo 'Python 3.13.15' ;;
  '-m pip install '*) echo pip >> "$CHECK_DIR/calls"; touch "$CHECK_DIR/deps" ;;
  '-c import '*) [ -f "$CHECK_DIR/deps" ] ;;
  '-c pass') exit 0 ;;
  *' status') echo "accessibility_trusted=${CHECK_TRUSTED:-True}" ;;
  *' plan') echo 'Schedule plan checked' ;;
  *) exit 1 ;;
esac
SH
cat > "$CHECK_DIR/bin/brew" <<'SH'
#!/bin/bash
set -eu
case "$1" in
  --prefix) echo "$CHECK_DIR/python" ;;
  install) echo brew >> "$CHECK_DIR/calls"; cp "$CHECK_DIR/python-fixture" "$CHECK_DIR/python/bin/python3.13" ;;
  *) exit 1 ;;
esac
SH
cat > "$CHECK_DIR/bin/launchctl" <<'SH'
#!/bin/bash
set -eu
case "$1" in
  bootstrap)
    if [ "${CHECK_LOAD_FAIL:-0}" = 1 ]; then exit 1; fi
    /usr/bin/plutil -lint "$3" >/dev/null
    echo loaded >> "$CHECK_DIR/calls" ;;
  bootout) exit 0 ;;
  list) echo 'com.dannylin.monitask-center.watch' ;;
  *) exit 1 ;;
esac
SH
printf '#!/bin/bash\necho Darwin\n' > "$CHECK_DIR/bin/uname"
printf '#!/bin/bash\necho signed >> "$CHECK_DIR/calls"\necho "# signed" >> "$CHECK_DIR/project space/MonitaskCenter.app/Contents/MacOS/python-runner"\n' > "$CHECK_DIR/bin/codesign"
printf '#!/bin/bash\nexit 0\n' > "$CHECK_DIR/bin/open"
chmod +x "$CHECK_DIR/python-fixture" "$CHECK_DIR/bin/uname" "$CHECK_DIR/bin/brew" \
  "$CHECK_DIR/bin/codesign" "$CHECK_DIR/bin/launchctl" "$CHECK_DIR/bin/open"

# HOME is overridden only for child processes; never change the shell's HOME.
install_project() {
  env HOME="$CHECK_DIR/home" PATH="$CHECK_DIR/bin:$PATH" \
    CHECK_TRUSTED="${CHECK_TRUSTED:-True}" CHECK_LOAD_FAIL="${CHECK_LOAD_FAIL:-0}" \
    make -C "$PROJECT" install > "$CHECK_DIR/output" 2>&1
}
install_project || { cat "$CHECK_DIR/output" >&2; exit 1; }
cmp "$PROJECT/.env" "$CHECK_DIR/env-before"
[ "$(grep -c '^loaded$' "$CHECK_DIR/calls")" -eq 7 ]
install_project
[ "$(grep -c '^brew$' "$CHECK_DIR/calls")" -eq 1 ]
[ "$(grep -c '^pip$' "$CHECK_DIR/calls")" -eq 1 ]
[ "$(grep -c '^signed$' "$CHECK_DIR/calls")" -eq 1 ]
[ "$(grep -c '^loaded$' "$CHECK_DIR/calls")" -eq 14 ]

# Configured Telegram must retain unrelated settings and keep the token hidden.
touch "$CHECK_DIR/home/.claude/scripts/tg-send.sh"
printf 'TG_BOT_TOKEN=123:synthetic_fixture\nTG_CHAT_ID=-123\n' >> "$PROJECT/.env"
install_project
grep -q '^OTHER_SETTING=preserved$' "$PROJECT/.env"
! grep -q '123:synthetic_fixture' "$CHECK_DIR/output"
[ "$(stat -f '%Lp' "$PROJECT/.env")" = 600 ]

if CHECK_TRUSTED=False install_project <<< $'n\n\n'; then
  echo 'FAIL: installer accepted missing Accessibility permission' >&2; exit 1
fi
[ "$(grep -c '^loaded$' "$CHECK_DIR/calls")" -eq 21 ]
if CHECK_LOAD_FAIL=1 install_project; then
  echo 'FAIL: installer accepted a failed launchd bootstrap' >&2; exit 1
fi
! grep -q 'Setup complete' "$CHECK_DIR/output"
echo 'Installer checks passed: fresh setup, repeat setup, config preservation, permission and job failures.'
