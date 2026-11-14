import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbconfig
import hbnscan


class ConfigCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.paths = {
            "FEEDS_PATH": root / "feeds.txt",
            "TOPICS_PATH": root / "topics.txt",
            "WATCHLIST_PATH": root / "watchlist.txt",
            "SETTINGS_PATH": root / "settings.json",
            "REPORT_PATH": root / "report.md",
            "DB_PATH": root / "state.db",
            "ENV_PATH": root / ".env",
            "APP_DIR": root,
            "PROFILE_PATH": root / "profile.json",
        }
        for name, value in self.paths.items():
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        (root / "profiles").mkdir()
        self.profile_patch = mock.patch.object(
            hbnscan, "current_profile",
            return_value=hbnscan.Profile.from_mapping(hbnscan.DEFAULT_PROFILE))
        self.profile_patch.start()
        self.addCleanup(self.profile_patch.stop)

    def write(self, name, text):
        Path(self.paths[name]).write_text(text, encoding="utf-8")
        return Path(self.paths[name])


class DocumentTest(ConfigCase):
    def test_opening_and_saving_preserves_comments_and_short_forms(self):
        original = (
            "# header documentation\n"
            "#   indented note\n"
            "\n"
            "municipal broadband | high\n"
            "pole attachment\n"
            "# trailing note\n"
        )
        path = self.write("TOPICS_PATH", original)
        document = hbconfig.topics_document()
        self.assertEqual(document.text(), original)

        document.append({"label": "BEAD funding", "severity": "high", "group": "Telecom"})
        saved = document.text()
        self.assertTrue(saved.startswith("# header documentation"))
        self.assertIn("# trailing note", saved)
        self.assertIn("beam" if False else "BEAD funding | high | Telecom", saved)
        path.write_text(saved, encoding="utf-8")
        topics, warnings = hbnscan.read_topic_file()
        self.assertEqual([topic["label"] for topic in topics],
                         ["municipal broadband", "pole attachment", "BEAD funding"])
        self.assertEqual(warnings, ())

    def test_untouched_lines_keep_their_exact_spelling(self):
        original = "one|high\n#keep\n  two   \n"
        self.write("TOPICS_PATH", original)
        self.assertEqual(hbconfig.topics_document().text(), original)


