import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import hbnscan


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "state.db"
        patch = mock.patch.object(hbnscan, "DB_PATH", self.db_path)
        patch.start()
        self.addCleanup(patch.stop)

    def connect(self):
        conn = hbnscan.connect()
        self.addCleanup(conn.close)
        return conn

    def add_signal(self, conn, title, *, published="", severity="high", layer="heuristic",
                   summary=None, seen_at="2026-11-13T10:00:00", flagged=1, dismissed=0):
        conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason) "
            "VALUES (?, ?, ?, 'https://feed.test/rss', ?, ?, ?, 'policy', ?, '{}', '', '[]', "
            "NULL, 0, ?, ?, ?, '')",
            (hbnscan.item_hash(title, title), title, "https://example.test/" + title,
             flagged, summary if summary is not None else "[Local Heuristic] Matched ruling.",
             seen_at, severity, dismissed, published, layer),
        )
        conn.commit()

class MigrationTest(DatabaseCase):
    def test_connect_upgrades_a_legacy_database_and_backs_it_up(self):
        legacy = sqlite3.connect(str(self.db_path))
        legacy.execute("CREATE TABLE seen_items (item_hash TEXT PRIMARY KEY, title TEXT, "
                       "link TEXT, feed_url TEXT, flagged INTEGER DEFAULT 0, summary TEXT, "
                       "seen_at TEXT)")
        legacy.execute("INSERT INTO seen_items (item_hash, title, flagged, summary) "
                       "VALUES ('a', 'Acme', 1, '[Watchlist] acme → policy/high.')")
        legacy.execute("INSERT INTO seen_items (item_hash, title, flagged, summary) "
                       "VALUES ('b', 'Beta', 0, '[Rejected by deep read] No.')")
        legacy.commit()
        legacy.close()

        conn = self.connect()
        layers = dict(conn.execute("SELECT item_hash, source_layer FROM seen_items"))
        self.assertEqual(layers, {"a": "watchlist", "b": "model"})
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         hbnscan.SCHEMA_VERSION)
        backups = list(Path(self.directory.name).glob("state-backup-*.db"))
        self.assertEqual(len(backups), 1, "a database with data is backed up before migrating")

    def test_migration_is_idempotent(self):
        conn = self.connect()
        self.add_signal(conn, "Acme")
        before = conn.execute("SELECT source_layer FROM seen_items").fetchall()
        hbnscan.migrate(conn)
        after = conn.execute("SELECT source_layer FROM seen_items").fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])
        self.assertEqual(len(list(Path(self.directory.name).glob("state-backup-*.db"))), 0)

    def test_backup_database_is_a_complete_copy(self):
        conn = self.connect()
        self.add_signal(conn, "Acme")
        target = hbnscan.backup_database(Path(self.directory.name) / "copy.db")
        copy = sqlite3.connect(str(target))
        try:
            self.assertEqual(copy.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0], 1)
        finally:
            copy.close()


class RankingTest(DatabaseCase):
    def test_newest_means_newest_news_not_newest_ingest(self):
        conn = self.connect()
        now = datetime.now()
        self.add_signal(conn, "old-news", severity="low",
                        published=(now - timedelta(days=30)).isoformat(timespec="seconds"),
                        seen_at=now.isoformat(timespec="seconds"))
        self.add_signal(conn, "fresh-news", severity="low",
                        published=(now - timedelta(hours=2)).isoformat(timespec="seconds"),
                        seen_at=(now - timedelta(hours=1)).isoformat(timespec="seconds"))

        rows, total = hbnscan.select_signals(conn, order="newest")
        self.assertEqual(total, 2)
        self.assertEqual([row["title"] for row in rows], ["fresh-news", "old-news"])

        rows, _ = hbnscan.select_signals(conn, order="detected")
        self.assertEqual([row["title"] for row in rows], ["old-news", "fresh-news"])

    def test_priority_still_leads_with_severity(self):
        conn = self.connect()
        now = datetime.now()
        self.add_signal(conn, "today-low", severity="low",
                        published=now.isoformat(timespec="seconds"))
        self.add_signal(conn, "older-high", severity="high",
                        published=(now - timedelta(days=3)).isoformat(timespec="seconds"))
        rows, _ = hbnscan.select_signals(conn, order="priority")
        self.assertEqual([row["title"] for row in rows], ["older-high", "today-low"])

    def test_undated_rows_are_labelled_not_guessed(self):
        self.assertEqual(hbnscan.freshness_label(""), "undated")
        self.assertEqual(hbnscan.freshness_label("not a date"), "undated")
        self.assertEqual(hbnscan.freshness_label(
            datetime.now().isoformat(timespec="seconds")), "today")
        self.assertEqual(hbnscan.published_age_days(""), None)


