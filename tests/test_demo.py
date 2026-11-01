"""Persistent developer data stays synthetic, repeatable and separate from real archives."""

import contextlib
import io
import os
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
                    output, "--keep-db", "--days", "2", "--visits-per-day", "7", "--profiles", "2"
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
                    output, "--keep-db", "--days", "5", "--visits-per-day", "20", "--profiles", "2"
                )
            self.assertEqual(
                (outputs[0] / "index.html").read_bytes(), (outputs[1] / "index.html").read_bytes()
            )
            with contextlib.closing(sqlite3.connect(outputs[0] / "capsule.db")) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 200)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 10)

    def test_seed_refuses_existing_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing"
            with contextlib.closing(storage.open_db(output)) as db:
                storage.set_profile_name(db, "Default", "Preserve this")
            original = (output / "capsule.db").read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.generate(output, "--keep-db")
            self.assertEqual(error.exception.code, 2)
            self.assertEqual((output / "capsule.db").read_bytes(), original)

    def test_small_html_demo_can_be_regenerated_without_leaving_a_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "demo"
            self.generate(output)
            first = (output / "index.html").read_bytes()
            self.generate(output)
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
                    self.generate(output, "--keep-db", option, "0")
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
