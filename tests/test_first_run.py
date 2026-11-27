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


class LocalProviderTest(unittest.TestCase):
    """A local inference server is authorized by running it, not by inventing an API key."""

    def status(self, base_url, api_key="", model="llama3.1"):
        return hbnscan.ai_status(base_url=base_url, api_key=api_key, model=model)

    def test_ollama_on_loopback_needs_no_key(self):
        with mock.patch.object(hbnscan, "LLM_BASE_URL",
                               "http://localhost:11434/v1/chat/completions"), \
             mock.patch.object(hbnscan, "LLM_API_KEY", "your_deepseek_api_key_here"):
            status = hbnscan.ai_status()
            self.assertEqual(status["mode"], hbnscan.AI_MODE_LOCAL)
            self.assertFalse(status["key_used"])
            self.assertTrue(hbnscan.deepseek_configured(),
                            "a local server is a configured provider")
            self.assertEqual(hbnscan.grading_mode(), "model")
            self.assertIn("local model", hbnscan.describe_grading_mode())

    def test_a_lan_inference_server_counts_as_local(self):
        for url in ("http://127.0.0.1:1234/v1/chat/completions",
                    "http://192.168.1.50:1234/v1/chat/completions",
                    "http://studio.local:8080/v1/chat/completions",
                    "http://[::1]:11434/v1/chat/completions"):
            self.assertEqual(self.status(url)["mode"], hbnscan.AI_MODE_LOCAL, url)

    def test_a_remote_endpoint_still_needs_a_key(self):
        status = self.status("https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(status["mode"], hbnscan.AI_MODE_OFFLINE)
        self.assertIn("no API key", status["reason"])

    def test_a_remote_endpoint_with_a_key_is_remote(self):
        status = self.status("https://api.openai.com/v1/chat/completions", api_key="sk-real-key-1234")
        self.assertEqual(status["mode"], hbnscan.AI_MODE_REMOTE)
        self.assertTrue(status["key_used"])

    def test_a_switched_off_provider_is_reported_as_disabled(self):
        with mock.patch.object(hbnscan, "AI_DISABLED", True):
            status = self.status("http://localhost:11434/v1/chat/completions",
                                 api_key="sk-real-key-1234")
            self.assertEqual(status["mode"], hbnscan.AI_MODE_DISABLED)
            self.assertIn("switched off", hbnscan.describe_grading_mode())
            self.assertFalse(hbnscan.deepseek_configured())

    def test_the_placeholder_is_not_sent_as_a_key(self):
        for template in ("your_deepseek_api_key_here", "your_api_key_here", "CHANGEME",
                         "sk-xxxxxxxx", "  "):
            self.assertEqual(hbnscan.llm_api_key(template), "", template)
        self.assertEqual(hbnscan.llm_api_key("sk-real-key-1234"), "sk-real-key-1234")

    def test_a_seeded_env_file_does_not_look_like_a_configured_key(self):
        # bootstrap_home copies .env.example into .env; its template value must read as absent.
        with mock.patch.object(hbnscan, "LLM_BASE_URL",
                               "https://api.deepseek.com/v1/chat/completions"):
            status = hbnscan.ai_status(api_key="your_api_key_here")
        self.assertEqual(status["mode"], hbnscan.AI_MODE_OFFLINE)
        self.assertFalse(status["key_used"])

    def test_a_keyless_local_server_is_not_sent_an_authorization_header(self):
        captured = {}

        class FakeResponse:
            headers = {}

            def read(self, size=-1):
                return json.dumps({"choices": [{"message": {"content": "{}"}}]}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def fake_open(url, purpose, timeout=None, headers=None, method=None, data=None,
                      **kwargs):
            captured.update(headers=headers, url=url)
            return FakeResponse()

        with mock.patch.object(hbnscan, "LLM_BASE_URL",
                               "http://localhost:11434/v1/chat/completions"), \
             mock.patch.object(hbnscan, "LLM_API_KEY", ""), \
             mock.patch.object(hbnscan.hbnet, "open_url", side_effect=fake_open):
            hbnscan._call_deepseek("system", "user", 16)
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(captured["url"], "http://localhost:11434/v1/chat/completions")


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
