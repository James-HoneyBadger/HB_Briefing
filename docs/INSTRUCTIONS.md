# HB Briefing — Instructions

Task-oriented steps for getting HB Briefing running and keeping it tuned. For the full
interface walkthrough see the [User Manual](./USER-MANUAL.md); for internals, schema and
algorithms see the [Technical Reference](./TECHNICAL-REFERENCE.md).

---

## What it does

HB Briefing is a toolkit for assessing RSS/Atom feeds. It watches the feeds you give it,
decides which items are genuine signals, and surfaces them — ranked, explained, briefed and
optionally mailed to you. It works **with no API key**: a deterministic offline heuristic
scores every headline. Add an LLM key and two more stages light up: article deep reads (which
confirm, promote or reject a candidate) and a written executive briefing.

It is configurable in three places, and all three layer:

| Layer | File | What you control |
| --- | --- | --- |
| Profile | `profiles/*.json` or `profile.json` | The domain: categories, keywords, prompt framing, trusted sources, plus its own topics and targets |
| Topics | `topics.txt` | The subjects you follow, with an interest level and a group |
| Targets | `watchlist.txt` | The names you never want to miss, pinned to a category and status |

Ten profiles ship, including the default (regulatory and legal action in telecoms and ISPs),
cybersecurity, energy, finance, health, consumer products, transport, platforms and general
news. Switching one changes the whole engine, not just a filter.

---

## 1. First run (about two minutes)

### Linux desktop

```bash
cd hb-news-scan
./start.sh
```

`start.sh` checks for Python and Tkinter, creates `.venv` if needed, installs
`requirements.txt`, seeds any missing data file from its `.example` twin, and launches the GUI.

### Any platform, command line

```bash
python3 -m pip install -r requirements.txt
python3 hbnscan.py            # one scan, writes latest_report.md
python3 -m hbnscan_gui.py     # optional desktop UI
```

On first start these files are created if absent:

| File | Purpose |
| --- | --- |
| `.env` | API key, mail settings (from `.env.example`) |
| `feeds.txt` | feed URLs, one per line (from `feeds.txt.example`) |
| `watchlist.txt` | your named priorities (from `watchlist.txt.example`) |
| `watchdog_state.db` | SQLite database, created empty and migrated |
| `settings.json` | saved GUI/settings state |

Nothing is overwritten — an existing file always wins over its example.

### Verify the install

```bash
python3 hbnscan.py --check      # validates the LLM connection, if a key is set
```

`--check` exits 0 with the recognised provider, or explains the failure (401 key rejected,
402 insufficient balance, 429 rate limited, DNS, timeout). Its first line names the active
profile and how many categories, topics and target rules it carries, so it doubles as a
configuration check. Every scan prints the same line at the top of its log.

---

## 2. Run a scan

Three ways, same pipeline:

- **GUI** — press **RUN WATCHDOG SCAN**. Progress and a live log appear; **■ Stop** cancels.
- **One shot** — `python3 hbnscan.py`.
- **Continuous** — `python3 hbnscan.py --daemon`, or tick **Auto-Scan** in the GUI.

A scan fetches feeds, skips items already seen, filters by your date window, scores new
items, deep-reads the best candidates, writes `latest_report.md`, refreshes the briefing and
optionally sends notifications.

---

## 3. Choose what to watch

Edit `feeds.txt` (GUI: **Settings → Save Feeds**, or any text editor). One URL per line,
`#` starts a comment.

```
https://www.example-regulator.gov/news-events/rss-releases
https://www.example-publisher.com/feed/
# no feed? a search query works instead
https://news.google.com/rss/search?q=%22class+action%22+telecom&hl=en-US&gl=US&ceid=US:en
```

**Google News search feeds** are the fastest way to cover anything without a native RSS
feed: the `q=` parameter accepts full search syntax (`"exact phrase"`, `OR`, `site:`, `when:7d`).
HB Briefing now resolves the redirect on those links, so items from Google feeds get deep
reads like any other source.

What makes a good source:

- **Primary publishers** — the body, court, company or publication that issues the news itself.
  Their own feed is always preferred: it carries full summaries and stable identities.
- **Aggregators and queries** — a Google News search when no feed exists.
- **Specialist press** — trade publications and law-firm blogs that follow your subject closely.
- **Company news** — only if the vendor publishes RSS; otherwise use a `site:` query.

