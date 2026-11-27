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
appear in the others. None of them re-implements the pipeline: they call the engine. Dossiers,
actions, source health and the full configuration workspace (sources, topics, targets, criteria,
budgets, transport) are edited in the desktop app or through the workspace commands
(`hbnscan.py dossier|action|sources`); the web app and TUI read signals, including each row's
story group.

---

## 1. Web app

```bash
python3 hbnscan.py --serve                       # http://127.0.0.1:8765/
python3 src/hbnweb.py --port 9000 --token my-secret  # or run it directly
```

Flags: `--host` (default `127.0.0.1`), `--port` (8765), `--token` (default: generated per run),
`--no-browser`, `--no-request-log`.

The server prints the URL and this run's token, and opens your browser (`--no-browser` skips
that). It is a single self-contained page — no framework, no CDN, no build step, no requests
beyond your own machine.

### 1.1 What you can do

- **Signal grid** with the same columns, filters, search, ordering and row limit as the desktop
  app, including the **Fresh** column (publication age, or `undated`) and the text tags for
  declared interest. *Priority* is a real column: `watchlist`, `topic`, `core` or `context`.
- **Detail pane** — assessment, next step, key facts, entities, confidence, verification state
  and the publisher link (resolved from a Google News redirect when the engine has it cached).
  It also shows **why the priority was assigned**: the separate axes (relevance, urgency,
  impact, evidence, novelty, coverage, confidence) with a reason for each, and whether the
  priority came from declared intent or from inference.
- **Run / Stop** a scan, with the live log in the *Log* tab and the digest in *Briefing*.
- **Dismiss** a row (*Not relevant*): it leaves the grid, report, exports and alerts but stays in
  the database, auditable.
- **Schedule** — on/off, interval and the scanning window, in one bar above the grid (see §3). Each
  change is saved the moment you make it; there is no *Apply* step and no state that a scan can
  reset.
- **Filters that stick** — severity, category, priority, publication recency, order and row
  limit are ordinary settings: the web app saves them as you change them and restores them on the next visit, so a
  reload or a scan never puts the grid back to defaults.
- **Theme** — six palettes, shared with the desktop app and generated from one table.
- **Export** — one click downloads the current signal set as HTML; every other format is served
  by the same endpoint (`/api/export?fmt=md|csv|json|docx|txt`).

### 1.2 Security model

The web app is a **single-user** tool with no accounts, and it distinguishes three contexts
instead of assuming one:

| Context | Reads | Writes | How it is identified |
| --- | --- | --- | --- |
| Loopback (`127.0.0.1`, `::1`, `localhost`) | no token | token | the bind address |
| LAN (`--host 192.168.x.x`, `--host 0.0.0.0`) | token or session | token or session | `require_auth` on the bound handler |
| Remote | LAN policy **plus** TLS or a trusted reverse proxy | same | not something the server can provide itself |

- **Loopback is the trusted case**, which is why it is the default. Mutating requests need the
  per-run token (`secrets.token_urlsafe(18)`, `X-HB-Token`, compared with `compare_digest`), and
  the page carries its own copy so a foreign origin cannot read it.
- **A non-loopback bind requires the token for reads too.** Open
  `http://host:8765/?token=<token>` once: the token is exchanged for a signed, expiring,
  `HttpOnly` + `SameSite=Strict` cookie (`hb_session`, 12 h) and the browser is redirected to a
  clean `/`. The cookie contains an HMAC over an expiry, never the token itself.
- **The token is not embedded in the page in that mode**, because the page is reachable by
  anyone on the network. Without a valid cookie it serves a small *token required* form and
  nothing else.
- **Query-string tokens are limited to one place** — a download the browser navigates to
  (`/api/export`). Every other route reads only the header or the cookie, so a token cannot leak
  into history, a log or a `Referer`. Request logs redact any `token=` value.
- **Rate limits** apply per client address: 300 reads and 60 writes per minute, answered with
  `429` rather than being queued.
- Requests whose `Host` header is not the address the server bound are rejected (421), which
  defeats DNS-rebinding tricks.
- **Headers on every response**: `Content-Security-Policy` (self only, no external origins),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
  `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`,
  `Permissions-Policy` with the sensors denied, and `Cache-Control: no-store`.
- **The service worker honours `no-store`**: it caches the app shell and offline reads, but never
  a response the server marked as non-cacheable.
- No path serving: the only bytes it returns are pages and exports it renders itself.
- Feed-derived text is written with `textContent`, never `innerHTML`, so a hostile headline cannot
  inject markup.

Plain HTTP is not transport security. If the port is reachable beyond a network you control,
put TLS (or an authenticating reverse proxy) in front of it; the token is a capability check,
not a substitute for encryption.

### 1.3 HTTP API

