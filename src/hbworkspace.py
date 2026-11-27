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
    try:
        return (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return ""


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


# --------------------------------------------------------------------------- entities


ENTITY_MIN_QUERY = 2
ENTITY_LIMIT = 200


def entity_values(raw):
    """Every entity string stored on one row, flattened out of its JSON groups."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    values = []
    for group in data.values():
        if isinstance(group, (list, tuple)):
            for value in group:
                text = _clean(value, 120)
                if text:
                    values.append(text)
        elif isinstance(group, str) and group.strip():
            values.append(_clean(group, 120))
    return values


def _entity_associations(signals, needle):
    """Count co-mentions, never infer ownership, affiliation or causation."""
    index = {}
    for item in signals:
        story = item.get("cluster_key") or "row:" + item["item_hash"]
        for name in item.get("co_mentions", []):
            key = name.casefold().rstrip(" .,;:")
            if not key or needle in key:
                continue
            entry = index.setdefault(key, {"names": {}, "stories": set(), "signals": set(),
                                           "examples": []})
            if item["item_hash"] in entry["signals"]:
                continue
            entry["signals"].add(item["item_hash"])
            entry["stories"].add(story)
            entry["names"][name] = entry["names"].get(name, 0) + 1
            if len(entry["examples"]) < 3:
                entry["examples"].append({"title": item.get("title") or "",
                                          "link": item.get("link") or "",
                                          "hash": item["item_hash"]})
    associations = [
        {"name": max(entry["names"], key=lambda name: (entry["names"][name], name)),
         "stories": len(entry["stories"]), "signals": len(entry["signals"]),
         "examples": entry["examples"]}
        for entry in index.values()]
    return sorted(associations, key=lambda entry: (-entry["stories"], -entry["signals"],
                                                  entry["name"].casefold()))[:30]


def entity_report(conn, query, limit=ENTITY_LIMIT):
    """Everything stored about one company, regulator or person.

    Matches entity values case-insensitively by substring, so "acme" finds "Acme Corp" and
    "Acme Fiber LLC". The SQL `LIKE` is only a cheap prefilter; the exact test happens on the
    parsed JSON, which is why a value is never counted twice.

    Graded-but-unflagged rows are included and marked, because "what do we know about Nvidia"
    is a different question from "what did the scan page me about today". Rows the reader
    dismissed are left out: that was an explicit "no".
    """
    needle = " ".join(str(query or "").split()).casefold()
    if len(needle) < ENTITY_MIN_QUERY:
        raise ValueError("give at least two characters to search for")
    limit = max(1, min(2000, int(limit)))
    columns = ", ".join(hbnscan.SIGNAL_COLUMNS)
    rows = conn.execute(
        f"SELECT {columns}, flagged, dismissed FROM seen_items "
        "WHERE dismissed = 0 AND COALESCE(entities, '') LIKE ? "
        f"ORDER BY flagged DESC, {hbnscan.row_order('newest')}",
        (f"%{query.strip()}%",),
    ).fetchall()

    signals = []
    truncated = False
    # One company, several spellings: "Nvidia", "NVIDIA" and "Nvidia Corp." are counted
    # together under the spelling seen most often.
    spellings = {}
    for row in rows:
        hits = [value for value in entity_values(row["entities"])
                if needle in value.casefold()]
        if not hits:
            continue
        if len(signals) >= limit:
            truncated = True
            break
        for hit in dict.fromkeys(hits):
            # Case and a trailing full stop are not a different company.
            key = hit.casefold().rstrip(" .,;:")
            counts = spellings.setdefault(key, {})
            counts[hit] = counts.get(hit, 0) + 1
        item = dict(row)
        item["source"] = _source(item.get("feed_url"))
        item["link"] = hbnscan.safe_link(item.get("link"))
        item["freshness"] = hbnscan.freshness_label(item.get("published"),
                                                    item.get("seen_at"))
        item["matched_entities"] = hits
        item["co_mentions"] = sorted(set(entity_values(row["entities"])) - set(hits),
                                     key=lambda name: (name.casefold(), name))
        item["entities"] = ", ".join(hits)
        item["flagged"] = int(item.get("flagged") or 0)
        item["assessment"] = hbnscan.strip_provenance(item.get("summary"))
        signals.append(item)

    hashes = [item["item_hash"] for item in signals]
    hash_set = set(hashes)
    like = f"%{query.strip()}%"
    dossiers = []
    if hashes:
        marks = ",".join("?" for _ in hashes)
        dossiers = [dict(row) for row in conn.execute(
            "SELECT d.id, d.name, d.status, d.updated_at, "
            "COUNT(i.item_hash) AS matched "
            "FROM dossiers d JOIN dossier_items i ON i.dossier_id = d.id "
            f"WHERE i.item_hash IN ({marks}) "
            "GROUP BY d.id ORDER BY matched DESC, d.name", hashes)]
    # A dossier whose scope note names the entity matters even before a signal is linked.
    for row in conn.execute(
            "SELECT id, name, status, updated_at, 0 AS matched FROM dossiers "
            "WHERE name LIKE ? OR summary LIKE ? ORDER BY id", (like, like)):
        if not any(entry["id"] == row["id"] for entry in dossiers):
            dossiers.append(dict(row))

    actions = [row for row in list_actions(conn, include_done=True)
               if row.get("item_hash") in hash_set
               or needle in f"{row['title']} {row.get('detail') or ''}".casefold()]
    notes = [row for row in list_notes(conn)
             if row.get("item_hash") in hash_set or needle in (row["body"] or "").casefold()]

    sources = {}
    severities = {}
    categories = {}
    for item in signals:
        sources[item["source"]] = sources.get(item["source"], 0) + 1
        severities[item.get("severity") or "none"] = severities.get(
            item.get("severity") or "none", 0) + 1
        categories[item.get("category") or "other"] = categories.get(
            item.get("category") or "other", 0) + 1
    matched = {max(counts, key=lambda name: (counts[name], name)): sum(counts.values())
               for counts in spellings.values()}
    dates = sorted((item.get("published") or "")[:10]
                   for item in signals if item.get("published"))

    def ranked(counts):
        return [{"name": name, "count": count}
                for name, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))]

    return {
        "query": str(query).strip(),
        "generated": _now(),
        "matched": ranked(matched),
        "signals": signals,
        "associations": _entity_associations(signals, needle),
        "association_scope": {"truncated": truncated, "signal_limit": limit,
                              "max_associations": 30,
                              "caveat": "Co-mentions in stored signals do not establish "
                                        "a factual relationship or independent corroboration."},
        "dossiers": dossiers,
        "actions": actions,
        "notes": notes,
        "sources": ranked(sources),
        "counts": {
            "signals": len(signals),
            "dossiers": len(dossiers),
            "open_actions": sum(1 for row in actions
                                if row["status"] in ("open", "in_progress")),
            "actions": len(actions),
            "notes": len(notes),
            "flagged": sum(1 for item in signals if item["flagged"]),
            "undated": sum(1 for item in signals if not (item.get("published") or "").strip()),
            "by_severity": severities,
            "by_category": categories,
        },
        "span": {"first": dates[0] if dates else "", "last": dates[-1] if dates else ""},
    }


def describe_entity(report):
    """One line for the CLI header."""
    counts = report["counts"]
    parts = [f"{counts['signals']} signal(s)",
             f"{counts['flagged']} flagged"]
    if report["matched"]:
        parts.append("as " + ", ".join(f"{entry['name']} ({entry['count']})"
                                       for entry in report["matched"][:4]))
    if counts["dossiers"]:
        parts.append(f"{counts['dossiers']} dossier(s)")
    if counts["open_actions"]:
        parts.append(f"{counts['open_actions']} open action(s)")
    if report["span"]["first"]:
        parts.append(f"{report['span']['first']} → {report['span']['last']}")
    return "  ·  ".join(parts)


# --------------------------------------------------------------------------- reports


def _report_rows(conn, dossier_id):
    return dossier_items(conn, dossier_id)


def _publication_stamp(item):
    return hbnscan._parse_timestamp(item.get("published"))


def _report_stamp(item):
    return _publication_stamp(item) or hbnscan._parse_timestamp(item.get("seen_at"))


def _story_evidence(items):
    """Describe observed coverage, not independent confirmation of claims."""
    groups = {}
    for item in items:
        key = item.get("cluster_key") or "row:" + item["item_hash"]
        groups.setdefault(key, []).append(item)
    stories = []
    for key, members in sorted(groups.items()):
        article_hosts, feed_hosts = set(), set()
        unresolved = 0
        for member in members:
            feed = _host(member.get("feed_url"))
            if feed:
                feed_hosts.add(feed)
            try:
                link = hbnscan.safe_link(member.get("link"))
                host = _host(link)
                if not host or hbnscan.is_aggregator_host(link):
                    unresolved += 1
                    continue
                article_hosts.add(host[4:] if host.startswith("www.") else host)
            except ValueError:
                unresolved += 1
        representative = min(members, key=lambda item: item["item_hash"])
        stories.append({
            "cluster_key": key, "title": representative.get("title") or "",
            "signals": len(members), "article_hosts": sorted(article_hosts),
            "feed_hosts": sorted(feed_hosts), "unresolved_links": unresolved,
            "article_reads": sum(bool(item.get("deep_read")) for item in members),
        })
    return {
        "stories": stories, "story_count": len(stories),
        "multi_item_stories": sum(story["signals"] > 1 for story in stories),
        "multi_host_stories": sum(len(story["article_hosts"]) > 1 for story in stories),
        "unresolved_links": sum(story["unresolved_links"] for story in stories),
        "caveat": "Story groups and distinct article hosts measure observed coverage, "
                  "not independent corroboration or factual verification.",
    }


def _report_gaps(conn, items, dossier, evidence):
    gaps = []
    screened = sum(1 for item in items if not item.get("deep_read"))
    if screened:
        gaps.append(f"{screened} signal(s) were screened from the feed only — the linked "
                    "article was not read, so their verdicts rest on headline and summary.")
    undated = sum(1 for item in items if _publication_stamp(item) is None)
    if undated:
        gaps.append(f"{undated} signal(s) have no publication date or no usable date; "
                    "the timeline falls back to detection time and labels them undated.")
    hosts = {_host(item.get("feed_url")) for item in items if _host(item.get("feed_url"))}
    if len(hosts) == 1 and len(items) > 1:
        gaps.append(f"Every signal was delivered by {next(iter(hosts))}; "
                    "a delivery host is not necessarily the original publisher.")
    if evidence["multi_item_stories"]:
        gaps.append(f"{evidence['multi_item_stories']} story group(s) contain multiple signals; "
                    "syndicated or duplicated coverage is not independent corroboration.")
    if evidence["unresolved_links"]:
        gaps.append(f"{evidence['unresolved_links']} signal(s) have unresolved or missing article "
                    "links; their publisher hosts cannot be established from stored links.")
    failing = conn.execute(
        "SELECT feed_url, last_status, last_error FROM feed_state "
        "WHERE last_error IS NOT NULL AND last_error != ''").fetchall()
    for feed_url, status, error in failing:
        gaps.append(f"Source {_source(feed_url)} failed its last fetch"
                    + (f" (HTTP {status})" if status else "") + f": {error}")
    if not gaps and items:
        gaps.append("No automatic evidence-gap flags; independent confirmation is not established.")
    if not items:
        gaps.append("The dossier has no signals yet, so there is nothing to corroborate.")
    if not dossier["summary"]:
        gaps.append("No scope note: the dossier does not yet state the question it answers.")
    return gaps


def _snapshot_payload(conn, dossier_id):
    """What a report contains right now, in a form the next export can diff."""
    items = sorted(row[0] for row in conn.execute(
        "SELECT item_hash FROM dossier_items WHERE dossier_id = ?", (dossier_id,)))
    notes = sorted(row[0] for row in conn.execute(
        "SELECT id FROM notes WHERE dossier_id = ?", (dossier_id,)))
    actions = {str(row[0]): row[1] for row in conn.execute(
        "SELECT id, status FROM actions WHERE dossier_id = ?", (dossier_id,))}
    return {"items": items, "notes": notes, "actions": actions}


def save_snapshot(conn, dossier_id, path=""):
    """Record what this export contained. Returns the new snapshot id."""
    payload = _snapshot_payload(conn, dossier_id)
    cursor = conn.execute(
        "INSERT INTO dossier_snapshots (dossier_id, created_at, path, payload) "
        "VALUES (?, ?, ?, ?)",
        (dossier_id, _now(), _clean(path, 400), json.dumps(payload)))
    conn.commit()
    return cursor.lastrowid


def latest_snapshot(conn, dossier_id):
    row = conn.execute(
        "SELECT id, created_at, path, payload FROM dossier_snapshots "
        "WHERE dossier_id = ? ORDER BY id DESC LIMIT 1", (dossier_id,)).fetchone()
    return dict(row) if row else None


def dossier_changes(conn, dossier_id, snapshot=None):
    """What was added, resolved or reopened since the last export of this dossier."""
    snapshot = snapshot or latest_snapshot(conn, dossier_id)
    empty = {"first_export": True, "since": "", "path": "", "new_signals": [],
             "new_notes": [], "new_actions": [], "resolved_actions": [],
             "reopened_actions": [], "counts": {"signals": 0, "notes": 0, "actions": 0,
                                                "resolved": 0, "reopened": 0}}
    if snapshot is None:
        return empty
    try:
        previous = json.loads(snapshot["payload"] or "{}")
    except ValueError:
        # An unreadable snapshot says nothing about the past. Reporting every signal as new
        # would be a guess; treating the export as the first one is not.
        return {**empty, "since": snapshot["created_at"] or "", "path": snapshot["path"] or ""}
    if (not isinstance(previous, dict)
            or not isinstance(previous.get("items"), list)
            or not isinstance(previous.get("notes"), list)
            or not isinstance(previous.get("actions"), dict)
            or not all(isinstance(value, str) for value in previous["items"])
            or not all(isinstance(value, int) for value in previous["notes"])
            or not all(isinstance(value, str) for value in previous["actions"].values())):
        return {**empty, "since": snapshot["created_at"] or "", "path": snapshot["path"] or ""}
    before_items = set(previous["items"])
    before_notes = set(previous["notes"])
    before_actions = previous["actions"]

    new_signals = []
    for item in dossier_items(conn, dossier_id):
        if item["item_hash"] in before_items:
            continue
        new_signals.append({
            "hash": item["item_hash"],
            "title": item.get("title") or "",
            "link": hbnscan.safe_link(item.get("link")),
            "source": item.get("source", ""),
            "severity": item.get("severity") or "none",
            "category": item.get("category") or "other",
            "date": (item.get("published") or "")[:10],
            "undated": not (item.get("published") or "").strip(),
        })
    new_notes = [{"body": note["body"], "created_at": note["created_at"]}
                 for note in list_notes(conn, dossier_id=dossier_id)
                 if note["id"] not in before_notes]
    actions = list_actions(conn, dossier_id=dossier_id)
    new_actions, resolved, reopened = [], [], []
    for action in actions:
        key = str(action["id"])
        if key not in before_actions:
            new_actions.append(action)
            continue
        was = before_actions[key]
        if was == action["status"]:
            continue
        if action["status"] in ("done", "dropped"):
            resolved.append({**action, "previous_status": was})
        else:
            reopened.append({**action, "previous_status": was})
    return {
        "first_export": False,
        "since": snapshot["created_at"] or "",
        "path": snapshot["path"] or "",
        "new_signals": new_signals,
        "new_notes": new_notes,
        "new_actions": new_actions,
        "resolved_actions": resolved,
        "reopened_actions": reopened,
        "counts": {"signals": len(new_signals), "notes": len(new_notes),
                   "actions": len(new_actions), "resolved": len(resolved),
                   "reopened": len(reopened)},
    }


def describe_changes(changes):
    """One line for the top of a report."""
    if not changes or changes.get("first_export"):
        return "First export: nothing to compare against yet."
    counts = changes["counts"]
    if not any(counts.values()):
        return f"No change since the export of {changes['since'].replace('T', ' ')}."
    parts = []
    for key, label in (("signals", "new signal(s)"), ("notes", "new note(s)"),
                       ("actions", "new action(s)"), ("resolved", "resolved"),
                       ("reopened", "reopened")):
        if counts[key]:
            parts.append(f"{counts[key]} {label}")
    return ", ".join(parts) + f" since the export of {changes['since'].replace('T', ' ')}"


def dossier_report(conn, ident, developments=5, since_last_export=False):
    """Build a cited report from stored signals, notes and actions. Returns a dict."""
    dossier = resolve_dossier(conn, ident)
    if dossier is None:
        raise ValueError(f"no dossier matches {ident!r}")
    items = _report_rows(conn, dossier["id"])
    notes = list_notes(conn, dossier_id=dossier["id"])
    actions = list_actions(conn, dossier_id=dossier["id"])
    # Changes are shown whenever a previous export exists; the flag exists for scripts that
    # want the section even on a first run, and to say so out loud.
    changes = (dossier_changes(conn, dossier["id"])
               if (since_last_export or latest_snapshot(conn, dossier["id"]))
               else None)

    ordered = sorted(items, key=lambda item: (_report_stamp(item) is None,
                                              _report_stamp(item) or datetime.max,
                                              item["item_hash"]))
    # Avoid datetime.min.timestamp(), which can fail on Windows.
    ranked = sorted(items, key=lambda item: item["item_hash"])
    ranked.sort(key=lambda item: _report_stamp(item) or datetime.min, reverse=True)
    ranked.sort(key=lambda item: {"high": 0, "medium": 1, "low": 2}.get(
        (item.get("severity") or "none").lower(), 3))
    evidence = _story_evidence(items)
    distinct_ranked, seen_clusters = [], set()
    for item in ranked:
        key = item.get("cluster_key") or "row:" + item["item_hash"]
        if key not in seen_clusters:
            distinct_ranked.append(item)
            seen_clusters.add(key)
    counts = {"signals": len(items), "high": 0, "medium": 0, "low": 0,
              "undated": 0, "deep_read": 0}
    for item in items:
        severity = (item.get("severity") or "none").lower()
        if severity in counts:
            counts[severity] += 1
        if _publication_stamp(item) is None:
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
            "date": _report_stamp(item).date().isoformat() if _report_stamp(item) else "",
            "undated": _publication_stamp(item) is None,
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
        "changes": changes,
        "counts": counts,
        "span": {"first": next((entry["date"] for entry in timeline if entry["date"]), ""),
                 "last": next((entry["date"] for entry in reversed(timeline) if entry["date"]), "")},
        "story_evidence": evidence,
        "sources": [{"host": host, "count": count}
                    for host, count in sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))],
        "developments": [
            {"severity": item.get("severity") or "none",
             "title": item.get("title") or "",
             "link": hbnscan.safe_link(item.get("link")),
             "source": item.get("source", ""),
             "published": (_publication_stamp(item).date().isoformat()
                           if _publication_stamp(item) else ""),
             "undated": _publication_stamp(item) is None,
             "freshness": item.get("freshness", ""),
             "assessment": hbnscan.strip_provenance(item.get("summary")),
             "action": item.get("action") or ""}
            for item in distinct_ranked[:max(1, int(developments))]
        ],
        "timeline": timeline,
        "notes": [{"body": note["body"], "created_at": note["created_at"]} for note in notes],
        "actions": actions,
        "gaps": _report_gaps(conn, items, dossier, evidence),
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
        f"{len(report['sources'])} delivery source(s)_",
        "",
    ]
    if dossier["status"] != "open":
        lines.extend([f"**Status:** {dossier['status']}", ""])
    lines.extend(["## Scope", "", _md(dossier["summary"]) if dossier["summary"]
                  else "_No scope note yet. Add one so the dossier states its question._", ""])

    changes = report.get("changes")
    if changes:
        lines.extend(["## What changed", "", _md(describe_changes(changes)), ""])
        if changes["new_signals"]:
            lines.append(f"**New signals ({len(changes['new_signals'])})**")
            lines.append("")
            for entry in changes["new_signals"]:
                stamp = entry["date"] or "undated"
                label = _md(entry["title"])
                link = _md_link(entry["link"])
                title = f"[{label}]({link})" if link else label
                lines.append(f"- {stamp} · **{_md(entry['severity'])}** · "
                             f"{_md(entry['category'])} · {title}")
            lines.append("")
        if changes["new_notes"]:
            lines.append("**New open questions**")
            lines.append("")
            lines.extend(f"- {_md(note['body'])}" for note in changes["new_notes"])
            lines.append("")
        if changes["new_actions"]:
            lines.append("**New actions**")
            lines.append("")
            lines.extend(f"- {_md(action['title'])} _({action['status']})_"
                         for action in changes["new_actions"])
            lines.append("")
        for key, label in (("resolved_actions", "Resolved actions"),
                           ("reopened_actions", "Reopened actions")):
            if changes[key]:
                lines.append(f"**{label}**")
                lines.append("")
                lines.extend(f"- {_md(action['title'])} _({action['previous_status']} → "
                             f"{action['status']})_" for action in changes[key])
                lines.append("")

    lines.extend(["## At a glance", ""])
    lines.append(f"- **{counts['signals']}** signal(s) · {counts['high']} high · "
                 f"{counts['medium']} medium · {counts['low']} low")
    lines.append(f"- **{counts['deep_read']}** linked articles read · "
                 f"**{counts['undated']}** undated")
    if report["span"]["first"]:
        lines.append(f"- Coverage: {report['span']['first']} → {report['span']['last']}")
    if report["sources"]:
        lines.append("- Delivery sources: " + ", ".join(
            f"{_md(entry['host'])} ({entry['count']})" for entry in report["sources"]))
    lines.append("")

    evidence = report.get("story_evidence")
    if evidence:
        lines.extend(["## Story coverage", "",
                      f"- {evidence['story_count']} story group(s); "
                      f"{evidence['multi_item_stories']} with multiple signals; "
                      f"{evidence['multi_host_stories']} with multiple article hosts.",
                      _md(evidence["caveat"]), ""])
        for story in evidence["stories"]:
            if story["signals"] > 1:
                hosts = ", ".join(story["article_hosts"]) or "unknown"
                lines.append(f"- {_md(story['title'])}: {story['signals']} signals; "
                             f"article hosts: {_md(hosts)}")
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


def render_report_html(report, source_path=None, download_name=""):
    dossier = report["dossier"]
    counts = report["counts"]
    esc = html_module.escape
    print_css, print_footer = hbnscan.print_footer_assets(source_path, download_name)
    changes = report.get("changes")
    changes_html = ""
    if changes:
        blocks = [f"<p>{esc(describe_changes(changes))}</p>"]
        if changes["new_signals"]:
            rows = "".join(
                "<li>"
                f"{esc(entry['date'] or 'undated')} · {esc(entry['severity'])} · "
                f"{esc(entry['category'])} · "
                + (f'<a href="{esc(entry["link"], quote=True)}">{esc(entry["title"])}</a>'
                   if entry["link"] else esc(entry["title"]))
                + "</li>"
                for entry in changes["new_signals"])
            blocks.append(f"<h3>New signals</h3><ul>{rows}</ul>")
        if changes["new_notes"]:
            blocks.append("<h3>New open questions</h3><ul>"
                          + "".join(f"<li>{esc(note['body'])}</li>"
                                    for note in changes["new_notes"]) + "</ul>")
        if changes["new_actions"]:
            blocks.append("<h3>New actions</h3><ul>"
                          + "".join(f"<li>{esc(action['title'])}</li>"
                                    for action in changes["new_actions"]) + "</ul>")
        for key, label in (("resolved_actions", "Resolved actions"),
                           ("reopened_actions", "Reopened actions")):
            if changes[key]:
                blocks.append(f"<h3>{label}</h3><ul>"
                              + "".join(f"<li>{esc(action['title'])} "
                                        f"({esc(action['previous_status'])} → "
                                        f"{esc(action['status'])})</li>"
                                        for action in changes[key]) + "</ul>")
        changes_html = f'<section class="changes"><h2>What changed</h2>{"".join(blocks)}</section>'
    else:
        changes_html = ""
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
    evidence = report.get("story_evidence")
    coverage_html = ""
    if evidence:
        rows = "".join(
            f"<li>{esc(story['title'])}: {story['signals']} signals; article hosts: "
            f"{esc(', '.join(story['article_hosts']) or 'unknown')}</li>"
            for story in evidence["stories"] if story["signals"] > 1)
        coverage_html = (
            f"<section><h2>Story coverage</h2><p>{evidence['story_count']} story group(s); "
            f"{evidence['multi_item_stories']} with multiple signals; "
            f"{evidence['multi_host_stories']} with multiple article hosts.</p>"
            f"<p>{esc(evidence['caveat'])}</p><ul>{rows}</ul></section>")
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
{print_css}</style></head><body>
<header><h1>{esc(dossier['name'])}</h1>
<p>{counts['signals']} signal(s) · {counts['high']} high · {counts['medium']} medium ·
{counts['low']} low · {esc(dossier['status'])} · generated {esc(report['generated'])}</p></header>
<main>
{changes_html}
<section><h2>Scope</h2><p>{esc(dossier['summary']) or '<em>No scope note yet.</em>'}</p>
<p class="meta">Coverage {esc(report['span']['first'])} → {esc(report['span']['last'])}
{('· ' + sources) if sources else ''}</p></section>
{coverage_html}
<section><h2>Key developments</h2>{''.join(development_cards) or '<p><em>No signals yet.</em></p>'}</section>
<section><h2>Timeline</h2><ul>{timeline or '<li><em>Nothing yet.</em></li>'}</ul></section>
<section><h2>Open questions</h2><ul>{notes}</ul></section>
<section><h2>Actions</h2><ul>{actions}</ul></section>
<section class="gap"><h2>Evidence gaps</h2><ul>{gaps}</ul></section>
<p class="meta">Generated from stored signals; no model call was made for this report.</p>
</main>{print_footer}</body></html>"""


def render_report_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def render_report(report, fmt="md", source_path=None, download_name=""):
    fmt = (fmt or "md").lower().lstrip(".")
    if fmt in ("markdown", "md"):
        return render_report_markdown(report)
    if fmt in ("html", "htm"):
        return render_report_html(report, source_path=source_path,
                                  download_name=download_name)
    if fmt == "json":
        return render_report_json(report)
    raise ValueError(f"unsupported report format {fmt!r}; use {', '.join(REPORT_FORMATS)}")


def export_report(conn, ident, path, fmt=None):
    """Write a dossier report to `path`. Returns (path, signal_count)."""
    target = Path(path)
    fmt = fmt or (target.suffix.lower().lstrip(".") or "md")
    report = dossier_report(conn, ident, since_last_export=True)
    # A printed dossier names the file it came from, like the signal export does.
    text = render_report(report, fmt, source_path=target)
    hbnscan.write_atomic(target, text)
    # Only after the file is on disk: the snapshot describes what was actually written.
    save_snapshot(conn, report["dossier"]["id"], target)
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


def _print_entity(report):
    if not report["signals"] and not report["dossiers"]:
        print(f"No stored signal or dossier mentions {report['query']!r}.")
        print("Entities come from the Stage 1 verdict, and dismissed rows are excluded; "
              "try a shorter fragment of the name, or run --reclassify to re-grade rows "
              "stored before entity extraction existed.")
        return
    print(f"{report['query']}")
    print(f"  {describe_entity(report)}")
    if report["sources"]:
        print("  sources: " + ", ".join(f"{entry['name']} ({entry['count']})"
                                        for entry in report["sources"][:6]))
    if report["counts"]["undated"]:
        print(f"  {report['counts']['undated']} signal(s) carry no publication date")
    if report.get("association_scope", {}).get("truncated"):
        print("  Results limited; association counts describe only the returned signals.")
    if report.get("associations"):
        print("  Co-mentioned entities (not established relationships):")
        for entry in report["associations"][:10]:
            print(f"    {entry['name']}: {entry['stories']} story group(s), "
                  f"{entry['signals']} signal(s)")
    print()
    if report["signals"]:
        print(f"{'DATE':<10}  {'FLAG':<4}  {'SEV':<6}  {'CATEGORY':<19}  {'SOURCE':<24}  "
              "HEADLINE")
        for item in report["signals"]:
            date = (item.get("published") or "")[:10] or "undated"
            flag = "*" if item.get("flagged") else ""
            print(f"{date:<10}  {flag:<4}  {(item.get('severity') or 'none'):<6}  "
                  f"{(item.get('category') or 'other'):<19}  "
                  f"{(item.get('source') or '')[:24]:<24}  {(item.get('title') or '')[:60]}")
    if report["dossiers"]:
        print()
        print("Dossiers")
        _print_dossier_rows(report["dossiers"])
    if report["actions"]:
        print()
        print("Actions")
        _print_actions(report["actions"])
    if report["notes"]:
        print()
        print("Notes")
        for note in report["notes"]:
            print(f"  {note['created_at'][:10]}  {(note['body'] or '')[:90]}")


# --------------------------------------------------------------------------- stories


def story_report(conn, cluster_key):
    """Everything stored about one story, or None when the key matches nothing."""
    rows = conn.execute(
        "SELECT item_hash, title, link, feed_url, published, seen_at, severity, category, "
        "summary, source_layer FROM seen_items WHERE cluster_key = ? "
        "ORDER BY COALESCE(NULLIF(published, ''), seen_at) ASC", (cluster_key,)).fetchall()
    if not rows:
        return None
    record = hbnscan.story_record(conn, cluster_key)
    return {
        "key": cluster_key,
        "title": rows[0]["title"] or "",
        "record": record.to_dict() if record else None,
        "summary": hbnscan.cluster_summary(conn, cluster_key),
        "sources": hbnscan.story_sources(conn, cluster_key),
        "relations": hbnscan.story_relations(conn, cluster_key),
        "related": hbnscan.related_stories(conn, cluster_key),
        "overrides": hbnscan.story_overrides(conn, cluster_key),
        "members": [dict(row) for row in rows],
    }


def _print_story(report):
    sources = report["sources"]
    print(f"Story: {report['title']}")
    print(f"  key: {report['key']}")
    print(f"  {sources['occurrences']} stored occurrence(s); "
          f"{len(sources['delivery_hosts'])} delivery host(s); "
          f"{len(sources['publisher_hosts'])} publisher host(s)")
    print(f"  independence: {sources['independence']} - {sources['note']}")
    relations = report["relations"]
    if relations:
        print("  relationships:")
        for entry in relations:
            print(f"    {entry['relation']:14} {entry['other_hash'][:12]}  "
                  f"score {entry['score']:.2f}  via {entry['method'] or 'unknown'}")
    print("  members:")
    for member in report["members"]:
        print(f"    {member['item_hash'][:12]}  {member['severity'] or '':6} "
              f"{(member['published'] or member['seen_at'] or '')[:10]}  "
              f"{(member['title'] or '')[:70]}")
    if report["related"]:
        print("  related stories (not merged):")
        for entry in report["related"]:
            print(f"    {entry['cluster_key']}  shares {', '.join(entry['shared'][:4])}")
    if report["overrides"]:
        print("  manual corrections:")
        for entry in report["overrides"]:
            print(f"    {entry['action']} {entry['item_hash'][:12]} "
                  f"from {entry['previous_key'] or '(none)'}  {entry['note']}")


def main(argv):
    """`hbnscan.py dossier|action|entity|sources ...` - the same workspace from a shell."""
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
    show.add_argument("--since-last-export", action="store_true", dest="since_last_export",
                      help="lead with what changed since the previous export")
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
    report.add_argument("--since-last-export", action="store_true",
                        dest="since_last_export",
                        help="lead with what changed since the previous export")
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

    entity = sub.add_parser("entity",
                            help="everything stored about one company, regulator or person")
    entity.add_argument("query", help="part of an entity name, case-insensitive")
    entity.add_argument("--json", action="store_true", help="machine-readable output")
    entity.add_argument("--limit", type=int, default=ENTITY_LIMIT,
                        help=f"maximum signals to list (default {ENTITY_LIMIT})")

    sources = sub.add_parser("sources", help="source health and coverage")
    sources.add_argument("--json", action="store_true", help="machine-readable output")

    story = sub.add_parser("story", help="story grouping: inspect it, correct it, undo the edit")
    story_sub = story.add_subparsers(dest="action", required=True)
    story_show = story_sub.add_parser("show", help="members, relationships and independence")
    story_show.add_argument("key", help="story key as shown in the grid or a signal")
    story_show.add_argument("--json", action="store_true", help="machine-readable output")
    story_merge = story_sub.add_parser("merge", help="move one story into another")
    story_merge.add_argument("from_key")
    story_merge.add_argument("into_key")
    story_merge.add_argument("--note", default="", help="why the merge was made")
    story_split = story_sub.add_parser("split", help="take one signal out of its story")
    story_split.add_argument("item_hash")
    story_split.add_argument("--note", default="", help="why the split was made")
    story_undo = story_sub.add_parser("undo", help="restore the story a merge or split moved")
    story_undo.add_argument("target", help="an item hash, or a story key with --all")
    story_undo.add_argument("--all", action="store_true", dest="all_in_story",
                            help="restore every corrected row in that story")

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

        if args.command == "story":
            if args.action == "show":
                report = story_report(conn, args.key)
                if report is None:
                    print(f"[FAIL] no stored signals in story {args.key!r}")
                    return 2
                if args.json:
                    print(json.dumps(report, indent=2, ensure_ascii=False))
                else:
                    _print_story(report)
                return 0
            if args.action == "merge":
                try:
                    moved = hbnscan.merge_stories(conn, args.from_key, args.into_key,
                                                  note=args.note)
                except ValueError as exc:
                    print(f"[FAIL] {exc}")
                    return 2
                print(f"[OK] {moved} signal(s) moved into {args.into_key}"
                      " (undo with: story undo <story> --all)")
                return 0
            if args.action == "split":
                try:
                    key = hbnscan.split_from_story(conn, args.item_hash, note=args.note)
                except ValueError as exc:
                    print(f"[FAIL] {exc}")
                    return 2
                print(f"[OK] {args.item_hash} now stands alone as {key}")
                return 0
            if args.action == "undo":
                try:
                    restored = hbnscan.undo_story_change(
                        conn,
                        item_hash_value="" if args.all_in_story else args.target,
                        cluster_key=args.target if args.all_in_story else "")
                except ValueError as exc:
                    print(f"[FAIL] {exc}")
                    return 2
                if not restored:
                    print("[OK] nothing to undo: that signal has no manual correction")
                    return 0
                print(f"[OK] restored {restored} signal(s) to their previous story")
                return 0

        if args.command == "entity":
            report = entity_report(conn, args.query, args.limit)
            if args.json:
                print(json.dumps(report, indent=2, ensure_ascii=False))
            else:
                _print_entity(report)
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
                report = dossier_report(conn, args.dossier,
                                        since_last_export=getattr(args, "since_last_export",
                                                                  False))
                print(render_report_markdown(report), end="")
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
                since = getattr(args, "since_last_export", False)
                if not args.export:
                    report = dossier_report(conn, row["id"], since_last_export=since)
                    print(render_report(report, args.format or "md"), end="")
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
