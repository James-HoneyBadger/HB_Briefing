import email.message
import io
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import hbnscan


class FakeResponse:
    """Just enough of an HTTP response for fetch_article()."""

    def __init__(self, body, content_type="text/html; charset=utf-8"):
        self._body = body
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type

    def read(self, _size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def http_error(code):
    return urllib.error.HTTPError("https://e.test/a", code, "blocked", email.message.Message(),
                                  None)


class PdfExtractionTest(unittest.TestCase):
    def test_pdf_is_extracted_through_pdftotext(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"Order 2026-11-12 text", stderr=b"")
        with mock.patch.object(hbnscan.hbnet, "open_url",
                               return_value=FakeResponse(b"%PDF-1.7 body", "application/pdf")), \
             mock.patch.object(hbnscan, "PDFTOTEXT", "/usr/bin/pdftotext"), \
             mock.patch.object(hbnscan.subprocess, "run", return_value=completed) as run:
            text = hbnscan.fetch_article("https://e.test/order.pdf")
        self.assertEqual(text, "Order 2026-11-12 text")
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["/usr/bin/pdftotext", "-q"])
        self.assertIsNone(run.call_args.kwargs.get("shell"))

    def test_pdf_detected_by_magic_when_the_server_lies_about_the_type(self):
        completed = subprocess.CompletedProcess([], 0, stdout=b"filing text", stderr=b"")
        with mock.patch.object(hbnscan.hbnet, "open_url",
                               return_value=FakeResponse(b"%PDF-1.4 x",
                                                         "application/octet-stream")), \
             mock.patch.object(hbnscan, "PDFTOTEXT", "/usr/bin/pdftotext"), \
             mock.patch.object(hbnscan.subprocess, "run", return_value=completed):
            self.assertEqual(hbnscan.fetch_article("https://e.test/view"), "filing text")

    def test_pdf_without_pdftotext_returns_empty_and_says_so(self):
        lines = []
        with mock.patch.object(hbnscan.hbnet, "open_url",
                               return_value=FakeResponse(b"%PDF-1.4 x", "application/pdf")), \
             mock.patch.object(hbnscan, "PDFTOTEXT", ""):
            text = hbnscan.fetch_article("https://e.test/order.pdf", log_callback=lines.append)
        self.assertEqual(text, "")
        self.assertTrue(any("pdftotext" in line for line in lines))

    def test_html_still_goes_through_the_text_extractor(self):
        body = b"<html><body><p>Plain <b>story</b> text</p></body></html>"
        with mock.patch.object(hbnscan.hbnet, "open_url",
                               return_value=FakeResponse(body)):
            self.assertIn("Plain story text", hbnscan.fetch_article("https://e.test/a"))


class _Sink:
    """Stands in for the child's stdin pipe."""

    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, chunk):
        self.data.extend(chunk)

    def close(self):
        self.closed = True


