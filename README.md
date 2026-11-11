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
- **Feed list repaired:** FTC and CFPB resolve directly; FCC is sourced via Google News (its own feeds are JavaScript-walled).

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

Deep reads cannot follow Google News item links: they are encrypted redirects that only resolve in a browser. Items from those feeds are still screened and briefed, but never marked *Verified* in the report. Point `feeds.txt` at publisher feeds when you need stage 2 on them.

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
  "feed_until": "2026-11-30"
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

Without an API key every stage degrades to the offline keyword heuristic — no network calls, no briefing, and the UI footer says `AI: Offline heuristic`.

Validate the configured key without running a scan (it prints a masked fingerprint, never the key):

```bash
python3 hbnscan.py --check
```
