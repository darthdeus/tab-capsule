#!/usr/bin/env python3
"""Generate an invented archive without accessing Chrome or personal history."""

import argparse
import datetime
import json
import shutil
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from tab_capsule import chrome, render, storage
from tab_capsule.models import Tab
from tests import corpus
from tests.fixtures import Snss, make_history

PAGES = [
    ("https://example.org/css-grid", "A practical guide to CSS grid layouts"),
    ("https://example.org/shaders", "Writing a small pixel shader"),
    ("https://example.net/postgres", "Postgres indexing and query plans"),
    ("https://example.net/game-loop", "Understanding the game loop"),
]
END = datetime.datetime(2026, 9, 4, 12)


def small_corpus(start: datetime.datetime, days: int, visits_per_day: int) -> corpus.Profile:
    """The fixed four-page demo; browser tests assert on its exact contents."""
    profile = corpus.Profile()
    for day in range(days):
        taken_at = (start + datetime.timedelta(days=day)).timestamp()
        offset = day % len(PAGES) if day >= 3 else 0
        pages = PAGES[offset:] + PAGES[:offset]
        tabs: list[Tab] = [
            {"url": url, "title": title, "pinned": index == 0}
            for index, (url, title) in enumerate(pages[: min(day + 2, len(PAGES))])
        ]
        profile.snapshots.append((taken_at, [tabs]))
        interval = min(300.0, 8 * 3600 / visits_per_day)
        for visit in range(visits_per_day):
            cycle, page_index = divmod(visit, len(PAGES))
            url, title = PAGES[page_index]
            if cycle:
                url += f"?article={cycle}"
                title += f" · article {cycle}"
            profile.visits.append((url, title, taken_at + visit * interval, corpus.TYPED))
    return profile


def populate(
    db: sqlite3.Connection,
    *,
    days: int,
    visits_per_day: int,
    profiles: int,
    chrome_dir: Path | None,
    kind: str = "small",
) -> None:
    # Larger datasets extend backward so collection does not start from future snapshots.
    start = END - datetime.timedelta(days=days)
    if kind == "realistic":
        generated = corpus.generate(
            start=start, days=days, visits_per_day=visits_per_day, profiles=profiles
        )
    else:
        generated = [small_corpus(start, days, visits_per_day) for _ in range(profiles)]
    names = {}
    for profile_index, archive in enumerate(generated):
        profile = "Default" if profile_index == 0 else f"Profile {profile_index}"
        name = "Demo" if profile_index == 0 else f"Demo {profile_index + 1}"
        names[profile] = {"name": name}
        storage.set_profile_name(db, profile, name)
        for taken_at, windows in archive.snapshots:
            storage.store_snapshot(db, profile, windows, now=taken_at, source="synthetic")
        with db:
            db.executemany(
                "INSERT INTO visits(profile,visit_time,url,title,transition,visit_duration)"
                " VALUES(?,?,?,?,?,?)",
                (
                    (profile, chrome.unix_to_webkit(t), url, title, transition, 30)
                    for url, title, t, transition in archive.visits
                ),
            )

        if chrome_dir is not None:
            profile_dir = chrome_dir / profile
            sessions = profile_dir / "Sessions"
            sessions.mkdir(parents=True)
            make_history(
                profile_dir / "History",
                ((i, url, title, t) for i, (url, title, t, _) in enumerate(archive.visits, 1)),
            )
            session = Snss()
            tab_id = 0
            for window_id, window in enumerate(archive.snapshots[-1][1], start=1):
                session.window_type(window_id)
                for index, tab in enumerate(window):
                    tab_id += 1
                    session.tab_window(window_id, tab_id).tab_index(tab_id, index)
                    session.nav(tab_id, 0, tab["url"], tab["title"])
                    session.pinned(tab_id, tab["pinned"])
            (sessions / "Session_1").write_bytes(session.bytes())

    if chrome_dir is not None:
        (chrome_dir / "Local State").write_text(
            json.dumps({"profile": {"info_cache": names}}), encoding="utf-8"
        )


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--keep-db", action="store_true", help="keep the archive and fake Chrome profiles"
    )
    parser.add_argument("--days", type=positive_int, default=3)
    parser.add_argument(
        "--visits-per-day", type=positive_int, default=4, help="visits per profile/day"
    )
    parser.add_argument("--profiles", type=positive_int, default=1)
    parser.add_argument(
        "--corpus",
        choices=["small", "realistic"],
        default="small",
        help="small: four fixed pages (tests rely on it); realistic: hundreds of invented"
        " pages with repeat visits, searches and drifting tabs",
    )
    args = parser.parse_args(argv)
    if args.keep_db and args.output_dir.exists():
        parser.error("persistent demo output directory must be new; choose another --output-dir")

    # Build everything in a temporary directory; persistent mode copies only completed fixtures.
    with tempfile.TemporaryDirectory(prefix="tab-capsule-demo-") as tmp:
        staging = Path(tmp)
        with closing(storage.open_db(staging)) as db:
            populate(
                db,
                days=args.days,
                visits_per_day=args.visits_per_day,
                profiles=args.profiles,
                chrome_dir=staging / "chrome" if args.keep_db else None,
                kind=args.corpus,
            )
            data = render.build_data(db)
            data["generated"] = int(END.timestamp())
            (staging / "index.html").write_text(render.render_html(data), encoding="utf-8")

        if args.keep_db:
            shutil.copytree(staging, args.output_dir)
        else:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(staging / "index.html", args.output_dir / "index.html")
    print(args.output_dir / "index.html")


if __name__ == "__main__":
    main()
