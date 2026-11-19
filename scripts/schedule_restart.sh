#!/bin/bash
# Daily automatic restart of the dashboard at 1:22 AM (macOS launchd, your user only).
#   ./scripts/schedule_restart.sh install     -> turn it on
#   ./scripts/schedule_restart.sh uninstall   -> turn it off
#   ./scripts/schedule_restart.sh status      -> is it on?
# The Mac must be awake (or set to wake) at 1:22; if it's asleep, launchd runs the job when it wakes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.symptomlens.restart"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/logs"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/restart.sh</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>1</integer><key>Minute</key><integer>22</integer></dict>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <!-- keep the dashboard running after restart.sh exits -->
  <key>AbandonProcessGroup</key><true/>
  <key>StandardOutPath</key><string>$ROOT/logs/schedule.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/schedule.log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    echo "Installed: SymptomLens restarts every day at 01:22. Plist: $PLIST"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed the daily restart."
    ;;
  status)
    if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
      echo "ON: restarts daily at 01:22 ($PLIST)"
    else
      echo "OFF"
    fi
    ;;
  *)
    echo "usage: $0 install|uninstall|status" >&2; exit 2 ;;
esac
