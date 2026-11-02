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
from unittest.mock import patch

from tab_capsule import cli, render, storage
from tests.fixtures import Snss, make_history


class CliTest(unittest.TestCase):
    def test_failed_profile_reports_failure_and_still_imports_other_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_dir = Path(tmp) / "chrome"
            bad = chrome_dir / "Default"
            good = chrome_dir / "Profile 1"
            bad.mkdir(parents=True)
            good.mkdir()
            (bad / "History").write_bytes(b"not sqlite")
            make_history(good / "History", [(1, "https://example.org/", "Synthetic", time.time())])
            data_dir = Path(tmp) / "archive"
            with patch("tab_capsule.chrome.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
                status = cli.main(
                    ["run", "--chrome-dir", str(chrome_dir), "--data-dir", str(data_dir)]
                )
            self.assertEqual(status, 1)
            with contextlib.closing(storage.open_db(data_dir)) as db:
                self.assertEqual(
                    db.execute("SELECT profile FROM visits").fetchall(), [("Profile 1",)]
                )
            self.assertTrue((data_dir / "index.html").is_file())

    def test_invalid_session_reports_failure_and_continues_other_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            chrome_dir = Path(tmp) / "chrome"
            for profile in ("Default", "Profile 1"):
                (chrome_dir / profile / "Sessions").mkdir(parents=True)
            (chrome_dir / "Default" / "Sessions" / "Session_1").write_bytes(b"invalid session")
            session = Snss().tab_window(1, 10).nav(10, 0, "https://example.org/", "Synthetic")
            (chrome_dir / "Profile 1" / "Sessions" / "Session_1").write_bytes(session.bytes())
            data_dir = Path(tmp) / "archive"
            with contextlib.redirect_stdout(io.StringIO()):
                status = cli.main(
                    ["snapshot", "--chrome-dir", str(chrome_dir), "--data-dir", str(data_dir)]
                )
            self.assertEqual(status, 1)
            with contextlib.closing(storage.open_db(data_dir)) as db:
                self.assertEqual(
                    db.execute("SELECT profile FROM snapshots").fetchall(), [("Profile 1",)]
                )

    def test_archive_connection_closes_when_render_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = storage.open_db(tmp)
            with (
                patch("tab_capsule.cli.open_db", return_value=db),
                patch("tab_capsule.cli.cmd_render", side_effect=OSError("synthetic disk failure")),
                self.assertRaisesRegex(OSError, "synthetic disk failure"),
            ):
                cli.main(["render", "--data-dir", tmp])
            with self.assertRaises(sqlite3.ProgrammingError):
                db.execute("SELECT 1")

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


class SandboxTest(unittest.TestCase):
    """--sandbox pins both directories to the synthetic archive made by seed.py."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.sandbox = self.tmp / "sandbox"
        patcher = patch.object(cli, "SANDBOX_DIR", self.sandbox)
        patcher.start()
        self.addCleanup(patcher.stop)

    def cli(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            status = cli.main(list(argv))
        return status, out.getvalue()

    def seed(self):
        import seed

        with contextlib.redirect_stdout(io.StringIO()):
            seed.main(["--corpus", "small", "--profiles", "2"])

    def test_sandbox_uses_its_own_archive_and_fake_chrome(self):
        self.seed()
        real = self.tmp / "real archive"
        with (
            patch.dict(os.environ, TAB_CAPSULE_DIR=str(real), TAB_CAPSULE_CHROME_DIR="/nowhere"),
            patch.object(cli, "DEFAULT_CHROME_DIR", "/nowhere"),
        ):
            status, out = self.cli("run", "--sandbox")
            self.assertEqual(status, 0, out)
            self.assertEqual(out.count("(unchanged)"), 2)
            self.assertEqual(out.count("+0 visits"), 2)
            status, out = self.cli("stats", "--sandbox")
        self.assertIn("Default (Demo): 12 visits", out)
        self.assertFalse(real.exists())

    def test_sandbox_must_exist_and_cannot_be_combined_with_directories(self):
        for argv in (["stats", "--sandbox"], ["stats", "--sandbox", "--data-dir", str(self.tmp)]):
            with (
                self.subTest(argv=argv),
                contextlib.redirect_stderr(io.StringIO()) as err,
                self.assertRaises(SystemExit) as exit,
            ):
                cli.main(argv)
            self.assertEqual(exit.exception.code, 2)
            self.assertIn("sandbox", err.getvalue())
        self.assertFalse(self.sandbox.exists())

    def test_chrome_dir_environment_variable(self):
        self.seed()
        archive = self.tmp / "archive"
        with patch.dict(os.environ, TAB_CAPSULE_CHROME_DIR=str(self.sandbox / "chrome")):
            status, out = self.cli("import-history", "--data-dir", str(archive))
        self.assertEqual(status, 0, out)
        self.assertIn("Default: +12 visits", out)