class ProvenanceTest(DatabaseCase):
    def test_priority_and_group_read_the_layer_column(self):
        conn = self.connect()
        # A syndicated copy loses the leading prefix, which used to lose its priority too.
        self.add_signal(conn, "acme", layer="watchlist",
                        summary="[Deduped] Same story already triaged this scan. "
                                "[Watchlist] acme → policy/high.")
        rows, total = hbnscan.select_signals(conn, {"group": "watchlist"})
        self.assertEqual(total, 1)
        self.assertEqual(hbnscan.priority_of(rows[0]["source_layer"], rows[0]["category"],
                                             rows[0]["summary"]), "watchlist")

    def test_dismissed_rows_leave_every_read_path(self):
        conn = self.connect()
        self.add_signal(conn, "acme", layer="watchlist")
        self.assertNotEqual(conn.execute(
            f"SELECT COUNT(*) FROM seen_items WHERE {hbnscan.VISIBLE_SQL}").fetchone()[0], 0)
        hbnscan.dismiss_item(conn, hbnscan.item_hash("acme", "acme"))
        rows, total = hbnscan.select_signals(conn)
        self.assertEqual((len(rows), total), (0, 0))

    def test_verdict_row_records_layer_and_reason(self):
        row = hbnscan._verdict_row(
            {"title": "Acme", "link": "https://e.test/a", "published": ""},
            "https://feed.test/rss",
            {"flagged": 1, "category": "policy", "severity": "high", "analysis": "x",
             "action": "", "entities": {}, "facts": [], "confidence": 0.0, "deep_read": 0,
             "dismissed": 0, "layer": "topic", "reason": "topic: broadband"},
        )
        # item_hash..verdict_reason are positions 0-17; the story keys follow.
        self.assertEqual(row[16], "topic")
        self.assertEqual(row[17], "topic: broadband")
        self.assertTrue(row[18].startswith(("u:", "t:")))
        self.assertTrue(row[19].startswith("t:"))

    def test_export_rows_expose_freshness_and_clean_text(self):
        conn = self.connect()
        self.add_signal(conn, "acme", published="")
        rows = hbnscan.export_rows(conn)
        self.assertEqual(rows[0]["freshness"], "undated")
        self.assertNotIn("[Local Heuristic]", rows[0]["analysis"])

    def test_csv_header_and_rows_have_the_same_width(self):
        conn = self.connect()
        self.add_signal(conn, "acme")
        text = hbnscan.render_csv(hbnscan.export_rows(conn))
        widths = {len(line.split(",")) for line in text.splitlines()}
        self.assertEqual(widths, {len(hbnscan.EXPORT_COLUMNS)})


class FilterTest(DatabaseCase):
    def test_recent_days_and_undated_filters(self):
        conn = self.connect()
        now = datetime.now()
        self.add_signal(conn, "fresh", published=now.isoformat(timespec="seconds"))
        self.add_signal(conn, "old",
                        published=(now - timedelta(days=40)).isoformat(timespec="seconds"))
        self.add_signal(conn, "undated")

        self.assertEqual(hbnscan.select_signals(conn, {"recent_days": 2})[1], 1)
        self.assertEqual(hbnscan.select_signals(conn, {"recent_days": "7d"})[1], 1)
        self.assertEqual(hbnscan.select_signals(conn, {"recent_days": 60})[1], 2)
        self.assertEqual(hbnscan.select_signals(conn, {"undated": True})[1], 1)
        self.assertEqual(hbnscan.setting_choices("filter_recent"),
                         ("all", "1d", "3d", "7d", "30d"))

    def test_search_reaches_the_assessment_and_action(self):
        conn = self.connect()
        self.add_signal(conn, "acme", summary="[Local Heuristic] Matched merger.")
        _rows, total = hbnscan.select_signals(conn, {"query": "merger"})
        self.assertEqual(total, 1)


class ColumnWidthTest(unittest.TestCase):
    def test_valid_widths_are_kept_and_clamped(self):
        stored = hbnscan._coerce_setting(
            "column_widths", {"title": "300", "analysis": 9999, "bad": "x", "": 100}, "")
        self.assertEqual(json.loads(stored), {"title": 300})

    def test_garbage_falls_back_and_out_of_range_values_are_dropped(self):
        self.assertEqual(hbnscan._coerce_setting("column_widths", "not json", ""), "")
        # Below the 30 px floor, so there is nothing usable left and the default applies.
        self.assertEqual(hbnscan._coerce_setting("column_widths", {"time": 5}, ""), "")

    def test_save_can_clear_the_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            with mock.patch.object(hbnscan, "SETTINGS_PATH", path):
                hbnscan.save_settings(column_widths={"title": 350})
                self.assertEqual(json.loads(hbnscan.load_settings()["column_widths"]),
                                 {"title": 350})
                hbnscan.save_settings(column_widths="")
                self.assertEqual(hbnscan.load_settings()["column_widths"], "")


class PagingTest(DatabaseCase):
    def test_select_signals_pages_and_counts_the_whole_filter(self):
        conn = self.connect()
        for index in range(25):
            self.add_signal(conn, f"story-{index:02d}")
        first, total = hbnscan.select_signals(conn, limit=10, offset=0)
        last, total_again = hbnscan.select_signals(conn, limit=10, offset=20)
        self.assertEqual(total, 25)
        self.assertEqual(total_again, 25)
        self.assertEqual((len(first), len(last)), (10, 5))
        self.assertFalse({r["item_hash"] for r in first} & {r["item_hash"] for r in last})


