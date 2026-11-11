# HB Briefing v1.0.0

An autonomous, open-source RSS/Atom intelligence monitor with two-stage DeepSeek AI triage and a standalone desktop GUI. It started as a single Python script; 1.0.0 is its first packaged release.

**HB Briefing tracks regulatory action and litigation.** That is the core of the product: who is being investigated, sued, fined, settled or ruled against, and what happens next. Billing disputes, fee complaints and security notes are context around that core — they are still screened, but they never outrank a proceeding. Items are classified as `regulatory_action` or `litigation` before any other category is considered, the report orders those two ahead of everything else at the same severity, and all three AI stages are prompted to prefer them. The only thing that outranks the core is an explicit watchlist hit — a matter the operator declared important by name.

## What's in 1.0.0

**Triage pipeline**
- **Structured verdicts instead of a boolean.** Each item is scored for `category`, `severity`, `entities` and a concrete `action`, not just flagged/unflagged.
- **Deep read (stage 2).** Flagged items get their article fetched, converted to readable text, and re-judged against the actual content — producing quoted `facts` and a `confidence` score. Budgeted at 8 reads per scan. A confident rejection is stored (`dismissed`) rather than dropped.
- **Executive briefing (stage 3).** One call per scan ranks the flagged set, merges syndicated duplicates across feeds, and writes a digest shown in the **AI Briefing** tab. Skipped when nothing new has arrived since the last briefing.
- **Cross-feed dedupe.** The same story carried by two publishers is triaged once and marked `[Deduped]`.
- **Offline heuristic fallback** with graded severity, so the tool stays useful with no API key.

**Reliability**
- **Hardened feed fetching:** conditional GET (ETag / Last-Modified), a browser-compatible `Accept` header, DTD/entity rejection, and an 8 MiB payload cap.
- **Honest AI fallback:** when the DeepSeek request fails, items are scored by the offline heuristic instead of being silently marked "not flagged".
- **No silent item loss:** new items beyond the per-scan cap roll over to the next scan instead of being skipped.
- **Safer output:** links are restricted to `http(s)`, report text is escaped, and the report is ordered by severity before truncation.
- **Direct sources where they exist:** FTC, CFPB newsroom, CFPB enforcement actions, SEC
  press releases, Federal Register rules for the FCC and FTC, Broadband Breakfast and
  Telecompetitor are fetched from their own feeds. Google News queries cover FCC news,
  state attorneys general and MetroNet/KKR, where no usable feed exists.

**Desktop**
- **Standalone GUI (`hbnscan_gui.py`):** non-blocking threaded scans, live telemetry log, interactive feed editor, and double-click URL launcher.
- **Linux autostart installer (`install_autostart.sh`):** resolves `$PWD` to register both an XDG application menu shortcut and a login autostart `.desktop` entry (tested on XFCE, Arch, and Fedora).

## Quick Start

One command checks the machine over, installs whatever is missing, and opens the GUI:

```bash
./start.sh
```

It verifies `python3` (3.8+), installs `python3-tk` through the system package manager if
`tkinter` is absent, creates a project-local `.venv` so the system interpreter is left
alone, installs `requirements.txt` into it, and creates `.env` from `.env.example` on
first run. Nothing is reinstalled once present.

Manual equivalent:
1. Copy `.env.example` to `.env` and insert your DeepSeek API key:
   ```bash
   cp .env.example .env
   ```
   Without a key HB Briefing still runs, using the offline keyword heuristic.
2. Install dependencies (Tkinter ships with most Python builds):
   ```bash
   pip install -r requirements.txt
   ```
   If `import tkinter` fails: Debian/Ubuntu `sudo apt install python3-tk`, Arch `sudo pacman -S tk`, Fedora `sudo dnf install python3-tkinter`.
3. Launch the standalone GUI:
   ```bash
   python3 hbnscan_gui.py
   ```
   Headless alternative:
   ```bash
   python3 hbnscan.py
   ```
4. *(Optional)* Enable Linux desktop menu & login autostart:
   ```bash
   ./install_autostart.sh
   ```

## The AI pipeline

| Stage | Runs on | Cost | Output |
|---|---|---|---|
| 1. Screen | every new item | 1 call, ~400 tokens | `flagged`, `category`, `severity`, `entities`, `action` |
| 2. Verify | flagged items only, max 8/scan | 1 call + 1 page fetch | `facts`, `confidence`, refined `severity` |
| 3. Brief | once per scan | 1 call | `headline`, `summary`, `top_stories`, `watchlist` |

