# HB Briefing, in a browser tab

This folder holds one HTML page and nothing else. `python3 packaging/build.py wasm` copies it
next to the engine's modules (`hbnscan.py`, `hbsimilar.py`, `hbversion.py`, `hbwasm.py`) and
writes `runtime.json`, which tells the page what to load.

The page downloads CPython compiled to WebAssembly ([Pyodide](https://pyodide.org)) once, then
runs **the same** `heuristic_verdict()`, `story_keys()` and MinHash pass as the desktop app. No
verdict logic is reimplemented in JavaScript, so a score shown here is the score the desktop app
would produce from the same text.

## Run it

```bash
python3 packaging/build.py wasm
cd dist/hb-briefing-1.0.0-wasm
python3 -m http.server 8765
# open http://127.0.0.1:8765/
```

A local server is required: a `file://` page cannot fetch the modules, and the Pyodide runtime
comes from a CDN, so the first load needs a network connection. After that the page works
offline for pasted or dropped files.

## What it accepts

| Input | How |
| --- | --- |
| JSON or CSV export from the desktop app | drop the file; verdicts are kept as exported |
| RSS or Atom feed | drop the `.xml`/`.rss` file, or paste its XML |
| OPML feed list | drop it, then *Fetch these feeds* (subject to the publisher's CORS policy) |
| Plain headlines | paste one per line and press *Analyse* |

## What it does not do

- **No database.** There is no SQLite file in the tab: the page analyses what you give it and
  forgets it on reload.
- **No AI stages.** Deep reads and briefings need an API key and a long-lived process; this page
  runs the offline heuristic only, which is what a keyless desktop install uses too.
- **No feed polling.** A browser cannot bypass CORS, so most publishers cannot be read directly.
  Use `hb-briefing-web` on a machine for that.

## Why the runtime is a CDN download

Bundling CPython would mean shipping ~10 MB of `.wasm` in the repository. Fetching it keeps the
checkout small and picks up Pyodide's security fixes. To pin or mirror it, change `indexURL` in
`wasm/index.html`.
