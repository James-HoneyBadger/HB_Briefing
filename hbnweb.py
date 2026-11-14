#!/usr/bin/env python3
"""
HB Briefing v1.0.0 - Web front end

A local web application over the same engine the desktop GUI uses: one page, one JSON API, no
third-party packages and no build step. It serves the signal grid with filters and search, the
detail pane, the briefing, scan control with a live log, dismissal, settings and exports.

Run it with:

    python3 hbnscan.py --serve                 # http://127.0.0.1:8765/
    python3 hbnweb.py --port 9000 --host 0.0.0.0 --token secret

Security model: the server binds to loopback, rejects requests whose Host header is not the
address it bound, and requires the per-run token on every mutating request. That combination
stops a page on another origin from driving the API (CSRF) and stops DNS-rebinding tricks. It is
a single-user tool: there are no accounts, and nothing is exposed beyond the signal database.
"""
import argparse
import collections
from contextlib import closing
import ipaddress
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse, urlsplit

import hbnscan

MAX_BODY = 64 * 1024
LOG_LINES = 400
# The engine owns the column set and the read query, so the API cannot drift from the GUI.
SIGNAL_FIELDS = hbnscan.SIGNAL_COLUMNS

# Scan state shared by the worker thread and the request threads.
SCAN = {
    "running": False,
    "started": None,
    "finished": None,
    "new": 0,
    "flagged": 0,
    "deep": 0,
    "error": None,
    "cancel": None,
    "log": collections.deque(maxlen=LOG_LINES),
}
SCAN_LOCK = threading.Lock()


def _log(line):
    text = " ".join(str(line).split())
    if text:
        with SCAN_LOCK:
            SCAN["log"].append(text)


# --------------------------------------------------------------------------- data access


def _connect():
    return hbnscan.connect(timeout=10)


def _decodable(value, fallback):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _signal_payload(row):
    """One row, JSON-safe, with provenance and freshness separated from the raw text."""
    return {
        "hash": row["item_hash"],
        "title": row["title"] or "",
        "link": hbnscan.safe_link(row["link"]),
        "severity": (row["severity"] or "none").lower(),
        "category": row["category"] or "other",
        "priority": hbnscan.priority_of(row["source_layer"], row["category"], row["summary"]),
        "layer": hbnscan.layer_of(row["source_layer"], row["summary"]),
        "assessment": hbnscan.strip_provenance(row["summary"]),
        "action": row["action"] or "",
        "facts": [str(f) for f in _decodable(row["facts"], []) if str(f).strip()],
        "entities": {str(k): [str(v) for v in vals]
                     for k, vals in _decodable(row["entities"], {}).items()
                     if isinstance(vals, list)},
        "confidence": "" if row["confidence"] is None else f"{float(row['confidence']):.2f}",
        "verification": "deep read" if row["deep_read"] else "screened only",
        "published": (row["published"] or "")[:10],
        "freshness": hbnscan.freshness_label(row["published"], row["seen_at"]),
        "detected": row["seen_at"] or "",
        "source": urlparse(row["feed_url"] or "").hostname or "",
        "cluster": row["cluster_key"] or "",
    }


