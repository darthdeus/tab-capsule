"""Interactive terminal browser for an archive: days, open tabs and history.

Uses only the standard library's curses. The browser state (`Browser`) and what it shows
(`Browser.render`) are plain Python, independent of curses, so they can be tested without
a terminal; `run` only draws the rendered lines and translates keys.
"""

from __future__ import annotations

import curses
import datetime
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .models import PageData, PageSnapshot

# Chrome's core page-transition names, indexed by `transition & 0xFF`.
TRANSITIONS = (
    "link",
    "typed",
    "bookmark",
    "subframe",
    "subframe",
    "generated",
    "toplevel",
    "form",
    "reload",
    "keyword",
    "keyword",
)
DAYS_WIDTH = 19  # " 2026-09-03 •  1234"
MIN_WIDTH_FOR_DAYS = 60

Style = str  # one of: bar, normal, dim, heading, profile, new, day, day_selected, selected
Segment = tuple[str, Style]
Line = list[Segment]


# Text measured in terminal cells


def cell_width(ch: str) -> int:
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_width(text: str) -> int:
    return sum(cell_width(ch) for ch in text)


def clean(text: str) -> str:
    """Replace control and format characters (tabs, newlines, bidi marks) with spaces."""
    return "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in text)


def fit(text: str, width: int) -> str:
    """Exactly `width` cells: truncated with an ellipsis, or padded with spaces."""
    if width <= 0:
        return ""
    text = clean(text)
    if text_width(text) <= width:
        return text + " " * (width - text_width(text))
    out, used = [], 0
    for ch in text:
        w = cell_width(ch)
        if used + w > width - 1:
            break
        out.append(ch)
        used += w
    return "".join(out) + "…" + " " * (width - 1 - used)


