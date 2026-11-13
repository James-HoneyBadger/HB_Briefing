#!/usr/bin/env python3
"""
HB Briefing v1.0.0 - Terminal UI

A curses front end for machines without a desktop: same engine, same database, same schedule.
It shows the signal table with severity colours, filters, search and a detail pane, and can run,
cancel and schedule scans without leaving the terminal.

    python3 hbnscan.py --tui
    python3 hbntui.py

Keys:  ↑/↓ move · Enter detail · d dismiss · r run · x stop · a schedule on/off ·
       i interval · f severity · c category · p priority · o order · / search ·
       b briefing · l log · e export · R reload · q quit
"""
import argparse
import collections
import curses
import json
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import hbnscan

SEVERITY_CYCLE = ("all", "high", "medium", "low")
GROUP_CYCLE = ("all", "watchlist", "topic", "core", "context")
ORDER_CYCLE = ("priority", "newest", "oldest", "severity", "title", "category")
INTERVAL_CYCLE = (5, 10, 15, 30, 60, 120, 240)
PAGE = 200
SIGNAL_COLUMNS = ("item_hash", "title", "link", "severity", "category", "summary",
                  "action", "facts", "entities", "confidence", "deep_read", "published",
                  "seen_at", "feed_url")


# --------------------------------------------------------------------------- state