Stage 2 is what makes the verdicts trustworthy: the model reads the article, not just the headline. It can confirm, promote or reject a screened signal; a rejection is stored with `dismissed = 1` (never deleted) and is hidden from the report and briefing. Stage 3 is what makes the output usable: one ranked digest instead of a wall of rows.

### Offline heuristic

Without a key, screening uses local keyword matching instead of the model:

- **Page type is checked first.** Index, form, station-information and login pages are dropped even when their titles name a regulator.
- **Regulatory action and litigation come first.** A headline naming a regulator (`ftc`, `fcc`, `cfpb`, `doj`, `attorney general`, `public utilities commission`) or a forum (`lawsuit`, `court`, `settlement`, `consent order`, `class action`, `injunction`) is classified before any consumer-hardship category is considered. It grades `high` when the headline reports an outcome (`sued`, `fined`, `settlement`, `ruling`) and `medium` when it is only an inquiry, proposal or announcement.
- **The headline decides the rest.** A strong phrase ("class action", "data breach", "fined") flags the item on its own; two *weak* words ("billing", "telecom", "fee") also qualify.
- **Summaries only corroborate.** A strong phrase in the description counts only when the headline already carries a signal, and it caps severity at `medium`. This is what removes the "learn how to avoid fraud and scams" false positives.
- **Signals match at word starts, not mid-word,** so `sues` no longer fires on `Issues` and `fined` no longer fires on `defined`.
- Outside the core, severity is graded rather than flat: two or more strong headline signals → `high`; one strong signal, or corroborated summary evidence → `medium`; weak words alone → `low`. Releases from `ftc.gov`, `consumerfinance.gov` and `fcc.gov` are in scope by definition and land at `medium` as `regulatory_action`.

### Sources

`feeds.txt` mixes two kinds of source, and it matters for stage 2:

| Kind | Example | Deep read |
|---|---|---|
| `direct` — the publisher's own feed | `ftc.gov`, `consumerfinance.gov/enforcement/actions`, `sec.gov`, `federalregister.gov`, `broadbandbreakfast.com` | works |
| `query` — a Google News search | `site:fcc.gov`, state AG, MetroNet/KKR | impossible: links are encrypted redirects |

Roughly half the configured sources are direct, so stage 2 covers the material it matters
most on. fcc.gov and justice.gov publish HTML or dead endpoints rather than feeds (both
were probed), which is why FCC coverage still runs through search. The Federal Register
feeds are filtered to `RULE` and `PRORULE` documents — the unfiltered agency feed is almost
entirely procedural notices ("information collections") that would drown the digest.

## Watchlist

`feeds.txt` decides what gets fetched; `watchlist.txt` decides what a match *means*. Each
rule pins a subject to a category and a status, ahead of keyword scoring and regardless
of topic:

```
metronet & better business bureau | billing_dispute   | high
metronet                          | regulatory_action | high
kkr                               | regulatory_action | medium
iowa attorney general             | regulatory_action | high
```

- Every `&`-joined phrase must appear at a word start in the item's title or summary.
- Rules are evaluated top to bottom and the first match wins, so narrow rules go above
  broad ones (`metronet & bbb` above `metronet`).
- `category` must be one of the categories the pipeline knows; `status` is `high`,
  `medium` or `low`. Malformed lines are skipped and reported in the scan log.
- The same list is appended to the model prompt as a standing watchlist, so AI mode
  honours it too, and watchlist items are never downgraded by a deep read's doubt.

The default file tracks, in this order: the operator's own identifiers
(`james@honey-badger.org`, `James Temple`, and `Honey Badger` in spaced, hyphenated and
unspaced spellings), MetroNet (always `high`, including BBB complaints), its investor KKR,
the Iowa Attorney General, and the FCC.

A watchlist hit outranks everything else in the report: ordering is severity first, then
watchlist hit, then core category, then recency.

### Tuning

Set these in `.env` to control spend and depth:

```dotenv
WATCHDOG_DEEP_READ=1          # 0 disables article fetching (stage 2)
WATCHDOG_MAX_DEEP_READS=8     # article fetches per scan
WATCHDOG_MAX_TRIAGE=40        # screening calls per scan; the remainder score offline
WATCHDOG_BRIEFING=1           # 0 disables the executive briefing (stage 3)
WATCHDOG_BRIEF_HOURS=24       # how far back the briefing looks for signals
```

`MAX_NEW_PER_FEED` (25) and `MAX_BRIEF_SIGNALS` (30) are constants at the top of `hbnscan.py`.

