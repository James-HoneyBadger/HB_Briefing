#!/usr/bin/env python3
"""
HB Briefing v1.0.0 - Standalone Desktop GUI

Tkinter front end over hbnscan.py: it never reimplements engine logic, only calls it. Scans run
in a worker thread and marshal back through a queue, so the interface stays responsive.
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
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import hbnscan
import hbworkspace as ws

FEEDS_PATH = hbnscan.FEEDS_PATH
TOPICS_PATH = hbnscan.TOPICS_PATH
REPORT_PATH = hbnscan.REPORT_PATH

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
    "midnight": {
        "bg_main": "#0a0e17", "bg_panel": "#111827", "bg_accent": "#1c2740",
        "bg_input": "#070a11", "bg_select": "#2b3a5c", "log_fg": "#a5b4cf",
        "fg_text": "#e5ecf8", "fg_muted": "#7b8aa8",
        "accent": "#60a5fa", "accent_hover": "#3b82f6",
        "green": "#22c55e", "red": "#f87171",
    },
    "solarized": {
        "bg_main": "#fdf6e3", "bg_panel": "#eee8d5", "bg_accent": "#dfd9c3",
        "bg_input": "#fffbf0", "bg_select": "#cfc9b3", "log_fg": "#586e75",
        "fg_text": "#073642", "fg_muted": "#657b83",
        "accent": "#b58900", "accent_hover": "#8f6c00",
        "green": "#859900", "red": "#dc322f",
    },
    "contrast": {
        "bg_main": "#000000", "bg_panel": "#0b0b0b", "bg_accent": "#1f1f1f",
        "bg_input": "#000000", "bg_select": "#3a3a3a", "log_fg": "#ffffff",
        "fg_text": "#ffffff", "fg_muted": "#d0d0d0",
        "accent": "#ffd400", "accent_hover": "#ffb700",
        "green": "#00ff88", "red": "#ff4d4d",
    },
}

if tuple(THEMES) != hbnscan.THEME_NAMES:
    raise RuntimeError(
        f"GUI palettes {tuple(THEMES)} do not match hbnscan.THEME_NAMES "
        f"{hbnscan.THEME_NAMES}; add the palette here or remove the name in the engine")

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


class SettingsForm:
    """The Tk variables the settings sections populate and the save handler reads.

    Declaring them here keeps the attribute contract in one place: a section that forgets
    to create one fails loudly at save time instead of silently saving a stale default.
    """

    FIELDS = ("profile_var", "profile_info", "since_var", "until_var", "provider_var",
              "model_var", "url_var", "key_var", "test_btn", "scan_var", "interval_var",
              "window_start_var", "window_end_var", "notify_var", "severity_var",
              "notify_test_btn", "theme_var", "scale_var")

    def __init__(self, window, settings):
        self.window = window
        self.settings = settings
        self.profile_choices = []
        self.profile_paths = {}
        for name in self.FIELDS:
            setattr(self, name, None)

    def require_complete(self):
        missing = [name for name in self.FIELDS if getattr(self, name) is None]
        if missing:
            raise RuntimeError("settings form is incomplete: " + ", ".join(missing))

    def clear_dates(self):
        self.since_var.set("")
        self.until_var.set("")


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
        self._auto_last_started = None

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._setup_styles()
        self._build_ui()
        self._restore_window()
        self.refresh_table()
        self.refresh_briefing()
        self.refresh_workspace()
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

    def _header_subtitle(self):
        """Name the profile in force: it can change without restarting."""
        return (f"v1.0.0  |  {hbnscan.PROFILE.get('name', hbnscan.APP_NAME)}  |  "
                "Weighted triage, ranked deep reads & written briefing")

    def _option_menu(self, parent, var, choices, command=None):
        """Every picker in the app looks and behaves the same."""
        menu = tk.OptionMenu(parent, var, *choices, command=command)
        menu.configure(bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                       relief="flat", font=("Sans", 9), highlightthickness=0, bd=0,
                       indicatoron=False, padx=8, pady=3, cursor="hand2")
        menu["menu"].configure(bg=BG_PANEL, fg=FG_TEXT, activebackground=BG_ACCENT,
                               activeforeground=AMBER, font=("Sans", 9))
        return menu

    def _section_title(self, parent, text):
        tk.Label(parent, text=text, bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(14, 2))

    def _build_ui(self):
        """Assemble the window from one builder per region."""
        self._build_header()
        self._build_toolbar()
        self._build_workspace()
        self._build_footer()

    def _build_header(self):
        header = tk.Frame(self, bg=BG_PANEL, height=64, padx=18, pady=10)
        header.pack(fill=tk.X, side=tk.TOP)

        title_box = tk.Frame(header, bg=BG_PANEL)
        title_box.pack(side=tk.LEFT)
        tk.Label(title_box, text="HB BRIEFING", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 14, "bold")).pack(anchor="w")
        self.header_sub_lbl = tk.Label(title_box, text=self._header_subtitle(),
                                       bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9))
        self.header_sub_lbl.pack(anchor="w")

        status_box = tk.Frame(header, bg=BG_PANEL)
        status_box.pack(side=tk.RIGHT)
        self.status_lbl = tk.Label(status_box, text="● IDLE  |  READY", bg=BG_ACCENT, fg=GREEN,
                                   font=("Monospace", 10, "bold"), padx=12, pady=6)
        self.status_lbl.pack(side=tk.RIGHT)

    def _build_toolbar(self):
        toolbar = tk.Frame(self, bg=BG_MAIN, padx=18, pady=10)
        toolbar.pack(fill=tk.X)

        self.run_btn = tk.Button(toolbar, text="▶ Run Scan Now", command=self.start_scan_thread,
                                 bg=AMBER, fg="#0f172a", activebackground=AMBER_HOVER,
                                 font=("Sans", 10, "bold"), relief="flat", padx=14, pady=6, cursor="hand2")
        self.run_btn.pack(side=tk.LEFT, padx=(0, 8))

        for label, command in (("✎ Edit Feeds", self.open_feeds_editor),
                               ("🎯 Topics", self.open_topics_editor),
                               ("⚙ Settings", self.open_settings),
                               ("⤓ Export", self.export_data),
                               ("📄 Open Report", self.open_report),
                               ("🗑 Clear Logs", self.clear_logs)):
            tk.Button(toolbar, text=label, command=command, bg=BG_ACCENT, fg=FG_TEXT,
                      activebackground=BG_SELECT, font=("Sans", 10), relief="flat",
                      padx=12, pady=6, cursor="hand2").pack(side=tk.LEFT, padx=4)

        self.cancel_btn = tk.Button(toolbar, text="■ Stop", command=self.cancel_scan,
                                    bg=BG_ACCENT, fg=FG_MUTED, activebackground=BG_SELECT,
                                    font=("Sans", 10), relief="flat", padx=12, pady=6,
                                    state="disabled", cursor="hand2")
        self.cancel_btn.pack(side=tk.LEFT, padx=4)

        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=120)
        self._progress_packed = False

        self.interval_var = tk.StringVar(value=str(self.settings["scan_interval_minutes"]))
        interval_menu = self._option_menu(
            toolbar, self.interval_var,
            [str(v) for v in (5, 10, 15, 30, 60, 120, 240)],
            command=self._on_interval_changed)
        interval_menu.pack(side=tk.RIGHT, padx=(0, 6))
        tk.Label(toolbar, text="every", bg=BG_MAIN, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.RIGHT, padx=(0, 4))

        tk.Checkbutton(toolbar, text="Auto-Scan", variable=self.auto_enabled,
                       command=self._on_auto_toggle,
                       bg=BG_MAIN, fg=FG_TEXT, selectcolor=BG_PANEL, activebackground=BG_MAIN,
                       font=("Sans", 10)).pack(side=tk.RIGHT, padx=(0, 8))

    def _build_workspace(self):
        paned = tk.PanedWindow(self, orient=tk.VERTICAL, bg=BG_MAIN, sashwidth=6,
                               sashrelief="flat", sashcursor="sb_v_double_arrow")
        self.paned = paned
        paned.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 14))

        top_frame = tk.Frame(paned, bg=BG_PANEL)
        notebook = ttk.Notebook(top_frame)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        today_tab = tk.Frame(notebook, bg=BG_PANEL)
        dossiers_tab = tk.Frame(notebook, bg=BG_PANEL)
        actions_tab = tk.Frame(notebook, bg=BG_PANEL)
        sources_tab = tk.Frame(notebook, bg=BG_PANEL)
        briefing_tab = tk.Frame(notebook, bg=BG_PANEL)
        notebook.add(today_tab, text="  Today  ")
        notebook.add(dossiers_tab, text="  Dossiers  ")
        notebook.add(actions_tab, text="  Actions  ")
        notebook.add(sources_tab, text="  Sources  ")
        notebook.add(briefing_tab, text="  AI Briefing  ")

        self._build_signals_tab(today_tab)
        self._build_dossiers_tab(dossiers_tab)
        self._build_actions_tab(actions_tab)
        self._build_sources_tab(sources_tab)
        self._build_briefing_tab(briefing_tab)

        paned.add(top_frame, height=360)
        self._build_log_pane(paned)

    def _build_signals_tab(self, signals_tab):
        self.coverage_lbl = tk.Label(signals_tab, text="Loading coverage…", bg=BG_PANEL,
                                     fg=FG_MUTED, font=("Sans", 9), anchor="w", padx=10,
                                     pady=4)
        self.coverage_lbl.pack(fill=tk.X)
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
        limit_menu = self._option_menu(search_bar, self.limit_var,
                                       [str(v) for v in hbnscan.ROW_LIMITS],
                                       command=self._on_limit_changed)
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
            ("filter_recent", "Within"),
            ("row_order", "Order"),
        )):
            tk.Label(filter_bar, text=f"{label}:", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Sans", 9)).pack(side=tk.LEFT, padx=(0 if position == 0 else 12, 4))
            var = tk.StringVar(value=saved[key])
            self.filter_vars[key] = var
            menu = self._option_menu(
                filter_bar, var, hbnscan.setting_choices(key),
                command=lambda value, name=key: self._on_filter_changed(name, value))
            menu.pack(side=tk.LEFT)

        cols = ("time", "fresh", "severity", "category", "title", "analysis")
        tree_wrap = tk.Frame(signals_tab, bg=BG_PANEL)
        tree_wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(tree_wrap, columns=cols, show="headings", selectmode="browse")
        self._heading_labels = {"time": "Detected", "fresh": "Published",
                                "severity": "Sev", "category": "Category",
                                "title": "Headline", "analysis": "Assessment"}
        for column, label in self._heading_labels.items():
            self.tree.heading(column, text=label,
                              command=lambda col=column: self._sort_by_column(col))
        self.tree.column("time", width=126, stretch=False)
        self.tree.column("fresh", width=92, stretch=False)
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
        self.row_menu.add_command(label="Detach from story", command=self._detach_story)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="Not relevant (dismiss)", command=self._dismiss_selected)
        self.tree.bind("<Delete>", lambda _e: self._dismiss_selected())
        self.bind("<Control-f>", lambda _e: self.search_entry.focus_set())
        self.row_links = {}

        detail = tk.Frame(signals_tab, bg=BG_INPUT)
        detail.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.detail_text = tk.Text(detail, height=4, bg=BG_INPUT, fg=FG_TEXT,
                                   insertbackground=FG_TEXT, font=("Sans", 9),
                                   relief="flat", padx=10, pady=6, wrap="word",
                                   state="disabled")
        self.detail_text.pack(fill=tk.X, expand=True)
        self.detail_text.tag_configure("key", foreground=AMBER, font=("Sans", 9, "bold"))
        self.tree.bind("<<TreeviewSelect>>", self._on_row_select)

    def _build_briefing_tab(self, briefing_tab):
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

    # -- dossiers -----------------------------------------------------------

    def _build_dossiers_tab(self, parent):
        bar = tk.Frame(parent, bg=BG_PANEL)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        for label, command in (("New dossier", self._new_dossier),
                               ("Edit", self._edit_dossier),
                               ("Add current signal", self._add_current_to_dossier),
                               ("Add note", self._add_dossier_note),
                               ("Export report", self._export_dossier),
                               ("Close / reopen", self._toggle_dossier_status),
                               ("Delete", self._delete_dossier)):
            tk.Button(bar, text=label, command=command, bg=BG_ACCENT, fg=FG_TEXT,
                      activebackground=BG_SELECT, font=("Sans", 9), relief="flat",
                      padx=9, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Label(bar, text="report preview updates when you pick a dossier",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 8)).pack(side=tk.RIGHT, padx=6)

        body = tk.Frame(parent, bg=BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        left = tk.Frame(body, bg=BG_PANEL)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.dossier_list = tk.Listbox(left, width=30, bg=BG_INPUT, fg=FG_TEXT,
                                       selectbackground=BG_SELECT, selectforeground=AMBER,
                                       relief="flat", font=("Sans", 10), activestyle="none",
                                       exportselection=False)
        self.dossier_list.pack(side=tk.LEFT, fill=tk.Y)
        dossier_scroll = ttk.Scrollbar(left, orient="vertical",
                                       command=self.dossier_list.yview)
        self.dossier_list.configure(yscrollcommand=dossier_scroll.set)
        dossier_scroll.pack(side=tk.LEFT, fill=tk.Y)
        self.dossier_list.bind("<<ListboxSelect>>", lambda _e: self._preview_dossier())

        self.dossier_text = tk.Text(body, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                                    font=("Sans", 9), relief="flat", padx=12, pady=8,
                                    wrap="word", state="disabled")
        self.dossier_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        self.dossier_text.tag_configure("h", font=("Sans", 11, "bold"), foreground=AMBER,
                                        spacing1=8, spacing3=4)
        self.dossier_text.tag_configure("muted", foreground=FG_MUTED)
        self.dossier_ids = []

    # -- actions ------------------------------------------------------------

    def _build_actions_tab(self, parent):
        bar = tk.Frame(parent, bg=BG_PANEL)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        for label, command in (("New action", self._new_action),
                               ("Edit", self._edit_action),
                               ("Complete / reopen", self._toggle_action),
                               ("Delete", self._delete_action),
                               ("Export list", self._export_actions)):
            tk.Button(bar, text=label, command=command, bg=BG_ACCENT, fg=FG_TEXT,
                      activebackground=BG_SELECT, font=("Sans", 9), relief="flat",
                      padx=9, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)

        wrap = tk.Frame(parent, bg=BG_PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        columns = ("id", "status", "priority", "due", "title", "dossier")
        self.actions_tree = ttk.Treeview(wrap, columns=columns, show="headings",
                                         selectmode="browse")
        for column, label, width in (("id", "ID", 44), ("status", "Status", 96),
                                     ("priority", "Priority", 72), ("due", "Due", 92),
                                     ("title", "Action", 330), ("dossier", "Dossier", 160)):
            self.actions_tree.heading(column, text=label)
            self.actions_tree.column(column, width=width, stretch=(column == "title"),
                                     anchor="center" if column in ("id", "priority") else "w")
        action_scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.actions_tree.yview)
        self.actions_tree.configure(yscrollcommand=action_scroll.set)
        self.actions_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        action_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.actions_tree.bind("<Double-1>", lambda _e: self._edit_action())
        self.action_ids = {}

    # -- sources ------------------------------------------------------------

    def _build_sources_tab(self, parent):
        bar = tk.Frame(parent, bg=BG_PANEL)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        for label, command in (("Refresh", self.refresh_workspace),
                               ("Scan now", self.start_scan_thread),
                               ("Edit feeds", self.open_feeds_editor)):
            tk.Button(bar, text=label, command=command, bg=BG_ACCENT, fg=FG_TEXT,
                      activebackground=BG_SELECT, font=("Sans", 9), relief="flat",
                      padx=9, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=3)
        tk.Label(bar, text="status comes from each feed's last fetch; a failure keeps the "
                           "previous success time visible",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 8)).pack(side=tk.RIGHT, padx=6)

        wrap = tk.Frame(parent, bg=BG_PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        columns = ("source", "status", "last_success", "http", "items", "error")
        self.sources_tree = ttk.Treeview(wrap, columns=columns, show="headings")
        for column, label, width in (("source", "Source", 220), ("status", "Status", 76),
                                     ("last_success", "Last success", 150),
                                     ("http", "HTTP", 58), ("items", "New", 56),
                                     ("error", "Last error", 420)):
            self.sources_tree.heading(column, text=label)
            self.sources_tree.column(column, width=width, stretch=(column == "error"),
                                     anchor="center" if column in ("http", "items") else "w")
        source_scroll = ttk.Scrollbar(wrap, orient="vertical",
                                      command=self.sources_tree.yview)
        self.sources_tree.configure(yscrollcommand=source_scroll.set)
        self.sources_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        source_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.sources_tree.tag_configure("bad", foreground=RED)
        self.sources_tree.tag_configure("warn", foreground=AMBER)
        self.sources_tree.tag_configure("ok", foreground=GREEN)

    # -- workspace refresh --------------------------------------------------

    def refresh_workspace(self):
        """Reload coverage, dossiers, actions and source health from the engine."""
        if self._closing or not hasattr(self, "dossier_list"):
            return
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                dossiers = ws.list_dossiers(conn)
                actions = ws.list_actions(conn)
                summary = ws.coverage_summary(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.log(f"[WARN] Could not read the workspace: {exc}")
            return

        self.coverage_lbl.configure(text=ws.describe_coverage(summary))

        selected = self._selected_dossier_id()
        self.dossier_list.delete(0, tk.END)
        self.dossier_ids = []
        for row in dossiers:
            status = "" if row["status"] == "open" else f"  [{row['status']}]"
            self.dossier_list.insert(
                tk.END, f"{row['name']}  ({row['items']} signal(s)){status}")
            self.dossier_ids.append(row["id"])
        if selected in self.dossier_ids:
            self.dossier_list.selection_set(self.dossier_ids.index(selected))
        elif self.dossier_ids:
            self.dossier_list.selection_set(0)
        self._preview_dossier()

        for iid in self.actions_tree.get_children():
            self.actions_tree.delete(iid)
        self.action_ids = {}
        for row in actions:
            iid = f"a{row['id']}"
            self.actions_tree.insert(
                "", tk.END, iid=iid,
                values=(row["id"], row["status"], row["priority"], row["due_date"] or "",
                        row["title"], row["dossier_name"] or ""))
            self.action_ids[iid] = row["id"]

        for iid in self.sources_tree.get_children():
            self.sources_tree.delete(iid)
        for index, entry in enumerate(summary["health"]):
            tag = {"failing": "bad", "never": "warn", "stale": "warn"}.get(entry["status"], "ok")
            self.sources_tree.insert(
                "", tk.END, iid=f"s{index}",
                values=(entry["source"], entry["status"],
                        entry["last_success"].replace("T", " ") or "never",
                        entry["http_status"] or "", entry["items_new"], entry["error"]),
                tags=(tag,))

    def _selected_dossier_id(self):
        if not hasattr(self, "dossier_list"):
            return None
        selection = self.dossier_list.curselection()
        if not selection:
            return None
        index = int(selection[0])
        return self.dossier_ids[index] if index < len(self.dossier_ids) else None

    def _preview_dossier(self):
        dossier_id = self._selected_dossier_id()
        self.dossier_text.configure(state="normal")
        self.dossier_text.delete("1.0", tk.END)
        if dossier_id is None:
            self.dossier_text.insert(
                tk.END,
                "No dossier selected.\n\nCreate one with New dossier, select a signal in "
                "Today, then press Add current signal. The report preview here is built from "
                "stored signals only and never invents a conclusion.", "muted")
            self.dossier_text.configure(state="disabled")
            return
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                report = ws.dossier_report(conn, dossier_id)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            self.dossier_text.insert(tk.END, f"Could not build the report: {exc}", "muted")
            self.dossier_text.configure(state="disabled")
            return
        for line in ws.render_report_markdown(report).splitlines():
            if line.startswith("## "):
                self.dossier_text.insert(tk.END, line[3:] + "\n", "h")
            elif line.startswith("# "):
                self.dossier_text.insert(tk.END, line[2:] + "\n", "h")
            else:
                self.dossier_text.insert(tk.END, line + "\n")
        self.dossier_text.configure(state="disabled")

    # -- dossier commands ---------------------------------------------------

    def _new_dossier(self):
        self._dossier_dialog()

    def _edit_dossier(self):
        dossier_id = self._selected_dossier_id()
        if dossier_id is None:
            messagebox.showinfo("Dossiers", "Select a dossier first.", parent=self)
            return
        self._dossier_dialog(dossier_id)

    def _dossier_dialog(self, dossier_id=None):
        existing = None
        if dossier_id is not None:
            try:
                conn = hbnscan.connect(timeout=5)
                try:
                    existing = ws.resolve_dossier(conn, dossier_id)
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                self.log(f"[ERROR] Could not read the dossier: {exc}")
                return
            if existing is None:
                messagebox.showerror("Dossiers", "That dossier no longer exists.", parent=self)
                return

        win = tk.Toplevel(self)
        win.title("HB Briefing - Dossier")
        win.geometry("560x430")
        win.configure(bg=BG_PANEL)
        win.transient(self)

        tk.Label(win, text="NAME", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        name_var = tk.StringVar(value=existing["name"] if existing else "")
        tk.Entry(win, textvariable=name_var, bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat", font=("Sans", 10)
                 ).pack(fill=tk.X, padx=16, ipady=4)

        tk.Label(win, text="KIND", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        kind_var = tk.StringVar(value=existing["kind"] if existing else "subject")
        self._option_menu(win, kind_var, list(ws.DOSSIER_KINDS)).pack(anchor="w", padx=16)

        tk.Label(win, text="SCOPE — the question this dossier answers", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(12, 2))
        summary_text = tk.Text(win, height=6, bg=BG_INPUT, fg=FG_TEXT,
                               insertbackground=FG_TEXT, relief="flat", wrap="word",
                               font=("Sans", 9), padx=8, pady=6)
        summary_text.pack(fill=tk.BOTH, expand=True, padx=16)
        if existing and existing["summary"]:
            summary_text.insert("1.0", existing["summary"])

        def save():
            try:
                if existing:
                    ws.update_dossier(conn_writer(), existing["id"], name=name_var.get(),
                                      kind=kind_var.get(),
                                      summary=summary_text.get("1.0", tk.END).strip())
                else:
                    ws.create_dossier(conn_writer(), name_var.get(),
                                      summary_text.get("1.0", tk.END).strip(),
                                      kind_var.get())
            except (ValueError, sqlite3.Error) as exc:
                messagebox.showerror("Dossier", str(exc), parent=win)
                return
            finally:
                close_writer()
            self.log(f"[OK] Dossier saved: {name_var.get().strip()}")
            win.destroy()
            self.refresh_workspace()

        writer = {"conn": None}

        def conn_writer():
            writer["conn"] = hbnscan.connect(timeout=10)
            return writer["conn"]

        def close_writer():
            if writer["conn"] is not None:
                writer["conn"].close()
                writer["conn"] = None

        buttons = tk.Frame(win, bg=BG_PANEL)
        buttons.pack(fill=tk.X, padx=16, pady=10)
        tk.Button(buttons, text="Cancel", command=win.destroy, bg=BG_ACCENT, fg=FG_MUTED,
                  relief="flat", padx=12, pady=6, font=("Sans", 10)).pack(side=tk.LEFT)
        tk.Button(buttons, text="Save", command=save, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=16, pady=6
                  ).pack(side=tk.RIGHT)

    def _add_current_to_dossier(self):
        dossier_id = self._selected_dossier_id()
        iid = self._selected_row()
        if dossier_id is None:
            messagebox.showinfo("Dossiers", "Select a dossier on the left first.", parent=self)
            return
        if iid is None:
            messagebox.showinfo("Dossiers", "Select a signal in Today first.", parent=self)
            return
        item_hash = self.row_keys.get(iid)
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                added = ws.add_item(conn, dossier_id, item_hash)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Dossiers", str(exc), parent=self)
            return
        self.log("[OK] Signal added to the dossier." if added
                 else "[INFO] That signal is already in the dossier.")
        self.refresh_workspace()

    def _add_dossier_note(self):
        dossier_id = self._selected_dossier_id()
        if dossier_id is None:
            messagebox.showinfo("Dossiers", "Select a dossier first.", parent=self)
            return
        text = simpledialog.askstring("Open question", "Note or open question:", parent=self)
        if not text:
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                ws.add_note(conn, text, dossier_id=dossier_id)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Dossiers", str(exc), parent=self)
            return
        self.refresh_workspace()

    def _export_dossier(self):
        dossier_id = self._selected_dossier_id()
        if dossier_id is None:
            messagebox.showinfo("Dossiers", "Select a dossier first.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="Export dossier report", defaultextension=".md",
            initialfile="dossier-report.md",
            filetypes=[("Markdown", "*.md"), ("HTML", "*.html"), ("JSON", "*.json")])
        if not path:
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                saved, count = ws.export_report(conn, dossier_id, path)
            finally:
                conn.close()
        except (ValueError, OSError, sqlite3.Error) as exc:
            messagebox.showerror("Export", str(exc), parent=self)
            return
        self.log(f"[OK] Dossier report ({count} signal(s)) written to {saved}")
        messagebox.showinfo("Export", f"Report written to\n{saved}", parent=self)

    def _toggle_dossier_status(self):
        dossier_id = self._selected_dossier_id()
        if dossier_id is None:
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                row = ws.resolve_dossier(conn, dossier_id)
                new_status = "closed" if row["status"] == "open" else "open"
                ws.update_dossier(conn, dossier_id, status=new_status)
            finally:
                conn.close()
        except (ValueError, sqlite3.Error) as exc:
            messagebox.showerror("Dossiers", str(exc), parent=self)
            return
        self.refresh_workspace()

    def _delete_dossier(self):
        dossier_id = self._selected_dossier_id()
        if dossier_id is None:
            return
        index = self.dossier_list.curselection()
        name = self.dossier_list.get(index[0]) if index else str(dossier_id)
        if not messagebox.askyesno(
                "Delete dossier",
                f"Delete {name}?\n\nThe signals and actions it references are kept; only the "
                "dossier, its notes and its membership list are removed.", parent=self):
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                ws.delete_dossier(conn, dossier_id)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            messagebox.showerror("Dossiers", str(exc), parent=self)
            return
        self.log(f"[OK] Dossier deleted: {name}")
        self.refresh_workspace()

    # -- action commands ----------------------------------------------------

    def _selected_action_id(self):
        selection = self.actions_tree.selection()
        return self.action_ids.get(selection[0]) if selection else None

    def _new_action(self):
        self._action_dialog()

    def _edit_action(self):
        action_id = self._selected_action_id()
        if action_id is None:
            messagebox.showinfo("Actions", "Select an action first.", parent=self)
            return
        self._action_dialog(action_id)

    def _action_dialog(self, action_id=None):
        existing = None
        dossier_rows = []
        if action_id is not None:
            try:
                conn = hbnscan.connect(timeout=5)
                try:
                    existing = conn.execute(
                        "SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                self.log(f"[ERROR] Could not read the action: {exc}")
                return
            if existing is None:
                messagebox.showerror("Actions", "That action no longer exists.", parent=self)
                return
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                dossier_rows = ws.list_dossiers(conn, include_archived=True)
            finally:
                conn.close()
        except sqlite3.Error:
            dossier_rows = []

        win = tk.Toplevel(self)
        win.title("HB Briefing - Action")
        win.geometry("560x430")
        win.configure(bg=BG_PANEL)
        win.transient(self)

        tk.Label(win, text="ACTION", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        title_var = tk.StringVar(value=existing["title"] if existing else "")
        tk.Entry(win, textvariable=title_var, bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat", font=("Sans", 10)
                 ).pack(fill=tk.X, padx=16, ipady=4)

        row = tk.Frame(win, bg=BG_PANEL)
        row.pack(fill=tk.X, padx=16, pady=(10, 0))
        priority_var = tk.StringVar(value=existing["priority"] if existing else "medium")
        due_var = tk.StringVar(value=(existing["due_date"] or "") if existing else "")
        tk.Label(row, text="Priority:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._option_menu(row, priority_var, list(ws.ACTION_PRIORITIES)).pack(
            side=tk.LEFT, padx=(0, 12))
        tk.Label(row, text="Due (YYYY-MM-DD):", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        tk.Entry(row, textvariable=due_var, width=12, bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat", font=("Monospace", 9)
                 ).pack(side=tk.LEFT, ipady=3)

        tk.Label(win, text="DOSSIER", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        dossier_names = ["(none)"] + [row["name"] for row in dossier_rows]
        current_dossier = "(none)"
        if existing and existing["dossier_id"]:
            for row in dossier_rows:
                if row["id"] == existing["dossier_id"]:
                    current_dossier = row["name"]
        dossier_var = tk.StringVar(value=current_dossier)
        self._option_menu(win, dossier_var, dossier_names).pack(anchor="w", padx=16)

        tk.Label(win, text="DETAIL", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 10, "bold")).pack(anchor="w", padx=16, pady=(10, 2))
        detail_text = tk.Text(win, height=5, bg=BG_INPUT, fg=FG_TEXT,
                              insertbackground=FG_TEXT, relief="flat", wrap="word",
                              font=("Sans", 9), padx=8, pady=6)
        detail_text.pack(fill=tk.BOTH, expand=True, padx=16)
        if existing and existing["detail"]:
            detail_text.insert("1.0", existing["detail"])

        def save():
            dossier_id = None
            for row in dossier_rows:
                if row["name"] == dossier_var.get():
                    dossier_id = row["id"]
                    break
            try:
                conn = hbnscan.connect(timeout=10)
                try:
                    payload = dict(title=title_var.get(), detail=detail_text.get(
                        "1.0", tk.END).strip(), priority=priority_var.get(),
                        due_date=due_var.get().strip(), dossier_id=dossier_id)
                    if existing:
                        ws.update_action(conn, existing["id"], **payload)
                    else:
                        ws.create_action(conn, **payload)
                finally:
                    conn.close()
            except (ValueError, sqlite3.Error) as exc:
                messagebox.showerror("Action", str(exc), parent=win)
                return
            self.log(f"[OK] Action saved: {title_var.get().strip()[:60]}")
            win.destroy()
            self.refresh_workspace()

        buttons = tk.Frame(win, bg=BG_PANEL)
        buttons.pack(fill=tk.X, padx=16, pady=10)
        tk.Button(buttons, text="Cancel", command=win.destroy, bg=BG_ACCENT, fg=FG_MUTED,
                  relief="flat", padx=12, pady=6, font=("Sans", 10)).pack(side=tk.LEFT)
        tk.Button(buttons, text="Save", command=save, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=16, pady=6
                  ).pack(side=tk.RIGHT)

    def _toggle_action(self):
        action_id = self._selected_action_id()
        if action_id is None:
            messagebox.showinfo("Actions", "Select an action first.", parent=self)
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                row = conn.execute(
                    "SELECT status FROM actions WHERE id = ?", (action_id,)).fetchone()
                if row is None:
                    return
                ws.complete_action(conn, action_id, undo=row["status"] == "done")
            finally:
                conn.close()
        except sqlite3.Error as exc:
            messagebox.showerror("Actions", str(exc), parent=self)
            return
        self.refresh_workspace()

    def _delete_action(self):
        action_id = self._selected_action_id()
        if action_id is None:
            return
        if not messagebox.askyesno("Delete action", "Delete this action?", parent=self):
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                ws.delete_action(conn, action_id)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            messagebox.showerror("Actions", str(exc), parent=self)
            return
        self.refresh_workspace()

    def _export_actions(self):
        path = filedialog.asksaveasfilename(
            parent=self, title="Export action list", defaultextension=".md",
            initialfile="action-list.md", filetypes=[("Markdown", "*.md")])
        if not path:
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                rows = ws.list_actions(conn)
            finally:
                conn.close()
            hbnscan.write_atomic(Path(path), ws.render_action_markdown(rows))
        except (OSError, sqlite3.Error) as exc:
            messagebox.showerror("Export", str(exc), parent=self)
            return
        self.log(f"[OK] {len(rows)} action(s) written to {path}")
        messagebox.showinfo("Export", f"Action list written to\n{path}", parent=self)

    def _build_log_pane(self, paned):
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

    def _build_footer(self):
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
        self.refresh_workspace()

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
            return {key: hbnscan.DEFAULT_SETTINGS[key] for key in hbnscan.FILTER_SETTING_KEYS}
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

        # One engine call for the grid, the web API and the exports: a filter cannot mean
        # two things, and paging/ordering stay identical across the front ends.
        selection = {
            "severity": filters["filter_severity"],
            "category": filters["filter_category"],
            "group": filters["filter_group"],
            "recent_days": filters["filter_recent"],
            "query": query,
            "limit": limit,
        }

        flagged_total = 0
        high_total = 0
        matched = 0
        new_count = 0
        rows = []
        totals = (0, 0)
        marker = None
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                rows, matched = hbnscan.select_signals(
                    conn, selection, order=filters["row_order"], limit=limit)
                totals = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(severity = 'high'), 0) "
                    f"FROM seen_items WHERE {hbnscan.VISIBLE_SQL}"
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
        for idx, row in enumerate(rows):
            severity = (row["severity"] or "none").lower()
            seen_at = row["seen_at"] or ""
            iid = f"row_{idx}"
            tag = f"sev_{severity}" if severity in ("high", "medium", "low") else ""
            is_new = bool(previous_scan) and seen_at > previous_scan
            new_count += is_new
            self.tree.insert(
                "", tk.END, iid=iid,
                values=(("NEW  " if is_new else "") + seen_at.replace("T", " "),
                        (row["published"] or "")[:10] or "undated",
                        severity.upper(), row["category"] or "other", row["title"],
                        self._assessment_cell(row)),
                tags=(tag,) if tag else ()
            )
            self.row_links[iid] = row["link"]
            self.row_keys[iid] = row["item_hash"]

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

        ai_mode = (hbnscan.LLM_MODEL if hbnscan.deepseek_configured()
                   else "Offline heuristic")
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
                 f"  |  MIT Licensed — see LICENSE"
        )

    @staticmethod
    def _assessment_cell(row):
        """Assessment plus a text tag for declared interest, so colour is not the only cue."""
        text = hbnscan.strip_provenance(row["summary"])
        layer = hbnscan.layer_of(row["source_layer"], row["summary"])
        if layer == "watchlist":
            return "★ watchlist · " + text
        if layer == "topic":
            return "◆ topic · " + text
        return text

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
        try:
            conn = hbnscan.connect(timeout=5)
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
                "A briefing is written once per scan, but only when an LLM API key is "
                "configured in .env — it needs a model call to rank and merge the signals.",
                "muted"
            )
        self.brief_text.configure(state="disabled")

    COLUMN_ORDERS = {"time": "detected", "fresh": "newest", "severity": "severity",
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
        self._show_detail({"item_hash": self.row_keys.get(selection[0], ""),
                           "link": self.row_links.get(selection[0], ""),
                           "title": values[4] if len(values) > 4 else ""})

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
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                sql = ("SELECT summary, action, facts, entities, confidence, deep_read, "
                       "published, seen_at, source_layer, cluster_key FROM seen_items ")
                if row.get("item_hash"):
                    record = conn.execute(
                        sql + "WHERE item_hash = ?", (row["item_hash"],)).fetchone()
                if record is None and row.get("link"):
                    record = conn.execute(
                        sql + "WHERE link = ? ORDER BY rowid DESC LIMIT 1",
                        (row["link"],)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            record = None

        if not record:
            self.detail_text.insert(tk.END, row["title"], "key")
            self.detail_text.configure(state="disabled")
            return

        (summary, action, facts, entities, confidence, deep_read, published, seen_at,
         source_layer, cluster_key) = record

        def field(label, value):
            if value:
                self.detail_text.insert(tk.END, f"{label}: ", "key")
                self.detail_text.insert(tk.END, f"{value}\n")

        field("Assessment", hbnscan.strip_provenance(summary))
        field("Decided by", hbnscan.layer_of(source_layer, summary))
        field("Next step", action)
        if published:
            field("Published", f"{published[:10]} "
                               f"({hbnscan.freshness_label(published, seen_at)})")
        else:
            field("Published", "undated (the feed supplied no date)")
        field("Detected", (seen_at or "").replace("T", " "))
        try:
            cluster_conn = hbnscan.connect(timeout=5)
            try:
                story = hbnscan.cluster_summary(cluster_conn, cluster_key)
            finally:
                cluster_conn.close()
        except sqlite3.Error:
            story = None
        if story and story["count"] > 1:
            field("Story", f"{story['count']} item(s) from "
                           f"{len(story['sources'])} publisher(s): "
                           + ", ".join(story["sources"]))
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
        headline = str(self.tree.item(iid, "values")[4])
        self.clipboard_clear()
        self.clipboard_append(headline)
        self.log(f"[INFO] Copied: {headline[:70]}")

    def _detach_story(self):
        """Undo a story merge for the selected row."""
        iid = self._selected_row()
        key = self.row_keys.get(iid) if iid else None
        if not key:
            return
        try:
            conn = hbnscan.connect(timeout=10)
            try:
                changed = hbnscan.detach_from_cluster(conn, key)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            self.log(f"[ERROR] Could not detach the row: {exc}")
            return
        self.log("[OK] Row detached into its own story." if changed
                 else "[INFO] No such signal.")
        self.refresh_table()

    def _dismiss_selected(self):
        iid = self._selected_row()
        if not iid:
            return
        key = self.row_keys.get(iid)
        if not key:
            return
        headline = str(self.tree.item(iid, "values")[4])
        if not messagebox.askyesno(
                "Dismiss signal",
                f"Mark as not relevant?\n\n{headline[:160]}\n\n"
                "It leaves the report, table and exports but stays in the database.",
                parent=self):
            return
        try:
            conn = hbnscan.connect(timeout=10)
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
            hbnscan.write_atomic(FEEDS_PATH, txt.get("1.0", tk.END).strip() + "\n")
            self.refresh_table()
            self.log("[INFO] Updated feeds.txt configuration.")
            win.destroy()

        tk.Button(win, text="Save Feeds", command=save_feeds, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=14, pady=6).pack(pady=10)

    def open_topics_editor(self):
        """Edit topics.txt: the standing subjects the monitor watches for."""
        win = tk.Toplevel(self)
        win.title("HB Briefing - Standing Topics")
        win.geometry("760x620")
        win.configure(bg=BG_PANEL)
        win.transient(self)

        tk.Label(win, text="STANDING TOPICS", bg=BG_PANEL, fg=AMBER,
                 font=("Sans", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        tk.Label(win,
                 text="One subject per line. A matching item is flagged on subject alone,\n"
                      "before any enforcement, filing or litigation language is considered.\n\n"
                      "    broadband                     medium interest\n"
                      "    broadband | high              chose an interest level\n"
                      "    broadband | high | Telecom    chose a level and a group\n\n"
                      "A comment beginning ## opens a group: every topic below it until the\n"
                      "next heading belongs to that group, so a block can be switched on or\n"
                      "off by commenting it out. Line comments use a single #.\n\n"
                      "The active profile contributes its own topics first; a line here with\n"
                      "the same wording replaces the profile's version, so you can re-grade\n"
                      "or re-group a shipped topic without copying the whole set.",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Monospace", 9), justify="left", anchor="w"
                 ).pack(fill=tk.X, padx=14)

        txt = tk.Text(win, bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                      font=("Monospace", 10), wrap="none", undo=True)
        txt.pack(fill=tk.BOTH, expand=True, padx=14, pady=(10, 4))
        if TOPICS_PATH.exists():
            txt.insert("1.0", TOPICS_PATH.read_text(encoding="utf-8"))

        def current_text():
            return txt.get("1.0", tk.END).strip() + "\n"

        as_feeds = tk.BooleanVar(value=bool(hbnscan.load_settings()["topics_as_feeds"]))
        count_lbl = tk.Label(win, text="", bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9), anchor="w")

        def update_count():
            text = current_text()
            active = [ln for ln in text.splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]
            feeds = min(len(active), hbnscan.TOPIC_FEED_LIMIT)
            count_lbl.config(
                text=f"{len(active)} active topic(s) - {feeds} Google News feed(s) - "
                     f"limit {hbnscan.TOPIC_LIMIT} topics",
                fg=RED if len(active) > hbnscan.TOPIC_LIMIT else FG_MUTED,
            )

        def insert_library():
            """Append the shipped topic library, skipping anything already present."""
            example = hbnscan.APP_DIR / "topics.txt.example"
            if not example.exists():
                messagebox.showwarning("HB Briefing", "topics.txt.example is not installed.",
                                       parent=win)
                return
            existing = {ln.strip().lstrip("#").strip().lower()
                        for ln in current_text().splitlines() if ln.strip()}
            added = 0
            block = []
            for line in example.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("# =") or stripped.startswith("# -"):
                    continue
                if stripped and stripped.lstrip("#").strip().lower() in existing:
                    continue
                block.append(line)
                if stripped and not (stripped.startswith("#") and
                                     not stripped.lstrip("# ").strip()):
                    added += 1
            if not block:
                self.log("[INFO] Topic library already present.")
                return
            txt.insert(tk.END, ("\n\n" if txt.get("1.0", tk.END).strip() else "") +
                       "\n".join(block) + "\n")
            update_count()
            self.log(f"[INFO] Added the topic library ({added} line(s)). Uncomment what you want.")

        def save_topics():
            text = current_text()
            hbnscan.write_atomic(TOPICS_PATH, text)
            hbnscan.save_settings(topics_as_feeds=bool(as_feeds.get()))
            hbnscan.load_topics()  # refresh cache and warnings
            for warning in hbnscan.topic_warnings():
                self.log(f"[WARN] topics.txt {warning}")
            topics = hbnscan.load_topics()
            groups = hbnscan.topic_groups(topics)
            detail = ", ".join(f"{name} ({len(members)})" for name, members in groups[:6])
            self.log(f"[INFO] Saved topics.txt: {len(topics)} active topic(s)"
                     + (f" - {detail}" if detail else ""))
            self.refresh_table()
            win.destroy()

        footer = tk.Frame(win, bg=BG_PANEL)
        footer.pack(fill=tk.X, padx=14, pady=(0, 12))
        tk.Checkbutton(footer, text="Search Google News for each active topic",
                       variable=as_feeds, command=update_count,
                       bg=BG_PANEL, fg=FG_TEXT, selectcolor=BG_INPUT, activebackground=BG_PANEL,
                       font=("Sans", 9), anchor="w").pack(side=tk.LEFT)
        tk.Button(footer, text="Insert Topic Library", command=insert_library,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT,
                  font=("Sans", 9), relief="flat", padx=10, pady=5).pack(side=tk.RIGHT, padx=4)
        tk.Button(footer, text="Save Topics", command=save_topics, bg=AMBER, fg="#0f172a",
                  font=("Sans", 10, "bold"), relief="flat", padx=14, pady=6).pack(side=tk.RIGHT, padx=4)
        count_lbl.pack(fill=tk.X, padx=14, pady=(0, 4))
        update_count()
        txt.bind("<KeyRelease>", lambda _event: update_count())

    def open_settings(self):
        """One dialog assembled from one method per section."""
        win = tk.Toplevel(self)
        win.title("HB Briefing - Settings")
        win.geometry("600x900")
        win.configure(bg=BG_PANEL)
        win.transient(self)

        ctx = SettingsForm(win, hbnscan.load_settings())
        self._settings_profile_section(win, ctx)
        self._settings_window_section(win, ctx)
        self._settings_ai_section(win, ctx)
        self._settings_scan_section(win, ctx)
        self._settings_notify_section(win, ctx)
        self._settings_appearance_section(win, ctx)
        self._settings_data_section(win, ctx)
        ctx.require_complete()

        buttons = tk.Frame(win, bg=BG_PANEL)
        buttons.pack(fill=tk.X, padx=16, pady=8)
        tk.Button(buttons, text="Clear dates", command=ctx.clear_dates, bg=BG_ACCENT,
                  fg=FG_MUTED, font=("Sans", 10), relief="flat", padx=12, pady=6
                  ).pack(side=tk.LEFT)
        tk.Button(buttons, text="Save", command=lambda: self._save_settings(ctx),
                  bg=AMBER, fg="#0f172a", font=("Sans", 10, "bold"), relief="flat",
                  padx=16, pady=6).pack(side=tk.RIGHT)

    def _settings_profile_section(self, win, ctx):
        self._section_title(win, "PROFILE")
        tk.Label(win,
                 text="A profile bundles the domain rules (categories, prompts, keywords), "
                      "the topic set and the standing targets.\n"
                      "Add your own .json to the profiles/ folder to extend this list; "
                      "topics.txt and watchlist.txt still layer on top.",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9), justify="left", anchor="w",
                 wraplength=560).pack(fill=tk.X, padx=16)

        ctx.profile_choices = hbnscan.available_profiles()
        ctx.profile_paths = {name: path for name, path in ctx.profile_choices}
        ctx.profile_var = tk.StringVar()
        active_path = hbnscan.active_profile_path()
        current_profile = ""
        for name, path in ctx.profile_choices:
            if path and Path(path) == active_path:
                current_profile = name
                break
        ctx.profile_var.set(current_profile or ctx.profile_choices[0][0])

        profile_row = tk.Frame(win, bg=BG_PANEL)
        profile_row.pack(fill=tk.X, padx=16, pady=(6, 0))
        profile_menu = self._option_menu(
            profile_row, ctx.profile_var, [name for name, _path in ctx.profile_choices])
        profile_menu.pack(side=tk.LEFT)
        ctx.profile_info = tk.Label(win, text="", bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9),
                                    justify="left", anchor="w", wraplength=560)
        ctx.profile_info.pack(fill=tk.X, padx=16, pady=(4, 0))

        def describe_profile(*_args):
            summary = hbnscan.profile_summary(ctx.profile_paths.get(ctx.profile_var.get(), ""))
            ctx.profile_info.configure(
                text=f"{summary['name']} - {len(summary['categories'])} categories, "
                     f"{summary['topics']} topic(s), {summary['targets']} target(s)"
                     + (f"\n{summary['focus']}" if summary["focus"] else ""))

        ctx.profile_var.trace_add("write", describe_profile)
        describe_profile()

    def _settings_window_section(self, win, ctx):
        self._section_title(win, "FEED DATE RANGE")
        tk.Label(win,
                 text="Only items published inside this window are triaged.\n"
                      "Leave a field blank for an open-ended range (YYYY-MM-DD).",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9), justify="left", anchor="w"
                 ).pack(fill=tk.X, padx=16)

        form = tk.Frame(win, bg=BG_PANEL)
        form.pack(fill=tk.X, padx=16, pady=12)
        form.columnconfigure(1, weight=1)

        ctx.since_var = tk.StringVar(value=ctx.settings["feed_since"])
        ctx.until_var = tk.StringVar(value=ctx.settings["feed_until"])
        entry_opts = dict(bg=BG_INPUT, fg=FG_TEXT, insertbackground=FG_TEXT,
                          relief="flat", font=("Monospace", 10))
        tk.Label(form, text="From:", bg=BG_PANEL, fg=FG_TEXT).grid(row=0, column=0,
                                                                  sticky="w", pady=4)
        tk.Entry(form, textvariable=ctx.since_var, **entry_opts).grid(
            row=0, column=1, sticky="we", padx=(8, 0), ipady=4)
        tk.Label(form, text="To:", bg=BG_PANEL, fg=FG_TEXT).grid(row=1, column=0,
                                                                sticky="w", pady=4)
        tk.Entry(form, textvariable=ctx.until_var, **entry_opts).grid(
            row=1, column=1, sticky="we", padx=(8, 0), ipady=4)

    def _settings_ai_section(self, win, ctx):
        self._section_title(win, "LLM / AI PROVIDER")
        if hbnscan.deepseek_configured():
            key_status = (f"Configured ({hbnscan.LLM_API_KEY[:6]}…"
                          f"{hbnscan.LLM_API_KEY[-4:]}) - {hbnscan.LLM_MODEL}")
            status_colour = GREEN
        else:
            key_status = "No key - running on the offline heuristic only."
            status_colour = AMBER
        tk.Label(win, text=key_status, bg=BG_PANEL, fg=status_colour, font=("Sans", 9),
                 anchor="w", justify="left", wraplength=500).pack(fill=tk.X, padx=16)

        provider_row = tk.Frame(win, bg=BG_PANEL)
        provider_row.pack(fill=tk.X, padx=16, pady=(6, 0))
        current = "Custom"
        for preset, (url, model) in hbnscan.LLM_PRESETS.items():
            if url and url == hbnscan.LLM_BASE_URL and model == hbnscan.LLM_MODEL:
                current = preset
                break
        ctx.provider_var = tk.StringVar(value=current)
        tk.Label(provider_row, text="Provider:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        provider_menu = self._option_menu(provider_row, ctx.provider_var,
                                          list(hbnscan.LLM_PRESETS))
        provider_menu.pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(provider_row, text="Model:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        ctx.model_var = tk.StringVar(value=hbnscan.LLM_MODEL)
        tk.Entry(provider_row, textvariable=ctx.model_var, width=22, bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat",
                 font=("Monospace", 9)).pack(side=tk.LEFT, ipady=3)

        url_row = tk.Frame(win, bg=BG_PANEL)
        url_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Label(url_row, text="Base URL:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        ctx.url_var = tk.StringVar(value=hbnscan.LLM_BASE_URL)
        tk.Entry(url_row, textvariable=ctx.url_var, bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat",
                 font=("Monospace", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)

        key_row = tk.Frame(win, bg=BG_PANEL)
        key_row.pack(fill=tk.X, padx=16, pady=6)
        tk.Label(key_row, text="API key:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        ctx.key_var = tk.StringVar()
        tk.Entry(key_row, textvariable=ctx.key_var, show="\u2022", bg=BG_INPUT, fg=FG_TEXT,
                 insertbackground=FG_TEXT, relief="flat",
                 font=("Monospace", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        ctx.test_btn = tk.Button(key_row, text="Test", bg=BG_ACCENT, fg=FG_TEXT,
                                 activebackground=BG_SELECT, font=("Sans", 9),
                                 relief="flat", padx=10, pady=3, cursor="hand2",
                                 command=lambda: self._test_llm(ctx))
        ctx.test_btn.pack(side=tk.LEFT, padx=(8, 0))

        def apply_provider(*_args):
            url, model = hbnscan.LLM_PRESETS.get(ctx.provider_var.get(), ("", ""))
            if url:
                ctx.url_var.set(url)
                ctx.model_var.set(model)

        ctx.provider_var.trace_add("write", apply_provider)

    def _test_llm(self, ctx):
        candidate = ctx.key_var.get().strip() or hbnscan.LLM_API_KEY
        if not candidate:
            messagebox.showwarning("LLM provider", "Enter an API key first.", parent=ctx.window)
            return
        base = ctx.url_var.get().strip()
        if not base:
            messagebox.showwarning("LLM provider", "Enter a base URL.", parent=ctx.window)
            return
        hbnscan.set_llm_config(base_url=base, model=ctx.model_var.get().strip(),
                               api_key=candidate)
        ctx.test_btn.configure(state="disabled", text="Testing...")
        self.log(f"[TEST] Calling {base} ({ctx.model_var.get().strip()})...")

        def worker():
            lines = []
            code = hbnscan.check_api(log_callback=lines.append, api_key=candidate)
            self._post(self._finish_llm_test, ctx, code == 0, lines)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_llm_test(self, ctx, ok, lines):
        for line in lines:
            self.log("  " + line)
        if not ctx.window.winfo_exists():
            return
        ctx.test_btn.configure(state="normal", text="Test")
        if ok:
            messagebox.showinfo("LLM provider", "Connection works. AI stages are live.",
                                parent=ctx.window)
        else:
            messagebox.showerror("LLM provider", "The test failed - see the log.",
                                 parent=ctx.window)

    def _settings_scan_section(self, win, ctx):
        self._section_title(win, "SCANNING")
        ctx.scan_var = tk.BooleanVar(value=ctx.settings["auto_scan"])
        tk.Checkbutton(win, text="Scan automatically", variable=ctx.scan_var,
                       bg=BG_PANEL, fg=FG_TEXT, selectcolor=BG_ACCENT,
                       activebackground=BG_PANEL, activeforeground=FG_TEXT,
                       font=("Sans", 9), anchor="w").pack(fill=tk.X, padx=16)
        scan_row = tk.Frame(win, bg=BG_PANEL)
        scan_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        ctx.interval_var = tk.StringVar(value=str(ctx.settings["scan_interval_minutes"]))
        ctx.window_start_var = tk.StringVar(value=ctx.settings["scan_window_start"])
        ctx.window_end_var = tk.StringVar(value=ctx.settings["scan_window_end"])
        tk.Label(scan_row, text="Every:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        interval_menu = self._option_menu(
            scan_row, ctx.interval_var,
            hbnscan.setting_choices("scan_interval_minutes"))
        interval_menu.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(scan_row, text="min   only between", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        for var in (ctx.window_start_var, ctx.window_end_var):
            tk.Entry(scan_row, textvariable=var, width=7, bg=BG_INPUT, fg=FG_TEXT,
                     insertbackground=FG_TEXT, relief="flat",
                     font=("Monospace", 9)).pack(side=tk.LEFT, padx=(0, 4), ipady=2)
        tk.Label(scan_row, text="(blank = all day)", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 8)).pack(side=tk.LEFT)

    def _settings_notify_section(self, win, ctx):
        self._section_title(win, "NOTIFICATIONS")
        ctx.notify_var = tk.BooleanVar(value=ctx.settings["notify_enabled"])
        tk.Checkbutton(win, text="Email / SMS me new high-priority signals",
                       variable=ctx.notify_var, bg=BG_PANEL, fg=FG_TEXT, selectcolor=BG_ACCENT,
                       activebackground=BG_PANEL, activeforeground=FG_TEXT,
                       font=("Sans", 9), anchor="w").pack(fill=tk.X, padx=16)
        tk.Label(win, text=hbnscan.describe_notifications(), bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 8), anchor="w", justify="left",
                 wraplength=470).pack(fill=tk.X, padx=16)
        notify_row = tk.Frame(win, bg=BG_PANEL)
        notify_row.pack(fill=tk.X, padx=16, pady=(4, 0))
        ctx.severity_var = tk.StringVar(value=ctx.settings["notify_min_severity"])
        tk.Label(notify_row, text="At least:", bg=BG_PANEL, fg=FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        sev_menu = self._option_menu(notify_row, ctx.severity_var,
                                     hbnscan.setting_choices("notify_min_severity"))
        sev_menu.pack(side=tk.LEFT)
        ctx.notify_test_btn = tk.Button(
            notify_row, text="Send test", bg=BG_ACCENT, fg=FG_TEXT,
            activebackground=BG_SELECT, font=("Sans", 9), relief="flat", padx=10, pady=3,
            cursor="hand2", command=lambda: self._test_notify(ctx))
        ctx.notify_test_btn.pack(side=tk.LEFT, padx=(12, 0))

    def _test_notify(self, ctx):
        ctx.notify_test_btn.configure(state="disabled", text="Sending...")

        def worker():
            lines = []
            code = hbnscan.send_test_notification(log_callback=lines.append)
            self._post(self._finish_notify_test, ctx, code == 0, lines)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_notify_test(self, ctx, ok, lines):
        for line in lines:
            self.log("  " + line)
        if not ctx.window.winfo_exists():
            return
        ctx.notify_test_btn.configure(state="normal", text="Send test")
        if ok:
            messagebox.showinfo("Notifications", "Test message sent.", parent=ctx.window)
        else:
            messagebox.showerror("Notifications", "Send failed - see the log.",
                                 parent=ctx.window)

    def _settings_appearance_section(self, win, ctx):
        self._section_title(win, "APPEARANCE")
        look = tk.Frame(win, bg=BG_PANEL)
        look.pack(fill=tk.X, padx=16)
        ctx.theme_var = tk.StringVar(value=ctx.settings["theme"])
        ctx.scale_var = tk.StringVar(value=str(ctx.settings["font_scale"]))
        for label, var, choices in (
            ("Theme", ctx.theme_var, hbnscan.setting_choices("theme")),
            ("Text size", ctx.scale_var, ("90", "100", "110", "125", "150")),
        ):
            tk.Label(look, text=f"{label}:", bg=BG_PANEL, fg=FG_MUTED,
                     font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
            self._option_menu(look, var, choices).pack(side=tk.LEFT, padx=(0, 12))

    def _settings_data_section(self, win, ctx):
        self._section_title(win, "DATA")
        tk.Label(win, text="Everything stays on this machine. A backup is a single file "
                           "worth taking before an upgrade or a large cleanup.",
                 bg=BG_PANEL, fg=FG_MUTED, font=("Sans", 9), anchor="w", justify="left",
                 wraplength=520).pack(fill=tk.X, padx=16)
        row = tk.Frame(win, bg=BG_PANEL)
        row.pack(fill=tk.X, padx=16, pady=(4, 0))
        tk.Button(row, text="Back up database", command=self._backup_database,
                  bg=BG_ACCENT, fg=FG_TEXT, activebackground=BG_SELECT, font=("Sans", 9),
                  relief="flat", padx=10, pady=4, cursor="hand2").pack(side=tk.LEFT)

    def _backup_database(self):
        try:
            path = hbnscan.backup_database()
        except (OSError, sqlite3.Error) as exc:
            messagebox.showerror("Backup", f"Backup failed:\n{exc}")
            return
        if path is None:
            messagebox.showinfo("Backup", "There is no database to back up yet.")
            return
        self.log(f"[OK] Database backed up to {path}")
        messagebox.showinfo("Backup", f"Database backed up to\n{path}")

    def _save_settings(self, ctx):
        win = ctx.window
        since = ctx.since_var.get().strip()
        until = ctx.until_var.get().strip()
        for label, value in (("From", since), ("To", until)):
            if value and not hbnscan.parse_setting_date(value):
                messagebox.showerror("Settings", f"{label} must be YYYY-MM-DD.", parent=win)
                return
        start = hbnscan.parse_setting_date(since)
        end = hbnscan.parse_setting_date(until)
        if start and end and start > end:
            messagebox.showerror("Settings", "'From' is after 'To'.", parent=win)
            return
        for label, value in (("From", ctx.window_start_var.get().strip()),
                             ("To", ctx.window_end_var.get().strip())):
            if value and hbnscan.parse_setting_clock(value) is None:
                messagebox.showerror("Settings", f"{label} must be HH:MM.", parent=win)
                return
        before = hbnscan.load_settings()
        # Save the name, not the path: a moved or renamed checkout keeps working.
        chosen_profile = ctx.profile_var.get()
        if chosen_profile == ctx.profile_choices[0][0]:
            chosen_profile = ""
        profile_changed = chosen_profile != before["profile"]
        hbnscan.save_settings(feed_since=since, feed_until=until,
                              theme=ctx.theme_var.get(), font_scale=ctx.scale_var.get(),
                              notify_enabled=ctx.notify_var.get(),
                              notify_min_severity=ctx.severity_var.get(),
                              auto_scan=ctx.scan_var.get(),
                              scan_interval_minutes=ctx.interval_var.get(),
                              scan_window_start=ctx.window_start_var.get().strip(),
                              scan_window_end=ctx.window_end_var.get().strip(),
                              profile=chosen_profile)
        if profile_changed:
            active = hbnscan.apply_profile()
            source = hbnscan.active_profile_path()
            self.header_sub_lbl.configure(text=self._header_subtitle())
            self.log(f"[INFO] Profile: {active['name']} "
                     f"({source.name if source.exists() else 'built-in default'}) - "
                     f"{len(hbnscan.CATEGORIES)} categories, "
                     f"{len(hbnscan.load_topics())} topic(s), "
                     f"{len(hbnscan.load_watchlist())} target(s).")
        after = hbnscan.load_settings()
        self.auto_enabled.set(after["auto_scan"])
        if any(before[key] != after[key] for key in
               ("auto_scan", "scan_interval_minutes", "scan_window_start",
                "scan_window_end")):
            self._reschedule_auto_scan()
        self.log(f"[INFO] Feed date window: {since or 'open'} to {until or 'open'}.")
        # Provider and key are applied before the appearance branch, which returns
        # early after rebuilding the UI: a typed key must not be dropped because a
        # theme change happened to be saved in the same click.
        base = ctx.url_var.get().strip()
        model = ctx.model_var.get().strip()
        if base or model:
            hbnscan.set_llm_config(base_url=base or hbnscan.LLM_BASE_URL,
                                   model=model or hbnscan.LLM_MODEL)
            self.log(f"[INFO] Provider: {model or hbnscan.LLM_MODEL} at "
                     f"{base or hbnscan.LLM_BASE_URL}")
        key = ctx.key_var.get().strip()
        if key:
            if hbnscan.set_api_key(key):
                self.log("[INFO] LLM key saved to .env; AI stages are live.")
            else:
                self.log("[WARN] Key looks like the placeholder; AI stays offline.")
        if (ctx.theme_var.get() != ctx.settings["theme"]
                or int(ctx.scale_var.get()) != int(ctx.settings["font_scale"])):
            self.log(f"[INFO] Appearance: {ctx.theme_var.get()}, "
                     f"text {ctx.scale_var.get()}%.")
            win.destroy()
            self._rebuild_ui()
            return
        if profile_changed:
            win.destroy()
            self._rebuild_ui()
            return
        self.refresh_table()
        win.destroy()

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
                conn = hbnscan.connect(timeout=10)
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

    def _on_interval_changed(self, value):
        hbnscan.save_settings(scan_interval_minutes=value)
        self.log(f"[AUTO] Scan interval set to every {value} minute(s).")
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
        """Wake on the engine's own schedule rule, so the GUI agrees with the web app."""
        if self._closing:
            return
        settings = hbnscan.load_settings()
        delay = hbnscan.next_scan_delay(settings, self._auto_last_started)
        if delay is None:                       # automatic scanning switched off
            self._auto_after_id = self.after(60000, self._schedule_auto_scan)
            return
        if delay <= 0:
            if self.is_scanning:
                delay = 60
            else:
                self.log("[AUTO] Startup scan triggered." if initial
                         else f"[AUTO] Scheduled scan ({hbnscan.describe_schedule(settings)}).")
                self._auto_last_started = datetime.now()
                self.start_scan_thread()
                delay = max(1, int(settings["scan_interval_minutes"])) * 60
        elif initial:
            self.log(f"[AUTO] Outside the scanning window; waiting "
                     f"{max(1, delay // 60)} min.")
        self._auto_after_id = self.after(max(1, int(delay)) * 1000, self._schedule_auto_scan)

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
        self.refresh_workspace()

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
