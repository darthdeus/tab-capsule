#!/usr/bin/env python3
"""Generate an invented archive without accessing Chrome or personal history."""

import argparse
import tempfile
from pathlib import Path

import tab_capsule as tc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    pages = [
        ("https://example.org/css-grid", "A practical guide to CSS grid layouts"),
        ("https://example.org/shaders", "Writing a small pixel shader"),
        ("https://example.net/postgres", "Postgres indexing and query plans"),
        ("https://example.net/game-loop", "Understanding the game loop"),
    ]
    base = 1788264000  # fixed synthetic timestamp
    with tempfile.TemporaryDirectory(prefix="tab-capsule-demo-") as tmp:
        db = tc.open_db(tmp)
        try:
            tc.set_profile_name(db, "Default", "Demo")
            for day in range(3):
                tabs = [
                    {"url": u, "title": t, "pinned": i == 0}
                    for i, (u, t) in enumerate(pages[: day + 2])
                ]
                tc.store_snapshot(db, "Default", [tabs], now=base + day * 86400)
                db.executemany(
                    "INSERT INTO visits(profile,visit_time,url,title,transition,visit_duration) VALUES(?,?,?,?,?,?)",
                    [
                        ("Default", tc.unix_to_webkit(base + day * 86400 + i * 300), u, t, 1, 30)
                        for i, (u, t) in enumerate(pages)
                    ],
                )
            db.commit()
            data = tc.build_data(db)
            data["generated"] = base + 3 * 86400
            args.output_dir.mkdir(parents=True, exist_ok=True)
            dest = args.output_dir / "index.html"
            dest.write_text(tc.render_html(data), encoding="utf-8")
            print(dest)
        finally:
            db.close()


if __name__ == "__main__":
    main()
