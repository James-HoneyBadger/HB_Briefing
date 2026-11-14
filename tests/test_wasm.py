"""The WebAssembly app is not a rewrite: it runs the engine, so these tests check the engine
reaches the browser intact — input shapes, verdicts, grouping and every export format.

The page itself is HTML + Pyodide; what can be proved without a browser is proved here, under
CPython, which is the same code the tab executes.
"""
import csv
import io
import json
import unittest
from unittest import mock

import hbwasm

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test feed</title>
  <item><title>FTC sues Acme over router fees</title>
        <link>https://example.org/a</link>
        <description>The regulator alleges Acme billed for returned equipment.</description>
        <pubDate>Tue, 12 Nov 2026 09:00:00 GMT</pubDate></item>
  <item><title>FCC opens 3.7 GHz proceeding</title>
        <link>https://example.org/b</link>
        <description>The commission seeks comment on the band.</description>
        <pubDate>Mon, 10 Nov 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""

OPML = """<?xml version="1.0"?>
<opml version="2.0"><head><title>Feeds</title></head><body>
  <outline text="FTC"><outline type="rss" text="FTC press" xmlUrl="https://ftc.gov/feed.xml"/></outline>
  <outline type="rss" text="CFPB" xmlUrl="https://consumerfinance.gov/feed/"/>
</body></opml>"""

EXPORT_CSV = (
    "title,link,feed_url,category,severity,source_layer,summary,published\n"
    "Acme fined over router fees,https://example.org/a,https://ftc.gov/feed.xml,"
    "litigation,high,watchlist,Penalty issued.,2026-11-12\n"
    "Toy recall expanded,https://example.org/c,https://cpsc.gov/feed.xml,"
    "consumer_product,medium,heuristic,Recall reported.,2026-11-11\n"
)


class SampleTest(unittest.TestCase):
    def setUp(self):
        self.result = hbwasm.analyse(hbwasm.sample(), "sample.json")

    def test_sample_is_scored_and_grouped(self):
        summary = self.result["summary"]
        self.assertEqual(summary["items"], 4)
        self.assertEqual(summary["stories"], 3, "the wire rewrite must join the original")
        self.assertEqual(summary["rewrites"], 1)
        self.assertTrue(self.result["rescored"], "a plain item list is scored locally")

    def test_the_grouped_row_records_its_score(self):
        grouped = [row for row in self.result["rows"] if row["cluster_size"] > 1]
        self.assertEqual(len(grouped), 2)
        scores = [row["similarity"] for row in grouped if row["similarity"]]
        self.assertEqual(len(scores), 1)
        self.assertGreaterEqual(scores[0], 0.7)

    def test_rows_carry_what_the_table_shows(self):
        row = self.result["rows"][0]
        for field in ("title", "category", "severity", "layer", "priority", "assessment",
                      "freshness", "cluster", "cluster_size"):
            self.assertIn(field, row)
        self.assertNotIn("title_key", row, "internal grouping keys must not reach the page")
        self.assertNotIn("excerpt", row)

    def test_undated_items_are_labelled_not_guessed(self):
        undated = [row for row in self.result["rows"] if not row["published"]]
        self.assertEqual(len(undated), 1)
        self.assertEqual(undated[0]["freshness"], "undated")
        self.assertTrue(undated[0]["undated"])

    def test_grouping_can_be_switched_off(self):
        result = hbwasm.analyse(hbwasm.sample(), threshold=0)
        self.assertEqual(result["summary"]["stories"], 4)


