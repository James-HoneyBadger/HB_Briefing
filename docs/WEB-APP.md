# HB Briefing — Web App and Terminal UI

HB Briefing ships four front ends over one engine and one database. They are interchangeable:
start a scan from any of them and the others see the result on their next refresh.

| Front end | Start it with | Best for |
| --- | --- | --- |
| Desktop GUI | `python3 hbnscan_gui.py` or `./start.sh` | everyday use on a workstation |
| Web app | `python3 hbnscan.py --serve` | a browser window, a second screen, or a machine without Tk |
| Terminal UI | `python3 hbnscan.py --tui` | SSH sessions and headless servers |
| CLI | `python3 hbnscan.py` | cron, scripts, one-shot scans and exports |

All four share the same `settings.json`, so the scan schedule, filters and theme you set in one
appear in the others. None of them re-implements the pipeline: they call the engine.

---

## 1. Web app

```bash
python3 hbnscan.py --serve                       # http://127.0.0.1:8765/
python3 hbnweb.py --port 9000 --token my-secret  # or run it directly
```

Flags: `--host` (default `127.0.0.1`), `--port` (8765), `--token` (default: generated per run),
`--no-browser`, `--no-request-log`.

The server prints the URL and this run's token, and opens your browser (`--no-browser` skips
that). It is a single self-contained page — no framework, no CDN, no build step, no requests
beyond your own machine.

### 1.1 What you can do

- **Signal grid** with the same columns, filters, search, ordering and row limit as the desktop
  app. *Priority* is a real column: `watchlist`, `topic`, `core` or `context`.
- **Detail pane** — assessment, next step, key facts, entities, confidence, verification state
  and the publisher link (resolved from a Google News redirect when the engine has it cached).
- **Run / Stop** a scan, with the live log in the *Log* tab and the digest in *Briefing*.
- **Dismiss** a row (*Not relevant*): it leaves the grid, report, exports and alerts but stays in
  the database, auditable.
- **Schedule** — on/off, interval and quiet hours, in one bar above the grid (see §3). Each
  change is saved the moment you make it; there is no *Apply* step and no state that a scan can
  reset.
- **Filters that stick** — severity, category, priority, order and row limit are ordinary
  settings: the web app saves them as you change them and restores them on the next visit, so a
  reload or a scan never puts the grid back to defaults.
- **Theme** — five palettes, shared with the desktop app.
- **Export** — one click downloads the current signal set as HTML; every other format is served
  by the same endpoint (`/api/export?fmt=md|csv|json|docx|txt`).

### 1.2 Security model

The web app is a **local** tool. It binds to loopback by default, and every mutating request needs
the per-run token embedded in the page:

- `--host 0.0.0.0` is possible and prints a warning. Anyone who can reach the port *and* knows the
  token can read your signals and start scans; there are no accounts.
- Requests whose `Host` header is not the address the server bound are rejected (421), which
  defeats DNS-rebinding tricks.
- No path serving: the only bytes it returns are pages and exports it renders itself.
- Feed-derived text is written with `textContent`, never `innerHTML`, so a hostile headline cannot
  inject markup.

### 1.3 HTTP API

Useful for scripting or a dashboard of your own.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | the app |
| GET | `/api/state` | profile, counts, AI mode, schedule, scan state, log tail |
| GET | `/api/signals?severity=&category=&group=&order=&query=&limit=&offset=` | rows + total |
| GET | `/api/signal/<hash>` | one row, with facts and entities |
| GET | `/api/briefing` | latest digest |
| GET | `/api/settings` | web-editable settings |
| GET | `/api/log` | scan log lines |
| GET | `/api/export?fmt=md\|html\|csv\|json\|docx\|txt` | download |
| POST | `/api/scan` | start a scan (409 when one is running) |
| POST | `/api/scan/cancel` | request cancellation |
| POST | `/api/dismiss` | `{"hash": "...", "undo": false}` |
| POST | `/api/settings` | persist `profile`, `feed_since`, `feed_until`, `table_limit`, `row_order`, `filter_*`, `topics_as_feeds`, `theme`, `auto_scan`, `scan_interval_minutes`, `scan_window_start`, `scan_window_end` |

Mutating calls need `X-HB-Token: <token>` (or `?token=` for downloads).

`POST /api/settings` takes a **partial** body — send just the keys you are changing, for example
`{"scan_interval_minutes": 15}` — and answers with the full editable set including the recomputed
`schedule_label`. Unmentioned keys keep their stored values, so a small script cannot reset
settings it never touched.

---

## 2. Terminal UI

