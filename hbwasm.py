"""HB Briefing in a browser tab: the same engine, compiled to WebAssembly by Pyodide.

Nothing here is a reimplementation. `analyse()` calls the engine's own
`heuristic_verdict()`, `story_keys()` and `hbsimilar` pass, so a verdict produced in the browser
is the verdict the desktop app would produce from the same inputs. That is the whole point: a
machine with no Python, no install, no network and no API key can still open an exported
briefing, re-rank it, group rewrites, and walk away with a report.

The module is plain standard library so it runs under CPython too (the tests do exactly that)
and under Pyodide, where `python-dotenv` is a wheel away and not worth the download.
"""
import csv
import io
import json
import sys
import types
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlsplit

import hbsimilar

MAX_ITEMS = 5000
TEXT_LIMIT = 20000
ORDERS = ("priority", "relevance", "newest", "oldest", "detected")
ROW_FIELDS = ("title", "link", "source", "feed_url", "published", "freshness", "category",
              "severity", "layer", "priority", "flagged", "confidence", "assessment",
              "action", "reason", "cluster", "cluster_size", "similarity", "undated")

_ENGINE = None


def engine():
    """Import the engine, supplying a no-op dotenv when the wheel is not installed.

    A browser tab has no `.env`, and Pyodide should not have to download a package to read a
    file that cannot exist there. Every other environment keeps the real module.
    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    try:
        import dotenv  # noqa: F401  (presence check only)
    except ImportError:
        shim = types.ModuleType("dotenv")
        shim.load_dotenv = lambda *args, **kwargs: False
        shim.dotenv_values = lambda *args, **kwargs: {}
        sys.modules["dotenv"] = shim
    import hbnscan
    _ENGINE = hbnscan
    return hbnscan


def _text(value, limit=TEXT_LIMIT):
    return " ".join(str(value or "").split())[:limit]


def _host(url):
    """The publisher name shown in the grid: a host, never a full URL."""
    try:
        return (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        return ""


# --------------------------------------------------------------------- input


def _item(raw):
    """Normalise one input record: a feed entry, an export row or a bare headline."""
    if isinstance(raw, str):
        raw = {"title": raw}
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title") or raw.get("name") or "", 300)
    description = _text(raw.get("description") or raw.get("summary") or raw.get("excerpt")
                        or raw.get("content") or "")
    link = _text(raw.get("link") or raw.get("url") or "", 2000)
    feed_url = _text(raw.get("feed_url") or raw.get("feed") or raw.get("source_url") or "", 2000)
    published = _text(raw.get("published") or raw.get("date") or raw.get("pubDate") or "", 40)
    if not title and not description:
        return None
    return {
        "title": title,
        "description": description,
        "link": link,
        "feed_url": feed_url,
        "published": published,
        "seen_at": _text(raw.get("seen_at") or raw.get("detected") or "", 40),
        # Present only when the record came from a desktop export.
        "category": _text(raw.get("category") or "", 60),
        "severity": _text(raw.get("severity") or "", 20),
        "layer": _text(raw.get("source_layer") or raw.get("layer") or "", 30),
        "action": _text(raw.get("action") or "", 500),
        "summary": _text(raw.get("summary") or ""),
        "confidence": raw.get("confidence"),
        "deep_read": int(raw.get("deep_read") or 0),
        "exported": bool(raw.get("category") or raw.get("severity")),
    }


def parse_opml(text):
    """Feed URLs from an OPML export, so the page can offer to fetch them."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"malformed OPML: {exc}") from None
    feeds = []
    for outline in root.iter("outline"):
        url = (outline.attrib.get("xmlUrl") or "").strip()
        if url:
            feeds.append({"title": (outline.attrib.get("title")
                                    or outline.attrib.get("text") or url).strip(), "url": url})
    return feeds


def parse_feed(text, feed_url=""):
    """Entries from RSS or Atom pasted or fetched by the page."""
    engine_module = engine()
    raw = text.encode("utf-8", "replace") if isinstance(text, str) else text
    try:
        entries = engine_module._parse_feed(raw, feed_url or "pasted feed")
    except engine_module.FeedError as exc:
        raise ValueError(str(exc)) from None
    items = []
    for entry in entries:
        entry.setdefault("feed_url", feed_url)
        normalised = _item(entry)
        if normalised:
            items.append(normalised)
    return items


