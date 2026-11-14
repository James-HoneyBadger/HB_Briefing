# HB Briefing v1.0.0

**An open-source toolkit for assessing RSS and Atom feeds.** Point it at any set of feeds and it
answers one question continuously: *which of these items actually matter, why, and what should I
do about them?* It collects, grades, verifies against the linked article, writes a digest, and
delivers the result to a desktop grid, a report, an export or your inbox.

HB Briefing is highly configurable. You choose the feeds it reads, the subjects that interest
you, the names you track and how strict the assessment should be — the same engine can follow a
regulator, a market, a competitor, a research field or a single company.

- **Works with no configuration**: a fresh clone seeds its feeds, topics and examples and scans.
- **Works with no spend**: a deterministic offline heuristic grades every headline; an LLM key
  adds article verification and a written briefing.
- **Works on any subject**: ten profiles ship, a topic library covers over 200 subjects across 20
  interest areas, and your own files always layer on top.
- **Turns reading into research**: collect signals into dossiers, write open questions, track
  follow-up actions, and export a cited report — all offline and reproducible.

```bash
./start.sh              # checks the machine, installs what is missing, opens the desktop GUI
python3 hbnscan.py      # one headless scan
python3 hbnscan.py --serve   # the web app, in your browser
python3 hbnscan.py --tui     # the terminal UI
```

## Four front ends, one engine

| Front end | Start it with | Best for |
| --- | --- | --- |
| **Desktop GUI** | `./start.sh` or `python3 hbnscan_gui.py` | everyday use on a workstation: grid, detail pane, editors, log |
| **Web app** | `python3 hbnscan.py --serve` | a browser window, a second screen, or a machine without Tk |
| **Terminal UI** | `python3 hbnscan.py --tui` | SSH and headless servers: table, filters, detail, scan control |
| **CLI** | `python3 hbnscan.py` | cron, scripts, one-shot scans and exports |

They share one database and one `settings.json`, so a scan started anywhere appears everywhere,
and the schedule or theme you set in one is what the others show. None of them contains pipeline
logic: they all call the same engine.

The web app is a single self-contained page — no framework, no CDN, no build step — bound to
loopback and gated by a per-run token. The terminal UI is curses, so it works over SSH with
nothing installed but Python. Details and the HTTP API: [Web app and terminal UI](./docs/WEB-APP.md).

**Scheduling** is exposed in each of them, so the timer is never buried in a file:

| Where | On/off | Interval | Quiet hours |
| --- | --- | --- | --- |
| GUI | **Auto-Scan** in the toolbar | the `every` menu beside it | Settings → Scanning |
| Web | **Scan on a schedule** | the `every` menu | the *scan only between* field |
| TUI | `a` | `i` | `settings.json` |
| CLI | `--auto` / `--no-auto` | `--interval N` | `--window HH:MM-HH:MM` or `off` |

**Themes** ship as five named palettes shared by the desktop and web apps — `dark`, `light`,
`midnight`, `solarized`, `contrast` — stored as one setting so the choice follows you.

---

## What it does

One scan collects, refines and delivers. The three numbered stages are the model-backed ones;
container-style steps around them run with or without a key:

| Step | What happens | Needs a key |
|---|---|---|
| **Collect** | Poll every feed with conditional GET, reject unsafe payloads, hash and dedupe items | no |
| **Stage 1 — triage** | Produce a structured verdict per new item: `flagged`, `category`, `severity`, `entities`, `action` | no — the offline heuristic runs instead |
| **Stage 2 — deep read** | For flagged items, fetch the linked article, extract its text and re-judge it — with quoted `facts` and a `confidence` | yes |
| **Stage 3 — briefing** | One call per scan ranks the batch, merges syndicated duplicates and writes a digest | yes |
| **Deliver** | Grid, report, exports (md/html/csv/json/docx/txt), e-mail and SMS alerts | no |

A **verdict** is the unit of output: not just "relevant / not relevant", but a category, a graded
severity, a short assessment, key facts, named entities, a confidence and one concrete next step.
Everything downstream — ranking, filtering, the briefing, exports, alerts — reads that verdict.

