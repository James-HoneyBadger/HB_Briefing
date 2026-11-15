"""First-run experience: which mode is grading, and does the tool say so?

A new user cannot tell an empty verdict list from a broken install. These tests pin the single
helper every front end reads, the settings flag that stops the notice nagging, and the promise
that the notice never appears once it has been dismissed.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan


class GradingModeTest(unittest.TestCase):
    def test_no_key_is_offline(self):
        self.assertEqual(hbnscan.grading_mode(api_key=""), "offline")
        self.assertIn("offline", hbnscan.describe_grading_mode(api_key=""))

    def test_a_placeholder_key_is_still_offline(self):
        self.assertEqual(hbnscan.grading_mode(api_key="your_deepseek_api_key_here"), "offline")

    def test_a_real_key_is_model_mode_and_names_the_model(self):
        self.assertEqual(hbnscan.grading_mode(api_key="sk-abcdefghijklmnop"), "model")
        self.assertIn(hbnscan.LLM_MODEL, hbnscan.describe_grading_mode(api_key="sk-abcdefghijklmnop"))

    def test_the_phrase_explains_what_a_key_adds(self):
        text = hbnscan.describe_grading_mode(api_key="")
        self.assertIn("deep read", text)
        self.assertIn("briefing", text)


class FirstRunFlagTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, value in (("SETTINGS_PATH", self.root / "settings.json"),
                            ("DB_PATH", self.root / "state.db")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_fresh_install_has_not_seen_the_notice(self):
        self.assertFalse(hbnscan.load_settings()["first_run_done"])

    def test_the_flag_round_trips(self):
        hbnscan.save_settings(first_run_done=True)
        self.assertTrue(hbnscan.load_settings()["first_run_done"])
        hbnscan.save_settings(first_run_done=False)
        self.assertFalse(hbnscan.load_settings()["first_run_done"])

    def test_the_flag_is_coerced_from_a_hand_edit(self):
        (self.root / "settings.json").write_text(json.dumps({"first_run_done": "yes"}),
                                                 encoding="utf-8")
        self.assertTrue(hbnscan.load_settings()["first_run_done"])


class ScanBannerTest(unittest.TestCase):
    def test_a_scan_states_the_mode_in_its_first_lines(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        lines = []
        with mock.patch.object(hbnscan, "DB_PATH", root / "state.db"), \
             mock.patch.object(hbnscan, "SETTINGS_PATH", root / "settings.json"), \
             mock.patch.object(hbnscan, "FEEDS_PATH", root / "feeds.txt"), \
             mock.patch.object(hbnscan, "REPORT_PATH", root / "report.md"), \
             mock.patch.object(hbnscan, "load_feeds", return_value=[]), \
             mock.patch.object(hbnscan, "load_topics", return_value=[]), \
             mock.patch.object(hbnscan, "load_watchlist", return_value=[]), \
             mock.patch.object(hbnscan, "current_profile",
                               return_value=hbnscan.Profile.from_mapping(
                                   hbnscan.DEFAULT_PROFILE)):
            hbnscan.run_scan(log_callback=lines.append)
        banner = "\n".join(lines[:3])
        self.assertIn("grading:", banner, banner)
        self.assertRegex(banner, r"grading: (offline heuristic|model )")


if __name__ == "__main__":
    unittest.main()
