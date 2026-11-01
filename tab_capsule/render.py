"""Prepare archive data and assemble the standalone HTML page."""

import base64
import datetime
import gzip
import json
import os
import time
from pathlib import Path

from .chrome import WEBKIT_EPOCH_OFFSET_US

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def local_day(t):
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")


def build_data(db):
    """Compact JSON-able dict for the HTML page.

    S: string table (urls and titles). P: profile display names.
    V: visits sorted by time, each [unix_s, url_i, title_i, profile_i, transition_core].
    D: days newest first, each [day, visit_start, visit_end] into V (end exclusive).
    T: last snapshot per profile per day, each {p, d, t, s, w:[[ [url_i, title_i, pinned, new], ...], ...]}.
       `new` = url not in that profile's previous embedded snapshot.
    """
    strings, index = [], {}

    def si(s):
        s = s or ""
        if s not in index:
            index[s] = len(strings)
            strings.append(s)
        return index[s]

    names = dict(db.execute("SELECT dir, name FROM profiles"))
    profiles = sorted(
        set(names)
        | {r[0] for r in db.execute("SELECT DISTINCT profile FROM snapshots")}
        | {r[0] for r in db.execute("SELECT DISTINCT profile FROM visits")}
    )
    pidx = {p: i for i, p in enumerate(profiles)}
    plabels = [names.get(p) or p for p in profiles]

    visits, day_ranges = [], {}
    for vt, url, title, prof, tr in db.execute(
        "SELECT visit_time, url, title, profile, transition FROM visits ORDER BY visit_time, profile, url"
    ):
        t = (vt - WEBKIT_EPOCH_OFFSET_US) // 1_000_000
        if t <= 0:
            continue
        day = local_day(t)
        rng = day_ranges.setdefault(day, [len(visits), len(visits)])
        visits.append([t, si(url), si(title), pidx[prof], (tr or 0) & 0xFF])
        rng[1] = len(visits)

    snaps = []
    for prof in profiles:
        rows = db.execute(
            "SELECT id, taken_at, last_seen_at FROM snapshots WHERE profile=? ORDER BY taken_at, id",
            (prof,),
        ).fetchall()
        last_of_day = {}
        for sid, taken, seen in rows:
            last_of_day[local_day(taken)] = (sid, taken, seen)
        prev_urls = None
        for day in sorted(last_of_day):
            sid, taken, seen = last_of_day[day]
            wins, urls = [], set()
            for w, url, title, pinned in db.execute(
                "SELECT window_idx, url, title, pinned FROM snapshot_tabs WHERE snapshot_id=? ORDER BY window_idx, tab_idx",
                (sid,),
            ):
                while len(wins) <= w:
                    wins.append([])
                urls.add(url)
                wins[w].append(
                    [
                        si(url),
                        si(title),
                        pinned,
                        int(prev_urls is not None and url not in prev_urls),
                    ]
                )
            snaps.append(
                {"p": pidx[prof], "d": day, "t": taken, "s": seen, "w": [x for x in wins if x]}
            )
            prev_urls = urls

    days = set(day_ranges) | {s["d"] for s in snaps}
    D = [[d] + day_ranges.get(d, [0, 0]) for d in sorted(days, reverse=True)]
    return {
        "S": strings,
        "P": plabels,
        "V": visits,
        "D": D,
        "T": snaps,
        "generated": int(time.time()),
    }


def render_html(data):
    """Embed the data as base64(gzip(JSON)); the page inflates it with DecompressionStream.
    URLs compress ~4x, which matters once years of visits accumulate.
    Frontend sources are resolved relative to the source root, independently of the cwd."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(gzip.compress(raw, compresslevel=9, mtime=0)).decode("ascii")
    template = (FRONTEND_DIR / "capsule.html").read_text(encoding="utf-8")
    # Inline local sources so the output can be moved and opened directly via file://.
    return (
        template.replace("/*STYLE*/", (FRONTEND_DIR / "capsule.css").read_text(encoding="utf-8"))
        .replace("/*SCRIPT*/", (FRONTEND_DIR / "capsule.js").read_text(encoding="utf-8"))
        .replace("/*DATA*/", blob)
    )


def extract_data(html):
    """Inverse of render_html (used by tests and for debugging)."""
    start = html.index('<script type="application/octet-stream" id="data">') + len(
        '<script type="application/octet-stream" id="data">'
    )
    blob = html[start : html.index("</script>", start)]
    return json.loads(gzip.decompress(base64.b64decode(blob)))


def cmd_render(db, data_dir, log=print):
    html = render_html(build_data(db))
    out = os.path.join(data_dir, "index.html")
    tmp = out + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(html)
    os.replace(tmp, out)
    log(f"wrote {out} ({len(html) // 1024} KiB)")