def _from_json(payload):
    data = json.loads(payload)
    if isinstance(data, dict):
        for key in ("rows", "items", "signals", "entries"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of items or an object with a 'rows' list")
    return [item for item in (_item(raw) for raw in data) if item]


def _from_csv(payload):
    reader = csv.DictReader(io.StringIO(payload))
    if not reader.fieldnames:
        raise ValueError("the CSV has no header row")
    lowered = {name.lower().strip(): name for name in reader.fieldnames if name}
    reader.fieldnames = [lowered.get((name or "").lower().strip(), name)
                         for name in reader.fieldnames]
    return [item for item in (_item(row) for row in reader) if item]


def load_document(text, filename=""):
    """Work out what the user dropped and turn it into items.

    Order matters: an export is JSON or CSV, a feed is XML, an OPML file is XML with a
    different root. Anything else is treated as one headline per line, which is the fastest
    way to paste a list of topics and see what the engine thinks of it.
    """
    text = (text or "")[:TEXT_LIMIT + 1]
    if not text.strip():
        raise ValueError("nothing to analyse: the file is empty")
    stripped = text.lstrip()
    notes = []
    if stripped.startswith("{") or stripped.startswith("["):
        return {"kind": "export", "items": _from_json(text), "feeds": [], "notes": notes}
    if stripped.startswith("<"):
        if "<opml" in stripped[:400].lower():
            feeds = parse_opml(text)
            return {"kind": "opml", "items": [], "feeds": feeds,
                    "notes": [f"{len(feeds)} feed(s) in the OPML file. Fetch them, or paste a "
                              "feed's XML to analyse it."]}
        return {"kind": "feed", "items": parse_feed(text, filename), "feeds": [],
                "notes": notes}
    lowered = stripped.splitlines()[0].lower()
    if "," in lowered and ("title" in lowered or "link" in lowered):
        return {"kind": "csv", "items": _from_csv(text), "feeds": [], "notes": notes}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    items = []
    for line in lines:
        if line.startswith("#"):
            continue
        items.append({"title": _text(line, 300), "description": "", "link": "", "feed_url": "",
                      "published": "", "seen_at": "", "category": "", "severity": "",
                      "layer": "", "action": "", "summary": "", "confidence": None,
                      "deep_read": 0, "exported": False})
    return {"kind": "lines", "items": items, "feeds": [],
            "notes": [f"{len(items)} headline(s) read as one per line."]}


# --------------------------------------------------------------------- analysis


def _row(engine_module, item, profile, rescore):
    title = item["title"]
    description = item.get("description") or ""
    verdict = None
    if rescore or not item.get("exported"):
        verdict = engine_module.heuristic_verdict(title, description, item.get("feed_url", ""),
                                                  profile)
    category = (verdict or {}).get("category") or item.get("category") or "other"
    severity = (verdict or {}).get("severity") or item.get("severity") or "none"
    layer = (verdict or {}).get("layer") or item.get("layer") or "imported"
    analysis = (verdict or {}).get("analysis") or item.get("summary") or ""
    action = (verdict or {}).get("action") or item.get("action") or ""
    reason = (verdict or {}).get("reason") or ""
    flagged = (verdict or {}).get("flagged")
    if flagged is None:
        flagged = 1 if severity not in ("", "none") else 0
    published = item.get("published") or ""
    source = _host(item.get("feed_url") or item.get("link") or "")
    cluster_key, title_key = engine_module.story_keys(title, item.get("link") or "")
    return {
        "title": title,
        "link": engine_module.safe_link(item.get("link") or ""),
        "source": source,
        "published": published[:10],
        "freshness": engine_module.freshness_label(published, item.get("seen_at") or ""),
        "category": category,
        "severity": severity,
        "layer": layer,
        "priority": engine_module.priority_of(layer, category, analysis),
        "flagged": int(bool(flagged)),
        "confidence": (verdict or {}).get("confidence") or item.get("confidence"),
        "assessment": engine_module.strip_provenance(analysis),
        "action": action,
        "reason": reason,
        "cluster": cluster_key,
        "cluster_size": 1,
        "similarity": 0.0,
        "undated": not published,
        # Internal only: the fields grouping needs but the table and exports do not show.
        "title_key": title_key,
        "excerpt": description or analysis,
    }


def _group_exact(rows):
    """Merge rows sharing a URL identity or a normalised headline, as the desktop does.

    The first row of a story becomes the anchor and later rows are repointed at it, so the
    group survives either key: the same URL from two feeds, or the same headline under two
    URLs.
    """
    by_cluster = {}
    by_title = {}
    for row in rows:
        anchor = by_cluster.get(row["cluster"])
        if anchor is None and row["title_key"]:
            anchor = by_title.get(row["title_key"])
        if anchor is None:
            anchor = row["cluster"]
            by_cluster.setdefault(anchor, anchor)
        else:
            row["cluster"] = anchor
        by_cluster.setdefault(row["cluster"], anchor)
        if row["title_key"]:
            by_title.setdefault(row["title_key"], anchor)
    return rows


def _group_similar(rows, threshold):
    """Group rewritten wire copy by MinHash/LSH, then exact Jaccard."""
    if threshold <= 0 or len(rows) < 2:
        return rows
    bands = {}
    texts = {}
    for row in rows:
        text = hbsimilar.document_text(row["title"], row.get("excerpt", ""))
        shingles = hbsimilar.shingles(text)
        if len(shingles) < 8:
            continue
        texts[row["cluster"]] = shingles
        for band, key in hbsimilar.bands(hbsimilar.signature(shingles)):
            bands.setdefault((band, key), []).append(row["cluster"])
    for members in bands.values():
        unique = list(dict.fromkeys(members))
        if len(unique) < 2:
            continue
        anchor = unique[0]
        for other in unique[1:]:
            if anchor not in texts or other not in texts:
                continue
            score = hbsimilar.jaccard(texts[anchor], texts[other])
            if score < threshold:
                continue
            for row in rows:
                if row["cluster"] != other:
                    continue
                row["cluster"] = anchor
                row["similarity"] = round(score, 3)
                texts[anchor] = texts[anchor] | texts[other]
    _recount(rows)
    return rows


def _recount(rows):
    """One pass to fill in cluster sizes after any grouping."""
    sizes = {}
    for row in rows:
        sizes[row["cluster"]] = sizes.get(row["cluster"], 0) + 1
    for row in rows:
        row["cluster_size"] = sizes[row["cluster"]]
    return rows


def _sort(engine_module, rows, order):
    if order in ("newest", "oldest"):
        reverse = order == "newest"

        def key(row):
            return (row["published"] or row["freshness"] or "", row["title"])
        return sorted(rows, key=key, reverse=reverse)
    severity_rank = {"high": 0, "medium": 1, "low": 2, "none": 3}
    priority_rank = {"watchlist": 0, "topic": 1, "core": 2, "context": 3}
    return sorted(rows, key=lambda row: (priority_rank.get(row["priority"], 9),
                                         severity_rank.get(row["severity"], 9),
                                         row["title"]))


def summarise(rows):
    def counts(key):
        out = {}
        for row in rows:
            out[row[key]] = out.get(row[key], 0) + 1
        return dict(sorted(out.items(), key=lambda pair: (-pair[1], pair[0])))

    dates = sorted(row["published"] for row in rows if row["published"])
    multi = {row["cluster"]: row["cluster_size"] for row in rows if row["cluster_size"] > 1}
    return {
        "items": len(rows),
        "flagged": sum(1 for row in rows if row["flagged"]),
        "undated": sum(1 for row in rows if row["undated"]),
        "stories": len({row["cluster"] for row in rows}),
        # Members beyond the first in each grouped story: 3 pairs read as 3 rewrites.
        "rewrites": sum(multi.values()) - len(multi),
        "by_severity": counts("severity"),
        "by_category": counts("category"),
        "by_layer": counts("layer"),
        "by_source": dict(list(counts("source").items())[:12]),
        "span": {"first": dates[0] if dates else "", "last": dates[-1] if dates else ""},
    }


def analyse(text, filename="", rescore=None, threshold=None, order="priority", profile=None):
    """The one call the page makes: text in, table plus summary out."""
    engine_module = engine()
    if profile is None:
        profile = engine_module.Profile.from_mapping(engine_module.DEFAULT_PROFILE)
    elif isinstance(profile, str):
        profile = engine_module.Profile.from_mapping(json.loads(profile))
    document = load_document(text, filename)
    items = document["items"][:MAX_ITEMS]
    if rescore is None:
        # An export keeps the verdicts it was exported with; anything else is scored here.
        rescore = not any(item.get("exported") for item in items)
    threshold = hbsimilar.DEFAULT_THRESHOLD if threshold is None else float(threshold)
    rows = [_row(engine_module, item, profile, rescore) for item in items]
    _group_exact(rows)
    _recount(rows)
    _group_similar(rows, threshold)
    rows = _sort(engine_module, rows, order)
    for row in rows:
        row.pop("title_key", None)
        row.pop("excerpt", None)
    notes = list(document["notes"])
    if len(document["items"]) > MAX_ITEMS:
        notes.append(f"Only the first {MAX_ITEMS} items were analysed.")
    return {
        "kind": document["kind"],
        "generated": datetime.now().isoformat(timespec="seconds"),
        "rescored": bool(rescore),
        "threshold": threshold,
        "rows": rows,
        "summary": summarise(rows),
        "feeds": document["feeds"],
        "notes": notes,
    }


# --------------------------------------------------------------------- output


def to_csv(rows):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(ROW_FIELDS), extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in ROW_FIELDS})
    return buffer.getvalue()


