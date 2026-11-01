"""Snapshot persistence and rendered archive data tests."""

import os
import tempfile
import time
import unittest

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
