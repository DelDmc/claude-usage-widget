#!/usr/bin/env bash
# Start the Claude usage widget if it isn't running (e.g. after a reboot), then print current usage.
# Safe to run any time: it never starts a second copy. Used by the /usage-widget Claude Code skill.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/usr/bin/python3
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/claude-usage-indicator.desktop"

running() { pgrep -f "$DIR/indicator.py" >/dev/null; }

if running; then
  echo "✓ Top-bar widget is already running"
else
  setsid -f "$PY" "$DIR/indicator.py" >/dev/null 2>&1 </dev/null
  for _ in 1 2 3 4 5 6; do sleep 0.5; running && break; done
  if running; then
    echo "✓ Top-bar widget started"
  else
    echo "✗ Top-bar widget didn't start. To see why, run: $PY $DIR/indicator.py"
    exit 1
  fi
fi

if [[ "$(jq -r '.statusLine.command // ""' "$SETTINGS" 2>/dev/null)" != "$PY $DIR/statusline.py" ]]; then
  echo "! The Claude Code status line isn't set up. Run $DIR/install.sh to add it."
fi
if [[ ! -f "$AUTOSTART" ]]; then
  echo "! The widget isn't set to start at login. Run $DIR/install.sh to fix that."
fi

echo
"$PY" "$DIR/claude_usage.py" show   # waits for the widget's first update if it's in progress