**Declared intent always outranks inference.** A watchlist rule or a profile target pins a
subject to a category and severity; neither the model nor a deep read can downgrade or dismiss
it. That is what makes the tool dependable when a matter genuinely cannot be missed.

## Why the assessment is worth trusting

- **Structured, validated output.** The model's answer is parsed and repaired, never trusted:
  severity is clamped to four legal values, categories to the active profile's vocabulary, flags
  coerced, confidence bounded. Anything out of contract is corrected, not applied.
- **The article, not just the headline.** Stage 2 is what separates a guess from a finding: the
  page is fetched, reduced to readable text and re-judged. It can confirm, promote or reject — and
  a rejection is stored (`dismissed`) rather than deleted, so the decision stays auditable.
- **Word-start matching, headline evidence.** Offline scoring matches phrases at word starts, so
  `sues` does not fire on *issues* and `fined` does not fire on *defined*; weak signals are read
  from the headline only, so boilerplate in a summary cannot manufacture a signal.
- **Cross-feed dedupe.** The same story syndicated by two publishers is assessed once and marked
  `[Deduped]`, so the digest and the counts stay honest.
- **Fail open, never silently.** A missing key, a failed request, an unparseable answer or a
  paywalled article degrades to the offline verdict and a log line. Nothing is dropped, and
  nothing is quietly marked irrelevant.
- **Provenance on every row.** `[Watchlist]`, `[Topic]`, `[Local Heuristic]`, `[Deduped]`,
  `[Rejected by deep read]`, `[Dismissed]` say exactly which layer decided.

## Point it at anything

Three settings decide what counts as interesting, and they stack. All three are plain text or
JSON, edited in the app or in any text editor.

**A profile is the whole domain in one file.** Categories, keyword signals, prompt framing,
trusted sources, *plus* its own topic set and standing targets. Ten ship; choosing one in
**Settings → Profile** switches the entire engine at once. One of them is the shipped default
(regulatory and legal action in telecoms and internet service); the rest are worked examples of
how far the same file can move the tool:

| Profile | Domain |
| --- | --- |
| *(built-in)* + `regulatory.json` | regulatory and legal action in telecoms and ISPs (shipped default) |
| `cybersecurity.json` | vulnerabilities, exploitation, ransomware crews |
| `ai_and_silicon.json` | AI and chip regulation, export controls, antitrust |
| `energy_utilities.json` | rate cases, grid access, reliability, liability |
| `financial_services.json` | banking, lending and payment enforcement |
| `healthcare_pharma.json` | approvals, pricing, recalls, product liability |
| `consumer_products.json` | recalls, junk fees, consumer class actions |
| `transport_automotive.json` | vehicle recalls, airline rules, rail and trucking |
| `platforms_media.json` | platform antitrust, privacy, content rules |
| `general_news.json` | policy, litigation and business across beats |

**A topic is a standing subject** — interesting on its own, with no enforcement verb required.
Write one line in **🎯 Topics** (`topics.txt`), or start from the bundled library of over 200
subjects in 20 interest groups:

```
## Broadband and spectrum
municipal broadband | high
BEAD funding | high | Funding
pole attachment | low
```

Topics match at a word start, raise an item to at least their interest level, are labelled
`[Topic]` and rank alongside watchlist hits. Switch on *Search Google News for each active topic*
and the first 20 topics also become query feeds, so a new subject starts producing items on the
next scan without you finding a feed URL.

