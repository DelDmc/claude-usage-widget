#!/usr/bin/env python3
"""GNOME top-bar indicator for Claude plan usage.

Shows e.g. "5h 17% · wk 62%" next to a ring icon that fills up and turns
amber (75%) / red (90%) for the limit closest to running out, and grey when
the numbers are stale. Click it for reset times and the weekly breakdown.

Needs the AppIndicator GObject bindings: gir1.2-ayatanaappindicator3-0.1 or the
older gir1.2-appindicator3-0.1 (either works; Ubuntu desktops usually have one).
"""

import fcntl
import importlib
import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claude_usage as cu  # noqa: E402

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

TICK_SECONDS = 30  # re-render relative times; fetches still happen only once per update interval


def load_appindicator():
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            return importlib.import_module(f"gi.repository.{namespace}")
        except (ValueError, ImportError):
            continue
    sys.exit("The AppIndicator library is missing. Install it with:\n"
             "  sudo apt install gir1.2-ayatanaappindicator3-0.1")


class UsageIndicator:
    def __init__(self, appindicator):
        self.icon_dir = cu.CACHE_DIR / "icons"
        self.icon_dir.mkdir(parents=True, exist_ok=True)
        self.ind = appindicator.Indicator.new(
            "claude-usage", self.icon(None, "grey"), appindicator.IndicatorCategory.APPLICATION_STATUS)
        self.ind.set_icon_theme_path(str(self.icon_dir))
        self.ind.set_title("Claude usage")
        self.ind.set_status(appindicator.IndicatorStatus.ACTIVE)

        self.menu = Gtk.Menu()
        self.line_items: list[Gtk.MenuItem | None] = []
        self.ind.set_menu(self.menu)
        self.fetching = False

        self.render(cu.load())
        self.update()
        GLib.timeout_add_seconds(TICK_SECONDS, self.tick)

    def icon(self, percent, color) -> str:
        """Name of a ring icon for this state; writes the SVG the first time it's needed."""
        bucket = 0 if not percent else min(100, max(5, round(percent / 5) * 5))
        name = f"claude-usage-{color}-{bucket}"
        path = self.icon_dir / f"{name}.svg"
        if not path.exists():
            path.write_text(cu.icon_svg(bucket, color))
        return name

    def tick(self) -> bool:
        self.update()
        return True  # keep the timer

    def update(self, force: bool = False) -> None:
        """Refresh in a worker thread (it may hit the network), then render on the GTK thread."""
        if self.fetching:
            return
        self.fetching = True

        def work():
            state = None
            try:
                state = cu.refresh(force=force)
            finally:
                GLib.idle_add(self.done, state)

        threading.Thread(target=work, daemon=True).start()

    def done(self, state) -> bool:
        self.fetching = False
        self.render(state if state is not None else cu.load())
        return False  # run once

    def render(self, state: dict) -> None:
        v = cu.view(state)
        worst = v.worst
        color = "grey" if v.stale or worst is None else worst.color
        self.ind.set_icon_full(self.icon(worst.percent if worst else None, color), "Claude usage")
        label = cu.compact_line(v)
        self.ind.set_label(label, label)
        self.set_lines(cu.menu_lines(v))

    def set_lines(self, lines: list) -> None:
        shape = [line is None for line in lines]
        if shape != [item is None for item in self.line_items]:
            self.rebuild_menu(lines)
            return
        for item, text in zip(self.line_items, lines):
            if item is not None and item.get_label() != text:
                item.set_label(text)

    def rebuild_menu(self, lines: list) -> None:
        for child in self.menu.get_children():
            self.menu.remove(child)
        self.line_items = []
        for text in lines:
            item = Gtk.MenuItem(label=text) if text is not None else None
            self.menu.append(item or Gtk.SeparatorMenuItem())
            self.line_items.append(item)
        self.menu.append(Gtk.SeparatorMenuItem())
        self.menu.append(self.interval_item())
        for label, action in (("Refresh now", lambda _: self.update(force=True)),
                              ("Quit", lambda _: Gtk.main_quit())):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", action)
            self.menu.append(item)
        self.menu.show_all()

    def interval_item(self) -> Gtk.MenuItem:
        """ "Update every N min" with a submenu of radio choices (1 / 2 / 3 min)."""
        current = cu.poll_seconds()
        parent = Gtk.MenuItem(label=interval_label(current))
        submenu = Gtk.Menu()
        group = None
        for seconds in cu.POLL_CHOICES:
            text = f"{seconds // 60} min" + (" (default)" if seconds == cu.DEFAULT_POLL_SECONDS else "")
            item = Gtk.RadioMenuItem.new_with_label_from_widget(group, text)
            if group is None:
                group = item
            item.set_active(seconds == current)  # before connecting, so this doesn't save anything
            item.connect("toggled", self.on_interval, seconds, parent)
            submenu.append(item)
        parent.set_submenu(submenu)
        return parent

    def on_interval(self, item, seconds, parent) -> None:
        if not item.get_active():
            return  # this is the radio item being switched off
        cu.set_poll_seconds(seconds)
        parent.set_label(interval_label(seconds))
        self.update()  # a shorter interval may make an update due right away


def interval_label(seconds: int) -> str:
    return f"Update every {seconds // 60} min"


def hold_single_instance_lock():
    cu.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(cu.CACHE_DIR / "indicator.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("The Claude usage indicator is already running.")
    return lock  # keep the file open for the life of the process


def main() -> None:
    appindicator = load_appindicator()
    lock = hold_single_instance_lock()  # noqa: F841
    UsageIndicator(appindicator)
    for sig in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
