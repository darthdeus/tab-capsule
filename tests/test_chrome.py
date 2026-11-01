"""Chrome session parsing and History import tests."""

import multiprocessing
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing, contextmanager
from unittest.mock import patch

from tab_capsule import chrome, storage
from tests.fixtures import Snss, make_history, urls


def _history_writer(path, control):
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("PRAGMA locking_mode=EXCLUSIVE")
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        next_id = writer.execute("SELECT MAX(id) + 1 FROM visits").fetchone()[0]
        control.send("ready")
        while control.recv() == "write":
            writer.execute(
                "INSERT INTO visits(id,url,visit_time) VALUES(?,1,?)",
                (next_id, chrome.unix_to_webkit(1788436800 + next_id)),
            )
            writer.commit()
            writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            control.send(next_id)
            next_id += 1


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

    def test_new_window_stays_after_surviving_older_window(self):
        session = Snss()
        for wid in (1, 2, 3):
            session.tab_window(wid, wid * 10).nav(
                wid * 10, 0, f"https://example.org/window-{wid}", str(wid)
            )
        session.window_closed(1).window_closed(2)
        session.tab_window(4, 40).nav(40, 0, "https://example.org/window-4", "4")
        self.assertEqual(
            urls(chrome.replay_session(session.bytes())),
            [["https://example.org/window-3"], ["https://example.org/window-4"]],
        )

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
    @contextmanager
    def writing_history(self):
        context = multiprocessing.get_context("spawn")
        control, worker = context.Pipe()
        process = context.Process(target=_history_writer, args=(self.hist, worker))
        process.start()
        worker.close()
        try:
            self.assertTrue(control.poll(10), "history writer did not start")
            self.assertEqual(control.recv(), "ready")
            yield control
        finally:
            if process.is_alive():
                control.send("stop")
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
            control.close()

    def commit_and_checkpoint(self, control):
        control.send("write")
        self.assertTrue(control.poll(10), "history writer did not finish its transaction")
        return control.recv()

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

    def test_import_includes_committed_visits_still_in_wal(self):
        writer = sqlite3.connect(self.hist)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute(
                "INSERT INTO visits(id,url,visit_time) VALUES(4,1,?)",
                (chrome.unix_to_webkit(time.time()),),
            )
            writer.commit()
            self.assertTrue(os.path.isfile(self.hist + "-wal"))
            self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 4)
            self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 0)
            self.assertEqual(self.count(), 4)
        finally:
            writer.close()

    def test_failed_import_rolls_back_all_visits(self):
        self.db.executescript("""
            CREATE TRIGGER fail_visit BEFORE INSERT ON visits
            WHEN NEW.url = 'https://b/'
            BEGIN SELECT RAISE(ABORT, 'synthetic visit failure'); END;
        """)
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic visit failure"):
            chrome.import_history_file(self.db, "Default", self.hist)
        storage.set_profile_name(self.db, "Default", "Synthetic")
        self.assertEqual(self.count(), 0)

        self.db.execute("DROP TRIGGER fail_visit")
        self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 3)

    def test_import_rejects_nonpositive_retry_count(self):
        for retries in (0, -1):
            with self.subTest(retries=retries), self.assertRaisesRegex(ValueError, "retries"):
                chrome.import_history_file(self.db, "Default", self.hist, retries=retries)

    def test_copy_retries_after_concurrent_commit_and_checkpoint(self):
        copy_file = shutil.copyfile
        copies = 0
        with self.writing_history() as control:

            def copy_then_write(source, destination):
                nonlocal copies
                result = copy_file(source, destination)
                if source == self.hist:
                    copies += 1
                    if copies == 1:
                        self.assertEqual(self.commit_and_checkpoint(control), 4)
                return result

            with (
                patch("tab_capsule.chrome.shutil.copyfile", side_effect=copy_then_write),
                patch("tab_capsule.chrome.time.sleep") as sleep,
            ):
                self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 4)
            self.assertEqual(copies, 2)
            sleep.assert_called_once_with(1)
            self.assertEqual(self.count(), 4)

    def test_continuous_source_changes_exhaust_retries_without_modifying_archive(self):
        copy_file = shutil.copyfile
        with self.writing_history() as control:
            chrome.import_history_file(self.db, "Default", self.hist)

            def copy_then_write(source, destination):
                result = copy_file(source, destination)
                if source == self.hist:
                    self.commit_and_checkpoint(control)
                return result

            with (
                patch("tab_capsule.chrome.shutil.copyfile", side_effect=copy_then_write),
                patch("tab_capsule.chrome.time.sleep") as sleep,
                self.assertRaisesRegex(sqlite3.OperationalError, "changed while copying"),
            ):
                chrome.import_history_file(self.db, "Default", self.hist, retries=3)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(self.count(), 3)

    def test_transient_copy_failure_is_retried(self):
        copy_file = shutil.copyfile
        copies = 0

        def disappear_once(source, destination):
            nonlocal copies
            copies += 1
            if copies == 1:
                raise FileNotFoundError("synthetic source rotation")
            return copy_file(source, destination)

        with (
            patch("tab_capsule.chrome.shutil.copyfile", side_effect=disappear_once),
            patch("tab_capsule.chrome.time.sleep") as sleep,
        ):
            self.assertEqual(chrome.import_history_file(self.db, "Default", self.hist), 3)
        self.assertEqual(copies, 2)
        sleep.assert_called_once_with(1)