class SourceTest(ConfigCase):
    def test_add_remove_update_and_validation(self):
        self.write("FEEDS_PATH", "# my feeds\nhttps://a.test/rss\n")
        hbconfig.add_source("https://b.test/rss")
        text = Path(self.paths["FEEDS_PATH"]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# my feeds"))
        self.assertIn("https://b.test/rss", text)

        hbconfig.add_source("https://c.test/rss", position="top")
        values = [url for _i, data, _e in hbconfig.sources_document().entries()
                  for url in [data["url"]]]
        self.assertEqual(values[0], "https://c.test/rss")

        with self.assertRaises(ValueError):
            hbconfig.add_source("https://a.test/rss")
        with self.assertRaises(ValueError):
            hbconfig.add_source("ftp://a.test/rss")
        with self.assertRaises(ValueError):
            hbconfig.add_source("https://has space.test/rss")

        hbconfig.update_source(0, "https://new.test/rss")
        self.assertIn("https://new.test/rss", Path(self.paths["FEEDS_PATH"]).read_text())
        hbconfig.remove_source(0)
        self.assertNotIn("https://new.test/rss", Path(self.paths["FEEDS_PATH"]).read_text())

    def test_test_source_reports_success_and_failure(self):
        with mock.patch.object(hbnscan, "fetch_feed",
                               return_value=([{"title": "x"}], "e", "m")):
            self.assertTrue(hbconfig.test_source("https://a.test/rss")["ok"])
        with mock.patch.object(hbnscan, "fetch_feed", side_effect=OSError("offline")):
            result = hbconfig.test_source("https://a.test/rss")
        self.assertFalse(result["ok"])
        self.assertIn("offline", result["error"])


class TopicTest(ConfigCase):
    def test_crud_and_validation(self):
        self.write("TOPICS_PATH", "## Broadband\nbroadband | high\n")
        hbconfig.add_topic("spectrum", "medium", "Telecom")
        topics = hbconfig.file_topics()
        self.assertEqual([topic["label"] for topic in topics], ["broadband", "spectrum"])

        hbconfig.update_topic(1, "spectrum policy", "low", "")
        self.assertEqual(hbconfig.file_topics()[1],
                         {"label": "spectrum policy", "severity": "low", "group": ""})

        for label, severity in (("", "high"), ("x" * 200, "high"), ("bad|pipe", "high"),
                                ("fine", "urgent")):
            with self.assertRaises(ValueError):
                hbconfig.add_topic(label, severity)
        with self.assertRaises(ValueError):
            hbconfig.add_topic("Broadband", "high")

        hbconfig.remove_topic(0)
        self.assertEqual(len(hbconfig.file_topics()), 1)


class TargetTest(ConfigCase):
    def test_crud_note_rendering_and_category_validation(self):
        self.write("WATCHLIST_PATH", "# pinned names\n")
        category = hbnscan.CATEGORIES[0]
        hbconfig.add_target(["acme fiber", "acme capital"], category, "high", "ask counsel")
        line = Path(self.paths["WATCHLIST_PATH"]).read_text(encoding="utf-8")
        self.assertIn("acme fiber & acme capital | " + category + " | high | ask counsel", line)

        with self.assertRaises(ValueError):
            hbconfig.add_target(["x"], "not_a_category", "high")
        with self.assertRaises(ValueError):
            hbconfig.add_target(["x"], category, "urgent")
        with self.assertRaises(ValueError):
            hbconfig.add_target(["acme fiber & acme capital"], category, "high")

        hbconfig.update_target(0, ["acme fiber"], category, "medium", "")
        rules, warnings = hbnscan.read_watchlist_file()
        self.assertEqual(rules[0]["terms"], ["acme fiber"])
        self.assertEqual(rules[0]["note"], "")
        self.assertEqual(warnings, ())
        hbconfig.remove_target(0)
        self.assertEqual(hbnscan.read_watchlist_file()[0], ())


class ProfileTest(ConfigCase):
    def test_editor_data_fills_defaults_and_reports_the_source(self):
        data = hbconfig.profile_editor_data("")
        self.assertTrue(data["builtin"])
        self.assertEqual(data["data"]["name"], hbnscan.DEFAULT_PROFILE["name"])

        path = self.paths["APP_DIR"] / "profiles" / "custom.json"
        path.write_text(json.dumps({"name": "Custom", "categories": ["policy", "other"]}),
                        encoding="utf-8")
        loaded = hbconfig.profile_editor_data("custom")
        self.assertEqual(loaded["data"]["name"], "Custom")
        self.assertEqual(loaded["data"]["categories"], ["policy", "other"])
        self.assertIn("strong_signals", loaded["data"], "defaults must be filled in")

    def test_validation_catches_real_mistakes(self):
        data = dict(hbnscan.DEFAULT_PROFILE)
        data["categories"] = ["policy", "other"]
        data["core_categories"] = ["litigation"]
        errors, _warnings = hbconfig.validate_profile(data)
        self.assertTrue(any("core categories not in the category list" in e for e in errors))

        data = dict(hbnscan.DEFAULT_PROFILE)
        data["targets"] = [{"match": ["acme"], "category": "nope", "severity": "high"}]
        errors, _ = hbconfig.validate_profile(data)
        self.assertTrue(any("not in the active profile" in e or "is not a category" in e
                            for e in errors))

        data = dict(hbnscan.DEFAULT_PROFILE)
        data["topics"] = ["a | high", "a | low"]
        errors, _ = hbconfig.validate_profile(data)
        self.assertTrue(any("duplicate topic" in e for e in errors))

    def test_save_writes_a_slug_and_never_clobbers_by_accident(self):
        data = dict(hbnscan.DEFAULT_PROFILE, name="My Criteria")
        path = hbconfig.save_profile("", data)
        self.assertEqual(path.name, "my-criteria.json")
        with self.assertRaises(ValueError):
            hbconfig.save_profile("", data)
        self.assertTrue(hbconfig.save_profile("", dict(data, name="My Criteria 2"),
                                              overwrite=True).exists())


class EnvTest(ConfigCase):
    def test_limits_round_trip_and_apply_immediately(self):
        before = (hbnscan.TRIAGE_BUDGET, hbnscan.DEEP_READ_ENABLED, hbnscan.BRIEF_WINDOW_HOURS)
        self.addCleanup(lambda: setattr(hbnscan, "TRIAGE_BUDGET", before[0]))
        self.addCleanup(lambda: setattr(hbnscan, "DEEP_READ_ENABLED", before[1]))
        self.addCleanup(lambda: setattr(hbnscan, "BRIEF_WINDOW_HOURS", before[2]))

        applied = hbconfig.save_env_limits(deep_read=False, briefing=False, max_triage=5,
                                           max_deep_reads=3, brief_hours=48)
        self.assertEqual(applied, {"deep_read": False, "briefing": False,
                                   "max_deep_reads": 3, "max_triage": 5, "brief_hours": 48})
        self.assertEqual(hbnscan.TRIAGE_BUDGET, 5)
        self.assertFalse(hbnscan.DEEP_READ_ENABLED)
        self.assertEqual(hbnscan.BRIEF_WINDOW_HOURS, 48)
        self.assertEqual(hbconfig.env_limits()["max_deep_reads"], 3)

        with self.assertRaises(ValueError):
            hbconfig.save_env_limits(max_triage="many")
        with self.assertRaises(ValueError):
            hbconfig.save_env_limits(brief_hours=1000000)

    def test_notifications_validate_and_keep_the_password(self):
        hbnscan.update_env_file(SMTP_PASSWORD="secret")
        hbconfig.save_notifications(smtp_host="smtp.test", smtp_port="587",
                                    smtp_user="me@test", email_to="me@test")
        values = hbnscan.read_env_values()
        self.assertEqual(values["SMTP_HOST"], "smtp.test")
        self.assertEqual(values["SMTP_PASSWORD"], "secret", "blank password must not erase it")

        with self.assertRaises(ValueError):
            hbconfig.save_notifications(smtp_port="abc")
        with self.assertRaises(ValueError):
            hbconfig.save_notifications(smtp_security="plain")


class DiagnoseTest(ConfigCase):
    def test_clean_configuration_has_no_errors(self):
        self.write("FEEDS_PATH", "https://a.test/rss\n")
        self.write("TOPICS_PATH", "## Broadband\nbroadband | high\n")
        self.write("WATCHLIST_PATH", "")
        findings = hbconfig.diagnose()
        self.assertFalse([f for f in findings if f["level"] == "error"], findings)

    def test_findings_point_at_the_right_area(self):
        self.write("FEEDS_PATH", "ftp://bad\nhttps://a.test/rss\nhttps://a.test/rss\n")
        self.write("TOPICS_PATH", "bad | urgent\n")
        self.write("WATCHLIST_PATH", "acme | not_a_category | high\n")
        hbnscan.save_settings(notify_enabled=True, topics_as_feeds=True)
        findings = hbconfig.diagnose()
        areas = {(f["level"], f["area"]) for f in findings}
        self.assertIn(("error", "Sources"), areas)
        self.assertIn(("warning", "Sources"), areas)     # duplicate URL
        self.assertIn(("error", "Topics"), areas)
        self.assertIn(("error", "Targets"), areas)
        self.assertIn(("error", "Notifications"), areas)  # enabled, no SMTP
        self.assertTrue(all(f["level"] in ("error", "warning", "info") for f in findings))
        self.assertIn("error", hbconfig.summarise(findings))


if __name__ == "__main__":
    unittest.main()