def to_json(result):
    return json.dumps(result, indent=2, ensure_ascii=False)


def to_markdown(result):
    """A briefing-style digest: the counts first, then one row per story."""
    engine_module = engine()
    rows = result["rows"]
    summary = result["summary"]
    lines = [
        "# HB Briefing (browser session)",
        "",
        f"_{result['generated'].replace('T', ' ')} · {summary['items']} item(s) · "
        f"{summary['stories']} story(ies) · {summary['flagged']} flagged_",
        "",
        "## At a glance",
        "",
        "| Severity | Items |",
        "| --- | --- |",
    ]
    for severity, count in summary["by_severity"].items():
        lines.append(f"| {engine_module._md_text(severity)} | {count} |")
    if summary["by_category"]:
        lines.append("")
        lines.append("| Category | Items |")
        lines.append("| --- | --- |")
        for category, count in summary["by_category"].items():
            lines.append(f"| {engine_module._md_text(category)} | {count} |")
    if summary["span"]["first"]:
        lines.extend(["", f"Coverage: {summary['span']['first']} → {summary['span']['last']}"])
    if summary["undated"]:
        lines.extend(["", f"{summary['undated']} item(s) carry no publication date."])
    lines.extend(["", "## Signals", ""])
    for row in rows[:200]:
        label = engine_module._md_text(row["title"])
        link = engine_module._md_url(row["link"])
        title = f"[{label}]({link})" if link else label
        stamp = row["published"] or "undated"
        lines.append(f"- **{engine_module._md_text(row['severity'])}** · "
                     f"{engine_module._md_text(row['category'])} · {stamp} · {title}")
        if row["assessment"]:
            lines.append(f"  - {engine_module._md_text(row['assessment'])}")
        if row["cluster_size"] > 1:
            lines.append(f"  - Grouped with {row['cluster_size'] - 1} rewrite(s)")
    if len(rows) > 200:
        lines.extend(["", f"_{len(rows) - 200} more item(s) are in the table._"])
    lines.extend(["", "_Produced in the browser: no data left this machine._", ""])
    return "\n".join(lines)