Some publishers refuse automated clients with `HTTP 403` and cannot be fetched at all; the scan
names the host once and moves on, and a `site:` query still gets their headlines.

Feeds that return HTML instead of XML will be reported as unusable; feeds using DTDs are
rejected by design.

For a single subject you do not need a feed at all: add it as a topic (§9) and HB Briefing
builds the Google News query for you.

---

## 4. Put your own priorities first

`watchlist.txt` promotes items **above** everything else, and the model cannot override it.
Format, one rule per line:

```
phrase [& phrase ...] | category | status [| note]
```

- `&` means **all** phrases must appear (logical AND).
- `category` must be one of the active profile's categories. The default profile offers
  `regulatory_action`, `litigation`, `billing_dispute`, `equipment_scam`, `deceptive_fees`,
  `data_breach`, `security`, `policy` and `other`; `python3 hbnscan.py --check` lists what is
  in force, and the GUI shows the same list in the category filter.
- `status` is `high`, `medium` or `low`.
- The optional note appears in the log; keep personal identifiers in your own untracked copy.

Example:

```
acme fiber | litigation | high | named defendant
acme fiber & outage | billing_dispute | medium
acme capital & acquisition | regulatory_action | medium
state regulator & forfeiture | regulatory_action | high | forfeiture orders only
```

A watchlist hit sets the category and status directly: it is never downgraded, and a deep
read that disagrees is logged rather than applied. Items are marked `[Watchlist]` in the
summary and sort to the top. The GUI also offers **Watchlist** as a filter group.

---

## 5. Turn on AI (optional)

1. **Settings → LLM / AI PROVIDER**.
2. Pick a preset — the base URL and model fill in automatically.
3. Paste the key, press **Test**, then **Save**.

| Preset | Base URL | Model |
| --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/v1/chat/completions` | `deepseek-chat` |
| OpenAI | `https://api.openai.com/v1/chat/completions` | `gpt-4o-mini` |
| Ollama / Local | `http://localhost:11434/v1/chat/completions` | `llama3.1` |
| Custom | your endpoint | your model |

Equivalent by hand in `.env`:

```ini
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1/chat/completions
```

`DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` and `DEEPSEEK_BASE_URL` still work as fallbacks, so
older `.env` files need no edit. Any OpenAI-compatible chat-completions endpoint is accepted.

**Budget control** (`.env`): `WATCHDOG_MAX_TRIAGE` caps screening calls per scan (default 40,
≈1 US cent), `WATCHDOG_MAX_DEEP_READS` caps article fetches (default 8), `WATCHDOG_DEEP_READ=0`
or `WATCHDOG_BRIEFING=0` disables a stage entirely. With no key at all, everything still
runs offline.

---

## 6. Schedule scanning

| Goal | Do this |
| --- | --- |
| Scan on a timer while the GUI is open | **Settings → Scanning → Scan automatically** + interval |
| Scan only outside working hours | Set **Quiet window** start/end (wraps midnight) |
| Run headless forever | `python3 hbnscan.py --daemon` |
| Run headless via systemd/user session | Add a `ExecStart` line pointing at `--daemon` |
| Scan once, from cron | `*/30 * * * * cd /path/to/hb-news-scan && python3 hbnscan.py` |

Intervals are 5–1440 minutes. Only one scan runs at a time: a file lock prevents overlap, and
the GUI refuses a second instance.

---

## 7. Get notified

**Settings → NOTIFICATIONS** enables alerts; the transport itself lives in `.env`:

```ini
SMTP_HOST=smtp.fastmail.com
SMTP_PORT=587
SMTP_SECURITY=starttls     # starttls | ssl | none
SMTP_USER=you@example.com
SMTP_PASSWORD=app-password
SMTP_FROM=you@example.com
NOTIFY_EMAIL_TO=you@example.com
NOTIFY_SMS_TO=+15551234567
SMS_GATEWAY_DOMAIN=vtext.com
```

Text messages use free carrier e-mail-to-SMS gateways — no Twilio account, no per-message
cost. Give a bare number and use `SMS_GATEWAY_DOMAIN`, or supply a full address yourself.

| Carrier | `SMS_GATEWAY_DOMAIN` |
| --- | --- |
| Verizon | `vtext.com` |
| AT&T | `txt.att.net` |
| T-Mobile | `tmomail.net` |
| Sprint | `messaging.sprintpcs.com` |
| US Cellular | `email.uscc.net` |
| Google Fi | `msg.fi.google.com` |
| Cricket | `sms.cricketwireless.net` |
| Metro | `mymetropcs.com` |