### Feed date window

**Settings** in the GUI (or `settings.json` in the program directory) restricts triage to
items published inside a date range:

```json
{
  "feed_since": "2026-11-01",
  "feed_until": "2026-11-30",
  "table_limit": 100
}
```

Both keys are optional; blank or missing means open-ended. Rules:

- Dates are `YYYY-MM-DD`; anything malformed is ignored and the range stays open.
- Items outside the window are skipped **before** triage, so they cost no model call
  and never consume the per-feed cap.
- Out-of-window items are not recorded as seen, so widening the range later picks them
  up on the next scan.
- Undated items are always kept — a missing date is not evidence the item is out of range.
- The active window is printed at scan start, shown in the GUI footer, and written into
  the report header.

This is separate from `WATCHDOG_BRIEF_HOURS`, which bounds how far back the *briefing*
looks among items already collected.

### Scheduling

Scanning is controlled from **Settings → Scanning** and mirrored in the footer:

- **Scan automatically** — off means the GUI never scans on its own; the Run button and
  the headless commands still work.
- **Every** — 5, 10, 15, 30, 60, 120 or 240 minutes.
- **only between** — a quiet-hours window (`HH:MM` to `HH:MM`). Blank means all day; the
  window wraps midnight, so `22:00`–`06:00` is overnight. Outside the window the GUI sleeps
  until it opens rather than polling.

These persist in `settings.json` as `auto_scan`, `scan_interval_minutes`,
`scan_window_start` and `scan_window_end`.

For a headless machine, the engine schedules itself:

```bash
python3 hbnscan.py --daemon     # or --watch
```

It re-reads settings every cycle, so toggling `auto_scan` or changing the interval from the
GUI takes effect on the next tick. A failed cycle is logged and the loop continues; `Ctrl-C`
stops it. One-shot `python3 hbnscan.py` and cron remain available.

### Searching signals

The **Flagged Signals** tab has a search box (`Ctrl+F`) that filters as you type, after a
250 ms debounce. It matches against headline, assessment, category and source URL, and
queries the whole database — not just the rows on screen — so an old signal is reachable
without scrolling.

`Rows` next to the search box controls how many rows are returned (50 → 5000) and is
saved as `table_limit` in `settings.json`. The table header always reports what you are
looking at, e.g. `SEARCH 'metronet': showing 51 of 51 match(es), limit 100`.

### Reading the grid

- **Click a column header** (`Detected`, `Sev`, `Category`, `Headline`) to re-sort. It sets
  the same `row_order` the Order menu uses, so the two never disagree.
- **Select a row** to see the detail pane: assessment, next step, published date, key
  facts, entities, confidence and whether the item was deep-read or only screened — the
  fields the grid has no room for.
- **Right-click a row** for *Open in browser*, *Copy headline* and **Not relevant
  (dismiss)** (`Delete` also works). Dismissing sets `flagged = 0, dismissed = 1` with a
  `[Dismissed]` marker in the analysis; the item leaves the report, the grid, exports and
  notifications but stays in the database, and `hbnscan.dismiss_item(conn, hash, undo=True)`
  restores it.
- **`NEW`** marks signals that arrived after the previous scan (`meta.prev_scan_at`), and
  the header counts them (`showing 100 of 186, limit 100, 2 new`). Nothing is marked on a
  fresh install, where everything would be new.

### Filters and ordering

A second row of controls narrows and sorts the grid. Every choice persists in
`settings.json`:

| Control | Values | Setting |
|---|---|---|
| Severity | `all`, `high`, `medium`, `low` | `filter_severity` |
| Category | `all` or any pipeline category | `filter_category` |
| Priority | `all`, `watchlist`, `core`, `context` | `filter_group` |
| Order | `priority`, `newest`, `oldest`, `severity`, `title`, `category` | `row_order` |

- `watchlist` shows only explicit watchlist hits; `core` shows non-watchlist regulatory
  and litigation items; `context` is everything else. Together they partition the set.
- Only whitelisted values are accepted — anything else in the file falls back to the
  default, and the SQL order fragments are fixed strings, never interpolated input.
- Filters combine with search and with the row limit; the header names every active
  filter and reports `showing N of M`.

Without an API key every stage degrades to the offline keyword heuristic — no network calls, no briefing, and the UI footer says `AI: Offline heuristic`.

Validate the configured key without running a scan (it prints a masked fingerprint, never the key):

```bash
python3 hbnscan.py --check
```

### AI key and spend

