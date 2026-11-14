import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import hbnscan
from hbnweb import Handler


class FeedParsingTests(unittest.TestCase):
    def test_parses_rss_and_sanitizes_item_link(self):
        raw = b"""<?xml version="1.0"?>
        <rss version="2.0"><channel><item>
          <title>Example story</title>
          <link>javascript:alert(1)</link>
          <description>A useful summary</description>
          <pubDate>Fri, 13 Nov 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""

        items = hbnscan._parse_feed(raw, "test RSS")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Example story")
        self.assertEqual(items[0]["link"], "")
        self.assertEqual(items[0]["description"], "A useful summary")

    def test_parses_atom_with_default_namespace(self):
        raw = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>Atom story</title>
          <link href="https://example.test/story"/>
          <summary>Atom summary</summary>
          <updated>2026-11-13T12:00:00Z</updated>
        </entry></feed>"""

        items = hbnscan._parse_feed(raw, "test Atom")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Atom story")
        self.assertEqual(items[0]["link"], "https://example.test/story")
        self.assertEqual(items[0]["description"], "Atom summary")

    def test_rejects_dtd_and_entity_declarations(self):
        raw = b"""<!DOCTYPE rss [<!ENTITY x "expanded">]>
        <rss><channel><item><title>&x;</title></item></channel></rss>"""

        with self.assertRaises(hbnscan.FeedError):
            hbnscan._parse_feed(raw, "untrusted feed")


class SettingsConcurrencyTests(unittest.TestCase):
    def test_concurrent_partial_updates_preserve_both_values(self):
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            start = threading.Barrier(3)

            def save(**values):
                start.wait(timeout=5)
                return hbnscan.save_settings(**values)

            with mock.patch.object(hbnscan, "SETTINGS_PATH", settings_path):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(save, auto_scan=True)
                    second = pool.submit(save, scan_interval_minutes=15)
                    start.wait(timeout=5)
                    first.result(timeout=5)
                    second.result(timeout=5)

                saved = hbnscan.load_settings()

            self.assertTrue(saved["auto_scan"])
            self.assertEqual(saved["scan_interval_minutes"], 15)
            self.assertTrue(Path(str(settings_path) + ".lock").exists())


class WebHostValidationTests(unittest.TestCase):
    @staticmethod
    def accepted(host, allowed=None, allow_ip_hosts=False):
        request = SimpleNamespace(
            headers={"Host": host},
            allowed_hosts=set(allowed or {"127.0.0.1", "localhost", "::1"}),
            allow_ip_hosts=allow_ip_hosts,
        )
        return Handler._host_ok(request)

    def test_accepts_loopback_with_port_and_bracketed_ipv6(self):
        self.assertTrue(self.accepted("localhost:8765"))
        self.assertTrue(self.accepted("[::1]:8765"))

    def test_wildcard_bind_accepts_ip_literals_but_not_dns_names(self):
        self.assertTrue(self.accepted("192.168.1.20:8765", allow_ip_hosts=True))
        self.assertTrue(self.accepted("[2001:db8::1]:8765", allow_ip_hosts=True))
        self.assertFalse(self.accepted("attacker.example:8765", allow_ip_hosts=True))

    def test_rejects_malformed_or_empty_host(self):
        self.assertFalse(self.accepted(""))
        self.assertFalse(self.accepted("localhost:bad-port"))
        self.assertFalse(self.accepted("localhost/path"))


if __name__ == "__main__":
    unittest.main()
