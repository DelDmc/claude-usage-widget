"""Tests for the usage widget. They use a fake local API and fake credentials,
never the real token or api.anthropic.com.  Run: python3 -m pytest tests"""

import copy
import importlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "tests" / "fixture_usage.json").read_text())
sys.path.insert(0, str(ROOT))
import claude_usage  # noqa: E402

# 2026-11-17 08:30 UTC = 10:30 in Kyiv; the fixture's session resets 13:00 UTC, weekly Wed 11:00 UTC.
FIXED_NOW = datetime(2026, 11, 17, 8, 30, tzinfo=timezone.utc).timestamp()
ANSI_RE = re.compile(r"\033\[[0-9;]*m")


class FakeAPI:
    def __init__(self):
        self.status, self.body, self.extra_headers, self.requests = 200, FIXTURE, {}, []
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                api.requests.append({k.lower(): v for k, v in self.headers.items()})
                body = json.dumps(api.body).encode()
                self.send_response(api.status)
                for key, value in api.extra_headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/api/oauth/usage"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "claude").mkdir()
        self.api = FakeAPI()
        self.addCleanup(self.api.close)
        self.env = {
            "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
            "XDG_CACHE_HOME": str(self.tmp / "cache"),
            "XDG_CONFIG_HOME": str(self.tmp / "config"),
            "CLAUDE_USAGE_URL": self.api.url,
            "no_proxy": "127.0.0.1,localhost",
            "TZ": "Europe/Kyiv",
        }
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(time.tzset)
        time.tzset()
        self.cu = importlib.reload(claude_usage)
        self.write_creds(expires_in=3600)

    def write_creds(self, expires_in):
        creds = {"claudeAiOauth": {"accessToken": "test-token",
                                   "expiresAt": int((time.time() + expires_in) * 1000)}}
        (self.tmp / "claude" / ".credentials.json").write_text(json.dumps(creds))

    def state_with(self, raw=FIXTURE, data_at=FIXED_NOW):
        return {"data": self.cu.parse(raw), "data_at": data_at, "error": None}


class ParseTests(Base):
    def test_current_response(self):
        data = self.cu.parse(FIXTURE)
        self.assertEqual([(lim["kind"], lim["percent"]) for lim in data["limits"]],
                         [("session", 17.0), ("weekly_all", 62.0)])
        self.assertEqual([b["name"] for b in data["breakdown"]], ["Claude Code", "Cowork", "Chats"])

    def test_older_response_without_limits_list(self):
        raw = {k: v for k, v in FIXTURE.items() if k != "limits"}
        data = self.cu.parse(raw)
        self.assertEqual([(lim["kind"], lim["percent"]) for lim in data["limits"]],
                         [("session", 17.0), ("weekly_all", 62.0)])

    def test_unrecognised_response(self):
        for raw in ({"something": "else"}, [], None, {"limits": [{"kind": "x"}]}):
            with self.assertRaises(ValueError):
                self.cu.parse(raw)


