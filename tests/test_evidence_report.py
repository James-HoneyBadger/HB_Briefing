"""Regression coverage for evidence-aware reports."""
import json

import hbworkspace
from test_workspace import WorkspaceCase


class EvidenceReportTest(WorkspaceCase):
    def test_grouped_copies_do_not_crowd_out_other_stories(self):
        dossier = hbworkspace.create_dossier(self.conn, "Coverage")
        a = self.add_signal("Permit issued", link="https://a.test/permit", feed="https://feeds.test/a")
        b = self.add_signal("Permit issued again", link="https://b.test/permit", feed="https://feeds.test/b")
        c = self.add_signal("Different event", link="https://c.test/event")
        self.conn.execute("UPDATE seen_items SET cluster_key = 'same' WHERE item_hash IN (?, ?)", (a, b))
        self.conn.commit()
        for item in (a, b, c):
            hbworkspace.add_item(self.conn, dossier, item)
        report = hbworkspace.dossier_report(self.conn, dossier, developments=2)
        self.assertEqual(report["counts"]["signals"], 3)
        evidence = report["story_evidence"]
        self.assertEqual(evidence["story_count"], 2)
        self.assertEqual(evidence["multi_host_stories"], 1)
        self.assertEqual(len(report["developments"]), 2)
        self.assertIn("Different event", [item["title"] for item in report["developments"]])
        self.assertEqual(len(report["timeline"]), 3)
        for rendered in (hbworkspace.render_report_markdown(report),
                         hbworkspace.render_report_html(report)):
            self.assertIn("Story coverage", rendered)
            self.assertIn("not independent corroboration", rendered)
        self.assertEqual(json.loads(hbworkspace.render_report_json(report))["story_evidence"], evidence)

    def test_invalid_publication_date_falls_back_to_detection(self):
        dossier = hbworkspace.create_dossier(self.conn, "Dates")
        item = self.add_signal("Invalid date", published="not a date")
        hbworkspace.add_item(self.conn, dossier, item)
        report = hbworkspace.dossier_report(self.conn, dossier)
        self.assertEqual(report["counts"]["undated"], 1)
        self.assertEqual(report["timeline"][0]["date"], "2026-11-13")
        self.assertTrue(report["timeline"][0]["undated"])

    def test_wrong_snapshot_shape_does_not_invent_changes(self):
        dossier = hbworkspace.create_dossier(self.conn, "Snapshots")
        for payload in ("[]", '{"items": [], "notes": ["bad"], "actions": {}}', "{}"):
            result = hbworkspace.dossier_changes(self.conn, dossier, {
                "payload": payload, "created_at": "2026-11-13", "path": ""})
            self.assertTrue(result["first_export"])
            self.assertFalse(any(result["counts"].values()))

    def test_aggregator_is_not_a_publisher(self):
        dossier = hbworkspace.create_dossier(self.conn, "Aggregator")
        item = self.add_signal("Aggregated", link="https://news.google.com/rss/articles/example")
        hbworkspace.add_item(self.conn, dossier, item)
        evidence = hbworkspace.dossier_report(self.conn, dossier)["story_evidence"]
        self.assertEqual(evidence["unresolved_links"], 1)
        self.assertEqual(evidence["stories"][0]["article_hosts"], [])
