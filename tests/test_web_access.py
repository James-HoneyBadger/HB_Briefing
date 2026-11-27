"""Web access policy: loopback is trusted, anything else must present the token."""
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D401
        return None


class RemoteAccessTest(unittest.TestCase):
    """A bound-to-LAN server: every read and write needs the token or a session cookie."""

    TOKEN = "lan-token-123"

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.patches = [
            mock.patch.object(hbnscan, "DB_PATH", root / "state.db"),
            mock.patch.object(hbnscan, "SETTINGS_PATH", root / "settings.json"),
            mock.patch.object(hbnscan, "load_feeds", return_value=[]),
            mock.patch.object(hbnscan, "load_watchlist", return_value=[]),
            mock.patch.object(hbnscan, "load_topics", return_value=[]),
            mock.patch.object(hbnscan, "deepseek_configured", return_value=False),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        hbnscan.init_db().close()

        handler = type("LanHandler", (hbnweb.Handler,), {
            "token": self.TOKEN,
            "allowed_hosts": {"127.0.0.1", "localhost", "::1"},
            "allow_ip_hosts": False,
            "quiet": True,
            "require_auth": True,
            "session_secret": b"0123456789abcdef0123456789abcdef",
        })
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def request(self, path, *, method="GET", body=None, token=None, cookie=None,
                follow=False):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("X-HB-Token", token)
        if cookie:
            req.add_header("Cookie", cookie)
        opener = urllib.request.build_opener() if follow else \
            urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=5) as resp:
                return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8"), dict(exc.headers)

    def test_the_shell_is_offered_without_leaking_the_token(self):
        status, page, _headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("token required", page)
        self.assertNotIn(self.TOKEN, page, "the shell must not hand the token to a stranger")

    def test_reads_are_refused_without_credentials(self):
        status, body, _headers = self.request("/api/signals")
        self.assertEqual(status, 401, body)
        status, _body, _headers = self.request("/api/state")
        self.assertEqual(status, 401)

    def test_the_header_token_authorizes_a_read(self):
        status, body, _headers = self.request("/api/signals", token=self.TOKEN)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["total"], 0)

    def test_a_query_string_token_does_not_authorize_reads(self):
        status, _body, _headers = self.request(f"/api/signals?token={self.TOKEN}")
        self.assertEqual(status, 401, "a token in a URL must not unlock the API")

    def test_a_valid_token_is_exchanged_for_a_session_cookie(self):
        status, _body, headers = self.request(f"/?token={self.TOKEN}")
        self.assertEqual(status, 303)
        self.assertEqual(headers.get("Location"), "/")
        cookie = headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn(self.TOKEN, cookie, "the cookie must not carry the token itself")

        pair = cookie.split(";")[0]
        status, body, _headers = self.request("/api/signals", cookie=pair)
        self.assertEqual(status, 200, body)

    def test_a_wrong_token_does_not_produce_a_session(self):
        status, page, headers = self.request("/?token=wrong")
        self.assertEqual(status, 200)
        self.assertIn("token required", page)
        self.assertNotIn("Set-Cookie", headers)

    def test_a_forged_cookie_is_refused(self):
        status, _body, _headers = self.request("/api/signals",
                                              cookie=f"{hbnweb.SESSION_COOKIE}=1.deadbeef")
        self.assertEqual(status, 401)

    def test_the_signed_page_does_not_embed_the_token(self):
        status, _body, headers = self.request(f"/?token={self.TOKEN}")
        pair = headers["Set-Cookie"].split(";")[0]
        status, page, _headers = self.request("/", cookie=pair)
        self.assertEqual(status, 200)
        self.assertIn('id="themeSel"', page)
        self.assertNotIn(f'"{self.TOKEN}"', page)

    def test_writes_still_need_the_token_or_a_session(self):
        status, _body, _headers = self.request("/api/settings", method="POST",
                                              body={"row_order": "relevance"})
        self.assertEqual(status, 403)
        status, body, _headers = self.request("/api/settings", method="POST",
                                              body={"row_order": "relevance"},
                                              token=self.TOKEN)
        self.assertEqual(status, 200, body)

    def status_of(self, path):
        """The status only, so binary assets can be checked without decoding them."""
        with urllib.request.urlopen(
                urllib.request.Request(f"http://127.0.0.1:{self.port}{path}"), timeout=5) as resp:
            return resp.status

    def test_install_assets_stay_public_because_they_carry_no_data(self):
        for path in ("/manifest.webmanifest", "/sw.js", "/favicon.ico", "/icon-192.png"):
            self.assertEqual(self.status_of(path), 200, path)

    def test_security_headers_are_present(self):
        _status, _body, headers = self.request("/api/signals", token=self.TOKEN)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")


class RateLimitTest(unittest.TestCase):
    def test_a_flood_of_writes_is_throttled(self):
        with mock.patch.object(hbnweb, "WRITE_LIMIT", 3):
            handler = type("ThrottledHandler", (hbnweb.Handler,), {
                "token": "t", "allowed_hosts": {"127.0.0.1"}, "allow_ip_hosts": False,
                "quiet": True, "require_auth": False, "session_secret": b"secret",
            })
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.daemon_threads = True
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)

            statuses = []
            for _index in range(5):
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/settings", data=b"{}", method="POST")
                req.add_header("Content-Type", "application/json")
                req.add_header("X-HB-Token", "t")
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        statuses.append(resp.status)
                except urllib.error.HTTPError as exc:
                    statuses.append(exc.code)
            self.assertIn(429, statuses, statuses)


class ServiceWorkerTest(unittest.TestCase):
    def test_the_worker_does_not_cache_responses_marked_no_store(self):
        self.assertIn("no-store", hbnweb.SERVICE_WORKER)
        self.assertIn("cacheable", hbnweb.SERVICE_WORKER)


if __name__ == "__main__":
    unittest.main()
