#!/usr/bin/env python3
"""Claude Code status line: model name + plan usage from the shared cache.

Prints e.g. "Opus 5.5 · 5h 17% ↻4h31m · wk 62% ↻1d2h". It never waits on the
network: it reads the cache and, when an update is due, starts a background
fetch whose result appears on the next redraw.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_usage as cu  # noqa: E402

ANSI = {"green": "32", "amber": "33", "red": "31", "grey": "90"}


def paint(text: str, color: str) -> str:
    return f"\033[{ANSI[color]}m{text}\033[0m"


def render(info: dict, state: dict, now: float) -> str:
    v = cu.view(state, now)
    model = info.get("model") if isinstance(info, dict) else None
    parts = [model["display_name"]] if isinstance(model, dict) and model.get("display_name") else []
    if not v.limits:
        parts.append(paint(f"usage: {v.error or 'loading…'}", "grey"))
        return " · ".join(parts)
    for lim in v.limits:
        if lim.percent is None:
            parts.append(paint(f"{lim.short} –", "grey"))
            continue
        text = f"{lim.short} {lim.percent:.0f}%"
        if lim.resets_at:
            text += f" ↻{cu.fmt_duration(lim.resets_at.timestamp() - now)}"
        parts.append(paint(text, lim.color))
    if v.stale or v.error:
        parts.append(paint(v.error or f"not updated for {cu.fmt_duration(v.age or 0)}", "grey"))
    return " · ".join(parts)


def main() -> None:
    try:
        info = json.load(sys.stdin)
    except ValueError:
        info = {}
    now = time.time()
    state = cu.load()
    if cu.wants_fetch(state, now):
        cu.spawn_background_refresh()
    print(render(info, state, now))


if __name__ == "__main__":
    main()
