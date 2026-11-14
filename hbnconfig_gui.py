#!/usr/bin/env python3
"""
HB Briefing - configuration window

One place to change every scan input without opening a text file: sources, standing topics,
pinned targets, the relevance criteria of a profile, the model budgets, and the notification
transport. Each section validates before it writes, reports what it wrote in the app log, and
refreshes the main window, so the grid and the report always reflect the saved state.

Structure: a navigation list on the left, one frame per section on the right. Frames are built
once and raised on selection; `refresh()` is called when a section is shown so nothing is stale.
The heavy lifting lives in `hbconfig` (parsing, validation, diagnostics) — this module owns only
widgets and the flow between them.
"""
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import hbnscan
import hbconfig
import hbworkspace

SECTIONS = (
    ("sources", "Sources"),
    ("topics", "Topics"),
    ("targets", "Targets"),
    ("criteria", "Criteria"),
    ("limits", "Scan limits"),
    ("notify", "Notifications"),
    ("diagnostics", "Diagnostics"),
)


def _topic_library():
    """Active-ready topics from the shipped library (commented lines in the .example file)."""
    path = hbnscan.APP_DIR / "topics.txt.example"
    if not path.exists():
        return []
    found = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("#") or line.startswith("##"):
            continue
        candidate = line.lstrip("#").strip()
        if not candidate or candidate.startswith("=") or "|" not in candidate:
            continue
        parts = [part.strip() for part in candidate.split("|")]
        label = parts[0]
        severity = parts[1].lower() if len(parts) > 1 else "medium"
        if not label or severity not in hbconfig.SEVERITY_LEVELS:
            continue
        group = parts[2] if len(parts) > 2 else ""
        found.append({"label": label, "severity": severity, "group": group})
    return found


