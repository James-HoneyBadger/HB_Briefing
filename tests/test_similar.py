import tempfile
import unittest
from pathlib import Path
from unittest import mock

import hbnscan
import hbsimilar

BODY = ("The regulator said on Thursday that Acme Fiber must open its poles to competitors "
        "within ninety days, ending a two-year dispute over access rates in rural counties. "
        "Acme said it would review the order and did not rule out an appeal.")


class SimilarityModuleTest(unittest.TestCase):
    def test_shingles_normalise_case_and_punctuation(self):
        left = hbsimilar.shingles("Acme, WINS the Permit today!")
        right = hbsimilar.shingles("acme wins the permit today")
        self.assertEqual(left, right)
        self.assertEqual(left, {"acme wins the permit", "wins the permit today"})

    def test_short_text_becomes_one_shingle(self):
        self.assertEqual(hbsimilar.shingles("two words"), {"two words"})
        self.assertEqual(hbsimilar.shingles(""), set())

    def test_rewritten_wire_copy_scores_above_the_default_threshold(self):
        original = f"Acme must open its poles, regulator says. {BODY}"
        rewrite = f"Regulator orders Acme Fiber to share poles. {BODY}"
        self.assertEqual(hbsimilar.similarity(original, original), 1.0)
        self.assertGreaterEqual(hbsimilar.similarity(original, rewrite), 0.7)

    def test_unrelated_stories_score_low(self):
        other = ("A bank agreed to pay a penalty over mortgage servicing failures in three "
                 "states, the consumer bureau said, without admitting the findings.")
        self.assertLess(hbsimilar.similarity(BODY, other), 0.2)

    def test_signatures_and_bands_are_deterministic(self):
        shingles = hbsimilar.shingles(BODY)
        first = hbsimilar.signature(shingles)
        second = hbsimilar.signature(hbsimilar.shingles(BODY))
        self.assertEqual(first, second)
        self.assertEqual(len(first), hbsimilar.SIGNATURE_SIZE)
        bands = hbsimilar.bands(first)
        self.assertEqual(len(bands), hbsimilar.BAND_COUNT)
        self.assertEqual(bands, hbsimilar.bands(first))

    def test_bands_differ_for_different_text(self):
        left = dict(hbsimilar.bands(hbsimilar.signature(hbsimilar.shingles(BODY))))
        right = dict(hbsimilar.bands(hbsimilar.signature(
            hbsimilar.shingles("Completely different subject: a recall of children's toys."))))
        self.assertNotEqual(left, right)

    def test_jaccard_edges(self):
        self.assertEqual(hbsimilar.jaccard(set(), {"a"}), 0.0)
        self.assertEqual(hbsimilar.jaccard({"a", "b"}, {"a", "b"}), 1.0)
        self.assertEqual(hbsimilar.jaccard({"a"}, {"b"}), 0.0)


class SimilarityIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        for name, value in (("DB_PATH", root / "state.db"),
                            ("SETTINGS_PATH", root / "s.json"),
                            ("REPORT_PATH", root / "r.md")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)

    def scan(self, items):
        def fetch(_url, _etag, _modified):
            return items, "e", "l"

        budget = hbnscan._ScanBudget(0, 0, False)
        lines = []
        with mock.patch.object(hbnscan, "fetch_feed", side_effect=fetch), \
             mock.patch.object(hbnscan, "deepseek_configured", return_value=False), \
             mock.patch.object(hbnscan, "_is_trusted_source", return_value=False):
            hbnscan._scan_feed(self.conn, "https://feed.test/rss", None, None, budget, None,
                               lines.append)
        return lines

    def test_rewritten_wire_story_is_grouped(self):
        lines = self.scan([
            {"title": "Acme must open its poles, regulator says",
             "link": "https://a.test/1", "description": BODY, "published": ""},
            {"title": "Regulator orders Acme Fiber to share poles",
             "link": "https://b.test/2", "description": BODY, "published": ""},
        ])
        keys = {row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items")}
        self.assertEqual(len(keys), 1, "the rewrite should join the original story")
        self.assertTrue(any("[SIMILAR]" in line for line in lines), lines)

    def test_unrelated_items_stay_separate(self):
        self.scan([
            {"title": "Acme must open its poles, regulator says",
             "link": "https://a.test/1", "description": BODY, "published": ""},
            {"title": "Toy recall expanded after choking reports",
             "link": "https://c.test/3",
             "description": "A retailer expanded a recall of magnetic toys after three "
                            "reported injuries, the safety commission said.",
             "published": ""},
        ])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(DISTINCT cluster_key) FROM seen_items").fetchone()[0], 2)

    def test_threshold_zero_disables_the_pass(self):
        with mock.patch.object(hbnscan, "SIMILARITY_THRESHOLD", 0):
            self.scan([
                {"title": "Acme must open its poles, regulator says",
                 "link": "https://a.test/1", "description": BODY, "published": ""},
                {"title": "Regulator orders Acme Fiber to share poles",
                 "link": "https://b.test/2", "description": BODY, "published": ""},
            ])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(DISTINCT cluster_key) FROM seen_items").fetchone()[0], 2)

    def test_bands_are_indexed_and_pruned_with_the_row(self):
        self.scan([{"title": "Acme must open its poles, regulator says",
                    "link": "https://a.test/1", "description": BODY, "published": ""}])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM story_bands").fetchone()[0], hbsimilar.BAND_COUNT)
        # An unflagged, old row is pruned together with its bands.
        self.conn.execute(
            "UPDATE seen_items SET flagged = 0, seen_at = '2000-01-01T00:00:00'")
        self.conn.commit()
        hbnscan.prune_old_items(self.conn, days=1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM story_bands").fetchone()[0], 0)

    def test_existing_rows_are_indexed_by_migration(self):
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, flagged, summary, seen_at, "
            "category, severity, dismissed) VALUES ('old', ?, 'https://old.test/1', 1, "
            "'[Local Heuristic] x', '2026-01-01T00:00:00', 'policy', 'low', 0)",
            ("Acme must open its poles, regulator says",))
        self.conn.commit()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM story_bands").fetchone()[0], 0)
        hbnscan._backfill_story_bands(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM story_bands").fetchone()[0], hbsimilar.BAND_COUNT)


if __name__ == "__main__":
    unittest.main()
