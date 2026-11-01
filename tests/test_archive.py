"""Snapshot persistence and rendered archive data tests."""

import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from tab_capsule import chrome, render, storage
from tests.fixtures import make_history, win


class SnapshotRenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.db = storage.open_db(self.data_dir)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snaps(self):
        return self.db.execute(
            "SELECT id, taken_at, last_seen_at FROM snapshots ORDER BY id"
        ).fetchall()

    def test_same_tabs_bump_last_seen(self):
        w = [win(("https://a/", "A"), ("https://b/", "B"))]
        sid1, c1 = storage.store_snapshot(self.db, "Default", w, now=1000)
        sid2, c2 = storage.store_snapshot(self.db, "Default", w, now=2000)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(sid1, sid2)
        self.assertEqual(self.snaps(), [(sid1, 1000, 2000)])

    def test_title_change_alone_is_not_a_new_snapshot(self):
        storage.store_snapshot(self.db, "Default", [win(("https://mail/", "Inbox"))], now=1000)
        _, created = storage.store_snapshot(
            self.db, "Default", [win(("https://mail/", "(3) Inbox"))], now=2000
        )
        self.assertFalse(created)

    def test_changed_tab_makes_new_snapshot(self):
        storage.store_snapshot(
            self.db, "Default", [win(("https://a/", "A"), ("https://b/", "B"))], now=1000
        )
        sid, created = storage.store_snapshot(
            self.db, "Default", [win(("https://a/", "A"), ("https://c/", "C"))], now=2000
        )
        self.assertTrue(created)
        self.assertEqual(len(self.snaps()), 2)
        self.assertEqual(
            [
                r[0]
                for r in self.db.execute(
                    "SELECT url FROM snapshot_tabs WHERE snapshot_id=? ORDER BY tab_idx", (sid,)
                )
            ],
            ["https://a/", "https://c/"],
        )

    def test_profiles_dedupe_independently(self):
        w = [win(("https://a/", "A"))]
        storage.store_snapshot(self.db, "Default", w, now=1000)
        _, created = storage.store_snapshot(self.db, "Profile 1", w, now=1000)
        self.assertTrue(created)

    def test_failed_snapshot_rolls_back_all_rows_and_can_be_retried(self):
        storage.store_snapshot(self.db, "Default", [win(("https://old.example/", "Old"))], now=1000)
        self.db.executescript("""
            CREATE TRIGGER fail_second_tab BEFORE INSERT ON snapshot_tabs
            WHEN NEW.tab_idx = 1
            BEGIN SELECT RAISE(ABORT, 'synthetic tab failure'); END;
        """)
        windows = [win(("https://a.example/", "A"), ("https://b.example/", "B"))]
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic tab failure"):
            storage.store_snapshot(self.db, "Default", windows, now=2000)

        # A later successful write must not commit a partial snapshot from the failure.
        storage.set_profile_name(self.db, "Default", "Synthetic")
        self.assertEqual(len(self.snaps()), 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM snapshot_tabs").fetchone()[0], 1)

        self.db.execute("DROP TRIGGER fail_second_tab")
        _, created = storage.store_snapshot(self.db, "Default", windows, now=2000)
        self.assertTrue(created)
        self.assertEqual(len(self.snaps()), 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM snapshot_tabs").fetchone()[0], 3)

    def test_render_embeds_valid_data(self):
        day1 = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
        day2 = time.mktime((2026, 9, 2, 12, 0, 0, 0, 0, -1))
        storage.set_profile_name(self.db, "Default", "Alex")
        storage.store_snapshot(
            self.db, "Default", [win(("https://a/", "A </script> & <b>"))], now=day1
        )
        storage.store_snapshot(
            self.db, "Default", [win(("https://a/", "A"), ("https://new/", "Nový 🐴"))], now=day2
        )
        hist = os.path.join(self.tmp.name, "History")
        make_history(
            hist,
            [
                (1, "https://v/", "Visit", day1),
                (2, "https://w/", "W", day2),
                (3, "https://v/", "Visit", day2 + 60),
            ],
        )
        chrome.import_history_file(self.db, "Default", hist)
        render.cmd_render(self.db, self.data_dir, log=lambda *_: None)
        with open(os.path.join(self.data_dir, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("<title>Tab Capsule</title>", html)
        self.assertNotIn("googleapis", html)
        self.assertNotIn("google.com/s2", html)
        data = render.extract_data(html)
        S = data["S"]
        self.assertEqual(data["P"], ["Alex"])
        self.assertEqual([d[0] for d in data["D"]], ["2026-09-02", "2026-09-01"])
        d2 = data["D"][0]
        self.assertEqual(d2[2] - d2[1], 2)
        self.assertEqual([S[v[1]] for v in data["V"][d2[1] : d2[2]]], ["https://w/", "https://v/"])
        snap2 = [s for s in data["T"] if s["d"] == "2026-09-02"][0]
        self.assertEqual(
            [(S[t[0]], t[3]) for t in snap2["w"][0]], [("https://a/", 0), ("https://new/", 1)]
        )
        self.assertIn("Nový 🐴", S)
        self.assertIn("A </script> & <b>", S)

    def test_concurrent_renders_publish_complete_pages(self):
        barrier = Barrier(2, timeout=5)
        replace_file = os.replace
        temporary_names = []
        complete_pages = []

        def replace_together(source, destination):
            temporary_names.append(source)
            complete_pages.append(Path(source).read_text(encoding="utf-8"))
            barrier.wait()
            return replace_file(source, destination)

        def render_profile(profile):
            with closing(storage.open_db(Path(self.tmp.name) / profile)) as db:
                storage.set_profile_name(db, "Default", profile)
                render.cmd_render(db, self.data_dir, log=lambda _: None)

        with patch("tab_capsule.render.os.replace", side_effect=replace_together):
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(render_profile, profile) for profile in ("One", "Two")]
                for future in futures:
                    future.result(timeout=10)

        self.assertEqual(len(set(temporary_names)), 2)
        output = Path(self.data_dir) / "index.html"
        html = output.read_text(encoding="utf-8")
        self.assertIn(html, complete_pages)
        self.assertIn(render.extract_data(html)["P"], [["One"], ["Two"]])
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(Path(self.data_dir).glob("*.tmp")), [])

    def test_failed_publish_preserves_previous_page_and_removes_temporary_file(self):
        render.cmd_render(self.db, self.data_dir, log=lambda _: None)
        output = Path(self.data_dir) / "index.html"
        previous = output.read_bytes()
        storage.set_profile_name(self.db, "Default", "New data")

        with (
            patch(
                "tab_capsule.render.os.replace", side_effect=OSError("synthetic publish failure")
            ),
            self.assertRaisesRegex(OSError, "synthetic publish failure"),
        ):
            render.cmd_render(self.db, self.data_dir, log=lambda _: None)

        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(Path(self.data_dir).glob("*.tmp")), [])

    def test_page_data_uses_one_database_view_during_concurrent_collection(self):
        self.db.execute("PRAGMA journal_mode=WAL")
        storage.set_profile_name(self.db, "Default", "Existing")
        self.db.execute(
            "INSERT INTO visits(profile,visit_time,url,title) VALUES(?,?,?,?)",
            ("Default", chrome.unix_to_webkit(1788436800), "https://example.org/old", "Old"),
        )
        self.db.commit()
        committed = False
        with closing(storage.open_db(self.data_dir)) as writer:

            def collect_after_profiles_were_read(sql):
                nonlocal committed
                if not committed and sql.startswith("SELECT visit_time, url, title"):
                    committed = True
                    writer.execute("INSERT INTO profiles(dir,name) VALUES('Profile 1','New')")
                    writer.execute(
                        "INSERT INTO visits(profile,visit_time,url,title) VALUES(?,?,?,?)",
                        (
                            "Profile 1",
                            chrome.unix_to_webkit(1788436860),
                            "https://example.org/new",
                            "New",
                        ),
                    )
                    writer.commit()

            self.db.set_trace_callback(collect_after_profiles_were_read)
            try:
                data = render.build_data(self.db)
            finally:
                self.db.set_trace_callback(None)

        self.assertTrue(committed)
        self.assertEqual(data["P"], ["Existing"])
        self.assertEqual(len(data["V"]), 1)
        self.assertFalse(self.db.in_transaction)
        next_render = render.build_data(self.db)
        self.assertEqual(next_render["P"], ["Existing", "New"])
        self.assertEqual(len(next_render["V"]), 2)

    def test_page_data_preserves_caller_owned_transaction(self):
        self.db.execute("INSERT INTO profiles(dir,name) VALUES('Default','Pending')")
        self.assertEqual(render.build_data(self.db)["P"], ["Pending"])
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 0)

    def test_page_data_releases_owned_transaction_after_query_failure(self):
        self.db.execute("DROP TABLE visits")
        with self.assertRaises(sqlite3.OperationalError):
            render.build_data(self.db)
        self.assertFalse(self.db.in_transaction)