class State:
    """Everything the screen draws, filled from the engine on demand."""

    def __init__(self):
        self.settings = hbnscan.load_settings()
        self.rows = []
        self.total = 0
        self.index = 0
        self.query = ""
        self.severity = self.settings["filter_severity"]
        self.category = self.settings["filter_category"]
        self.group = self.settings["filter_group"]
        self.order = self.settings["row_order"]
        self.detail = None
        self.pane = "detail"                  # detail | briefing | log
        self.briefing = ("", "")
        self.log = collections.deque(maxlen=400)
        self.scanning = False
        self.status = ""
        self.last_started = None
        self.running = True
        self.pending = ""
        self.confirm = ""

    # -- data ---------------------------------------------------------------
    def filters(self):
        return {"severity": self.severity, "category": self.category,
                "group": self.group, "query": self.query, "limit": PAGE}

    def reload(self):
        where, params, limit = hbnscan._export_filters(self.filters())
        order = hbnscan.row_order(self.order)
        columns = ", ".join(SIGNAL_COLUMNS)
        try:
            conn = sqlite3.connect(hbnscan.DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                self.rows = [dict(row) for row in conn.execute(
                    f"SELECT {columns} FROM seen_items WHERE {where} "
                    f"ORDER BY {order} LIMIT ?", (*params, limit))]
                self.total = conn.execute(
                    f"SELECT COUNT(*) FROM seen_items WHERE {where}", params).fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.rows, self.total = [], 0
            self.status = f"database error: {exc}"
        self.index = max(0, min(self.index, len(self.rows) - 1))
        self.load_detail()

    def load_detail(self):
        self.detail = self.rows[self.index] if self.rows else None

    def load_briefing(self):
        try:
            conn = sqlite3.connect(hbnscan.DB_PATH, timeout=5)
            try:
                content, created = hbnscan.latest_briefing(conn)
            finally:
                conn.close()
        except sqlite3.Error:
            content, created = "", ""
        self.briefing = (content or "", created or "")

    def current(self):
        return self.rows[self.index] if self.rows else None

    # -- schedule -----------------------------------------------------------
    def toggle_schedule(self):
        self.settings = hbnscan.save_settings(auto_scan=not self.settings["auto_scan"])
        self.last_started = None
        self.status = f"schedule: {hbnscan.describe_schedule(self.settings)}"

    def cycle_interval(self):
        current = int(self.settings["scan_interval_minutes"])
        try:
            nxt = INTERVAL_CYCLE[(INTERVAL_CYCLE.index(current) + 1) % len(INTERVAL_CYCLE)]
        except ValueError:
            nxt = INTERVAL_CYCLE[3]
        self.settings = hbnscan.save_settings(scan_interval_minutes=nxt)
        self.last_started = None
        self.status = f"schedule: {hbnscan.describe_schedule(self.settings)}"


def scan_worker(state, cancel):
    def log(line):
        text = " ".join(str(line).split())
        if text:
            state.log.append(text)

    try:
        new_count, flagged = hbnscan.run_scan(log_callback=log, cancel=cancel)
        state.status = f"scan complete: {new_count} new, {flagged} flagged"
    except Exception as exc:                                  # noqa: BLE001
        state.status = f"scan failed: {type(exc).__name__}: {exc}"
    finally:
        state.scanning = False


def start_scan(state):
    if state.scanning:
        return
    state.scanning = True
    state.last_started = datetime.now()
    state.status = "scanning…"
    cancel = threading.Event()
    state.cancel = cancel
    threading.Thread(target=scan_worker, args=(state, cancel), daemon=True).start()


# --------------------------------------------------------------------------- drawing


def sev_attr(severity):
    return {"high": curses.color_pair(1) | curses.A_BOLD,
            "medium": curses.color_pair(2) | curses.A_BOLD,
            "low": curses.color_pair(3)}.get(severity, curses.color_pair(3))


def priority_tag(summary):
    for prefix in ("[Watchlist]", "[Topic]"):
        if (summary or "").startswith(prefix):
            return prefix.strip("[]").lower()
    return ""


def clean(summary):
    text = summary or ""
    for prefix in ("[Watchlist] ", "[Topic] ", "[Local Heuristic] "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def addstr(screen, y, x, text, attr=0, width=None):
    """addstr that never raises at the edge of the window."""
    height, cols = screen.getmaxyx()
    if y < 0 or y >= height or x >= cols:
        return
    limit = max(0, (width if width is not None else cols - x))
    try:
        screen.addnstr(y, x, str(text), min(limit, cols - x), attr)
    except curses.error:
        pass


def draw(screen, state):
    screen.erase()
    height, width = screen.getmaxyx()
    head = (f" HB BRIEFING  ·  {hbnscan.APP_NAME}  ·  {state.total} flagged"
            f"  ·  {'AI ' + hbnscan.LLM_MODEL if hbnscan.deepseek_configured() else 'offline heuristic'}")
    addstr(screen, 0, 0, head, curses.A_BOLD, width)
    schedule = hbnscan.describe_schedule(state.settings)
    dot = "●" if state.scanning else "○"
    right = f"{dot} {schedule}  "
    addstr(screen, 0, max(0, width - len(right) - 1), right,
           curses.color_pair(4) if state.scanning else curses.A_DIM)
    addstr(screen, 1, 0, "─" * width, curses.A_DIM)

    filters = (f" sev:{state.severity}  cat:{state.category}  priority:{state.group}"
               f"  order:{state.order}" + (f"  search:“{state.query}”" if state.query else ""))
    addstr(screen, 2, 0, filters, curses.color_pair(2), width)
    addstr(screen, 3, 0, "─" * width, curses.A_DIM)

    pane_h = 10 if height > 18 else max(3, height // 3)
    table_top = 4
    table_bottom = height - pane_h - 3
    visible = max(1, table_bottom - table_top)

    if state.index < len(state.rows):
        top = max(0, min(state.index, len(state.rows) - visible))
    else:
        top = 0
    for offset, row in enumerate(state.rows[top:top + visible]):
        y = table_top + offset
        selected = (top + offset) == state.index
        severity = (row["severity"] or "none").lower()
        tag = priority_tag(row["summary"])
        title = row["title"] or "(untitled)"
        when = (row["seen_at"] or "").replace("T", " ")[:16]
        mark = "▸" if selected else " "
        attr = curses.A_REVERSE if selected else 0
        addstr(screen, y, 0, mark, attr)
        addstr(screen, y, 2, f"{severity.upper():6}", sev_attr(severity) | attr)
        addstr(screen, y, 9, f"{tag:9}", curses.color_pair(4) | attr, 9)
        addstr(screen, y, 19, f"{(row['category'] or ''):18}", curses.A_DIM | attr, 18)
        addstr(screen, y, 38, f"{when:16}", curses.A_DIM | attr, 16)
        addstr(screen, y, 56, title, attr, width - 57)

    if not state.rows:
        addstr(screen, table_top + 1, 2, "No signals match these filters.", curses.A_DIM)

    addstr(screen, table_bottom, 0, "─" * width, curses.A_DIM)
    pane_title = {"detail": " DETAIL ", "briefing": " BRIEFING ", "log": " SCAN LOG "}[state.pane]
    addstr(screen, table_bottom, 2, pane_title, curses.color_pair(2) | curses.A_BOLD)
    body_h = height - table_bottom - 3
    if state.pane == "detail":
        row = state.detail
        if row:
            line = 0
            lines = [("Assessment: ", clean(row["summary"]), 0)]
            if row["action"]:
                lines.append(("Next step: ", row["action"], 0))
            if row["published"]:
                lines.append(("Published: ", str(row["published"])[:10], 0))
            try:
                facts = json.loads(row["facts"] or "[]")
            except ValueError:
                facts = []
            for fact in facts[:3]:
                lines.append(("• ", str(fact), 0))
            try:
                entities = json.loads(row["entities"] or "{}")
            except ValueError:
                entities = {}
            if entities:
                rendered = "; ".join(f"{k}: {', '.join(v)}" for k, v in entities.items()
                                     if isinstance(v, list))
                lines.append(("Entities: ", rendered, 0))
            lines.append(("Link: ", hbnscan.safe_link(row["link"]), 0))
            for label, value, _ in lines:
                if line >= body_h:
                    break
                addstr(screen, table_bottom + 1 + line, 2, label, curses.A_DIM, 14)
                addstr(screen, table_bottom + 1 + line, 16, value, 0, width - 18)
                line += 1
        else:
            addstr(screen, table_bottom + 1, 2, "Nothing selected.", curses.A_DIM)
    elif state.pane == "briefing":
        content, created = state.briefing
        if not content:
            addstr(screen, table_bottom + 1, 2,
                   "No briefing yet — one is written when a scan runs with an LLM key.",
                   curses.A_DIM)
        else:
            addstr(screen, table_bottom + 1, 2, f"briefed {created}", curses.A_DIM)
            for i, text in enumerate(content.splitlines()[:body_h - 1]):
                addstr(screen, table_bottom + 2 + i, 2, text, 0, width - 4)
    else:
        lines = list(state.log)[-body_h:]
        for i, text in enumerate(lines):
            addstr(screen, table_bottom + 1 + i, 2, text, curses.A_DIM, width - 4)

    footer = (" ↑↓ move · Enter detail · d dismiss · r run · x stop · a schedule · i interval · "
              "f sev · c cat · p prio · o order · / search · b briefing · l log · e export · q quit")
    addstr(screen, height - 2, 0, footer[:width - 1], curses.A_DIM)
    if state.pending == "search":
        addstr(screen, height - 1, 0, "search: " + state.confirm[:width - 9], curses.A_BOLD)
    elif state.pending == "dismiss":
        addstr(screen, height - 1, 0, "dismiss this signal? y/n ", curses.A_BOLD)
    else:
        addstr(screen, height - 1, 0, state.status[:width - 1],
               curses.color_pair(2) if state.status else curses.A_DIM)
    screen.refresh()


# --------------------------------------------------------------------------- keys


def handle_key(state, key):
    row = state.current()
    if state.pending == "search":
        if key in (10, 13):
            state.query = state.confirm.strip()
            state.pending = ""
            state.index = 0
            state.reload()
        elif key == 27:
            state.pending = ""
        elif key in (curses.KEY_BACKSPACE, 127, 8):
            state.confirm = state.confirm[:-1]
        elif 32 <= key < 127:
            state.confirm += chr(key)
        return
    if state.pending == "dismiss":
        state.pending = ""
        if chr(key).lower() == "y" and row:
            try:
                conn = sqlite3.connect(hbnscan.DB_PATH, timeout=5)
                try:
                    hbnscan.dismiss_item(conn, row["item_hash"])
                finally:
                    conn.close()
                state.status = "dismissed"
                state.reload()
            except sqlite3.Error as exc:
                state.status = f"dismiss failed: {exc}"
        return

    if key in (ord("q"),):
        state.running = False
    elif key in (curses.KEY_DOWN, ord("j")):
        if state.rows:
            state.index = min(state.index + 1, len(state.rows) - 1)
            state.load_detail()
    elif key in (curses.KEY_UP, ord("k")):
        if state.rows:
            state.index = max(0, state.index - 1)
            state.load_detail()
    elif key in (curses.KEY_NPAGE,):
        state.index = min(state.index + 10, max(0, len(state.rows) - 1))
        state.load_detail()
    elif key in (curses.KEY_PPAGE,):
        state.index = max(0, state.index - 10)
        state.load_detail()
    elif key in (10, 13):
        state.pane = "detail"
    elif key == ord("d"):
        if row:
            state.pending = "dismiss"
    elif key == ord("r"):
        start_scan(state)
    elif key == ord("x"):
        cancel = getattr(state, "cancel", None)
        if cancel is not None:
            cancel.set()
            state.status = "cancellation requested"
    elif key == ord("a"):
        state.toggle_schedule()
    elif key == ord("i"):
        state.cycle_interval()
    elif key == ord("f"):
        state.severity = SEVERITY_CYCLE[(SEVERITY_CYCLE.index(state.severity) + 1)
                                        % len(SEVERITY_CYCLE)]
        state.index = 0
        state.reload()
    elif key == ord("c"):
        categories = ("all",) + tuple(hbnscan.CATEGORIES)
        try:
            state.category = categories[(categories.index(state.category) + 1) % len(categories)]
        except ValueError:
            state.category = "all"
        state.index = 0
        state.reload()
    elif key == ord("p"):
        state.group = GROUP_CYCLE[(GROUP_CYCLE.index(state.group) + 1) % len(GROUP_CYCLE)]
        state.index = 0
        state.reload()
    elif key == ord("o"):
        state.order = ORDER_CYCLE[(ORDER_CYCLE.index(state.order) + 1) % len(ORDER_CYCLE)]
        state.reload()
    elif key == ord("/"):
        state.pending = "search"
        state.confirm = state.query
    elif key == ord("b"):
        state.pane = "briefing" if state.pane != "briefing" else "detail"
        if state.pane == "briefing":
            state.load_briefing()
    elif key == ord("l"):
        state.pane = "log" if state.pane != "log" else "detail"
    elif key == ord("e"):
        state.status = export_current(state)
    elif key == ord("R"):
        state.settings = hbnscan.load_settings()
        state.reload()
        state.status = "reloaded"


def export_current(state):
    """Write the current filter to a Markdown file and report where it went."""
    stamp = time.strftime("%Y%m%d-%H%M")
    path = Path(hbnscan.APP_DIR) / f"hb-briefing-{stamp}.md"
    try:
        conn = sqlite3.connect(hbnscan.DB_PATH, timeout=10)
        try:
            saved, count = hbnscan.export_items(conn, path, filters=state.filters())
        finally:
            conn.close()
        return f"exported {count} signal(s) to {saved}"
    except Exception as exc:                                  # noqa: BLE001
        return f"export failed: {type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- loop


def run(screen):
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_CYAN, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    screen.timeout(250)

    state = State()
    state.reload()
    state.load_briefing()
    state.status = f"schedule: {hbnscan.describe_schedule(state.settings)}"

    while state.running:
        if hbnscan.next_scan_delay(hbnscan.load_settings(), state.last_started) == 0:
            start_scan(state)
        draw(screen, state)
        try:
            key = screen.getch()
        except KeyboardInterrupt:
            break
        if key != -1:
            handle_key(state, key)
        elif not state.scanning:
            # periodically pick up changes made elsewhere (GUI, web app, a text editor)
            current = hbnscan.load_settings()
            if (current["auto_scan"], current["scan_interval_minutes"]) != (
                    state.settings["auto_scan"], state.settings["scan_interval_minutes"]):
                state.settings = current
                state.last_started = None


def main(argv=None):
    parser = argparse.ArgumentParser(description="HB Briefing terminal interface.")
    parser.add_argument("--db", default=None, help="alternative database path")
    args = parser.parse_args(argv)
    if args.db:
        hbnscan.DB_PATH = Path(args.db).expanduser()
    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
