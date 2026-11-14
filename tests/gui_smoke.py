"""Desktop smoke test: build the window, render a row, open settings, rebuild.

Run headless with ``xvfb-run -a python tests/gui_smoke.py`` (the CI workflow does this).
Skips cleanly where Tk or a display is unavailable, so the rest of the suite still runs
for contributors without a desktop.
"""
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import tkinter as tk
except ImportError:                                          # pragma: no cover
    print("SKIP: tkinter is not installed")
    sys.exit(0)

import hbnscan                                              # noqa: E402
import hbnscan_gui                                          # noqa: E402

SIGNAL_SQL = (
    "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, seen_at,"
    " category, severity, entities, action, facts, confidence, deep_read, dismissed,"
    " published, source_layer, verdict_reason) VALUES"
    " ('h1','Acme fined','https://e.test/a','https://f.test/rss',1,"
    " '[Watchlist] acme → regulatory_action/high. Penalty issued.','2026-11-13T10:00:00',"
    " 'regulatory_action','high','{}','Do a thing','[]',0.9,1,0,'2026-11-12T09:00:00',"
    " 'watchlist','rule: acme')"
)


def walk(widget):
    """Every descendant of a widget, for assertions about the built dialog."""
    for child in widget.winfo_children():
        yield child
        yield from walk(child)


def main():
    tmp = Path(tempfile.mkdtemp())
    patches = [
        mock.patch.object(hbnscan, "DB_PATH", tmp / "t.db"),
        mock.patch.object(hbnscan, "SETTINGS_PATH", tmp / "s.json"),
        mock.patch.object(hbnscan, "REPORT_PATH", tmp / "r.md"),
        mock.patch.object(hbnscan, "load_settings",
                          lambda: dict(hbnscan.DEFAULT_SETTINGS, auto_scan=False)),
        mock.patch.object(hbnscan, "load_feeds", lambda: []),
        mock.patch.object(hbnscan, "load_watchlist", lambda: []),
        mock.patch.object(hbnscan, "load_topics", lambda: []),
    ]
    for patch in patches:
        patch.start()
    app = None
    try:
        try:
            app = hbnscan_gui.NewsScanApp()
        except tk.TclError as exc:                          # pragma: no cover
            print(f"SKIP: no display available ({exc})")
            return 0
        app.update()

        conn = hbnscan.init_db()
        try:
            conn.execute(SIGNAL_SQL)
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('prev_scan_at', '')")
            conn.commit()
        finally:
            conn.close()

        app.refresh_table()
        app.update()
        rows = app.tree.get_children()
        assert len(rows) == 1, f"expected one grid row, got {len(rows)}"
        values = app.tree.item(rows[0], "values")
        assert values[4] == "Acme fined", values
        assert values[5].startswith("★ watchlist"), values
        assert values[1] == "2026-11-12", f"publication date not shown: {values}"

        app.filter_vars["filter_recent"].set("3d")
        app._on_filter_changed("filter_recent", "3d")
        app.update()
        assert len(app.tree.get_children()) == 1, "a 3-day window lost a 1-day-old story"
        app.filter_vars["filter_recent"].set("all")
        app._on_filter_changed("filter_recent", "all")
        app.update()
        rows = app.tree.get_children()

        app.tree.selection_set(rows[0])
        app._on_row_select()
        app.update()
        detail = app.detail_text.get("1.0", tk.END)
        assert "Decided by: watchlist" in detail, detail
        assert "Published: 2026-11-12 (1d)" in detail, detail

        app.open_settings()
        app.update()
        dialogs = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)]
        assert len(dialogs) == 1, f"settings dialog missing ({len(dialogs)} found)"
        dialog = dialogs[0]
        widgets = list(walk(dialog))
        labels = {str(w.cget("text")) for w in widgets if isinstance(w, tk.Label)}
        for title in ("PROFILE", "FEED DATE RANGE", "LLM / AI PROVIDER", "SCANNING",
                      "NOTIFICATIONS", "APPEARANCE", "DATA"):
            assert title in labels, f"settings section missing: {title}"

        buttons = {str(w.cget("text")): w for w in widgets if isinstance(w, tk.Button)}
        for name in ("Clear dates", "Save", "Test", "Send test", "Back up database"):
            assert name in buttons, f"settings control missing: {name}"
        buttons["Save"].invoke()
        app.update()
        assert not dialog.winfo_exists(), "Save did not close the settings dialog"
        assert len(app.tree.get_children()) == 1, "the grid broke after saving settings"

        # Dossier -> signal -> action -> report, through the real widgets.
        app._dossier_dialog()
        app.update()
        dialog = [w for w in app.winfo_children()
                  if isinstance(w, tk.Toplevel)][0]
        entries = [w for w in walk(dialog) if isinstance(w, tk.Entry)]
        entries[0].insert(0, "Acme dossier")
        buttons = {str(w.cget("text")): w for w in walk(dialog)
                   if isinstance(w, tk.Button)}
        buttons["Save"].invoke()
        app.update()
        assert len(app.dossier_ids) == 1, app.dossier_ids

        app.tree.selection_set(app.tree.get_children()[0])
        app._add_current_to_dossier()
        app.update()
        assert "Acme fined" in app.dossier_text.get("1.0", tk.END), "signal not in dossier report"

        app._action_dialog()
        app.update()
        dialog = [w for w in app.winfo_children()
                  if isinstance(w, tk.Toplevel)][0]
        entries = [w for w in walk(dialog) if isinstance(w, tk.Entry)]
        entries[0].insert(0, "Call the clerk")
        buttons = {str(w.cget("text")): w for w in walk(dialog)
                   if isinstance(w, tk.Button)}
        buttons["Save"].invoke()
        app.update()
        assert len(app.actions_tree.get_children()) == 1

        coverage = app.coverage_lbl.cget("text")
        assert "sources" in coverage and "signals" in coverage, coverage
        assert len(app.sources_tree.get_children()) >= 0

        app.refresh_briefing()
        app.update()
        app._rebuild_ui()
        app.update()
        assert len(app.tree.get_children()) == 1, "the grid did not survive a rebuild"
        print("SMOKE OK")
        return 0
    finally:
        if app is not None:
            app.destroy()
        for patch in reversed(patches):
            patch.stop()


if __name__ == "__main__":
    sys.exit(main())
