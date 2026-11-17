#!/usr/bin/env python3
"""Claude plan usage: fetch, cache and describe the numbers behind /usage.

Standard library only. Shared by indicator.py (GNOME top bar) and
statusline.py (Claude Code status line). Every consumer goes through
refresh(), so the API is called at most once per update interval (1, 2 or
3 minutes, picked in the widget's menu) no matter how many processes ask.

Usage:
  python3 claude_usage.py            show current usage (fetches if due)
  python3 claude_usage.py refresh    fetch now, then show
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# The endpoint Claude Code's /usage calls. It is undocumented, so parse() is defensive.
USAGE_URL = os.environ.get("CLAUDE_USAGE_URL", "https://api.anthropic.com/api/oauth/usage")
CREDENTIALS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / ".credentials.json"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "claude-usage"
CACHE_FILE = CACHE_DIR / "usage.json"
LOCK_FILE = CACHE_DIR / "fetch.lock"
CONFIG_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "claude-usage" / "settings.json"

POLL_CHOICES = (60, 120, 180)  # update intervals offered in the widget's menu
DEFAULT_POLL_SECONDS = 180
FETCH_TIMEOUT = 15
RETRY_OFFLINE = 60      # network down or token expired: cheap to retry soon
RETRY_AUTH = 120
RETRY_SERVER = 600      # 5xx and 429
MIN_FORCE_GAP = 10      # "Refresh now" can't fire more often than this
STALE_AFTER = 10 * 60   # data older than this is flagged as stale
AMBER_AT, RED_AT = 75, 90

SHORT_NAMES = {"session": "5h", "weekly_all": "wk", "weekly_opus": "wk Opus", "weekly_sonnet": "wk Sonnet"}
TITLES = {"session": "Session (5h)", "weekly_all": "Weekly",
          "weekly_opus": "Weekly (Opus)", "weekly_sonnet": "Weekly (Sonnet)"}
# Older responses had no "limits" list, only these windows.
LEGACY_WINDOWS = (("five_hour", "session"), ("seven_day", "weekly_all"),
                  ("seven_day_opus", "weekly_opus"), ("seven_day_sonnet", "weekly_sonnet"))
ICON_COLORS = {"green": "#4ade80", "amber": "#fbbf24", "red": "#f87171", "grey": "#9ca3af"}


# --------------------------------------------------------------------------- settings

def _read_settings() -> dict:
    try:
        settings = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def poll_seconds() -> int:
    """The update interval chosen in the widget's menu (re-read on every call, so changes apply at once)."""
    value = _read_settings().get("poll_seconds")
    return int(value) if value in POLL_CHOICES else DEFAULT_POLL_SECONDS