class ConfigurationWindow(tk.Toplevel):
    """The configuration screen. Owned by a `NewsScanApp`; never edits the database."""

    def __init__(self, app):
        super().__init__(app)
        import hbnscan_gui                                   # palette, read live
        self.p = hbnscan_gui
        self.app = app
        self.title("HB Briefing - Configuration")
        self.geometry("1020x720")
        self.minsize(860, 560)
        self.configure(bg=self.p.BG_MAIN)
        self.transient(app)

        self._build_header()
        body = tk.Frame(self, bg=self.p.BG_MAIN)
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 8))

        nav_wrap = tk.Frame(body, bg=self.p.BG_PANEL)
        nav_wrap.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(nav_wrap, text="CONFIGURE", bg=self.p.BG_PANEL, fg=self.p.AMBER,
                 font=("Sans", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
        self.nav = tk.Listbox(nav_wrap, width=20, bg=self.p.BG_INPUT, fg=self.p.FG_TEXT,
                              selectbackground=self.p.BG_SELECT,
                              selectforeground=self.p.AMBER, relief="flat",
                              font=("Sans", 10), activestyle="none", exportselection=False)
        self.nav.pack(fill=tk.Y, expand=True, padx=8, pady=(0, 8))
        for _key, label in SECTIONS:
            self.nav.insert(tk.END, "  " + label)
        self.nav.bind("<<ListboxSelect>>", lambda _e: self._show_current())

        self.content = tk.Frame(body, bg=self.p.BG_PANEL)
        self.content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))
        self.frames = {}
        builders = {
            "sources": self._build_sources,
            "topics": self._build_topics,
            "targets": self._build_targets,
            "criteria": self._build_criteria,
            "limits": self._build_limits,
            "notify": self._build_notify,
            "diagnostics": self._build_diagnostics,
        }
        for key, _label in SECTIONS:
            frame = tk.Frame(self.content, bg=self.p.BG_PANEL)
            frame.place(x=0, y=0, relwidth=1, relheight=1)
            self.frames[key] = frame
            builders[key](frame)

        self._build_footer()
        self.nav.selection_set(0)
        self._show_current()

    # -- chrome -------------------------------------------------------------

    def _build_header(self):
        header = tk.Frame(self, bg=self.p.BG_PANEL, padx=18, pady=12)
        header.pack(fill=tk.X)
        tk.Label(header, text="CONFIGURATION", bg=self.p.BG_PANEL, fg=self.p.AMBER,
                 font=("Sans", 13, "bold")).pack(anchor="w")
        tk.Label(header,
                 text="Every scan input in one place. Changes are validated before they are "
                      "written, comments in your files are preserved, and nothing is saved by "
                      "a hidden default.",
                 bg=self.p.BG_PANEL, fg=self.p.FG_MUTED, font=("Sans", 9),
                 anchor="w", justify="left", wraplength=940).pack(anchor="w")

    def _build_footer(self):
        footer = tk.Frame(self, bg=self.p.BG_PANEL, padx=18, pady=8)
        footer.pack(fill=tk.X, side=tk.BOTTOM)
        self.status = tk.Label(footer, text="", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED,
                               font=("Sans", 9), anchor="w")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._btn(footer, "Recheck configuration", self.run_diagnostics).pack(side=tk.RIGHT, padx=4)
        self._btn(footer, "Close", self.destroy).pack(side=tk.RIGHT, padx=4)

    def _btn(self, parent, label, command, accent=False):
        return tk.Button(parent, text=label, command=command,
                         bg=self.p.AMBER if accent else self.p.BG_ACCENT,
                         fg="#0f172a" if accent else self.p.FG_TEXT,
                         activebackground=self.p.AMBER_HOVER if accent else self.p.BG_SELECT,
                         font=("Sans", 9, "bold" if accent else "normal"),
                         relief="flat", padx=10, pady=4, cursor="hand2")

    def _title(self, parent, text, help_text=""):
        tk.Label(parent, text=text, bg=self.p.BG_PANEL, fg=self.p.AMBER,
                 font=("Sans", 11, "bold")).pack(anchor="w", padx=12, pady=(10, 2))
        if help_text:
            tk.Label(parent, text=help_text, bg=self.p.BG_PANEL, fg=self.p.FG_MUTED,
                     font=("Sans", 9), anchor="w", justify="left", wraplength=740
                     ).pack(fill=tk.X, padx=12, pady=(0, 6))

    def _tree(self, parent, columns, height=14):
        wrap = tk.Frame(parent, bg=self.p.BG_PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 6))
        tree = ttk.Treeview(wrap, columns=[c[0] for c in columns], show="headings",
                            height=height, selectmode="browse")
        for key, label, width, stretch in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=stretch)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return tree

    def _entry_row(self, parent, label, variable, width=30):
        row = tk.Frame(parent, bg=self.p.BG_PANEL)
        row.pack(fill=tk.X, padx=12, pady=2)
        tk.Label(row, text=label, bg=self.p.BG_PANEL, fg=self.p.FG_MUTED, width=24,
                 anchor="w", font=("Sans", 9)).pack(side=tk.LEFT)
        tk.Entry(row, textvariable=variable, width=width, bg=self.p.BG_INPUT,
                 fg=self.p.FG_TEXT, insertbackground=self.p.FG_TEXT, relief="flat",
                 font=("Monospace", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=3)
        return row

    def _option(self, parent, variable, choices, width=None):
        menu = tk.OptionMenu(parent, variable, *choices)
        menu.configure(bg=self.p.BG_ACCENT, fg=self.p.FG_TEXT,
                       activebackground=self.p.BG_SELECT, relief="flat", font=("Sans", 9),
                       highlightthickness=0, bd=0, indicatoron=False, padx=8, pady=3,
                       cursor="hand2")
        menu["menu"].configure(bg=self.p.BG_PANEL, fg=self.p.FG_TEXT,
                               activebackground=self.p.BG_ACCENT,
                               activeforeground=self.p.AMBER, font=("Sans", 9))
        return menu

    def _scrolled_text(self, parent, height=6, mono=True):
        wrap = tk.Frame(parent, bg=self.p.BG_PANEL)
        wrap.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        text = tk.Text(wrap, height=height, bg=self.p.BG_INPUT, fg=self.p.FG_TEXT,
                       insertbackground=self.p.FG_TEXT, relief="flat", wrap="none",
                       font=("Monospace" if mono else "Sans", 9), padx=8, pady=6,
                       undo=True)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        return text

    def _show_current(self):
        selection = self.nav.curselection()
        key = SECTIONS[int(selection[0])][0] if selection else "sources"
        self.frames[key].lift()
        self.section = key
        refresh = getattr(self, "_refresh_" + key, None)
        if refresh:
            refresh()

    def _say(self, message, error=False):
        self.status.configure(text=message, fg=self.p.RED if error else self.p.FG_MUTED)
        self.app.log(("[ERROR] " if error else "[OK] ") + message)

    def _after_write(self, message):
        """Every section ends the same way: report, refresh the main window."""
        self._say(message)
        self.app.refresh_table()
        self.app.refresh_workspace()

    # -- sources ------------------------------------------------------------

    def _build_sources(self, frame):
        self._title(frame, "SOURCES",
                    "Each line is an RSS or Atom URL. Comments in feeds.txt are preserved; the "
                    "table shows exactly the active entries. Test fetches a feed without saving "
                    "or scoring anything.")
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._btn(bar, "Add…", self._source_add, accent=True).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Edit…", self._source_edit).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Remove", self._source_remove).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Test", self._source_test).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Test all", self._source_test_all).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Edit as text", self.app.open_feeds_editor).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Scan now", self.app.start_scan_thread).pack(side=tk.RIGHT, padx=3)
        self.sources_tree = self._tree(frame, (
            ("n", "#", 40, False), ("url", "URL", 430, True), ("state", "Status", 78, False),
            ("last", "Last success", 140, False), ("items", "Items", 60, False)))
        self.sources_tree.bind("<Double-1>", lambda _e: self._source_edit())

    def _refresh_sources(self):
        for iid in self.sources_tree.get_children():
            self.sources_tree.delete(iid)
        self.source_rows = hbconfig.sources_document().entries()
        health = {}
        try:
            conn = hbnscan.connect(timeout=5)
            try:
                urls = [data.get("url", "") for _o, data, _e in self.source_rows]
                health = {entry["url"]: entry for entry in hbworkspace.source_health(conn, urls)}
            finally:
                conn.close()
        except hbnscan.sqlite3.Error:
            health = {}
        for ordinal, data, error in self.source_rows:
            entry = health.get(data.get("url", ""), {})
            self.sources_tree.insert(
                "", tk.END, iid=f"src{ordinal}",
                values=(ordinal + 1, data.get("url", ""),
                        "invalid" if error else entry.get("status", "never"),
                        (entry.get("last_success") or "never").replace("T", " "),
                        entry.get("items_new", "")))

    def _selected_source(self):
        selection = self.sources_tree.selection()
        return int(selection[0][3:]) if selection else None

    def _source_add(self):
        url = simpledialog.askstring("Add source", "RSS or Atom URL:", parent=self)
        if not url:
            return
        try:
            hbconfig.add_source(url)
        except ValueError as exc:
            messagebox.showerror("Sources", str(exc), parent=self)
            return
        self._refresh_sources()
        self._after_write(f"source added: {url.strip()}")

    def _source_edit(self):
        ordinal = self._selected_source()
        if ordinal is None:
            return
        current = self.source_rows[ordinal][1].get("url", "")
        url = simpledialog.askstring("Edit source", "RSS or Atom URL:", initialvalue=current,
                                     parent=self)
        if not url or url == current:
            return
        try:
            hbconfig.update_source(ordinal, url)
        except ValueError as exc:
            messagebox.showerror("Sources", str(exc), parent=self)
            return
        self._refresh_sources()
        self._after_write("source updated")

    def _source_remove(self):
        ordinal = self._selected_source()
        if ordinal is None:
            return
        url = self.source_rows[ordinal][1].get("url", "")
        if not messagebox.askyesno("Remove source", f"Remove this source?\n\n{url}",
                                   parent=self):
            return
        hbconfig.remove_source(ordinal)
        self._refresh_sources()
        self._after_write(f"source removed: {url}")

    def _source_test(self):
        ordinal = self._selected_source()
        if ordinal is None:
            messagebox.showinfo("Sources", "Select a source first.", parent=self)
            return
        url = self.source_rows[ordinal][1].get("url", "")
        self._say(f"testing {url} …")
        self.update_idletasks()
        result = hbconfig.test_source(url)
        if result["ok"]:
            self._say(f"{url}: {result['items']} item(s) in the feed")
        else:
            self._say(f"{url}: {result['error']}{result['hint']}", error=True)

    def _source_test_all(self):
        failures = 0
        total = 0
        for _ordinal, data, error in self.source_rows:
            if error:
                continue
            total += 1
            result = hbconfig.test_source(data.get("url", ""))
            if not result["ok"]:
                failures += 1
                self.app.log(f"[TEST] {data.get('url')}: {result['error']}")
            self.update_idletasks()
        self._say(f"tested {total} source(s): {total - failures} ok, {failures} failing",
                  error=bool(failures))

    # -- topics -------------------------------------------------------------

    def _build_topics(self, frame):
        self._title(frame, "TOPICS",
                    "A standing subject is relevant on subject alone. Only topics.txt is edited "
                    "here; the profile's own topics are shown as read-only context and are "
                    "edited under Criteria.")
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._btn(bar, "Add…", self._topic_add, accent=True).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Edit…", self._topic_edit).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Remove", self._topic_remove).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Add from library…", self._topic_library_dialog).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Edit as text", self.app.open_topics_editor).pack(side=tk.LEFT, padx=3)
        self.topics_as_feeds = tk.BooleanVar(value=bool(hbnscan.load_settings()["topics_as_feeds"]))
        tk.Checkbutton(bar, text="Search Google News per topic",
                       variable=self.topics_as_feeds, command=self._save_topics_toggle,
                       bg=self.p.BG_PANEL, fg=self.p.FG_TEXT,
                       selectcolor=self.p.BG_ACCENT, activebackground=self.p.BG_PANEL,
                       font=("Sans", 9)).pack(side=tk.RIGHT, padx=6)
        self.topics_tree = self._tree(frame, (
            ("n", "#", 40, False), ("label", "Topic", 330, True), ("level", "Interest", 78, False),
            ("group", "Group", 150, False), ("origin", "Origin", 96, False)))
        self.topics_tree.bind("<Double-1>", lambda _e: self._topic_edit())

    def _refresh_topics(self):
        for iid in self.topics_tree.get_children():
            self.topics_tree.delete(iid)
        self.topic_rows = hbconfig.topics_document().entries()
        self.topics_as_feeds.set(bool(hbnscan.load_settings()["topics_as_feeds"]))
        for ordinal, data, error in self.topic_rows:
            self.topics_tree.insert(
                "", tk.END, iid=f"top{ordinal}",
                values=(ordinal + 1, data.get("label", "(unreadable)"),
                        data.get("severity", ""), data.get("group", ""),
                        "invalid" if error else "topics.txt"))
        for index, topic in enumerate(hbnscan.current_profile().topics):
            self.topics_tree.insert(
                "", tk.END, iid=f"profile{index}",
                values=("", topic["label"], topic["severity"], topic.get("group", ""),
                        "profile"))

    def _selected_topic(self):
        selection = self.topics_tree.selection()
        if not selection or not selection[0].startswith("top"):
            return None
        return int(selection[0][3:])

    def _topic_dialog(self, title, label="", severity="medium", group=""):
        """Compact modal editor for one topic. Returns a dict or None."""
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=self.p.BG_PANEL)
        win.transient(self)
        win.geometry("460x210")
        label_var = tk.StringVar(value=label)
        group_var = tk.StringVar(value=group)
        severity_var = tk.StringVar(value=severity)
        self._entry_row(win, "Topic", label_var)
        row = tk.Frame(win, bg=self.p.BG_PANEL)
        row.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(row, text="Interest level", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED, width=24,
                 anchor="w", font=("Sans", 9)).pack(side=tk.LEFT)
        self._option(row, severity_var, list(hbconfig.SEVERITY_LEVELS)).pack(side=tk.LEFT)
        self._entry_row(win, "Group (optional)", group_var)
        result = {}

        def save():
            result.update(label=label_var.get(), severity=severity_var.get(),
                          group=group_var.get())
            win.destroy()

        buttons = tk.Frame(win, bg=self.p.BG_PANEL)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        self._btn(buttons, "Cancel", win.destroy).pack(side=tk.LEFT)
        self._btn(buttons, "Save", save, accent=True).pack(side=tk.RIGHT)
        win.grab_set()
        self.wait_window(win)
        return result or None

    def _topic_add(self):
        data = self._topic_dialog("Add topic")
        if not data:
            return
        try:
            hbconfig.add_topic(data["label"], data["severity"], data["group"])
        except ValueError as exc:
            messagebox.showerror("Topics", str(exc), parent=self)
            return
        self._refresh_topics()
        self._after_write(f"topic added: {data['label']}")

    def _topic_edit(self):
        ordinal = self._selected_topic()
        if ordinal is None:
            if self.topics_tree.selection():
                messagebox.showinfo("Topics",
                                    "Profile topics are edited under Criteria.", parent=self)
            return
        current = self.topic_rows[ordinal][1]
        data = self._topic_dialog("Edit topic", current.get("label", ""),
                                  current.get("severity", "medium"),
                                  current.get("group", ""))
        if not data:
            return
        try:
            hbconfig.update_topic(ordinal, data["label"], data["severity"], data["group"])
        except ValueError as exc:
            messagebox.showerror("Topics", str(exc), parent=self)
            return
        self._refresh_topics()
        self._after_write("topic updated")

    def _topic_remove(self):
        ordinal = self._selected_topic()
        if ordinal is None:
            return
        label = self.topic_rows[ordinal][1].get("label", "")
        if not messagebox.askyesno("Remove topic", f"Remove “{label}”?", parent=self):
            return
        hbconfig.remove_topic(ordinal)
        self._refresh_topics()
        self._after_write(f"topic removed: {label}")

    def _save_topics_toggle(self):
        hbnscan.save_settings(topics_as_feeds=bool(self.topics_as_feeds.get()))
        self._say("topic query feeds " + ("enabled" if self.topics_as_feeds.get() else "disabled"))
        self.app.refresh_table()

    def _topic_library_dialog(self):
        library = _topic_library()
        existing = {data.get("label", "").lower() for _o, data, _e in self.topic_rows}
        library = [topic for topic in library if topic["label"].lower() not in existing]
        if not library:
            messagebox.showinfo("Topics", "Every library topic is already in the list.",
                                parent=self)
            return
        win = tk.Toplevel(self)
        win.title("Add topics from the library")
        win.configure(bg=self.p.BG_PANEL)
        win.transient(self)
        win.geometry("520x520")
        tk.Label(win, text=f"{len(library)} topic(s) available", bg=self.p.BG_PANEL,
                 fg=self.p.FG_MUTED, font=("Sans", 9)).pack(anchor="w", padx=12, pady=8)
        holder = tk.Frame(win, bg=self.p.BG_PANEL)
        holder.pack(fill=tk.BOTH, expand=True, padx=12)
        listing = tk.Listbox(holder, selectmode="extended", bg=self.p.BG_INPUT,
                             fg=self.p.FG_TEXT, selectbackground=self.p.BG_SELECT,
                             relief="flat", font=("Sans", 9), activestyle="none")
        for topic in library:
            listing.insert(tk.END, f"{topic['label']}  ·  {topic['severity']}"
                                   + (f"  ·  {topic['group']}" if topic["group"] else ""))
        listing.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=listing.yview)
        listing.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        def add_selected():
            chosen = [library[index] for index in listing.curselection()]
            added = 0
            for topic in chosen:
                try:
                    hbconfig.add_topic(topic["label"], topic["severity"], topic["group"])
                    added += 1
                except ValueError:
                    continue
            win.destroy()
            self._refresh_topics()
            self._after_write(f"{added} topic(s) added from the library")

        buttons = tk.Frame(win, bg=self.p.BG_PANEL)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        self._btn(buttons, "Cancel", win.destroy).pack(side=tk.LEFT)
        self._btn(buttons, "Add selected", add_selected, accent=True).pack(side=tk.RIGHT)

    # -- targets ------------------------------------------------------------

    def _build_targets(self, frame):
        self._title(frame, "TARGETS",
                    "A target pins a named party to a category and status, ahead of keyword "
                    "scoring and regardless of subject. Category must exist in the active "
                    "profile. Profile targets are read-only context.")
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._btn(bar, "Add…", self._target_add, accent=True).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Edit…", self._target_edit).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Remove", self._target_remove).pack(side=tk.LEFT, padx=3)
        self.targets_tree = self._tree(frame, (
            ("n", "#", 40, False), ("phrases", "Match phrases (all must appear)", 330, True),
            ("category", "Category", 150, False), ("severity", "Status", 70, False),
            ("note", "Note", 190, False), ("origin", "Origin", 90, False)))
        self.targets_tree.bind("<Double-1>", lambda _e: self._target_edit())

    def _refresh_targets(self):
        for iid in self.targets_tree.get_children():
            self.targets_tree.delete(iid)
        self.target_rows = hbconfig.targets_document().entries()
        for ordinal, data, error in self.target_rows:
            self.targets_tree.insert(
                "", tk.END, iid=f"tgt{ordinal}",
                values=(ordinal + 1,
                        " & ".join(data.get("terms", [])) or "(unreadable)",
                        data.get("category", ""), data.get("severity", ""),
                        data.get("note", ""), "invalid" if error else "watchlist.txt"))
        for index, rule in enumerate(hbnscan.current_profile().targets):
            self.targets_tree.insert(
                "", tk.END, iid=f"ptgt{index}",
                values=("", " & ".join(rule["terms"]), rule["category"], rule["severity"],
                        rule.get("note", ""), "profile"))

    def _selected_target(self):
        selection = self.targets_tree.selection()
        if not selection or not selection[0].startswith("tgt"):
            return None
        return int(selection[0][3:])

    def _target_dialog(self, title, phrases="", category="", severity="medium", note=""):
        win = tk.Toplevel(self)
        win.title(title)
        win.configure(bg=self.p.BG_PANEL)
        win.transient(self)
        win.geometry("560x270")
        phrase_var = tk.StringVar(value=phrases)
        note_var = tk.StringVar(value=note)
        categories = hbconfig.watchlist_categories()
        category_var = tk.StringVar(value=category or (categories[0] if categories else ""))
        severity_var = tk.StringVar(value=severity)
        self._entry_row(win, "Phrases (join with &)", phrase_var, width=40)
        row = tk.Frame(win, bg=self.p.BG_PANEL)
        row.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(row, text="Category", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED, width=24,
                 anchor="w", font=("Sans", 9)).pack(side=tk.LEFT)
        self._option(row, category_var, list(categories)).pack(side=tk.LEFT, padx=(0, 14))
        tk.Label(row, text="Status", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(0, 4))
        self._option(row, severity_var, ["high", "medium", "low"]).pack(side=tk.LEFT)
        self._entry_row(win, "Note (optional)", note_var, width=40)
        result = {}

        def save():
            terms = [part.strip() for part in phrase_var.get().split("&") if part.strip()]
            result.update(terms=terms, category=category_var.get(),
                          severity=severity_var.get(), note=note_var.get().strip())
            win.destroy()

        buttons = tk.Frame(win, bg=self.p.BG_PANEL)
        buttons.pack(fill=tk.X, padx=12, pady=10)
        self._btn(buttons, "Cancel", win.destroy).pack(side=tk.LEFT)
        self._btn(buttons, "Save", save, accent=True).pack(side=tk.RIGHT)
        win.grab_set()
        self.wait_window(win)
        return result or None

    def _target_add(self):
        data = self._target_dialog("Add target")
        if not data:
            return
        try:
            hbconfig.add_target(data["terms"], data["category"], data["severity"],
                                data["note"])
        except ValueError as exc:
            messagebox.showerror("Targets", str(exc), parent=self)
            return
        self._refresh_targets()
        self._after_write("target added")

    def _target_edit(self):
        ordinal = self._selected_target()
        if ordinal is None:
            if self.targets_tree.selection():
                messagebox.showinfo("Targets", "Profile targets are edited under Criteria.",
                                    parent=self)
            return
        current = self.target_rows[ordinal][1]
        data = self._target_dialog("Edit target", " & ".join(current.get("terms", [])),
                                   current.get("category", ""),
                                   current.get("severity", "medium"),
                                   current.get("note", ""))
        if not data:
            return
        try:
            hbconfig.update_target(ordinal, data["terms"], data["category"],
                                   data["severity"], data["note"])
        except ValueError as exc:
            messagebox.showerror("Targets", str(exc), parent=self)
            return
        self._refresh_targets()
        self._after_write("target updated")

    def _target_remove(self):
        ordinal = self._selected_target()
        if ordinal is None:
            return
        label = " & ".join(self.target_rows[ordinal][1].get("terms", []))
        if not messagebox.askyesno("Remove target", f"Remove “{label}”?", parent=self):
            return
        hbconfig.remove_target(ordinal)
        self._refresh_targets()
        self._after_write(f"target removed: {label}")

    # -- criteria -----------------------------------------------------------

    def _build_criteria(self, frame):
        self._title(frame, "CRITERIA (PROFILE)",
                    "The profile is the whole relevance model: categories, keyword signals, "
                    "noise rules, trusted hosts and the prompt framing. Editing changes what "
                    "counts as relevant; it does not re-grade stored signals.")
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=(0, 4))
        choices = [name for name, _path in hbnscan.available_profiles()]
        self.profile_choice = tk.StringVar(value=choices[0] if choices else "")
        self._option(bar, self.profile_choice, choices).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Load", self._criteria_load).pack(side=tk.LEFT, padx=3)
        self._btn(bar, "Save", self._criteria_save, accent=True).pack(side=tk.RIGHT, padx=3)
        self._btn(bar, "Save as new…", self._criteria_save_as).pack(side=tk.RIGHT, padx=3)
        self._btn(bar, "Validate", self._criteria_validate).pack(side=tk.RIGHT, padx=3)
        self._btn(bar, "Activate", self._criteria_activate).pack(side=tk.RIGHT, padx=3)

        body = tk.Frame(frame, bg=self.p.BG_PANEL)
        body.pack(fill=tk.BOTH, expand=True, padx=12, pady=(4, 8))
        self.criteria_text = {}          # field -> widget
        left = tk.Frame(body, bg=self.p.BG_PANEL)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(body, bg=self.p.BG_PANEL)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0))

        for field_name, label in hbconfig.PROFILE_TEXT_FIELDS:
            variable = tk.StringVar()
            self.criteria_text[field_name] = variable
            self._entry_row(left, label, variable, width=34)

        for field_name, label in hbconfig.PROFILE_LIST_FIELDS:
            holder = tk.Frame(left, bg=self.p.BG_PANEL)
            holder.pack(fill=tk.X, padx=12, pady=(6, 0))
            tk.Label(holder, text=f"{label} (one per line)", bg=self.p.BG_PANEL,
                     fg=self.p.FG_MUTED, font=("Sans", 9), anchor="w").pack(anchor="w")
            self.criteria_text[field_name] = self._scrolled_text(holder, height=3)

        for field_name, label, hint in (
                ("category_hints", "Category hints", "category: phrase, phrase"),
                ("topics", "Profile topics", "topic | high | group"),
                ("targets", "Profile targets", "phrase & phrase | category | status | note")):
            holder = tk.Frame(right, bg=self.p.BG_PANEL)
            holder.pack(fill=tk.BOTH, expand=True, padx=12, pady=(6, 0))
            tk.Label(holder, text=f"{label} — {hint}", bg=self.p.BG_PANEL,
                     fg=self.p.FG_MUTED, font=("Sans", 9), anchor="w").pack(anchor="w")
            self.criteria_text[field_name] = self._scrolled_text(holder, height=5)

    def _criteria_load(self):
        name = self.profile_choice.get()
        choice = "" if name == hbnscan.available_profiles()[0][0] else name
        loaded = hbconfig.profile_editor_data(choice)
        data = loaded["data"]
        for field_name, _label in hbconfig.PROFILE_TEXT_FIELDS:
            self.criteria_text[field_name].set(str(data.get(field_name) or ""))
        for field_name, _label in hbconfig.PROFILE_LIST_FIELDS:
            widget = self.criteria_text[field_name]
            widget.delete("1.0", tk.END)
            widget.insert("1.0", "\n".join(str(value) for value in data.get(field_name) or []))
        hints = self.criteria_text["category_hints"]
        hints.delete("1.0", tk.END)
        hints.insert("1.0", "\n".join(
            f"{name}: {', '.join(phrases)}" for name, phrases in data.get("category_hints") or []))
        topics = self.criteria_text["topics"]
        topics.delete("1.0", tk.END)
        topics.insert("1.0", "\n".join(
            f"{entry.get('label')} | {entry.get('severity', 'medium')}"
            + (f" | {entry.get('group')}" if entry.get("group") else "")
            for entry in data.get("topics") or []))
        targets = self.criteria_text["targets"]
        targets.delete("1.0", tk.END)
        targets.insert("1.0", "\n".join(
            f"{' & '.join(entry.get('terms') or [])} | {entry.get('category')} | "
            f"{entry.get('severity')}"
            + (f" | {entry.get('note')}" if entry.get("note") else "")
            for entry in data.get("targets") or []))
        self._say(f"loaded criteria: {data.get('name')} "
                  f"({'built-in default' if loaded['builtin'] else loaded['path'].name})")

    def _criteria_data(self):
        """Collect the form into a profile mapping, parsing the list fields."""
        data = {field: variable.get().strip()
                for field, variable in self.criteria_text.items()
                if hasattr(variable, "get") and not isinstance(variable, tk.Text)}
        for field_name, _label in hbconfig.PROFILE_LIST_FIELDS:
            raw = self.criteria_text[field_name].get("1.0", tk.END)
            data[field_name] = [line.strip() for line in raw.splitlines() if line.strip()]
        hints = []
        for line in self.criteria_text["category_hints"].get("1.0", tk.END).splitlines():
            if not line.strip() or ":" not in line:
                continue
            category, phrases = line.split(":", 1)
            hints.append([category.strip().lower(),
                          [phrase.strip() for phrase in phrases.split(",") if phrase.strip()]])
        data["category_hints"] = hints
        topics = []
        for line in self.criteria_text["topics"].get("1.0", tk.END).splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            topics.append({"label": parts[0],
                           "severity": parts[1].lower() if len(parts) > 1 and parts[1]
                           else "medium",
                           "group": parts[2] if len(parts) > 2 else ""})
        data["topics"] = topics
        targets = []
        for line in self.criteria_text["targets"].get("1.0", tk.END).splitlines():
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue
            targets.append({"match": [term.strip() for term in parts[0].split("&")
                                      if term.strip()],
                            "category": parts[1].lower(), "severity": parts[2].lower(),
                            "note": parts[3] if len(parts) > 3 else ""})
        data["targets"] = targets
        data["version"] = str(hbnscan.DEFAULT_PROFILE.get("version", ""))
        return data

    def _criteria_validate(self):
        errors, warnings = hbconfig.validate_profile(self._criteria_data())
        if errors:
            messagebox.showerror("Criteria", "Fix these first:\n\n" + "\n".join(errors),
                                 parent=self)
            self._say(f"{len(errors)} validation error(s)", error=True)
        elif warnings:
            messagebox.showwarning("Criteria", "\n".join(warnings), parent=self)
            self._say(f"valid, {len(warnings)} warning(s)")
        else:
            self._say("criteria are valid")
        return not errors

    def _criteria_save(self, overwrite=False, name=None):
        data = self._criteria_data()
        if not self._criteria_validate():
            return
        try:
            path = hbconfig.save_profile(name or self.profile_choice.get(), data,
                                         overwrite=overwrite)
        except ValueError as exc:
            if "already exists" in str(exc):
                if messagebox.askyesno("Criteria",
                                       f"{exc}\n\nOverwrite it?", parent=self):
                    return self._criteria_save(overwrite=True, name=name)
                return
            messagebox.showerror("Criteria", str(exc), parent=self)
            return
        self._say(f"criteria saved: profiles/{path.name}")
        self.app.log("[INFO] Restart the interface or activate the profile to apply it.")

    def _criteria_save_as(self):
        name = simpledialog.askstring("Save profile as", "Profile name:", parent=self)
        if not name:
            return
        self._criteria_save(overwrite=False, name=name)

    def _criteria_activate(self):
        if not messagebox.askyesno(
                "Activate profile",
                "Apply this profile now? The relevance model switches immediately and the "
                "window reloads. Stored signals keep their old categories until "
                "--reclassify.", parent=self):
            return
        name = self.profile_choice.get()
        choices = hbnscan.available_profiles()
        choice = "" if choices and name == choices[0][0] else name
        hbconfig.activate_profile(choice)
        self.app.log(f"[INFO] Profile active: {hbnscan.APP_NAME}")
        self.destroy()
        self.app._rebuild_ui()

    # -- limits -------------------------------------------------------------

    def _build_limits(self, frame):
        self._title(frame, "SCAN LIMITS",
                    "How much work one scan may do. The model budgets are stored in .env and "
                    "apply immediately; the item cap and retention are settings and apply to "
                    "the next scan.")
        self.limit_vars = {}
        self.deep_read_var = tk.BooleanVar()
        self.briefing_var = tk.BooleanVar()
        tk.Checkbutton(frame, text="Deep read flagged items (fetch the article)",
                       variable=self.deep_read_var, bg=self.p.BG_PANEL, fg=self.p.FG_TEXT,
                       selectcolor=self.p.BG_ACCENT, activebackground=self.p.BG_PANEL,
                       font=("Sans", 9)).pack(anchor="w", padx=12, pady=2)
        tk.Checkbutton(frame, text="Write a briefing each scan",
                       variable=self.briefing_var, bg=self.p.BG_PANEL, fg=self.p.FG_TEXT,
                       selectcolor=self.p.BG_ACCENT, activebackground=self.p.BG_PANEL,
                       font=("Sans", 9)).pack(anchor="w", padx=12, pady=2)
        for key, label in (("max_triage", "AI triage calls per scan (0 = offline only)"),
                           ("max_deep_reads", "Deep reads per scan"),
                           ("brief_hours", "Briefing window (hours)"),
                           ("max_new_per_feed", "Max new items per feed per scan"),
                           ("retention_days", "Keep dismissed rows for (days)")):
            variable = tk.StringVar()
            self.limit_vars[key] = variable
            self._entry_row(frame, label, variable, width=10)
        self._btn(frame, "Save limits", self._save_limits, accent=True).pack(
            anchor="w", padx=12, pady=10)

    def _refresh_limits(self):
        limits = hbconfig.env_limits()
        settings = hbnscan.load_settings()
        self.deep_read_var.set(bool(limits["deep_read"]))
        self.briefing_var.set(bool(limits["briefing"]))
        self.limit_vars["max_triage"].set(str(limits["max_triage"]))
        self.limit_vars["max_deep_reads"].set(str(limits["max_deep_reads"]))
        self.limit_vars["brief_hours"].set(str(limits["brief_hours"]))
        self.limit_vars["max_new_per_feed"].set(str(settings["max_new_per_feed"]))
        self.limit_vars["retention_days"].set(str(settings["retention_days"]))

    def _save_limits(self):
        try:
            hbconfig.save_env_limits(
                deep_read=self.deep_read_var.get(), briefing=self.briefing_var.get(),
                max_triage=self.limit_vars["max_triage"].get(),
                max_deep_reads=self.limit_vars["max_deep_reads"].get(),
                brief_hours=self.limit_vars["brief_hours"].get())
            hbnscan.save_settings(
                max_new_per_feed=self.limit_vars["max_new_per_feed"].get(),
                retention_days=self.limit_vars["retention_days"].get())
        except ValueError as exc:
            messagebox.showerror("Scan limits", str(exc), parent=self)
            return
        self._refresh_limits()
        self._after_write("scan limits saved")

    # -- notifications ------------------------------------------------------

    def _build_notify(self, frame):
        self._title(frame, "NOTIFICATIONS",
                    "Transport lives in .env with 0600 permissions. A blank password keeps the "
                    "stored one; Send test uses the real code path.")
        self.notify_vars = {}
        for key, label in (("smtp_host", "SMTP host"), ("smtp_port", "Port"),
                           ("smtp_user", "Username"), ("smtp_from", "From address"),
                           ("email_to", "E-mail recipients (comma separated)"),
                           ("sms_to", "SMS numbers (comma separated)"),
                           ("sms_gateway", "SMS gateway domain")):
            variable = tk.StringVar()
            self.notify_vars[key] = variable
            self._entry_row(frame, label, variable, width=34)
        row = tk.Frame(frame, bg=self.p.BG_PANEL)
        row.pack(fill=tk.X, padx=12, pady=4)
        tk.Label(row, text="Security", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED, width=24,
                 anchor="w", font=("Sans", 9)).pack(side=tk.LEFT)
        self.notify_security = tk.StringVar(value="starttls")
        self._option(row, self.notify_security, list(hbconfig.SMTP_SECURITY)).pack(
            side=tk.LEFT, padx=(0, 16))
        self.notify_enabled = tk.BooleanVar()
        self.notify_severity = tk.StringVar(value="high")
        tk.Checkbutton(row, text="Enabled", variable=self.notify_enabled,
                       bg=self.p.BG_PANEL, fg=self.p.FG_TEXT, selectcolor=self.p.BG_ACCENT,
                       activebackground=self.p.BG_PANEL, font=("Sans", 9)).pack(side=tk.LEFT)
        tk.Label(row, text="at least", bg=self.p.BG_PANEL, fg=self.p.FG_MUTED,
                 font=("Sans", 9)).pack(side=tk.LEFT, padx=(12, 4))
        self._option(row, self.notify_severity, ["high", "medium"]).pack(side=tk.LEFT)
        self.notify_password = tk.StringVar()
        self._entry_row(frame, "SMTP password (blank = keep)", self.notify_password, width=34)
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=8)
        self._btn(bar, "Save notifications", self._save_notify, accent=True).pack(side=tk.LEFT)
        self._btn(bar, "Send test", self._test_notify).pack(side=tk.LEFT, padx=6)

    def _refresh_notify(self):
        values = hbconfig.notification_editor()
        for key, variable in self.notify_vars.items():
            variable.set(values.get(key, ""))
        self.notify_security.set(values.get("smtp_security", "starttls"))
        self.notify_password.set("")
        settings = hbnscan.load_settings()
        self.notify_enabled.set(bool(settings["notify_enabled"]))
        self.notify_severity.set(settings["notify_min_severity"])

    def _save_notify(self):
        try:
            hbconfig.save_notifications(
                smtp_host=self.notify_vars["smtp_host"].get(),
                smtp_port=self.notify_vars["smtp_port"].get(),
                smtp_security=self.notify_security.get(),
                smtp_user=self.notify_vars["smtp_user"].get(),
                smtp_password=self.notify_password.get(),
                smtp_from=self.notify_vars["smtp_from"].get(),
                email_to=self.notify_vars["email_to"].get(),
                sms_to=self.notify_vars["sms_to"].get(),
                sms_gateway=self.notify_vars["sms_gateway"].get())
            hbnscan.save_settings(notify_enabled=self.notify_enabled.get(),
                                  notify_min_severity=self.notify_severity.get())
        except ValueError as exc:
            messagebox.showerror("Notifications", str(exc), parent=self)
            return
        self._refresh_notify()
        self._say("notification settings saved: "
                  + hbnscan.describe_notifications())
        self.app.refresh_table()

    def _test_notify(self):
        self._say("sending a test message …")
        self.update_idletasks()
        lines = []
        code = hbnscan.send_test_notification(log_callback=lines.append)
        for line in lines:
            self.app.log("  " + line)
        if code == 0:
            self._say("test message sent")
        else:
            self._say("test failed; see the log", error=True)

    # -- diagnostics --------------------------------------------------------

    def _build_diagnostics(self, frame):
        self._title(frame, "DIAGNOSTICS",
                    "A whole-configuration check: broken URLs, unknown categories, topics over "
                    "the limit, criteria problems, notification gaps and sources that failed "
                    "their last fetch.")
        bar = tk.Frame(frame, bg=self.p.BG_PANEL)
        bar.pack(fill=tk.X, padx=12, pady=(0, 4))
        self._btn(bar, "Run check", self.run_diagnostics, accent=True).pack(side=tk.LEFT, padx=3)
        self.diagnostic_summary = tk.Label(bar, text="", bg=self.p.BG_PANEL,
                                           fg=self.p.FG_MUTED, font=("Sans", 9))
        self.diagnostic_summary.pack(side=tk.LEFT, padx=10)
        self.diagnostics_tree = self._tree(frame, (
            ("level", "Level", 80, False), ("area", "Area", 120, False),
            ("message", "Finding", 600, True)), height=18)

    def run_diagnostics(self):
        conn = None
        try:
            conn = hbnscan.connect(timeout=5)
        except hbnscan.sqlite3.Error:
            conn = None
        try:
            findings = hbconfig.diagnose(conn)
        finally:
            if conn is not None:
                conn.close()
        for iid in self.diagnostics_tree.get_children():
            self.diagnostics_tree.delete(iid)
        for index, finding in enumerate(findings):
            self.diagnostics_tree.insert("", tk.END, iid=f"diag{index}",
                                         values=(finding["level"], finding["area"],
                                                 finding["message"]))
        summary = hbconfig.summarise(findings)
        self.diagnostic_summary.configure(
            text=summary,
            fg=self.p.RED if "error" in summary else self.p.FG_MUTED)
        self._say("diagnostics: " + summary, error="error" in summary)

    def _refresh_diagnostics(self):
        self.run_diagnostics()


def open_configuration(app):
    """Open (or focus) the configuration window for a running app."""
    existing = getattr(app, "_config_window", None)
    if existing is not None and existing.winfo_exists():
        existing.lift()
        existing.focus_force()
        existing.nav.selection_clear(0, tk.END)
        existing.nav.selection_set(0)
        existing._show_current()
        return existing
    window = ConfigurationWindow(app)
    app._config_window = window

    def on_close():
        app._config_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)
    return window
