"""Starter feeds: a fresh install must produce a useful first scan, not an empty one.

Every URL shipped in a profile is exercised against the live network once, by hand, with the
engine's own parser; these tests pin the behaviour around it (what gets written, what is left
alone, what happens when you switch profile) so it cannot regress silently.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan


class PackTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.assets = self.root / "assets"
        self.home = self.root / "state"
        self.assets.mkdir()
        for name in ("watchlist.txt.example", "topics.txt.example", ".env.example"):
            (self.assets / name).write_text("# example\n", encoding="utf-8")
        (self.assets / "feeds.txt.example").write_text(
            "# curated example\nhttps://example.test/curated.xml\n", encoding="utf-8")
        self.profiles = self.assets / "profiles"
        self.profiles.mkdir()
        for name, feeds in (("cyber", ["https://cisa.test/all.xml", "https://krebs.test/feed/"]),
                            ("energy", ["https://eia.test/rss", "https://utility.test/feeds/"])):
            (self.profiles / f"{name}.json").write_text(json.dumps({
                "name": name.title(), "starter_feeds": feeds}), encoding="utf-8")
        self.apply_paths()

    def apply_paths(self, feeds=None):
        values = {
            "ASSET_DIR": self.assets,
            "HOME_DIR": self.home,
            "PROFILES_DIR": self.home / "profiles",
            "FEEDS_PATH": self.home / "feeds.txt",
            "WATCHLIST_PATH": self.home / "watchlist.txt",
            "TOPICS_PATH": self.home / "topics.txt",
            "SETTINGS_PATH": self.home / "settings.json",
            "ENV_PATH": self.home / ".env",
            "REPORT_PATH": self.home / "report.md",
            "DB_PATH": self.home / "state.db",
        }
        for name, value in values.items():
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def use_profile(self, name):
        patch = mock.patch.object(
            hbnscan, "current_profile",
            return_value=hbnscan.Profile.from_mapping(
                json.loads((self.profiles / f"{name}.json").read_text(encoding="utf-8"))))
        patch.start()
        self.addCleanup(patch.stop)

    def lines(self):
        return [line for line in self.FEEDS_PATH.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")]

    @property
    def FEEDS_PATH(self):
        return self.home / "feeds.txt"

    def test_a_fresh_install_gets_the_profile_pack(self):
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.assertEqual(self.lines(), ["https://cisa.test/all.xml", "https://krebs.test/feed/"])
        self.assertIn("'Cyber' profile", self.FEEDS_PATH.read_text(encoding="utf-8"))

    def test_the_curated_example_is_not_used_when_a_pack_exists(self):
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.assertNotIn("curated.xml", self.FEEDS_PATH.read_text(encoding="utf-8"))

    def test_without_a_pack_the_example_still_seeds(self):
        self.use_profile("cyber")
        with mock.patch.object(hbnscan, "starter_feeds", return_value=()):
            hbnscan.bootstrap_home()
        self.assertEqual(self.lines(), ["https://example.test/curated.xml"])

    def test_an_existing_feed_list_is_never_overwritten(self):
        self.home.mkdir()
        self.FEEDS_PATH.write_text("https://mine.test/rss\n", encoding="utf-8")
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.assertEqual(self.lines(), ["https://mine.test/rss"])

    def test_an_empty_feed_list_is_filled_in(self):
        self.home.mkdir()
        self.FEEDS_PATH.write_text("# everything commented out\n", encoding="utf-8")
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.assertEqual(self.lines(), ["https://cisa.test/all.xml", "https://krebs.test/feed/"])

    def test_switching_profile_appends_only_the_new_feeds(self):
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.use_profile("energy")
        added = hbnscan.seed_starter_feeds()
        self.assertEqual(added, ["https://eia.test/rss", "https://utility.test/feeds/"])
        self.assertEqual(len(self.lines()), 4, "the previous profile's feeds stay")

    def test_a_commented_feed_counts_as_present(self):
        self.home.mkdir()
        self.FEEDS_PATH.write_text("# https://cisa.test/all.xml\n", encoding="utf-8")
        self.use_profile("cyber")
        added = hbnscan.seed_starter_feeds()
        self.assertNotIn("https://cisa.test/all.xml", added)
        self.assertIn("https://krebs.test/feed/", added)

    def test_seeding_twice_adds_nothing(self):
        self.use_profile("cyber")
        hbnscan.bootstrap_home()
        self.assertEqual(hbnscan.seed_starter_feeds(), [])

    def test_the_default_profile_ships_a_pack(self):
        pack = hbnscan.starter_feeds(hbnscan.Profile.from_mapping(hbnscan.DEFAULT_PROFILE))
        self.assertGreaterEqual(len(pack), 3)
        for url in pack:
            self.assertTrue(url.startswith(("http://", "https://")), url)


class ShippedProfilesTest(unittest.TestCase):
    """Guards the packs that ship in profiles/: one bad URL is a broken first impression."""

    def test_every_shipped_profile_has_a_usable_pack(self):
        folder = Path(__file__).resolve().parents[1] / "assets" / "profiles"
        checked = 0
        for path in sorted(folder.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if "starter_feeds" not in data:
                continue                      # a user's own profile inherits the default
            checked += 1
            feeds = data["starter_feeds"]
            self.assertGreaterEqual(len(feeds), 3, f"{path.name} ships only {len(feeds)} feeds")
            for url in feeds:
                self.assertTrue(url.startswith(("http://", "https://")), f"{path.name}: {url}")
            self.assertEqual(len(feeds), len(set(feeds)), f"{path.name} repeats a URL")
        self.assertEqual(checked, 10, "expected ten shipped profiles with starter packs")

    def test_the_packs_are_not_all_the_same(self):
        folder = Path(__file__).resolve().parents[1] / "assets" / "profiles"
        packs = {}
        for path in sorted(folder.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("starter_feeds"):
                packs[path.stem] = tuple(data["starter_feeds"])
        self.assertEqual(len(set(packs.values())), len(packs),
                         "each profile should recommend sources for its own subject")


class CliTest(unittest.TestCase):
    def test_the_flag_is_idempotent(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        import contextlib
        import io
        buffer = io.StringIO()

        def run():
            # cli_main exits the way the real CLI does; the code is what matters here.
            try:
                return hbnscan.cli_main(["--seed-feeds"])
            except SystemExit as exc:
                return exc.code

        with mock.patch.object(hbnscan, "HOME_DIR", root), \
             mock.patch.object(hbnscan, "FEEDS_PATH", root / "feeds.txt"), \
             mock.patch.object(hbnscan, "DB_PATH", root / "state.db"), \
             mock.patch.object(hbnscan, "SETTINGS_PATH", root / "settings.json"):
            with contextlib.redirect_stdout(buffer):
                first = run()
                second = run()
        self.assertEqual((first, second), (0, 0))
        # bootstrap_home() seeds the pack on the first call, so the flag itself has nothing
        # left to add; running it again must stay silent and stay idempotent.
        output = buffer.getvalue()
        self.assertIn("already in feeds.txt", output)
        url_lines = [line for line in (root / "feeds.txt").read_text(
            encoding="utf-8").splitlines() if line.startswith("https://")]
        self.assertEqual(url_lines, list(hbnscan.starter_feeds()))


if __name__ == "__main__":
    unittest.main()