def sample():
    """A small offline demo so the page works before anyone finds a file.

    The first two entries are the same wire story under two headlines, which is exactly the
    case the similarity pass exists to catch.
    """
    body = ("The regulator said the carrier billed subscribers for routers and set-top boxes "
            "that had already been returned, and that the practice continued for eighteen "
            "months after an internal review flagged it. The complaint seeks refunds for "
            "affected households, a civil penalty and an order stopping the practice. The "
            "carrier said it would respond in court and disputed the timeline.")
    return json.dumps([
        {"title": "FTC sues Acme Fiber over hidden router fees",
         "link": "https://example.org/ftc-acme",
         "description": body,
         "published": "2026-11-12"},
        {"title": "Regulator takes Acme Fiber to court over router fee billing",
         "link": "https://example.com/wire-rewrite",
         "description": body,
         "published": "2026-11-12"},
        {"title": "FCC opens proceeding on the 3.7 GHz band",
         "link": "https://example.org/fcc-37",
         "description": "The commission seeks comment on expanding flexible use of the band, "
                        "including whether to add co-primary allocations for fixed wireless.",
         "published": "2026-11-10"},
        {"title": "Retailer recalls magnetic toys after injuries",
         "link": "https://example.org/recall",
         "description": "A retailer expanded a recall of magnetic building sets after three "
                        "reported injuries, the safety commission said.",
         "published": ""},
    ], indent=2)