**A target pins a name.** `watchlist.txt` (and a profile's `targets`) promote any phrase to a
fixed category and status, ahead of keyword scoring and regardless of subject:

```
acme fiber & better business bureau | billing_dispute   | high
acme fiber                          | regulatory_action | high
acme capital                        | regulatory_action | medium
```

- Every `&`-joined phrase must appear at a word start in the title or summary.
- Rules are evaluated top to bottom, first match wins — narrow rules go above broad ones.
- `category` must exist in the active profile; `status` is `high`, `medium` or `low`.
- Malformed lines are skipped and reported in the scan log, never silently ignored.
- The list is also appended to the model prompt, so AI mode honours the same intent.

Your own `topics.txt` and `watchlist.txt` layer on top of whichever profile is active, so a local
edit is never lost by switching. All three are git-ignored and seeded from committed `.example`
twins on first run.

```bash
python3 hbnscan.py --check                     # active profile, categories, topics, targets
WATCHDOG_PROFILE=cybersecurity python3 hbnscan.py
```

## Feeds and discovery

`feeds.txt` is one URL per line, `#` for comments. Two kinds of source work, and the difference
matters for stage 2:

| Kind | What it is | Deep read |
|---|---|---|
| `direct` | the publisher's own RSS/Atom feed | works |
| `query` | a Google News search URL (`site:`, `"exact phrase"`, `OR`, `when:7d`) | works once the redirect is resolved to the publisher URL — unless that publisher refuses automated clients |

Google News item links are encrypted redirects, so HB Briefing decodes the payload (or runs the
`batchexecute` handshake) to recover the publisher URL before fetching an article, and caches the
result permanently. A publisher that answers `401/403` to automated clients is recorded, logged
once and skipped for the rest of the scan — triage still works from the feed summary, only
article-level verification is lost.

Where a feed is missing, a query is the practical answer; where a feed exists, it is always
better: it carries full summaries, publication dates and a stable identity. A feed that starts
failing prints its HTTP status with a hint (blocked, gone, rate limited) rather than a bare error.

`feeds.txt.example` ships with a verified block of major world, business and technology news
feeds — BBC, Guardian, NPR, Al Jazeera, NYT, Washington Post, the US networks, CBC, DW, France 24,
Sky, FT, Bloomberg, the Economist, Politico, Axios, Ars Technica, The Verge — commented out so the
default narrow profile stays narrow. Uncomment what you want, or start from
`profiles/general_news.json`.
`python3 hbnscan.py --check` validates the AI path; a normal scan's log names every feed it
touches and why it skipped one.

## Delivering the result

**The grid** is the working surface: detected time, publication date, severity, category,
headline and assessment, with a detail pane for the deciding layer, facts, entities, confidence
and verification state. Full-database search (debounced), severity/category/priority filters, a
publication-recency filter, eight orderings and a 50–5000 row limit all persist in
`settings.json`.

- **Published is a column of its own.** The grid shows the publisher's date (or `undated`), and
  *Newest* sorts by publication, not by when the scan happened to see it — a backlog ingest no
  longer looks like today's news. *Detected* keeps the ingest order.
- **Priority** is a real column: `watchlist`, `topic`, `core` (core for the active profile) or
  `context` — a partition, not a label, stored as a database field rather than parsed back out
  of the assessment text.
- **Click a column header** to re-sort; it sets the same `row_order` the Order menu shows.
- **`NEW`** marks items that arrived after the previous scan.
- **Right-click → Not relevant** (`Delete`) sets `flagged = 0, dismissed = 1`: the row leaves the
  grid, report, exports and alerts but stays in the database, auditable and reversible.

**The briefing** is stage 3's narrative digest, shown in its own tab and embedded in the report.

**The report** (`latest_report.md`) is rewritten after every scan and rendered through the same
code path as the exports, so the two cannot drift. It opens with counts (severity, core,
watchlist, topics, sources, last scan, last AI spend), then "new since the previous scan", then
the briefing, then the top 50 signals in priority order.

**Exports** carry the full verdict — assessment, next step, facts, entities, confidence,
verification state — plus the latest briefing:

| Format | Notes |
|---|---|
| `.md` | Markdown, same shape as the report |
| `.html` | Self-contained page: severity badges, facts, entities, briefing, print-friendly |
| `.csv` | One row per signal, 15 columns (including publication freshness) — spreadsheet and pivot friendly |
| `.json` | Nested objects plus a top-level `briefing`, for downstream tooling |
| `.docx` | Word document, generated as OOXML directly — no extra dependency |
| `.txt` | Plain text, for paste or mail |

```bash
python3 hbnscan.py --export signals.html           # format from the extension
python3 hbnscan.py --export out.bin --format json  # or state it explicitly
```

The GUI's **⤓ Export** writes exactly what the grid is showing: active search, filters and row
limit included.

**Alerts** fire at the end of a scan for signals above a threshold and only for items not sent
before; anything beyond the per-message cap stays pending for the next scan rather than being
dropped. E-mail goes through any SMTP server; SMS uses the carrier's free e-mail-to-SMS gateway,
so no third-party messaging account is needed.

**Scheduling** is a first-class feature: an interval, a scanning window that wraps midnight, and
a headless daemon for machines without a desktop.

```bash
python3 hbnscan.py --daemon     # loop per settings.json; Ctrl-C stops
```

## Configuration without hand-editing

**⛭ Configure** is a configuration workspace over every scan input, with validation and
diagnostics instead of a text editor:

| Section | Covers |
| --- | --- |
| Sources | add / edit / remove / test feeds, with last status and item counts |
| Topics | standing subjects with interest level and group; add from the shipped library; per-topic Google News queries |
| Targets | pin names to a category and status, with the profile's own categories in the menu |
| Criteria | the whole relevance model of a profile — categories, signals, noise rules, hints, prompts, shipped topics and targets; validate, save, save-as, activate |
| Scan limits | deep reads, briefing, triage and deep-read budgets, briefing window, per-feed item cap, retention |
| Notifications | SMTP transport, recipients, SMS gateway, on/off and minimum severity, with a real test send |
| Diagnostics | one check for broken URLs, unknown categories, duplicate rules, topic limits, criteria gaps, missing transport and failing sources |

**Your files stay yours.** The three text files are parsed line by line, so comments and headers
survive every edit; an untouched file is written back byte-for-byte, and a line the editor cannot
parse is reported rather than deleted. A rejected value leaves the stored configuration unchanged.
The text editors remain available for bulk work — both paths write the same files.

## Research workspace

Collecting is the easy half. HB Briefing also keeps the work that follows: **dossiers**,
**notes**, **actions** and **cited reports**, in the desktop app and from the shell.

| Tab | What it is for |
| --- | --- |
| **Today** | The signal grid, with a coverage strip: sources ok/failing/stale, signals, new since the last scan, undated |
| **Dossiers** | A named container (subject, entity or question) for the signals you chose, a note per signal, open questions, its actions, and a live report preview |
| **Actions** | Follow-ups with status, priority, due date and dossier; complete or reopen in one click; export as Markdown |
| **Sources** | Every feed's last fetch result: `ok`, `failing`, `stale` or `never`, with the last success time and the error text |
| **AI Briefing** | The stage-3 digest, unchanged |

**Reports are evidence, not narrative.** A dossier report is generated from stored rows only —
scope, at-a-glance counts, key developments with publisher and date, a full timeline, open
questions, actions, and an explicit *Evidence gaps* section ("4 signals were screened from the
feed only", "2 signals are undated", "source example.com failed its last fetch (HTTP 403)").
It never calls a model, so the same dossier always produces the same report, and it exports to
Markdown, HTML or JSON.

**Stories, not duplicates.** Syndicated copies are grouped into one story when their canonical
URLs match (tracking parameters, `www.`, fragments ignored) or their normalised headlines match.
The detail pane shows the group and its publishers, and **Detach from story** undoes a wrong
merge. Grouping changes presentation, never what is flagged.

**Sources are visible, not assumed.** Each feed records its last attempt, last success, HTTP
status and error. A failure keeps the previous success time, so a silently dead feed is obvious,
and the scan history records feeds attempted, feeds failed, cancellation and duration.

```bash
python3 hbnscan.py dossier new "Acme fiber" --summary "Are they building?"
python3 hbnscan.py dossier add "Acme fiber" --latest 10
python3 hbnscan.py dossier note 1 "Does the permit cover the whole route?"
python3 hbnscan.py dossier report 1 --export acme-fiber.md
python3 hbnscan.py action new "Call the clerk" --dossier 1 --priority high --due 2026-12-01
python3 hbnscan.py action list --all
python3 hbnscan.py sources --json
```

Every workspace table lives in the same SQLite file, so a backup is still one file
(**Settings → Data → Back up database**), and deleting a dossier never deletes the signals or
actions it referenced.

## Cost, control and failure modes

Nothing here requires a model. With no key the offline heuristic grades every item, the briefing
is skipped, and the footer says so. With a key, three budgets bound the spend per scan:

```dotenv
WATCHDOG_MAX_TRIAGE=40        # screening calls per scan; the remainder score offline
WATCHDOG_MAX_DEEP_READS=8     # article fetches per scan
WATCHDOG_BRIEFING=1           # 0 disables the digest (stage 3)
```

Transient failures are retried rather than degrading the scan: `408`, `429`, `5xx`, connection
errors and timeouts are retried up to three times with exponential backoff and jitter, honouring
`Retry-After`; a provider that rejects JSON mode is retried without it; a truncated answer is
re-asked with a larger token budget. `401`/`402`/`403` fail immediately — a bad key should not
burn attempts. Every call's `usage` is recorded in `scan_runs`, and the scan log ends with the
per-run totals.

Any OpenAI-compatible endpoint works — DeepSeek by default, with OpenAI, Ollama/local and a custom
URL selectable in **Settings → LLM / AI Provider** (`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`;
the older `DEEPSEEK_*` names remain as fallbacks).

## Reliability and safety

- **Conditional GET** (`ETag` / `Last-Modified`) per feed, with an 8 MiB payload cap.
- **DTD/entity rejection** before parsing, so a feed cannot mount an entity-expansion attack.
- **Links restricted to `http(s)`**; report and export text is escaped for its target format.
- **Parameterised SQL everywhere**; the only interpolated fragments are the active profile's own
  category names, quoted and escaped.
- **Untrusted text is fenced** in every prompt, with explicit instructions, and model output is
  validated structurally rather than executed.
- **No silent item loss**: items beyond the per-feed cap roll over to the next scan (the stored
  validator is deliberately not advanced), and a failed scan cycle never kills the daemon.
- **Atomic configuration writes**: `settings.json`, `.env`, `feeds.txt`, `topics.txt` and the
  report are replaced through a temp file, so an interrupted write cannot truncate them.
- **One scan at a time** (file lock) and **one GUI at a time** (instance lock).
- **Private by default**: keys live in `.env` (`0600`), masked in logs, never exported; your
  feeds, topics, priority rules and settings are git-ignored.

## Quick start

One command checks the machine over, installs whatever is missing, and opens the GUI:

```bash
./start.sh
```

It verifies `python3` (3.8+), installs `python3-tk` through the system package manager if
`tkinter` is absent, creates a project-local `.venv` so the system interpreter is left alone,
installs `requirements.txt` into it, and seeds `.env`, `feeds.txt`, `watchlist.txt` and
`topics.txt` from their `.example` twins on first run. Nothing is reinstalled once present.

Manual equivalent:

1. `cp .env.example .env` and, optionally, add an LLM key. Without one the tool runs offline.
2. `pip install -r requirements.txt` (Tkinter ships with most Python builds; if not:
   Debian/Ubuntu `sudo apt install python3-tk`, Arch `sudo pacman -S tk`,
   Fedora `sudo dnf install python3-tkinter`).
3. `python3 hbnscan_gui.py` for the desktop interface, `python3 hbnscan.py` for one scan,
   `--serve` for the browser, `--tui` for the terminal.
4. *(Optional)* `./install_autostart.sh` to add a desktop menu entry and login autostart.

## Documentation

| Document | For |
| --- | --- |
| [Instructions](./docs/INSTRUCTIONS.md) | Short, task-oriented steps: install, scan, tune, schedule, notify, export |
| [User Manual](./docs/USER-MANUAL.md) | Every screen, setting and file, with the reasoning behind it |
| [Technical Reference](./docs/TECHNICAL-REFERENCE.md) | Architecture, profiles and scoring, AI contract, schema, security, runbook |
| [Web App and Terminal UI](./docs/WEB-APP.md) | The browser and terminal front ends, the HTTP API, scheduling and themes from each |

HB Briefing is MIT licensed and uses only the Python standard library plus `python-dotenv`.