class InputTest(unittest.TestCase):
    def test_feed_xml_becomes_items(self):
        document = hbwasm.load_document(RSS, "feed.xml")
        self.assertEqual(document["kind"], "feed")
        self.assertEqual(len(document["items"]), 2)
        self.assertEqual(document["items"][0]["title"], "FTC sues Acme over router fees")
        self.assertTrue(document["items"][0]["published"].startswith("2026-11-12"))

    def test_a_feed_is_scored_when_analysed(self):
        import hbnscan
        # A browser tab has no watchlist or topic file; pin that, or the checkout's own
        # files would decide what this test sees.
        with mock.patch.object(hbnscan, "load_watchlist", return_value=[]), \
             mock.patch.object(hbnscan, "load_topics", return_value=[]):
            result = hbwasm.analyse(RSS, "feed.xml")
        self.assertTrue(result["rescored"])
        self.assertEqual(result["summary"]["items"], 2)
        self.assertTrue(all(row["layer"] == "heuristic" for row in result["rows"]))

    def test_opml_yields_feeds_not_items(self):
        document = hbwasm.load_document(OPML, "feeds.opml")
        self.assertEqual(document["kind"], "opml")
        self.assertEqual(document["items"], [])
        self.assertEqual([feed["url"] for feed in document["feeds"]],
                         ["https://ftc.gov/feed.xml", "https://consumerfinance.gov/feed/"])

    def test_a_desktop_export_keeps_its_verdicts(self):
        result = hbwasm.analyse(EXPORT_CSV, "export.csv")
        self.assertEqual(result["kind"], "csv")
        self.assertFalse(result["rescored"], "an export is already scored")
        categories = {row["title"]: row["category"] for row in result["rows"]}
        self.assertEqual(categories["Acme fined over router fees"], "litigation")
        layers = {row["title"]: row["layer"] for row in result["rows"]}
        self.assertEqual(layers["Acme fined over router fees"], "watchlist")

    def test_plain_headlines_are_one_item_each(self):
        document = hbwasm.load_document("FTC sues Acme\nCFPB fines bank\n# a comment\n")
        self.assertEqual(document["kind"], "lines")
        self.assertEqual(len(document["items"]), 2)

    def test_json_object_with_rows_is_accepted(self):
        payload = json.dumps({"rows": [{"title": "Acme fined", "summary": "Penalty."}]})
        document = hbwasm.load_document(payload)
        self.assertEqual(document["kind"], "export")
        self.assertEqual(document["items"][0]["title"], "Acme fined")
        self.assertEqual(document["items"][0]["description"], "Penalty.")

    def test_bad_input_is_reported_not_swallowed(self):
        with self.assertRaises(ValueError):
            hbwasm.load_document("   ")
        with self.assertRaises(ValueError):
            hbwasm.load_document("{not json}")
        with self.assertRaises(ValueError):
            hbwasm.load_document("<rss><channel>")

    def test_the_item_cap_is_applied_and_reported(self):
        payload = json.dumps([{"title": f"Headline {index}"} for index in range(6)])
        with mock.patch.object(hbwasm, "MAX_ITEMS", 3):
            result = hbwasm.analyse(payload)
        self.assertEqual(len(result["rows"]), 3)
        self.assertTrue(any("first 3" in note for note in result["notes"]))


class ProfileTest(unittest.TestCase):
    def test_a_profile_can_come_from_the_page(self):
        profile = {"name": "Quiet", "strong_signals": ["zebra"]}
        result = hbwasm.analyse(json.dumps([{"title": "Zebra crossing approved"}]),
                                profile=json.dumps(profile))
        self.assertEqual(result["summary"]["items"], 1)
        self.assertEqual(result["rows"][0]["severity"], "medium")


class OutputTest(unittest.TestCase):
    def setUp(self):
        self.result = hbwasm.analyse(hbwasm.sample(), "sample.json")

    def test_csv_has_the_documented_header(self):
        text = hbwasm.to_csv(self.result["rows"])
        header = text.splitlines()[0].split(",")
        self.assertEqual(header, list(hbwasm.ROW_FIELDS))
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["title"], self.result["rows"][0]["title"])

    def test_markdown_is_a_readable_digest(self):
        text = hbwasm.to_markdown(self.result)
        self.assertIn("# HB Briefing (browser session)", text)
        self.assertIn("## Signals", text)
        self.assertIn("FTC sues Acme Fiber over hidden router fees", text)
        self.assertIn("Grouped with 1 rewrite", text)
        self.assertIn("no data left this machine", text)

    def test_json_output_round_trips(self):
        payload = json.loads(hbwasm.to_json(self.result))
        self.assertEqual(payload["summary"]["items"], 4)
        self.assertEqual(len(payload["rows"]), 4)

    def test_summary_counts_match_the_rows(self):
        summary = self.result["summary"]
        self.assertEqual(summary["flagged"], sum(1 for row in self.result["rows"] if row["flagged"]))
        self.assertEqual(sum(summary["by_severity"].values()), 4)
        self.assertEqual(summary["span"]["first"], "2026-11-10")


class EngineBridgeTest(unittest.TestCase):
    def test_the_engine_is_the_real_module(self):
        import hbnscan
        self.assertIs(hbwasm.engine(), hbnscan)

    def test_missing_dotenv_is_tolerated(self):
        """A browser tab has no .env, and Pyodide should not need the wheel to find that out."""
        import sys
        import types
        saved = {name: sys.modules.pop(name) for name in list(sys.modules)
                 if name in ("dotenv", "hbnscan") or name.startswith("hbnscan.")}
        have_real = "dotenv" in saved
        try:
            if have_real:
                sys.modules["dotenv"] = None          # forces ImportError on `import dotenv`
            hbwasm._ENGINE = None
            engine = hbwasm.engine()
            self.assertTrue(hasattr(engine, "heuristic_verdict"))
            self.assertIsInstance(sys.modules.get("dotenv"), types.ModuleType)
        finally:
            for name in ("dotenv", "hbnscan"):
                sys.modules.pop(name, None)
            sys.modules.update(saved)
            hbwasm._ENGINE = None


if __name__ == "__main__":
    unittest.main()