class PresentationTests(Base):
    def test_texts_for_fixture(self):
        v = self.cu.view(self.state_with(), FIXED_NOW)
        self.assertEqual(self.cu.compact_line(v), "5h 17% · wk 62%")
        self.assertEqual(self.cu.menu_lines(v), [
            "Session (5h): 17% · resets 15:00 (in 4h30m)",
            "Weekly: 62% · resets Wed 13:00 (in 1d2h)",
            None,
            "This week by app: Claude Code 92% · Cowork 7% · Chats 1%",
            None,
            "Updated just now",
        ])

    def test_colors_and_countdown_when_close_to_limit(self):
        raw = copy.deepcopy(FIXTURE)
        cases = [(74, "normal", "green"), (75, "normal", "amber"), (90, "normal", "red"),
                 (10, "warning", "amber"), (10, "critical", "red")]
        for percent, severity, expected in cases:
            raw["limits"][1].update(percent=percent, severity=severity)
            lim = self.cu.view(self.state_with(raw), FIXED_NOW).limits[1]
            self.assertEqual(lim.color, expected, (percent, severity))
        raw["limits"][1].update(percent=91, severity="normal")
        self.assertEqual(self.cu.compact_line(self.cu.view(self.state_with(raw), FIXED_NOW)),
                         "5h 17% · wk 91% ↻1d2h")

    def test_window_that_already_reset(self):
        after_session_reset = datetime(2026, 11, 17, 13, 5, tzinfo=timezone.utc).timestamp()
        v = self.cu.view(self.state_with(data_at=after_session_reset - 60), after_session_reset)
        self.assertEqual(self.cu.compact_line(v), "5h – · wk 62%")
        self.assertEqual(v.worst.kind, "weekly_all")

    def test_stale_and_error_states(self):
        v = self.cu.view({**self.state_with(), "error": "offline"}, FIXED_NOW + 3600)
        self.assertTrue(v.stale)
        self.assertEqual(self.cu.status_text(v), "⚠ offline (data from 1h00m ago)")
        empty = self.cu.view({}, FIXED_NOW)
        self.assertEqual(self.cu.compact_line(empty), "Claude ?")
        self.assertEqual(self.cu.menu_lines(empty), ["Waiting for the first update…"])

    def test_durations(self):
        f = self.cu.fmt_duration
        self.assertEqual([f(-5), f(30), f(59 * 60), f(61 * 60), f(26 * 3600 + 5)],
                         ["<1m", "<1m", "59m", "1h01m", "1d2h"])

    def test_icon(self):
        self.assertNotIn("stroke-dasharray", self.cu.icon_svg(0, "grey"))
        svg = self.cu.icon_svg(50, "amber")
        self.assertIn(self.cu.ICON_COLORS["amber"], svg)
        self.assertIn('stroke-dasharray="18.85 99"', svg)


