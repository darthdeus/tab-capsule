"""Terminal browser: day views, keys and layout without a terminal, plus a pty smoke test."""

import contextlib
import datetime
import fcntl
import io
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path
from typing import Any

import seed
from tab_capsule import cli, tui
from tab_capsule.models import PageData

ROOT = Path(__file__).resolve().parent.parent


def at(day: str, hour: int, minute: int = 0) -> int:
    """Local-time timestamp, matching how the archive assigns visits to days."""
    return int(datetime.datetime.fromisoformat(f"{day}T{hour:02}:{minute:02}").timestamp())


def archive_data() -> PageData:
    """Two profiles; profile 1 has no snapshot on 09-02, so its 09-01 tabs carry forward."""
    strings = [
        "https://example.org/a", "Alpha page",
        "https://example.org/b", "",
        "https://例え.jp/パス", "日本語のタイトル 🐴",
        "https://example.net/c", "Line\nbreak\ttitle",
    ]  # fmt: skip
    return {
        "S": strings,
        "P": ["Work", "Home"],
        "V": [
            [at("2026-09-01", 9), 0, 1, 0, 0],
            [at("2026-09-02", 9, 5), 2, 3, 0, 1],
            [at("2026-09-02", 9, 40), 4, 5, 1, 0],
            [at("2026-09-02", 14, 1), 6, 7, 0, 8],
        ],
        "D": [["2026-09-02", 1, 4], ["2026-09-01", 0, 1]],
        "T": [
            {"p": 0, "d": "2026-09-01", "t": at("2026-09-01", 12), "s": 0, "w": [[[0, 1, 1, 0]]]},
            {"p": 1, "d": "2026-09-01", "t": at("2026-09-01", 12), "s": 0, "w": [[[4, 5, 0, 0]]]},
            {
                "p": 0, "d": "2026-09-02", "t": at("2026-09-02", 12), "s": 0,
                "w": [[[0, 1, 1, 0], [2, 3, 0, 1]], [[6, 7, 0, 1]]],
            },
        ],
        "generated": at("2026-09-02", 15),
    }  # fmt: skip


def text(line: tui.Line) -> str:
    return "".join(segment for segment, _ in line)


class TextWidthTest(unittest.TestCase):
    def test_fit_counts_terminal_cells(self) -> None:
        for value in ["plain", "日本語", "🐴 horse", "éclair", "", "x" * 50]:
            for width in [1, 2, 5, 9, 20]:
                with self.subTest(value=value, width=width):
                    fitted = tui.fit(value, width)
                    self.assertEqual(tui.text_width(fitted), width)
        self.assertEqual(tui.fit("日本語", 5), "日本…")
        self.assertEqual(tui.fit("日本語", 4), "日… ")  # never half of a wide character
        self.assertEqual(tui.fit("abcdef", 4), "abc…")
        self.assertEqual(tui.text_width("é"), 1)

    def test_control_characters_never_reach_the_terminal(self) -> None:
        self.assertEqual(tui.fit("a\nb\tc\x1b[31m‮", 12), "a b c [31m  ")

    def test_domain(self) -> None:
        self.assertEqual(tui.domain("https://www.example.org/x"), "example.org")
        self.assertEqual(tui.domain("file:///tmp/a.html"), "file")
        self.assertEqual(tui.domain("http://[::1"), "")


class ArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.archive = tui.Archive(archive_data())

    def test_day_shows_tabs_per_profile_then_history_newest_first(self) -> None:
        rows = self.archive.rows("2026-09-02")
        self.assertEqual(
            [(row.kind, row.text) for row in rows],
            [
                ("heading", "Open tabs"),
                ("profile", "Work"),
                ("window", "Window 1"),
                ("tab", "Alpha page"),
                ("tab", "https://example.org/b"),  # untitled tabs show their URL
                ("window", "Window 2"),
                ("tab", "Line\nbreak\ttitle"),
                ("profile", "Home"),
                ("tab", "日本語のタイトル 🐴"),
                ("heading", "History"),
                ("hour", "14:00"),
                ("visit", "Line\nbreak\ttitle"),
                ("hour", "09:00"),
                ("visit", "日本語のタイトル 🐴"),
                ("visit", "https://example.org/b"),
            ],
        )
        work, home = rows[1], rows[7]
        self.assertEqual(work.note, "3 tabs · 2 windows · 2 new · snapshot 12:00")
        self.assertEqual(home.note, "1 tabs · 1 windows · carried forward from 2026-09-01")
        self.assertTrue(rows[3].pinned)
        self.assertTrue(rows[4].new)
        self.assertEqual(rows[9].note, "3 visits")
        # Profiles are named when there are several; non-link transitions are shown.
        self.assertEqual(rows[11].note, "example.net · Work · reload")
        self.assertEqual(rows[13].note, "例え.jp · Home")
        self.assertEqual(rows[14].note, "example.org · Work · typed")
        self.assertEqual(rows[14].time, "09:05")

    def test_days_without_snapshots_or_visits(self) -> None:
        archive = tui.Archive(
            {"S": [], "P": [], "V": [], "D": [["2026-09-01", 0, 0]], "T": [], "generated": 0}
        )
        self.assertEqual(
            [row.text for row in archive.rows("2026-09-01")],
            ["Open tabs", "No tab snapshot existed yet on this day.", "History", "No visits."],
        )
        self.assertEqual(archive.visit_count("2026-09-01"), 0)
        self.assertFalse(archive.has_snapshot("2026-09-01"))


class BrowserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.browser = tui.Browser(tui.Archive(archive_data()))

    def selected_text(self) -> str:
        row = self.browser.selected
        assert row is not None
        return row.text

    def test_starts_on_newest_day_with_first_entry_selected(self) -> None:
        self.assertEqual(self.browser.day_name, "2026-09-02")
        self.assertEqual(self.selected_text(), "Alpha page")
        self.assertEqual(self.browser.focus, "rows")

    def test_row_movement_skips_headings_and_stops_at_the_ends(self) -> None:
        visited = [self.selected_text()]
        for _ in range(10):
            self.browser.handle("j")
            visited.append(self.selected_text())
        self.assertEqual(
            visited[:7],
            [
                "Alpha page",
                "https://example.org/b",
                "Line\nbreak\ttitle",
                "日本語のタイトル 🐴",
                "Line\nbreak\ttitle",
                "日本語のタイトル 🐴",
                "https://example.org/b",
            ],
        )
        self.assertEqual(visited[7:], ["https://example.org/b"] * 4)
        self.browser.handle("g")
        self.assertEqual(self.selected_text(), "Alpha page")
        self.browser.handle("end")
        self.assertEqual(self.browser.rows[self.browser.cursor].kind, "visit")
        self.browser.handle("pageup", page=3)
        self.assertEqual(self.selected_text(), "日本語のタイトル 🐴")
        self.browser.handle("up")
        self.assertEqual(self.selected_text(), "Line\nbreak\ttitle")

    def test_days_pane_and_brackets_change_day_and_reset_selection(self) -> None:
        self.browser.handle("j")
        self.browser.handle("tab")
        self.assertEqual(self.browser.focus, "days")
        self.browser.handle("down")
        self.assertEqual(self.browser.day_name, "2026-09-01")
        self.assertEqual(self.selected_text(), "Alpha page")
        self.browser.handle("down")  # already the oldest day
        self.assertEqual(self.browser.day_name, "2026-09-01")
        self.browser.handle("enter")
        self.assertEqual(self.browser.focus, "rows")
        self.browser.handle("]")
        self.assertEqual(self.browser.day_name, "2026-09-02")
        self.browser.handle("]")
        self.assertEqual(self.browser.day_name, "2026-09-02")
        self.browser.handle("[")
        self.assertEqual(self.browser.day_name, "2026-09-01")
        self.browser.handle("l")
        self.assertEqual(self.browser.focus, "rows")
        self.browser.handle("h")
        self.assertEqual(self.browser.focus, "days")

    def test_enter_shows_details_and_escape_closes_before_quitting(self) -> None:
        self.browser.handle("enter")
        footer = text(self.browser.render(10, 80)[-1])
        self.assertIn("https://example.org/a  —  Alpha page", footer)
        self.assertTrue(self.browser.handle("esc"))
        self.assertNotIn("example.org", text(self.browser.render(10, 80)[-1]))
        self.assertTrue(self.browser.handle("?"))
        self.assertIn("older/newer day", text(self.browser.render(10, 120)[-1]))
        self.assertTrue(self.browser.handle("esc"))
        self.assertFalse(self.browser.handle("esc"))
        self.assertFalse(tui.Browser(tui.Archive(archive_data())).handle("q"))

    def test_render_fills_every_size_exactly(self) -> None:
        for height, width in [(1, 10), (3, 20), (5, 40), (12, 59), (12, 60), (40, 200)]:
            with self.subTest(height=height, width=width):
                lines = self.browser.render(height, width)
                self.assertEqual(len(lines), height)
                for line in lines:
                    self.assertEqual(tui.text_width(text(line)), width, text(line))

    def test_layout_shows_days_selection_and_notes(self) -> None:
        lines = [text(line) for line in self.browser.render(12, 90)]
        self.assertEqual(
            lines[0].rstrip(), " Tab Capsule · Wednesday, 02 September 2026 · 3 visits"
        )
        self.assertTrue(lines[1].startswith(" 2026-09-02 •    3 │─ Open tabs ─"))
        self.assertTrue(lines[2].startswith(" 2026-09-01 •    1 │ Work"))
        self.assertIn("3 tabs · 2 windows · 2 new · snapshot 12:00", lines[2])
        self.assertTrue(lines[4].endswith("example.org"))
        self.assertIn("^ Alpha page", lines[4])
        self.assertIn("+ https://example.org/b", lines[5])
        self.assertIn("Line break title", lines[7])
        styles = self.browser.render(12, 90)[4]
        self.assertEqual([style for _, style in styles], ["normal", "dim", "selected"])
        # Narrow terminals hide the day list and drop notes that do not fit.
        narrow = [text(line) for line in self.browser.render(8, 40)]
        self.assertTrue(narrow[1].startswith("─ Open tabs ─"))
        self.assertEqual(narrow[2].rstrip(), " Work")

    def test_scrolling_keeps_the_selection_visible(self) -> None:
        for _ in range(6):
            self.browser.handle("j")
        lines = self.browser.render(6, 80)
        selected = [text(line) for line in lines if line[-1][1] == "selected"]
        self.assertEqual(len(selected), 1)
        self.assertIn("09:05", selected[0])
        self.browser.handle("g")
        self.assertIn("Open tabs", text(self.browser.render(6, 80)[1]))

    def test_empty_archive(self) -> None:
        browser = tui.Browser(
            tui.Archive({"S": [], "P": [], "V": [], "D": [], "T": [], "generated": 0})
        )
        lines = [text(line) for line in browser.render(6, 80)]
        self.assertEqual(lines[0].rstrip(), " Tab Capsule")
        self.assertIn("No data yet", lines[2])
        for key in ["j", "k", "G", "tab", "down", "enter", "[", "]", "?"]:
            self.assertTrue(browser.handle(key))


