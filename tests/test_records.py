"""Canonical records: stable identity, honest provenance, and history that survives re-grading."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import hbnscan
import hbrecords


class IdentityTest(unittest.TestCase):
    def test_every_identifier_says_what_it_names(self):
        pairs = {
            "source": hbrecords.source_id("https://feed.test/rss"),
            "source_occurrence": hbrecords.occurrence_id("https://feed.test/rss", "https://e.test/a"),
            "article": hbrecords.article_id("https://e.test/a"),
            "story": hbrecords.story_id("u:abc"),
            "claim": hbrecords.claim_id("Acme was fined"),
            "signal": hbrecords.signal_id(hbrecords.article_id("https://e.test/a")),
            "assessment": hbrecords.assessment_id("sig:1", "p", "heuristic", "policy", "high",
                                                  "text"),
            "alert": hbrecords.alert_id("sig:1", "webhook", "high"),
            "dossier": hbrecords.dossier_id(7),
            "action": hbrecords.action_id(9),
        }
        for kind, value in pairs.items():
            self.assertEqual(value.split(":", 1)[0], hbrecords.KIND_PREFIXES[kind], kind)
            self.assertGreater(len(value.split(":", 1)[1]), 8, kind)

    def test_ids_are_stable_and_part_order_matters(self):
        self.assertEqual(hbrecords.article_id("https://e.test/a"),
                         hbrecords.article_id("https://e.test/a"))
        self.assertNotEqual(hbrecords.make_id("article", "a", "bc"),
                            hbrecords.make_id("article", "ab", "c"))
        self.assertNotEqual(hbrecords.article_id("https://e.test/a"),
                            hbrecords.article_id("https://e.test/b"))

    def test_an_unknown_kind_is_a_programming_error_not_a_guess(self):
        with self.assertRaises(ValueError):
            hbrecords.make_id("teapot", "x")

    def test_a_tracking_parameter_does_not_create_a_second_article(self):
        left = hbrecords.article_id("https://www.Example.com/a/?utm_source=x&b=2#top")
        right = hbrecords.article_id("http://example.com/a?b=2")
        self.assertEqual(left, right)

    def test_the_same_article_in_two_feeds_is_one_article_and_two_occurrences(self):
        link = "https://e.test/story"
        self.assertEqual(hbrecords.article_id(link), hbrecords.article_id(link))
        self.assertNotEqual(hbrecords.occurrence_id("https://a.test/rss", link),
                            hbrecords.occurrence_id("https://b.test/rss", link))

    def test_an_empty_story_has_no_identity_to_invent(self):
        with self.assertRaises(ValueError):
            hbrecords.story_id("", "")


class ContractTest(unittest.TestCase):
    def test_every_kind_round_trips_through_json(self):
        samples = [
            hbrecords.Source(id=hbrecords.source_id("https://f.test/rss"),
                             name="Feed", feed_url="https://f.test/rss"),
            hbrecords.SourceOccurrence(id=hbrecords.occurrence_id("f", "g"), feed_url="f"),
            hbrecords.Article(id=hbrecords.article_id("https://e.test/a"),
                              canonical_url="https://e.test/a", title="A"),
            hbrecords.Story(id=hbrecords.story_id("u:1"), title="S"),
            hbrecords.Claim(id=hbrecords.claim_id("x"), statement="x"),
            hbrecords.Signal(id=hbrecords.signal_id("art:1"), title="T"),
            hbrecords.Assessment(id=hbrecords.assessment_id("sig:1", "p", "heuristic",
                                                            "policy", "low", "a")),
            hbrecords.Alert(id=hbrecords.alert_id("sig:1", "email", "high")),
            hbrecords.Dossier(id=hbrecords.dossier_id(1), name="D"),
            hbrecords.Action(id=hbrecords.action_id(1), title="A"),
        ]
        for record in samples:
            rebuilt = hbrecords.from_json(record.to_json())
            self.assertEqual(type(rebuilt), type(record))
            self.assertEqual(rebuilt.id, record.id)
            self.assertEqual(rebuilt.contract, hbrecords.CONTRACT_VERSION)

    def test_a_list_becomes_a_tuple_again_after_a_round_trip(self):
        signal = hbrecords.Signal(id=hbrecords.signal_id("art:1"), entities=("Acme", "FTC"))
        rebuilt = hbrecords.from_json(signal.to_json())
        self.assertIsInstance(rebuilt.entities, tuple)
        self.assertEqual(rebuilt.entities, ("Acme", "FTC"))

    def test_a_newer_contract_is_refused_rather_than_misread(self):
        payload = {"kind": "signal", "id": "sig:1", "contract": hbrecords.CONTRACT_VERSION + 1}
        with self.assertRaises(ValueError) as caught:
            hbrecords.from_dict(payload)
        self.assertIn("understands", str(caught.exception))

    def test_an_older_contract_is_kept_so_it_can_be_migrated(self):
        payload = {"kind": "signal", "id": "sig:1", "contract": 0, "title": "old"}
        self.assertEqual(hbrecords.from_dict(payload).contract, 0)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            hbrecords.from_dict({"kind": "horoscope", "id": "x:1"})

    def test_an_envelope_carries_the_discriminator_up_front(self):
        article = hbrecords.Article(id=hbrecords.article_id("https://e.test/a"))
        wrapped = hbrecords.envelope(article)
        self.assertEqual((wrapped["kind"], wrapped["contract"]), ("article", 1))
        self.assertEqual(wrapped["record"]["id"], article.id)


class AdapterTest(unittest.TestCase):
    ROW = {
        "item_hash": "h1", "title": "Acme fined", "link": "https://www.e.test/a?utm_source=x",
        "feed_url": "https://feed.test/rss", "flagged": 1, "summary": "[Watchlist] acme.",
        "seen_at": "2026-11-13T10:00:00", "category": "policy", "severity": "high",
        "entities": json.dumps(["Acme"]), "action": "Read the order",
        "facts": json.dumps(["a fact"]), "confidence": 0.9, "deep_read": 1, "dismissed": 0,
        "published": "2026-11-12T09:00:00", "source_layer": "watchlist",
        "verdict_reason": "rule: acme", "cluster_key": "u:abc", "title_key": "t:abc",
        "excerpt": "Feed summary text",
    }

    def test_the_projections_agree_on_identity(self):
        article = hbrecords.article_from_row(self.ROW)
        occurrence = hbrecords.occurrence_from_row(self.ROW)
        signal = hbrecords.signal_from_row(self.ROW)
        assessment = hbrecords.assessment_from_row(self.ROW, profile="p")

        self.assertEqual(article.id, hbrecords.article_id(self.ROW["link"]))
        self.assertEqual(occurrence.article_id, article.id)
        self.assertEqual(signal.article_id, article.id)
        self.assertEqual(assessment.article_id, article.id)
        self.assertEqual(signal.id, assessment.signal_id)
        self.assertEqual(signal.occurrence_ids, (occurrence.id,))
        self.assertEqual(occurrence.source_id, hbrecords.source_id(self.ROW["feed_url"]))

    def test_the_article_says_how_complete_it_is(self):
        self.assertEqual(hbrecords.article_from_row(self.ROW).completeness, "partial")
        shallow = dict(self.ROW, deep_read=0, excerpt="")
        self.assertEqual(hbrecords.article_from_row(shallow).completeness, "headline")
        self.assertEqual(hbrecords.article_from_row(shallow).retrieval_outcome,
                         "not_extracted")

    def test_the_feed_summary_is_kept_as_evidence(self):
        self.assertEqual(hbrecords.article_from_row(self.ROW).summary, "Feed summary text")

    def test_a_signal_keeps_its_declared_layer_and_reason(self):
        signal = hbrecords.signal_from_row(self.ROW)
        self.assertEqual(signal.layer, "watchlist")
        self.assertEqual(signal.reason, "rule: acme")
        self.assertEqual(signal.entities, ("Acme",))
        self.assertEqual(signal.facts, ("a fact",))

    def test_a_sparse_row_does_not_raise(self):
        sparse = {"item_hash": "h2", "title": "t", "link": "", "feed_url": "",
                  "flagged": 0}
        signal = hbrecords.signal_from_row(sparse)
        self.assertEqual(signal.entities, ())
        self.assertEqual(signal.severity, "")


class AssessmentHistoryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name, value in (("DB_PATH", self.root / "state.db"),
                            ("SETTINGS_PATH", self.root / "settings.json"),
                            ("WATCHLIST_PATH", self.root / "watchlist.txt")):
            patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.connect()
        self.addCleanup(self.conn.close)

    def add_row(self, title="Acme fined", layer="heuristic", summary="[Local Heuristic] x."):
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason) VALUES "
            "(?, ?, 'https://e.test/a', 'https://feed.test/rss', 1, ?, "
            "'2026-11-13T10:00:00', 'policy', 'medium', '{}', '', '[]', 0.5, 0, 0, "
            "'2026-11-12T09:00:00', ?, 'core: acme')",
            (hbnscan.item_hash(title, "https://e.test/a"), title, summary, layer))
        self.conn.commit()
        return hbnscan.item_hash(title, "https://e.test/a")

    def test_the_schema_carries_the_history_table(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0],
                         hbnscan.SCHEMA_VERSION)
        self.assertTrue(self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assessment_history'"
        ).fetchone())

    def test_recording_appends_and_chains(self):
        item = self.add_row()
        first = hbnscan.record_assessment(
            self.conn, item, link="https://e.test/a", method="heuristic", layer="heuristic",
            category="policy", severity="medium", flagged=1, confidence=0.5,
            analysis="first judgement", reason="core: acme")
        second = hbnscan.record_assessment(
            self.conn, item, link="https://e.test/a", method="watchlist", layer="watchlist",
            category="policy", severity="high", flagged=1, confidence=0.5,
            analysis="second judgement", reason="rule: acme")
        history = hbnscan.assessment_history(self.conn, item)
        self.assertEqual(len(history), 2)
        self.assertEqual(second.supersedes, first.id)
        self.assertEqual({row["analysis"] for row in history},
                         {"first judgement", "second judgement"})

    def test_reclassifying_appends_instead_of_rewriting_history(self):
        item = self.add_row()
        hbnscan.record_assessment(
            self.conn, item, link="https://e.test/a", method="heuristic", layer="heuristic",
            category="policy", severity="medium", flagged=1, confidence=0.5,
            analysis="[Local Heuristic] x.", reason="core: acme")
        before = self.conn.execute(
            "SELECT link, facts, title FROM seen_items WHERE item_hash = ?",
            (item,)).fetchone()

        rule = {"label": "acme", "category": "policy", "severity": "high", "terms": ("acme",)}
        with mock.patch.object(hbnscan, "load_watchlist", return_value=[rule]):
            changed = hbnscan.reclassify(self.conn, apply=True, log_callback=lambda _line: None)
        self.assertEqual(changed, 1)

        history = hbnscan.assessment_history(self.conn, item)
        self.assertEqual(len(history), 2, history)
        self.assertEqual(history[0]["method"], "watchlist")
        self.assertEqual(history[0]["severity"], "high")
        self.assertTrue(history[0]["supersedes"], "the new judgement must name its predecessor")
        # The evidence behind the signal is untouched by the re-grade.
        after = self.conn.execute(
            "SELECT link, facts, title FROM seen_items WHERE item_hash = ?", (item,)).fetchone()
        self.assertEqual(tuple(after), tuple(before))

    def test_a_dry_run_records_nothing(self):
        item = self.add_row()
        rule = {"label": "acme", "category": "policy", "severity": "high", "terms": ("acme",)}
        with mock.patch.object(hbnscan, "load_watchlist", return_value=[rule]):
            changed = hbnscan.reclassify(self.conn, apply=False,
                                         log_callback=lambda _line: None)
        self.assertEqual(changed, 1)
        self.assertEqual(hbnscan.assessment_history(self.conn, item), [])

    def test_two_rows_sharing_one_link_keep_separate_histories(self):
        # The same URL under two headlines is two stored rows with one article identity; one
        # row's assessment must never replace the other's.
        first = self.add_row(title="Acme fined")
        second = self.add_row(title="Acme fined again")
        for item in (first, second):
            hbnscan.record_assessment(self.conn, item, link="https://e.test/a",
                                      method="heuristic", layer="heuristic",
                                      category="policy", severity="medium",
                                      analysis="same text")
        self.conn.commit()
        self.assertEqual(len(hbnscan.assessment_history(self.conn, first)), 1)
        self.assertEqual(len(hbnscan.assessment_history(self.conn, second)), 1)
        signal_ids = {row["id"] for row in self.conn.execute(
            "SELECT id FROM assessment_history")}
        self.assertEqual(len(signal_ids), 2, "ids collided across rows")

    def test_history_for_a_row_that_was_never_assessed_is_empty_not_an_error(self):
        self.assertEqual(hbnscan.assessment_history(self.conn, "no-such-item"), [])

    def test_pruning_removes_history_with_the_row_and_keeps_flagged_history(self):
        old = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, dismissed) VALUES "
            "('keep', 'kept', 'https://e.test/k', 'https://f.test/rss', 1, '', ?, 'policy', "
            "'high', 0)", (old,))
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, dismissed) VALUES "
            "('drop', 'dropped', 'https://e.test/d', 'https://f.test/rss', 0, '', ?, "
            "'policy', 'low', 0)", (old,))
        for item_hash_value, link in (("keep", "https://e.test/k"),
                                      ("drop", "https://e.test/d")):
            hbnscan.record_assessment(self.conn, item_hash_value, link=link,
                                      method="heuristic", layer="heuristic",
                                      category="policy", severity="low")
        self.conn.commit()

        hbnscan.prune_old_items(self.conn, days=90)
        self.conn.commit()
        self.assertEqual(len(hbnscan.assessment_history(self.conn, "keep")), 1)
        self.assertEqual(hbnscan.assessment_history(self.conn, "drop"), [])


if __name__ == "__main__":
    unittest.main()