class FakeProcess:
    """A child process whose pipes are in memory, so the streaming loop is exercised as is."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0, hang=False):
        self.stdin = _Sink()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.hang = hang
        self.killed = False

    def wait(self, timeout=None):
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("fetcher", timeout)
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class FetchHookTest(unittest.TestCase):
    def run_with_hook(self, hook, process, *, url="https://e.test/a", **kwargs):
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", hook), \
             mock.patch.object(hbnscan, "FETCH_CMD_TIMEOUT", 5), \
             mock.patch.object(hbnscan.subprocess, "Popen", return_value=process) as runner:
            text = hbnscan.fetch_article(url, **kwargs)
        return text, runner

    def test_403_falls_back_to_the_hook_with_the_url_on_stdin(self):
        process = FakeProcess(b"<html><body>Hooked article</body></html>")
        text, runner = self.run_with_hook("myfetcher --quiet", process)
        self.assertIn("Hooked article", text)
        self.assertEqual(bytes(process.stdin.data), b"https://e.test/a")
        self.assertIsNone(runner.call_args.kwargs.get("shell"))

    def test_url_placeholder_becomes_an_argument_and_stdin_stays_empty(self):
        process = FakeProcess(b"body")
        _text, runner = self.run_with_hook("fetcher --url {url}", process)
        self.assertEqual(runner.call_args.args[0], ["fetcher", "--url", "https://e.test/a"])
        self.assertIsNone(runner.call_args.kwargs["stdin"])

    def test_without_a_hook_the_http_error_propagates(self):
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", ""):
            with self.assertRaises(urllib.error.HTTPError):
                hbnscan.fetch_article("https://e.test/a")

    def test_hook_is_only_for_auth_failures_unless_always_is_set(self):
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(500)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan, "FETCH_CMD_ALWAYS", False):
            with self.assertRaises(urllib.error.HTTPError):
                hbnscan.fetch_article("https://e.test/a")
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(500)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan, "FETCH_CMD_ALWAYS", True), \
             mock.patch.object(hbnscan.subprocess, "Popen",
                               return_value=FakeProcess(b"ok")):
            self.assertIn("ok", hbnscan.fetch_article("https://e.test/a"))

    def test_hook_failure_reports_the_exit_code_and_last_stderr_line(self):
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan.subprocess, "Popen",
                               return_value=FakeProcess(b"", b"warn\nboom", returncode=3)):
            with self.assertRaises(hbnscan.FeedError) as caught:
                hbnscan.fetch_article("https://e.test/a")
        self.assertIn("exit 3", str(caught.exception))
        self.assertIn("boom", str(caught.exception))

    def test_missing_binary_is_a_clear_error(self):
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "definitely-not-installed"), \
             mock.patch.object(hbnscan.subprocess, "Popen", side_effect=FileNotFoundError):
            with self.assertRaises(hbnscan.FeedError) as caught:
                hbnscan.fetch_article("https://e.test/a")
        self.assertIn("not found", str(caught.exception))

    def test_oversized_hook_output_is_rejected_while_streaming(self):
        process = FakeProcess(b"x" * (hbnscan.MAX_ARTICLE_BYTES + 1))
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan.subprocess, "Popen", return_value=process):
            with self.assertRaises(hbnscan.FeedError) as caught:
                hbnscan.fetch_article("https://e.test/a")
        self.assertIn("size cap", str(caught.exception))
        self.assertTrue(process.killed, "an oversized fetcher must be stopped, not drained")

    def test_a_hung_fetcher_is_killed_and_reported_as_a_timeout(self):
        process = FakeProcess(b"", hang=True)
        with mock.patch.object(hbnscan.hbnet, "open_url", side_effect=http_error(403)), \
             mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan, "FETCH_CMD_TIMEOUT", 2), \
             mock.patch.object(hbnscan.subprocess, "Popen", return_value=process):
            with self.assertRaises(hbnscan.FeedError) as caught:
                hbnscan.fetch_article("https://e.test/a")
        self.assertIn("timed out", str(caught.exception))
        self.assertTrue(process.killed)

    def test_a_failed_hook_does_not_block_the_host_for_the_whole_scan(self):
        lines = []
        verdict = hbnscan._empty_verdict(flagged=1)
        hbnscan._BLOCKED_HOSTS.clear()
        item = {"title": "t", "link": "https://blocked.test/a", "description": "d"}
        with mock.patch.object(hbnscan, "FETCH_CMD", "fetcher"), \
             mock.patch.object(hbnscan, "fetch_article", side_effect=http_error(403)):
            hbnscan.deep_read_item(item, dict(verdict), lines.append)
        self.assertNotIn("blocked.test", hbnscan._BLOCKED_HOSTS)
        self.assertTrue(any("fetch hook" in line for line in lines))

    def test_without_a_hook_the_host_is_remembered_as_blocked(self):
        lines = []
        verdict = hbnscan._empty_verdict(flagged=1)
        hbnscan._BLOCKED_HOSTS.clear()
        item = {"title": "t", "link": "https://blocked.test/a", "description": "d"}
        with mock.patch.object(hbnscan, "FETCH_CMD", ""), \
             mock.patch.object(hbnscan, "fetch_article", side_effect=http_error(403)):
            hbnscan.deep_read_item(item, dict(verdict), lines.append)
        self.assertEqual(hbnscan._BLOCKED_HOSTS.get("blocked.test"), 403)


class ConnectionTest(unittest.TestCase):
    def test_every_connection_is_wal_with_a_generous_busy_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            with mock.patch.object(hbnscan, "DB_PATH", path):
                conn = hbnscan.connect()
                try:
                    settings = hbnscan.connection_settings(conn)
                finally:
                    conn.close()
        self.assertEqual(settings["journal_mode"], "wal")
        self.assertEqual(settings["busy_timeout"], hbnscan.BUSY_TIMEOUT_MS)
        self.assertEqual(settings["synchronous"], 1)      # NORMAL


if __name__ == "__main__":
    unittest.main()
