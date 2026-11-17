# Claude usage widget

Keeps your Claude plan usage (the numbers behind `/usage`) on screen, so you don't have to open the mobile app or run `/usage` to check them.

It shows the 5-hour session limit and the weekly limit in the GNOME top bar and in the Claude Code status line, and updates them on its own every 3 minutes, or every 1 or 2 minutes if you choose that in its menu.

> **Unofficial:** not affiliated with or endorsed by Anthropic. It reads an undocumented address that Anthropic may change at any time; see [Caveat](#caveat).

## What you see

```
Top bar        ◔ 5h 17% · wk 62%

Click it       Session (5h): 17% · resets 15:00 (in 4h25m)
               Weekly: 62% · resets Wed 13:00 (in 1d2h)
               ─────
               This week by app: Claude Code 92% · Cowork 7% · Chats 1%
               ─────
               Updated 2m ago
               ─────
               Update every 3 min  ›  1 min / 2 min / 3 min (default)
               Refresh now
               Quit

Claude Code    Opus 5.5 · 5h 17% ↻4h25m · wk 62% ↻1d2h
```

The ring icon fills up with whichever limit is highest:

| Color | Meaning |
|-------|---------|
| Green | under 75% |
| Amber | 75% or more. The top bar also shows the time until that limit resets. |
| Red | 90% or more |
| Grey | the numbers haven't updated for over 10 minutes. Click the icon to see why. |

The numbers include everything you use on your plan: Claude Code, the mobile app and claude.ai.

## Install

You need:
- Ubuntu or another GNOME desktop with tray icons (AppIndicator) enabled. Ubuntu has this by default.
- The system Python 3, `git` and `jq`.
- Claude Code, logged in with a Claude subscription (Pro or Max).

**On a computer without a copy of this repo**, run:

```bash
curl -fsSL https://raw.githubusercontent.com/DelDmc/claude-usage-widget/main/get.sh | bash
```

This downloads the widget to `~/.local/share/claude-usage-widget` and runs `install.sh` from there. To update later, run the same command again.

**From a copy of this repo**, run `./install.sh`.

`install.sh` does three things, once:
1. It starts the top-bar widget and sets it to start every time you log in.
2. It adds the status line to `~/.claude/settings.json`. It saves a backup as `settings.json.bak-usage-widget` first. If you already have a status line, it leaves yours alone.
3. It installs the `/usage-widget` Claude Code skill, which you can use in any project.

After that there's nothing to run. Run `./install.sh` again after you move the folder, so the paths get updated. To remove everything:

```bash
./install.sh --uninstall
```

## After a reboot

The widget starts by itself when you log in. If it's missing (or you quit it), do either of these:
- type `/usage-widget` in Claude Code, or
- run `./start.sh`.

Both start the widget only if it isn't already running, then print your current usage.

Right after a reboot, the widget may show `login expired` until you open Claude Code. Claude Code renews the login, and the widget picks it up within a minute.

## How it works

- At the update interval you chose (3 minutes by default), the widget fetches your usage from the same Anthropic address that `/usage` uses.
- It logs in with the token Claude Code keeps in `~/.claude/.credentials.json`.
- The numbers are saved to `~/.cache/claude-usage/usage.json`. The token is never saved there.
- The top bar and the status line share that saved file, so Anthropic is asked at most once per interval.
- If a check fails, it keeps showing the last numbers and says why (offline, login expired, rate limited).

## Troubleshooting

| You see | What to do |
|---------|------------|
| `login expired` | Open Claude Code. It renews the login itself, and the widget picks it up within a minute. |
| Nothing in the top bar | Check that the "Ubuntu AppIndicators" extension is enabled, then run `./install.sh` again. |
| No status line in Claude Code | Restart Claude Code. |
| Numbers look wrong | Run `python3 claude_usage.py refresh` to check now and print the result. |

## Settings

**Update interval:** click the widget, then choose **Update every** › 1, 2 or 3 minutes. The default is 3 minutes. A new choice takes effect right away. It's saved in `~/.config/claude-usage/settings.json`, and the Claude Code status line uses it too.

To change the colors, or how soon the icon turns grey, edit these values at the top of `claude_usage.py`:

| Setting | Default | What it controls |
|---------|---------|------------------|
| `AMBER_AT` | 75 | Percent at which the icon turns amber |
| `RED_AT` | 90 | Percent at which the icon turns red |
| `STALE_AFTER` | 600 | Seconds without an update before the icon turns grey |

## Files

| File | What it does |
|------|--------------|
| `claude_usage.py` | Fetches, saves and formats the numbers. Also a small command-line tool. |
| `indicator.py` | The GNOME top-bar widget |
| `statusline.py` | The Claude Code status line |
| `get.sh` | The one-line installer: downloads or updates the repo, then runs `install.sh` |
| `install.sh` | Installs or uninstalls everything |
| `start.sh` | Starts the widget if it isn't running, then prints your usage |
| `skill/SKILL.md` | The `/usage-widget` skill. `install.sh` copies it to `~/.claude/skills/` and fills in this folder's path. |
| `tests/` | `python3 -m pytest tests`. The tests use a fake server and never your real login. |

## Caveat

The usage address is not officially documented, so Anthropic may change it without notice. If that happens, the widget keeps the last numbers it had and turns grey; it doesn't crash.

## License

MIT; see [LICENSE](LICENSE).
