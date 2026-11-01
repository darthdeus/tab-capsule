"""Chrome session parsing and History import tests."""

import os
import sqlite3
import tempfile
import time
import unittest

from tab_capsule import chrome, storage
from tests.fixtures import Snss, make_history, urls


class SnssTest(unittest.TestCase):
    def test_replay_windows_tabs_and_selected_nav(self):
        s = Snss().window_type(1).window_type(2)
        s.tab_window(1, 10).tab_index(10, 1).nav(10, 0, "https://a.example/", "A").selected_nav(
            10, 0
        )
        s.tab_window(1, 11).tab_index(11, 0).nav(11, 0, "https://b.example/1", "B1")
        s.nav(11, 1, "https://b.example/2", "B2").selected_nav(11, 1)
        s.tab_window(2, 20).tab_index(20, 0).nav(20, 0, "https://c.example/", "C").pinned(20)
        w = chrome.replay_session(s.bytes())
        self.assertEqual(
            urls(w), [["https://b.example/2", "https://a.example/"], ["https://c.example/"]]
        )
        self.assertEqual(w[0][0]["title"], "B2")
        self.assertTrue(w[1][0]["pinned"])
        self.assertFalse(w[0][0]["pinned"])

    def test_selected_index_back_in_history(self):
        s = (
            Snss()
            .tab_window(1, 10)
            .nav(10, 0, "https://x/1", "1")
            .nav(10, 1, "https://x/2", "2")
            .selected_nav(10, 0)
        )
        self.assertEqual(urls(chrome.replay_session(s.bytes())), [["https://x/1"]])

    def test_tab_moved_between_windows(self):
        s = (
            Snss()
            .tab_window(1, 10)
            .tab_window(2, 11)
            .nav(10, 0, "https://a/", "A")
            .nav(11, 0, "https://b/", "B")
        )
        s.tab_window(2, 10).tab_index(10, 1)
        self.assertEqual(urls(chrome.replay_session(s.bytes())), [["https://b/", "https://a/"]])

    def test_tab_closed(self):
        s = (
            Snss()
            .tab_window(1, 10)
            .nav(10, 0, "https://a/", "A")
            .tab_window(1, 11)
            .tab_index(11, 1)
        )
        s.nav(11, 0, "https://b/", "B").tab_closed(10)
        self.assertEqual(urls(chrome.replay_session(s.bytes())), [["https://b/"]])

    def test_window_closed_drops_its_tabs(self):
        s = (
            Snss()
            .tab_window(1, 10)
            .nav(10, 0, "https://a/", "A")
            .tab_window(2, 20)
            .nav(20, 0, "https://b/", "B")
        )
        s.window_closed(1)
        self.assertEqual(urls(chrome.replay_session(s.bytes())), [["https://b/"]])

    def test_prune_from_back(self):
        s = Snss().tab_window(1, 10)
        for i in range(3):
            s.nav(10, i, f"https://x/{i}", str(i))
        s.selected_nav(10, 2).pruned_from_back(10, 1)
        # selected index 2 no longer exists → falls back to the last remaining nav
        self.assertEqual(urls(chrome.replay_session(s.bytes())), [["https://x/0"]])

    def test_truncated_trailing_record_is_ignored(self):
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A")
        good = s.bytes()
        s.nav(10, 1, "https://never-finished/", "partial").selected_nav(10, 1)
        data = s.bytes()
        cut = data[: len(good) + 7]  # mid-way into the next record
        self.assertEqual(urls(chrome.replay_session(cut)), [["https://a/"]])

    def test_multibyte_titles(self):
        title = "Příliš žluťoučký kůň 🐴 úpěl ďábelské ódy 🎵"
        s = Snss().tab_window(1, 10).nav(10, 0, "https://cz.example/ř", title)
        w = chrome.replay_session(s.bytes())
        self.assertEqual(w[0][0]["title"], title)
        self.assertEqual(w[0][0]["url"], "https://cz.example/ř")

    def test_timestamps_plausible_or_none(self):
        now = time.time() - 3600
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A", ts_unix=now).last_active(10, now)
        s.tab_window(1, 11).tab_index(11, 1).nav(11, 0, "https://b/", "B")  # ts 0 → None
        w = chrome.replay_session(s.bytes())
        self.assertAlmostEqual(w[0][0]["nav_time"], now, delta=1)
        self.assertAlmostEqual(w[0][0]["last_active"], now, delta=1)
        self.assertIsNone(w[0][1]["nav_time"])

    def test_not_snss(self):
        with self.assertRaises(ValueError):
            chrome.replay_session(b"NOPE\x03\0\0\0")


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hist = os.path.join(self.tmp.name, "History")
        self.db = storage.open_db(os.path.join(self.tmp.name, "data"))
        base = time.time() - 86400
        make_history(
            self.hist,
            [
                (1, "https://a/", "A", base),
                (2, "https://b/", "Bé 🐴", base + 60),
                (3, "https://a/", "A", base + 120),
            ],
        )

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM visits").fetchone()[0]

    def test_import_twice_no_dupes(self):
        self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 3)
        self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 0)
        self.assertEqual(self.count(), 3)
        self.assertEqual(
            self.db.execute("SELECT title FROM visits WHERE url='https://b/'").fetchone()[0],
            "Bé 🐴",
        )
        self.assertEqual(
            self.db.execute("SELECT transition FROM visits LIMIT 1").fetchone()[0], 0x30000001
        )

    def test_archive_keeps_rows_chrome_expired(self):
        chrome.import_history_file(self.db, "Default", self.hist)
        src = sqlite3.connect(self.hist)
        src.execute("DELETE FROM visits WHERE id IN (1, 2)")
        src.commit()
        src.close()
        make_history(self.hist, [(4, "https://c/", "C", time.time())])
        self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 1)
        self.assertEqual(self.count(), 4)
        self.assertEqual(
            {r[0] for r in self.db.execute("SELECT url FROM visits")},
            {"https://a/", "https://b/", "https://c/"},
        )

    def test_profiles_are_separate(self):
        chrome.import_history_file(self.db, "Default", self.hist)
        chrome.import_history_file(self.db, "Profile 1", self.hist)
        self.assertEqual(self.count(), 6)

    def test_hot_journal_copy_is_readable(self):
        # A History-journal next to History is copied along; an empty one must not break the import.
        open(self.hist + "-journal", "wb").close()
        self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 3)
