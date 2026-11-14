#!/usr/bin/env python3
"""
HB Briefing - research workspace

Dossiers, notes, actions, cited reports and source health, layered on the engine's database.

The design rule here is evidence over assertion. A report is built from stored rows: every
development carries its source and date, every gap is stated rather than papered over, and a
missing publication date is labelled instead of being estimated. Nothing in this module calls a
model, so a dossier report is reproducible and works offline.
"""
from datetime import datetime, timedelta
import html as html_module
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import urlparse

import hbnscan

DOSSIER_KINDS = ("subject", "entity", "question")
DOSSIER_STATUSES = ("open", "closed", "archived")
ACTION_STATUSES = ("open", "in_progress", "done", "dropped")
ACTION_PRIORITIES = ("high", "medium", "low")
REPORT_FORMATS = ("md", "html", "json")

_MAX_TEXT = 4000
_MAX_TITLE = 300


# --------------------------------------------------------------------------- helpers


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _clean(value, limit=_MAX_TEXT):
    return " ".join(str(value or "").split())[:limit]


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _host(url):
    return (urlparse(url or "").hostname or "").lower()


def _source(feed_url):
    return _host(feed_url) or (feed_url or "unknown source")


# --------------------------------------------------------------------------- dossiers


def create_dossier(conn, name, summary="", kind="subject"):
    """Create a dossier. Raises ValueError for a blank or duplicate name."""
    name = _clean(name, 160)
    if not name:
        raise ValueError("a dossier needs a name")
    if kind not in DOSSIER_KINDS:
        kind = "subject"
    now = _now()
    try:
        cursor = conn.execute(
            "INSERT INTO dossiers (name, summary, kind, status, created_at, updated_at) "
            "VALUES (?, ?, ?, 'open', ?, ?)",
            (name, _clean(summary), kind, now, now),
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"a dossier named {name!r} already exists") from None
    conn.commit()
    return cursor.lastrowid


def resolve_dossier(conn, ident):
    """Look a dossier up by id, or by name (case-insensitive). Returns a row or None."""
    numeric = _as_int(ident)
    if numeric is not None:
        row = conn.execute("SELECT * FROM dossiers WHERE id = ?", (numeric,)).fetchone()
        if row is not None:
            return row
    return conn.execute(
        "SELECT * FROM dossiers WHERE name = ? COLLATE NOCASE", (str(ident).strip(),)
    ).fetchone()


