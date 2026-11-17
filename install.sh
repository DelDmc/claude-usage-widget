#!/usr/bin/env bash
# Claude usage widget: install or remove it for the current user.
#   ./install.sh              start the top-bar indicator at login (and now), add the Claude Code
#                             status line and the /usage-widget skill
#   ./install.sh --uninstall  undo all of that
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=/usr/bin/python3   # system Python: that's where the GTK / AppIndicator bindings live
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/claude-usage-indicator.desktop"
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
STATUS_CMD="$PY $DIR/statusline.py"
SKILL_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/usage-widget"

stop_indicator() {
  pkill -f "$DIR/indicator.py" 2>/dev/null || true
  for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -f "$DIR/indicator.py" >/dev/null || return 0; sleep 0.3; done
}

edit_settings() {  # $1 = jq filter; keeps a backup and the file's permissions
  local tmp
  tmp="$(mktemp "$SETTINGS.XXXXXX")"
  cp -p "$SETTINGS" "$SETTINGS.bak-usage-widget"
  jq --arg cmd "$STATUS_CMD" "$1" "$SETTINGS" > "$tmp"
  chmod --reference="$SETTINGS" "$tmp"
  mv "$tmp" "$SETTINGS"
}

if [[ "${1:-}" == "--uninstall" ]]; then
  stop_indicator
  rm -f "$AUTOSTART" "$SKILL_DIR/SKILL.md"
  rmdir "$SKILL_DIR" 2>/dev/null || true
  if [[ -f "$SETTINGS" ]] && [[ "$(jq -r '.statusLine.command // ""' "$SETTINGS")" == "$STATUS_CMD" ]]; then
    edit_settings 'del(.statusLine)'
  fi
  echo "Removed the indicator, the status line and the /usage-widget skill."
  echo "Cached numbers are in ~/.cache/claude-usage (safe to delete)."
  exit 0
fi

# 1. Claude Code status line (needs nothing extra)
mkdir -p "$(dirname "$SETTINGS")"
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"
current="$(jq -r '.statusLine.command // ""' "$SETTINGS")"
if [[ "$current" == "$STATUS_CMD" ]]; then
  echo "✓ Status line already installed"
elif [[ -n "$current" ]] || jq -e 'has("statusLine")' "$SETTINGS" >/dev/null; then
  echo "! You already have a statusLine in $SETTINGS, so I left it alone."
  echo "  To use this one instead, set its command to: $STATUS_CMD"
else
  edit_settings '.statusLine = {type: "command", command: $cmd, padding: 0}'
  echo "✓ Status line added to $SETTINGS (backup: $SETTINGS.bak-usage-widget)"
fi

# 2. Claude Code skill: /usage-widget starts the widget again, e.g. after a reboot
mkdir -p "$SKILL_DIR"
sed "s|__REPO__|$DIR|g" "$DIR/skill/SKILL.md" > "$SKILL_DIR/SKILL.md"
echo "✓ Claude Code skill installed: type /usage-widget in any project"

# 3. Top-bar indicator
if ! "$PY" -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null &&
   ! "$PY" -c 'import gi; gi.require_version("AppIndicator3", "0.1")' 2>/dev/null; then
  echo "✗ Top-bar indicator not installed: the AppIndicator library is missing. Run:"
  echo "    sudo apt install gir1.2-ayatanaappindicator3-0.1"
  echo "  then run ./install.sh again."
  exit 1
fi
mkdir -p "$(dirname "$AUTOSTART")"
cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=Claude usage
Comment=Claude plan usage in the top bar
Exec=$PY "$DIR/indicator.py"
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=5
NoDisplay=true
EOF
stop_indicator
setsid -f "$PY" "$DIR/indicator.py" >/dev/null 2>&1 </dev/null
echo "✓ Indicator running in the top bar, and set to start at login ($AUTOSTART)"
