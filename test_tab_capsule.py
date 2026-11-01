"""Tests for tab-capsule: python3 -m unittest test_tab_capsule (from this dir)."""
import contextlib, io, os, sqlite3, struct, subprocess, sys, tempfile, time, unittest

import tab_capsule as tc


# ---------------------------------------------------------------- synthetic SNSS builder

def _pad4(b):
    return b + b"\0" * (-len(b) % 4)


def p_int(v):
    return struct.pack("<i", v)


def p_str(s):
    b = s.encode("utf-8")
    return p_int(len(b)) + _pad4(b)


def p_str16(s):
    b = s.encode("utf-16-le")
    return p_int(len(b) // 2) + _pad4(b)


def pickle(body):
    return struct.pack("<I", len(body)) + body


class Snss:
    def __init__(self):
        self.records = []

    def cmd(self, cid, payload):
        self.records.append(struct.pack("<HB", len(payload) + 1, cid) + payload)
        return self

    def tab_window(self, wid, tid):
        return self.cmd(0, struct.pack("<ii", wid, tid))

    def tab_index(self, tid, idx):
        return self.cmd(2, struct.pack("<ii", tid, idx))

    def nav(self, tid, idx, url, title, ts_unix=None):
        ts = tc.unix_to_webkit(ts_unix) if ts_unix else 0
        body = (p_int(tid) + p_int(idx) + p_str(url) + p_str16(title) + p_str("PAGESTATE")
                + p_int(0) + p_int(0) + p_str("") + p_int(0) + p_str(url) + p_int(0)
                + struct.pack("<q", ts) + p_int(200))
        return self.cmd(6, pickle(body))

    def selected_nav(self, tid, idx):
        return self.cmd(7, struct.pack("<ii", tid, idx))

    def window_type(self, wid, t=0):
        return self.cmd(9, struct.pack("<ii", wid, t))

    def pinned(self, tid, on=True):
        return self.cmd(12, struct.pack("<i?3x", tid, on))

    def tab_closed(self, tid):
        return self.cmd(16, struct.pack("<i4xq", tid, tc.unix_to_webkit(time.time())))

    def window_closed(self, wid):
        return self.cmd(17, struct.pack("<i4xq", wid, tc.unix_to_webkit(time.time())))

    def last_active(self, tid, t):
        return self.cmd(21, struct.pack("<i4xq", tid, tc.unix_to_webkit(t)))

    def pruned_from_back(self, tid, idx):
        return self.cmd(5, struct.pack("<ii", tid, idx))

    def bytes(self):
        return b"SNSS" + struct.pack("<i", 3) + b"".join(self.records)


def urls(windows):
    return [[t["url"] for t in w] for w in windows]


class SnssTest(unittest.TestCase):
    def test_replay_windows_tabs_and_selected_nav(self):
        s = Snss().window_type(1).window_type(2)
        s.tab_window(1, 10).tab_index(10, 1).nav(10, 0, "https://a.example/", "A").selected_nav(10, 0)
        s.tab_window(1, 11).tab_index(11, 0).nav(11, 0, "https://b.example/1", "B1")
        s.nav(11, 1, "https://b.example/2", "B2").selected_nav(11, 1)
        s.tab_window(2, 20).tab_index(20, 0).nav(20, 0, "https://c.example/", "C").pinned(20)
        w = tc.replay_session(s.bytes())
        self.assertEqual(urls(w), [["https://b.example/2", "https://a.example/"], ["https://c.example/"]])
        self.assertEqual(w[0][0]["title"], "B2")
        self.assertTrue(w[1][0]["pinned"])
        self.assertFalse(w[0][0]["pinned"])

    def test_selected_index_back_in_history(self):
        s = Snss().tab_window(1, 10).nav(10, 0, "https://x/1", "1").nav(10, 1, "https://x/2", "2").selected_nav(10, 0)
        self.assertEqual(urls(tc.replay_session(s.bytes())), [["https://x/1"]])

    def test_tab_moved_between_windows(self):
        s = Snss().tab_window(1, 10).tab_window(2, 11).nav(10, 0, "https://a/", "A").nav(11, 0, "https://b/", "B")
        s.tab_window(2, 10).tab_index(10, 1)
        self.assertEqual(urls(tc.replay_session(s.bytes())), [["https://b/", "https://a/"]])

    def test_tab_closed(self):
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A").tab_window(1, 11).tab_index(11, 1)
        s.nav(11, 0, "https://b/", "B").tab_closed(10)
        self.assertEqual(urls(tc.replay_session(s.bytes())), [["https://b/"]])

    def test_window_closed_drops_its_tabs(self):
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A").tab_window(2, 20).nav(20, 0, "https://b/", "B")
        s.window_closed(1)
        self.assertEqual(urls(tc.replay_session(s.bytes())), [["https://b/"]])

    def test_prune_from_back(self):
        s = Snss().tab_window(1, 10)
        for i in range(3):
            s.nav(10, i, f"https://x/{i}", str(i))
        s.selected_nav(10, 2).pruned_from_back(10, 1)
        # selected index 2 no longer exists → falls back to the last remaining nav
        self.assertEqual(urls(tc.replay_session(s.bytes())), [["https://x/0"]])

    def test_truncated_trailing_record_is_ignored(self):
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A")
        good = s.bytes()
        s.nav(10, 1, "https://never-finished/", "partial").selected_nav(10, 1)
        data = s.bytes()
        cut = data[:len(good) + 7]  # mid-way into the next record
        self.assertEqual(urls(tc.replay_session(cut)), [["https://a/"]])

    def test_multibyte_titles(self):
        title = "Příliš žluťoučký kůň 🐴 úpěl ďábelské ódy 🎵"
        s = Snss().tab_window(1, 10).nav(10, 0, "https://cz.example/ř", title)
        w = tc.replay_session(s.bytes())
        self.assertEqual(w[0][0]["title"], title)
        self.assertEqual(w[0][0]["url"], "https://cz.example/ř")

    def test_timestamps_plausible_or_none(self):
        now = time.time() - 3600
        s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A", ts_unix=now).last_active(10, now)
        s.tab_window(1, 11).tab_index(11, 1).nav(11, 0, "https://b/", "B")  # ts 0 → None
        w = tc.replay_session(s.bytes())
        self.assertAlmostEqual(w[0][0]["nav_time"], now, delta=1)
        self.assertAlmostEqual(w[0][0]["last_active"], now, delta=1)
        self.assertIsNone(w[0][1]["nav_time"])

    def test_not_snss(self):
        with self.assertRaises(ValueError):
            tc.replay_session(b"NOPE\x03\0\0\0")


# ---------------------------------------------------------------- history import

def make_history(path, rows):
    """rows: [(visit_id, url, title, visit_time_unix)] using Chrome's urls/visits schema."""
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS urls(id INTEGER PRIMARY KEY AUTOINCREMENT,url LONGVARCHAR,title LONGVARCHAR,
      visit_count INTEGER DEFAULT 0 NOT NULL,typed_count INTEGER DEFAULT 0 NOT NULL,last_visit_time INTEGER NOT NULL,
      hidden INTEGER DEFAULT 0 NOT NULL);
    CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY AUTOINCREMENT,url INTEGER NOT NULL,visit_time INTEGER NOT NULL,
      from_visit INTEGER,external_referrer_url TEXT,transition INTEGER DEFAULT 0 NOT NULL,segment_id INTEGER,
      visit_duration INTEGER DEFAULT 0 NOT NULL);
    """)
    for vid, url, title, t in rows:
        r = db.execute("SELECT id FROM urls WHERE url=?", (url,)).fetchone()
        uid = r[0] if r else db.execute("INSERT INTO urls(url,title,last_visit_time) VALUES(?,?,?)",
                                        (url, title, tc.unix_to_webkit(t))).lastrowid
        db.execute("INSERT INTO visits(id,url,visit_time,transition,visit_duration) VALUES(?,?,?,?,?)",
                   (vid, uid, tc.unix_to_webkit(t), 0x30000001, 5))
    db.commit()
    db.close()


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hist = os.path.join(self.tmp.name, "History")
        self.db = tc.open_db(os.path.join(self.tmp.name, "data"))
        base = time.time() - 86400
        make_history(self.hist, [(1, "https://a/", "A", base), (2, "https://b/", "Bé 🐴", base + 60),
                                 (3, "https://a/", "A", base + 120)])

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def count(self):
        return self.db.execute("SELECT COUNT(*) FROM visits").fetchone()[0]

    def test_import_twice_no_dupes(self):
        self.assertEqual(tc.import_history_file(self.db, "Default", self.hist), 3)
        self.assertEqual(tc.import_history_file(self.db, "Default", self.hist), 0)
        self.assertEqual(self.count(), 3)
        self.assertEqual(self.db.execute("SELECT title FROM visits WHERE url='https://b/'").fetchone()[0], "Bé 🐴")
        self.assertEqual(self.db.execute("SELECT transition FROM visits LIMIT 1").fetchone()[0], 0x30000001)

    def test_archive_keeps_rows_chrome_expired(self):
        tc.import_history_file(self.db, "Default", self.hist)
        src = sqlite3.connect(self.hist)
        src.execute("DELETE FROM visits WHERE id IN (1, 2)")
        src.commit()
        src.close()
        make_history(self.hist, [(4, "https://c/", "C", time.time())])
        self.assertEqual(tc.import_history_file(self.db, "Default", self.hist), 1)
        self.assertEqual(self.count(), 4)
        self.assertEqual({r[0] for r in self.db.execute("SELECT url FROM visits")},
                         {"https://a/", "https://b/", "https://c/"})

    def test_profiles_are_separate(self):
        tc.import_history_file(self.db, "Default", self.hist)
        tc.import_history_file(self.db, "Profile 1", self.hist)
        self.assertEqual(self.count(), 6)

    def test_hot_journal_copy_is_readable(self):
        # A History-journal next to History is copied along; an empty one must not break the import.
        open(self.hist + "-journal", "wb").close()
        self.assertEqual(tc.import_history_file(self.db, "Default", self.hist), 3)


# ---------------------------------------------------------------- snapshots + render

def win(*pairs):
    return [{"url": u, "title": t, "pinned": False, "last_active": None, "nav_time": None} for u, t in pairs]


class SnapshotRenderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = os.path.join(self.tmp.name, "data")
        self.db = tc.open_db(self.data_dir)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def snaps(self):
        return self.db.execute("SELECT id, taken_at, last_seen_at FROM snapshots ORDER BY id").fetchall()

    def test_same_tabs_bump_last_seen(self):
        w = [win(("https://a/", "A"), ("https://b/", "B"))]
        sid1, c1 = tc.store_snapshot(self.db, "Default", w, now=1000)
        sid2, c2 = tc.store_snapshot(self.db, "Default", w, now=2000)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(sid1, sid2)
        self.assertEqual(self.snaps(), [(sid1, 1000, 2000)])

    def test_title_change_alone_is_not_a_new_snapshot(self):
        tc.store_snapshot(self.db, "Default", [win(("https://mail/", "Inbox"))], now=1000)
        _, created = tc.store_snapshot(self.db, "Default", [win(("https://mail/", "(3) Inbox"))], now=2000)
        self.assertFalse(created)

    def test_changed_tab_makes_new_snapshot(self):
        tc.store_snapshot(self.db, "Default", [win(("https://a/", "A"), ("https://b/", "B"))], now=1000)
        sid, created = tc.store_snapshot(self.db, "Default", [win(("https://a/", "A"), ("https://c/", "C"))], now=2000)
        self.assertTrue(created)
        self.assertEqual(len(self.snaps()), 2)
        self.assertEqual([r[0] for r in self.db.execute("SELECT url FROM snapshot_tabs WHERE snapshot_id=? ORDER BY tab_idx", (sid,))],
                         ["https://a/", "https://c/"])

    def test_profiles_dedupe_independently(self):
        w = [win(("https://a/", "A"))]
        tc.store_snapshot(self.db, "Default", w, now=1000)
        _, created = tc.store_snapshot(self.db, "Profile 1", w, now=1000)
        self.assertTrue(created)

    def test_render_embeds_valid_data(self):
        day1 = time.mktime((2026, 9, 1, 12, 0, 0, 0, 0, -1))
        day2 = time.mktime((2026, 9, 2, 12, 0, 0, 0, 0, -1))
        tc.set_profile_name(self.db, "Default", "Alex")
        tc.store_snapshot(self.db, "Default", [win(("https://a/", "A </script> & <b>"))], now=day1)
        tc.store_snapshot(self.db, "Default", [win(("https://a/", "A"), ("https://new/", "Nový 🐴"))], now=day2)
        hist = os.path.join(self.tmp.name, "History")
        make_history(hist, [(1, "https://v/", "Visit", day1), (2, "https://w/", "W", day2), (3, "https://v/", "Visit", day2 + 60)])
        tc.import_history_file(self.db, "Default", hist)
        tc.cmd_render(self.db, self.data_dir, log=lambda *_: None)
        with open(os.path.join(self.data_dir, "index.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("<title>Tab Capsule</title>", html)
        self.assertNotIn("googleapis", html)
        self.assertNotIn("google.com/s2", html)
        data = tc.extract_data(html)
        S = data["S"]
        self.assertEqual(data["P"], ["Alex"])
        self.assertEqual([d[0] for d in data["D"]], ["2026-09-02", "2026-09-01"])
        d2 = data["D"][0]
        self.assertEqual(d2[2] - d2[1], 2)
        self.assertEqual([S[v[1]] for v in data["V"][d2[1]:d2[2]]], ["https://w/", "https://v/"])
        snap2 = [s for s in data["T"] if s["d"] == "2026-09-02"][0]
        self.assertEqual([(S[t[0]], t[3]) for t in snap2["w"][0]], [("https://a/", 0), ("https://new/", 1)])
        self.assertIn("Nový 🐴", S)
        self.assertIn("A </script> & <b>", S)


class CliTest(unittest.TestCase):
    def test_render_from_another_directory_with_env_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "archive")
            result = subprocess.run(
                [sys.executable, os.path.abspath(tc.__file__), "render"],
                cwd=tmp, env=dict(os.environ, TAB_CAPSULE_DIR=data_dir),
                check=True, capture_output=True, text=True)
            self.assertIn("wrote " + os.path.join(data_dir, "index.html"), result.stdout)
            with open(os.path.join(data_dir, "index.html"), encoding="utf-8") as f:
                html = f.read()
            self.assertEqual(tc.extract_data(html)["D"], [])
            self.assertNotIn("/*STYLE*/", html)
            self.assertNotIn("/*SCRIPT*/", html)
            self.assertIn("function search(q)", html)

    def test_run_end_to_end_with_fake_chrome_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome = os.path.join(tmp, "chrome")
            prof = os.path.join(chrome, "Default")
            os.makedirs(os.path.join(prof, "Sessions"))
            with open(os.path.join(chrome, "Local State"), "w") as f:
                f.write('{"profile":{"info_cache":{"Default":{"name":"Alex"}}}}')
            s = Snss().tab_window(1, 10).nav(10, 0, "https://a/", "A")
            with open(os.path.join(prof, "Sessions", "Session_1"), "wb") as f:
                f.write(s.bytes())
            make_history(os.path.join(prof, "History"), [(1, "https://a/", "A", time.time() - 60)])
            data = os.path.join(tmp, "data")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                for _ in range(2):
                    tc.main(["run", "--chrome-dir", chrome, "--data-dir", data])
            self.assertIn("(unchanged)", out.getvalue())
            db = sqlite3.connect(os.path.join(data, "capsule.db"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT name FROM profiles").fetchone()[0], "Alex")
            db.close()
            self.assertTrue(os.path.isfile(os.path.join(data, "index.html")))


if __name__ == "__main__":
    unittest.main()