def list_dossiers(conn, include_archived=False):
    """Every dossier with its item and open-action counts, most recently touched first."""
    where = "" if include_archived else "WHERE d.status != 'archived'"
    rows = conn.execute(
        "SELECT d.id, d.name, d.summary, d.kind, d.status, d.created_at, d.updated_at, "
        "(SELECT COUNT(*) FROM dossier_items i WHERE i.dossier_id = d.id) AS items, "
        "(SELECT COUNT(*) FROM actions a WHERE a.dossier_id = d.id "
        " AND a.status IN ('open', 'in_progress')) AS open_actions "
        f"FROM dossiers d {where} ORDER BY d.updated_at DESC, d.id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def update_dossier(conn, dossier_id, **fields):
    """Rename, re-describe, re-kind or re-status a dossier. Unknown keys are rejected."""
    allowed = {"name", "summary", "kind", "status"}
    values = {}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "name":
            value = _clean(value, 160)
            if not value:
                raise ValueError("a dossier needs a name")
        elif key == "kind":
            if value not in DOSSIER_KINDS:
                raise ValueError(f"kind must be one of {', '.join(DOSSIER_KINDS)}")
        elif key == "status":
            if value not in DOSSIER_STATUSES:
                raise ValueError(f"status must be one of {', '.join(DOSSIER_STATUSES)}")
        else:
            value = _clean(value)
        values[key] = value
    if not values:
        return False
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    try:
        conn.execute(f"UPDATE dossiers SET {assignments} WHERE id = ?",
                     (*values.values(), dossier_id))
    except sqlite3.IntegrityError:
        raise ValueError("another dossier already uses that name") from None
    conn.commit()
    return True


def delete_dossier(conn, dossier_id):
    """Remove a dossier and its membership/notes. Signals and actions survive."""
    with hbnscan.transaction(conn):
        conn.execute("UPDATE actions SET dossier_id = NULL WHERE dossier_id = ?", (dossier_id,))
        conn.execute("DELETE FROM notes WHERE dossier_id = ?", (dossier_id,))
        conn.execute("DELETE FROM dossier_items WHERE dossier_id = ?", (dossier_id,))
        cursor = conn.execute("DELETE FROM dossiers WHERE id = ?", (dossier_id,))
    return cursor.rowcount > 0


def touch_dossier(conn, dossier_id):
    conn.execute("UPDATE dossiers SET updated_at = ? WHERE id = ?", (_now(), dossier_id))


def add_item(conn, dossier_id, item_hash, note=""):
    """Add a signal to a dossier. Idempotent: returns False when it was already there."""
    if conn.execute("SELECT 1 FROM dossiers WHERE id = ?", (dossier_id,)).fetchone() is None:
        raise ValueError("no such dossier")
    if conn.execute("SELECT 1 FROM seen_items WHERE item_hash = ?", (item_hash,)).fetchone() is None:
        raise ValueError("no such signal")
    exists = conn.execute(
        "SELECT 1 FROM dossier_items WHERE dossier_id = ? AND item_hash = ?",
        (dossier_id, item_hash)).fetchone()
    if exists:
        return False
    with hbnscan.transaction(conn):
        conn.execute(
            "INSERT INTO dossier_items (dossier_id, item_hash, note, added_at) "
            "VALUES (?, ?, ?, ?)", (dossier_id, item_hash, _clean(note), _now()))
        touch_dossier(conn, dossier_id)
    return True


def remove_item(conn, dossier_id, item_hash):
    with hbnscan.transaction(conn):
        cursor = conn.execute(
            "DELETE FROM dossier_items WHERE dossier_id = ? AND item_hash = ?",
            (dossier_id, item_hash))
        touch_dossier(conn, dossier_id)
    return cursor.rowcount > 0


def set_item_note(conn, dossier_id, item_hash, note):
    with hbnscan.transaction(conn):
        cursor = conn.execute(
            "UPDATE dossier_items SET note = ? WHERE dossier_id = ? AND item_hash = ?",
            (_clean(note), dossier_id, item_hash))
        touch_dossier(conn, dossier_id)
    return cursor.rowcount > 0


def add_latest(conn, dossier_id, count=1, query=""):
    """Add the newest visible signals (optionally matching a search) to a dossier."""
    rows, _total = hbnscan.select_signals(
        conn, {"query": query, "limit": max(1, int(count))}, order="newest")
    added = 0
    for row in rows:
        if add_item(conn, dossier_id, row["item_hash"]):
            added += 1
    return added


def dossier_items(conn, dossier_id):
    """The signals in a dossier, newest news first, with the user's per-item note."""
    columns = ", ".join(f"i.{name}" for name in hbnscan.SIGNAL_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns}, d.note FROM dossier_items d "
        f"JOIN seen_items i ON i.item_hash = d.item_hash "
        "WHERE d.dossier_id = ? "
        f"ORDER BY {hbnscan.row_order('newest')}",
        (dossier_id,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["source"] = _source(item.get("feed_url"))
        item["freshness"] = hbnscan.freshness_label(item.get("published"), item.get("seen_at"))
        out.append(item)
    return out


# --------------------------------------------------------------------------- notes


def add_note(conn, body, dossier_id=None, item_hash=None):
    body = _clean(body)
    if not body:
        raise ValueError("a note needs text")
    cursor = conn.execute(
        "INSERT INTO notes (dossier_id, item_hash, body, created_at) VALUES (?, ?, ?, ?)",
        (dossier_id, item_hash, body, _now()))
    if dossier_id is not None:
        touch_dossier(conn, dossier_id)
    conn.commit()
    return cursor.lastrowid


def list_notes(conn, dossier_id=None, item_hash=None):
    clauses, params = [], []
    if dossier_id is not None:
        clauses.append("dossier_id = ?")
        params.append(dossier_id)
    if item_hash is not None:
        clauses.append("item_hash = ?")
        params.append(item_hash)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [dict(row) for row in conn.execute(
        f"SELECT id, dossier_id, item_hash, body, created_at FROM notes {where} "
        "ORDER BY id DESC", params)]


def delete_note(conn, note_id):
    cursor = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    conn.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------- actions


def create_action(conn, title, detail="", dossier_id=None, item_hash=None,
                  priority="medium", due_date="", status="open"):
    title = _clean(title, _MAX_TITLE)
    if not title:
        raise ValueError("an action needs a title")
    if priority not in ACTION_PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(ACTION_PRIORITIES)}")
    if status not in ACTION_STATUSES:
        raise ValueError(f"status must be one of {', '.join(ACTION_STATUSES)}")
    if due_date and hbnscan.parse_setting_date(due_date) is None:
        raise ValueError("due date must be YYYY-MM-DD")
    now = _now()
    cursor = conn.execute(
        "INSERT INTO actions (title, detail, dossier_id, item_hash, status, priority, "
        "due_date, created_at, updated_at, completed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title, _clean(detail), dossier_id, item_hash, status, priority, due_date or "",
         now, now, now if status == "done" else None))
    if dossier_id is not None:
        touch_dossier(conn, dossier_id)
    conn.commit()
    return cursor.lastrowid


