"""Push notifications over plain HTTP: the endpoint shapes, and what counts as delivered.

Nothing here needs a real Slack or a real phone: a throwaway HTTP server records what the
transport actually sent, which is the only way to know the payloads are right.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import hbnscan


class Recorder(BaseHTTPRequestHandler):
    """Accepts one request shape and records it on the server object."""

    received = []
    status = 200
    body = b""

    def log_message(self, *args):        # silence the server in test output
        pass

    def _record(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = self.rfile.read(length) if length else b""
        type(self).received.append({
            "method": self.command,
            "path": self.path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": payload,
        })
        self.send_response(type(self).status)
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    do_POST = _record
    do_PUT = _record


class WebhookTest(unittest.TestCase):
    def setUp(self):
        Recorder.received = []
        Recorder.status = 200
        Recorder.body = b""
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.base = f"http://127.0.0.1:{self.port}"

    def config(self, fmt, path="/hook", token=""):
        return {"url": f"{self.base}{path}", "format": fmt, "token": token,
                "configured": True}

    def sent(self):
        self.assertEqual(len(Recorder.received), 1, Recorder.received)
        return Recorder.received[0]

    def test_generic_posts_title_and_text(self):
        ok, detail = hbnscan.send_webhook("Subject", "Body", self.config("generic", token="t0k"))
        self.assertTrue(ok, detail)
        request = self.sent()
        self.assertEqual(request["method"], "POST")
        payload = json.loads(request["body"])
        self.assertEqual(payload["title"], "Subject")
        self.assertEqual(payload["text"], "Body")
        self.assertEqual(request["headers"]["authorization"], "Bearer t0k")

    def test_slack_wraps_the_digest_in_one_text_field(self):
        self.assertTrue(hbnscan.send_webhook("S", "B", self.config("slack"))[0])
        payload = json.loads(self.sent()["body"])
        self.assertEqual(list(payload), ["text"])
        self.assertIn("*S*", payload["text"])

    def test_discord_truncates_to_its_message_limit(self):
        ok, _detail = hbnscan.send_webhook("S", "x" * 4000, self.config("discord"))
        self.assertTrue(ok)
        payload = json.loads(self.sent()["body"])
        self.assertLessEqual(len(payload["content"]), 2000)
        self.assertTrue(payload["content"].startswith("**S**"))

    def test_gotify_asks_for_a_title_message_and_priority(self):
        self.assertTrue(hbnscan.send_webhook("S", "B", self.config("gotify"))[0])
        payload = json.loads(self.sent()["body"])
        self.assertEqual(payload, {"title": "S", "message": "B", "priority": 5})

    def test_ntfy_sends_plain_text_with_title_headers(self):
        ok, _detail = hbnscan.send_webhook("S", "B", self.config("ntfy", token="tk"))
        self.assertTrue(ok)
        request = self.sent()
        self.assertEqual(request["body"], b"B")
        self.assertEqual(request["headers"]["title"], "S")
        self.assertEqual(request["headers"]["authorization"], "Bearer tk")

    def test_matrix_uses_put_with_a_bearer_token(self):
        ok, _detail = hbnscan.send_webhook("S", "B", self.config("matrix", token="mt"))
        self.assertTrue(ok)
        request = self.sent()
        self.assertEqual(request["method"], "PUT")
        self.assertEqual(request["headers"]["authorization"], "Bearer mt")
        self.assertEqual(json.loads(request["body"])["msgtype"], "m.text")

    def test_an_http_error_is_reported_with_its_hint(self):
        Recorder.status = 401
        Recorder.body = b"bad token"
        ok, detail = hbnscan.send_webhook("S", "B", self.config("ntfy"))
        self.assertFalse(ok)
        self.assertIn("401", detail)
        self.assertIn("NOTIFY_WEBHOOK_TOKEN", detail)

    def test_a_missing_topic_is_explained(self):
        Recorder.status = 404
        ok, detail = hbnscan.send_webhook("S", "B", self.config("ntfy"))
        self.assertFalse(ok)
        self.assertIn("topic", detail)

    def test_an_unreachable_host_does_not_raise(self):
        config = {"url": "http://127.0.0.1:9/hook", "format": "generic", "token": "",
                  "configured": True}
        ok, detail = hbnscan.send_webhook("S", "B", config)
        self.assertFalse(ok)
        self.assertIn("could not reach", detail)

    def test_no_url_means_nothing_is_sent(self):
        ok, detail = hbnscan.send_webhook("S", "B", {"url": "", "format": "generic",
                                                     "token": "", "configured": False})
        self.assertFalse(ok)
        self.assertIn("no webhook", detail)
        self.assertEqual(Recorder.received, [])

    def test_the_format_is_inferred_from_the_host(self):
        cases = {"https://discord.com/api/webhooks/1/x": "discord",
                 "https://hooks.slack.com/services/x": "slack",
                 "https://ntfy.sh/topic": "ntfy",
                 "https://gotify.example/message?token=a": "gotify",
                 "https://m.test/_matrix/client/r0/rooms/!r/send/m.room.message/1": "matrix",
                 "https://example.test/hook": "generic"}
        for url, expected in cases.items():
            self.assertEqual(hbnscan._guess_webhook_format(url), expected, url)


class SettingsTest(unittest.TestCase):
    def test_webhook_values_are_read_from_the_env_file(self):
        values = {"NOTIFY_WEBHOOK_URL": "https://ntfy.sh/briefing",
                  "NOTIFY_WEBHOOK_FORMAT": "auto",
                  "NOTIFY_WEBHOOK_TOKEN": "tk"}
        with mock.patch.object(hbnscan, "_env_values", return_value=values):
            config = hbnscan.webhook_config()
        self.assertEqual(config["format"], "ntfy", "auto must be resolved before use")
        self.assertEqual(config["token"], "tk")
        self.assertTrue(config["configured"])
        self.assertIn("ntfy", hbnscan.describe_webhook(config))

    def test_an_unknown_format_falls_back_to_inference(self):
        values = {"NOTIFY_WEBHOOK_URL": "https://discord.com/api/webhooks/1/x",
                  "NOTIFY_WEBHOOK_FORMAT": "nonsense"}
        with mock.patch.object(hbnscan, "_env_values", return_value=values):
            self.assertEqual(hbnscan.webhook_config()["format"], "discord")

    def test_describe_says_webhook_only_when_smtp_is_absent(self):
        with mock.patch.object(hbnscan, "_env_values",
                               return_value={"NOTIFY_WEBHOOK_URL": "https://ntfy.sh/t"}):
            text = hbnscan.describe_notifications()
        self.assertIn("webhook only", text)

    def test_nothing_configured_says_so(self):
        with mock.patch.object(hbnscan, "_env_values", return_value={}):
            self.assertIn("not configured", hbnscan.describe_notifications())


class SendNotificationsTest(unittest.TestCase):
    """The push path has to agree with the queue: delivered marks sent, failed does not."""

    def setUp(self):
        Recorder.received = []
        Recorder.status = 200
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        import tempfile
        from pathlib import Path
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, value in (("DB_PATH", root / "state.db"),
                            ("SETTINGS_PATH", root / "settings.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason) VALUES ('h1','Acme fined',"
            "'https://e.test/a','https://f.test/rss',1,'[Local Heuristic] Core focus.',"
            "'2026-11-13T10:00:00','litigation','high','{}','','[]',0.9,1,0,'2026-11-12',"
            "'heuristic','core')")
        self.conn.commit()
        self.settings = dict(hbnscan.load_settings(), notify_enabled=True,
                             notify_min_severity="high")

    def env(self, status=200, url=None):
        values = {"NOTIFY_WEBHOOK_URL": url or f"http://127.0.0.1:{self.port}/hook",
                  "NOTIFY_WEBHOOK_FORMAT": "generic"}
        patches = [mock.patch.object(hbnscan, "_env_values", return_value=values),
                   mock.patch.object(hbnscan, "notifications_configured", return_value=False)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        Recorder.status = status

    def sent_rows(self):
        return self.conn.execute("SELECT COUNT(*) FROM sent_alerts").fetchone()[0]

    def test_a_delivered_push_marks_the_signal_sent(self):
        self.env()
        lines = []
        with mock.patch.object(hbnscan, "load_settings", return_value=self.settings):
            count = hbnscan.send_notifications(self.conn, log_callback=lines.append)
        self.assertEqual(count, 1)
        self.assertEqual(self.sent_rows(), 1)
        self.assertTrue(any("[NOTIFY]" in line for line in lines), lines)

    def test_a_failed_push_leaves_the_signal_queued(self):
        self.env(status=500)
        lines = []
        with mock.patch.object(hbnscan, "load_settings", return_value=self.settings):
            count = hbnscan.send_notifications(self.conn, log_callback=lines.append)
        self.assertEqual(count, 0)
        self.assertEqual(self.sent_rows(), 0, "a failure must not silence the next attempt")
        self.assertTrue(any("No channel delivered" in line for line in lines), lines)

    def test_nothing_configured_is_reported(self):
        with mock.patch.object(hbnscan, "_env_values", return_value={}), \
             mock.patch.object(hbnscan, "notifications_configured", return_value=False), \
             mock.patch.object(hbnscan, "load_settings", return_value=self.settings):
            lines = []
            count = hbnscan.send_notifications(self.conn, log_callback=lines.append)
        self.assertEqual(count, 0)
        self.assertTrue(any("no channel is configured" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
