---
name: usage-widget
description: Start the Claude usage widget (GNOME top bar + Claude Code status line) if it isn't running, for example after a reboot, and show current plan usage. Use when the user asks to start, activate, restart or check the usage widget.
allowed-tools: Bash(__REPO__/start.sh:*)
---

# Start the Claude usage widget

1. Run `__REPO__/start.sh`. It starts the top-bar widget only if it isn't already running, so it's safe to run any time.
2. Tell the user in two or three short lines:
   - whether the widget was started or was already running
   - the current usage: session and weekly percentages, and when each resets
   - any line starting with `!` or `✗`, with the fix the script suggests

Don't run `install.sh`, edit settings, or change the code unless the user asks.
