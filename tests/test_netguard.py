"""The network trust boundary: what each kind of request is allowed to reach."""
import email.message
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.error
from unittest import mock

import hbnet


def addrinfo(*addresses):
    infos = []
    for address in addresses:
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        infos.append((family, socket.SOCK_STREAM, 6, "", (address, 80)))
    return infos


class SchemeAndShapeTest(unittest.TestCase):
    def assert_refused(self, url, purpose=hbnet.PURPOSE_ARTICLE, needle=""):
        with self.assertRaises(hbnet.NetworkError) as caught:
            hbnet.validate_url(url, purpose)
        if needle:
            self.assertIn(needle, str(caught.exception))
        return caught.exception

    def test_only_http_and_https_are_accepted(self):
        for url in ("file:///etc/passwd", "ftp://e.test/a", "javascript:alert(1)",
                    "gopher://e.test/1", "//e.test/a"):
            self.assert_refused(url, needle="not allowed")

    def test_embedded_credentials_are_refused(self):
        self.assert_refused("https://user:secret@e.test/a", needle="credentials")
        self.assert_refused("https://token@e.test/a", needle="credentials")

    def test_control_characters_and_absurd_length_are_refused(self):
        self.assert_refused("https://e.test/a b")
        self.assert_refused("https://e.test/\nHost: evil")
        self.assert_refused("https://e.test/" + "a" * hbnet.MAX_URL_LENGTH, needle="long")

    def test_empty_url_is_refused(self):
        self.assert_refused("")


