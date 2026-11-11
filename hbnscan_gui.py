#!/usr/bin/env python3
"""
HB Briefing v1.0.0 - Standalone Desktop GUI
Clean, dark-slate Tkinter interface built for Linux (XFCE/Arch/Fedora), macOS, and Windows.
"""
import json
import os
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser

import tkinter as tk
from tkinter import ttk, messagebox

import hbnscan

FEEDS_PATH = hbnscan.FEEDS_PATH
REPORT_PATH = hbnscan.REPORT_PATH
DB_PATH = hbnscan.DB_PATH

# Themes. Widgets capture colours at creation time, so switching rebuilds the UI.
THEMES = {
    "dark": {
        "bg_main": "#12161f", "bg_panel": "#1a202c", "bg_accent": "#252e3f",
        "bg_input": "#0d1117", "bg_select": "#334155", "log_fg": "#cbd5e1",
        "fg_text": "#e2e8f0", "fg_muted": "#94a3b8",
        "accent": "#f59e0b", "accent_hover": "#d97706",
        "green": "#10b981", "red": "#ef4444",
    },
    "light": {
        "bg_main": "#eef2f6", "bg_panel": "#ffffff", "bg_accent": "#dfe6ee",
        "bg_input": "#f8fafc", "bg_select": "#c7d3e0", "log_fg": "#1e293b",
        "fg_text": "#0f172a", "fg_muted": "#475569",
        "accent": "#b45309", "accent_hover": "#92400e",
        "green": "#047857", "red": "#b91c1c",
    },
}

BG_MAIN = BG_PANEL = BG_ACCENT = BG_INPUT = BG_SELECT = ""
FG_TEXT = FG_MUTED = LOG_FG = AMBER = AMBER_HOVER = GREEN = RED = ""
THEME_NAME = "dark"


def apply_theme(name):
    """Bind the module-level palette names used by every widget builder."""
    global BG_MAIN, BG_PANEL, BG_ACCENT, BG_INPUT, BG_SELECT, FG_TEXT, FG_MUTED
    global LOG_FG, AMBER, AMBER_HOVER, GREEN, RED, THEME_NAME
    THEME_NAME = name if name in THEMES else "dark"
    theme = THEMES[THEME_NAME]
    BG_MAIN, BG_PANEL, BG_ACCENT = theme["bg_main"], theme["bg_panel"], theme["bg_accent"]
    BG_INPUT, BG_SELECT, LOG_FG = theme["bg_input"], theme["bg_select"], theme["log_fg"]
    FG_TEXT, FG_MUTED = theme["fg_text"], theme["fg_muted"]
    AMBER, AMBER_HOVER = theme["accent"], theme["accent_hover"]
    GREEN, RED = theme["green"], theme["red"]


apply_theme("dark")


class NewsScanApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = hbnscan.load_settings()
        apply_theme(self.settings["theme"])
        self._apply_font_scale()

        self.title("HB Briefing v1.0.0")
        self.minsize(880, 560)
        self.configure(bg=BG_MAIN)

        self.is_scanning = False
        self._closing = False
        self.auto_enabled = tk.BooleanVar(value=self.settings["auto_scan"])

        # Worker threads must never touch Tk directly; they post callables here.
        self._ui_queue = queue.Queue()
        self._ui_after_id = None
        self._auto_after_id = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_styles()
        self._build_ui()
        self._restore_window()
        self.refresh_table()
        self.refresh_briefing()
        self._drain_ui_queue()
        self._schedule_auto_scan(initial=True)

    def _apply_font_scale(self):
        """Point sizes scale with Tk's points-per-pixel ratio, so one call covers every font."""
        self.tk.call("tk", "scaling", 1.333 * self.settings["font_scale"] / 100.0)

    def _restore_window(self):
        geometry = self.settings["window_geometry"]
        if geometry:
            match = re.match(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$", geometry)
            if match:
                _w, _h, x, y = (int(match.group(i)) for i in (1, 2, 3, 4))
                # Only trust a saved position that still lands on this screen.
                if (-50 < x < self.winfo_screenwidth() - 100
                        and -50 < y < self.winfo_screenheight() - 100):
                    self.geometry(geometry)
                else:
                    self.geometry(f"{match.group(1)}x{match.group(2)}")
        else:
            self.geometry("1040x680")
        if self.settings["window_state"] == "zoomed":
            try:
                self.state("zoomed")
            except tk.TclError:
                pass
        self.after(60, self._restore_sash)

    def _restore_sash(self):
        """Sash placement needs a mapped window; doing it in __init__ is a no-op."""
        if not hasattr(self, "paned") or not self.winfo_exists():
            return
        try:
            self.paned.sash_place(0, 0, self.settings["sash"])
        except tk.TclError:
            pass

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
        style.map("Treeview", background=[("selected", BG_SELECT)])
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
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="⚙ Settings", command=self.open_settings,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="⤓ Export", command=self.export_data,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="📄 Open Report", command=self.open_report,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        tk.Button(toolbar, text="🗑 Clear Logs", command=self.clear_logs,
                  bg=BG_ACCENT, fg=FG_MUTED, activebackground=BG_SELECT,
                  font=("Sans", 10), relief="flat", padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        self.cancel_btn = tk.Button(toolbar, text="■ Stop", command=self.cancel_scan,
                                    bg=BG_ACCENT, fg=FG_MUTED, activebackground=BG_SELECT,
                                    font=("Sans", 10), relief="flat", padx=12, pady=6,
                                    state="disabled", cursor="hand2")
        self.cancel_btn.pack(side=tk.LEFT, padx=4)

        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=120)
        self._progress_packed = False

        tk.Checkbutton(toolbar, text="Auto-Scan", variable=self.auto_enabled,
                       command=self._on_auto_toggle,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_PANEL, activebackground=BG_MAIN,
                       font=("Sans", 10)).pack(side=tk.RIGHT)

        # Main Split Area
        paned = tk.PanedWindow(self, orient=tk.VERTICAL, bg=BG_MAIN, sashwidth=6,
                               sashrelief="flat", sashcursor="sb_v_double_arrow")
        self.paned = paned
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
                                     bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                     relief="flat", font=("Sans", 10))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        self.search_entry.bind("<Escape>", lambda _e: self._clear_search())
        tk.Label(search_bar, text="Rows:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(10, 4))
        self.limit_var = tk.StringVar(value=str(hbnscan.load_settings()["table_limit"]))
        limit_menu = tk.OptionMenu(search_bar, self.limit_var,
                                   *[str(v) for v in hbnscan.ROW_LIMITS],
                                   command=self._on_limit_changed)
        limit_menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                             relief="flat", font=("Sans", 9), highlightthickness=0,
                             bd=0, indicatoron=False, padx=8, pady=3, cursor="hand2")
        limit_menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                     activeforeground=AMBER, font=("Sans", 9))
        limit_menu.pack(side=tk.LEFT)

        tk.Button(search_bar, text="Clear", command=self._clear_search,
                  bg=BG_ACCENT, fg=FG_MUTED, activebackground=BG_SELECT,
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
            menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                           relief="flat", font=("Sans", 9), highlightthickness=0,
                           bd=0, indicatoron=False, padx=8, pady=3, cursor="hand2")
            menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                   activeforeground=AMBER, font=("Sans", 9))
            menu.pack(side=tk.LEFT)

        cols = ("time", "severity", "category", "title", "analysis")
        tree_wrap = tk.Frame(signals_tab, bg=BG_PANEL)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="browse")
        self._heading_labels = {"time": "Detected", "severity": "Sev",
                                "category": "Category", "title": "Headline",
                                "analysis": "AI Assessment"}
        for column, label in self._heading_labels.items():
            self.tree.heading(column, text=label,
                              command=lambda col=column: self._sort_by_column(col))
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
        self.tree.bind("<Button-3>", self._show_row_menu)
        self.tree.bind("<Button-2>", self._show_row_menu)
        self.row_menu = tk.Menu(self, tearoff=0, bg=BG_PANEL, fg=FG_TEXT,
                                activebackground=BG_SELECT, activeforeground=AMBER,
                                font=("Sans", 9))
        self.row_menu.add_command(label="Open in browser", command=self._open_selected)
        self.row_menu.add_command(label="Copy headline", command=self._copy_selected)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Not relevant (dismiss)", command=self._dismiss_selected)
        self.tree.bind("<Delete>", lambda _e: self._dismiss_selected())
        self.bind("<Control-f>", lambda _e: self.search_entry.focus_set())
        self.row_links = {}

        self.brief_text = tk.Text(briefing_tab, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
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

        detail = tk.Frame(signals_tab, bg=BG_INPUT)
        detail.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.detail_text = tk.Text(detail, height=4, bg=BG_INPUT, fg=FG_TEXT,
                                   insertbackground=FG_TEXT, font=("Sans", 9),
                                   relief="flat", padx=10, pady=6, wrap="word",
                                   state="disabled")
        self.detail_text.pack(fill=tk.X, expand=True)
        self.detail_text.tag_configure("key", foreground=AMBER, font=("Sans", 9, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

        paned.add(top_frame, height=360)

        # Bottom Pane: Live Console Log
        bot_frame = tk.Frame(paned, bg=BG_PANEL)
        tk.Label(bot_frame, text="LIVE WATCHDOG TELEMETRY LOG",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9, "bold"), anchor="w", padx=10, pady=6).pack(fill=tk.X)

        log_wrap = tk.Frame(bot_frame, bg=BG_PANEL)
        log_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.log_text = tk.Text(log_wrap, bg=BG_INPUT, fg=LOG_FG, insertbackground=FG_TEXT,
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
        except tk.TclError:
            return  # interpreter is going away
        if self._closing or not self.winfo_exists():
            return
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
        self._cancel_event = threading.Event()
        self.run_btn.configure(state="disabled", text="⏳ Scanning...")
        self.cancel_btn.configure(state="normal", fg=FG_TEXT)
        self.status_lbl.configure(text="● SCANNING FEEDS...", fg=AMBER)
        if not self._progress_packed:
            self.progress.pack(side=tk.RIGHT, padx=(0, 12))
            self._progress_packed = True
        self.progress.start(14)
        threading.Thread(target=self._run_worker, daemon=True).start()

    def cancel_scan(self):
        if self.is_scanning and self._cancel_event is not None:
            self._cancel_event.set()
            self.status_lbl.configure(text="● STOPPING...", fg=AMBER)
            self.log("[INFO] Cancel requested; stopping after the current item.")

    def _run_worker(self):
        try:
            hbnscan.run_scan(log_callback=self.log, cancel=self._cancel_event)
        except Exception as exc:
            self.log(f"[ERROR] Scan failed: {exc}")
        finally:
            self._post(self._scan_finished)

    def _scan_finished(self):
        self.is_scanning = False
        self._stop_progress()
        if self._closing:
            return
        self.run_btn.configure(state="normal", text="▶ Run Scan Now")
        self.cancel_btn.configure(state="disabled", fg=FG_MUTED)
        cancelled = self._cancel_event is not None and self._cancel_event.is_set()
        self.status_lbl.configure(text="● IDLE  |  STOPPED" if cancelled else "● IDLE  |  UPDATED",
                                  fg=AMBER if cancelled else GREEN)
        self.refresh_table()
        self.refresh_briefing()

    def _stop_progress(self):
        self.progress.stop()
        if self._progress_packed:
            self.progress.pack_forget()
            self._progress_packed = False

    def _reset_scan_controls(self):
        """Used after a UI rebuild: never leave a stale disabled button behind."""
        if not self.is_scanning:
            self.run_btn.configure(state="normal", text="▶ Run Scan Now")
            self.cancel_btn.configure(state="disabled", fg=FG_MUTED)

    def _active_filters(self):
        if not hasattr(self, "filter_vars"):
            return {key: hbnscan.DEFAULT_SETTINGS[key] for key in hbnscan.SETTING_CHOICES}
        return {key: var.get() for key, var in self.filter_vars.items()}

    def refresh_table(self):
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.row_links.clear()
        self.row_keys = {}
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
                        "SELECT seen_at, title, summary, link, severity, category, item_hash "
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
                    marker = conn.execute(
                        "SELECT value FROM meta WHERE key = 'prev_scan_at'").fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                self.log(f"[WARN] Could not read database: {exc}")
                rows = []
                totals = (0, 0)
            flagged_total, high_total = totals
            previous_scan = marker[0] if marker and marker[0] else ""
            new_count = 0
            for idx, (seen_at, title, summary, link, severity, category,
                      item_hash) in enumerate(rows):
                severity = (severity or "none").lower()
                iid = f"row_{idx}"
                tag = f"sev_{severity}" if severity in ("high", "medium", "low") else ""
                is_new = bool(previous_scan) and seen_at > previous_scan
                new_count += is_new
                self.tree.insert(
                    "", tk.END, iid=iid,
                    values=(("NEW  " if is_new else "") + seen_at.replace("T", " "),
                            severity.upper(), category or "other", title, summary),
                    tags=(tag,) if tag else ()
                )
                self.row_links[iid] = link
                self.row_keys[iid] = item_hash

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
                 + (f", {new_count} new" if new_count else "")
                 + ("" if (query or active) else " (double-click opens, right-click dismisses)")
        )

        ai_mode = (f"DeepSeek ({hbnscan.DEEPSEEK_MODEL})"
                   if hbnscan.deepseek_configured() else "Offline heuristic")
        window = hbnscan.load_settings()
        window_text = ""
        if window["feed_since"] or window["feed_until"]:
            window_text = (f"  |  Window: {window['feed_since'] or 'open'}"
                           f"→{window['feed_until'] or 'open'}")
        scan_mode = (f"Scan: {window['scan_interval_minutes']}m"
                     if window["auto_scan"] else "Scan: manual")
        if window["auto_scan"] and (window["scan_window_start"] or window["scan_window_end"]):
            scan_mode += f" ({window['scan_window_start'] or '00:00'}-" \
                         f"{window['scan_window_end'] or '24:00'})"
        self.footer_lbl.configure(
            text=f"Active Feeds: {feeds_count}  |  Signals: {flagged_total} "
                 f"({high_total} high)  |  {scan_mode}  |  AI: {ai_mode}{window_text}"
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

    COLUMN_ORDERS = {"time": "newest", "severity": "severity",
                     "category": "category", "title": "title"}

    def _sort_by_column(self, column):
        """Header clicks reuse the persisted row_order rather than a second scheme."""
        order = self.COLUMN_ORDERS.get(column)
        if not order or not hasattr(self, "filter_vars"):
            return
        self.filter_vars["row_order"].set(order)
        self._on_filter_changed("row_order", order)

    def _on_row_select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            self._show_detail(None)
            return
        values = self.tree.item(selection[0], "values")
        self._show_detail({"link": self.row_links.get(selection[0], ""),
                           "title": values[3] if len(values) > 3 else ""})

    def _show_detail(self, row):
        """Surface the fields the grid cannot fit: facts, entities, confidence."""
        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", tk.END)
        if not row:
            self.detail_text.insert(
                tk.END, "Select a row for facts, entities, confidence and verification.", "key")
            self.detail_text.configure(state="disabled")
            return

        record = None
        if DB_PATH.exists():
            try:
                conn = sqlite3.connect(DB_PATH, timeout=5)
                try:
                    record = conn.execute(
                        "SELECT summary, action, facts, entities, confidence, deep_read, "
                        "published FROM seen_items WHERE link = ? ORDER BY rowid DESC LIMIT 1",
                        (row["link"],),
                    ).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error:
                record = None

        if not record:
            self.detail_text.insert(tk.END, row["title"], "key")
            self.detail_text.configure(state="disabled")
            return

        summary, action, facts, entities, confidence, deep_read, published = record

        def field(label, value):
            if value:
                self.detail_text.insert(tk.END, f"{label}: ", "key")
                self.detail_text.insert(tk.END, f"{value}\n")

        field("Assessment", summary)
        field("Next step", action)
        field("Published", (published or "")[:10])
        try:
            fact_list = json.loads(facts or "[]")
        except ValueError:
            fact_list = []
        if fact_list:
            field("Facts", "; ".join(str(item) for item in fact_list))
        try:
            entity_map = json.loads(entities or "{}")
        except ValueError:
            entity_map = {}
        if isinstance(entity_map, dict) and entity_map:
            field("Entities", "; ".join(f"{key}: {', '.join(map(str, values))}"
                                        for key, values in entity_map.items() if values))
        if confidence is not None:
            field("Confidence", f"{float(confidence):.2f}")
        field("Verification", "deep read" if deep_read else "screened only")
        self.detail_text.configure(state="disabled")

    def _show_row_menu(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._on_row_select()
        try:
            self.row_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.row_menu.grab_release()

    def _selected_row(self):
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _open_selected(self):
        iid = self._selected_row()
        if iid:
            url = hbnscan.safe_link(self.row_links.get(iid, ""))
            if url:
                webbrowser.open(url)

    def _copy_selected(self):
        iid = self._selected_row()
        if not iid:
            return
        headline = str(self.tree.item(iid, "values")[3])
        self.clipboard_clear()
        self.clipboard_append(headline)
        self.log(f"[INFO] Copied: {headline[:70]}")

    def _dismiss_selected(self):
        iid = self._selected_row()
        if not iid:
            return
        key = self.row_keys.get(iid)
        if not key:
            return
        headline = str(self.tree.item(iid, "values")[3])
        if not messagebox.askyesno(
                "Dismiss signal",
                f"Mark as not relevant?\n\n{headline[:160]}\n\n"
                "It leaves the report, table and exports but stays in the database.",
                parent=self):
            return
        try:
            conn = sqlite3.connect(DB_PATH, timeout=10)
            try:
                state = hbnscan.dismiss_item(conn, key)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.log(f"[ERROR] Dismiss failed: {exc}")
            return
        if state == "dismissed":
            self.log(f"[INFO] Dismissed: {headline[:70]}")
            self.refresh_table()

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
        txt = tk.Text(win, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT, font=("Monospace", 10))
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
        win.geometry("560x780")
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
        entry_opts = dict(bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                          relief="flat", font=("Monospace", 10))
        tk.Label(form, text="From:", bg=BG_PANEL, fg=FG_TEXT).grid(row=0, column=0,
                                                                  sticky="w", pady=4)
        tk.Entry(form, textvariable=since_var, **entry_opts).grid(row=0, column=1,
                                                                 sticky="we", padx=(8, 0), ipady=4)
        tk.Label(form, text="To:", bg=BG_PANEL, fg=FG_TEXT).grid(row=1, column=0,
                                                                sticky="w", pady=4)
        tk.Entry(form, textvariable=until_var, **entry_opts).grid(row=1, column=1,
                                                                 sticky="we", padx=(8, 0), ipady=4)

        tk.Label(win, text="DEEPSEEK API KEY", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(6, 2))
        if hbnscan.deepseek_configured():
            key_status = (f"Configured ({hbnscan.DEEPSEEK_API_KEY[:6]}…"
                          f"{hbnscan.DEEPSEEK_API_KEY[-4:]}). Leave blank to keep it.")
            status_colour = GREEN
        else:
            key_status = "Not configured - running on the offline heuristic only."
            status_colour = AMBER
        key_status_lbl = tk.Label(win, text=key_status, bg=BG_PANEL, fg=status_colour,
                                  font=("Sans", 9), anchor="w", justify="left")
        key_status_lbl.pack(fill=tk.X, padx=16)

        key_row = tk.Frame(win, bg=BG_PANEL)
        key_row.pack(fill=tk.X, padx=16, pady=8)
        key_var = tk.StringVar()
        tk.Entry(key_row, textvariable=key_var, show="\u2022", **entry_opts
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        test_btn = tk.Button(key_row, text="Test key", bg=BG_ACCENT, fg=FG_TEXT,
                             activebackground=BG_SELECT, font=("Sans", 9),
                             relief="flat", padx=10, pady=3, cursor="hand2")
        test_btn.pack(side=tk.LEFT, padx=(8, 0))

        def finish_test(ok, lines):
            for line in lines:
                self.log("  " + line)
            test_btn.configure(state="normal", text="Test key")
            if ok:
                messagebox.showinfo("API key", "Key works. AI stages are live.", parent=win)
            else:
                messagebox.showerror("API key", "The test failed - see the log.", parent=win)

        def test_key():
            candidate = key_var.get().strip() or hbnscan.DEEPSEEK_API_KEY
            if not candidate:
                messagebox.showwarning("API key", "Enter a key first.", parent=win)
                return
            test_btn.configure(state="disabled", text="Testing...")
            self.log("[TEST] Calling DeepSeek to validate the key...")

            def worker():
                lines = []
                code = hbnscan.check_api(log_callback=lines.append, api_key=candidate)
                self._post(finish_test, code == 0, lines)

            threading.Thread(target=worker, daemon=True).start()

        test_btn.configure(command=test_key)

        tk.Label(win, text="SCANNING", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        scan_var = tk.BooleanVar(value=settings["auto_scan"])
        tk.Checkbutton(win, text="Scan automatically", variable=scan_var,
                       bg=BG_PANEL, fg=FG_TEXT, selectcolor=BG_ACCENT,
                       activebackground=BG_PANEL, activeforeground=FG_TEXT,
                       font=("Sans", 9), anchor="w").pack(fill=tk.X, padx=16)
        scan_row = tk.Frame(win, bg=BG_PANEL)
        scan_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        interval_var = tk.StringVar(value=str(settings["scan_interval_minutes"]))
        window_start_var = tk.StringVar(value=settings["scan_window_start"])
        window_end_var = tk.StringVar(value=settings["scan_window_end"])
        for label, var, choices in (
            ("Every", interval_var, hbnscan.SETTING_CHOICES["scan_interval_minutes"]),
        ):
            tk.Label(scan_row, text=f"{label}:", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
            menu = tk.OptionMenu(scan_row, var, *choices)
            menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                           relief="flat", font=("Sans", 9), highlightthickness=0,
                           bd=0, indicatoron=False, padx=8, pady=3)
            menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                   activeforeground=AMBER, font=("Sans", 9))
            menu.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(scan_row, text="min   only between", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        for var, hint in ((window_start_var, "HH:MM"), (window_end_var, "HH:MM")):
            tk.Entry(scan_row, textvariable=var, width=7, bg=BG_INPUT, fg=FG_TEXT,
                     insertbackground=FG_TEXT, relief="flat",
                     font=("Monospace", 9)).pack(side=tk.LEFT, padx=(0, 4), ipady=2)
        tk.Label(scan_row, text="(blank = all day)", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 8)).pack(side=tk.LEFT)

        tk.Label(win, text="NOTIFICATIONS", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        notify_var = tk.BooleanVar(value=settings["notify_enabled"])
        tk.Checkbutton(win, text="Email / SMS me new high-priority signals",
                       variable=notify_var, bg=BG_PANEL, fg=FG_TEXT, selectcolor=BG_ACCENT,
                       activebackground=BG_PANEL, activeforeground=FG_TEXT,
                       font=("Sans", 9), anchor="w").pack(fill=tk.X, padx=16)
        tk.Label(win, text=hbnscan.describe_notifications(), bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 8), anchor="w", justify="left",
                 wraplength=470).pack(fill=tk.X, padx=16)
        notify_row = tk.Frame(win, bg=BG_PANEL)
        notify_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        severity_var = tk.StringVar(value=settings["notify_min_severity"])
        tk.Label(notify_row, text="At least:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        sev_menu = tk.OptionMenu(notify_row, severity_var,
                                 *hbnscan.SETTING_CHOICES["notify_min_severity"])
        sev_menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                           relief="flat", font=("Sans", 9), highlightthickness=0,
                           bd=0, indicatoron=False, padx=8, pady=3)
        sev_menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                   activeforeground=AMBER, font=("Sans", 9))
        sev_menu.pack(side=tk.LEFT)
        notify_test_btn = tk.Button(notify_row, text="Send test", bg=BG_ACCENT, fg=FG_TEXT,
                                    activebackground=BG_SELECT, font=("Sans", 9),
                                    relief="flat", padx=10, pady=3, cursor="hand2")
        notify_test_btn.pack(side=tk.LEFT, padx=(12, 0))

        def finish_notify_test(ok, lines):
            for line in lines:
                self.log("  " + line)
            notify_test_btn.configure(state="normal", text="Send test")
            if ok:
                messagebox.showinfo("Notifications", "Test message sent.", parent=win)
            else:
                messagebox.showerror("Notifications", "Send failed - see the log.", parent=win)

        def test_notify():
            notify_test_btn.configure(state="disabled", text="Sending...")

            def worker():
                lines = []
                code = hbnscan.send_test_notification(log_callback=lines.append)
                self._post(finish_notify_test, code == 0, lines)

            threading.Thread(target=worker, daemon=True).start()

        notify_test_btn.configure(command=test_notify)

        tk.Label(win, text="APPEARANCE", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        look = tk.Frame(win, bg=BG_PANEL)
        look.pack(fill=tk.X, padx=16)
        theme_var = tk.StringVar(value=settings["theme"])
        scale_var = tk.StringVar(value=str(settings["font_scale"]))
        for label, var, choices in (
            ("Theme", theme_var, hbnscan.SETTING_CHOICES["theme"]),
            ("Text size", scale_var, ("90", "100", "110", "125", "150")),
        ):
            tk.Label(look, text=f"{label}:", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
            menu = tk.OptionMenu(look, var, *choices)
            menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                           relief="flat", font=("Sans", 9), highlightthickness=0,
                           bd=0, indicatoron=False, padx=8, pady=3, cursor="hand2")
            menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                                   activeforeground=AMBER, font=("Sans", 9))
            menu.pack(side=tk.LEFT, padx=(0, 12))

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
            for label, value in (("From", window_start_var.get().strip()),
                                 ("To", window_end_var.get().strip())):
                if value and hbnscan.parse_setting_clock(value) is None:
                    messagebox.showerror("Settings", f"{label} must be HH:MM.", parent=win)
                    return
            before = hbnscan.load_settings()
            hbnscan.save_settings(feed_since=since, feed_until=until,
                                  theme=theme_var.get(), font_scale=scale_var.get(),
                                  notify_enabled=notify_var.get(),
                                  notify_min_severity=severity_var.get(),
                                  auto_scan=scan_var.get(),
                                  scan_interval_minutes=interval_var.get(),
                                  scan_window_start=window_start_var.get().strip(),
                                  scan_window_end=window_end_var.get().strip())
            after = hbnscan.load_settings()
            self.auto_enabled.set(after["auto_scan"])
            if any(before[key] != after[key] for key in
                   ("auto_scan", "scan_interval_minutes", "scan_window_start",
                    "scan_window_end")):
                self._reschedule_auto_scan()
            self.log(f"[INFO] Feed date window: {since or 'open'} to {until or 'open'}.")
            if (theme_var.get() != self.settings["theme"]
                    or int(scale_var.get()) != int(self.settings["font_scale"])):
                self.log(f"[INFO] Appearance: {theme_var.get()}, text {scale_var.get()}%.")
                win.destroy()
                self._rebuild_ui()
                return
            key = key_var.get().strip()
            if key:
                if hbnscan.set_api_key(key):
                    self.log("[INFO] DeepSeek key saved to .env; AI stages are live.")
                else:
                    self.log("[WARN] Key looks like the placeholder; AI stays offline.")
            self.refresh_table()
            win.destroy()

        buttons = tk.Frame(win, bg=BG_PANEL)
        buttons.pack(fill=tk.X, padx=16, pady=8)
        tk.Button(buttons, text="Clear", command=clear, bg=BG_ACCENT, fg=FG_MUTED,
                  font=("Sans", 10), relief="flat", padx=12, pady=6).pack(side=tk.LEFT)
        tk.Button(buttons, text="Save", command=save, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=16, pady=6).pack(side=tk.RIGHT)

    def export_data(self):
        """Write exactly what the grid is showing, in any supported format."""
        filters = self._active_filters()
        query = self.search_var.get().strip()
        try:
            limit = int(self.limit_var.get())
        except (ValueError, tk.TclError):
            limit = hbnscan.load_settings()["table_limit"]
        stamp = time.strftime("%Y%m%d-%H%M")
        path = filedialog.asksaveasfilename(
            parent=self, title="Export signals", defaultextension=".md",
            initialfile=f"hb-briefing-{stamp}.md", filetypes=hbnscan.EXPORT_TYPES,
        )
        if not path:
            return
        selection = {"severity": filters["filter_severity"],
                     "category": filters["filter_category"],
                     "group": filters["filter_group"],
                     "query": query, "limit": limit}

        def worker():
            try:
                conn = sqlite3.connect(DB_PATH, timeout=10)
                try:
                    saved, count = hbnscan.export_items(
                        conn, path, filters=selection, log_callback=self.log)
                finally:
                    conn.close()
                self._post(self._export_done, saved, count, None)
            except Exception as exc:
                self._post(self._export_done, path, 0, exc)

        self.log(f"[INFO] Exporting {path} ...")
        threading.Thread(target=worker, daemon=True).start()

    def _export_done(self, path, count, error):
        if self._closing:
            return
        if error is not None:
            self.log(f"[ERROR] Export failed: {error}")
            messagebox.showerror("Export", f"Export failed:\n{error}")
            return
        self.log(f"[OK] Exported {count} signal(s) to {path}")
        messagebox.showinfo("Export", f"{count} signal(s) written to\n{path}")

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

    def _on_auto_toggle(self):
        enabled = self.auto_enabled.get()
        hbnscan.save_settings(auto_scan=enabled)
        self.log(f"[AUTO] Automatic scanning {'enabled' if enabled else 'disabled'}.")
        self._reschedule_auto_scan()

    def _reschedule_auto_scan(self):
        """Cancel any pending timer and start a fresh cycle from now."""
        if self._auto_after_id is not None:
            try:
                self.after_cancel(self._auto_after_id)
            except tk.TclError:
                pass
            self._auto_after_id = None
        self.refresh_table()  # footer shows the schedule
        self._schedule_auto_scan(initial=False)

    def _schedule_auto_scan(self, initial=False):
        if self._closing:
            return
        settings = hbnscan.load_settings()
        if not self.auto_enabled.get():
            self._auto_after_id = self.after(60000, self._schedule_auto_scan)
            return

        interval_ms = max(1, int(settings["scan_interval_minutes"])) * 60000
        if hbnscan.in_scan_window(settings):
            if not self.is_scanning:
                self.log("[AUTO] Startup scan triggered." if initial
                         else f"[AUTO] Scheduled scan ({settings['scan_interval_minutes']}m).")
                self.start_scan_thread()
            delay = interval_ms
        else:
            wait = hbnscan.seconds_until_window(settings) * 1000
            delay = min(max(wait, 60000), interval_ms)
            if initial:
                self.log("[AUTO] Outside the scanning window; waiting.")
        self._auto_after_id = self.after(delay, self._schedule_auto_scan)

    def _rebuild_ui(self):
        """Recreate every widget so a new palette or text size takes effect."""
        for child in self.winfo_children():
            child.destroy()
        self.settings = hbnscan.load_settings()
        apply_theme(self.settings["theme"])
        self._apply_font_scale()
        self.configure(bg=BG_MAIN)
        self._setup_styles()
        self._build_ui()
        self.after(60, self._restore_sash)
        self._reset_scan_controls()
        self.refresh_table()
        self.refresh_briefing()

    def _persist_window_state(self):
        try:
            state = "zoomed" if self.state() == "zoomed" else "normal"
            values = {"window_geometry": self.geometry(), "window_state": state}
            if hasattr(self, "paned"):
                values["sash"] = self.paned.sash_coord(0)[1]
            hbnscan.save_settings(**values)
        except (tk.TclError, ValueError):
            pass

    def _on_close(self):
        self._persist_window_state()
        self._closing = True
        for after_id in (self._ui_after_id, self._auto_after_id):
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
        self.destroy()


if __name__ == "__main__":
    # One GUI only: a second copy would scan over the first.
    instance_lock = hbnscan.acquire_lock(hbnscan.APP_DIR / ".hb-briefing-gui.lock")
    if instance_lock is None:
        _root = tk.Tk()
        _root.withdraw()
        messagebox.showwarning("HB Briefing", "HB Briefing is already running.")
        _root.destroy()
        sys.exit(0)
    app = NewsScanApp()
    app.mainloop()
