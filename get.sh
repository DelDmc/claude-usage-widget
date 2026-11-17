#!/usr/bin/env bash
# Install (or update) the Claude usage widget on a computer without a copy of this repo:
#
#   curl -fsSL https://raw.githubusercontent.com/DelDmc/claude-usage-widget/main/get.sh | bash
#
# Downloads the repo to ~/.local/share/claude-usage-widget (or pulls the latest
# version if it's already there) and runs install.sh from it.
# Override the location with CLAUDE_USAGE_WIDGET_DIR=/some/path.

main() {  # wrapped in a function so a partly downloaded script never runs
  set -euo pipefail
  local repo="${CLAUDE_USAGE_WIDGET_REPO:-https://github.com/DelDmc/claude-usage-widget.git}"
  local dest="${CLAUDE_USAGE_WIDGET_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-usage-widget}"

  local cmd pkg
  for cmd in git:git jq:jq /usr/bin/python3:python3; do
    pkg="${cmd##*:}"; cmd="${cmd%:*}"
    if ! command -v "$cmd" >/dev/null; then
      echo "✗ $cmd is missing. Install it with: sudo apt install $pkg" >&2
      exit 1
    fi
  done

  if [[ -d "$dest/.git" ]]; then
    echo "Updating $dest"
    git -C "$dest" pull --ff-only --quiet
  else
    echo "Downloading to $dest"
    mkdir -p "$(dirname "$dest")"
    git clone --quiet --depth 1 "$repo" "$dest"
  fi
  "$dest/install.sh"
}

main "$@"