def _int_param(query, name, default):
    """Read one integer query parameter, refusing anything that is not a number."""
    raw = (query.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be a whole number, not {raw!r}") from None


def list_signals(filters):
    """One filtered page of signals plus the unpaged total for the same filter."""
    with closing(_connect()) as conn:
        rows, total = hbnscan.select_signals(
            conn, filters, order=filters.get("order"),
            limit=filters.get("limit"), offset=filters.get("offset") or 0)
    return [_signal_payload(row) for row in rows], total


def cached_resolution(url):
    """Publisher URL for an aggregator link, using only the engine's resolution cache."""
    if not hbnscan.is_aggregator_link(url):
        return ""
    article_id = hbnscan._google_article_id(url)
    if not article_id:
        return ""
    return hbnscan.aggregator_cache().get("gnurl:" + article_id, "")


def one_signal(item_hash):
    columns = ", ".join(SIGNAL_FIELDS)
    with closing(_connect()) as conn:
        row = conn.execute(
            f"SELECT {columns} FROM seen_items WHERE item_hash = ?", (item_hash,)).fetchone()
    if not row:
        return None
    payload = _signal_payload(row)
    payload["display_link"] = cached_resolution(payload["link"]) or payload["link"]
    try:
        with closing(_connect()) as conn:
            payload["story"] = hbnscan.cluster_summary(conn, payload["cluster"])
    except sqlite3.Error:
        payload["story"] = None
    return payload


def state_payload():
    settings = hbnscan.load_settings()
    with closing(_connect()) as conn:
        counts = dict(conn.execute(
            f"SELECT severity, COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
            "GROUP BY severity"))
        total = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL}").fetchone()[0]
        core = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
            f"AND {hbnscan._core_category_sql()}").fetchone()[0]
        watch = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
            f"AND {hbnscan._layer_sql()} = 'watchlist'").fetchone()[0]
        topic = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
            f"AND {hbnscan._layer_sql()} = 'topic'").fetchone()[0]
        undated = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
            "AND COALESCE(published, '') = ''").fetchone()[0]
        last_scan = conn.execute(
            "SELECT value FROM meta WHERE key = 'last_scan_at'").fetchone()
        dismissed = conn.execute(
            "SELECT COUNT(*) FROM seen_items WHERE dismissed = 1").fetchone()[0]
    profile_source = hbnscan.active_profile_path()
    with SCAN_LOCK:
        scan_state = {k: SCAN[k] for k in ("running", "started", "finished", "new",
                                           "flagged", "deep", "error")}
        log_lines = list(SCAN["log"])
    return {
        "product": f"{hbnscan.APP_NAME} v1.0.0",
        "profile": {
            "name": hbnscan.APP_NAME,
            "file": profile_source.name if profile_source.exists() else "built-in default",
            "categories": list(hbnscan.CATEGORIES),
            "core": list(hbnscan.CORE_CATEGORIES),
            "topics": len(hbnscan.load_topics()),
            "targets": len(hbnscan.load_watchlist()),
        },
        "counts": {
            "flagged": total,
            "high": counts.get("high", 0),
            "medium": counts.get("medium", 0),
            "low": counts.get("low", 0),
            "core": core,
            "watchlist": watch,
            "topic": topic,
            "undated": undated,
            "dismissed": dismissed,
            "feeds": len(hbnscan.load_feeds()),
        },
        "ai": {
            "model": hbnscan.LLM_MODEL if hbnscan.deepseek_configured() else None,
            "endpoint": hbnscan.LLM_BASE_URL if hbnscan.deepseek_configured() else None,
        },
        "settings": {
            "feed_since": settings["feed_since"],
            "feed_until": settings["feed_until"],
            "profile": settings["profile"],
            "row_order": settings["row_order"],
            "filter_severity": settings["filter_severity"],
            "filter_category": settings["filter_category"],
            "filter_group": settings["filter_group"],
            "filter_recent": settings["filter_recent"],
            "table_limit": settings["table_limit"],
            "auto_scan": settings["auto_scan"],
            "scan_interval_minutes": settings["scan_interval_minutes"],
            "scan_window_start": settings["scan_window_start"],
            "scan_window_end": settings["scan_window_end"],
            "schedule_label": hbnscan.describe_schedule(settings),
            "theme": settings["theme"],
        },
        "last_scan": last_scan[0] if last_scan else None,
        "scan": scan_state,
        "log": log_lines[-80:],
    }


def briefing_payload():
    with closing(_connect()) as conn:
        content, created = hbnscan.latest_briefing(conn)
    return {"content": content or "", "created_at": created or ""}


SCHEDULE_KEYS = ("auto_scan", "scan_interval_minutes", "scan_window_start",
                 "scan_window_end")


def settings_payload():
    settings = hbnscan.load_settings()
    payload = {key: settings[key] for key in (
        "profile", "feed_since", "feed_until", "table_limit", "row_order",
        "filter_severity", "filter_category", "filter_group", "filter_recent",
        "topics_as_feeds", "theme") + SCHEDULE_KEYS}
    payload["schedule_label"] = hbnscan.describe_schedule(settings)
    return payload


EDITABLE_SETTINGS = ("profile", "feed_since", "feed_until", "table_limit", "row_order",
                     "filter_severity", "filter_category", "filter_group", "filter_recent",
                     "topics_as_feeds", "theme", "auto_scan", "scan_interval_minutes",
                     "scan_window_start", "scan_window_end")


def update_settings(values):
    """Persist the web-editable subset, then rebind the profile if it changed."""
    if not isinstance(values, dict):
        raise ValueError("settings body must be a JSON object")
    accepted = {k: v for k, v in values.items() if k in EDITABLE_SETTINGS}
    if not accepted:
        raise ValueError("no editable setting supplied")
    before = hbnscan.load_settings()["profile"]
    saved = hbnscan.save_settings(**accepted)
    if saved["profile"] != before:
        hbnscan.apply_profile()
    return settings_payload()


# --------------------------------------------------------------------------- scan control


