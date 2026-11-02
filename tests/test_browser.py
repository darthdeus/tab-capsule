"""Browser checks of the generated page in headless Chrome. Every archive is synthetic;
no Chrome profile is read. Needs only a Chrome/Chromium executable (see tests/cdp.py):
CHROME_BIN selects it, TAB_CAPSULE_SKIP_BROWSER=1 skips these tests explicitly."""

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from calendar import timegm
from pathlib import Path
from typing import Any

import seed
from tab_capsule.render import render_html
from tests.cdp import Browser, Page, find_chrome

MAIN_DETAILS = "#main > details"


class BrowserTest(unittest.TestCase):
    browser: Browser
    temporary: Path
    demo: Path

    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("TAB_CAPSULE_SKIP_BROWSER") == "1":
            raise unittest.SkipTest("TAB_CAPSULE_SKIP_BROWSER=1")
        chrome = find_chrome()
        if chrome is None:
            # A hard failure, so a missing browser is never mistaken for passing checks.
            raise RuntimeError(
                "No Chrome/Chromium found: set CHROME_BIN to its executable, "
                "or TAB_CAPSULE_SKIP_BROWSER=1 to skip the browser tests."
            )
        cls.temporary = Path(tempfile.mkdtemp(prefix="tab-capsule-browser-"))
        output = cls.temporary / "generated"
        cls.generate(output, "--page-only", "--corpus", "small")
        # Move just the HTML, then remove its original directory: no server or sidecars.
        cls.demo = cls.temporary / "standalone.html"
        (output / "index.html").rename(cls.demo)
        shutil.rmtree(output)
        cls.browser = Browser(chrome)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        shutil.rmtree(cls.temporary, ignore_errors=True)

    @staticmethod
    def generate(output: Path, *options: str) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            seed.main(["--output-dir", str(output), *options])

    def fixture(self, name: str, data: dict[str, Any]) -> Path:
        file = self.temporary / f"{name}.html"
        file.write_text(render_html(data), encoding="utf-8")  # type: ignore[arg-type]
        return file

    def open_archive(self, file: Path | None = None, timezone: str = "Europe/Prague") -> Page:
        page = self.browser.new_page(timezone=timezone, locale="en-GB")
        self.addCleanup(self.close_page, page)
        page.goto((file or self.demo).as_uri())
        page.wait_for("document.querySelector('#gen').textContent.startsWith('updated ')")
        return page

    def close_page(self, page: Page) -> None:
        page.wait(0.05)  # collect events still in flight
        page.close()
        self.assertEqual(page.errors, [], "no JavaScript or browser console errors")
        # Includes favicon loads: the page must never reach the network.
        self.assertEqual(page.requests, [], "no external requests")

    def query(self, page: Page, value: str) -> None:
        page.fill("#q", value)
        # Wait for the existing 200 ms debounce and its visible result.
        page.wait_for(
            f"document.querySelector('h2')?.textContent === {'Search: ' + value.strip()!r}"
        )

    def test_standalone_demo_loads_styles_snapshots_history_and_day_navigation(self) -> None:
        page = self.open_archive()
        self.assertEqual(page.evaluate("document.title"), "Tab Capsule")
        self.assertEqual(page.count("#days a"), 3)
        self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-03")
        self.assertRegex(
            page.inner_text(f"{MAIN_DETAILS} > summary"), r"Demo.*4 tabs · 1 windows · 1 new"
        )
        self.assertEqual(page.count(".pin"), 1)
        self.assertEqual(page.count(".badge"), 1)
        self.assertEqual(page.inner_text(".badge"), "NEW")
        self.assertEqual(page.count("details.hr li"), 4)
        self.assertEqual(
            page.evaluate("getComputedStyle(document.querySelector('header')).position"),
            "sticky",
        )
        page.click_button("expand all")
        self.assertEqual(page.count("details.hr:not([open])"), 0)
        page.click_button("collapse all")
        self.assertEqual(page.count("details.hr[open]"), 0)
        page.click('#days a[href="#2026-09-01"]')
        page.wait_for("document.querySelector('#days a.sel')?.hash === '#2026-09-01'")
        self.assertRegex(page.inner_text(f"{MAIN_DETAILS} > summary"), r"2 tabs")
        self.assertEqual(page.count(".badge"), 0)

    def test_literal_search_combines_terms_keeps_recency_and_deduplicates_tabs(self) -> None:
        page = self.open_archive()
        self.query(page, "  EXAMPLE.ORG   shader  ")
        self.assertEqual(page.inner_text("#main > .sub"), "3 visits · 1 open-tab matches")
        self.assertEqual(page.count(f"{MAIN_DETAILS}:first-of-type li"), 1)
        self.assertEqual(page.texts(f"{MAIN_DETAILS}:first-of-type .jump"), ["2026-09-03"])
        self.assertEqual(
            page.texts(f"{MAIN_DETAILS}:last-of-type .jump"),
            ["2026-09-03", "2026-09-02", "2026-09-01"],
        )
        self.query(page, "shaeder")
        self.assertEqual(page.inner_text("#main > .sub"), "0 visits · 0 open-tab matches")
        self.assertEqual(page.inner_text(".empty"), "No visits match.")
        self.query(page, "grid")
        page.click(f"{MAIN_DETAILS}:last-of-type .jump", index=-1)
        self.assertEqual(page.value("#q"), "")
        self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-01")

    def test_keyboard_shortcuts_short_queries_and_hash_fallback_keep_day_routing(self) -> None:
        page = self.open_archive()
        page.press("/")
        self.assertTrue(page.is_focused("#q"))
        self.query(page, "postgres")
        page.press("Escape")
        self.assertEqual(page.value("#q"), "")
        self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-03")
        page.fill("#q", "x")
        page.wait(0.25)
        self.assertEqual(page.inner_text("#main h2"), "Thursday, 3 September 2026")
        page.fill("#q", "")
        page.evaluate("location.hash = 'missing-day'")
        page.wait(0.25)
        self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-03")

    def test_one_character_input_still_allows_day_navigation(self) -> None:
        page = self.open_archive()
        page.fill("#q", "x")
        # Settle the input debounce before clicking, so it cannot mask a missed hashchange.
        page.wait(0.25)
        page.click('#days a[href="#2026-09-01"]')
        page.wait_for("document.querySelector('#days a.sel')?.hash === '#2026-09-01'")
        self.assertEqual(page.inner_text("#main h2"), "Tuesday, 1 September 2026")
        self.assertEqual(page.value("#q"), "x")

    def test_compressed_unicode_html_titles_stay_text_and_unsafe_links_stay_blocked(
        self,
    ) -> None:
        title = 'Příliš 🐴 </script><img src="https://example.org/probe"> & "quoted"'
        file = self.fixture(
            "escaping",
            {
                "S": ["javascript:alert(1)", title],
                "P": ["Synthetic <profile>"],
                "V": [[1788436800, 0, 1, 0, 1]],
                "D": [["2026-09-03", 0, 1]],
                "T": [
                    {
                        "p": 0,
                        "d": "2026-09-03",
                        "t": 1788436800,
                        "s": 1788436800,
                        "w": [[[0, 1, 0, 0]]],
                    },
                ],
                "generated": 1788436800,
            },
        )
        page = self.open_archive(file)
        self.query(page, "PŘÍLIŠ 🐴")
        self.assertEqual(page.inner_text("#main > .sub"), "1 visits · 1 open-tab matches")
        self.assertEqual(page.texts(".ti a"), [title, title])
        self.assertEqual(page.count("#main img, #main script"), 0)
        for index in range(page.count(".ti a")):
            self.assertEqual(page.attribute(".ti a", "href", index), "#")
            self.assertEqual(page.attribute(".ti a", "rel", index), "noreferrer noopener")

    def test_existing_search_display_limits_preserve_total_counts(self) -> None:
        strings: list[str] = []
        tabs = []
        for i in range(105):
            strings += [f"https://example.org/{i}", f"Needle {i}"]
            tabs.append([2 * i, 2 * i + 1, 0, 0])
        data = {
            "S": strings,
            "P": ["Synthetic"],
            "V": [[1788436800 + i, 0, 1, 0, 1] for i in range(305)],
            "D": [["2026-09-03", 0, 305]],
            "T": [{"p": 0, "d": "2026-09-03", "t": 1788436800, "s": 1788436800, "w": [tabs]}],
            "generated": 1788436800,
        }
        page = self.open_archive(self.fixture("limits", data))
        self.query(page, "needle")
        self.assertEqual(
            page.inner_text("#main > .sub"),
            "305 visits (showing newest 300) · 105 open-tab matches",
        )
        self.assertEqual(page.count(f"{MAIN_DETAILS}:first-of-type li"), 100)
        self.assertEqual(page.count(f"{MAIN_DETAILS}:last-of-type li"), 300)

    def test_empty_archive_loads_and_can_be_searched(self) -> None:
        empty = {"S": [], "P": [], "V": [], "D": [], "T": [], "generated": 1788436800}
        page = self.open_archive(self.fixture("empty", empty))
        self.assertEqual(page.inner_text("#main"), "No data yet.")
        self.query(page, "example")
        self.assertEqual(page.inner_text("#main > .sub"), "0 visits · 0 open-tab matches")

    def test_persistent_seeded_data_renders_multiple_profiles_and_can_be_searched(self) -> None:
        output = self.temporary / "seeded"
        self.generate(
            output, "--corpus", "small", "--days", "2", "--visits-per-day", "7", "--profiles", "2"
        )
        page = self.open_archive(output / "index.html")
        self.assertEqual(page.inner_text("#main > .sub"), "14 history visits")
        self.assertEqual(page.count(MAIN_DETAILS), 3)
        self.query(page, "postgres")
        self.assertEqual(page.inner_text("#main > .sub"), "8 visits · 2 open-tab matches")
        self.assertEqual(
            sorted(page.texts(f"{MAIN_DETAILS}:first-of-type .prof")), ["Demo", "Demo 2"]
        )

    def test_realistic_corpus_renders_and_reports_search_counts(self) -> None:
        output = self.temporary / "realistic"
        self.generate(
            output, "--page-only", "--days", "5", "--visits-per-day", "80",
            "--profiles", "2",
        )  # fmt: skip
        page = self.open_archive(output / "index.html")
        self.assertEqual(page.count("#days a"), 5)
        self.assertGreater(page.count("details.hr li"), 50)
        self.query(page, "gh")
        self.assertRegex(page.inner_text("#main > .sub"), r"^\d+ visits · \d+ open-tab matches$")
        self.assertGreater(page.count(".jump"), 20)

    def test_history_jumps_keep_archive_days_in_other_browser_timezones(self) -> None:
        before_midnight = timegm((2026, 9, 1, 23, 30, 0))
        after_midnight = timegm((2026, 9, 2, 0, 30, 0))
        for timezone in ("UTC", "America/Los_Angeles", "Asia/Tokyo"):
            with self.subTest(timezone=timezone):
                file = self.fixture(
                    "midnight-" + timezone.replace("/", "-"),
                    {
                        "S": ["https://example.org/midnight", "Midnight"],
                        "P": ["Synthetic"],
                        "V": [[before_midnight, 0, 1, 0, 1], [after_midnight, 0, 1, 0, 1]],
                        "D": [["2026-09-02", 1, 2], ["2026-09-01", 0, 1]],
                        "T": [],
                        "generated": after_midnight,
                    },
                )
                page = self.open_archive(file, timezone)
                self.query(page, "midnight")
                self.assertEqual(page.texts(".jump"), ["2026-09-02", "2026-09-01"])
                page.click(".jump", index=-1)
                self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-01")
                self.assertEqual(page.inner_text("#main > .sub"), "1 history visits")

                self.query(page, "midnight")
                page.click(".jump", index=0)
                self.assertEqual(page.attribute("#days a.sel", "href"), "#2026-09-02")
                self.assertEqual(page.inner_text("#main > .sub"), "1 history visits")


if __name__ == "__main__":
    unittest.main()