class FetchTests(Base):
    def test_success_is_cached_without_the_token(self):
        state = self.cu.refresh()
        self.assertIsNone(state["error"])
        self.assertEqual(len(self.api.requests), 1)
        sent = self.api.requests[0]
        self.assertEqual(sent["authorization"], "Bearer test-token")
        self.assertEqual(sent["anthropic-beta"], "oauth-2025-04-20")
        cached = self.cu.CACHE_FILE.read_text()
        self.assertNotIn("test-token", cached)
        self.assertEqual(self.cu.load()["data"]["limits"][0]["percent"], 17.0)

    def test_polls_at_most_once_per_interval(self):
        self.cu.refresh()
        self.cu.refresh()
        self.cu.refresh()
        self.assertEqual(len(self.api.requests), 1)
        self.cu.refresh(force=True)  # within MIN_FORCE_GAP
        self.assertEqual(len(self.api.requests), 1)

    def test_waits_for_a_fetch_already_in_progress(self):
        import fcntl
        self.cu.CACHE_DIR.mkdir(parents=True)
        with open(self.cu.LOCK_FILE, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # another process (the widget) is fetching
            self.assertEqual(self.cu.refresh(), {})  # without wait: returns what's cached right away
            result = {}
            waiter = threading.Thread(target=lambda: result.update(self.cu.refresh(wait=True)))
            waiter.start()
            time.sleep(0.3)
            self.assertTrue(waiter.is_alive())
            self.cu._save({**self.state_with(data_at=time.time()), "attempt_at": time.time()})
        waiter.join(5)  # lock released: the other fetch finished
        self.assertEqual(result["data"]["limits"][0]["percent"], 17.0)
        self.assertEqual(self.api.requests, [])  # reused that result instead of fetching again

    def test_rejected_login_keeps_last_numbers(self):
        self.cu.refresh()
        self.api.status = 401
        self.cu.MIN_FORCE_GAP = 0
        state = self.cu.refresh(force=True)
        self.assertEqual(len(self.api.requests), 2)
        self.assertIn("login rejected", state["error"])
        self.assertEqual(state["data"]["limits"][1]["percent"], 62.0)

    def test_expired_token_is_not_sent(self):
        self.write_creds(expires_in=-60)
        state = self.cu.refresh()
        self.assertEqual(self.api.requests, [])
        self.assertIn("expired", state["error"])

    def test_missing_credentials(self):
        (self.tmp / "claude" / ".credentials.json").unlink()
        self.assertEqual(self.cu.refresh()["error"], "not logged in to Claude Code")

    def test_rate_limit_backs_off_even_when_forced(self):
        self.api.status, self.api.extra_headers = 429, {"Retry-After": "1200"}
        before = time.time()
        state = self.cu.refresh()
        self.assertGreaterEqual(state["hold_until"], before + 1200)
        self.cu.MIN_FORCE_GAP = 0
        self.cu.refresh(force=True)
        self.assertEqual(len(self.api.requests), 1)

    def test_offline(self):
        self.api.close()
        state = self.cu.refresh()
        self.assertEqual(state["error"], "offline")
        self.assertAlmostEqual(state["retry_at"] - state["attempt_at"], self.cu.RETRY_OFFLINE)

    def test_garbage_response_keeps_last_numbers(self):
        self.cu.refresh()
        self.api.body = {"unexpected": True}
        self.cu.MIN_FORCE_GAP = 0
        state = self.cu.refresh(force=True)
        self.assertIn("unexpected response", state["error"])
        self.assertEqual(state["data"]["limits"][0]["percent"], 17.0)


class IntervalTests(Base):
    def age_last_fetch(self, seconds):
        """Pretend the last fetch happened `seconds` ago."""
        state = self.cu.load()
        state["attempt_at"] -= seconds
        self.cu._save(state)

    def test_default_and_validation(self):
        self.assertEqual(self.cu.poll_seconds(), 180)
        for seconds in (60, 120, 180):
            self.cu.set_poll_seconds(seconds)
            self.assertEqual(self.cu.poll_seconds(), seconds)
        with self.assertRaises(ValueError):
            self.cu.set_poll_seconds(30)
        self.cu.CONFIG_FILE.write_text('{"poll_seconds": 5}')  # hand-edited to something unsupported
        self.assertEqual(self.cu.poll_seconds(), 180)
        self.cu.CONFIG_FILE.write_text("not json")
        self.assertEqual(self.cu.poll_seconds(), 180)

    def test_interval_is_honoured(self):
        self.cu.refresh()
        self.age_last_fetch(70)
        self.cu.refresh()  # default 180 s: not due yet
        self.assertEqual(len(self.api.requests), 1)
        self.cu.set_poll_seconds(60)  # switching to 1 min applies to the current wait too
        self.cu.refresh()
        self.assertEqual(len(self.api.requests), 2)
        self.age_last_fetch(110)
        self.cu.set_poll_seconds(120)
        self.cu.refresh()
        self.assertEqual(len(self.api.requests), 2)
        self.age_last_fetch(15)
        self.cu.refresh()
        self.assertEqual(len(self.api.requests), 3)

    def test_failed_fetch_retries_on_its_own_schedule(self):
        self.cu.set_poll_seconds(180)
        self.api.close()
        self.cu.refresh()  # offline: retry after RETRY_OFFLINE (60 s), not the 180 s interval
        state = self.cu.load()
        self.assertTrue(self.cu.wants_fetch(state, state["attempt_at"] + 61))
        self.assertFalse(self.cu.wants_fetch(state, state["attempt_at"] + 59))


class StatusLineTests(Base):
    def run_statusline(self, stdin='{"model": {"display_name": "Opus 5.5"}}'):
        out = subprocess.run([sys.executable, str(ROOT / "statusline.py")], input=stdin,
                             capture_output=True, text=True, env={**os.environ, **self.env}, timeout=10)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip()

    def test_shows_cached_numbers_without_fetching(self):
        raw = copy.deepcopy(FIXTURE)
        soon = datetime.now(timezone.utc) + timedelta(hours=2, minutes=30, seconds=30)
        raw["limits"][0]["resets_at"] = soon.isoformat()
        state = {**self.state_with(raw, data_at=time.time()), "attempt_at": time.time()}
        self.cu.CACHE_DIR.mkdir(parents=True)
        self.cu.CACHE_FILE.write_text(json.dumps(state))
        line = self.run_statusline()
        self.assertEqual(ANSI_RE.sub("", line).split(" · ")[:2], ["Opus 5.5", "5h 17% ↻2h30m"])
        self.assertIn("\033[32m", line)  # green
        time.sleep(0.3)
        self.assertEqual(self.api.requests, [])

    def test_first_run_fetches_in_background(self):
        line = ANSI_RE.sub("", self.run_statusline("not json"))
        self.assertEqual(line, "usage: loading…")
        deadline = time.time() + 10
        while time.time() < deadline and not self.cu.load().get("data"):
            time.sleep(0.1)
        self.assertEqual(len(self.api.requests), 1)
        self.assertEqual(self.cu.load()["data"]["limits"][1]["percent"], 62.0)


if __name__ == "__main__":
    unittest.main()
