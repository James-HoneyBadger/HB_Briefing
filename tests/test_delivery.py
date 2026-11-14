import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        db_patch = mock.patch.object(hbnscan, "DB_PATH", Path(self.directory.name) / "test.db")
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)
        self.settings = dict(hbnscan.DEFAULT_SETTINGS, notify_enabled=True, notify_max_items=1)

    def add_signal(self, item_hash, seen_at):
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, flagged, severity, category, "
            "summary, seen_at) VALUES (?, ?, ?, 1, 'high', 'other', '', ?)",
            (item_hash, item_hash, "https://example.test/" + item_hash, seen_at),
        )
        self.conn.commit()

    def test_notification_batches_deliver_each_item_once(self):
        self.add_signal("first", "2026-01-01T10:00:00")
        self.add_signal("second", "2026-01-01T10:00:00")
        sent = []

        def capture(_config, _subject, body):
            sent.append(body)

        with mock.patch.object(hbnscan, "notification_config", return_value={"recipients": ["test"]}), \
             mock.patch.object(hbnscan, "notifications_configured", return_value=True), \
             mock.patch.object(hbnscan, "_smtp_send", side_effect=capture):
            self.assertEqual(hbnscan.send_notifications(self.conn, self.settings, lambda _: None), 1)
            self.assertEqual(hbnscan.send_notifications(self.conn, self.settings, lambda _: None), 1)
            self.assertEqual(hbnscan.send_notifications(self.conn, self.settings, lambda _: None), 0)

        self.assertEqual(sum("first" in body for body in sent), 1)
        self.assertEqual(sum("second" in body for body in sent), 1)

    def test_failed_notification_remains_pending(self):
        self.add_signal("first", "2026-01-01T10:00:00")
        with mock.patch.object(hbnscan, "notification_config", return_value={"recipients": ["test"]}), \
             mock.patch.object(hbnscan, "notifications_configured", return_value=True), \
             mock.patch.object(hbnscan, "_smtp_send", side_effect=[OSError("offline"), None]):
            self.assertEqual(hbnscan.send_notifications(self.conn, self.settings, lambda _: None), 0)
            self.assertEqual(hbnscan.send_notifications(self.conn, self.settings, lambda _: None), 1)

    def test_declared_interest_toggle_covers_a_deduped_copy(self):
        self.add_signal("first", "2026-01-01T10:00:00")
        self.conn.execute(
            "UPDATE seen_items SET summary = ?, source_layer = 'watchlist' WHERE item_hash = ?",
            ("[Deduped] Same story already triaged this scan. [Watchlist] acme → policy/high.",
             "first"))
        self.conn.commit()
        settings = dict(self.settings, notify_include_watchlist=False)
        self.assertEqual(hbnscan._pending_alerts(self.conn, settings), [])

    def test_legacy_watermark_does_not_resend_old_items(self):
        self.add_signal("old", "2026-01-01T10:00:00")
        self.add_signal("new", "2026-01-01T11:00:00")
        self.conn.execute("INSERT INTO meta (key, value) VALUES ('last_notified_at', ?)",
                          ("2026-01-01T10:30:00",))
        self.conn.commit()
        self.assertEqual([item["title"] for item in hbnscan._pending_alerts(self.conn, self.settings)],
                         ["new"])

    def test_report_uses_current_scan_metadata(self):
        self.conn.execute("INSERT INTO meta (key, value) VALUES ('last_scan_at', ?)",
                          ("2000-01-01T00:00:00",))
        self.conn.commit()
        report_path = Path(self.directory.name) / "report.md"
        with mock.patch.object(hbnscan, "REPORT_PATH", report_path), \
             mock.patch.object(hbnscan, "load_feeds", return_value=[]), \
             mock.patch.object(hbnscan, "load_watchlist", return_value=[]), \
             mock.patch.object(hbnscan, "watchlist_warnings", return_value=[]), \
             mock.patch.object(hbnscan, "load_topics", return_value=[]), \
             mock.patch.object(hbnscan, "topic_warnings", return_value=[]), \
             mock.patch.object(hbnscan, "load_settings", return_value=self.settings), \
             mock.patch.object(hbnscan, "build_briefing", return_value=None), \
             mock.patch.object(hbnscan, "send_notifications") as notify:
            hbnscan._run_scan(lambda _: None)

        notify.assert_called_once()
        metadata = dict(self.conn.execute("SELECT key, value FROM meta"))
        report = report_path.read_text(encoding="utf-8")
        self.assertEqual(metadata["prev_scan_at"], "2000-01-01T00:00:00")
        self.assertIn("Last scan: " + metadata["last_scan_at"].replace("T", " "), report)


if __name__ == "__main__":
    unittest.main()