def update_action(conn, action_id, **fields):
    allowed = {"title", "detail", "status", "priority", "due_date", "dossier_id"}
    values = {}
    for key, value in fields.items():
        if key not in allowed or value is None:
            continue
        if key == "title":
            value = _clean(value, _MAX_TITLE)
            if not value:
                raise ValueError("an action needs a title")
        elif key == "status":
            if value not in ACTION_STATUSES:
                raise ValueError(f"status must be one of {', '.join(ACTION_STATUSES)}")
        elif key == "priority":
            if value not in ACTION_PRIORITIES:
                raise ValueError(f"priority must be one of {', '.join(ACTION_PRIORITIES)}")
        elif key == "due_date":
            if value and hbnscan.parse_setting_date(value) is None:
                raise ValueError("due date must be YYYY-MM-DD")
        elif key == "detail":
            value = _clean(value)
        values[key] = value
    if not values:
        return False
    if "status" in values:
        values["completed_at"] = _now() if values["status"] == "done" else None
    values["updated_at"] = _now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    conn.execute(f"UPDATE actions SET {assignments} WHERE id = ?",
                 (*values.values(), action_id))
    conn.commit()
    return True


def complete_action(conn, action_id, undo=False):
    """Mark an action done, or reopen it. Keeps `completed_at` honest in both directions."""
    return update_action(conn, action_id, status="open" if undo else "done")


def delete_action(conn, action_id):
    cursor = conn.execute("DELETE FROM actions WHERE id = ?", (action_id,))
    conn.commit()
    return cursor.rowcount > 0


def list_actions(conn, statuses=None, dossier_id=None, include_done=True):
    """Actions ordered by status, priority and due date; each with its dossier name."""
    statuses = list(statuses) if statuses else list(ACTION_STATUSES)
    if not include_done:
        statuses = [status for status in statuses if status not in ("done", "dropped")]
    marks = ",".join("?" for _ in statuses)
    clauses = [f"a.status IN ({marks})"]
    params = list(statuses)
    if dossier_id is not None:
        clauses.append("a.dossier_id = ?")
        params.append(dossier_id)
    rows = conn.execute(
        "SELECT a.id, a.title, a.detail, a.dossier_id, a.item_hash, a.status, a.priority, "
        "a.due_date, a.created_at, a.updated_at, a.completed_at, d.name AS dossier_name "
        "FROM actions a LEFT JOIN dossiers d ON d.id = a.dossier_id "
        f"WHERE {' AND '.join(clauses)} "
        "ORDER BY CASE a.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 "
        "WHEN 'done' THEN 2 ELSE 3 END, "
        "CASE a.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "CASE WHEN a.due_date IS NULL OR a.due_date = '' THEN 1 ELSE 0 END, a.due_date, "
        "a.id",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def render_action_markdown(rows, title="Action list"):
    lines = [f"# {title}", f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}_", ""]
    if not rows:
        lines.append("No actions recorded yet.")
        return "\n".join(lines) + "\n"
    for row in rows:
        box = "x" if row["status"] == "done" else " "
        due = f", due {row['due_date']}" if row.get("due_date") else ""
        dossier = f" — {row['dossier_name']}" if row.get("dossier_name") else ""
        lines.append(f"- [{box}] **{row['priority']}** {row['title']}{dossier}"
                     f" _({row['status']}{due})_")
        if row.get("detail"):
            lines.append(f"  - {row['detail']}")
        if row.get("item_hash"):
            lines.append(f"  - signal: `{row['item_hash'][:16]}`")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- reports


def _report_rows(conn, dossier_id):
    return dossier_items(conn, dossier_id)


def _report_gaps(conn, items, dossier):
    gaps = []
    screened = sum(1 for item in items if not item.get("deep_read"))
    if screened:
        gaps.append(f"{screened} signal(s) were screened from the feed only — the linked "
                    "article was not read, so their verdicts rest on headline and summary.")
    undated = sum(1 for item in items if not (item.get("published") or "").strip())
    if undated:
        gaps.append(f"{undated} signal(s) have no publication date; the timeline places them "
                    "by detection time and labels them undated.")
    hosts = {_host(item.get("feed_url")) for item in items if _host(item.get("feed_url"))}
    if len(hosts) == 1 and len(items) > 1:
        gaps.append(f"Every signal comes from {next(iter(hosts))}; treat the picture as "
                    "single-source until a second publisher corroborates it.")
    failing = conn.execute(
        "SELECT feed_url, last_status, last_error FROM feed_state "
        "WHERE last_error IS NOT NULL AND last_error != ''").fetchall()
    for feed_url, status, error in failing:
        gaps.append(f"Source {_source(feed_url)} failed its last fetch"
                    + (f" (HTTP {status})" if status else "") + f": {error}")
    if not gaps:
        gaps.append("No material gaps detected in the stored evidence.")
    if not items:
        gaps.append("The dossier has no signals yet, so there is nothing to corroborate.")
    if not dossier["summary"]:
        gaps.append("No scope note: the dossier does not yet state the question it answers.")
    return gaps


def dossier_report(conn, ident, developments=5):
    """Build a cited report from stored signals, notes and actions. Returns a dict."""
    dossier = resolve_dossier(conn, ident)
    if dossier is None:
        raise ValueError(f"no dossier matches {ident!r}")
    items = _report_rows(conn, dossier["id"])
    notes = list_notes(conn, dossier_id=dossier["id"])
    actions = list_actions(conn, dossier_id=dossier["id"])

    def stamp(item):
        return item.get("published") or item.get("seen_at") or ""

    ordered = sorted(items, key=stamp)
    def ranked_key(item):
        severity = {"high": 0, "medium": 1, "low": 2}.get(
            (item.get("severity") or "none").lower(), 3)
        parsed = hbnscan._parse_timestamp(stamp(item)) or datetime.min
        return (severity, -parsed.timestamp())

    ranked = sorted(items, key=ranked_key)
    counts = {"signals": len(items), "high": 0, "medium": 0, "low": 0,
              "undated": 0, "deep_read": 0}
    for item in items:
        severity = (item.get("severity") or "none").lower()
        if severity in counts:
            counts[severity] += 1
        if not (item.get("published") or "").strip():
            counts["undated"] += 1
        if item.get("deep_read"):
            counts["deep_read"] += 1
    sources = {}
    for item in items:
        host = _host(item.get("feed_url")) or "unknown"
        sources[host] = sources.get(host, 0) + 1

    timeline = []
    for item in ordered:
        timeline.append({
            "date": (item.get("published") or "")[:10] or (item.get("seen_at") or "")[:10],
            "undated": not (item.get("published") or "").strip(),
            "freshness": item.get("freshness", ""),
            "severity": item.get("severity") or "none",
            "category": item.get("category") or "other",
            "title": item.get("title") or "",
            "link": hbnscan.safe_link(item.get("link")),
            "source": item.get("source", ""),
            "note": item.get("note") or "",
            "assessment": hbnscan.strip_provenance(item.get("summary")),
        })

    return {
        "dossier": {
            "id": dossier["id"], "name": dossier["name"], "summary": dossier["summary"],
            "kind": dossier["kind"], "status": dossier["status"],
            "created_at": dossier["created_at"], "updated_at": dossier["updated_at"],
        },
        "generated": _now(),
        "counts": counts,
        "span": {"first": timeline[0]["date"] if timeline else "",
                 "last": timeline[-1]["date"] if timeline else ""},
        "sources": [{"host": host, "count": count}
                    for host, count in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))],
        "developments": [
            {"severity": item.get("severity") or "none",
             "title": item.get("title") or "",
             "link": hbnscan.safe_link(item.get("link")),
             "source": item.get("source", ""),
             "published": (item.get("published") or "")[:10],
             "undated": not (item.get("published") or "").strip(),
             "freshness": item.get("freshness", ""),
             "assessment": hbnscan.strip_provenance(item.get("summary")),
             "action": item.get("action") or ""}
            for item in ranked[:max(1, int(developments))]
        ],
        "timeline": timeline,
        "notes": [{"body": note["body"], "created_at": note["created_at"]} for note in notes],
        "actions": actions,
        "gaps": _report_gaps(conn, items, dossier),
    }