Useful for scripting or a dashboard of your own.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | the app |
| GET | `/api/state` | profile, counts, AI mode, schedule, scan state, log tail |
| GET | `/api/signals?severity=&category=&group=&recent=&order=&query=&limit=&offset=` | rows + total (`recent` is `all`, `1d`, `3d`, `7d` or `30d`, on publication time) |
| GET | `/api/signal/<hash>` | one row, with facts, entities and the `story` summary (count and publishers) |
| GET | `/api/briefing` | latest digest |
| GET | `/api/settings` | web-editable settings |
| GET | `/api/log` | scan log lines |
| GET | `/api/export?fmt=md\|html\|csv\|json\|docx\|txt` | download |
| POST | `/api/scan` | start a scan (409 when one is running) |
| POST | `/api/scan/cancel` | request cancellation |
| POST | `/api/dismiss` | `{"hash": "...", "undo": false}` |
| POST | `/api/settings` | persist `profile`, `feed_since`, `feed_until`, `table_limit`, `row_order`, `filter_*`, `topics_as_feeds`, `theme`, `auto_scan`, `scan_interval_minutes`, `scan_window_start`, `scan_window_end` |

Mutating calls need `X-HB-Token: <token>` (or a valid `hb_session` cookie); the page's own
download link may pass it as `?token=`, the only route that accepts a query token,
which is accepted everywhere for convenience. Reads need nothing — see §1.2.

`POST /api/settings` takes a **partial** body — send just the keys you are changing, for example
`{"scan_interval_minutes": 15}` — and answers with the full editable set including the recomputed
`schedule_label`. Unmentioned keys keep their stored values, so a small script cannot reset
settings it never touched.

---

## 2. Terminal UI

```bash
python3 hbnscan.py --tui
python3 src/hbntui.py --db /path/to/watchdog_state.db
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
| `[` `]` | previous / next page (the header shows `1-200 of N`) |
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
| Web app | **Scan on a schedule** checkbox | the `every` menu in the same bar | the *scan only between* field, `HH:MM-HH:MM` |
| Terminal UI | `a` | `i` | `settings.json`, or the CLI below |
| CLI | `--auto` / `--no-auto` | `--interval N` | `--window HH:MM-HH:MM` (or `off`) |

Every one of those controls writes `settings.json` as you use it — the GUI and the web app save on
the spot, with no *Apply* button, and the bar echoes the new schedule (*"every 15 min,
22:00-06:00 only"*) once the write lands. Nothing else writes settings: a scan reads them and
leaves them alone, so an interval, a scanning window, a filter or a theme can only change because
someone changed it.

```bash
python3 hbnscan.py --interval 15           # edit settings and exit; add --daemon to act on it
python3 hbnscan.py --window 22:00-06:00
python3 hbnscan.py --no-auto --daemon      # run the loop but never scan on a timer
```

The scheduling flags are a settings edit on their own and exit 0, so pass them in their own
invocation rather than alongside `--serve` or `--tui` — those start their own argument parsers,
where `--no-request-log` is the flag that silences per-request output. Setting a window for a web
app therefore looks like this:

```bash
python3 hbnscan.py --window 22:00-06:00    # writes settings.json, exits
python3 hbnscan.py --serve                 # the running app picks it up on its next tick
```

The rule itself lives in the engine (`next_scan_delay`), and every front end reads it — the GUI,
the web app, the terminal UI and `--daemon`:

- `auto_scan` off → nothing is scheduled; manual runs still work everywhere.
- Outside the scanning window → wait until it opens, re-checking at least once per interval.
- At startup with auto on → scan immediately.
- Otherwise → the remainder of the interval since the last scan this front end started.

A scan takes a file lock, so two front ends cannot scan at once: the second logs *"Another scan
is already running"* and returns.

---

## 4. Themes

Six palettes ship, with identical names in the desktop and web apps, stored as the `theme`
setting so a choice follows you between them. The table lives once, in `src/hbtheme.py`, and
the page's CSS custom properties are generated from it, so the two front ends cannot drift.

| Name | Look |
| --- | --- |
| `classic` | Honey Badger Classic: muted light, slate header, brass accent (default) |
| `dark` | slate blue-black with amber accents |
| `light` | light grey with dark text |
| `midnight` | near-black with blue accents |
| `solarized` | Solarized Light — warm paper, teal text |
| `contrast` | pure black, pure white text, yellow accents |

`classic` is the default for a new installation. A saved `theme` is never rewritten, so an
upgraded install keeps the palette it had.

Desktop: **Settings → Appearance → Theme** (the window rebuilds to apply it). Web: the `theme`
menu in the schedule bar. Terminal: colours follow the terminal's own palette.

---

## 5. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `403 missing or invalid token` | The page came from an older server run, or you are calling the API by hand: reload, or use the token printed at startup |
| `401 this server needs the token` | The server is bound beyond loopback: open `http://host:port/?token=<token>` once to sign in |
| `429 too many requests` | A per-client rate limit tripped; wait a minute |
| `421 unexpected Host header` | Reached the server by a name it did not bind (proxy, hosts entry); use the printed URL |
| `started: false` (409) | A scan is already running — wait, or press Stop |
| Page loads but the grid is empty | Filters exclude everything, or the database is empty: clear search, set Priority and Category to *all* |
| Browser opens the wrong port | `--port` was taken; read the URL printed in the terminal |
| TUI looks garbled | Terminal too small or `TERM` unset; resize, or run with `TERM=xterm-256color` |
| TUI exits immediately | `curses` is missing (`python3 -c "import curses"`), or another process holds the scan lock |


The `src/` prefix applies to Git checkouts; in a flat release archive, omit it. `python3 hbnscan.py --serve` and `--tui` continue to work in both layouts.