class AddressPolicyTest(unittest.TestCase):
    def test_feed_endpoints_may_reach_a_local_publisher(self):
        target = hbnet.validate_url("http://192.168.1.20:1200/rss", hbnet.PURPOSE_FEED)
        self.assertEqual(target.kind, "private")

    def test_article_links_may_not_reach_the_local_network(self):
        for url, kind in (("http://127.0.0.1:8080/admin", "loopback"),
                          ("http://192.168.1.1/", "private"),
                          ("http://10.0.0.5/", "private"),
                          ("http://[::1]/", "loopback")):
            with self.assertRaises(hbnet.NetworkError) as caught:
                hbnet.validate_url(url, hbnet.PURPOSE_ARTICLE)
            self.assertIn(kind.replace("_", "-"), str(caught.exception))

    def test_cloud_metadata_is_refused_for_every_purpose(self):
        for purpose in hbnet.PURPOSES:
            for url in ("http://169.254.169.254/latest/meta-data/",
                        "http://[fd00:ec2::254]/",
                        "http://100.100.100.200/"):
                with self.assertRaises(hbnet.NetworkError):
                    hbnet.validate_url(url, purpose)

    def test_metadata_hostnames_are_refused_before_dns(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo") as resolver:
            with self.assertRaises(hbnet.NetworkError) as caught:
                hbnet.validate_url("http://metadata.google.internal/computeMetadata/v1/",
                                   hbnet.PURPOSE_FEED)
        resolver.assert_not_called()
        self.assertIn("metadata", str(caught.exception))

    def test_link_local_is_never_acceptable(self):
        for purpose in hbnet.PURPOSES:
            with self.assertRaises(hbnet.NetworkError):
                hbnet.validate_url("http://169.254.10.10/", purpose)

    def test_a_local_model_server_is_allowed_but_not_from_feed_content(self):
        hbnet.validate_url("http://localhost:11434/v1/chat/completions", hbnet.PURPOSE_AI)
        hbnet.validate_url("http://127.0.0.1:1234/v1/chat/completions", hbnet.PURPOSE_AI)
        with self.assertRaises(hbnet.NetworkError):
            hbnet.validate_url("http://127.0.0.1:1234/v1", hbnet.PURPOSE_ARTICLE)

    def test_a_self_hosted_push_endpoint_may_live_on_the_lan(self):
        hbnet.validate_url("http://192.168.1.9:8080/mytopic", hbnet.PURPOSE_WEBHOOK)

    def test_public_addresses_pass_every_purpose(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34")):
            for purpose in hbnet.PURPOSES:
                target = hbnet.validate_url("https://example.com/x", purpose)
                self.assertEqual(target.kind, "global")
                self.assertEqual(target.address, "93.184.216.34")


class DnsRebindingTest(unittest.TestCase):
    def test_any_private_answer_refuses_the_whole_name(self):
        # Round-robin between a public and a local address must not be a way in.
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34", "127.0.0.1")):
            with self.assertRaises(hbnet.NetworkError):
                hbnet.validate_url("https://rebind.test/x", hbnet.PURPOSE_ARTICLE)

    def test_the_connection_is_pinned_to_the_validated_address(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34")):
            target = hbnet.validate_url("https://example.com/x", hbnet.PURPOSE_ARTICLE)
        self.assertEqual(target.address, "93.184.216.34")
        self.assertEqual(target.host, "example.com")

    def test_unresolvable_hosts_report_a_clear_error(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               side_effect=socket.gaierror(-2, "Name or service not known")):
            with self.assertRaises(hbnet.NetworkError) as caught:
                hbnet.validate_url("https://nope.invalid/x", hbnet.PURPOSE_FEED)
        self.assertIn("could not resolve", str(caught.exception))


class RedirectTest(unittest.TestCase):
    def http_error(self, code, location):
        headers = email.message.Message()
        if location:
            headers["Location"] = location
        return urllib.error.HTTPError("https://e.test/a", code, "redirect", headers, None)

    def test_a_redirect_into_private_space_is_refused(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34")), \
             mock.patch.object(hbnet, "_build_opener") as builder:
            builder.return_value.open = mock.Mock(
                side_effect=self.http_error(302, "http://169.254.169.254/latest/meta-data/"))
            with self.assertRaises(hbnet.NetworkError):
                hbnet.open_url("https://example.com/x", hbnet.PURPOSE_ARTICLE)

    def test_a_redirect_to_a_public_hop_is_followed_and_validated(self):
        response = object()
        first = self.http_error(301, "/moved")
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34")), \
             mock.patch.object(hbnet, "_build_opener") as builder:
            builder.return_value.open = mock.Mock(side_effect=[first, response])
            self.assertIs(hbnet.open_url("https://example.com/story", hbnet.PURPOSE_ARTICLE),
                          response)
            opened = [call.args[0] for call in builder.return_value.open.call_args_list]
        self.assertEqual(opened[-1].full_url, "https://example.com/moved")

    def test_a_redirect_loop_terminates(self):
        with mock.patch.object(hbnet.socket, "getaddrinfo",
                               return_value=addrinfo("93.184.216.34")), \
             mock.patch.object(hbnet, "_build_opener") as builder:
            builder.return_value.open = mock.Mock(
                side_effect=self.http_error(302, "https://example.com/x"))
            with self.assertRaises(hbnet.NetworkError):
                hbnet.open_url("https://example.com/x", hbnet.PURPOSE_ARTICLE)


class ResponseCapTest(unittest.TestCase):
    class Fake:
        def __init__(self, body, content_length=None):
            self._body = body
            self.headers = email.message.Message()
            if content_length is not None:
                self.headers["Content-Length"] = str(content_length)

        def read(self, size=-1):
            return self._body if size < 0 else self._body[:size]

    def test_a_declared_oversized_body_is_refused_without_reading_it(self):
        fake = self.Fake(b"x" * 10, content_length=10 * 1024 * 1024)
        with self.assertRaises(hbnet.NetworkError):
            hbnet.read_capped(fake, 1024)

    def test_an_undeclared_oversized_body_is_refused_after_the_cap(self):
        with self.assertRaises(hbnet.NetworkError):
            hbnet.read_capped(self.Fake(b"x" * 2048), 1024)

    def test_a_body_at_the_cap_is_accepted(self):
        self.assertEqual(len(hbnet.read_capped(self.Fake(b"x" * 1024), 1024)), 1024)

    def test_a_garbage_content_length_is_ignored(self):
        self.assertEqual(hbnet.read_capped(self.Fake(b"abc", content_length="nonsense"), 16),
                         b"abc")


if __name__ == "__main__":
    unittest.main()


class _Handler(BaseHTTPRequestHandler):
    """A tiny origin server, so the pinned-connection path is exercised over a real socket."""

    BODY = b"x" * 100

    def log_message(self, *args):
        return

    def do_GET(self):                                        # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/final")
            self.end_headers()
            return
        if self.path == "/to-metadata":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/to-file":
            self.send_response(302)
            self.send_header("Location", "file:///etc/passwd")
            self.end_headers()
            return
        payload = b"y" * 4096 if self.path == "/big" else self.BODY
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class LiveConnectionTest(unittest.TestCase):
    """The real socket path: pinning, redirects and the byte cap, no mocks."""

    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_a_user_configured_feed_endpoint_is_fetched_over_a_real_socket(self):
        with hbnet.open_url(self.url("/feed"), hbnet.PURPOSE_FEED, timeout=5) as response:
            self.assertEqual(hbnet.read_capped(response, 1024), _Handler.BODY)

    def test_the_same_endpoint_is_refused_for_feed_content(self):
        with self.assertRaises(hbnet.NetworkError):
            hbnet.open_url(self.url("/feed"), hbnet.PURPOSE_ARTICLE, timeout=5)

    def test_a_redirect_is_followed_on_a_real_connection(self):
        with hbnet.open_url(self.url("/redirect"), hbnet.PURPOSE_FEED, timeout=5) as response:
            self.assertEqual(response.geturl(), self.url("/final"))

    def test_a_redirect_to_a_metadata_endpoint_is_refused_over_a_real_connection(self):
        # This is the case a default urllib redirect handler would have followed silently.
        with self.assertRaises(hbnet.NetworkError):
            hbnet.open_url(self.url("/to-metadata"), hbnet.PURPOSE_FEED, timeout=5)

    def test_a_redirect_into_the_filesystem_is_refused_over_a_real_connection(self):
        with self.assertRaises(hbnet.NetworkError):
            hbnet.open_url(self.url("/to-file"), hbnet.PURPOSE_FEED, timeout=5)

    def test_an_oversized_body_is_refused_over_a_real_connection(self):
        with hbnet.open_url(self.url("/big"), hbnet.PURPOSE_FEED, timeout=5) as response:
            with self.assertRaises(hbnet.NetworkError):
                hbnet.read_capped(response, 1024)
