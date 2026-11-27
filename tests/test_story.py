"""Story intelligence: typed relationships, honest independence, reversible correction."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import hbnscan
import hbworkspace


class StoryCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "state.db"
        for name, value in (("DB_PATH", self.db_path),
                            ("SETTINGS_PATH", Path(self.directory.name) / "s.json"),
                            ("load_settings", None)):
            if name == "load_settings":
                patch = mock.patch.object(hbnscan, name,
                                          return_value=dict(hbnscan.DEFAULT_SETTINGS))
            else:
                patch = mock.patch.object(hbnscan, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.conn = hbnscan.init_db()
        self.addCleanup(self.conn.close)

    def add(self, title, link, feed="https://feed.test/rss", published=None, seen_at=None):
        link = link
        published = published or (datetime.now() - timedelta(days=1)).isoformat(
            timespec="seconds")
        seen_at = seen_at or "2026-11-13T10:00:00"
        cluster_key, title_key = hbnscan.story_keys(title, link)
        item = hbnscan.item_hash(title, link)
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, category, severity, entities, action, facts, confidence, deep_read, "
            "dismissed, published, source_layer, verdict_reason, cluster_key, title_key) "
            "VALUES (?, ?, ?, ?, 1, '[Local Heuristic] matched.', ?, 'policy', 'high', "
            "'{}', '', '[]', NULL, 0, 0, ?, 'heuristic', '', ?, ?)",
            (item, title, link, feed, seen_at, published, cluster_key, title_key))
        self.conn.commit()
        return item, cluster_key, title_key


class RelationshipTest(StoryCase):
    def test_a_same_url_copy_is_a_duplicate_not_a_corroboration(self):
        first, key, _ = self.add("Acme fined", "https://a.test/story")
        second = "second"
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, published, cluster_key, title_key) VALUES (?, 'Acme is fined', "
            "'https://a.test/story', 'https://other.test/rss', 1, '', '2026-11-13T11:00:00', "
            "'2026-11-12', ?, ?)", (second, "u:placeholder", "t:placeholder"))
        adopted = hbnscan.adopt_cluster(self.conn, second, hbnscan.story_keys(
            "Acme is fined", "https://a.test/story")[0], hbnscan.story_keys(
            "Acme is fined", "https://a.test/story")[1])
        self.conn.commit()
        relations = hbnscan.story_relations(self.conn, adopted)
        self.assertEqual([entry["relation"] for entry in relations], ["duplicate"])
        self.assertEqual(relations[0]["method"], "canonical_url")
        self.assertEqual(relations[0]["other_hash"], first)

    def test_the_same_headline_under_another_url_is_syndication(self):
        first, _, _ = self.add("Acme wins permit - Reuters", "https://a.test/1")
        second = "syndicated"
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, published, cluster_key, title_key) VALUES (?, 'Acme wins permit', "
            "'https://b.test/2', 'https://feed.test/rss', 1, '', '2026-11-13T11:00:00', "
            "'2026-11-12', 'u:second', 't:second')", (second,))
        cluster_key, title_key = hbnscan.story_keys("Acme wins permit", "https://b.test/2")
        adopted = hbnscan.adopt_cluster(self.conn, second, cluster_key, title_key)
        self.conn.commit()
        relations = hbnscan.story_relations(self.conn, adopted)
        self.assertEqual([entry["relation"] for entry in relations], ["syndicated"])
        self.assertEqual(relations[0]["method"], "normalized_headline")
        self.assertEqual(relations[0]["other_hash"], first)

    def test_a_related_story_is_shown_but_never_merged(self):
        first, key, _ = self.add(
            "Acme fined", "https://a.test/1")
        self.conn.execute(
            "UPDATE seen_items SET entities = ? WHERE item_hash = ?",
            (json.dumps({"companies": ["Acme"]}), first))
        _, other_key, _ = self.add("Acme opens a factory", "https://b.test/2")
        self.conn.execute(
            "UPDATE seen_items SET entities = ? WHERE item_hash = ?",
            (json.dumps({"companies": ["Acme"]}), hbnscan.item_hash(
                "Acme opens a factory", "https://b.test/2")))
        self.conn.commit()
        related = hbnscan.related_stories(self.conn, key)
        self.assertEqual(related[0]["cluster_key"], other_key)
        self.assertIn("not merged", related[0]["note"])
        # The two stories are still two stories.
        keys = {row[0] for row in self.conn.execute("SELECT cluster_key FROM seen_items")}
        self.assertEqual(len(keys), 2)


class IndependenceTest(StoryCase):
    def test_one_delivery_host_is_not_corroboration(self):
        self.add("Acme fined", "https://a.test/1")
        key = hbnscan.story_keys("Acme fined", "https://a.test/1")[0]
        self.conn.execute("UPDATE seen_items SET cluster_key = ?", (key,))
        self.conn.commit()
        sources = hbnscan.story_sources(self.conn, key)
        self.assertEqual(sources["independence"], "single_publisher")
        self.assertIn("not independent corroboration", sources["note"])

    def test_several_hosts_are_not_claimed_to_be_independent(self):
        key = "u:shared"
        for feed, link in (("https://one.test/rss", "https://news-one.test/a"),
                           ("https://two.test/rss", "https://news-two.test/a")):
            self.conn.execute(
                "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
                "seen_at, published, cluster_key) VALUES (?, 't', ?, ?, 1, '', "
                "'2026-11-13T10:00:00', '2026-11-12', ?)",
                (f"h{feed}", link, feed, key))
        self.conn.commit()
        sources = hbnscan.story_sources(self.conn, key)
        self.assertEqual(sources["independence"], "multiple_hosts")
        self.assertEqual(len(sources["publisher_hosts"]), 2)
        self.assertIn("shared ownership", sources["note"])

    def test_an_unresolved_aggregator_link_means_unknown(self):
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, published, cluster_key) VALUES ('h1', 't', "
            "'https://news.google.com/rss/articles/CBMiabc', 'https://feed.test/rss', 1, '', "
            "'2026-11-13T10:00:00', '2026-11-12', 'u:x')")
        self.conn.commit()
        sources = hbnscan.story_sources(self.conn, "u:x")
        self.assertEqual(sources["independence"], "unknown")
        self.assertEqual(sources["unresolved"], 1)
        self.assertIn("cannot be assessed", sources["note"])

    def test_the_summary_carries_the_verdict(self):
        self.add("Acme fined", "https://a.test/1")
        key = hbnscan.story_keys("Acme fined", "https://a.test/1")[0]
        summary = hbnscan.cluster_summary(self.conn, key)
        self.assertIn("independence", summary)
        self.assertTrue(summary["independence_note"])


class CorrectionTest(StoryCase):
    def merge_pair(self):
        self.add("Acme wins permit", "https://a.test/1")
        self.add("Acme wins permit - Reuters", "https://b.test/2")
        keys = [row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items ORDER BY cluster_key")]
        return keys[0], keys[1]

    def test_a_merge_moves_members_and_undo_restores_them(self):
        first, second = self.merge_pair()
        moved = hbnscan.merge_stories(self.conn, first, second, note="same permit")
        self.assertEqual(moved, 1)
        self.assertEqual({row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items")}, {second})
        self.assertEqual(len(hbnscan.story_overrides(self.conn, second)), 1)

        restored = hbnscan.undo_story_change(self.conn, cluster_key=second)
        self.assertEqual(restored, 1)
        self.assertEqual({row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items")}, {first, second})
        self.assertEqual(hbnscan.story_overrides(self.conn), [])

    def test_a_split_isolates_one_row_and_undo_rejoins_it(self):
        title = "Acme wins permit"
        item, key, _ = self.add(title, "https://a.test/1")
        other = "other"
        self.conn.execute(
            "INSERT INTO seen_items (item_hash, title, link, feed_url, flagged, summary, "
            "seen_at, published, cluster_key) VALUES (?, 'Acme wins permit - wire', "
            "'https://b.test/2', 'https://feed.test/rss', 1, '', '2026-11-13T11:00:00', "
            "'2026-11-12', ?)", (other, key))
        self.conn.commit()

        new_key = hbnscan.split_from_story(self.conn, other, note="different permit")
        self.assertEqual(new_key, f"row:{other}")
        self.assertEqual(hbnscan.cluster_summary(self.conn, key)["count"], 1)

        self.assertEqual(hbnscan.undo_story_change(self.conn, item_hash_value=other), 1)
        self.assertEqual(hbnscan.cluster_summary(self.conn, key)["count"], 2)

    def test_detach_records_an_undoable_edit(self):
        title = "Acme wins permit"
        item, key, _ = self.add(title, "https://a.test/1")
        self.assertTrue(hbnscan.detach_from_cluster(self.conn, item))
        self.assertEqual(len(hbnscan.story_overrides(self.conn)), 1)
        self.assertEqual(hbnscan.undo_story_change(self.conn, item_hash_value=item), 1)
        row = self.conn.execute("SELECT cluster_key FROM seen_items WHERE item_hash = ?",
                                (item,)).fetchone()
        self.assertEqual(row[0], key)

    def test_merging_a_story_into_itself_is_refused(self):
        key, _ = self.merge_pair()
        with self.assertRaises(ValueError):
            hbnscan.merge_stories(self.conn, key, key)

    def test_merging_an_empty_story_is_refused(self):
        key, _ = self.merge_pair()
        with self.assertRaises(ValueError):
            hbnscan.merge_stories(self.conn, "u:nothing", key)

    def test_the_story_record_names_its_articles_and_sources(self):
        item, key, _ = self.add("Acme wins permit", "https://a.test/1")
        record = hbnscan.story_record(self.conn, key)
        self.assertEqual(record.id, hbnscan.hbrecords.story_id(key))
        self.assertEqual(len(record.article_ids), 1)
        self.assertEqual(len(record.source_ids), 1)


class StoryCommandTest(StoryCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = hbworkspace.main(list(argv))
        return code, output.getvalue()

    def test_show_prints_the_evidence_and_the_independence_verdict(self):
        item, key, _ = self.add("Acme wins permit", "https://a.test/1")
        code, text = self.run_cli("story", "show", key)
        self.assertEqual(code, 0, text)
        self.assertIn("Story: Acme wins permit", text)
        self.assertIn("independence:", text)
        self.assertIn("members:", text)

    def test_show_json_is_machine_readable(self):
        _item, key, _ = self.add("Acme wins permit", "https://a.test/1")
        code, text = self.run_cli("story", "show", key, "--json")
        self.assertEqual(code, 0)
        report = json.loads(text)
        self.assertEqual(report["key"], key)
        self.assertIn("sources", report)
        self.assertIn("record", report)

    def test_show_on_an_unknown_story_is_a_usage_error(self):
        code, text = self.run_cli("story", "show", "u:nothing")
        self.assertEqual(code, 2)
        self.assertIn("[FAIL]", text)

    def test_merge_split_and_undo_from_the_command_line(self):
        self.add("Acme wins permit", "https://a.test/1")
        self.add("Acme wins permit - Reuters", "https://b.test/2")
        keys = [row[0] for row in self.conn.execute(
            "SELECT DISTINCT cluster_key FROM seen_items ORDER BY cluster_key")]
        code, text = self.run_cli("story", "merge", keys[0], keys[1])
        self.assertEqual(code, 0, text)
        self.assertIn("undo with", text)

        code, text = self.run_cli("story", "undo", keys[1], "--all")
        self.assertEqual(code, 0, text)
        self.assertIn("restored 1", text)

        item = self.conn.execute("SELECT item_hash FROM seen_items LIMIT 1").fetchone()[0]
        code, text = self.run_cli("story", "split", item)
        self.assertEqual(code, 0, text)
        self.assertIn(f"row:{item}", text)

    def test_undo_with_nothing_to_restore_says_so(self):
        _item, key, _ = self.add("Acme wins permit", "https://a.test/1")
        code, text = self.run_cli("story", "undo", key, "--all")
        self.assertEqual(code, 0)
        self.assertIn("nothing to undo", text)


if __name__ == "__main__":
    unittest.main()
