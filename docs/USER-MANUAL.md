# HB Briefing — User Manual

HB Briefing is an autonomous open-source intelligence monitor for a narrow, high-value
question: *what regulatory or legal action just happened that I need to know about?*

It watches your feeds, decides what deserves attention, explains why, ranks the result, and
can deliver it to your screen, your inbox and your phone. This manual covers operating it:
concepts, the interface, every setting, and how to keep the signal-to-noise ratio high.

- **New here?** [Instructions](./INSTRUCTIONS.md) is the short version.
- **Maintaining or extending it?** [Technical Reference](./TECHNICAL-REFERENCE.md).

**Contents**

1. [Concepts and vocabulary](#1-concepts-and-vocabulary)
2. [Installation](#2-installation)
3. [Interface tour](#3-interface-tour)
4. [Scanning](#4-scanning)
5. [Working with the signal table](#5-working-with-the-signal-table)
6. [The report and the executive briefing](#6-the-report-and-the-executive-briefing)
7. [The watchlist](#7-the-watchlist)
8. [Settings reference](#8-settings-reference)
9. [Scheduling](#9-scheduling)
10. [Notifications](#10-notifications)
11. [Exports](#11-exports)
12. [Profiles and topics: retargeting the engine](#12-profiles-and-topics-retargeting-the-engine)
13. [Appearance and keyboard](#13-appearance-and-keyboard)
14. [Judging a verdict](#14-judging-a-verdict)
15. [Data, storage and maintenance](#15-data-storage-and-maintenance)
16. [Troubleshooting and FAQ](#16-troubleshooting-and-faq)
17. [Appendix: file map and glossary](#17-appendix-file-map-and-glossary)

---

## 1. Concepts and vocabulary

Understanding six words is enough to operate everything.

**Feed.** An RSS or Atom URL. HB Briefing polls each feed on every scan, asking for changes
only (HTTP `ETag` / `Last-Modified`).

**Item.** One entry from a feed: headline, summary, link, publication date. Items are hashed
and stored, so the same item arriving twice is never re-analysed or re-alerted.

**Signal.** An item the engine decided is worth your attention. Only signals are shown,
ranked, briefed, exported and mailed. The rest are recorded as seen (to prevent repeats)
but stay invisible unless you reclassify.

**Verdict.** The engine's judgement on an item: *is this a signal*, plus category, severity,
confidence, an assessment, a suggested next step, key facts and named entities. Verdicts are
produced offline by a deterministic heuristic, or by a model when a key is configured.

**Pipeline stages.** Three optional stages sit on top of the offline heuristic:

| Stage | Name | What it does | Needs a key |
| --- | --- | --- | --- |
| 1 | Triage | Screens an item, returns a structured verdict | yes (else heuristic) |
| 2 | Deep read | Fetches the linked article, re-judges with full text; can confirm, promote or reject | yes |
| 3 | Briefing | Writes a short executive narrative across the period's signals | yes |

**Watchlist.** Your own named priorities, in `watchlist.txt`. A match is authoritative — it
wins over both the heuristic and the model.

**Profile.** A named bundle of everything domain-specific: the categories, keyword signals and
prompt framing, plus the **topic set** and the **standing targets** that ship with it. The
default profile is regulatory action and litigation in telecoms and ISPs, with MetroNet as a
target; another profile retargets the whole engine (see [§12](#12-profiles-and-topics-retargeting-the-engine)).

**Topic.** A plain-language subject treated as always relevant — `municipal broadband`,
`zero-day exploit`, `drug price negotiation`. Topics are grouped, carry an interest level, and
optionally become their own Google News queries. They are what you retarget without writing
JSON (see [§12.5](#125-topics-the-subjects-inside-a-profile)).

**Target.** A named party from a profile or from `watchlist.txt` whose appearances are pinned
to a category and severity regardless of what the model thinks.

---

## 2. Installation

### 2.1 Requirements

- Python 3.10+ (3.13 tested), standard library plus `python-dotenv`.
- Tkinter only if you want the desktop interface.
- Linux, macOS or Windows. The bundled helpers (`start.sh`, `install_autostart.sh`) target Linux.

### 2.2 Guided start (Linux)

```bash
./start.sh
```

The launcher is defensive by design: it verifies Python, offers to install `python3-tk` via
your package manager, creates `.venv` and checks that it is actually usable (a broken
`ensurepip` leaves a half-built directory, which it detects and rebuilds), installs
`requirements.txt`, seeds `.env`, `feeds.txt` and `watchlist.txt` from their `.example`
templates when absent, then starts the GUI.

### 2.3 Manual start

```bash
python3 -m venv .venv && . .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env            # optional; edit for AI and mail
python3 hbnscan.py             # first scan
python3 -m hbnscan_gui         # desktop UI
```

### 2.4 Optional: start with your session

```bash
./install_autostart.sh
```

Installs a desktop entry under `~/.config/autostart`, and removes stale entries from earlier
product names on upgrade.

### 2.5 First-run files

| File | Created from | Notes |
| --- | --- | --- |
| `.env` | `.env.example` | key, provider, SMTP, pipeline tuning |
| `feeds.txt` | `feeds.txt.example` | 15 starter sources: regulators and aggregators |
| `watchlist.txt` | `watchlist.txt.example` | template with a commented example rule |
| `watchdog_state.db` | — | SQLite; schema created and migrated automatically |
| `settings.json` | — | GUI state, written on change |
| `latest_report.md` | — | regenerated by scans that found something new |

Existing files are never clobbered, so upgrades are safe and your data stays yours. Keep
`feeds.txt`, `watchlist.txt` and `.env` out of version control — they contain your interests
and secrets.

---

## 3. Interface tour

```
┌──────────────────────────────────────────────────────────────────────────┐
│ HB BRIEFING            v1.0.0 | two-stage triage, deep reads, briefing    │
│                                        ● IDLE | READY                    │
├──────────────────────────────────────────────────────────────────────────┤
│ ▶ Run Scan Now │ ✎ Edit Feeds │ ⚙ Settings │ ⤓ Export │ 📄 Open Report │
│ 🗑 Clear Logs │ ■ Stop │ ▓▓▓ progress                        □ Auto-Scan │
├──────────────────────────────────────────────────────────────────────────┤
│ ┌ Flagged Signals │ AI Briefing ┐                                       │
│ │ FLAGGED INTELLIGENCE SIGNALS (Double-click row to open URL)           │
│ │ 🔍 search…                          Rows: [100 ▾]  Clear             │
│ │ Severity: [all ▾] Category: [all ▾] Priority: [all ▾] Order: [▾]     │
│ │ ┌────┬──────┬──────────┬───────────────────┬─────────────────┐        │
│ │ │time│ sev  │ category │ headline          │ assessment      │        │
│ │ └────┴──────┴──────────┴───────────────────┴─────────────────┘        │
│ │ ┌ detail pane: assessment, facts, entities, confidence, link ─┐      │
│ └───────────────────────────────────────────────────────────────┘       │
├──────────────────────────────────────────────────────────────────────────┤
│ LIVE WATCHDOG TELEMETRY LOG                                              │
│ [2025-…] [INFO] Fetching 15 feeds…                                       │
├──────────────────────────────────────────────────────────────────────────┤
│ Feeds loaded: 15 | Database: watchdog_state.db | MIT Open Source         │
└──────────────────────────────────────────────────────────────────────────┘
```

**Header** — product name, version, the active profile's name, and a status badge: `IDLE`,
`SCANNING`, `DONE`, `CANCELLED`, or an error state.

**Toolbar**

| Control | Effect |
| --- | --- |
| **▶ Run Scan Now** | Starts a scan in a worker thread; the UI stays responsive |
| **✎ Edit Feeds** | Opens `feeds.txt` for editing and saves it back |
| **🎯 Topics** | Opens `topics.txt` — the standing subjects, with group and interest level |
| **⚙ Settings** | Feed window, LLM provider, scheduling, notifications, appearance, exports |
| **⤓ Export** | Writes the current signals to a chosen format |
| **📄 Open Report** | Opens `latest_report.md` in your default viewer |
| **🗑 Clear Logs** | Clears the log pane only (no data is deleted) |
| **■ Stop** | Requests cancellation; the scan finishes the item in flight |
| **Auto-Scan** | Enables the scheduled loop according to the interval and quiet window |

**Signal table** — five columns: time detected, severity, category, headline, assessment.
Headlines are colour-coded by severity (high, medium, low); declared-interest hits are marked
`[Watchlist]` or `[Topic]` and carry a group badge (the watchlist note or the topic's group). A
`NEW` marker distinguishes items added by the most recent scan from the historical backlog.

**Detail pane** — for the selected row: full assessment, key facts as a list, named entities,
model confidence, the suggested next step and the canonical link.

**Log pane** — live telemetry with severity-tagged lines: feed fetches, per-item decisions,
stage-2 outcomes, token usage, notification results, warnings. It is the first place to look
when something behaves unexpectedly.

**Footer** — feed count, active database and licence.

---

## 4. Scanning

### 4.1 What a scan does, in order

1. Loads `feeds.txt`, `watchlist.txt`, settings and the active profile.
2. Fetches every feed in parallel-safe sequence with conditional requests and a 15-second
   timeout; parse failures are reported, never fatal.
3. Parses RSS or Atom, normalises fields, filters by the feed date window.
4. Drops items already present in the database (hash match).
5. Applies the watchlist, then triage (model when configured, heuristic otherwise), within
   the per-scan triage budget; remaining items are scored offline.
6. Deep-reads the strongest candidates within the deep-read budget.
7. Persists verdicts, prunes unflagged items past the retention window, records the run.
8. Regenerates `latest_report.md` **only when something new was found**, then refreshes the
   briefing if it has aged past its window.
9. Sends notifications for new qualifying signals, if enabled.

### 4.2 Progress and cancellation

While scanning, the status badge reads `SCANNING` and the progress bar animates. **■ Stop**
sets a cancellation flag: the current item completes, results already found are kept, the log
records `CANCELLED`, and the report is still written. This is deliberate — an interrupted scan
should never lose work.

### 4.3 One scan at a time

A file lock (`scan.lock`, `fcntl.flock`) prevents overlapping scans, and a second GUI instance
refuses to start while the first is alive (`.hb-briefing-gui.lock`). If a scan was killed
abruptly on a system where locking is unreliable, delete the stale `.lock` files.

### 4.4 How long a scan takes

Roughly 3–10 seconds for feed fetching, plus, with AI enabled, ~1 second per triage call and
2–5 seconds per deep read. With the defaults (40 triage, 8 deep reads) expect 30–90 seconds.
Offline scans finish in seconds.

---

## 5. Working with the signal table

### 5.1 Filtering

| Filter | Values | Notes |
| --- | --- | --- |
| **Severity** | all, high, medium, low | plus *none* only via reclassification output |
| **Category** | all, or the profile's categories | default: regulatory_action, litigation, consumer_hardship, policy, market, other |
| **Priority** | all, watchlist, core, context | *core* = the profile's core categories |
| **Order** | priority, newest, oldest, severity, title, category | *priority* = severity → watchlist → core → recency |

All filters persist across restarts, and the same ordering logic drives the report, the
notification digest and exports — so what you see is what gets mailed and written.

### 5.2 Search

`Ctrl+F` focuses the search box. Typing filters **across the whole database**, not just the
visible page: headline, summary, category, facts and entities. Search is debounced, so typing
stays smooth on large tables. `Esc` or **Clear** resets search and filters.

### 5.3 Row limit

**Rows:** controls how many records are fetched (50 to 5000). Lower values keep the UI instant;
use search rather than a huge limit to find old material.

### 5.4 Reading a row

Select a row to populate the detail pane. The assessment field tells you which rule fired —
`[Watchlist] …`, `[Local Heuristic] Core focus: …`, `[Local Heuristic] Matched …`, or the
model's prose. Items with a deep read also carry facts, entities and a confidence score.

Double-click opens the source article in your browser.

### 5.5 Dismissing a false positive

Select the row and press `Delete`, or right-click → **Dismiss**. The item disappears from the
table, report, exports and notifications, but is *not* deleted: it is marked dismissed so you
can audit the decision later. Dismissal also teaches you something — if the same pattern keeps
appearing, adjust `watchlist.txt` or the profile rather than dismissing repeatedly.

### 5.6 Old rows and reclassification

Heuristics improve; historical verdicts do not change by themselves. `--reclassify` re-derives
category and severity for items that were scored offline, leaving the flag decision alone:

```bash
python3 hbnscan.py --reclassify --dry-run   # report what would change
python3 hbnscan.py --reclassify             # apply, then rewrite the report
```

It examines rows carrying a offline provenance marker — `[Local Heuristic]`, `[Watchlist]` and
`[Topic]` — and re-applies the same precedence a fresh scan uses: watchlist/target rule first,
then topic, then core-focus keywords. Rows whose verdict came from the model or a deep read are
never touched: the evidence that produced them (article text) is not stored, so re-deriving from
a headline would weaken the decision rather than improve it. The same applies after switching
profile: stored rows keep their category until you reclassify, and their stored category may not
exist in the new profile — the Priority and Category filters simply will not offer it.

---

## 6. The report and the executive briefing

### 6.1 `latest_report.md`

Rewritten at the end of **every** scan, CLI or GUI, and again on export; it is never left stale,
and it is rendered through the same code as the exports so the two cannot drift. The structure
is fixed so it can be diffed and mailed:

1. **Header** — the active profile's name, generation timestamp, feed-window line
   (`since`/`until` when set).
2. **Executive briefing** — a fresh stage-3 narrative, or the previous digest explicitly
   labelled as carried over (a scan with nothing new does not buy a new briefing).
3. **Summary** — counts by severity; how many signals are core for the *active profile*, how
   many are watchlist matches and how many are topic matches; source counts; last scan time;
   last AI spend.
4. **New since the previous scan** — the items that arrived after the last scan, with the true
   total when it exceeds the 15 listed.
5. **Signals** — the top 50 in priority order (severity → watchlist or topic → core → recency),
   each with detection time, severity, category, priority, headline, assessment, key facts,
   entities, confidence, verification state and link.

An empty scan therefore still refreshes the counts and the timestamp; only the *briefing* is
carried over, and the report says so.

### 6.2 The briefing

The **AI Briefing** tab shows the latest narrative: what happened in this period, why it
matters, and what to watch. The briefing is bounded by `WATCHDOG_BRIEF_HOURS` (default 24) and
is regenerated only when it is older than that window and there are new signals, so repeated
scans do not pay for identical text. Each briefing is stored with its model name and the number
of signals it covered, so you can tell a 3-signal briefing from a 30-signal one.

The briefing is deliberately conservative: it summarises stored verdicts and does not invent
facts. When it has little to work with it says so rather than padding.

---

## 7. The watchlist

The watchlist is the highest-authority layer in the engine. It exists because *you* know which
names matter, and no keyword model will infer that a particular company is the point of the
exercise.

### 7.1 Syntax

```
phrase [& phrase ...] | category | status [| note]
```

| Part | Rules |
| --- | --- |
| `phrase` | Case-insensitive, matched at word boundaries (`MetroNet` will not match `MetroNetworks`) |
| `&` | All phrases must be present; up to several per rule |
| `category` | Must be one of the profile's categories |
| `status` | `high`, `medium` or `low` |
| `note` | Optional free text, printed in the log for traceability |

Lines starting with `#` are comments. Malformed lines are skipped with a warning, so a typo
cannot silently disable the rest of the file. The file is re-read when its modification time
changes; no restart is needed.

### 7.2 What a hit does

- Sets `flagged = 1` with the rule's category and severity — no inference involved.
- Prefixes the summary with `[Watchlist]` and records the matched rule.
- Ranks above every non-watchlist item in table, report, digest and export.
- **Cannot be overridden**: a stage-2 deep read that disagrees is logged, not applied.

### 7.3 Practical guidance

- Be specific. `acquisition` alone will fire constantly; `KKR & acquisition` will not.
- Prefer `&` over multiple single-phrase rules when the combination is what matters.
- Track your own identifiers privately — keep personal names, addresses and case numbers in
  your untracked `watchlist.txt`, not in the shipped `.example`.
- A watchlist rule is also a filter you can select: **Priority → watchlist** shows exactly
  what your rules caught, and **Priority → topic** does the same for topic matches.
- The active profile ships its own `targets` with the same syntax and the same pinning
  behaviour; your `watchlist.txt` is consulted first, so a local rule always wins a tie.

---

## 8. Settings reference

**Settings** writes to `settings.json` immediately on **Save**; `.env` is written for secrets
and mail transport. Values are validated — out-of-range numbers are clamped, unknown choices
fall back to defaults, and no field is ever interpolated into SQL.

### 8.1 Profile

- **Profile** — the active bundle of domain rules, topics and targets. The list shows
  *built-in default* plus every `.json` found in `profiles/`; the value is saved by **name**, so
  a moved checkout still resolves it. A description underneath states the profile's name, how
  many categories, topics and targets it carries, and what it is for.
- Choosing another profile and pressing **Save** applies it immediately: categories, filter
  menus, prompts, topic set and targets all switch, and the log records the change. Nothing
  already stored is reclassified — run `--reclassify` for that.

Your own `topics.txt` and `watchlist.txt` still layer on top of whichever profile is active.

### 8.2 Feeds

- **RSS Feed URLs** — the raw contents of `feeds.txt`, one URL per line.
- **Feed date range — From / To** — only items published inside the window are triaged. Leave
  blank for no bound. Useful for backfills (`from` only) and for freezing a period for a report.
  Items outside the window are still recorded as seen, so widening the window later does not
  re-alert you for old news.

### 8.3 LLM / AI provider

| Field | Meaning |
| --- | --- |
| **Provider** | Preset that fills the two fields below: DeepSeek, OpenAI, Ollama/Local, Custom |
| **Model** | Model identifier sent to the endpoint, e.g. `deepseek-chat`, `gpt-4o-mini`, `llama3.1` |
| **Base URL** | Full chat-completions URL, e.g. `https://api.deepseek.com/v1/chat/completions` |
| **API key** | Stored in `.env` with `0600` permissions; blank keeps the existing key |
| **Test** | Sends one minimal request and reports the verdict, or the failure reason |

The status line above the fields tells you whether AI is live and which model is active. With
no key the engine runs offline and says so.

### 8.4 Scanning

- **Scan automatically** — enables the scheduled loop (`auto_scan`).
- **Every N minutes** — 5, 10, 15, 30, 60, 120 or 240 (5–1440 accepted in the file).
- **Quiet window start / end** — no scans between these clock times; the window wraps midnight
  correctly (`22:00`–`06:00` means overnight). Blank disables the restriction.

### 8.5 Notifications

- **Email / SMS me new high-priority signals** — master switch (`notify_enabled`).
- **At least** — `high`, or `high + medium` (`notify_min_severity`).
- **Declared interest** (`notify_include_watchlist` in `settings.json`, default on) — keep or
  exclude your own watchlist **and topic** matches from the digest. There is no checkbox; edit
  the file.
- **Max items** — 1–20 per message.
- **Send test** — exercises the real transport and reports the SMTP error verbatim.

Transport lives in `.env`; see [§10](#10-notifications).

### 8.6 Appearance

- **Theme** — dark or light, applied immediately and remembered.
- **Text size** — 80 % to 150 % scaling for the whole interface, for dense tables or large displays.
- Window size, position, maximised state and pane divider position persist automatically.

---

## 9. Scheduling

Two independent mechanisms, both respecting the same schedule:

**GUI Auto-Scan** — while the window is open, a timer starts scans at the configured interval,
skipping any tick inside the quiet window or while a scan is already running.

**Headless daemon** — `python3 hbnscan.py --daemon` (alias `--watch`) loops until `Ctrl+C`.
It sleeps in short increments so cancellation is immediate, and logs each cycle. Ideal for a
server, a spare desktop, or a systemd user unit:

```ini
[Unit]
Description=HB Briefing monitor
[Service]
Type=simple
WorkingDirectory=/home/you/hb-news-scan
ExecStart=/usr/bin/python3 /home/you/hb-news-scan/hbnscan.py --daemon
Restart=on-failure
[Install]
WantedBy=default.target
```

Design notes:

- **Disabled means idle.** If automatic scanning is off, the daemon does not spin: it waits and
  re-reads settings, so re-enabling in the GUI takes effect without a restart.
- **Quiet hours are inclusive of the end boundary and wrap midnight.**
- **Overlap is impossible** thanks to the scan lock; a slow scan delays the next tick rather
  than stacking.
- **Failures do not exit the loop.** A dead feed, an expired key or a DNS outage is logged; the
  next cycle tries again.

---

## 10. Notifications

### 10.1 What gets sent

A digest of **new** signals since the last notification, filtered by your minimum severity,
optionally excluding declared-interest hits (watchlist rules and topics), capped by max items,
ordered by the same priority logic as the table. Each entry carries severity, category, headline
and link; the subject line states the count and the leading headline, and both the subject and
the footer carry the active profile's name.

Nothing is sent when nothing qualifies — no "0 new items" noise. The last notification time is
stored, so restarts never re-send the same items.

### 10.2 E-mail

Any SMTP server works. Use an app password, never your account password. `SMTP_SECURITY`
accepts `starttls` (587, most common), `ssl` (465) or `none` (local relay only).

### 10.3 SMS

Texts go through carrier email-to-SMS gateways, so there is no paid API and no third party
holding your data. Give `NOTIFY_SMS_TO` bare numbers (which `SMS_GATEWAY_DOMAIN` turns into
addresses) or full gateway addresses you already know. Multiple recipients are comma-separated
in both variables.

Gateways are best-effort by nature: carriers throttle, truncate long messages to ~160
characters per segment, and can silently drop sender addresses they distrust. Treat SMS as a
heads-up and the e-mail as the record. If texts stop arriving, verify the address by mailing the
gateway manually once.

### 10.4 Failure behaviour

Notification failures are logged and swallowed — a scan's value is the data it collected, and
losing that because SMTP was down would be a worse bug than a missed alert.

---

## 11. Exports

**Export** (or `--export`) writes the current signals with filters applied. The format follows
the extension; use `--format` to force one.

| Format | Best for | Characteristics |
| --- | --- | --- |
| **Markdown** | archiving, git, notes | full report structure, plain text, diffable |
| **HTML** | e-mailing, publishing, printing | self-contained styling, no external assets |
| **DOCX** | circulating to non-technical readers | real Word document, validated to open in Word and LibreOffice |
| **CSV** | spreadsheets, pivots, tracking | flat columns, one row per signal |
| **JSON** | scripting, dashboards, pipelines | rows plus briefing metadata and run context |
| **TXT** | pasting into tickets or chat | no markup at all |

### 11.1 What is included

Every export carries the same fields:

`detected`, `published`, `severity`, `category`, `priority`, `headline`, `link`, `source`,
**AI assessment**, **next step**, **key facts**, **entities**, **confidence**, **verification**.

The AI columns are the payoff: instead of re-reading sources to write a summary, you export and
the analysis is already in the document — what happened, why it matters, what to do next, and
how confident the engine is. When offline verdicts are exported, those columns carry the
heuristic's own explanation rather than an empty cell, so the document is never misleading
about what was and was not model-generated.

### 11.2 Practical patterns

- **Weekly pack** — export `.docx` for management, `.csv` for the tracker, both from the same
  filtered view.
- **Evidence trail** — export `.json`; it retains ids, timestamps and confidence, so a later
  review can prove what was known when.
- **Briefing without opening the app** — `python3 hbnscan.py --export briefing.html` then mail
  the file.

---

## 12. Profiles and topics: retargeting the engine

Two layers decide what the monitor considers interesting, and they stack:

| Layer | Where it lives | What it holds | Who it suits |
| --- | --- | --- | --- |
| **Profile** | `profiles/*.json` (or `profile.json`) | Domain rules (categories, keyword signals, prompt framing, trusted hosts) **plus** a topic set and standing targets | Anyone retargeting the engine to a different world of news |
| **Topics** | `topics.txt`, and the profile's own `topics` list | Plain-language subjects with an interest level and a group | Anyone adding or dropping a subject without touching JSON |

Topics are the everyday control; a profile is the larger switch that brings its own rules,
its own topic set and its own named targets. Everything in your `topics.txt` and
`watchlist.txt` still applies on top of whichever profile is active, so a local edit is never
lost by switching.

### 12.1 Selecting a profile

Precedence, highest first:

1. `WATCHDOG_PROFILE` environment variable (a name in `profiles/`, or a path).
2. The **Profile** dropdown in **Settings**, stored as the `profile` key in `settings.json`.
3. `profile.json` in the app directory.
4. The built-in default.

```bash
WATCHDOG_PROFILE=cybersecurity python3 hbnscan.py          # one run, by name
WATCHDOG_PROFILE=profiles/cybersecurity.json python3 hbnscan.py
```

```ini
WATCHDOG_PROFILE=cybersecurity                             # .env, permanent for CLI and GUI
```

A value is resolved in this order: an absolute path; a relative path from the working
directory; the same relative path from the app directory; then `profiles/<value>.json`. So
`profiles/cybersecurity.json` works from a `.env`, a desktop launcher or a cron job without
depending on where the process started. The GUI stores the **name**, not the path, so moving or
renaming the checkout does not break the setting.

```bash
python3 hbnscan.py --check      # prints the active profile, categories, topics and target rules
```

Every scan prints the same line as its first log entry. A missing, unreadable or invalid file
falls back to the default profile — the engine always starts — and an unknown name in
`settings.json` simply resolves to the default. Switching profile does not rewrite stored
verdicts: new scans use the new rules, and `--reclassify` (see [§5.6](#56-old-rows-and-reclassification))
re-derives the offline ones.

### 12.2 Shipped profiles

Ten profiles ship in `profiles/`. Each is a complete, editable document: copy it, change it,
drop it back in the folder, and it appears in the Settings dropdown.

| File | Focus | Topics | Targets |
| --- | --- | --- | --- |
| *(built-in)* | regulatory action and litigation in telecoms and ISPs | 18 | MetroNet (high), MetroNet & BBB, KKR |
| `regulatory.json` | the default, materialised as a file to copy and edit | 18 | same as built-in |
| `cybersecurity.json` | vulnerability and exploitation | 14 | LockBit, Cl0p, Scattered Spider |
| `ai_and_silicon.json` | AI and semiconductor regulation, export controls, antitrust | 12 | — |
| `energy_utilities.json` | rate cases, grid access, reliability, wildfire liability | 12 | — |
| `financial_services.json` | enforcement and litigation in banking, lending, payments | 12 | — |
| `healthcare_pharma.json` | FDA/CMS action, pricing, recalls, product liability | 12 | — |
| `consumer_products.json` | recalls, junk fees, consumer class actions | 11 | — |
| `transport_automotive.json` | recalls, airline consumer rules, rail and trucking enforcement | 10 | — |
| `platforms_media.json` | platform antitrust, privacy, content rules, media bargaining | 11 | — |
| `general_news.json` | policy, litigation and business across beats | 11 | — |

The default profile is the regulatory/legal one because that is what the tool is for. Its
targets make MetroNet — and, at medium, its investor KKR — a standing subject: an item naming
MetroNet is pinned to `regulatory_action`/`high` even when the headline says nothing about
regulation, and a deep read cannot downgrade it.

Shipping a *regulator* as a target is deliberately avoided in the other profiles: a target pins
category and severity outright, so pinning `FDA` would flatten a genuine high-severity recall
down to the target's level. Regulators are covered by `trusted_source_hosts` and
`category_hints` instead, which grade each item on its own merits.

### 12.3 Authoring your own profile

Copy the closest example to `profile.json` (or to `profiles/myname.json`) and edit. Fields:

| Field | Purpose |
| --- | --- |
| `name` | Product name shown in the header and report |
| `version` | Your profile's version string |
| `focus` | One sentence naming the core of the domain — injected into prompts |
| `secondary` | What is relevant but not the point |
| `briefing_interest` | What the briefing should emphasise |
| `deep_always_relevant` | What makes an item automatically worth a deep read |
| `severity_high` / `medium` / `low` | Natural-language grading rules for the model |
| `excluded` | What to ignore (marketing, index pages, how-tos) |
| `categories` | Full category vocabulary (`other` is appended automatically) |
| `core_categories` | The subset ranked first and used for "core focus" scoring |
| `category_hints` | Ordered `[category, [keywords]]` pairs used to classify |
| `strong_signals` | Keywords decisive on their own |
| `weak_signals` | Keywords that need corroboration |
| `noise_title_signals` | Title patterns marking a page as navigation/UI, not news |
| `enforcement_signals` | Words that raise severity inside a core category |
| `trusted_source_hosts` | Hosts whose press feeds are in scope by default |
| `aggregator_hosts` | Redirect hosts needing resolution before deep reads |
| `topics` | Topic set shipped with the profile — see [§12.5](#125-topics-the-subjects-inside-a-profile) |
| `targets` | Standing targets shipped with the profile — see [§12.6](#126-targets-named-parties-in-a-profile) |

Guidance that saves time:

- `strong_signals` should be phrases you would accept alone in a headline. Multi-word phrases
  are matched literally, so `consent order` beats `order`.
- Put ambiguous words in `weak_signals`; the engine wants two of them, or a strong signal,
  before flagging.
- `noise_title_signals` stops index pages, login screens and form titles being treated as news.
- Keep `core_categories` narrow. If everything is core, ranking stops discriminating.
- Every `categories` entry should appear in `category_hints`; a category with no hints can
  only be reached by the model, never by the offline heuristic.
- After editing, run a scan and read the log: every decision prints the rule that fired, which
  is the fastest way to find a missing keyword or an over-eager one.

### 12.4 How the layers combine during scoring

For each item, the offline heuristic — and the model's verdict afterwards — is overridden in
this order, first match wins:

1. **Watchlist / target** — `watchlist.txt` rules first, then the profile's `targets`. Pins
   category and severity; nothing can downgrade it.
2. **Topic** — the best matching topic raises the item to at least its interest level and marks
   it `[Topic]`. A deep read cannot dismiss it.
3. **Core focus** — profile `core_categories` matched through `category_hints`.
4. **Strong signal in the headline** — two or more, or one plus an enforcement word, means high.
5. **Weak signals** — counted from the headline only, never the summary, to avoid contamination.
6. **Trusted source** — a low-severity fallback for regulator press feeds.

Ranking in the report and the grid follows the same priority: severity, then watchlist/topic
hit, then core, then recency.

### 12.5 Topics: the subjects inside a profile

A topic is a subject that is relevant on its own — no enforcement verb required. Add them in
**🎯 Topics** in the toolbar, or edit `topics.txt` directly (one subject per line):

```
broadband overbuild                     subject only, medium interest
municipal broadband | high              choose an interest level
BEAD funding | high | Broadband         choose a level and a group
```

- `#` starts a line comment; `## Heading` opens a **group**. Every topic below a heading belongs
  to it until the next one, so a block can be switched off wholesale by commenting it out.
- The group is the heading the topic is listed under, the grouping used in the AI prompt, and
  the parenthesised text in the stored `[Topic] <label> (<group>)` note, so a hundred topics
  stay readable.
- Matching is at a word start and case-insensitive: `sue` does not match `issues`.
- When topics overlap, the **highest interest level wins, then the longest phrase** — so
  `municipal broadband` beats a bare `broadband`.
- Limits: 300 active topics, 120 characters each.
- Switching on **Search Google News for each active topic** in the topics editor turns the
  first 20 topics into Google News query feeds, so a new subject starts producing items on the
  next scan without you adding a feed URL. It is off by default: with a profile carrying 20
  topics, leaving it on would add 20 feeds to a fresh install before anything is tuned.

The active set is the profile's `topics` array **plus** your `topics.txt`, in that order. A line
in `topics.txt` whose wording matches a profile topic replaces it, which is how you re-grade or
re-group a shipped topic without copying the whole set. Duplicates are ignored.

`topics.txt.example` ships an empty, heavily annotated library of 20 groups and about 210
subjects across many interests — telecom, energy, health, finance, transport, platforms, AI,
consumer products, environment, labour, education and more. Press **Insert Topic Library** in
the topics editor to append it, then uncomment what you want.

### 12.6 Targets: named parties in a profile

`targets` is the profile's own watchlist. Same semantics as `watchlist.txt`, same syntax:

```json
"targets": [
  {"match": ["metronet"], "category": "regulatory_action", "severity": "high",
   "note": "default target"},
  {"match": ["metronet", "better business bureau"], "category": "billing_dispute",
   "severity": "high", "note": "complaint pattern"}
]
```

An item naming every phrase in `match` (word-start, case-insensitive) is pinned to that
category and severity, marked `[Watchlist]`, and sorted into the highest tier. Your
`watchlist.txt` is consulted **before** the profile's targets, so a local rule always wins a
tie. A profile with `"targets": []` is perfectly valid — most shipped profiles have none, and
expect you to add your own parties locally.

---

## 13. Appearance and keyboard

**Themes.** Dark (default) and light, switchable in Settings and applied to every widget
instantly — including the table, log, dialogs and menus.

**Text size.** 80–150 %, scaling all fonts proportionally for dense tables or high-DPI screens.

**Layout.** The main area is a draggable splitter: drag the divider to trade table space for
log space. Size, position, maximised state and divider position are restored on next launch.

**Keyboard**

| Key | Action |
| --- | --- |
| `Ctrl+F` | Focus the search box |
| `Esc` | Clear search (while in the search box) |
| `Delete` | Dismiss the selected signal |
| `Double-click` | Open the selected signal's source in the browser |
| `Right-click` | Row context menu — open, copy link, dismiss |

---

## 14. Judging a verdict

Signals differ in how much you should trust them, and the UI is explicit about which is which.

**Offline heuristic verdicts** are deterministic and explainable. The assessment names the rule
and the matched words, so you can verify the logic instead of trusting a score. They never
invent facts and never produce a confidence value.

**Model verdicts** add an assessment in prose, a suggested next step, entities and a confidence
score between 0 and 1. Confidence is the model's own estimate of its judgement on the *text it
saw*, not a calibrated probability. Use it as a tiebreaker: below ~0.5 treat the item as a lead
to verify; above ~0.8 the classification is usually sound.

**Deep-read verdicts** are the strongest: the model saw the article, not just the summary. They
also carry key facts extracted from the text, which is what makes exported documents useful
without re-reading sources. A deep read can also *reject*: when it concludes an item is not a
signal with at least 0.5 confidence, the item is marked dismissed and logged as a rejection —
visible in the audit trail, silent in the table.

**Watchlist verdicts** are authoritative by design; see [§7](#7-the-watchlist).

Severity means: **high** — action taken or imminent (a fine, a filed suit, a final rule);
**medium** — a process that may lead to action (comment period, investigation, appeal);
**low** — in scope but background.

---

## 15. Data, storage and maintenance

Everything is local. There is no account, no telemetry and no server; the only outbound
traffic is your feeds, the articles fetched for deep reads, your LLM endpoint and your SMTP
server.

### 15.1 The database

`watchdog_state.db` (SQLite) holds seen items and verdicts, feed cache validators, briefings,
per-scan statistics and internal state. Schema, column semantics and the row-state model are documented in the
[Technical Reference §14](./TECHNICAL-REFERENCE.md#14-storage-schema-and-lifecycle).

### 15.2 Growth and retention

Unflagged items older than 90 days are pruned on each scan. Flagged items are kept indefinitely —
they are the record. In normal use with 15 feeds the database stabilises in the low tens of
megabytes.

### 15.3 Backup

Copy the database while no scan is running (or use `sqlite3 watchdog_state.db ".backup b.db"`).
It is a single portable file; restore by copying it back. `feeds.txt`, `watchlist.txt`,
`settings.json` and `.env` complete the picture — four text files and one database reproduce the
entire installation.

### 15.4 Resetting

| Goal | Action |
| --- | --- |
| Forget everything and start clean | Stop the app, delete `watchdog_state.db`; it is recreated |
| Re-alert everything | Same as above (items are only sent once, by design) |
| Restore default settings | Delete `settings.json` |
| Restore default feeds | Copy `feeds.txt.example` over `feeds.txt` |
| Start over with AI | Blank the key in Settings, or remove it from `.env` |

### 15.5 Cost control

With AI enabled, cost is bounded per scan rather than per feed: at most 40 triage calls and 8
deep reads. Triage is a few hundred tokens; a deep read is a few thousand. Expect fractions of
a cent per scan on budget providers. Each run records calls, prompt tokens and completion
tokens, so actual spend is auditable rather than estimated.

---

## 16. Troubleshooting and FAQ

### Nothing is flagged

Check three things in order: the feed date window in Settings (an old `from` date with a `to`
date in the past excludes everything), the log's per-item lines to see which rule fired or did
not, and finally whether the profile's vocabulary matches your sources. Run
`python3 hbnscan.py --reclassify --dry-run` — if it reports changes, the stored verdicts are
stale relative to the current rules and applying them will fix the visible output.

### Everything is flagged

Usually a `weak_signals` entry that is far too common in your industry, or a source whose every
article mentions your keywords. Inspect the assessment text: it names the matched words. Demote
the offender to a more specific phrase, or add the site's title boilerplate to
`noise_title_signals`.

### Deep reads never happen

Five causes, in likelihood order: the deep-read budget was consumed by higher-ranked candidates;
the article returned fewer than 200 readable characters (paywall, JavaScript-only page, PDF);
the publisher refuses automated clients (`HTTP 401/403` — logged once per host per scan, then
that host is skipped for the rest of the run); an aggregator link could not be resolved; or deep
reads are disabled (`WATCHDOG_DEEP_READ=0`). The log names the reason, and for a blocked
publisher it names the host.

### The report is stale

It should not be: the report is rewritten at the end of every scan, even an empty one, so a
stale timestamp means the last scan did not finish. Check the log for the failing feed, a
cancelled run, or the scan lock (`Another scan is already running`). The "new since previous
scan" section will simply be absent when nothing arrived.

### The briefing disappeared

Briefings are regenerated only when older than `WATCHDOG_BRIEF_HOURS` and new signals exist.
If no briefing exists, either stage 3 is disabled (`WATCHDOG_BRIEFING=0`), no key is configured,
or the last generation failed — the log records the reason.

### "Feed unusable (HTML)" in the log

That URL returns a web page, not a feed. Find the site's real feed URL (`/feed`, `/rss.xml`,
`/feeds/press-releases.xml`) or use a Google News query instead. Some government sites drop
RSS entirely; aggregator queries are the workaround.

### The model answers nonsense or refuses

Try a stronger model in Settings, or lower the workload (`WATCHDOG_MAX_TRIAGE`). Very small or
heavily quantised local models handle the strict JSON contract poorly; the engine retries and
falls back to the heuristic rather than storing a malformed verdict.

### `--check` reports 402/429

402 means the provider account has no credit; 429 means throttling — the engine honours
`Retry-After` and backs off during scans. Neither stops the offline pipeline.

### A second GUI will not start

Single-instance lock, by design — two windows writing one database would fight. If you are
certain no instance is running, delete `.hb-briefing-gui.lock`.

### Does it work without any API key at all?

Yes. Feed fetching, deduplication, the watchlist, the heuristic, the report, notifications and
every export format work offline. Only the three model stages need a key, and each is optional.

### Does it send my data anywhere else?

No. Feeds and articles are fetched directly, the LLM call goes only to the endpoint you
configure, mail goes only to your SMTP server, and nothing is written outside the application
directory.

### Can I run several independent monitors?

Yes — copy the directory. Each copy is self-contained (own database, feeds, settings, `.env`),
and the locks are per-directory, so instances do not interfere.

---

## 17. Appendix: file map and glossary

### File map

| Path | Role | Safe to edit |
| --- | --- | --- |
| `hbnscan.py` | engine: feeds, scoring, AI, report, exports, notifications, daemon | for development |
| `hbnscan_gui.py` | Tkinter desktop interface | for development |
| `start.sh` | dependency bootstrap and launcher | yes, for your environment |
| `install_autostart.sh` | desktop autostart entry | yes |
| `.env` | key, provider, SMTP, pipeline tuning | **yes — keep private** |
| `.env.example` | template for the above | yes |
| `feeds.txt` | your sources | **yes — keep private** |
| `feeds.txt.example` | starter sources | yes |
| `watchlist.txt` | your priorities | **yes — keep private** |
| `watchlist.txt.example` | template | yes |
| `profile.json` | your domain override (optional) | yes |
| `profiles/*.json` | ten shipped profiles (rules + topics + targets) | copy, do not overwrite |
| `topics.txt` | your standing subjects | **yes — keep private** |
| `topics.txt.example` | annotated topic library, ~210 subjects in 20 groups | copy into `topics.txt` |
| `settings.json` | GUI state | written by the app |
| `watchdog_state.db` | all collected data | back up, do not hand-edit |
| `latest_report.md` | most recent report | regenerated |
| `requirements.txt` | Python dependencies | yes |
| `docs/` | these documents | yes |

### Glossary

| Term | Meaning |
| --- | --- |
| **Aggregator link** | A redirect URL (e.g. Google News) that must be resolved before the article can be read |
| **Briefing** | Stage-3 narrative summary across a period's signals |
| **Core category** | A category the profile treats as the point of the exercise; ranked above context |
| **Profile** | A bundle of domain rules, topics and targets that retargets the whole engine |
| **Topic** | A standing subject, grouped and graded, that is relevant on its own |
| **Target** | A named party pinned to a category and severity wherever it appears |
| **Deep read** | Stage-2 fetch and re-judgement of the full article text |
| **Dismissed** | Marked as not a signal after review; hidden but auditable |
| **Flagged** | Judged to be a signal; appears in table, report, exports and alerts |
| **Item hash** | Stable identifier for an item, used for deduplication |
| **Watchlist** | Your named priorities, applied after screening and never downgraded |
| **Retention window** | 90 days for unflagged items; flagged items are permanent |
| **Signal** | A flagged item |
| **Triage** | Stage-1 screening that produces a structured verdict |
| **Verdict** | The structured judgement: flag, category, severity, confidence, facts, entities |