Behaviour: only **new** items since the last notification are sent, filtered by the
**At least** threshold (high, or high + medium), declared-interest hits (watchlist rules and
topics) included by default, capped by **Max items**. Set `notify_include_watchlist` to false in
`settings.json` to drop both from the digest. Press **Send test** to prove the transport works.
Failures are logged and never abort a scan.

---

## 8. Export data

**Export** in the GUI, or:

```bash
python3 hbnscan.py --export briefing.html
python3 hbnscan.py --export report.docx
python3 hbnscan.py --export data.csv --format csv
```

| Extension | Contents |
| --- | --- |
| `.md`, `.markdown` | Full report: briefing, summary, ranked signals with assessments |
| `.html`, `.htm` | Same content, styled and self-contained — mail it or archive it |
| `.docx` | Word document built as OOXML; opens in Word/LibreOffice/Google Docs |
| `.csv` | One row per signal, flat columns for spreadsheets |
| `.json` | Structured rows plus briefing metadata, for scripts |
| `.txt` | Plain text, no markup |

Every export includes the stored AI analysis (assessment, next step, key facts, entities,
confidence) alongside the deterministic fields. Generate one before a meeting and the
assessment text is already written.

---

## 9. Add a topic (the fast way)

A **topic** is a subject that is interesting on its own, no enforcement verb needed. Press
**🎯 Topics** in the toolbar, or edit `topics.txt`:

```
municipal broadband | high | Broadband
pole attachment | low
```

Format: `subject [| interest] [| group]`. `## Heading` starts a group. A matching item is
flagged `[Topic]`, is ranked with your watchlist hits, and cannot be dismissed by a deep read.
Tick **Search Google News for each active topic** and the first 20 topics also become query
feeds, so new subjects start producing items on the next scan (off by default).

**Insert Topic Library** in the editor appends a ready-made catalogue — 20 groups, roughly 210
subjects across telecom, energy, health, finance, transport, platforms, AI, consumer products
and more — all commented out for you to pick from.

