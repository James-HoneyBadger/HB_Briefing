"""Printed HTML names the file it came from.

A printed briefing is a paper artefact: unless the page says which export produced it, the
reader has no way back to the file. These tests pin the three cases that matter — a file on
disk, a browser download, and no source at all — plus the escape that keeps a path from
breaking the markup it sits in.
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan
import hbworkspace

ROW_SQL = (
    "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, seen_at,"
    " category, severity, entities, action, facts, confidence, deep_read, dismissed,"
    " published, source_layer, verdict_reason) VALUES ('h1','Acme fined',"
    "'https://e.test/a','https://f.test/rss',1,'[Watchlist] acme. Penalty issued.',"
    "'2026-11-13T10:00:00','litigation','high','{}','Do a thing','[]',0.9,1,0,"
    "'2026-11-12','watchlist','rule: acme')"
)


class FooterAssetsTest(unittest.TestCase):
    def test_a_path_becomes_name_and_full_path(self):
        css, markup = hbnscan.print_footer_assets("/tmp/out/briefing.html")
        self.assertIn('class="print-footer"', markup)
        self.assertIn("briefing.html", markup)
        self.assertIn(str(Path("/tmp/out/briefing.html").resolve()), markup)
        self.assertIn("@media print", css)
        self.assertIn("counter(pages)", css)
        self.assertIn("position: fixed", css,
                      "the fixed block is what repeats on every printed page")

    def test_page_numbers_are_css_only_so_a_wrong_count_cannot_be_printed(self):
        css, _markup = hbnscan.print_footer_assets("briefing.html")
        self.assertIn("counter(page)", css)
        self.assertIn("@bottom-right", css)
        self.assertNotIn("Page 1 of", css, "the count must never be hardcoded")

    def test_a_download_names_the_file_without_inventing_a_path(self):
        _css, markup = hbnscan.print_footer_assets(None, "hb-briefing-20261114-1830.html")
        self.assertIn("hb-briefing-20261114-1830.html", markup)
        self.assertIn("downloaded copy", markup)

    def test_no_source_means_no_footer(self):
        self.assertEqual(hbnscan.print_footer_assets(), ("", ""))

    def test_a_hostile_path_cannot_break_the_markup(self):
        _css, markup = hbnscan.print_footer_assets('/tmp/x"><script>alert(1)</script>.html')
        self.assertNotIn("<script>", markup)
        self.assertIn("&quot;&gt;&lt;script&gt;", markup)


class ExportHtmlTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name, value in (("DB_PATH", self.root / "state.db"),
                            ("SETTINGS_PATH", self.root / "settings.json")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.conn.execute(ROW_SQL)
        self.conn.commit()
        self.addCleanup(self.conn.close)

    def test_the_signal_export_names_its_own_file(self):
        target = self.root / "exports" / "briefing.html"
        target.parent.mkdir()
        path, count = hbnscan.export_items(self.conn, target, "html")
        text = Path(path).read_text(encoding="utf-8")
        self.assertEqual(count, 1)
        self.assertIn('<footer class="print-footer">', text)
        self.assertIn("briefing.html", text)
        self.assertIn(str(target.resolve()), text)
        self.assertIn("Acme fined", text, "the footer must not displace the content")

    def test_other_formats_do_not_grow_a_footer(self):
        for name in ("briefing.md", "briefing.csv", "briefing.json", "briefing.txt"):
            path, _count = hbnscan.export_items(self.conn, self.root / name)
            text = Path(path).read_text(encoding="utf-8")
            self.assertNotIn("print-footer", text, name)

    def test_a_dossier_report_names_its_own_file(self):
        dossier_id = hbworkspace.create_dossier(self.conn, "Acme", "What is happening?")
        hbworkspace.add_item(self.conn, dossier_id, "h1")
        target = self.root / "acme.html"
        path, _count = hbworkspace.export_report(self.conn, dossier_id, target, "html")
        text = Path(path).read_text(encoding="utf-8")
        self.assertIn('<footer class="print-footer">', text)
        self.assertIn(str(target.resolve()), text)

    def test_the_browser_download_uses_the_name_not_a_local_path(self):
        body = hbnscan.render_html(
            hbnscan.export_rows(self.conn), title="t",
            download_name="hb-briefing-20261114-1830.html")
        self.assertIn("hb-briefing-20261114-1830.html", body)
        self.assertIn("downloaded copy", body)
        self.assertNotIn(str(self.root), body)


class PageCssTest(unittest.TestCase):
    def test_the_footer_css_is_inside_the_page_style_block(self):
        body = hbnscan.render_html([], title="Empty", source_path="/tmp/empty.html")
        style = body.split("<style>", 1)[1].split("</style>", 1)[0]
        self.assertIn("@media print", style)
        self.assertIn(".print-footer", style)
        self.assertLess(body.index("</style>"), body.index('<footer class="print-footer">'),
                        "the footer belongs to the body, the rules to the head")


if __name__ == "__main__":
    unittest.main()
