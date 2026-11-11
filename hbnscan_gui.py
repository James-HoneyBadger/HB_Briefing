#!/usr/bin/env python3
"""
HB Briefing v1.0.0 - Standalone Desktop GUI
Clean, dark-slate Tkinter interface built for Linux (XFCE/Arch/Fedora), macOS, and Windows.
"""
import os
import queue
import sqlite3
import subprocess
import sys
import threading
import webbrowser

import tkinter as tk
from tkinter import ttk, messagebox

import hbnscan

FEEDS_PATH = hbnscan.FEEDS_PATH
REPORT_PATH = hbnscan.REPORT_PATH
DB_PATH = hbnscan.DB_PATH

# Palette
BG_MAIN = "#12161f"
BG_PANEL = "#1a202c"
BG_ACCENT = "#252e3f"
FG_TEXT = "#e2e8f0"
FG_MUTED = "#94a3b8"
AMBER = "#f59e0b"
AMBER_HOVER = "#d97706"
GREEN = "#10b981"
RED = "#ef4444"


class NewsScanApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HB Briefing v1.0.0")
        self.geometry("1040x680")
        self.minsize(880, 560)
        self.configure(bg=BG_MAIN)

        self.is_scanning = False
        self._closing = False
        self.auto_interval_sec = 1800  # 30 mins
        self.auto_enabled = tk.BooleanVar(value=True)

        # Worker threads must never touch Tk directly; they post callables here.
        self._ui_queue = queue.Queue()
        self._ui_after_id = None
        self._auto_after_id = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_styles()
        self._build_ui()
        self.refresh_table()
        self.refresh_briefing()
        self._drain_ui_queue()
        self._schedule_auto_scan(initial=True)

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview",
                        background=BG_PANEL,
                        foreground=FG_TEXT,
                        fieldbackground=BG_PANEL,
                        rowheight=28,
                        borderwidth=0,
                        font=("Sans", 10))
        style.configure("Treeview.Heading",
                        background=BG_ACCENT,
                        foreground=AMBER,
                        font=("Sans", 10, "bold"),
                        relief="flat")
        style.map("Treeview", background=[("selected", "#334155")])
        style.configure("TNotebook", background=BG_MAIN, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=BG_ACCENT, foreground=FG_MUTED,
                        padding=(14, 6), borderwidth=0, font=("Sans", 9, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", BG_PANEL)],
                  foreground=[("selected", AMBER)])

    def _build_ui(self):
        # Top Header Bar
        header = tk.Frame(self, bg=BG_PANEL, height=64, padx=18, pady=10)
        header.pack(fill=tk.X, side=tk.TOP)

        title_box = tk.Frame(header, bg=BG_PANEL)
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text="HB BRIEFING", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 14, "bold")).pack(anchor="w")
        tk.Label(title_box, text="v1.0.0  |  Two-stage AI triage, deep reads & daily briefing",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9)).pack(anchor="w")

        # Status Badge
        status_box = tk.Frame(header, bg=BG_PANEL)
        status_box.pack(side=tk.RIGHT)
        self.status_lbl = tk.Label(status_box, text="● IDLE  |  READY", bg=BG_ACCENT, fg=GREEN,
                                   font=("Monospace", 10, "bold"), padx=12, pady=6)
        self.status_lbl.pack(side=tk.RIGHT)

        # Toolbar
        toolbar = tk.Frame(self, bg=BG_MAIN, padx=18, pady=10)
        toolbar.pack(fill=tk.X)

        self.run_btn = tk.Button(toolbar, text="▶ Run Scan Now", command=self.start_scan_thread,
                                 bg=AMBER, fg="#0f172a", activebackground=AMBER_HOVER,
                                 font=("Sans", 10, "bold"), relief="flat", padx=14, pady=6, cursor="hand2")
        self.run_btn.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(toolbar, text="✎ Edit Feeds", command=self.open_feeds_editor,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground="#334155",
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="⚙ Settings", command=self.open_settings,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground="#334155",
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="📄 Open Report", command=self.open_report,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground="#334155",
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="🗑 Clear Logs", command=self.clear_logs,
                  bg=BG_ACCENT, fg=FG_MUTED, activebackground="#334155",
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Checkbutton(toolbar, text="Auto-Poll (30m)", variable=self.auto_enabled,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_PANEL, activebackground=BG_MAIN,
                       font=("Sans", 10)).pack(side=tk.RIGHT)

        # Main Split Area
        paned = tk.PanedWindow(self, orient=tk.VERTICAL, bg=BG_MAIN, sashwidth=6, sashrelief="flat")
        paned.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 14))

        # Top Pane: tabbed signal table + AI briefing
        top_frame = tk.Frame(paned, bg=BG_PANEL)
        notebook = ttk.Notebook(top_frame)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        signals_tab = tk.Frame(notebook, bg=BG_PANEL)
        briefing_tab = tk.Frame(notebook, bg=BG_PANEL)
        notebook.add(signals_tab, text="  Flagged Signals  ")
        notebook.add(briefing_tab, text="  AI Briefing  ")

        self.table_lbl = tk.Label(signals_tab,
                                  text="FLAGGED INTELLIGENCE SIGNALS (Double-click row to open URL)",
                                  bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9, "bold"),
                                  anchor="w", padx=10, pady=6)
        self.table_lbl.pack(fill=tk.X)

        search_bar = tk.Frame(signals_tab, bg=BG_PANEL)
        search_bar.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(search_bar, text="🔍", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 10)).pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        self._search_after = None
        self.search_entry = tk.Entry(search_bar, textvariable=self.search_var,
                                     bg="#0d1117", fg=FG_TEXT, insertbackground=FG_TEXT,
                                     relief="flat", font=("Sans", 10))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.search_entry.bind("<Escape>", lambda _e: self._clear_search())
        tk.Label(search_bar, text="Rows:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(10, 4))
        self.limit_var = tk.StringVar(value=str(hbnscan.load_settings()["table_limit"]))
        limit_menu = tk.OptionMenu(search_bar, self.limit_var,
                                   *[str(v) for v in hbnscan.ROW_LIMITS],
                                   command=self._on_limit_changed)
        limit_menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground="#334155",
                             relief="flat", font=("Sans", 9), highlightthickness=0,
                             bd=0, indicatoron=False, padx=8, pady=3, cursor="hand2")
        limit_menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                     activeforeground=AMBER, font=("Sans", 9))
        limit_menu.pack(side=tk.LEFT)

        tk.Button(search_bar, text="Clear", command=self._clear_search,
                  bg=BG_ACCENT, fg=FG_MUTED, activebackground="#334155",
                  font=("Sans", 9), relief="flat", padx=10, pady=3).pack(side=tk.LEFT, padx=(6, 0))
        self.search_var.trace_add("write", self._on_search_changed)

        saved = hbnscan.load_settings()
        filter_bar = tk.Frame(signals_tab, bg=BG_PANEL)
        filter_bar.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.filter_vars = {}
        for position, (key, label) in enumerate((
            ("filter_severity", "Severity"),
            ("filter_category", "Category"),
            ("filter_group", "Priority"),
            ("row_order", "Order"),
        )):
            tk.Label(filter_bar, text=f"{label}:", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Sans", 9)).pack(side=tk.LEFT, padx=(0 if position == 0 else 12, 4))
            var = tk.StringVar(value=saved[key])
            self.filter_vars[key] = var
            menu = tk.OptionMenu(filter_bar, var, *hbnscan.SETTING_CHOICES[key],
                                 command=lambda value, name=key: self._on_filter_changed(name, value))
            menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground="#334155",
                           relief="flat", font=("Sans", 9), highlightthickness=0,
                           bd=0, indicatoron=False, padx=8, pady=3, cursor="hand2")
            menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                   activeforeground=AMBER, font=("Sans", 9))
            menu.pack(side=tk.LEFT)

        cols = ("time", "severity", "category", "title", "analysis")
        tree_wrap = tk.Frame(signals_tab, bg=BG_PANEL)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="browse")
        self.tree.heading("time", text="Detected")
        self.tree.heading("severity", text="Sev")
        self.tree.heading("category", text="Category")
        self.tree.heading("title", text="Headline")
        self.tree.heading("analysis", text="AI Assessment")
        self.tree.column("time", width=126, stretch=False)
        self.tree.column("severity", width=58, stretch=False, anchor="center")
        self.tree.column("category", width=138, stretch=False)
        self.tree.column("title", width=300)
        self.tree.column("analysis", width=330)
        tree_scroll = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("sev_high", foreground=RED)
        self.tree.tag_configure("sev_medium", foreground=AMBER)
        self.tree.tag_configure("sev_low", foreground=FG_MUTED)
        self.tree.bind("<Double-1>", self._on_row_double_click)
        self.bind("<Control-f>", lambda _e: self.search_entry.focus_set())
        self.row_links = {}

        self.brief_text = tk.Text(briefing_tab, bg="#0d1117", fg=FG_TEXT, insertbackground=FG_TEXT,
                                  font=("Sans", 10), relief="flat", padx=14, pady=12,
                                  wrap="word", state="disabled")
        brief_scroll = ttk.Scrollbar(briefing_tab, orient="vertical", command=self.brief_text.yview)
        self.brief_text.configure(yscrollcommand=brief_scroll.set)
        self.brief_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        brief_scroll.pack(side=tk.RIGHT, fill=tk.Y, pady=8, padx=(0, 8))
        self.brief_text.tag_configure("h", font=("Sans", 12, "bold"), foreground=AMBER,
                                      spacing1=6, spacing3=6)
        self.brief_text.tag_configure("body", spacing3=4)
        self.brief_text.tag_configure("muted", foreground=FG_MUTED)

        paned.add(top_frame, height=360)

        # Bottom Pane: Live Console Log
        bot_frame = tk.Frame(paned, bg=BG_PANEL)
        tk.Label(bot_frame, text="LIVE WATCHDOG TELEMETRY LOG",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9, "bold"), anchor="w", padx=10, pady=6).pack(fill=tk.X)

        log_wrap = tk.Frame(bot_frame, bg=BG_PANEL)
        log_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_wrap, bg="#0d1117", fg="#cbd5e1", insertbackground=FG_TEXT,
                                font=("Monospace", 9), relief="flat", padx=10, pady=8, state="disabled")
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(bot_frame, height=180)

        # Footer Bar
        footer = tk.Frame(self, bg=BG_PANEL, height=28, padx=18)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        self.footer_lbl = tk.Label(footer, text="Feeds loaded: 0  |  Database: watchdog_state.db  |  MIT Open Source",
                                   bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 8))
        self.footer_lbl.pack(side=tk.LEFT, pady=4)

    def _post(self, callback, *args):
        """Queue work for the Tk main loop (safe to call from worker threads)."""
        self._ui_queue.put((callback, args))

    def _drain_ui_queue(self):
        try:
            while True:
                callback, args = self._ui_queue.get_nowait()
                callback(*args)
        except queue.Empty:
            pass
        if not self._closing:
            self._ui_after_id = self.after(80, self._drain_ui_queue)

    def log(self, msg):
        self._post(self._append_log, msg)

    def _append_log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def start_scan_thread(self):
        if self.is_scanning or self._closing:
            return
        self.is_scanning = True
        self.run_btn.configure(state="disabled", text="⏳ Scanning...")
        self.status_lbl.configure(text="● SCANNING FEEDS...", fg=AMBER)
        threading.Thread(target=self._run_worker, daemon=True).start()

    def _run_worker(self):
        try:
            hbnscan.run_scan(log_callback=self.log)
        except Exception as exc:
            self.log(f"[ERROR] Scan failed: {exc}")
        finally:
            self._post(self._scan_finished)

    def _scan_finished(self):
        self.is_scanning = False
        if self._closing:
            return
        self.run_btn.configure(state="normal", text="▶ Run Scan Now")
        self.status_lbl.configure(text="● IDLE  |  UPDATED", fg=GREEN)
        self.refresh_table()
        self.refresh_briefing()

    def _active_filters(self):
        if not hasattr(self, "filter_vars"):
            return {key: hbnscan.DEFAULT_SETTINGS[key] for key in hbnscan.SETTING_CHOICES}
        return {key: var.get() for key, var in self.filter_vars.items()}

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.row_links.clear()
        feeds_count = len(hbnscan.load_feeds())
        query = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        filters = self._active_filters()
        try:
            limit = int(self.limit_var.get())
        except (AttributeError, ValueError, tk.TclError):
            limit = hbnscan.load_settings()["table_limit"]

        clauses = ["flagged = 1"]
        params = []
        severity_filter = filters["filter_severity"]
        category_filter = filters["filter_category"]
        group_filter = filters["filter_group"]
        if severity_filter != "all":
            clauses.append("severity = ?")
            params.append(severity_filter)
        if category_filter != "all":
            clauses.append("category = ?")
            params.append(category_filter)
        if group_filter == "watchlist":
            clauses.append("summary LIKE '[Watchlist]%'")
        elif group_filter == "core":
            clauses.append("summary NOT LIKE '[Watchlist]%'")
            clauses.append("category IN ('regulatory_action', 'litigation')")
        elif group_filter == "context":
            clauses.append("summary NOT LIKE '[Watchlist]%'")
            clauses.append("category NOT IN ('regulatory_action', 'litigation')")
        if query:
            like = f"%{query}%"
            clauses.append("(title LIKE ? OR summary LIKE ? OR category LIKE ? OR link LIKE ?)")
            params.extend([like, like, like, like])
        where = " AND ".join(clauses)
        order = hbnscan.ROW_ORDERS.get(filters["row_order"], hbnscan.ROW_ORDERS["priority"])

        flagged_total = 0
        high_total = 0
        matched = 0
        rows = []
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(DB_PATH, timeout=5)
                try:
                    rows = conn.execute(
                        "SELECT seen_at, title, summary, link, severity, category "
                        f"FROM seen_items WHERE {where} ORDER BY {order} LIMIT ?",
                        (*params, limit),
                    ).fetchall()
                    matched = conn.execute(
                        f"SELECT COUNT(*) FROM seen_items WHERE {where}", tuple(params)
                    ).fetchone()[0]
                    totals = conn.execute(
                        "SELECT COUNT(*), COALESCE(SUM(severity = 'high'), 0) "
                        "FROM seen_items WHERE flagged = 1"
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                self.log(f"[WARN] Could not read database: {exc}")
                rows = []
                totals = (0, 0)
            flagged_total, high_total = totals
            for idx, (seen_at, title, summary, link, severity, category) in enumerate(rows):
                severity = (severity or "none").lower()
                iid = f"row_{idx}"
                tag = f"sev_{severity}" if severity in ("high", "medium", "low") else ""
                self.tree.insert(
                    "", tk.END, iid=iid,
                    values=(seen_at.replace("T", " "), severity.upper(),
                            category or "other", title, summary),
                    tags=(tag,) if tag else ()
                )
                self.row_links[iid] = link

        active = [
            f"{key.split('_', 1)[1]}={value}"
            for key, value in filters.items() if value != "all" and key != "row_order"
        ]
        if filters["row_order"] != "priority":
            active.append(f"order={filters['row_order']}")
        parts = []
        if query:
            parts.append(f"SEARCH '{query}'")
        if active:
            parts.append("FILTERED " + ", ".join(active))
        detail = " — ".join(parts) or "FLAGGED INTELLIGENCE SIGNALS"
        total = matched if (query or active) else flagged_total
        self.table_lbl.configure(
            text=f"{detail}: showing {len(rows)} of {total}, limit {limit}"
                 + ("" if (query or active) else " (double-click a row to open)")
        )

        ai_mode = (f"DeepSeek ({hbnscan.DEEPSEEK_MODEL})"
                   if hbnscan.deepseek_configured() else "Offline heuristic")
        window = hbnscan.load_settings()
        window_text = ""
        if window["feed_since"] or window["feed_until"]:
            window_text = (f"  |  Window: {window['feed_since'] or 'open'}"
                           f"→{window['feed_until'] or 'open'}")
        self.footer_lbl.configure(
            text=f"Active Feeds: {feeds_count}  |  Signals: {flagged_total} "
                 f"({high_total} high)  |  AI: {ai_mode}{window_text}"
                 f"  |  MIT License — James-HoneyBadger"
        )

    def _on_filter_changed(self, key, value):
        hbnscan.save_settings(**{key: value})
        self.refresh_table()

    def _on_search_changed(self, *_):
        """Debounce typing so a fast query does not hit SQLite per keystroke."""
        if self._search_after is not None:
            try:
                self.after_cancel(self._search_after)
            except tk.TclError:
                pass
        self._search_after = self.after(250, self.refresh_table)

    def _clear_search(self):
        self.search_var.set("")
        self.search_entry.focus_set()

    def _on_limit_changed(self, value):
        hbnscan.save_settings(table_limit=value)
        self.refresh_table()

    def refresh_briefing(self):
        content, stamped = "", ""
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(DB_PATH, timeout=5)
                try:
                    row = conn.execute(
                        "SELECT content, created_at FROM briefings ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                self.log(f"[WARN] Could not read briefing: {exc}")
                row = None
            if row:
                content, stamped = row[0] or "", row[1] or ""

        self.brief_text.configure(state="normal")
        self.brief_text.delete("1.0", tk.END)
        if content:
            if stamped:
                self.brief_text.insert(tk.END, f"Briefed {stamped}\n", "muted")
            for line in content.splitlines():
                if line.startswith("### "):
                    self.brief_text.insert(tk.END, line[4:].strip() + "\n", "h")
                elif line.startswith("**") and line.rstrip().endswith("**"):
                    self.brief_text.insert(tk.END, line.strip("*") + "\n", "h")
                else:
                    self.brief_text.insert(tk.END, line + "\n", "body")
        else:
            self.brief_text.insert(
                tk.END,
                "No briefing yet.\n\n"
                "A briefing is written once per scan, but only when a DeepSeek API key is "
                "configured in .env — it needs a model call to rank and merge the signals.",
                "muted"
            )
        self.brief_text.configure(state="disabled")

    def _on_row_double_click(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        url = hbnscan.safe_link(self.row_links.get(sel[0], ""))
        if url:
            webbrowser.open(url)

    def open_feeds_editor(self):
        win = tk.Toplevel(self)
        win.title("Edit feeds.txt")
        win.geometry("640x420")
        win.configure(bg=BG_PANEL)

        tk.Label(win, text="RSS Feed URLs (one per line, # for comments):",
                 bg=BG_PANEL, fg=FG_TEXT, font=("Sans", 10, "bold")).pack(anchor="w", padx=12, pady=(10, 4))
        txt = tk.Text(win, bg="#0d1117", fg=FG_TEXT, insertbackground=FG_TEXT, font=("Monospace", 10))
        txt.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)
        if FEEDS_PATH.exists():
            txt.insert("1.0", FEEDS_PATH.read_text(encoding="utf-8"))

        def save_feeds():
            FEEDS_PATH.write_text(txt.get("1.0", tk.END).strip() + "\n", encoding="utf-8")
            self.refresh_table()
            self.log("[INFO] Updated feeds.txt configuration.")
            win.destroy()

        tk.Button(win, text="Save Feeds", command=save_feeds, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=14, pady=6).pack(pady=10)

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("HB Briefing - Settings")
        win.geometry("480x280")
        win.configure(bg=BG_PANEL)
        win.transient(self)

        settings = hbnscan.load_settings()

        tk.Label(win, text="FEED DATE RANGE", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(win,
                 text="Only items published inside this window are triaged.\n"
                      "Leave a field blank for an open-ended range (YYYY-MM-DD).",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9), justify="left", anchor="w"
                 ).pack(fill=tk.X, padx=16)

        form = tk.Frame(win, bg=BG_PANEL)
        form.pack(fill=tk.X, padx=16, pady=12)
        form.columnconfigure(1, weight=1)

        since_var = tk.StringVar(value=settings["feed_since"])
        until_var = tk.StringVar(value=settings["feed_until"])
        entry_opts = dict(bg="#0d1117", fg=FG_TEXT, insertbackground=FG_TEXT,
                          relief="flat", font=("Monospace", 10))
        tk.Label(form, text="From:", bg=BG_PANEL, fg=FG_TEXT).grid(row=0, column=0,
                                                                  sticky="w", pady=4)
        tk.Entry(form, textvariable=since_var, **entry_opts).grid(row=0, column=1,
                                                                 sticky="we", padx=(8, 0), ipady=4)
        tk.Label(form, text="To:", bg=BG_PANEL, fg=FG_TEXT).grid(row=1, column=0,
                                                                sticky="w", pady=4)
        tk.Entry(form, textvariable=until_var, **entry_opts).grid(row=1, column=1,
                                                                 sticky="we", padx=(8, 0), ipady=4)

        def clear():
            since_var.set("")
            until_var.set("")

        def save():
            since = since_var.get().strip()
            until = until_var.get().strip()
            for label, value in (("From", since), ("To", until)):
                if value and not hbnscan.parse_setting_date(value):
                    messagebox.showerror("Settings", f"{label} must be YYYY-MM-DD.", parent=win)
                    return
            start = hbnscan.parse_setting_date(since)
            end = hbnscan.parse_setting_date(until)
            if start and end and start > end:
                messagebox.showerror("Settings", "'From' is after 'To'.", parent=win)
                return
            hbnscan.save_settings(feed_since=since, feed_until=until)
            self.log(f"[INFO] Feed date window: {since or 'open'} to {until or 'open'}.")
            self.refresh_table()
            win.destroy()

        buttons = tk.Frame(win, bg=BG_PANEL)
        buttons.pack(fill=tk.X, padx=16, pady=8)
        tk.Button(buttons, text="Clear", command=clear, bg=BG_ACCENT, fg=FG_MUTED,
                  font=("Sans", 10), relief="flat", padx=12, pady=6).pack(side=tk.LEFT)
        tk.Button(buttons, text="Save", command=save, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=16, pady=6).pack(side=tk.RIGHT)

    def open_report(self):
        if not REPORT_PATH.exists():
            messagebox.showinfo("Report", "No report generated yet. Run a scan first.")
            return
        if sys.platform in ("win32", "cygwin"):
            os.startfile(str(REPORT_PATH))
            return
        opener = "xdg-open" if sys.platform.startswith("linux") else "open"
        subprocess.Popen([opener, str(REPORT_PATH)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def clear_logs(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")

    def _schedule_auto_scan(self, initial=False):
        if self._closing:
            return
        if self.auto_enabled.get() and not self.is_scanning:
            if initial:
                self.log("[AUTO] Startup scan triggered.")
            else:
                self.log("[AUTO] Scheduled 30-minute poll triggered.")
            self.start_scan_thread()
        self._auto_after_id = self.after(self.auto_interval_sec * 1000, self._schedule_auto_scan)

    def _on_close(self):
        self._closing = True
        for after_id in (self._ui_after_id, self._auto_after_id):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        self.destroy()


if __name__ == "__main__":
    app = NewsScanApp()
    app.mainloop()