def set_poll_seconds(seconds: int) -> None:
    if seconds not in POLL_CHOICES:
        raise ValueError(f"update interval must be one of {POLL_CHOICES}")
    settings = _read_settings()
    settings["poll_seconds"] = seconds
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_name(f".settings.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(settings, indent=1) + "\n")
    os.replace(tmp, CONFIG_FILE)


# --------------------------------------------------------------------------- fetching

class FetchError(Exception):
    def __init__(self, message: str, retry_after: float, hold: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.hold = hold  # True: don't retry early even on "Refresh now" (rate limited)


def read_token() -> str:
    """The OAuth access token Claude Code keeps (and renews) in its credentials file."""
    try:
        oauth = json.loads(CREDENTIALS.read_text()).get("claudeAiOauth") or {}
    except FileNotFoundError:
        raise FetchError("not logged in to Claude Code", RETRY_AUTH) from None
    except (OSError, ValueError, AttributeError) as e:
        raise FetchError(f"can't read Claude Code credentials ({type(e).__name__})", RETRY_AUTH) from None
    token = oauth.get("accessToken")
    if not token:
        raise FetchError("not logged in to Claude Code", RETRY_AUTH)
    expires_ms = oauth.get("expiresAt")
    if isinstance(expires_ms, (int, float)) and expires_ms / 1000 < time.time() + 30:
        # Claude Code renews it on its next start; we only re-read the file.
        raise FetchError("login expired, open Claude Code to renew it", RETRY_OFFLINE)
    return token


def fetch_raw(token: str) -> dict:
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "claude-usage-widget/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("login rejected, open Claude Code to renew it", RETRY_AUTH) from None
        if e.code == 429:
            raise FetchError("usage API is rate limiting, backing off",
                             max(_retry_after(e), RETRY_SERVER), hold=True) from None
        raise FetchError(f"usage API error (HTTP {e.code})",
                         RETRY_SERVER if e.code >= 500 else poll_seconds()) from None
    except OSError:  # URLError, timeouts, DNS, connection refused
        raise FetchError("offline", RETRY_OFFLINE) from None
    except ValueError:
        raise FetchError("unreadable response from the usage API", poll_seconds()) from None


def _retry_after(err: urllib.error.HTTPError) -> float:
    try:
        return float(err.headers.get("Retry-After", 0))
    except (TypeError, ValueError):
        return 0.0


def parse(raw) -> dict:
    """Reduce the API response to {"limits": [...], "breakdown": [...]}. Raises ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("response is not an object")
    limits = []
    for item in raw.get("limits") or []:
        if isinstance(item, dict) and isinstance(item.get("percent"), (int, float)):
            limits.append({
                "kind": str(item.get("kind") or item.get("group") or "limit"),
                "scope": item.get("scope") if isinstance(item.get("scope"), str) else None,
                "percent": float(item["percent"]),
                "severity": str(item.get("severity") or "normal"),
                "resets_at": item.get("resets_at"),
            })
    if not limits:
        for key, kind in LEGACY_WINDOWS:
            window = raw.get(key)
            if isinstance(window, dict) and isinstance(window.get("utilization"), (int, float)):
                limits.append({"kind": kind, "scope": None, "percent": float(window["utilization"]),
                               "severity": "normal", "resets_at": window.get("resets_at")})
    if not limits:
        raise ValueError("no usage limits in response")

    breakdown = []
    weekly = raw.get("seven_day_breakdown")
    for row in (weekly.get("rows") if isinstance(weekly, dict) else None) or []:
        if isinstance(row, dict) and isinstance(row.get("percent"), (int, float)) and row["percent"] > 0:
            breakdown.append({"name": str(row.get("display_name") or row.get("key")),
                              "percent": float(row["percent"])})
    breakdown.sort(key=lambda r: -r["percent"])
    return {"limits": limits, "breakdown": breakdown}


# --------------------------------------------------------------------------- cache

def load() -> dict:
    try:
        state = json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save(state: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_DIR / f".usage.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, CACHE_FILE)  # atomic: readers never see half a file


def _num(state: dict, key: str) -> float:
    value = state.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def wants_fetch(state: dict, now: float, force: bool = False) -> bool:
    if now < _num(state, "hold_until"):  # rate limited: wait even when forced
        return False
    last = _num(state, "attempt_at")
    if force:
        return now - last >= MIN_FORCE_GAP
    retry_at = state.get("retry_at")  # only set after a failed fetch
    due = retry_at if isinstance(retry_at, (int, float)) else last + poll_seconds()
    return now >= due


def refresh(force: bool = False, wait: bool = False) -> dict:
    """Fetch new numbers if due (or forced) and return the cached state.

    The last good data is kept when a fetch fails; the failure goes in "error".
    wait=True: if another process is fetching right now, wait for its result
    instead of returning the older numbers.
    """
    now = time.time()
    state = load()
    if not wants_fetch(state, now, force):
        return state
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            return state  # another process is fetching right now
        state = load()  # it may have just finished
        if not wants_fetch(state, now, force):
            return state
        state["attempt_at"] = now  # saved first, so other processes don't start a fetch meanwhile
        state.pop("retry_at", None)
        _save(state)
        try:
            state["data"] = parse(fetch_raw(read_token()))
        except FetchError as e:
            state["error"] = str(e)
            state["retry_at"] = now + e.retry_after
            if e.hold:
                state["hold_until"] = now + e.retry_after
        except Exception as e:  # unexpected response shape or a bug: keep old data, retry later
            state["error"] = f"unexpected response from the usage API ({type(e).__name__})"
            state["retry_at"] = now + poll_seconds()
        else:
            state.update(data_at=now, error=None)
        _save(state)
        return state


def spawn_background_refresh() -> None:
    """Fetch in a detached process, for callers that must never wait on the network."""
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "refresh-if-due"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# --------------------------------------------------------------------------- presentation

@dataclass
class LimitView:
    kind: str
    short: str                 # "5h", "wk"
    title: str                 # "Session (5h)", "Weekly"
    percent: float | None      # None once its window has reset and no new data arrived yet
    color: str                 # green | amber | red | grey
    resets_at: datetime | None


@dataclass
class View:
    now: float
    limits: list[LimitView]
    breakdown: list[dict]
    error: str | None
    age: float | None          # seconds since the last successful fetch
    stale: bool

    @property
    def worst(self) -> LimitView | None:
        known = [lim for lim in self.limits if lim.percent is not None]
        return max(known, key=lambda lim: lim.percent) if known else None


def _parse_time(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _color(percent: float, severity: str) -> str:
    sev = severity.lower()
    if percent >= RED_AT or any(word in sev for word in ("crit", "exceed", "block", "reached", "danger")):
        return "red"
    if percent >= AMBER_AT or sev not in ("normal", "ok", "low", ""):
        return "amber"
    return "green"


def view(state: dict, now: float | None = None) -> View:
    now = time.time() if now is None else now
    data = state.get("data") if isinstance(state.get("data"), dict) else {}
    data_at = _num(state, "data_at") or None
    age = now - data_at if data_at else None
    limits = []
    for item in data.get("limits") or []:
        kind = item.get("kind", "limit")
        short = SHORT_NAMES.get(kind, kind.replace("weekly_", "wk ").replace("_", " "))
        title = TITLES.get(kind, kind.replace("_", " ").capitalize())
        if item.get("scope"):
            short, title = f"{short} {item['scope']}", f"{title} ({item['scope']})"
        resets = _parse_time(item.get("resets_at"))
        percent = item.get("percent")
        if resets is not None and resets.timestamp() <= now:
            percent = None  # that window is over; the new one hasn't been fetched yet
        color = "grey" if percent is None else _color(percent, str(item.get("severity") or "normal"))
        limits.append(LimitView(kind, short, title, percent, color, resets))
    error = state.get("error") if isinstance(state.get("error"), str) else None
    return View(now, limits, list(data.get("breakdown") or []), error, age,
                stale=age is None or age > STALE_AFTER)


def fmt_duration(seconds: float) -> str:
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h"


def fmt_clock(when: datetime, now: float) -> str:
    local = when.astimezone()
    days_ahead = (local.date() - datetime.fromtimestamp(now).date()).days
    if days_ahead == 0:
        return local.strftime("%H:%M")
    if 0 < days_ahead < 7:
        return local.strftime("%a %H:%M")
    return local.strftime("%d %b %H:%M")


def compact_line(v: View) -> str:
    """Top-bar label, e.g. "5h 17% · wk 62%". Adds a reset countdown once a limit is amber or red."""
    if not v.limits:
        return "Claude ?"
    parts = []
    for lim in v.limits:
        if lim.percent is None:
            parts.append(f"{lim.short} –")
            continue
        text = f"{lim.short} {lim.percent:.0f}%"
        if lim.color in ("amber", "red") and lim.resets_at:
            text += f" ↻{fmt_duration(lim.resets_at.timestamp() - v.now)}"
        parts.append(text)
    return " · ".join(parts)


def status_text(v: View) -> str:
    if v.error and v.age is not None:
        return f"⚠ {v.error} (data from {fmt_duration(v.age)} ago)"
    if v.error:
        return f"⚠ {v.error}"
    if v.age is None:
        return "Waiting for the first update…"
    if v.stale:
        return f"Not updated for {fmt_duration(v.age)}"
    return "Updated just now" if v.age < 60 else f"Updated {fmt_duration(v.age)} ago"


def menu_lines(v: View) -> list[str | None]:
    """Lines for the indicator's dropdown; None marks a separator."""
    lines: list[str | None] = []
    for lim in v.limits:
        if lim.percent is None:
            lines.append(f"{lim.title}: reset, waiting for new numbers")
            continue
        text = f"{lim.title}: {lim.percent:.0f}%"
        if lim.resets_at:
            text += (f" · resets {fmt_clock(lim.resets_at, v.now)}"
                     f" (in {fmt_duration(lim.resets_at.timestamp() - v.now)})")
        lines.append(text)
    if v.breakdown:
        lines += [None, "This week by app: " + " · ".join(f"{b['name']} {b['percent']:.0f}%" for b in v.breakdown)]
    if lines:
        lines.append(None)
    lines.append(status_text(v))
    return lines


def icon_svg(percent: float | None, color: str) -> str:
    """16px ring that fills clockwise with the percentage, for the top bar."""
    ring = '<circle cx="8" cy="8" r="6" fill="none" stroke="#ffffff" stroke-opacity="0.35" stroke-width="2.6"/>'
    arc = ""
    if percent:
        length = 2 * math.pi * 6 * min(percent, 100) / 100
        arc = (f'<circle cx="8" cy="8" r="6" fill="none" stroke="{ICON_COLORS[color]}" stroke-width="2.6" '
               f'stroke-dasharray="{length:.2f} 99" transform="rotate(-90 8 8)"/>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">{ring}{arc}</svg>\n'


# --------------------------------------------------------------------------- CLI

def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"
    if cmd == "refresh-if-due":  # used by the status line; prints nothing
        refresh()
        return 0
    if cmd not in ("show", "refresh"):
        print(__doc__.strip())
        return 2
    v = view(refresh(force=cmd == "refresh", wait=True))
    print(compact_line(v))
    for line in menu_lines(v):
        print(f"  {line}" if line else "")
    return 0 if v.limits and not v.error else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