```bash
python3 hbnscan.py --tui
python3 hbntui.py --db /path/to/watchdog_state.db
```

A full-screen curses interface: signal table with severity colours, filters, search, a detail
pane, the briefing, the live scan log, scan control and the schedule.

| Key | Action |
| --- | --- |
| `↑` `↓` / `k` `j` | move the selection |
| `PgUp` `PgDn` | jump ten rows |
| `Enter` | show the detail pane |
| `d` | dismiss the selected signal (asks y/n) |
| `r` / `x` | run a scan / request cancellation |
| `a` | toggle scheduled scanning on or off |
| `i` | cycle the scan interval (5, 10, 15, 30, 60, 120, 240 minutes) |
| `f` `c` `p` `o` | cycle severity, category, priority and ordering |
| `/` | search the whole database (`Esc` cancels) |
| `b` / `l` | switch the bottom pane to briefing / scan log |
| `e` | export the current filter to `hb-briefing-<stamp>.md` |
| `R` | reload settings and rows |
| `q` | quit |

`a` and `i` write `settings.json`, so the change applies to every front end; the header shows the
active schedule (`○ every 30 min` when idle, `● …` while scanning).

---

## 3. Scheduling from any front end

One rule decides when to scan, and every interface exposes it:

| Where | On/off | Interval | Quiet hours |
| --- | --- | --- | --- |
| Desktop GUI | **Auto-Scan** checkbox in the toolbar | the `every` menu beside it (or Settings → Scanning) | Settings → Scanning |
| Web app | **Scan on a schedule** checkbox | the `every` menu in the same bar | the `quiet` field, `HH:MM-HH:MM` |
| Terminal UI | `a` | `i` | `settings.json`, or the CLI below |
| CLI | `--auto` / `--no-auto` | `--interval N` | `--quiet HH:MM-HH:MM` (or `off`) |

Every one of those controls writes `settings.json` as you use it — the GUI and the web app save on
the spot, with no *Apply* button, and the bar echoes the new schedule (*"every 15 min, quiet
22:00-06:00"*) once the write lands. Nothing else writes settings: a scan reads them and leaves
them alone, so an interval, a quiet window, a filter or a theme can only change because someone
changed it.

```bash
python3 hbnscan.py --interval 15           # edit settings and exit; add --daemon to act on it
python3 hbnscan.py --quiet 22:00-06:00
python3 hbnscan.py --no-auto --daemon      # run the loop but never scan on a timer
```

The scheduling flags are a settings edit on their own and exit 0, so pass them in their own
invocation rather than alongside `--serve` or `--tui` — those start their own argument parsers,
where `--quiet` means "do not log each request". Setting quiet hours for a web app therefore
looks like this:

```bash
python3 hbnscan.py --quiet 22:00-06:00     # writes settings.json, exits
python3 hbnscan.py --serve                 # the running app picks it up on its next tick
```

The rule itself lives in the engine (`next_scan_delay`):

- `auto_scan` off → nothing is scheduled; manual runs still work everywhere.
- Outside the quiet window → wait until it opens, re-checking at least once per interval.
- At startup with auto on → scan immediately.
- Otherwise → the remainder of the interval since the last scan this front end started.

A scan takes a file lock, so two front ends cannot scan at once: the second logs *"Another scan
is already running"* and returns.

---

## 4. Themes

Five palettes ship, with identical names in the desktop and web apps, stored as the `theme`
setting so a choice follows you between them:

| Name | Look |
| --- | --- |
| `dark` | slate blue-black with amber accents (default) |
| `light` | light grey with dark text |
| `midnight` | near-black with blue accents |
| `solarized` | Solarized Light — warm paper, teal text |
| `contrast` | pure black, pure white text, yellow accents |

Desktop: **Settings → Appearance → Theme** (the window rebuilds to apply it). Web: the `theme`
menu in the schedule bar. Terminal: colours follow the terminal's own palette.

---

## 5. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `403 missing or invalid token` | The page came from an older server run, or you are calling the API by hand: reload, or use the token printed at startup |
| `421 unexpected Host header` | Reached the server by a name it did not bind (proxy, hosts entry); use the printed URL |
| `started: false` (409) | A scan is already running — wait, or press Stop |
| Page loads but the grid is empty | Filters exclude everything, or the database is empty: clear search, set Priority and Category to *all* |
| Browser opens the wrong port | `--port` was taken; read the URL printed in the terminal |
| TUI looks garbled | Terminal too small or `TERM` unset; resize, or run with `TERM=xterm-256color` |
| TUI exits immediately | `curses` is missing (`python3 -c "import curses"`), or another process holds the scan lock |