def _md(value):
    return hbnscan._md_text(value)


def _md_link(url):
    return hbnscan._md_url(url)


def render_report_markdown(report):
    dossier = report["dossier"]
    counts = report["counts"]
    lines = [
        f"# Dossier: {_md(dossier['name'])}",
        f"_Generated {report['generated'].replace('T', ' ')} · {counts['signals']} signal(s) · "
        f"{len(report['sources'])} publisher(s)_",
        "",
    ]
    if dossier["status"] != "open":
        lines.extend([f"**Status:** {dossier['status']}", ""])
    lines.extend(["## Scope", "", _md(dossier["summary"]) if dossier["summary"]
                  else "_No scope note yet. Add one so the dossier states its question._", ""])

    lines.extend(["## At a glance", ""])
    lines.append(f"- **{counts['signals']}** signal(s) · {counts['high']} high · "
                 f"{counts['medium']} medium · {counts['low']} low")
    lines.append(f"- **{counts['deep_read']}** verified against the linked article · "
                 f"**{counts['undated']}** undated")
    if report["span"]["first"]:
        lines.append(f"- Coverage: {report['span']['first']} → {report['span']['last']}")
    if report["sources"]:
        lines.append("- Publishers: " + ", ".join(
            f"{_md(entry['host'])} ({entry['count']})" for entry in report["sources"]))
    lines.append("")

    lines.extend(["## Key developments", ""])
    if not report["developments"]:
        lines.append("_No signals collected yet._")
    for index, item in enumerate(report["developments"], start=1):
        when = (f"{item['published']} ({item['freshness']})" if not item["undated"]
                else "undated")
        title = (f"[{_md(item['title'])}]({_md_link(item['link'])})" if item["link"]
                 else _md(item["title"]))
        lines.append(f"{index}. **[{item['severity']}] {title}** — {_md(item['source'])}, {when}")
        if item["assessment"]:
            lines.append(f"   {_md(item['assessment'])}")
        if item["action"]:
            lines.append(f"   _Next step:_ {_md(item['action'])}")
    lines.append("")

    lines.extend(["## Timeline", ""])
    if not report["timeline"]:
        lines.append("_Nothing on the timeline yet._")
    for entry in report["timeline"]:
        when = entry["date"] + (" (undated)" if entry["undated"] else "")
        title = (f"[{_md(entry['title'])}]({_md_link(entry['link'])})" if entry["link"]
                 else _md(entry["title"]))
        lines.append(f"- **{when}** · {entry['severity']} · {_md(entry['category'])} · "
                     f"{title} — {_md(entry['source'])}")
        if entry["note"]:
            lines.append(f"  - Note: {_md(entry['note'])}")
    lines.append("")

    lines.extend(["## Open questions", ""])
    if not report["notes"]:
        lines.append("_No notes recorded._")
    for note in report["notes"]:
        lines.append(f"- {_md(note['body'])}")
    lines.append("")

    lines.extend(["## Actions", ""])
    if not report["actions"]:
        lines.append("_No actions recorded._")
    for action in report["actions"]:
        box = "x" if action["status"] == "done" else " "
        due = f", due {action['due_date']}" if action.get("due_date") else ""
        lines.append(f"- [{box}] **{action['priority']}** {_md(action['title'])} "
                     f"_({action['status']}{due})_")
        if action.get("detail"):
            lines.append(f"  - {_md(action['detail'])}")
    lines.append("")

    lines.extend(["## Evidence gaps", ""])
    lines.extend(f"- {_md(gap)}" for gap in report["gaps"])
    lines.extend(["", "_Generated from stored signals; no model call was made for this report._"])
    return "\n".join(lines).rstrip("\n") + "\n"


