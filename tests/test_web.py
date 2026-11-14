import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import hbnscan
import hbnweb


class WebApiTest(unittest.TestCase):
    """End-to-end request tests against a real server on an ephemeral port."""

    TOKEN = "test-token"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "state.db"
        self.patches = [
            mock.patch.object(hbnscan, "DB_PATH", self.db_path),
            mock.patch.object(hbnscan, "SETTINGS_PATH", Path(self.directory.name) / "s.json"),
            mock.patch.object(hbnscan, "load_feeds", return_value=[]),
            mock.patch.object(hbnscan, "load_watchlist", return_value=[]),
            mock.patch.object(hbnscan, "load_topics", return_value=[]),
            mock.patch.object(hbnscan, "deepseek_configured", return_value=False),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

        conn = hbnscan.init_db()
        conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason) VALUES "
            "('h1', 'Acme fined', 'https://e.test/a', 'https://feed.test/rss', 1, "
            "'[Watchlist] acme → policy/high. Penalty issued.', '2026-11-13T10:00:00', "
            "'policy', 'high', '{}', 'Read the order', '[\"a fact\"]', 0.9, 1, 0, "
            "'2026-11-12T09:00:00', 'watchlist', 'rule: acme')")
        conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason) VALUES "
            "('h2', 'Undated notice', 'https://e.test/b', 'https://feed.test/rss', 1, "
            "'[Local Heuristic] Matched notice.', '2026-11-13T11:00:00', 'policy', 'low', "
            "'{}', '', '[]', NULL, 0, 0, '', 'heuristic', 'matched: notice')")
        conn.commit()
        conn.close()

        handler = type("BoundHandler", (hbnweb.Handler,), {
            "token": self.TOKEN,
            "allowed_hosts": {"127.0.0.1", "localhost", "::1"},
            "allow_ip_hosts": False,
            "quiet": True,
        })
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, *, method="GET", body=None, token=None, host=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-HB-Token", token)
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8")

    def get_json(self, path):
        status, body = self.request(path)
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_page_and_signal_listing_carry_provenance_and_freshness(self):
        status, page = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(self.TOKEN, page)

        payload = self.get_json("/api/signals?order=priority")
        self.assertEqual(payload["total"], 2)
        row = next(item for item in payload["rows"] if item["hash"] == "h1")
        self.assertEqual(row["priority"], "watchlist")
        self.assertEqual(row["layer"], "watchlist")
        self.assertTrue(row["freshness"])
        self.assertNotIn("[Watchlist]", row["assessment"])
        undated = next(item for item in payload["rows"] if item["hash"] == "h2")
        self.assertEqual(undated["freshness"], "undated")
        self.assertNotIn("[Local Heuristic]", undated["assessment"])

        # A recency filter is about publication; an undated row cannot claim to be recent.
        recent = self.get_json("/api/signals?recent=3650d")
        self.assertEqual(recent["total"], 1)
        self.assertEqual(recent["rows"][0]["hash"], "h1")

    def test_state_counts_flagged_and_undated(self):
        state = self.get_json("/api/state")
        self.assertEqual(state["counts"]["flagged"], 2)
        self.assertEqual(state["counts"]["watchlist"], 1)
        self.assertEqual(state["counts"]["undated"], 1)

    def test_dismiss_needs_the_token_and_hides_the_row(self):
        status, _body = self.request("/api/dismiss", method="POST", body={"hash": "h2"})
        self.assertEqual(status, 403)

        status, body = self.request("/api/dismiss", method="POST", body={"hash": "h2"},
                                    token=self.TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["state"], "dismissed")
        self.assertEqual(self.get_json("/api/signals")["total"], 1)

        status, body = self.request("/api/dismiss", method="POST",
                                    body={"hash": "h2", "undo": True}, token=self.TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(self.get_json("/api/signals")["total"], 2)

    def test_settings_update_is_partial_and_persisted(self):
        status, body = self.request("/api/settings", method="POST",
                                    body={"row_order": "relevance"}, token=self.TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["row_order"], "relevance")
        self.assertEqual(hbnscan.load_settings()["scan_interval_minutes"],
                         hbnscan.DEFAULT_SETTINGS["scan_interval_minutes"])

    def test_foreign_host_header_is_rejected(self):
        status, _body = self.request("/api/signals", host="attacker.example")
        self.assertEqual(status, 421)

    def test_export_serves_csv_from_the_same_filter(self):
        status, body = self.request("/api/export?fmt=csv")
        self.assertEqual(status, 200)
        self.assertIn("Headline", body)
        self.assertIn("Acme fined", body)


class TerminalPagingTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patches = [
            mock.patch.object(hbnscan, "DB_PATH", Path(self.directory.name) / "state.db"),
            mock.patch.object(hbnscan, "SETTINGS_PATH", Path(self.directory.name) / "s.json"),
            mock.patch.object(hbnscan, "load_settings", return_value=dict(hbnscan.DEFAULT_SETTINGS)),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        conn = hbnscan.init_db()
        conn.executemany(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, published, source_layer) "
            "VALUES (?, ?, ?, 'https://feed.test/rss', 1, '[Local Heuristic] x', "
            "'2026-11-13T10:00:00', 'policy', 'high', '', 'heuristic')",
            [(f"h{i}", f"story {i}", f"https://e.test/{i}") for i in range(250)])
        conn.commit()
        conn.close()

    def test_pages_cover_the_whole_result_set(self):
        import hbntui
        state = hbntui.State()
        state.reload()
        self.assertEqual(len(state.rows), hbntui.PAGE)
        self.assertEqual(state.page_range(), f"1-{hbntui.PAGE} of 250")

        state.next_page()
        self.assertEqual(state.offset, hbntui.PAGE)
        self.assertEqual(len(state.rows), 250 - hbntui.PAGE)
        self.assertEqual(state.page_range(), f"{hbntui.PAGE + 1}-250 of 250")

        state.prev_page()
        self.assertEqual(state.offset, 0)
        self.assertEqual(len(state.rows), hbntui.PAGE)


class TuiImportTest(unittest.TestCase):
    def test_tui_module_imports_without_a_terminal(self):
        import hbntui
        self.assertIn("relevance", hbntui.ORDER_CYCLE)
        self.assertEqual(hbntui.SIGNAL_COLUMNS, hbnscan.SIGNAL_COLUMNS)


if __name__ == "__main__":
    unittest.main()