def start_scan():
    """Run one scan in a worker thread. Returns False when a scan is already running."""
    with SCAN_LOCK:
        if SCAN["running"]:
            return False
        SCAN.update(running=True, started=time.strftime("%Y-%m-%d %H:%M:%S"),
                    finished=None, new=0, flagged=0, deep=0, error=None,
                    cancel=threading.Event(), log=collections.deque(maxlen=LOG_LINES))
        cancel = SCAN["cancel"]

    def worker():
        try:
            new_count, flagged = hbnscan.run_scan(log_callback=_log, cancel=cancel)
            with SCAN_LOCK:
                SCAN.update(new=new_count, flagged=flagged)
        except Exception as exc:                      # a scan must never kill the server
            _log(f"[ERROR] Scan failed: {type(exc).__name__}: {exc}")
            with SCAN_LOCK:
                SCAN["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with SCAN_LOCK:
                SCAN.update(running=False, finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                            cancel=None)

    threading.Thread(target=worker, daemon=True, name="hb-scan").start()
    return True


SCHEDULER = {"last_started": None}


def scheduler_step(now=None):
    """Start a scan when the shared schedule says one is due. Returns True when started."""
    settings = hbnscan.load_settings()
    with SCAN_LOCK:
        running = SCAN["running"]
    if running:
        return False
    delay = hbnscan.next_scan_delay(settings, SCHEDULER["last_started"], now)
    if delay is None or delay > 0:
        return False
    started = start_scan()
    if started:
        # Record the slot only when a scan really began: a manual scan holding the engine
        # lock must not silently consume the next automatic one.
        SCHEDULER["last_started"] = now or hbnscan.datetime.now()
        _log(f"[AUTO] Scheduled scan ({hbnscan.describe_schedule(settings)}).")
    return started


def _scheduler_loop(stop_event, tick=5.0):
    """Poll the schedule. Cheap: settings are read from a small file, nothing is fetched."""
    while not stop_event.is_set():
        try:
            scheduler_step()
        except Exception as exc:                              # noqa: BLE001
            _log(f"[ERROR] Scheduler: {type(exc).__name__}: {exc}")
        stop_event.wait(tick)


def cancel_scan():
    with SCAN_LOCK:
        cancel = SCAN.get("cancel")
    if cancel is None:
        return False
    cancel.set()
    _log("[CANCEL] Cancellation requested.")
    return True


# --------------------------------------------------------------------------- export


def export_document(fmt):
    """Render the current signal set to (bytes, filename, content type)."""
    fmt = (fmt or "md").lower()
    filters = {"limit": 10000}
    with closing(_connect()) as conn:
        rows = hbnscan.export_rows(conn, filters)
        briefing = hbnscan.latest_briefing(conn)
    stamp = time.strftime("%Y%m%d-%H%M")
    title = f"{hbnscan.APP_NAME} - {len(rows)} signals"
    if fmt in ("md", "markdown"):
        body = hbnscan.render_markdown(rows, title=title, briefing=briefing)
        return body.encode("utf-8"), f"hb-briefing-{stamp}.md", "text/markdown; charset=utf-8"
    if fmt == "html":
        body = hbnscan.render_html(rows, title=title, briefing=briefing)
        return body.encode("utf-8"), f"hb-briefing-{stamp}.html", "text/html; charset=utf-8"
    if fmt == "csv":
        body = hbnscan.render_csv(rows)
        return body.encode("utf-8"), f"hb-briefing-{stamp}.csv", "text/csv; charset=utf-8"
    if fmt == "json":
        body = hbnscan.render_json(rows, title=title, briefing=briefing)
        return body.encode("utf-8"), f"hb-briefing-{stamp}.json", "application/json"
    if fmt == "txt":
        body = hbnscan.render_text(rows, title=title, briefing=briefing)
        return body.encode("utf-8"), f"hb-briefing-{stamp}.txt", "text/plain; charset=utf-8"
    if fmt == "docx":
        handle, path = tempfile.mkstemp(suffix=".docx")
        os.close(handle)
        try:
            hbnscan.render_docx(rows, path, title=title, briefing=briefing)
            data = Path(path).read_bytes()
        finally:
            os.unlink(path)
        return data, f"hb-briefing-{stamp}.docx", (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    raise ValueError(f"unsupported format: {fmt}")


# --------------------------------------------------------------------------- HTTP layer


class Handler(BaseHTTPRequestHandler):
    server_version = "HBBriefing/1.0"
    token = ""
    allowed_hosts = set()
    quiet = False

    # -- helpers -----------------------------------------------------------------
    def log_message(self, fmt, *args):
        # Request logs go to the console, never into the scan log the page displays.
        if not self.quiet:
            sys.stderr.write(f"[WEB] {self.address_string()} {fmt % args}\n")

    def _host_ok(self):
        """Reject Host headers we did not bind: this is the DNS-rebinding guard."""
        try:
            parsed = urlsplit("//" + (self.headers.get("Host") or ""))
            host = parsed.hostname
            parsed.port
        except ValueError:
            return False
        if not host or parsed.path or parsed.query or parsed.fragment:
            return False
        host = host.lower()
        if host in self.allowed_hosts:
            return True
        if self.allow_ip_hosts:
            try:
                ipaddress.ip_address(host)
                return True
            except ValueError:
                pass
        return False

    def _token_ok(self):
        supplied = self.headers.get("X-HB-Token", "") or parse_qs(
            urlparse(self.path).query).get("token", [""])[0]
        return bool(self.token) and secrets.compare_digest(supplied, self.token)

    def _send(self, status, body, content_type="application/json", headers=None):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ValueError("request body too large")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    # -- verbs -------------------------------------------------------------------
    def do_GET(self):                                        # noqa: N802 (http.server API)
        if not self._host_ok():
            return self._error(421, "unexpected Host header")
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        route = parsed.path
        try:
            if route in ("/", "/index.html"):
                page = PAGE.replace("__TOKEN__", self.token)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if route == "/api/state":
                return self._send(200, state_payload())
            if route == "/api/signals":
                filters = {
                    "severity": query.get("severity", "all"),
                    "category": query.get("category", "all"),
                    "group": query.get("group", "all"),
                    "query": query.get("query", ""),
                    "order": query.get("order", "relevance"),
                    "recent_days": query.get("recent", "all"),
                    "limit": _int_param(query, "limit", 200),
                    "offset": _int_param(query, "offset", 0),
                }
                rows, total = list_signals(filters)
                return self._send(200, {"rows": rows, "total": total,
                                        "limit": filters["limit"],
                                        "offset": filters["offset"]})
            if route.startswith("/api/signal/"):
                found = one_signal(route.rsplit("/", 1)[-1])
                return self._send(200, found) if found else self._error(404, "no such signal")
            if route == "/api/briefing":
                return self._send(200, briefing_payload())
            if route == "/api/settings":
                return self._send(200, settings_payload())
            if route == "/api/export":
                data, filename, ctype = export_document(query.get("fmt", "md"))
                return self._send(200, data, ctype,
                                  {"Content-Disposition": f'attachment; filename="{filename}"'})
            if route == "/api/log":
                with SCAN_LOCK:
                    return self._send(200, {"log": list(SCAN["log"])})
            return self._error(404, "not found")
        except ValueError as exc:
            return self._error(400, str(exc))
        except sqlite3.Error as exc:
            return self._error(500, f"database error: {exc}")
        except Exception as exc:                              # noqa: BLE001 - report, never die
            return self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self):                                       # noqa: N802
        if not self._host_ok():
            return self._error(421, "unexpected Host header")
        if not self._token_ok():
            return self._error(403, "missing or invalid token")
        route = urlparse(self.path).path
        try:
            body = self._body()
            if route == "/api/scan":
                started = start_scan()
                return self._send(202 if started else 409,
                                  {"started": started, "running": True})
            if route == "/api/scan/cancel":
                return self._send(200, {"cancelling": cancel_scan()})
            if route == "/api/dismiss":
                item_hash = str(body.get("hash") or "")
                if not item_hash:
                    return self._error(400, "hash is required")
                with closing(_connect()) as conn:
                    state = hbnscan.dismiss_item(conn, item_hash, undo=bool(body.get("undo")))
                return self._send(200, {"state": state}) if state else self._error(
                    404, "no such signal")
            if route == "/api/settings":
                return self._send(200, update_settings(body))
            return self._error(404, "not found")
        except ValueError as exc:
            return self._error(400, str(exc))
        except sqlite3.Error as exc:
            return self._error(500, f"database error: {exc}")
        except Exception as exc:                              # noqa: BLE001
            return self._error(500, f"{type(exc).__name__}: {exc}")


# --------------------------------------------------------------------------- page
# One file, no external requests: system fonts, inline CSS and vanilla JS. Every value that
# comes from a feed is written with textContent, never innerHTML, so a hostile headline cannot
# inject markup.

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HB Briefing</title>
<style>
  :root, html[data-theme="dark"] {
    --bg:#0f131c; --panel:#171d29; --line:#253044; --line-soft:#1b2331; --hover:#141b27;
    --bg-input:#0c111a; --ink:#e6edf7; --muted:#94a3b8;
    --amber:#f5b642; --green:#3fb950; --red:#e5534b; --blue:#58a6ff; --violet:#a371f7;
  }
  html[data-theme="light"] {
    --bg:#eef2f6; --panel:#ffffff; --line:#c7d3e0; --line-soft:#e3e9f0; --hover:#e6edf5;
    --bg-input:#f8fafc; --ink:#0f172a; --muted:#475569;
    --amber:#b45309; --green:#047857; --red:#b91c1c; --blue:#1d4ed8; --violet:#6d28d9;
  }
  html[data-theme="midnight"] {
    --bg:#0a0e17; --panel:#111827; --line:#1c2740; --line-soft:#151d2e; --hover:#131c2c;
    --bg-input:#070a11; --ink:#e5ecf8; --muted:#7b8aa8;
    --amber:#60a5fa; --green:#22c55e; --red:#f87171; --blue:#93c5fd; --violet:#c4b5fd;
  }
  html[data-theme="solarized"] {
    --bg:#fdf6e3; --panel:#eee8d5; --line:#d8d2bd; --line-soft:#e7e0cc; --hover:#e9e2cd;
    --bg-input:#fffbf0; --ink:#073642; --muted:#657b83;
    --amber:#b58900; --green:#859900; --red:#dc322f; --blue:#268bd2; --violet:#6c71c4;
  }
  html[data-theme="contrast"] {
    --bg:#000000; --panel:#0b0b0b; --line:#5a5a5a; --line-soft:#2e2e2e; --hover:#1a1a1a;
    --bg-input:#000000; --ink:#ffffff; --muted:#d7d7d7;
    --amber:#ffd400; --green:#00ff88; --red:#ff4d4d; --blue:#7cc4ff; --violet:#d7a6ff;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  header { display:flex; align-items:center; gap:16px; padding:12px 18px;
           background:var(--panel); border-bottom:1px solid var(--line); flex-wrap:wrap; }
  h1 { font-size:15px; letter-spacing:.08em; margin:0; color:var(--amber); }
  .sub { color:var(--muted); font-size:12px; }
  .spacer { flex:1; }
  .schedbar { display:flex; gap:14px; align-items:center; flex-wrap:wrap;
              padding:8px 18px; background:var(--panel); border-bottom:1px solid var(--line); }
  .schedbar label { display:flex; gap:6px; align-items:center; }
  .schedbar input[type=text] { background:var(--bg-input); color:var(--ink); border:1px solid var(--line);
              border-radius:6px; padding:4px 6px; font-size:12px; }
  button { background:var(--panel); color:var(--ink); border:1px solid var(--line);
           border-radius:6px; padding:6px 12px; font-size:13px; cursor:pointer; }
  button:hover { border-color:var(--muted); }
  button:disabled { opacity:.45; cursor:default; }
  button.primary { background:var(--amber); color:var(--bg); border-color:transparent; font-weight:600; }
  main { display:grid; grid-template-columns:minmax(0,1fr) 380px; gap:0; height:calc(100vh - 56px); }
  .left { display:flex; flex-direction:column; min-width:0; }
  .bar { display:flex; gap:10px; align-items:center; padding:10px 14px; flex-wrap:wrap;
         border-bottom:1px solid var(--line); }
  select, input[type=search] { background:var(--bg-input); color:var(--ink); border:1px solid var(--line);
         border-radius:6px; padding:6px 8px; font-size:13px; }
  input[type=search] { flex:1 1 240px; min-width:150px; }
  .counts { color:var(--muted); font-size:12px; padding:0 14px 8px; }
  table { width:100%; border-collapse:collapse; table-layout:fixed; }
  thead th { text-align:left; font-size:11px; letter-spacing:.06em; color:var(--muted);
             text-transform:uppercase; padding:8px 10px; border-bottom:1px solid var(--line);
             position:sticky; top:0; background:var(--bg); cursor:pointer; }
  tbody tr { cursor:pointer; border-bottom:1px solid var(--line-soft); }
  tbody tr:hover { background:var(--hover); }
  tbody tr.sel { background:var(--hover); box-shadow:inset 3px 0 0 var(--amber); }
  td { padding:7px 10px; vertical-align:top; }
  /* table-layout:fixed takes its widths from the first row, so the widths belong to the
     th selectors as well as the td ones - otherwise the headline column collapses. */
  th.sev, td.sev { width:64px; font-size:11px; font-weight:700; letter-spacing:.04em; }
  th.cat, td.cat { width:124px; color:var(--muted); font-size:12px;
                   overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  th.title, td.title { width:auto; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  th.when, td.when { width:104px; color:var(--muted); font-size:12px; }
  th.fresh, td.fresh { width:64px; color:var(--muted); font-size:12px; }
  td.fresh.undated { font-style:italic; }
  .high { color:var(--red); } .medium { color:var(--amber); }
  .low { color:var(--muted); } .none { color:var(--muted); }
  .tag { display:inline-block; padding:1px 6px; border-radius:999px; font-size:10px;
         border:1px solid var(--line); color:var(--muted); margin-right:4px; }
  .tag.watchlist { color:var(--amber); }
  .tag.topic { color:var(--blue); }
  .tag.core { color:var(--violet); }
  .scroller { overflow:auto; flex:1; }
  aside { border-left:1px solid var(--line); background:var(--panel); overflow:auto;
          display:flex; flex-direction:column; }
  .detail { padding:14px 16px; }
  .detail h2 { font-size:15px; margin:0 0 8px; line-height:1.3; }
  .detail a { color:var(--blue); word-break:break-all; }
  .detail dl { display:grid; grid-template-columns:90px 1fr; gap:4px 10px; margin:10px 0; }
  .detail dt { color:var(--muted); font-size:12px; }
  .detail dd { margin:0; }
  .detail ul { margin:6px 0 0 18px; padding:0; }
  .tabs { display:flex; gap:6px; padding:10px 14px 0; }
  .tabs button { padding:5px 10px; font-size:12px; }
  .tabs button.on { background:var(--amber); color:var(--bg); border-color:transparent; }
  pre.log { margin:0; padding:10px 14px; font:11.5px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;
            color:var(--muted); white-space:pre-wrap; word-break:break-word; }
  .brief { padding:12px 16px; white-space:pre-wrap; }
  .empty { color:var(--muted); padding:24px 16px; }
  .dot { width:8px; height:8px; border-radius:50%; display:inline-block; background:var(--muted); }
  .dot.on { background:var(--green); animation:pulse 1.2s infinite; }
  @keyframes pulse { 50% { opacity:.35; } }
  @media (max-width: 760px) { main { grid-template-columns:1fr; height:auto; } aside { border-left:0; border-top:1px solid var(--line); min-height:320px; } }
</style>
</head>
<body>
<header>
  <h1>HB BRIEFING</h1>
  <span class="sub" id="profileLine">loading…</span>
  <span class="spacer"></span>
  <span class="sub"><span class="dot" id="scanDot"></span> <span id="scanState">idle</span></span>
  <button class="primary" id="scanBtn">Run scan</button>
  <button id="cancelBtn" disabled>Stop</button>
  <button id="exportBtn">Export</button>
  <button id="reloadBtn">Refresh</button>
</header>
<div class="schedbar">
  <label class="sub"><input type="checkbox" id="autoOn"> Scan on a schedule</label>
  <label class="sub">every
    <select id="intervalSel">
      <option value="5">5 min</option><option value="10">10 min</option>
      <option value="15">15 min</option><option value="30">30 min</option>
      <option value="60">60 min</option><option value="120">2 h</option>
      <option value="240">4 h</option>
    </select>
  </label>
  <label class="sub">scan only between
    <input type="text" id="scanWindow" size="12" placeholder="22:00-06:00">
  </label>
  <label class="sub">theme
    <select id="themeSel">
      <option value="dark">Dark</option><option value="light">Light</option>
      <option value="midnight">Midnight</option><option value="solarized">Solarized</option>
      <option value="contrast">High contrast</option>
    </select>
  </label>
  <span class="sub" id="scheduleLabel"></span>
  <span class="sub" id="saveState"></span>
</div>
<main>
  <section class="left">
    <div class="bar">
      <input type="search" id="q" placeholder="Search headline, assessment, category, link…">
      <select id="sev">
        <option value="all">All severities</option><option value="high">High</option>
        <option value="medium">Medium</option><option value="low">Low</option>
      </select>
      <select id="cat"><option value="all">All categories</option></select>
      <select id="grp">
        <option value="all">All priorities</option><option value="watchlist">Watchlist</option>
        <option value="topic">Topic</option><option value="core">Core</option>
        <option value="context">Context</option>
      </select>
      <select id="rec">
        <option value="all">Any date</option><option value="1d">Published today</option>
        <option value="3d">Last 3 days</option><option value="7d">Last 7 days</option>
        <option value="30d">Last 30 days</option>
      </select>
      <select id="ord">
        <option value="relevance">Relevance</option><option value="priority">Severity</option>
        <option value="newest">Newest news</option><option value="detected">Newest ingest</option>
        <option value="oldest">Oldest</option><option value="severity">Severity only</option>
        <option value="title">Title</option><option value="category">Category</option>
      </select>
      <select id="lim">
        <option>50</option><option selected>200</option><option>500</option><option>1000</option>
      </select>
    </div>
    <div class="counts" id="counts"></div>
    <div class="scroller">
      <table>
        <thead><tr><th class="sev">Sev</th><th class="cat">Category</th><th>Headline</th><th class="fresh">Fresh</th><th class="when">Detected</th></tr></thead>
        <tbody id="rows"></tbody>
      </table>
      <div class="empty" id="empty" hidden>No signals match these filters.</div>
    </div>
  </section>
  <aside>
    <div class="tabs">
      <button class="on" id="tabDetail">Signal</button>
      <button id="tabBrief">Briefing</button>
      <button id="tabLog">Log</button>
    </div>
    <div class="detail" id="paneDetail"><div class="empty">Select a row to see its assessment, facts and entities.</div></div>
    <div class="brief" id="paneBrief" hidden></div>
    <pre class="log" id="paneLog" hidden></pre>
  </aside>
</main>
<script>
const TOKEN = "__TOKEN__";
const $ = (id) => document.getElementById(id);
let state = null, selected = null, timer = null;

async function api(path, options) {
  const opts = Object.assign({headers: {}}, options || {});
  opts.headers["X-HB-Token"] = TOKEN;
  if (opts.body) opts.headers["Content-Type"] = "application/json";
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).error || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

function esc(tag, text) {
  const el = document.createElement(tag);
  el.textContent = text == null ? "" : String(text);
  return el;
}

function setText(id, text) { $(id).textContent = text; }

// Every control writes through to /api/settings the moment the user changes it, so the
// stored settings are the only source of truth and a refresh can never undo an edit.
// `pending` covers the round trip; `document.activeElement` covers a poll that lands
// while something is still being typed into.
const pending = new Set();
const busy = (id) => pending.has(id) || document.activeElement === $(id);

function selectOption(sel, value, label) {
  if (![...sel.options].some((o) => o.value === value)) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label || value;
    sel.append(opt);
  }
  sel.value = value;
}

async function persistSettings(fields, controls) {
  controls.forEach((id) => pending.add(id));
  try {
    const saved = await api("/api/settings", {method: "POST", body: JSON.stringify(fields)});
    if (state) state.settings = Object.assign({}, state.settings, saved);  // server truth
    setText("saveState", "saved " + new Date().toLocaleTimeString());
    return saved;
  } finally {
    controls.forEach((id) => pending.delete(id));
  }
}

function parseWindow(raw) {
  const parts = raw.trim().split("-").map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return ["", ""];
  if (parts.length !== 2 || !parts.every((p) => /^\d{1,2}:\d{2}$/.test(p))) return null;
  return parts;
}

function syncControls() {
  const s = (state && state.settings) || {};
  if (s.theme && !busy("themeSel")) {
    document.documentElement.dataset.theme = s.theme;
    $("themeSel").value = s.theme;
  }
  if (!busy("autoOn")) $("autoOn").checked = !!s.auto_scan;
  if (!busy("intervalSel")) {
    selectOption($("intervalSel"), String(s.scan_interval_minutes || 30), `${s.scan_interval_minutes} min`);
  }
  if (!busy("scanWindow")) {
    $("scanWindow").value = s.scan_window_start || s.scan_window_end
      ? `${s.scan_window_start || "00:00"}-${s.scan_window_end || "24:00"}` : "";
  }
  if (!busy("sev") && s.filter_severity) $("sev").value = s.filter_severity;
  if (!busy("cat") && s.filter_category) {
    const cat = $("cat");
    if ([...cat.options].some((o) => o.value === s.filter_category)) cat.value = s.filter_category;
  }
  if (!busy("grp") && s.filter_group) $("grp").value = s.filter_group;
  if (!busy("ord") && s.row_order) $("ord").value = s.row_order;
  if (!busy("rec") && s.filter_recent) $("rec").value = s.filter_recent;
  if (!busy("lim") && s.table_limit) {
    selectOption($("lim"), String(s.table_limit), String(s.table_limit));
  }
  setText("scheduleLabel", s.schedule_label || "");
}

async function saveSchedule() {
  const window = parseWindow($("scanWindow").value);
  if (!window) {
    alert("The scanning window must look like 22:00-06:00, or be blank for all day.");
    syncControls();
    return;
  }
  try {
    const saved = await persistSettings({
      auto_scan: $("autoOn").checked,
      scan_interval_minutes: $("intervalSel").value,
      scan_window_start: window[0],
      scan_window_end: window[1],
    }, ["autoOn", "intervalSel", "scanWindow"]);
    setText("scheduleLabel", saved.schedule_label);
  } catch (e) {
    alert(e.message);
  }
  syncControls();
}

async function saveFilters() {
  try {
    await persistSettings({
      filter_severity: $("sev").value,
      filter_category: $("cat").value,
      filter_group: $("grp").value,
      filter_recent: $("rec").value,
      row_order: $("ord").value,
      table_limit: $("lim").value,
    }, ["sev", "cat", "grp", "ord", "rec", "lim"]);
  } catch (e) {
    setText("saveState", "not saved: " + e.message);
  }
  syncControls();
  await loadSignals();
}

async function loadState() {
  state = await api("/api/state");
  setText("profileLine", `${state.product} · profile ${state.profile.name} `
    + `(${state.profile.file}) · ${state.profile.topics} topics · `
    + `${state.profile.targets} target rules · `
    + `${state.ai.model ? "AI " + state.ai.model : "offline heuristic"}`);
  const c = state.counts;
  setText("counts", `${c.flagged} flagged · ${c.high} high · ${c.medium} medium · ${c.low} low · `
    + `${c.core} core · ${c.watchlist} watchlist · ${c.topic} topic · ${c.feeds} feeds`);
  const cat = $("cat"), current = cat.value;
  const wanted = ["all", ...state.profile.categories];
  if (cat.options.length !== wanted.length) {
    cat.replaceChildren(...wanted.map((value) => {
      const o = document.createElement("option");
      o.value = value; o.textContent = value === "all" ? "All categories" : value;
      return o;
    }));
    cat.value = wanted.includes(current) ? current : "all";
  }
  syncControls();
  const scanning = state.scan.running;
  $("scanDot").className = "dot" + (scanning ? " on" : "");
  setText("scanState", scanning ? "scanning…" :
    (state.last_scan ? "last scan " + state.last_scan.replace("T", " ").slice(0, 16) : "idle"));
  $("scanBtn").disabled = scanning;
  $("cancelBtn").disabled = !scanning;
  if (scanning) setText("paneLog", state.log.join("\n"));
  return state;
}

function rowElement(sig) {
  const tr = document.createElement("tr");
  tr.dataset.hash = sig.hash;
  if (selected === sig.hash) tr.className = "sel";
  const sev = esc("td", sig.severity.toUpperCase());
  sev.className = "sev " + sig.severity;
  const cat = esc("td", sig.category);
  cat.className = "cat";
  const title = document.createElement("td");
  title.className = "title";
  if (sig.priority !== "context") {
    const tag = esc("span", sig.priority);
    tag.className = "tag " + sig.priority;
    title.append(tag);
  }
  title.append(esc("span", sig.title));
  const when = esc("td", (sig.detected || "").replace("T", " ").slice(0, 16));
  when.className = "when";
  const fresh = esc("td", sig.freshness || "undated");
  fresh.className = "fresh" + (sig.published ? "" : " undated");
  tr.append(sev, cat, title, fresh, when);
  tr.addEventListener("click", () => select(sig.hash));
  return tr;
}

async function loadSignals() {
  const params = new URLSearchParams({
    severity: $("sev").value, category: $("cat").value, group: $("grp").value,
    recent: $("rec").value, order: $("ord").value, limit: $("lim").value,
    query: $("q").value.trim(),
  });
  const data = await api("/api/signals?" + params.toString());
  const body = $("rows");
  body.replaceChildren(...data.rows.map(rowElement));
  $("empty").hidden = data.rows.length > 0;
  if (data.rows.length && !data.rows.some((r) => r.hash === selected)) select(data.rows[0].hash);
}

async function select(hash) {
  selected = hash;
  for (const tr of $("rows").children) tr.classList.toggle("sel", tr.dataset.hash === hash);
  const sig = await api("/api/signal/" + encodeURIComponent(hash));
  const pane = $("paneDetail");
  pane.replaceChildren();
  const h2 = esc("h2", sig.title);
  pane.append(h2);
  const tags = document.createElement("div");
  const sev = esc("span", sig.severity.toUpperCase());
  sev.className = "tag " + sig.severity;
  tags.append(sev, esc("span", sig.category), document.createTextNode(" · "),
              esc("span", sig.priority), document.createTextNode(" · "),
              esc("span", sig.verification));
  if (sig.confidence) tags.append(document.createTextNode(" · confidence " + sig.confidence));
  pane.append(tags);
  const dl = document.createElement("dl");
  const add = (label, value) => {
    if (!value) return;
    dl.append(esc("dt", label), esc("dd", value));
  };
  add("Detected", (sig.detected || "").replace("T", " "));
  add("Published", sig.published ? sig.published + " (" + (sig.freshness || "") + ")"
                                 : "undated: the feed supplied no date");
  add("Source", sig.source);
  add("Assessment", sig.assessment);
  add("Next step", sig.action);
  pane.append(dl);
  if (sig.facts.length) {
    pane.append(esc("div", "Key facts"));
    const ul = document.createElement("ul");
    for (const fact of sig.facts) ul.append(esc("li", fact));
    pane.append(ul);
  }
  const ents = Object.entries(sig.entities);
  if (ents.length) {
    const ul = document.createElement("ul");
    for (const [kind, values] of ents) ul.append(esc("li", kind + ": " + values.join(", ")));
    pane.append(esc("div", "Entities"), ul);
  }
  const links = document.createElement("p");
  const target = sig.display_link || sig.link;
  if (target) {
    const a = document.createElement("a");
    a.href = target; a.textContent = target;
    a.target = "_blank"; a.rel = "noopener noreferrer";
    links.append(a);
  }
  const dismiss = document.createElement("button");
  dismiss.textContent = "Not relevant";
  dismiss.style.marginTop = "10px";
  dismiss.addEventListener("click", async () => {
    await api("/api/dismiss", {method: "POST", body: JSON.stringify({hash: sig.hash})});
    selected = null; await loadState(); await loadSignals();
  });
  links.append(document.createElement("br"), dismiss);
  pane.append(links);
  showTab("detail");
}

async function loadBriefing() {
  const data = await api("/api/briefing");
  $("paneBrief").textContent = data.content
    ? (data.created_at ? "Briefed " + data.created_at + "\n\n" : "") + data.content
    : "No briefing yet. A briefing is written when a scan runs with an LLM key configured.";
}

function showTab(which) {
  $("tabDetail").classList.toggle("on", which === "detail");
  $("tabBrief").classList.toggle("on", which === "brief");
  $("tabLog").classList.toggle("on", which === "log");
  $("paneDetail").hidden = which !== "detail";
  $("paneBrief").hidden = which !== "brief";
  $("paneLog").hidden = which !== "log";
  if (which === "brief") loadBriefing();
  if (which === "log") api("/api/log").then((d) => setText("paneLog", d.log.join("\n")));
}

function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(async () => { await refresh(); schedule(); },
    state && state.scan.running ? 1500 : 20000);
}

async function refresh() {
  const previous = state && state.scan.running;
  await loadState();
  await loadSignals();
  if (state.scan.running || previous) {
    const d = await api("/api/log");
    setText("paneLog", d.log.join("\n"));
    if (!state.scan.running) { await loadBriefing(); }
  }
}

$("scanBtn").addEventListener("click", async () => {
  try { await api("/api/scan", {method: "POST", body: "{}"}); }
  catch (e) { alert(e.message); }
  await refresh();
});
$("cancelBtn").addEventListener("click", async () => {
  await api("/api/scan/cancel", {method: "POST", body: "{}"});
});
$("exportBtn").addEventListener("click", () => {
  window.location = "/api/export?fmt=html&token=" + encodeURIComponent(TOKEN);
});
$("reloadBtn").addEventListener("click", refresh);
$("themeSel").addEventListener("change", async () => {
  const chosen = $("themeSel").value;
  document.documentElement.dataset.theme = chosen;       // instant feedback
  try { await persistSettings({theme: chosen}, ["themeSel"]); }
  catch (e) { alert(e.message); syncControls(); }
});
$("autoOn").addEventListener("change", saveSchedule);
$("intervalSel").addEventListener("change", saveSchedule);
$("scanWindow").addEventListener("change", saveSchedule);
$("scanWindow").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); saveSchedule(); }
});
$("tabDetail").addEventListener("click", () => showTab("detail"));
$("tabBrief").addEventListener("click", () => showTab("brief"));
$("tabLog").addEventListener("click", () => showTab("log"));
for (const id of ["sev", "cat", "grp", "rec", "ord", "lim"]) {
  $(id).addEventListener("change", saveFilters);
}
let debounce = null;
$("q").addEventListener("input", () => {
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(loadSignals, 250);
});