**Settings → DeepSeek API key** saves the key to `.env` (mode `0600`, comments and other
keys preserved) and applies it to the running process, so no restart is needed. **Test
key** spends one live call and reports the verdict plus token usage in the log.

Transient failures are retried rather than degrading the scan:

- `408`, `429`, `500`, `502`, `503`, `504` and connection/timeout errors are retried up
  to 3 times with exponential backoff and jitter.
- `Retry-After` is honoured and capped at 20 s.
- `401`/`402`/`403` and other 4xx fail immediately — a bad key should not burn attempts.

Every call's `usage` is counted, and a scan that spent model calls writes a row to
`scan_runs` (`ai_calls`, `prompt_tokens`, `completion_tokens`, `new_items`, `flagged`,
`deep_reads`, `model`). The scan log ends with the same figures:

```
[22:35:49] Scan complete. New: 2 | Flagged: 2 | Deep reads: 0 | AI: 2 calls, 400 in / 120 out
```

```sql
SELECT started_at, ai_calls, prompt_tokens, completion_tokens FROM scan_runs ORDER BY id DESC LIMIT 10;
```

### Notifications

Email and SMS alerts fire at the end of a scan for signals above a threshold, and only for
items that have not been sent before (`meta.last_notified_at` keeps the watermark).

Configure in `.env` — nothing secret goes into `settings.json`:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURITY=starttls        # starttls | ssl | none
SMTP_USER=me@example.com
SMTP_PASSWORD=app-password
SMTP_FROM=me@example.com
NOTIFY_EMAIL_TO=me@example.com
NOTIFY_SMS_TO=+15551234567
SMS_GATEWAY_DOMAIN=vtext.com
```

**SMS uses the carrier's free email-to-SMS gateway**, so no Twilio account is needed —
`+15551234567` becomes `15551234567@vtext.com`. Supported names: `verizon`, `att`,
`tmobile`, `sprint`, `uscellular`, `googlefi`, `cricket`, `metro`; anything else is used
verbatim as the domain. You can also put a full gateway address in `NOTIFY_SMS_TO` and
skip `SMS_GATEWAY_DOMAIN`.

Toggles live in **Settings → Notifications**: enable, minimum severity (`high` or
`medium`), and **Send test**, which sends one fixed message through the same code path.
Watchlist-pinned items can be excluded by setting `notify_include_watchlist` to false in
`settings.json`.

A failed send is logged and never fails the scan, and it does not advance the watermark, so
the next scan retries those items.

## The daily report

`latest_report.md` is rewritten after every scan and is rendered through the same code path
as the exports, so the two never drift. It opens with a summary block rather than dropping
straight into items:

```markdown
## Summary

- **186** flagged · 79 high · 90 medium · 17 low
- **180** regulatory/litigation · **110** watchlist matches
- **15** sources configured (8 direct, 7 search)
- Last scan: 2026-11-11 05:10:56
- AI: offline heuristic (no model calls)
```

Then, when anything arrived since the previous scan, a **New since the previous scan** list
of just those items; the executive briefing (fresh, or explicitly labelled as carried over);
and finally the top 50 signals ordered by severity → watchlist hit → core → recency, each
with category, priority, detected/published dates, verification state, assessment, facts,
entities and confidence.

The offline heuristic's catch-all next step ("Open the source item and confirm the details.")
is suppressed — repeated 180 times it was just noise.

## Exporting

Every export carries the full AI output — assessment, key facts, entities, confidence and
whether the item was deep-read or only screened — plus the latest executive briefing when
one exists.

From the GUI, **⤓ Export** writes exactly what the grid is showing: the active search,
severity/category/priority filters and the row limit all apply.

From the CLI:

```bash
python3 hbnscan.py --export signals.html          # format from the extension
python3 hbnscan.py --export signals.csv
python3 hbnscan.py --export briefing.docx
python3 hbnscan.py --export out.bin --format json # or state it explicitly
```

| Format | Notes |
|---|---|
| `.md` | Markdown, same shape as the daily report |
| `.html` | Self-contained page: severity badges, facts, entities, briefing block, print-friendly |
| `.csv` | One row per signal, 14 columns — spreadsheet and pivot friendly |
| `.json` | Nested objects plus a top-level `briefing` field, for downstream tooling |
| `.docx` | Word document, generated as OOXML directly — **no extra dependency** |
| `.txt` | Plain text, for paste or mail |

Only standard-library code is used: the `.docx` writer emits `[Content_Types].xml`,
`_rels/.rels` and `word/document.xml` into a zip, which Word and LibreOffice both open.
