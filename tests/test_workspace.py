import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import hbnscan
import hbworkspace


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "state.db"
        patches = [
            mock.patch.object(hbnscan, "DB_PATH", self.db_path),
            mock.patch.object(hbnscan, "SETTINGS_PATH", Path(self.directory.name) / "s.json"),
            mock.patch.object(hbnscan, "load_settings",
                              return_value=dict(hbnscan.DEFAULT_SETTINGS)),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)

    def add_signal(self, title, *, published=None, severity="high", layer="heuristic",
                   link=None, feed="https://feed.test/rss", deep_read=0):
        published = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds") \
            if published is None else published
        conn = self.conn
        conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason, cluster_key, title_key) "
            "VALUES (?, ?, ?, ?, 1, '[Local Heuristic] Matched ruling.', "
            "'2026-11-13T10:00:00', 'policy', ?, '{}', '', '[]', NULL, ?, 0, ?, ?, '', "
            "?, ?)",
            (hbnscan.item_hash(title, link or title), title, link or f"https://e.test/{title}",
             feed, severity, deep_read, published, layer,
             *hbnscan.story_keys(title, link or f"https://e.test/{title}")))
        conn.commit()
        return hbnscan.item_hash(title, link or title)


class DossierTest(WorkspaceCase):
    def test_create_resolve_and_duplicate(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme fiber", "Are they building?")
        self.assertGreater(dossier_id, 0)
        self.assertEqual(hbworkspace.resolve_dossier(self.conn, dossier_id)["name"], "Acme fiber")
        self.assertEqual(hbworkspace.resolve_dossier(self.conn, "acme FIBER")["id"], dossier_id)
        with self.assertRaises(ValueError):
            hbworkspace.create_dossier(self.conn, "acme fiber")
        with self.assertRaises(ValueError):
            hbworkspace.create_dossier(self.conn, "   ")

    def test_items_are_idempotent_and_carry_notes(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme")
        item = self.add_signal("Acme fined")
        self.assertTrue(hbworkspace.add_item(self.conn, dossier_id, item))
        self.assertFalse(hbworkspace.add_item(self.conn, dossier_id, item))
        self.assertTrue(hbworkspace.set_item_note(self.conn, dossier_id, item, "Ask counsel"))
        items = hbworkspace.dossier_items(self.conn, dossier_id)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["note"], "Ask counsel")
        self.assertEqual(items[0]["source"], "feed.test")
        self.assertTrue(items[0]["freshness"])

    def test_add_latest_and_remove(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme")
        self.add_signal("Second story", published=datetime.now().isoformat(timespec="seconds"))
        self.add_signal("First story", published="2020-01-01T00:00:00")
        self.assertEqual(hbworkspace.add_latest(self.conn, dossier_id, 2), 2)
        items = hbworkspace.dossier_items(self.conn, dossier_id)
        self.assertEqual(items[0]["title"], "Second story")
        self.assertTrue(hbworkspace.remove_item(self.conn, dossier_id,
                                                hbnscan.item_hash("First story", "First story")))
        self.assertEqual(hbworkspace.list_dossiers(self.conn)[0]["items"], 1)

    def test_delete_keeps_signals_and_actions(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme")
        item = self.add_signal("Acme fined")
        hbworkspace.add_item(self.conn, dossier_id, item)
        action_id = hbworkspace.create_action(self.conn, "Call counsel", dossier_id=dossier_id)
        self.assertTrue(hbworkspace.delete_dossier(self.conn, dossier_id))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0], 1)
        action = self.conn.execute(
            "SELECT dossier_id FROM actions WHERE id = ?", (action_id,)).fetchone()
        self.assertIsNone(action[0])


class ActionTest(WorkspaceCase):
    def test_validation_and_lifecycle(self):
        with self.assertRaises(ValueError):
            hbworkspace.create_action(self.conn, "")
        with self.assertRaises(ValueError):
            hbworkspace.create_action(self.conn, "x", priority="urgent")
        with self.assertRaises(ValueError):
            hbworkspace.create_action(self.conn, "x", due_date="soon")
        high = hbworkspace.create_action(self.conn, "High one", priority="high",
                                         due_date="2026-12-01")
        low = hbworkspace.create_action(self.conn, "Low one", priority="low")

        rows = hbworkspace.list_actions(self.conn)
        self.assertEqual([row["id"] for row in rows], [high, low])
        self.assertTrue(hbworkspace.complete_action(self.conn, high))
        rows = hbworkspace.list_actions(self.conn)
        self.assertEqual(rows[-1]["id"], high)
        self.assertTrue(rows[-1]["completed_at"])
        self.assertTrue(hbworkspace.complete_action(self.conn, high, undo=True))
        self.assertIsNone(hbworkspace.list_actions(self.conn)[0]["completed_at"])

    def test_action_list_markdown_marks_completed(self):
        hbworkspace.create_action(self.conn, "Open item", priority="high")
        done = hbworkspace.create_action(self.conn, "Finished", priority="low")
        hbworkspace.complete_action(self.conn, done)
        text = hbworkspace.render_action_markdown(hbworkspace.list_actions(self.conn))
        self.assertIn("- [ ] **high** Open item", text)
        self.assertIn("- [x] **low** Finished", text)


class ReportTest(WorkspaceCase):
    def build(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme fiber", "Build-out status")
        dated = self.add_signal("Acme wins permit", published="2026-11-12T09:00:00",
                                severity="high", deep_read=1)
        undated = self.add_signal("Acme complaint", published="", severity="medium")
        hbworkspace.add_item(self.conn, dossier_id, dated, "Primary source")
        hbworkspace.add_item(self.conn, dossier_id, undated)
        hbworkspace.add_note(self.conn, "Does the permit cover the whole route?", dossier_id)
        hbworkspace.create_action(self.conn, "Ask the clerk", dossier_id=dossier_id,
                                  priority="high", due_date="2026-12-01")
        return dossier_id

    def test_report_is_cited_and_marks_gaps(self):
        dossier_id = self.build()
        report = hbworkspace.dossier_report(self.conn, dossier_id)
        self.assertEqual(report["counts"]["signals"], 2)
        self.assertEqual(report["counts"]["undated"], 1)
        self.assertEqual(report["counts"]["deep_read"], 1)
        self.assertEqual(report["developments"][0]["title"], "Acme wins permit")
        self.assertTrue(any("no publication date" in gap for gap in report["gaps"]))
        self.assertTrue(any("screened from the feed only" in gap for gap in report["gaps"]))

        text = hbworkspace.render_report_markdown(report)
        self.assertIn("# Dossier: Acme fiber", text)
        self.assertIn("[Acme wins permit](https://e.test/Acme wins permit)", text.replace("%20", " "))
        self.assertIn("undated", text)
        self.assertIn("## Evidence gaps", text)
        self.assertIn("Ask the clerk", text)
        self.assertIn("Does the permit cover the whole route?", text)
        self.assertNotIn("[Local Heuristic]", text)
        self.assertIn("no model call was made", text)

    def test_html_is_escaped_and_json_round_trips(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Hostile <b>title</b>")
        item = self.add_signal("Acme <script>alert(1)</script>")
        hbworkspace.add_item(self.conn, dossier_id, item)
        report = hbworkspace.dossier_report(self.conn, dossier_id)
        page = hbworkspace.render_report_html(report)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;b&gt;title&lt;/b&gt;", page)
        data = json.loads(hbworkspace.render_report_json(report))
        self.assertEqual(data["dossier"]["name"], "Hostile <b>title</b>")

    def test_export_writes_each_format(self):
        dossier_id = self.build()
        for suffix in ("md", "html", "json"):
            target = Path(self.directory.name) / f"report.{suffix}"
            path, count = hbworkspace.export_report(self.conn, dossier_id, target)
            self.assertEqual(count, 2)
            self.assertTrue(path.read_text(encoding="utf-8").strip())
        with self.assertRaises(ValueError):
            hbworkspace.export_report(self.conn, dossier_id,
                                      Path(self.directory.name) / "report.pdf")

    def test_unknown_dossier_is_rejected(self):
        with self.assertRaises(ValueError):
            hbworkspace.dossier_report(self.conn, "nope")


class ClusteringTest(WorkspaceCase):
    def test_canonical_url_ignores_tracking_and_www(self):
        left = hbnscan.canonical_url("https://www.Example.com/a/?utm_source=x&b=2#top")
        right = hbnscan.canonical_url("http://example.com/a?b=2")
        self.assertEqual(left, right)

    def test_same_story_under_two_urls_shares_a_title_key(self):
        first = hbnscan.story_keys("Acme wins permit - Reuters", "https://a.test/1")
        second = hbnscan.story_keys("Acme wins permit | The Verge", "https://b.test/2")
        self.assertNotEqual(first[0], second[0])
        self.assertEqual(first[1], second[1])

    def test_adopt_joins_the_earliest_member_and_detach_splits(self):
        self.add_signal("Acme wins permit - Reuters", link="https://a.test/1")
        second = self.add_signal("Acme wins permit", link="https://b.test/2")
        # The scan pipeline calls this for every inserted row; a direct insert does not.
        cluster_key, title_key = hbnscan.story_keys("Acme wins permit", "https://b.test/2")
        hbnscan.adopt_cluster(self.conn, second, cluster_key, title_key)
        self.conn.commit()
        rows = self.conn.execute(
            "SELECT item_hash, cluster_key FROM seen_items ORDER BY rowid").fetchall()
        keys = {row[0]: row[1] for row in rows}
        self.assertEqual(len(set(keys.values())), 1, "syndicated copies should share a story")

        summary = hbnscan.cluster_summary(self.conn, next(iter(keys.values())))
        self.assertEqual(summary["count"], 2)
        self.assertEqual(len(summary["sources"]), 1)

        victim = rows[1][0]
        self.assertTrue(hbnscan.detach_from_cluster(self.conn, victim))
        detached = self.conn.execute(
            "SELECT cluster_key FROM seen_items WHERE item_hash = ?", (victim,)).fetchone()[0]
        self.assertEqual(detached, f"row:{victim}")

    def test_scan_merges_syndicated_copies_across_feeds(self):
        first = [{"title": "Acme wins permit - Reuters", "link": "https://a.test/1",
                  "description": "A ruling", "published": ""}]
        second = [{"title": "Acme wins permit", "link": "https://b.test/2",
                   "description": "Same story", "published": ""}]

        def fetch(url, etag, last_modified):
            return (first if "a.test" in url else second), "e", "l"

        budget = hbnscan._ScanBudget(0, 0, False)
        with mock.patch.object(hbnscan, "fetch_feed", side_effect=fetch), \
             mock.patch.object(hbnscan, "deepseek_configured", return_value=False), \
             mock.patch.object(hbnscan, "_is_trusted_source", return_value=False):
            hbnscan._scan_feed(self.conn, "https://a.test/rss", None, None, budget, None,
                               lambda _line: None)
            hbnscan._scan_feed(self.conn, "https://b.test/rss", None, None, budget, None,
                               lambda _line: None)

        keys = {row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items")}
        self.assertEqual(len(keys), 1)


class SourceHealthTest(WorkspaceCase):
    def test_statuses_and_coverage(self):
        feeds = ["https://ok.test/rss", "https://bad.test/rss", "https://old.test/rss",
                 "https://new.test/rss"]
        hbnscan.record_feed_result(self.conn, "https://ok.test/rss", True, 200, "", 3)
        hbnscan.record_feed_result(self.conn, "https://bad.test/rss", False, 403, "forbidden")
        hbnscan.record_feed_result(self.conn, "https://old.test/rss", True, 200, "", 0)
        self.conn.execute(
            "UPDATE feed_state SET last_success_at = ? WHERE feed_url = ?",
            ((datetime.now() - timedelta(days=10)).isoformat(timespec="seconds"),
             "https://old.test/rss"))
        self.conn.commit()

        health = {entry["url"]: entry for entry in hbworkspace.source_health(self.conn, feeds)}
        self.assertEqual(health["https://ok.test/rss"]["status"], "ok")
        self.assertEqual(health["https://bad.test/rss"]["status"], "failing")
        self.assertEqual(health["https://bad.test/rss"]["http_status"], 403)
        self.assertEqual(health["https://old.test/rss"]["status"], "stale")
        self.assertEqual(health["https://new.test/rss"]["status"], "never")

        summary = hbworkspace.coverage_summary(self.conn, feeds)
        self.assertEqual(summary["sources"]["total"], 4)
        self.assertEqual(summary["sources"]["failing"], 1)
        self.assertEqual(summary["sources"]["stale"], 1)
        self.assertIn("1 failing", hbworkspace.describe_coverage(summary))

    def test_failure_keeps_the_last_success(self):
        hbnscan.record_feed_result(self.conn, "https://f.test/rss", True, 200, "", 1)
        before = self.conn.execute(
            "SELECT last_success_at FROM feed_state").fetchone()[0]
        hbnscan.record_feed_result(self.conn, "https://f.test/rss", False, 500, "boom")
        row = self.conn.execute(
            "SELECT last_success_at, failures FROM feed_state").fetchone()
        self.assertEqual(row[0], before)
        self.assertEqual(row[1], 1)


class NotesTest(WorkspaceCase):
    def test_add_list_delete(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme")
        note_id = hbworkspace.add_note(self.conn, "Check the filing", dossier_id=dossier_id)
        self.assertEqual(len(hbworkspace.list_notes(self.conn, dossier_id)), 1)
        with self.assertRaises(ValueError):
            hbworkspace.add_note(self.conn, "   ")
        self.assertTrue(hbworkspace.delete_note(self.conn, note_id))
        self.assertEqual(hbworkspace.list_notes(self.conn, dossier_id), [])


if __name__ == "__main__":
    unittest.main()