def render_report_html(report):
    dossier = report["dossier"]
    counts = report["counts"]
    esc = html_module.escape
    badge = {"high": "#b91c1c", "medium": "#b45309", "low": "#475569", "none": "#64748b"}
    development_cards = []
    for item in report["developments"]:
        link = hbnscan.safe_link(item["link"])
        title = (f'<a href="{esc(link, quote=True)}">{esc(item["title"])}</a>' if link
                 else esc(item["title"]))
        when = ("undated" if item["undated"]
                else f'{esc(item["published"])} ({esc(str(item["freshness"]))})')
        development_cards.append(f"""
        <article class="card">
          <h3><span class="badge" style="background:{badge.get(item['severity'], '#64748b')}">
            {esc(item['severity'].upper())}</span> {title}</h3>
          <p class="meta">{esc(item['source'])} · {when}</p>
          {f'<p>{esc(item["assessment"])}</p>' if item["assessment"] else ""}
          {f'<p><strong>Next step.</strong> {esc(item["action"])}</p>' if item["action"] else ""}
        </article>""")
    timeline = "".join(
        f"<li><strong>{esc(entry['date'])}"
        f"{' (undated)' if entry['undated'] else ''}</strong> · {esc(entry['severity'])} · "
        f"{esc(entry['category'])} · "
        + (f'<a href="{esc(entry["link"], quote=True)}">{esc(entry["title"])}</a>'
           if entry["link"] else esc(entry["title"]))
        + f" — {esc(entry['source'])}"
        + (f"<br><em>Note:</em> {esc(entry['note'])}" if entry["note"] else "")
        + "</li>"
        for entry in report["timeline"])
    notes = "".join(f"<li>{esc(note['body'])}</li>" for note in report["notes"]) or \
        "<li><em>No notes recorded.</em></li>"
    actions = "".join(
        f"<li>{'✔' if action['status'] == 'done' else '☐'} <strong>{esc(action['priority'])}</strong> "
        f"{esc(action['title'])} <em>({esc(action['status'])}"
        + (f", due {esc(action['due_date'])}" if action.get("due_date") else "") + ")</em></li>"
        for action in report["actions"]) or "<li><em>No actions recorded.</em></li>"
    gaps = "".join(f"<li>{esc(gap)}</li>" for gap in report["gaps"])
    sources = ", ".join(f"{esc(entry['host'])} ({entry['count']})" for entry in report["sources"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{esc(dossier['name'])} — dossier</title>
<style>
 body {{ font: 15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; margin: 0;
        background: #f4f6f9; color: #0f172a; }}
 header {{ background: #1a202c; color: #e2e8f0; padding: 22px 28px; }}
 header h1 {{ margin: 0 0 6px; font-size: 21px; }}
 header p {{ margin: 0; color: #94a3b8; font-size: 13px; }}
 main {{ max-width: 900px; margin: 0 auto; padding: 22px; }}
 h2 {{ font-size: 17px; margin: 26px 0 10px; }}
 .card {{ background: #fff; border-radius: 8px; padding: 14px 18px; margin: 0 0 12px;
          box-shadow: 0 1px 3px rgba(15,23,42,.10); }}
 .card h3 {{ font-size: 15px; margin: 0 0 6px; }}
 .card a {{ color: #1d4ed8; text-decoration: none; }}
 .badge {{ color: #fff; font-size: 11px; padding: 2px 7px; border-radius: 4px;
           vertical-align: 2px; letter-spacing: .04em; }}
 .meta {{ color: #64748b; font-size: 12px; margin: 0 0 8px; }}
 ul {{ margin: 6px 0; padding-left: 20px; }}
 .gap {{ background: #fff7ed; border-left: 3px solid #f59e0b; padding: 10px 14px;
         border-radius: 6px; }}
</style></head><body>
<header><h1>{esc(dossier['name'])}</h1>
<p>{counts['signals']} signal(s) · {counts['high']} high · {counts['medium']} medium ·
{counts['low']} low · {esc(dossier['status'])} · generated {esc(report['generated'])}</p></header>
<main>
<section><h2>Scope</h2><p>{esc(dossier['summary']) or '<em>No scope note yet.</em>'}</p>
<p class="meta">Coverage {esc(report['span']['first'])} → {esc(report['span']['last'])}
{('· ' + sources) if sources else ''}</p></section>
<section><h2>Key developments</h2>{''.join(development_cards) or '<p><em>No signals yet.</em></p>'}</section>
<section><h2>Timeline</h2><ul>{timeline or '<li><em>Nothing yet.</em></li>'}</ul></section>
<section><h2>Open questions</h2><ul>{notes}</ul></section>
<section><h2>Actions</h2><ul>{actions}</ul></section>
<section class="gap"><h2>Evidence gaps</h2><ul>{gaps}</ul></section>
<p class="meta">Generated from stored signals; no model call was made for this report.</p>
</main></body></html>"""


def render_report_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def render_report(report, fmt="md"):
    fmt = (fmt or "md").lower().lstrip(".")
    if fmt in ("markdown", "md"):
        return render_report_markdown(report)
    if fmt in ("html", "htm"):
        return render_report_html(report)
    if fmt == "json":
        return render_report_json(report)
    raise ValueError(f"unsupported report format {fmt!r}; use {', '.join(REPORT_FORMATS)}")


def export_report(conn, ident, path, fmt=None):
    """Write a dossier report to `path`. Returns (path, signal_count)."""
    target = Path(path)
    fmt = fmt or (target.suffix.lower().lstrip(".") or "md")
    report = dossier_report(conn, ident)
    text = render_report(report, fmt)
    hbnscan.write_atomic(target, text)
    return target, report["counts"]["signals"]


# --------------------------------------------------------------------------- source health


def source_health(conn, feeds=None, stale_hours=None):
    """Per-source fetch status: ok, failing, stale or never.

    `stale_hours` defaults to twice the configured scan interval, with a six-hour floor, so a
    daily schedule does not label everything stale between runs.
    """
    feeds = list(feeds if feeds is not None else hbnscan.load_feeds())
    if stale_hours is None:
        interval = int(hbnscan.load_settings()["scan_interval_minutes"])
        stale_hours = max(6, (interval * 2) // 60 + 1)
    cutoff = (datetime.now() - timedelta(hours=stale_hours)).isoformat(timespec="seconds")
    out = []
    for url in feeds:
        row = conn.execute(
            "SELECT last_success_at, checked_at, last_status, last_error, items_new, failures "
            "FROM feed_state WHERE feed_url = ?", (url,)).fetchone()
        record = dict(row) if row else {}
        last_success = record.get("last_success_at") or ""
        if record.get("last_error"):
            status = "failing"
        elif not last_success:
            status = "never"
        elif last_success < cutoff:
            status = "stale"
        else:
            status = "ok"
        out.append({
            "url": url,
            "source": _host(url) or url,
            "status": status,
            "last_success": last_success,
            "checked_at": record.get("checked_at") or "",
            "http_status": record.get("last_status"),
            "error": record.get("last_error") or "",
            "items_new": record.get("items_new") or 0,
            "failures": record.get("failures") or 0,
        })
    order = {"failing": 0, "never": 1, "stale": 2, "ok": 3}
    out.sort(key=lambda entry: (order[entry["status"]], entry["source"]))
    return out


def coverage_summary(conn, feeds=None):
    """The numbers behind the Today strip and the Sources tab."""
    feeds = list(feeds if feeds is not None else hbnscan.load_feeds())
    health = source_health(conn, feeds)
    counts = {"ok": 0, "failing": 0, "stale": 0, "never": 0}
    for entry in health:
        counts[entry["status"]] += 1
    visible = conn.execute(
        f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL}").fetchone()[0]
    undated = conn.execute(
        f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} "
        "AND COALESCE(published, '') = ''").fetchone()[0]
    previous = conn.execute(
        "SELECT value FROM meta WHERE key = 'prev_scan_at'").fetchone()
    previous = previous[0] if previous and previous[0] else ""
    new_since = 0
    if previous:
        new_since = conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL} AND seen_at > ?",
            (previous,)).fetchone()[0]
    last = conn.execute(
        "SELECT started_at, finished_at, new_items, flagged, feeds_failed, cancelled, "
        "duration_ms FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "sources": {"total": len(feeds), **counts},
        "signals": {"visible": visible, "undated": undated, "new_since_prev": new_since},
        "last_run": dict(last) if last else None,
        "health": health,
    }


def describe_coverage(summary):
    """One line for the Today strip."""
    sources = summary["sources"]
    signals = summary["signals"]
    parts = [f"{sources['total']} sources"]
    if sources["failing"]:
        parts.append(f"{sources['failing']} failing")
    if sources["stale"]:
        parts.append(f"{sources['stale']} stale")
    if sources["never"]:
        parts.append(f"{sources['never']} not yet fetched")
    parts.append(f"{signals['visible']} signals")
    if signals["new_since_prev"]:
        parts.append(f"{signals['new_since_prev']} new")
    if signals["undated"]:
        parts.append(f"{signals['undated']} undated")
    run = summary.get("last_run")
    if run and run.get("started_at"):
        parts.append("last scan " + run["started_at"].replace("T", " "))
        if run.get("cancelled"):
            parts.append("cancelled")
        if run.get("feeds_failed"):
            parts.append(f"{run['feeds_failed']} feed error(s)")
    return "  ·  ".join(parts)


# --------------------------------------------------------------------------- command line


def _connect():
    return hbnscan.init_db()


def _print_dossier_rows(rows):
    if not rows:
        print("No dossiers yet. Create one with: hbnscan.py dossier new \"Name\"")
        return
    print(f"{'ID':>3}  {'ITEMS':>5}  {'OPEN':>4}  {'STATUS':<8}  NAME")
    for row in rows:
        print(f"{row['id']:>3}  {row['items']:>5}  {row['open_actions']:>4}  "
              f"{row['status']:<8}  {row['name']}")


def _print_actions(rows):
    if not rows:
        print("No actions recorded.")
        return
    print(f"{'ID':>3}  {'STATUS':<11}  {'PRIORITY':<8}  {'DUE':<10}  TITLE")
    for row in rows:
        dossier = f"  [{row['dossier_name']}]" if row.get("dossier_name") else ""
        print(f"{row['id']:>3}  {row['status']:<11}  {row['priority']:<8}  "
              f"{(row['due_date'] or ''):<10}  {row['title']}{dossier}")


def _print_sources(summary):
    for entry in summary["health"]:
        detail = entry["error"] or (f"HTTP {entry['http_status']}"
                                    if entry["http_status"] else "")
        print(f"{entry['status']:<8}  {entry['source']:<32}  "
              f"ok: {entry['last_success'] or 'never':<19}  {detail}")
    print()
    print(describe_coverage(summary))


def main(argv):
    """`hbnscan.py dossier|action|sources ...` - the same workspace from a shell."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="hbnscan.py",
        description="Dossiers, actions and source health, without a front end.")
    sub = parser.add_subparsers(dest="command", required=True)

    dossier = sub.add_parser("dossier", help="research dossiers")
    dossier_sub = dossier.add_subparsers(dest="action", required=True)
    dossier_sub.add_parser("list", help="list dossiers")
    dossier_list = dossier_sub.choices["list"]
    dossier_list.add_argument("--all", action="store_true", help="include archived dossiers")
    new = dossier_sub.add_parser("new", help="create a dossier")
    new.add_argument("name")
    new.add_argument("--summary", default="", help="the question this dossier answers")
    new.add_argument("--kind", default="subject", choices=list(DOSSIER_KINDS))
    show = dossier_sub.add_parser("show", help="print a dossier report")
    show.add_argument("dossier")
    add = dossier_sub.add_parser("add", help="add signals to a dossier")
    add.add_argument("dossier")
    add.add_argument("--hash", dest="item_hash", help="exact signal hash")
    add.add_argument("--latest", type=int, default=0, help="add the newest N signals")
    add.add_argument("--search", default="", help="restrict --latest to a search term")
    note = dossier_sub.add_parser("note", help="record an open question")
    note.add_argument("dossier")
    note.add_argument("text")
    report = dossier_sub.add_parser("report", help="write a dossier report")
    report.add_argument("dossier")
    report.add_argument("--export", help="output path (.md, .html or .json)")
    report.add_argument("--format", choices=list(REPORT_FORMATS))
    status = dossier_sub.add_parser("status", help="set a dossier's status")
    status.add_argument("dossier")
    status.add_argument("value", choices=list(DOSSIER_STATUSES))

    action = sub.add_parser("action", help="follow-up actions")
    action_sub = action.add_subparsers(dest="action", required=True)
    action_list = action_sub.add_parser("list", help="list actions")
    action_list.add_argument("--all", action="store_true", help="include done and dropped")
    action_new = action_sub.add_parser("new", help="add an action")
    action_new.add_argument("title")
    action_new.add_argument("--dossier")
    action_new.add_argument("--detail", default="")
    action_new.add_argument("--priority", default="medium", choices=list(ACTION_PRIORITIES))
    action_new.add_argument("--due", default="", help="YYYY-MM-DD")
    action_done = action_sub.add_parser("done", help="complete an action")
    action_done.add_argument("id", type=int)
    action_reopen = action_sub.add_parser("reopen", help="reopen a completed action")
    action_reopen.add_argument("id", type=int)
    action_export = action_sub.add_parser("export", help="write the action list")
    action_export.add_argument("path")

    sources = sub.add_parser("sources", help="source health and coverage")
    sources.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)
    conn = _connect()
    try:
        if args.command == "sources":
            summary = coverage_summary(conn)
            if args.json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                _print_sources(summary)
            return 0

        if args.command == "action":
            if args.action == "list":
                _print_actions(list_actions(conn, include_done=args.all))
                return 0
            if args.action == "new":
                dossier_id = None
                if args.dossier:
                    row = resolve_dossier(conn, args.dossier)
                    if row is None:
                        print(f"[FAIL] no dossier matches {args.dossier!r}")
                        return 2
                    dossier_id = row["id"]
                action_id = create_action(conn, args.title, detail=args.detail,
                                          dossier_id=dossier_id, priority=args.priority,
                                          due_date=args.due)
                print(f"[OK] action {action_id}: {args.title}")
                return 0
            if args.action in ("done", "reopen"):
                if not update_action(conn, args.id,
                                     status="done" if args.action == "done" else "open"):
                    print(f"[FAIL] no action with id {args.id}")
                    return 2
                print(f"[OK] action {args.id} {args.action}ed")
                return 0
            if args.action == "export":
                rows = list_actions(conn)
                hbnscan.write_atomic(Path(args.path), render_action_markdown(rows))
                print(f"[OK] {len(rows)} action(s) written to {args.path}")
                return 0

        if args.command == "dossier":
            if args.action == "list":
                _print_dossier_rows(list_dossiers(conn, include_archived=args.all))
                return 0
            if args.action == "new":
                dossier_id = create_dossier(conn, args.name, args.summary, args.kind)
                print(f"[OK] dossier {dossier_id}: {args.name}")
                return 0
            if args.action == "show":
                print(render_report_markdown(dossier_report(conn, args.dossier)), end="")
                return 0
            if args.action == "add":
                row = resolve_dossier(conn, args.dossier)
                if row is None:
                    print(f"[FAIL] no dossier matches {args.dossier!r}")
                    return 2
                if args.item_hash:
                    added = 1 if add_item(conn, row["id"], args.item_hash) else 0
                elif args.latest:
                    added = add_latest(conn, row["id"], args.latest, args.search)
                else:
                    print("[FAIL] use --hash HASH or --latest N")
                    return 2
                print(f"[OK] {added} signal(s) added to {row['name']}")
                return 0
            if args.action == "note":
                row = resolve_dossier(conn, args.dossier)
                if row is None:
                    print(f"[FAIL] no dossier matches {args.dossier!r}")
                    return 2
                add_note(conn, args.text, dossier_id=row["id"])
                print("[OK] note recorded")
                return 0
            if args.action == "status":
                row = resolve_dossier(conn, args.dossier)
                if row is None:
                    print(f"[FAIL] no dossier matches {args.dossier!r}")
                    return 2
                update_dossier(conn, row["id"], status=args.value)
                print(f"[OK] {row['name']} is now {args.value}")
                return 0
            if args.action == "report":
                row = resolve_dossier(conn, args.dossier)
                if row is None:
                    print(f"[FAIL] no dossier matches {args.dossier!r}")
                    return 2
                if not args.export:
                    print(render_report(dossier_report(conn, row["id"]), args.format or "md"),
                          end="")
                    return 0
                path, count = export_report(conn, row["id"], args.export, args.format)
                print(f"[OK] {count} signal(s) written to {path}")
                return 0
        print("[FAIL] nothing to do", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 2
    finally:
        conn.close()