class ScanPipelineTest(DatabaseCase):
    def run_offline_scan(self, items):
        settings = dict(hbnscan.DEFAULT_SETTINGS, auto_scan=False)
        patches = (
            mock.patch.object(hbnscan, "load_feeds", return_value=["https://feed.test/rss"]),
            mock.patch.object(hbnscan, "fetch_feed",
                              return_value=(items, "etag", "last-modified")),
            mock.patch.object(hbnscan, "load_watchlist", return_value=[]),
            mock.patch.object(hbnscan, "watchlist_warnings", return_value=[]),
            mock.patch.object(hbnscan, "load_topics", return_value=[]),
            mock.patch.object(hbnscan, "topic_warnings", return_value=[]),
            mock.patch.object(hbnscan, "load_settings", return_value=settings),
            mock.patch.object(hbnscan, "deepseek_configured", return_value=False),
            mock.patch.object(hbnscan, "build_briefing", return_value=None),
            mock.patch.object(hbnscan, "write_report"),
            mock.patch.object(hbnscan, "send_notifications"),
            mock.patch.object(hbnscan, "_is_trusted_source", return_value=False),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return hbnscan._run_scan(lambda _line: None)

    def test_offline_scan_stores_provenance_and_records_the_run(self):
        items = [{"title": "Court rules on Acme", "link": "https://e.test/a",
                  "description": "A ruling was issued", "published": ""}]
        new, flagged = self.run_offline_scan(items)
        self.assertEqual(new, 1)
        conn = self.connect()
        row = conn.execute("SELECT source_layer, verdict_reason, flagged FROM seen_items").fetchone()
        self.assertEqual(row["source_layer"], "heuristic")
        self.assertTrue(row["verdict_reason"])
        run = conn.execute("SELECT ai_calls, new_items, deep_reads FROM scan_runs").fetchone()
        self.assertEqual(tuple(run), (0, 1, 0), "offline scans are part of the history")

    def test_a_failed_report_does_not_lose_the_scan(self):
        items = [{"title": "Court rules on Acme", "link": "https://e.test/a",
                  "description": "A ruling was issued", "published": ""}]
        settings = dict(hbnscan.DEFAULT_SETTINGS, auto_scan=False)
        patches = (
            mock.patch.object(hbnscan, "load_feeds", return_value=["https://feed.test/rss"]),
            mock.patch.object(hbnscan, "fetch_feed",
                              return_value=(items, "etag", "last-modified")),
            mock.patch.object(hbnscan, "load_watchlist", return_value=[]),
            mock.patch.object(hbnscan, "watchlist_warnings", return_value=[]),
            mock.patch.object(hbnscan, "load_topics", return_value=[]),
            mock.patch.object(hbnscan, "topic_warnings", return_value=[]),
            mock.patch.object(hbnscan, "load_settings", return_value=settings),
            mock.patch.object(hbnscan, "deepseek_configured", return_value=False),
            mock.patch.object(hbnscan, "build_briefing", return_value=None),
            mock.patch.object(hbnscan, "write_report", side_effect=OSError("disk full")),
            mock.patch.object(hbnscan, "send_notifications"),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        lines = []
        new, _flagged = hbnscan._run_scan(lines.append)
        self.assertEqual(new, 1)
        conn = self.connect()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 1)
        self.assertTrue(any("Report could not be written" in line for line in lines))

    def test_second_scan_does_not_reassess_the_same_item(self):
        items = [{"title": "Court rules on Acme", "link": "https://e.test/a",
                  "description": "A ruling was issued", "published": ""}]
        self.run_offline_scan(items)
        new, _flagged = self.run_offline_scan(items)
        self.assertEqual(new, 0)


class ProfileTest(unittest.TestCase):
    def test_profile_keeps_mapping_style_access(self):
        profile = hbnscan.Profile.from_mapping(hbnscan.DEFAULT_PROFILE)
        self.assertEqual(profile["name"], profile.name)
        self.assertEqual(profile.get("missing", "fallback"), "fallback")
        with self.assertRaises(KeyError):
            profile["missing"]

    def test_scoring_accepts_an_explicit_profile(self):
        data = dict(hbnscan.DEFAULT_PROFILE)
        data["name"] = "Test profile"
        data["strong_signals"] = ["zebra"]
        data["weak_signals"] = []
        data["core_categories"] = []
        data["category_hints"] = []
        data["trusted_source_hosts"] = []
        profile = hbnscan.Profile.from_mapping(data)

        verdict = hbnscan.heuristic_verdict("A zebra crossing", "", profile=profile)
        self.assertEqual(verdict["flagged"], 1)
        self.assertEqual(verdict["layer"], "heuristic")

        untouched = hbnscan.heuristic_verdict("A ruling was issued", "", profile=profile)
        self.assertEqual(untouched["flagged"], 0)


if __name__ == "__main__":
    unittest.main()