Full syntax, ranking rules and limits: [User Manual §12.5](./USER-MANUAL.md#125-topics-the-subjects-inside-a-profile).

---

## 10. Switch profile (the deep way)

A **profile** is the whole domain in one file: categories, keyword signals, prompt framing and
trusted sources, *plus* its own topic set and standing targets. Ten ship in `profiles/`, each a
complete working example you can copy and edit:

| Profile | Focus |
| --- | --- |
| *(built-in)* | regulatory and legal action in telecoms and ISPs — the shipped default |
| `regulatory.json` | the same default, as a file to copy and edit |
| `cybersecurity.json` | vulnerabilities and exploitation |
| `ai_and_silicon.json` | AI and chip regulation, export controls, antitrust |
| `energy_utilities.json` | rate cases, grid access, reliability, wildfire liability |
| `financial_services.json` | banking, lending and payment enforcement |
| `healthcare_pharma.json` | approvals, pricing, recalls, product liability |
| `consumer_products.json` | recalls, junk fees, consumer class actions |
| `transport_automotive.json` | recalls, airline consumer rules, rail and trucking |
| `platforms_media.json` | platform antitrust, privacy, content rules |
| `general_news.json` | policy, litigation and business across beats |

Pick one in **Settings → Profile** and press **Save** — it applies immediately. Or by file:

```bash
# try a profile without touching your defaults
WATCHDOG_PROFILE=cybersecurity python3 hbnscan.py
```

```ini
# or make it permanent in .env
WATCHDOG_PROFILE=cybersecurity
```

Precedence: `WATCHDOG_PROFILE` → the `profile` key in `settings.json` → `profile.json` → the
built-in default. An unknown name falls back to the default rather than failing.

Your own `topics.txt` and `watchlist.txt` layer on top of whichever profile is active, so a
local edit is never lost by switching. To write a profile, copy the closest one from
`profiles/` to `profile.json` and edit — the field-by-field schema, the scoring order and the
targets syntax are in
[User Manual §12](./USER-MANUAL.md#12-profiles-and-topics-retargeting-the-engine).

---

## 11. Cheat sheet

| I want to… | Do this |
| --- | --- |
| See only high-severity signals | Severity filter → **high** |
| Find an item from last month | Type in the search box (`Ctrl+F`), searches the whole database |
| Know what is new since the last scan | Rows carry **NEW** markers; the report has a "new since" section |
| Open a source article | Double-click the row |
| Remove a false positive | Select row, press `Delete`, or right-click → **Dismiss** |
| See the model's reasoning | Select a row — the detail pane shows assessment, facts, confidence |
| Re-sort | Click a column heading, or pick an order in the Rows bar |
| Limit table size | **Rows:** box (50–5000) |
| Read the narrative summary | **AI Briefing** tab |
| Change colours or text size | **Settings → Appearance** (dark/light theme, 80–150 % text) |
| Resize panes | Drag the horizontal divider; size and position are remembered |
| Check what a scan cost | Settings/log: calls, prompt and completion tokens per run |
| Repair old classifications | `python3 hbnscan.py --reclassify --dry-run` then without `--dry-run` |
| Follow a new subject | **🎯 Topics** → add a line (§9) |
| Change the subject area entirely | **Settings → Profile** → pick → **Save** (§10) |
| See which profile and topics are live | `python3 hbnscan.py --check` |

---

## 12. Command-line reference

| Command | Effect |
| --- | --- |
| `python3 hbnscan.py` | One scan |
| `python3 hbnscan.py --check` | Validate the LLM connection and print a sample verdict |
| `python3 hbnscan.py --daemon` (or `--watch`) | Loop scans per `settings.json` schedule; `Ctrl+C` stops |
| `python3 hbnscan.py --export FILE` | Export signals; format inferred from extension |
| `python3 hbnscan.py --export FILE --format csv` | Force a format; canonical names (`markdown`, `text`, `html`, `csv`, `json`, `docx`) and suffix aliases (`md`, `txt`) both work |
| `python3 hbnscan.py --reclassify` | Re-derive category/severity for offline rows, keep flags |
| `python3 hbnscan.py --reclassify --dry-run` | Show what reclassification would change |
| `python3 -m hbnscan_gui` | Start the desktop interface |

Exit codes: `0` success, `1` AI/connection failure, `2` bad arguments.

---

## 13. Troubleshooting essentials

| Symptom | Cause and fix |
| --- | --- |
| "No new items" every scan | Feeds are cached by ETag; Google feeds always refetch. Run `--reclassify --dry-run` to confirm the DB is intact |
| Nothing is flagged | Feed date window excludes everything, or the profile's categories do not match your sources — check the log lines per item |
| `--check` says *key rejected* | Wrong key, or a key for a different provider than the base URL |
| `--check` says *insufficient balance* | Provider account has no credit; the offline heuristic still works |
| Deep reads never happen | Aggregator link could not be resolved, article returned <200 characters, the publisher blocks automated fetches (HTTP 401/403 — logged once per host per scan), or budget `WATCHDOG_MAX_DEEP_READS` reached |
| A feed logs `HTTP 403` | The publisher refuses automated clients (Cloudflare and similar). Replace the feed with a Google News `site:<domain>` query so items are still ingested; the hint in the log says the same |
| Report looks stale | The report is rewritten at the end of every scan, so a stale one means the last scan did not finish — read the log for the failing feed or the cancelled run |
| Profile switched but old rows still show the previous profile's categories | Stored verdicts are not rewritten on a profile change; run `--reclassify` (or leave them — new scans use the new profile) |
| Category filter reset itself to `all` | The saved category belonged to the previous profile; the filter only offers categories the active profile defines |
| A topic never matches | Check the scan log for `[!] topics.txt:` warnings; a bad interest level or an over-long label is skipped, and matching is word-start only |
| "Feed unusable (HTML)" | That URL serves a web page, not RSS — find the real feed URL or use a Google News query |
| Email never arrives | `SMTP_HOST` blank, or `notify_enabled` off; use **Send test** and read the log |
| SMS never arrives | Carrier gateway blocked, number not in `NOTIFY_SMS_TO`, or the gateway domain does not match the carrier |
| GUI will not start a second time | Single-instance lock — close the existing window, or remove `.hb-briefing-gui.lock` if it is stale |
| `start.sh` fails on tkinter | Install `python3-tk` (Debian/Ubuntu) or `python3-tkinter` (Fedora); `start.sh` tries this for you |

Deeper diagnostics: [User Manual §16](./USER-MANUAL.md#16-troubleshooting-and-faq) and
[Technical Reference §17](./TECHNICAL-REFERENCE.md#17-operational-runbook).
