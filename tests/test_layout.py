"""An installed copy runs from a read-only code directory and writes to a state directory.

These tests cover the split between bundled assets (read-only) and user state (writable), which
is what a .deb, an AppImage or a frozen Windows build relies on.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan


class InstalledLayoutTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        self.assets = root / "assets"
        self.home = root / "state"
        (self.assets / "profiles").mkdir(parents=True)
        for name in ("feeds.txt.example", "watchlist.txt.example", "topics.txt.example"):
            (self.assets / name).write_text("# example\n", encoding="utf-8")
        (self.assets / ".env.example").write_text("LLM_API_KEY=\n", encoding="utf-8")
        (self.assets / "profiles" / "bundled.json").write_text(
            json.dumps({"name": "Bundled"}), encoding="utf-8")
        for name, value in (("ASSET_DIR", self.assets),
                            ("HOME_DIR", self.home),
                            ("PROFILES_DIR", self.home / "profiles"),
                            ("FEEDS_PATH", self.home / "feeds.txt"),
                            ("WATCHLIST_PATH", self.home / "watchlist.txt"),
                            ("TOPICS_PATH", self.home / "topics.txt"),
                            ("SETTINGS_PATH", self.home / "settings.json"),
                            ("ENV_PATH", self.home / ".env"),
                            ("REPORT_PATH", self.home / "report.md"),
                            ("DB_PATH", self.home / "state.db")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_bootstrap_seeds_the_state_directory_from_the_bundle(self):
        self.assertFalse(self.home.exists())
        created = hbnscan.bootstrap_home()
        self.assertEqual(sorted(created),
                         [".env", "feeds.txt", "profiles/", "topics.txt", "watchlist.txt"])
        self.assertTrue((self.home / "feeds.txt").is_file())
        self.assertTrue((self.home / ".env").is_file(), "the .env template must be seeded too")
        self.assertTrue((self.home / "profiles" / "bundled.json").is_file())

    def test_bootstrap_never_overwrites_user_edits(self):
        hbnscan.bootstrap_home()
        (self.home / "feeds.txt").write_text("https://mine.test/rss\n", encoding="utf-8")
        self.assertEqual(hbnscan.bootstrap_home(), [])
        self.assertEqual((self.home / "feeds.txt").read_text(encoding="utf-8"),
                         "https://mine.test/rss\n")

    def test_user_profiles_shadow_bundled_ones(self):
        hbnscan.bootstrap_home()
        (self.home / "profiles" / "bundled.json").write_text(
            json.dumps({"name": "Mine"}), encoding="utf-8")
        (self.home / "profiles" / "extra.json").write_text(
            json.dumps({"name": "Extra"}), encoding="utf-8")
        names = [name for name, _path in hbnscan.available_profiles()]
        self.assertEqual(names, ["built-in default", "bundled", "extra"])
        resolved = hbnscan.resolve_profile_path("bundled")
        self.assertEqual(resolved, self.home / "profiles" / "bundled.json",
                         "a user profile must win over the bundled copy of the same name")

    def test_profile_names_still_resolve_from_the_bundle_before_first_save(self):
        resolved = hbnscan.resolve_profile_path("bundled")
        self.assertEqual(resolved, self.assets / "profiles" / "bundled.json")

    def test_connecting_creates_a_missing_state_directory(self):
        conn = hbnscan.connect(self.home / "nested" / "state.db")
        self.addCleanup(conn.close)
        self.assertTrue((self.home / "nested" / "state.db").is_file())
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0],
                         hbnscan.SCHEMA_VERSION)

    def test_connections_are_configured_for_concurrent_readers(self):
        conn = hbnscan.connect(self.home / "state.db")
        self.addCleanup(conn.close)
        settings = hbnscan.connection_settings(conn)
        self.assertEqual(settings["journal_mode"], "wal")
        self.assertGreaterEqual(settings["busy_timeout"], 5000)

    def test_a_writer_and_a_reader_can_share_the_database(self):
        writer = hbnscan.connect(self.home / "state.db")
        self.addCleanup(writer.close)
        reader = hbnscan.connect(self.home / "state.db")
        self.addCleanup(reader.close)
        writer.execute(
            "INSERT INTO seen_items (item_hash, title, link, flagged, summary, seen_at, "
            "category, severity, dismissed) VALUES ('h1', 't', 'https://x.test/1', 0, '', "
            "'2026-01-01T00:00:00', 'policy', 'low', 0)")
        writer.commit()
        self.assertEqual(reader.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0], 1)

    def test_the_home_override_wins_over_the_checkout(self):
        with mock.patch.dict("os.environ", {"HB_BRIEFING_HOME": str(self.home)}, clear=True):
            self.assertEqual(hbnscan.default_home(), self.home)
        # The legacy name must work on its own, so the newer variable is cleared here: a host
        # shell that exports HB_BRIEFING_HOME must not decide what this assertion proves.
        with mock.patch.dict("os.environ", {"WATCHDOG_HOME": str(self.home)}, clear=True):
            self.assertEqual(hbnscan.default_home(), self.home)

    def test_the_current_name_outranks_the_legacy_one(self):
        with mock.patch.dict("os.environ", {"HB_BRIEFING_HOME": str(self.home),
                                            "WATCHDOG_HOME": str(self.home / "legacy")},
                             clear=True):
            self.assertEqual(hbnscan.default_home(), self.home)

    def test_a_source_checkout_keeps_state_beside_the_code(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(hbnscan.default_home(), hbnscan.SOURCE_ROOT)


if __name__ == "__main__":
    unittest.main()