class TerminalTest(unittest.TestCase):
    """Runs the real command under a pseudo-terminal: curses starts, keys work, q exits."""

    def test_browse_runs_in_a_terminal_and_quits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                seed.main(["--corpus", "small", "--output-dir", f"{tmp}/archive"])
            pid, fd = pty.fork()
            if pid == 0:  # child: size its terminal before curses reads it, then exec
                set_winsize(0, rows=24, columns=100)
                os.environ.update(TERM="xterm-256color", LC_ALL="C.UTF-8")
                os.execv(
                    sys.executable,
                    [sys.executable, str(ROOT / "tab_capsule.py"), "browse",
                     "--data-dir", f"{tmp}/archive"],
                )  # fmt: skip
            try:
                screen = read_until(fd, b"Tab Capsule")
                self.assertIn(b"2026-09-03", screen)
                # Keys arrive as escape sequences. Down (xterm keypad mode: ESC O B) must be
                # decoded, and an unknown sequence ignored rather than read as Esc (quit).
                os.write(fd, b"\t")
                os.write(fd, b"\x1b[99~")
                time.sleep(0.1)
                os.write(fd, b"\x1bOB")
                read_until(fd, b"02 September 2026")
                os.write(fd, b"?")
                read_until(fd, b"older/newer day")
                os.write(fd, b"\x1b")  # a lone Esc closes the help line
                read_until(fd, b"? help")
                os.write(fd, b"q")
                self.assertEqual(wait_exit(pid), 0)
            finally:
                with contextlib.suppress(ProcessLookupError, ChildProcessError):
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                os.close(fd)

    def test_browse_refuses_without_a_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(ROOT / "tab_capsule.py"), "browse", "--data-dir", tmp],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("interactive terminal", result.stderr)

    def test_cli_lists_browse(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(SystemExit):
            cli.main(["--help"])
        self.assertIn("browse", out.getvalue())


def set_winsize(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


def read_until(fd: int, marker: bytes, timeout: float = 10.0) -> bytes:
    output = b""
    deadline = time.monotonic() + timeout
    quiet_since = None
    # Read until the marker appears and the screen has stopped updating for 0.2 s.
    while marker not in output or quiet_since is None or time.monotonic() - quiet_since < 0.2:
        if time.monotonic() > deadline:
            raise AssertionError(f"{marker!r} not shown; got {output[-500:]!r}")
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            quiet_since = quiet_since or time.monotonic()
            continue
        quiet_since = None
        try:
            output += os.read(fd, 65536)
        except OSError:  # the program exited and closed the terminal
            if marker not in output:
                raise AssertionError(f"exited before showing {marker!r}: {output[-500:]!r}")
            break
    return output


def wait_exit(pid: int, timeout: float = 10.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        done, status = os.waitpid(pid, os.WNOHANG)
        if done:
            return os.waitstatus_to_exitcode(status)
        time.sleep(0.02)
    raise AssertionError("browse did not exit after q")


if __name__ == "__main__":
    unittest.main()
