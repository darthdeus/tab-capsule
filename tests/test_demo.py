"""Persistent developer data stays synthetic, repeatable and separate from real archives."""

import contextlib
import io
import os
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import demo
from tab_capsule import cli, render, storage


class DemoTest(unittest.TestCase):
    def generate(self, output, *options):
        with contextlib.redirect_stdout(io.StringIO()):
            demo.main(["--output-dir", str(output), *options])

    def test_persistent_seed_supports_full_collection_without_real_chrome(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "sandbox"
            unrelated = Path(tmp) / "unrelated archive"
            with (
                patch.dict(os.environ, TAB_CAPSULE_DIR=str(unrelated)),
                patch(
                    "tab_capsule.chrome.list_profiles", side_effect=AssertionError("Chrome read")
                ),
            ):
                self.generate(
                    output,
                    "--corpus",
                    "small",
                    "--days",
                    "2",
                    "--visits-per-day",
                    "7",
                    "--profiles",
                    "2",
                )
            self.assertFalse(unrelated.exists())
            self.assertTrue((output / "chrome" / "Default" / "History").is_file())
            self.assertTrue((output / "chrome" / "Profile 1" / "Sessions" / "Session_1").is_file())
            with contextlib.closing(storage.open_db(output)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 28)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 4)

            with contextlib.redirect_stdout(io.StringIO()) as log:
                status = cli.main(
                    ["run", "--data-dir", str(output), "--chrome-dir", str(output / "chrome")]
                )
            self.assertEqual(status, 0)
            self.assertEqual(log.getvalue().count("(unchanged)"), 2)
            self.assertEqual(log.getvalue().count("+0 visits"), 2)
            data = render.extract_data((output / "index.html").read_text(encoding="utf-8"))
            self.assertEqual(data["P"], ["Demo", "Demo 2"])
            self.assertEqual(len(data["V"]), 28)

    def test_seed_is_repeatable_with_the_same_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [Path(tmp) / name for name in ("one", "two")]
            for output in outputs:
                self.generate(
                    output,
                    "--corpus",
                    "small",
                    "--days",
                    "5",
                    "--visits-per-day",
                    "20",
                    "--profiles",
                    "2",
                )
            self.assertEqual(
                (outputs[0] / "index.html").read_bytes(), (outputs[1] / "index.html").read_bytes()
            )
            with contextlib.closing(sqlite3.connect(outputs[0] / "capsule.db")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 200)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 10)

    def test_realistic_corpus_round_trips_and_has_the_shape_of_real_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "realistic"
            self.generate(
                output, "--corpus", "realistic", "--days", "12",
                "--visits-per-day", "150", "--profiles", "2",
            )  # fmt: skip
            data = render.extract_data((output / "index.html").read_text(encoding="utf-8"))
            S, V, T = data["S"], data["V"], data["T"]
            self.assertEqual(len(V), 12 * 150 * 2)
            self.assertLessEqual(V[-1][0], int(demo.END.timestamp()), "no visits in the future")

            # Repeat visits and a long tail: many distinct pages, but favourites dominate.
            counts: dict[int, int] = {}
            for visit in V:
                counts[visit[1]] = counts.get(visit[1], 0) + 1
            self.assertGreater(len(counts), len(V) // 4)
            self.assertGreater(max(counts.values()), len(V) // 50)
            self.assertGreater(
                sum(S[u].startswith("https://www.google.com/search?") for u in counts), 50
            )

            # Short terms mostly occur inside longer words, as in real history.
            texts = [(S[v[1]] + " " + S[v[2]]).lower() for v in V]
            anywhere = [t for t in texts if "gh" in t]
            word_start = [t for t in anywhere if re.search(r"(?<![a-z0-9])gh", t)]
            self.assertGreater(len(word_start), 0)
            self.assertGreater(len(anywhere), 10 * len(word_start))

            # Tabs drift: several windows, pinned tabs, and day-to-day changes.
            self.assertTrue(any(len(snapshot["w"]) > 1 for snapshot in T))
            self.assertTrue(all(snapshot["w"][0][0][2] for snapshot in T), "pinned first tab")
            self.assertGreater(sum(tab[3] for s in T for w in s["w"] for tab in w), len(T))
            closed = 0
            for earlier, later in zip(T, T[1:]):
                if earlier["p"] == later["p"]:
                    after = {tab[0] for w in later["w"] for tab in w}
                    closed += any(tab[0] not in after for w in earlier["w"] for tab in w)
            self.assertGreater(closed, len(T) // 2, "tabs also close from day to day")

            # The fake Chrome profiles match the archive exactly.
            with contextlib.redirect_stdout(io.StringIO()) as log:
                status = cli.main(
                    ["run", "--data-dir", str(output), "--chrome-dir", str(output / "chrome")]
                )
            self.assertEqual(status, 0)
            self.assertEqual(log.getvalue().count("(unchanged)"), 2, log.getvalue())
            self.assertEqual(log.getvalue().count("+0 visits"), 2, log.getvalue())

    def test_realistic_corpus_is_repeatable(self):
        with tempfile.TemporaryDirectory() as tmp:
            outputs = [Path(tmp) / name for name in ("one", "two")]
            for output in outputs:
                self.generate(
                    output, "--page-only", "--days", "4", "--visits-per-day", "40",
                    "--profiles", "2",
                )  # fmt: skip
            self.assertEqual(
                (outputs[0] / "index.html").read_bytes(), (outputs[1] / "index.html").read_bytes()
            )

    def test_no_options_rebuild_the_sandbox_with_realistic_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp) / "checkout" / "demo-output" / "sandbox"
            with patch.object(cli, "SANDBOX_DIR", sandbox):
                for _ in range(2):  # a second run replaces the first sandbox
                    with contextlib.redirect_stdout(io.StringIO()) as log:
                        self.assertEqual(demo.main([]), sandbox)
            self.assertIn("tab_capsule.py --sandbox browse", log.getvalue())
            self.assertTrue((sandbox / cli.SANDBOX_MARKER).is_file())
            self.assertTrue((sandbox / "chrome" / "Profile 1" / "History").is_file())
            with contextlib.closing(sqlite3.connect(sandbox / "capsule.db")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 6000)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0], 2)
                distinct = db.execute("SELECT COUNT(DISTINCT url) FROM visits").fetchone()[0]
            self.assertGreater(distinct, 1000, "realistic corpus by default")

    def test_page_only_defaults_to_demo_output_without_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(demo, "ROOT", Path(tmp)):
                with contextlib.redirect_stdout(io.StringIO()):
                    output = demo.main(["--page-only", "--days", "2"])
            self.assertEqual(output, Path(tmp) / "demo-output")
            self.assertEqual([p.name for p in output.iterdir()], ["index.html"])

    def test_replaces_only_directories_a_demo_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "demo"
            self.generate(output, "--corpus", "small")
            (output / "stray").write_text("from the previous demo", encoding="utf-8")
            self.generate(output, "--corpus", "small", "--days", "1")
            self.assertFalse((output / "stray").exists())
            (output / cli.SANDBOX_MARKER).unlink()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.generate(output, "--corpus", "small")
            self.assertTrue((output / "capsule.db").is_file())

    def test_seed_refuses_existing_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing"
            with contextlib.closing(storage.open_db(output)) as db:
                storage.set_profile_name(db, "Default", "Preserve this")
            original = (output / "capsule.db").read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.generate(output)
            self.assertEqual(error.exception.code, 2)
            self.assertEqual((output / "capsule.db").read_bytes(), original)

    def test_small_html_demo_can_be_regenerated_without_leaving_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "demo"
            self.generate(output, "--page-only", "--corpus", "small")
            first = (output / "index.html").read_bytes()
            self.generate(output, "--page-only", "--corpus", "small")
            self.assertEqual((output / "index.html").read_bytes(), first)
            self.assertEqual([path.name for path in output.iterdir()], ["index.html"])
            self.assertEqual(len(render.extract_data(first.decode("utf-8"))["V"]), 12)

    def test_invalid_counts_do_not_create_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "invalid"
            for option in ("--days", "--visits-per-day", "--profiles"):
                with (
                    self.subTest(option=option),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as error,
                ):
                    self.generate(output, option, "0")
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
