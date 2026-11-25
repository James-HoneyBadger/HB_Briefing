"""The entity index answers "what do we know about this company" across every surface.

Signals, dossiers, actions and notes live in different tables; the report has to join them
without inventing anything, and it has to match the way people type a name ("nvidia" for
"NVIDIA", "Acme" for "Acme Corp.").
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


class EntityReportTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, value in (("DB_PATH", root / "state.db"),
                            ("SETTINGS_PATH", root / "settings.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)

    def add(self, item_hash, title, entities, *, flagged=1, dismissed=0, published="2026-11-12",
            severity="high", category="litigation"):
        self.conn.execute(SIGNAL_SQL, (
            item_hash, title, f"https://e.test/{item_hash}", "https://ftc.gov/feed.xml", flagged,
            "[Local Heuristic] Core focus.", "2026-11-13T10:00:00", category, severity,
            json.dumps(entities), "", "[]", 0.9, 1, dismissed, published, "heuristic", "core"))
        self.conn.commit()

    def report(self, query, **kwargs):
        return hbworkspace.entity_report(self.conn, query, **kwargs)

    def test_a_substring_finds_the_company_in_any_case(self):
        self.add("h1", "FTC sues Acme Fiber", {"companies": ["Acme Fiber LLC"]})
        self.add("h2", "Acme expands rural build", {"companies": ["Acme Fiber"]})
        report = self.report("acme")
        self.assertEqual(report["counts"]["signals"], 2)
        self.assertEqual({entry["name"] for entry in report["matched"]},
                         {"Acme Fiber LLC", "Acme Fiber"})

    def test_spellings_of_one_company_are_counted_together(self):
        self.add("h1", "NVIDIA beats estimates", {"companies": ["NVIDIA"]})
        self.add("h2", "Nvidia beats estimates", {"companies": ["Nvidia"]})
        self.add("h3", "Nvidia Corp. guidance", {"companies": ["Nvidia Corp."]})
        self.add("h4", "Nvidia Corp guidance", {"companies": ["Nvidia Corp"]})
        report = self.report("nvidia")
        names = [entry["name"] for entry in report["matched"]]
        self.assertEqual(len(names), 2, names)
        # "NVIDIA"/"Nvidia" is one entry, "Nvidia Corp."/"Nvidia Corp" is the other.
        self.assertEqual(sorted(entry["count"] for entry in report["matched"]), [2, 2])

    def test_unflagged_rows_are_kept_and_marked(self):
        self.add("h1", "Acme paged", {"companies": ["Acme"]}, flagged=1)
        self.add("h2", "Acme background mention", {"companies": ["Acme"]}, flagged=0)
        report = self.report("acme")
        self.assertEqual(report["counts"]["signals"], 2)
        self.assertEqual(report["counts"]["flagged"], 1)
        self.assertEqual(report["signals"][0]["flagged"], 1, "flagged rows come first")

    def test_dismissed_rows_are_left_out(self):
        self.add("h1", "Acme paged", {"companies": ["Acme"]})
        self.add("h2", "Acme rejected", {"companies": ["Acme"]}, dismissed=1)
        self.assertEqual(self.report("acme")["counts"]["signals"], 1)

    def test_dossiers_actions_and_notes_are_joined_in(self):
        self.add("h1", "Acme fined", {"companies": ["Acme"]})
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme enforcement",
                                                "What is the Acme posture?")
        hbworkspace.add_item(self.conn, dossier_id, "h1")
        hbworkspace.create_action(self.conn, "Read the Acme order", dossier_id=dossier_id,
                                  item_hash="h1", priority="high")
        hbworkspace.add_note(self.conn, "Check whether Acme appeals.", dossier_id=dossier_id)
        report = self.report("acme")
        self.assertEqual([row["name"] for row in report["dossiers"]], ["Acme enforcement"])
        self.assertEqual(report["counts"]["open_actions"], 1)
        self.assertEqual(report["counts"]["notes"], 1)
        self.assertEqual(report["dossiers"][0]["matched"], 1)

    def test_a_dossier_that_names_the_entity_is_found_before_any_signal(self):
        hbworkspace.create_dossier(self.conn, "Acme", "Watch the Acme takeover")
        report = self.report("acme")
        self.assertEqual(report["counts"]["signals"], 0)
        self.assertEqual([row["name"] for row in report["dossiers"]], ["Acme"])

    def test_severity_and_source_breakdown_match_the_rows(self):
        self.add("h1", "Acme one", {"companies": ["Acme"]}, severity="high")
        self.add("h2", "Acme two", {"companies": ["Acme"]}, severity="low")
        report = self.report("acme")
        self.assertEqual(report["counts"]["by_severity"], {"high": 1, "low": 1})
        self.assertEqual(report["sources"][0]["name"], "ftc.gov")
        self.assertEqual(report["span"], {"first": "2026-11-12", "last": "2026-11-12"})

    def test_an_unknown_name_is_an_empty_report_not_an_error(self):
        report = self.report("nothing-like-this")
        self.assertEqual(report["counts"]["signals"], 0)
        self.assertEqual(report["dossiers"], [])
        self.assertEqual(report["matched"], [])

    def test_too_short_a_query_is_refused(self):
        with self.assertRaises(ValueError):
            self.report("a")

    def test_the_limit_caps_the_signal_list(self):
        for index in range(5):
            self.add(f"h{index}", f"Acme item {index}", {"companies": ["Acme"]})
        self.assertEqual(len(self.report("acme", limit=2)["signals"]), 2)

    def test_co_mentions_count_distinct_stories_and_keep_evidence(self):
        self.add("h1", "First", {"companies": ["Acme", "Beta", "Beta"], "regulators": ["FTC"]})
        self.add("h2", "Second", {"companies": ["Acme", "BETA"]})
        self.add("h3", "Third", {"companies": ["Acme", "Beta"]})
        self.conn.execute("UPDATE seen_items SET cluster_key = 'shared' WHERE item_hash IN ('h1', 'h2')")
        self.conn.commit()
        report = self.report("acme")
        beta = next(entry for entry in report["associations"] if entry["name"].lower() == "beta")
        self.assertEqual(beta["signals"], 3)
        self.assertEqual(beta["stories"], 2)
        self.assertEqual(len(beta["examples"]), 3)
        self.assertFalse(report["association_scope"]["truncated"])
        self.assertTrue(self.report("acme", limit=1)["association_scope"]["truncated"])

    def test_malformed_entity_json_is_ignored(self):
        self.conn.execute(SIGNAL_SQL, (
            "h1", "Broken entities", "https://e.test/b", "https://f.test/rss", 1, "", 
            "2026-11-13T10:00:00", "other", "none", "not json", "", "[]", None, 0, 0, "",
            "heuristic", ""))
        self.conn.commit()
        self.assertEqual(self.report("acme")["counts"]["signals"], 0)


class EntityCliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, value in (("DB_PATH", root / "state.db"),
                            ("SETTINGS_PATH", root / "settings.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        conn = hbnscan.init_db()
        conn.execute(SIGNAL_SQL, (
            "h1", "Acme fined", "https://e.test/a", "https://ftc.gov/feed.xml", 1, 
            "[Local Heuristic] Core focus.", "2026-11-13T10:00:00", "litigation", "high",
            json.dumps({"companies": ["Acme Fiber"]}), "", "[]", 0.9, 1, 0, "2026-11-12",
            "heuristic", "core"))
        conn.commit()
        conn.close()

    def test_json_output_is_machine_readable(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = hbworkspace.main(["entity", "acme", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["query"], "acme")
        self.assertEqual(payload["counts"]["signals"], 1)

    def test_text_output_names_the_company_and_the_headline(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = hbworkspace.main(["entity", "acme"])
        text = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Acme Fiber", text)
        self.assertIn("Acme fined", text)

    def test_a_missing_entity_explains_itself(self):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = hbworkspace.main(["entity", "zzz-nothing"])
        self.assertEqual(code, 0)
        self.assertIn("No stored signal", buffer.getvalue())


class SearchReachesEntitiesTest(unittest.TestCase):
    def test_the_search_box_finds_an_entity_name(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        with mock.patch.object(hbnscan, "DB_PATH", root / "state.db"), \
             mock.patch.object(hbnscan, "SETTINGS_PATH", root / "settings.json"):
            conn = hbnscan.init_db()
            self.addCleanup(conn.close)
            conn.execute(SIGNAL_SQL, (
                "h1", "Penalty issued", "https://e.test/a", "https://ftc.gov/feed.xml", 1,
                "[Local Heuristic] Core focus.", "2026-11-13T10:00:00", "litigation", "high",
                json.dumps({"companies": ["Acme Fiber"]}), "", "[]", 0.9, 1, 0,
                "2026-11-12", "heuristic", "core"))
            conn.commit()
            rows, total = hbnscan.select_signals(conn, {"query": "acme"})
        self.assertEqual(total, 1, "the headline has no 'acme': the entity column must match")
        self.assertEqual(rows[0]["item_hash"], "h1")


if __name__ == "__main__":
    unittest.main()