def domain(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    host = parts.hostname or ""
    return host.removeprefix("www.") or parts.scheme


def clock(t: int) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%H:%M")


# What a day shows


@dataclass(frozen=True)
class Row:
    """One line of the day view. Rows with a URL can be selected."""

    kind: str  # heading, profile, window, tab, hour, visit, empty
    text: str
    url: str = ""
    time: str = ""
    note: str = ""
    pinned: bool = False
    new: bool = False

    @property
    def selectable(self) -> bool:
        return bool(self.url)


class Archive:
    """Day-oriented views over the compact page data, matching the HTML page."""

    def __init__(self, data: PageData) -> None:
        self.data = data
        self.days = [str(d[0]) for d in data["D"]]  # newest first
        self._ranges = {str(d[0]): (int(d[1]), int(d[2])) for d in data["D"]}
        self._snapshots: dict[int, list[PageSnapshot]] = {}
        for snapshot in data["T"]:
            self._snapshots.setdefault(snapshot["p"], []).append(snapshot)
        for snapshots in self._snapshots.values():
            snapshots.sort(key=lambda s: s["d"])
        self._snapshot_days = {s["d"] for s in data["T"]}

    def visit_count(self, day: str) -> int:
        start, end = self._ranges.get(day, (0, 0))
        return end - start

    def has_snapshot(self, day: str) -> bool:
        return day in self._snapshot_days

    def snapshot_as_of(self, profile: int, day: str) -> PageSnapshot | None:
        """The profile's last snapshot on or before `day` (tabs carry forward)."""
        found = None
        for snapshot in self._snapshots.get(profile, []):
            if snapshot["d"] > day:
                break
            found = snapshot
        return found

    def rows(self, day: str) -> list[Row]:
        S, P, V = self.data["S"], self.data["P"], self.data["V"]
        rows = [Row("heading", "Open tabs")]
        any_snapshot = False
        for profile, name in enumerate(P):
            snapshot = self.snapshot_as_of(profile, day)
            if snapshot is None:
                continue
            any_snapshot = True
            windows = snapshot["w"]
            tabs = sum(len(w) for w in windows)
            new = sum(tab[3] for w in windows for tab in w)
            when = (
                f"snapshot {clock(snapshot['t'])}"
                if snapshot["d"] == day
                else f"carried forward from {snapshot['d']}"
            )
            summary = f"{tabs} tabs · {len(windows)} windows" + (f" · {new} new" if new else "")
            rows.append(Row("profile", name, note=f"{summary} · {when}"))
            for index, window in enumerate(windows):
                if len(windows) > 1:
                    rows.append(Row("window", f"Window {index + 1}", note=f"{len(window)} tabs"))
                for url, title, pinned, is_new in window:
                    rows.append(
                        Row(
                            "tab",
                            S[title] or S[url],
                            url=S[url],
                            note=domain(S[url]),
                            pinned=bool(pinned),
                            new=bool(is_new),
                        )
                    )
        if not any_snapshot:
            rows.append(Row("empty", "No tab snapshot existed yet on this day."))

        start, end = self._ranges.get(day, (0, 0))
        rows.append(Row("heading", "History", note=f"{end - start} visits"))
        if start == end:
            rows.append(Row("empty", "No visits."))
        hour = ""
        for i in range(end - 1, start - 1, -1):
            t, url, title, profile, transition = V[i]
            time = clock(t)
            if time[:2] != hour:
                hour = time[:2]
                rows.append(Row("hour", f"{hour}:00"))
            note = domain(S[url])
            if len(P) > 1:
                note += f" · {P[profile]}"
            if transition < len(TRANSITIONS) and TRANSITIONS[transition] != "link":
                note += f" · {TRANSITIONS[transition]}"
            rows.append(Row("visit", S[title] or S[url], url=S[url], time=time, note=note))
        return rows


# Browser state and keys


@dataclass
class Browser:
    """Keyboard-driven state: which day, which pane has focus, which row is selected.

    Keys are names, not curses codes: "up", "down", "left", "right", "tab", "pageup",
    "pagedown", "home", "end", "enter", "esc", "resize", or a typed character.
    """

    archive: Archive
    day: int = 0
    focus: str = "rows"  # or "days"
    cursor: int = -1  # index into rows of the selected (selectable) row, -1 if none
    top: int = 0  # first visible row
    days_top: int = 0
    detail: bool = False
    help: bool = False
    _rows: list[Row] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._load_day()

    @property
    def rows(self) -> list[Row]:
        return self._rows

    @property
    def day_name(self) -> str:
        return self.archive.days[self.day] if self.archive.days else ""

    @property
    def selected(self) -> Row | None:
        return self._rows[self.cursor] if self.cursor >= 0 else None

    def _load_day(self) -> None:
        self._rows = self.archive.rows(self.day_name) if self.archive.days else []
        selectable = [i for i, row in enumerate(self._rows) if row.selectable]
        self.cursor = selectable[0] if selectable else -1
        self.top = 0
        self.detail = False

    def _select_day(self, index: int) -> None:
        index = max(0, min(index, len(self.archive.days) - 1))
        if index != self.day:
            self.day = index
            self._load_day()

    def _move(self, steps: int) -> None:
        """Move the row cursor by `steps` selectable rows (negative is up)."""
        selectable = [i for i, row in enumerate(self._rows) if row.selectable]
        if not selectable:
            return
        position = selectable.index(self.cursor) if self.cursor in selectable else 0
        position = max(0, min(position + steps, len(selectable) - 1))
        self.cursor = selectable[position]
        if position == 0:
            self.top = 0  # show the headings above the first entry
        self.detail = False

    def handle(self, key: str, page: int = 20) -> bool:
        """Apply one key; `page` is the number of visible rows. False means quit."""
        if key in ("q", "esc"):
            if key == "esc" and (self.detail or self.help):
                self.detail = self.help = False
                return True
            return False
        if key == "?":
            self.help = not self.help
        elif key in ("tab", "left", "right", "h", "l"):
            wanted = {"left": "days", "h": "days", "right": "rows", "l": "rows"}
            self.focus = wanted.get(key, "rows" if self.focus == "days" else "days")
        elif key in ("[", "]"):
            self._select_day(self.day + (1 if key == "[" else -1))
        elif key == "enter":
            if self.focus == "days":
                self.focus = "rows"
            elif self.selected is not None:
                self.detail = not self.detail
        else:
            steps = {
                "down": 1, "j": 1, "up": -1, "k": -1,
                "pagedown": page, "pageup": -page,
                "home": -(10**9), "g": -(10**9), "end": 10**9, "G": 10**9,
            }.get(key)  # fmt: skip
            if steps is not None:
                if self.focus == "days":
                    self._select_day(self.day + steps)
                else:
                    self._move(steps)
        return True

    # Rendering to styled text lines, independent of curses

    def render(self, height: int, width: int) -> list[Line]:
        """Exactly `height` lines, each exactly `width` cells wide."""
        if height < 3 or width < 20:
            return [[(fit("Terminal too small" if y == 0 else "", width), "normal")]
                    for y in range(height)]  # fmt: skip
        body = height - 2
        show_days = width >= MIN_WIDTH_FOR_DAYS and bool(self.archive.days)
        days_width = DAYS_WIDTH if show_days else 0
        rows_width = width - days_width - (1 if show_days else 0)
        self._scroll(body)

        lines: list[Line] = [self._title_bar(width)]
        day_lines = self._day_lines(body, days_width) if show_days else []
        row_lines = self._row_lines(body, rows_width)
        for y in range(body):
            line: Line = []
            if show_days:
                line += day_lines[y]
                line.append(("│", "dim"))
            line += row_lines[y]
            lines.append(line)
        lines.append(self._footer(width))
        return lines

    def _scroll(self, body: int) -> None:
        if self.cursor >= 0:
            if self.cursor < self.top:
                self.top = self.cursor
            elif self.cursor >= self.top + body:
                self.top = self.cursor - body + 1
        self.top = max(0, min(self.top, max(0, len(self._rows) - body)))
        if self.day < self.days_top:
            self.days_top = self.day
        elif self.day >= self.days_top + body:
            self.days_top = self.day - body + 1

    def _title_bar(self, width: int) -> Line:
        if not self.archive.days:
            return [(fit(" Tab Capsule", width), "bar")]
        date = datetime.date.fromisoformat(self.day_name)
        visits = self.archive.visit_count(self.day_name)
        title = f" Tab Capsule · {date.strftime('%A, %d %B %Y')} · {visits} visits"
        return [(fit(title, width), "bar")]

    def _day_lines(self, body: int, width: int) -> list[Line]:
        lines: list[Line] = []
        for y in range(body):
            index = self.days_top + y
            if index >= len(self.archive.days):
                lines.append([(" " * width, "normal")])
                continue
            day = self.archive.days[index]
            mark = "•" if self.archive.has_snapshot(day) else " "
            count = self.archive.visit_count(day) or ""
            text = f" {day} {mark}{count:>{width - 14}} "
            if index == self.day:
                style = "selected" if self.focus == "days" else "day_selected"
            else:
                style = "day"
            lines.append([(fit(text, width), style)])
        return lines

    def _row_lines(self, body: int, width: int) -> list[Line]:
        if not self.archive.days:
            empty = fit(
                "  No data yet. Run `tab_capsule.py run` to collect tabs and history.", width
            )
            return [[(empty if y == 1 else " " * width, "dim")] for y in range(body)]
        lines: list[Line] = []
        for y in range(body):
            index = self.top + y
            if index >= len(self._rows):
                lines.append([(" " * width, "normal")])
            else:
                selected = index == self.cursor and self.focus == "rows"
                lines.append(self._row_line(self._rows[index], width, selected))
        return lines

    def _row_line(self, row: Row, width: int, selected: bool) -> Line:
        if row.kind == "heading":
            text = fit("─" + f" {row.text} " + (f"· {row.note} " if row.note else ""), width)
            stripped = text.rstrip()
            fill = width - text_width(stripped) - 1
            return [(stripped + " " + "─" * fill if fill > 0 else text, "heading")]
        if row.kind == "hour":
            return [(fit(f"  {row.text}", width), "dim")]
        if row.kind == "empty":
            return [(fit(f"  {row.text}", width), "dim")]
        if row.kind == "profile":
            return self._split(f" {row.text}", row.note, width, "profile", selected)
        if row.kind == "window":
            return self._split(f"   {row.text}", row.note, width, "dim", selected)
        if row.kind == "tab":
            mark = "^" if row.pinned else "+" if row.new else " "
            style = "new" if row.new else "normal"
            return self._split(f"   {mark} {row.text}", row.note, width, style, selected)
        return self._split(f"   {row.time}  {row.text}", row.note, width, "normal", selected)

    @staticmethod
    def _split(left: str, note: str, width: int, style: Style, selected: bool) -> Line:
        """Left text and a right-aligned dim note, dropping the note when space is short."""
        note = clean(note)
        note_width = text_width(note) + 1 if note else 0
        # Keep the note unless it would squeeze the text below its length or half the width.
        if note_width and width - note_width < min(text_width(left), max(20, width // 2)):
            note, note_width = "", 0
        main = fit(left, width - note_width)
        if selected:
            return [(main + (" " + note if note else ""), "selected")]
        return [(main, style)] + ([(" " + note, "dim")] if note else [])

    def _footer(self, width: int) -> Line:
        row = self.selected
        if self.detail and row is not None:
            return [(fit(f" {row.url}  —  {clean(row.text)}", width), "bar")]
        if self.help:
            text = (
                " jk move · PgUp/PgDn page · g/G first/last · hl/Tab pane"
                " · [ ] older/newer day · Enter details · Esc close · q quit"
            )
        else:
            text = " ↑↓ move  ←→ pane  [ ] day  Enter details  ? help  q quit"
        return [(fit(text, width), "bar")]


# Curses front end

CURSES_KEYS = {
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    curses.KEY_PPAGE: "pageup",
    curses.KEY_NPAGE: "pagedown",
    curses.KEY_HOME: "home",
    curses.KEY_END: "end",
    curses.KEY_ENTER: "enter",
    curses.KEY_RESIZE: "resize",
}
CHARACTER_KEYS = {"\t": "tab", "\n": "enter", "\r": "enter", "\x1b": "esc"}


def key_name(key: int | str) -> str:
    if isinstance(key, int):
        return CURSES_KEYS.get(key, "")
    return CHARACTER_KEYS.get(key, key)


def read_key(screen: curses.window) -> str:
    """Next key name. A lone Esc is "esc"; an escape sequence curses could not decode
    (Alt+key, keys missing from terminfo) is drained and ignored instead of quitting."""
    key = screen.get_wch()
    if key != "\x1b":
        return key_name(key)
    screen.nodelay(True)
    try:
        pending = False
        while True:
            try:
                screen.get_wch()
            except curses.error:
                break
            pending = True
    finally:
        screen.nodelay(False)
    return "" if pending else "esc"


def _styles() -> dict[Style, int]:
    styles: dict[Style, int] = {
        "bar": curses.A_REVERSE,
        "normal": curses.A_NORMAL,
        "dim": curses.A_DIM,
        "heading": curses.A_BOLD,
        "profile": curses.A_BOLD,
        "new": curses.A_BOLD,
        "day": curses.A_NORMAL,
        "day_selected": curses.A_BOLD | curses.A_UNDERLINE,
        "selected": curses.A_REVERSE,
    }
    if curses.has_colors():
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for number, color in ((1, curses.COLOR_CYAN), (2, curses.COLOR_GREEN)):
            curses.init_pair(number, color, background)
        styles["heading"] |= curses.color_pair(1)
        styles["new"] |= curses.color_pair(2)
    return styles


def _main(screen: curses.window, browser: Browser) -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    curses.set_escdelay(25)  # Esc closes details; don't wait a second for a sequence
    styles = _styles()
    screen.keypad(True)
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        for y, line in enumerate(browser.render(height, width)):
            x = 0
            for text, style in line:
                try:
                    screen.addstr(y, x, text, styles.get(style, curses.A_NORMAL))
                except curses.error:
                    pass  # writing the bottom-right cell moves the cursor off-screen
                x += text_width(text)
        screen.refresh()
        if not browser.handle(read_key(screen), page=max(1, height - 2)):
            return


def run(data: PageData) -> None:
    """Show the archive until the user quits. Needs an interactive terminal."""
    curses.wrapper(_main, Browser(Archive(data)))
