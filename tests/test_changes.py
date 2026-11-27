"""Change intelligence: what is genuinely new, against a watermark the user controls."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import hbnscan
import hbworkspace


class ChangeCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for name, value in (("DB_PATH", Path(self.directory.name) / "state.db"),
                            ("SETTINGS_PATH", Path(self.directory.name) / "s.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        patch = mock.patch.object(hbnscan, "load_settings",
                                  return_value=dict(hbnscan.DEFAULT_SETTINGS))
        patch.start()
        self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)

    def add(self, item_hash, title, seen_at, *, link=None, story="", severity="high",
            entities="{}", flagged=1, dismissed=0, feed="https://feed.test/rss"):
        link = link or f"https://e.test/{item_hash}"
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason, cluster_key, title_key) "
            "VALUES (?, ?, ?, ?, ?, '', ?, 'policy', ?, ?, '', '[]', NULL, 0, ?, '2026-11-12', "
            "'heuristic', '', ?, '')",
            (item_hash, title, link, feed, flagged, seen_at, severity, entities, dismissed,
             story))
        self.conn.commit()
        return item_hash

    def ago(self, **delta):
        return (datetime.now() - timedelta(**delta)).isoformat(timespec="seconds")


class WatermarkTest(ChangeCase):
    def test_a_new_signal_and_a_story_update_are_different_findings(self):
        old = self.ago(days=3)
        self.add("existing", "Acme fined", old, story="u:acme")
        self.add("update", "Acme fined again", self.ago(hours=2), story="u:acme")
        self.add("fresh", "FTC opens inquiry", self.ago(hours=1), story="u:ftc")
        summary = hbnscan.changes_since(self.conn, self.ago(days=1))
        self.assertEqual([e["item_hash"] for e in summary["new_signals"]], ["fresh"])
        self.assertEqual([e["item_hash"] for e in summary["story_updates"]], ["update"])
        self.assertEqual(summary["story_updates"][0]["story_first_seen"], old)

    def test_nothing_from_before_the_watermark_is_repeated(self):
        self.add("old", "Old news", self.ago(days=5))
        summary = hbnscan.changes_since(self.conn, self.ago(days=1))
        self.assertEqual(summary["counts"]["new_signals"], 0)
        self.assertEqual(hbnscan.render_changes(summary).count("Old news"), 0)

    def test_a_revised_assessment_reports_both_sides(self):
        item = self.add("h1", "Acme fined", self.ago(days=2), story="u:acme")
        with mock.patch.object(hbnscan, "load_watchlist", return_value=[]):
            hbnscan.record_assessment(self.conn, item, link="https://e.test/h1",
                                      method="heuristic", layer="heuristic", category="policy",
                                      severity="medium", flagged=1, confidence=0.4,
                                      analysis="first", reason="core: acme")
            hbnscan.record_assessment(self.conn, item, link="https://e.test/h1",
                                      method="watchlist", layer="watchlist", category="policy",
                                      severity="high", flagged=1, confidence=0.4,
                                      analysis="second", reason="rule: acme")
        self.conn.commit()
        summary = hbnscan.changes_since(self.conn, self.ago(days=1))
        revised = summary["revised_assessments"]
        self.assertEqual(len(revised), 1)
        self.assertEqual(revised[0]["from"]["severity"], "medium")
        self.assertEqual(revised[0]["to"]["severity"], "high")
        self.assertEqual(revised[0]["method"], "watchlist")

    def test_an_initial_assessment_is_not_a_revision(self):
        item = self.add("h1", "Acme fined", self.ago(days=2))
        hbnscan.record_assessment(self.conn, item, link="https://e.test/h1",
                                  method="heuristic", layer="heuristic", category="policy",
                                  severity="medium", analysis="first")
        self.conn.commit()
        self.assertEqual(hbnscan.changes_since(self.conn, self.ago(days=1))
                         ["counts"]["revised_assessments"], 0)

    def test_only_genuinely_new_entities_are_listed(self):
        self.add("old", "Acme fined", self.ago(days=3),
                 entities=json.dumps({"companies": ["Acme"]}))
        self.add("new", "Acme and Beta sued", self.ago(hours=1),
                 entities=json.dumps({"companies": ["Acme", "Beta"]}))
        names = [e["name"] for e in hbnscan.changes_since(self.conn, self.ago(days=1))
                 ["new_entities"]]
        self.assertEqual(names, ["Beta"], "Acme was already known and is not news")

    def test_a_story_correction_is_reported(self):
        self.add("a", "Acme wins permit", self.ago(days=2), story="u:a")
        self.add("b", "Acme wins permit - wire", self.ago(days=2), story="u:b")
        hbnscan.merge_stories(self.conn, "u:a", "u:b", note="same permit")
        summary = hbnscan.changes_since(self.conn, self.ago(days=1))
        self.assertEqual(summary["counts"]["story_corrections"], 1)
        self.assertEqual(summary["story_corrections"][0]["action"], "merge")

    def test_dismissed_signals_are_not_new_findings(self):
        self.add("gone", "Dismissed thing", self.ago(hours=1), dismissed=1)
        self.assertEqual(hbnscan.changes_since(self.conn, self.ago(days=1))
                         ["counts"]["new_signals"], 0)


class AcknowledgmentTest(ChangeCase):
    def test_acknowledging_moves_the_watermark_without_touching_the_data(self):
        item = self.add("h1", "Acme fined", self.ago(hours=2))
        before = self.conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]
        when = hbnscan.acknowledge(self.conn, "signal", item, note="read it")
        self.assertTrue(when)
        state = hbnscan.review_state(self.conn, item)
        self.assertTrue(state["acknowledged"])
        self.assertEqual(state["note"], "read it")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0],
                         before, "acknowledging is not collecting")

    def test_a_signal_stored_in_the_same_second_as_the_acknowledgment_is_not_new(self):
        stamp = datetime.now().isoformat(timespec="seconds")
        self.add("h1", "Acme fined", stamp)
        hbnscan.acknowledge(self.conn, "briefing", "all")
        summary = hbnscan.review_subject(self.conn)
        self.assertEqual(summary["counts"]["new_signals"], 0)
        self.assertEqual(summary["basis"], "last acknowledgment")

    def test_without_an_acknowledgment_the_answer_says_what_it_compared_against(self):
        self.add("h1", "Acme fined", self.ago(hours=2))
        summary = hbnscan.review_subject(self.conn, days=2)
        self.assertEqual(summary["basis"], "last 2 day(s)")
        self.assertEqual(summary["counts"]["new_signals"], 1)

    def test_a_stored_summary_comes_back_unchanged(self):
        self.add("h1", "Acme fined", self.ago(hours=2))
        first = hbnscan.review_subject(self.conn)
        stored = hbnscan.latest_change_summary(self.conn)
        self.assertEqual(stored["since"], first["since"])
        self.assertEqual(stored["counts"], first["counts"])
        self.assertEqual(stored["generated_at"], first["generated_at"])

    def test_an_unknown_subject_type_is_refused(self):
        with self.assertRaises(ValueError):
            hbnscan.acknowledge(self.conn, "horoscope", "today")

    def test_a_review_can_be_scoped_to_one_dossier(self):
        self.add("in", "Acme fined", self.ago(hours=1))
        self.add("out", "Unrelated", self.ago(hours=1))
        dossier = hbworkspace.create_dossier(self.conn, "Acme")
        hbworkspace.add_item(self.conn, dossier, "in")
        self.conn.commit()
        summary = hbnscan.review_subject(self.conn, "dossier", "Acme", days=1)
        self.assertEqual([e["item_hash"] for e in summary["new_signals"]], ["in"])


class ReviewCommandTest(ChangeCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = hbworkspace.main(list(argv))
        return code, output.getvalue()

    def test_review_prints_what_changed(self):
        self.add("h1", "FTC opens inquiry", self.ago(hours=1))
        code, text = self.run_cli("review", "--days", "1")
        self.assertEqual(code, 0, text)
        self.assertIn("FTC opens inquiry", text)
        self.assertIn("New signals", text)

    def test_acknowledge_then_review_reports_nothing_new(self):
        self.add("h1", "FTC opens inquiry", self.ago(hours=1))
        code, text = self.run_cli("review", "--ack")
        self.assertEqual(code, 0, text)
        self.assertIn("acknowledged at", text)
        code, text = self.run_cli("review")
        self.assertEqual(code, 0)
        self.assertIn("Nothing new", text)

    def test_json_output_is_machine_readable(self):
        self.add("h1", "FTC opens inquiry", self.ago(hours=1))
        code, text = self.run_cli("review", "--days", "1", "--json")
        self.assertEqual(code, 0)
        summary = json.loads(text)
        self.assertEqual(summary["counts"]["new_signals"], 1)
        self.assertIn("notes", summary)

    def test_stored_prints_the_last_summary(self):
        self.add("h1", "FTC opens inquiry", self.ago(hours=1))
        self.run_cli("review", "--days", "1")
        code, text = self.run_cli("review", "--stored")
        self.assertEqual(code, 0, text)
        self.assertIn("FTC opens inquiry", text)

    def test_an_unknown_dossier_is_a_usage_error(self):
        code, text = self.run_cli("review", "--dossier", "nope")
        self.assertEqual(code, 2)
        self.assertIn("[FAIL]", text)


if __name__ == "__main__":
    unittest.main()
