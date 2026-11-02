"""Finding Chrome: CHROME_BIN, then PATH, then standard install locations."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import cdp


class FindChromeTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        (self.tmp / "bin").mkdir()
        environ = patch.dict(os.environ, PATH=str(self.tmp / "bin"))
        environ.start()
        self.addCleanup(environ.stop)
        os.environ.pop("CHROME_BIN", None)

    def executable(self, relative: str) -> str:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return str(path)

    def find(self, *locations: str) -> str | None:
        with patch.object(cdp, "CHROME_LOCATIONS", locations):
            return cdp.find_chrome()

    def test_order_is_chrome_bin_then_path_then_install_locations(self) -> None:
        app = self.executable("Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        missing = str(self.tmp / "Applications/Chromium.app/Contents/MacOS/Chromium")
        self.assertIsNone(self.find(missing))
        self.assertEqual(self.find(missing, app), app)
        on_path = self.executable("bin/chromium")
        self.assertEqual(self.find(app), on_path)
        with patch.dict(os.environ, CHROME_BIN=app):
            self.assertEqual(self.find(), app)

    def test_install_location_must_be_executable(self) -> None:
        app = self.executable("opt/google/chrome/chrome")
        os.chmod(app, 0o644)
        self.assertIsNone(self.find(app))

    def test_home_locations_expand(self) -> None:
        app = self.executable("home/Applications/Chromium.app/Contents/MacOS/Chromium")
        with patch.dict(os.environ, HOME=str(self.tmp / "home")):
            self.assertEqual(self.find("~/Applications/Chromium.app/Contents/MacOS/Chromium"), app)

    def test_default_locations_cover_macos_apps(self) -> None:
        self.assertIn(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", cdp.CHROME_LOCATIONS
        )


if __name__ == "__main__":
    unittest.main()