(async () => {
  await refresh();
  schedule();
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- entry point


def serve(host="127.0.0.1", port=8765, token=None, open_browser=True, quiet=False):
    """Start the web app. Returns the server so a caller can shut it down."""
    token = token or secrets.token_urlsafe(18)
    handler = type("BoundHandler", (Handler,), {
        "token": token,
        "allowed_hosts": {"127.0.0.1", "localhost", "::1", host.lower()},
        "allow_ip_hosts": host in ("0.0.0.0", "::"),
        "quiet": quiet,
    })
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"[INFO] {hbnscan.APP_NAME} web app on {url}")
    print(f"[INFO] token: {token}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("[WARN] Bound to a non-loopback address; anyone who can reach this port and "
              "knows the token can read your signals and start scans.")
    stop_event = threading.Event()
    scheduler = threading.Thread(target=_scheduler_loop, args=(stop_event,), daemon=True,
                                 name="hb-scheduler")
    scheduler.start()
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Stopped.")
    finally:
        stop_event.set()
        httpd.server_close()
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="HB Briefing web app: signal grid, scan control and exports in a browser.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (default 127.0.0.1; loopback only)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=None,
                        help="shared token for mutating requests (default: generated)")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    # Deliberately not --quiet: the main CLI's scheduling flag is --window now, but --quiet
    # lingers as its alias, and this flag only silences per-request logging.
    parser.add_argument("--no-request-log", action="store_true",
                        help="do not log each HTTP request to stderr")
    args = parser.parse_args(argv)
    serve(host=args.host, port=args.port, token=args.token,
          open_browser=not args.no_browser, quiet=args.no_request_log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
