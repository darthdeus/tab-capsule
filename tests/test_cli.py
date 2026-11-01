"""CLI tests with synthetic profiles and temporary archives."""

import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tab_capsule import cli, render
from tests.fixtures import Snss, make_history


class CliTest(unittest.TestCase):
    def test_module_entry_point_respects_data_dir_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "explicit archive"
            env_dir = Path(tmp) / "environment archive"
            result = subprocess.run(
                [sys.executable, "-m", "tab_capsule", "render", "--data-dir", str(data_dir)],
                cwd=Path(__file__).resolve().parents[1],
                env=dict(os.environ, TAB_CAPSULE_DIR=str(env_dir)),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("wrote " + str(data_dir / "index.html"), result.stdout)
            self.assertEqual(
                render.extract_data((data_dir / "index.html").read_text(encoding="utf-8"))["D"],
                [],
            )
            self.assertFalse(env_dir.exists())

    def test_render_from_another_directory_with_env_data_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "archive")
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "tab_capsule.py"),
                    "render",
                ],
                cwd=tmp,
                env=dict(os.environ, TAB_CAPSULE_DIR=data_dir),
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("wrote " + os.path.join(data_dir, "index.html"), result.stdout)
            with open(os.path.join(data_dir, "index.html"), encoding="utf-8") as f:
                html = f.read()
            self.assertEqual(render.extract_data(html)["D"], [])
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
                    cli.main(["run", "--chrome-dir", chrome, "--data-dir", data])
            self.assertIn("(unchanged)", out.getvalue())
            db = sqlite3.connect(os.path.join(data, "capsule.db"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM visits").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT name FROM profiles").fetchone()[0], "Alex")
            db.close()
            self.assertTrue(os.path.isfile(os.path.join(data, "index.html")))
