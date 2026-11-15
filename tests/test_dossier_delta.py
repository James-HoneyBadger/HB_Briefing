"""A dossier report over time: what is new, what got resolved, what reopened.

The point of the snapshot is that a second export answers "what changed since I last looked"
without the reader diffing two documents by hand. It must be honest on the first export too.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan
import hbworkspace

SIGNAL_SQL = (
    "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, seen_at,"
    " category, severity, entities, action, facts, confidence, deep_read, dismissed,"
    " published, source_layer, verdict_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class DossierDeltaTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, value in (("DB_PATH", self.root / "state.db"),
                            ("SETTINGS_PATH", self.root / "settings.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)
        self.dossier_id = hbworkspace.create_dossier(self.conn, "Acme", "Are they expanding?")
        self.add_signal("h1", "Acme wins permit")
        hbworkspace.add_item(self.conn, self.dossier_id, "h1")

    def add_signal(self, item_hash, title, published="2026-11-12"):
        self.conn.execute(SIGNAL_SQL, (
            item_hash, title, f"https://e.test/{item_hash}", "https://ftc.gov/feed.xml", 1,
            "[Local Heuristic] Core focus.", "2026-11-13T10:00:00", "litigation", "high",
            "{}", "", "[]", 0.9, 1, 0, published, "heuristic", "core"))
        self.conn.commit()

    def export(self, name="report.md"):
        return hbworkspace.export_report(self.conn, self.dossier_id,
                                         self.root / name, "md")

    def changes(self):
        return hbworkspace.dossier_report(self.conn, self.dossier_id)["changes"]

    def test_the_first_report_says_there_is_nothing_to_compare(self):
        report = hbworkspace.dossier_report(self.conn, self.dossier_id, since_last_export=True)
        changes = report["changes"]
        self.assertTrue(changes["first_export"])
        text = hbworkspace.render_report_markdown(report)
        self.assertIn("## What changed", text)
        self.assertIn("First export", text)

    def test_a_report_without_history_omits_the_section(self):
        report = hbworkspace.dossier_report(self.conn, self.dossier_id)
        self.assertIsNone(report["changes"])
        self.assertNotIn("## What changed", hbworkspace.render_report_markdown(report))

    def test_exporting_records_a_snapshot(self):
        self.assertIsNone(hbworkspace.latest_snapshot(self.conn, self.dossier_id))
        self.export()
        snapshot = hbworkspace.latest_snapshot(self.conn, self.dossier_id)
        self.assertIsNotNone(snapshot)
        payload = json.loads(snapshot["payload"])
        self.assertEqual(payload["items"], ["h1"])
        self.assertTrue(snapshot["path"].endswith("report.md"))

    def test_new_signals_notes_and_actions_appear(self):
        self.export()
        self.add_signal("h2", "Acme appeals")
        hbworkspace.add_item(self.conn, self.dossier_id, "h2")
        hbworkspace.add_note(self.conn, "Does the appeal stay the permit?",
                             dossier_id=self.dossier_id)
        hbworkspace.create_action(self.conn, "Read the appeal", dossier_id=self.dossier_id)
        changes = self.changes()
        self.assertEqual([entry["hash"] for entry in changes["new_signals"]], ["h2"])
        self.assertEqual(len(changes["new_notes"]), 1)
        self.assertEqual(len(changes["new_actions"]), 1)
        self.assertEqual(changes["counts"]["signals"], 1)

    def test_a_resolved_action_is_reported_as_resolved(self):
        action_id = hbworkspace.create_action(self.conn, "Read the order",
                                              dossier_id=self.dossier_id)
        self.export()
        hbworkspace.update_action(self.conn, action_id, status="done")
        changes = self.changes()
        self.assertEqual(len(changes["resolved_actions"]), 1)
        self.assertEqual(changes["resolved_actions"][0]["previous_status"], "open")
        self.assertEqual(changes["counts"]["resolved"], 1)

    def test_reopening_is_reported_separately(self):
        action_id = hbworkspace.create_action(self.conn, "Read the order",
                                              dossier_id=self.dossier_id)
        hbworkspace.update_action(self.conn, action_id, status="done")
        self.export()
        hbworkspace.update_action(self.conn, action_id, status="open")
        changes = self.changes()
        self.assertEqual(len(changes["reopened_actions"]), 1)
        self.assertEqual(changes["reopened_actions"][0]["previous_status"], "done")

    def test_an_unchanged_dossier_says_so(self):
        self.export()
        changes = self.changes()
        self.assertEqual(changes["counts"]["signals"], 0)
        self.assertIn("No change", hbworkspace.describe_changes(changes))

    def test_the_section_lists_the_new_signal_with_its_link(self):
        self.export()
        self.add_signal("h2", "Acme appeals the permit")
        hbworkspace.add_item(self.conn, self.dossier_id, "h2")
        report = hbworkspace.dossier_report(self.conn, self.dossier_id)
        text = hbworkspace.render_report_markdown(report)
        self.assertIn("Acme appeals the permit", text)
        self.assertIn("https://e.test/h2", text)
        self.assertLess(text.index("## What changed"), text.index("## At a glance"),
                        "a returning reader should meet the changes first")

    def test_html_carries_the_same_section(self):
        self.export()
        self.add_signal("h2", "Acme appeals the permit")
        hbworkspace.add_item(self.conn, self.dossier_id, "h2")
        report = hbworkspace.dossier_report(self.conn, self.dossier_id)
        page = hbworkspace.render_report_html(report)
        self.assertIn("What changed", page)
        self.assertIn("Acme appeals the permit", page)

    def test_json_carries_the_changes_object(self):
        self.export()
        self.add_signal("h2", "Acme appeals")
        hbworkspace.add_item(self.conn, self.dossier_id, "h2")
        report = hbworkspace.dossier_report(self.conn, self.dossier_id)
        payload = json.loads(hbworkspace.render_report_json(report))
        self.assertEqual([entry["hash"] for entry in payload["changes"]["new_signals"]], ["h2"])

    def test_a_removed_signal_is_not_reported_as_new(self):
        self.export()
        self.conn.execute("DELETE FROM dossier_items WHERE dossier_id = ? AND item_hash = 'h1'",
                          (self.dossier_id,))
        self.conn.commit()
        self.assertEqual(self.changes()["new_signals"], [])

    def test_a_corrupt_snapshot_degrades_to_first_export(self):
        self.export()
        self.conn.execute("UPDATE dossier_snapshots SET payload = 'not json'")
        self.conn.commit()
        changes = self.changes()
        self.assertEqual(changes["new_signals"], [],
                         "an unreadable snapshot must not turn every signal into 'new'")
        self.assertTrue(changes["first_export"])
        self.assertTrue(changes["since"], "the timestamp of that export is still known")


class CliFlagTest(unittest.TestCase):
    def test_the_flag_is_accepted_by_show_and_report(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        import contextlib
        import io
        with mock.patch.object(hbnscan, "DB_PATH", root / "state.db"), \
             mock.patch.object(hbnscan, "SETTINGS_PATH", root / "settings.json"):
            conn = hbnscan.init_db()
            hbworkspace.create_dossier(conn, "Acme", "Watch Acme")
            conn.close()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = hbworkspace.main(["dossier", "show", "Acme", "--since-last-export"])
        self.assertEqual(code, 0)
        self.assertIn("First export", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
