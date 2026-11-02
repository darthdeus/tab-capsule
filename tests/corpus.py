"""Invented but realistically shaped browsing, for demos and search work.

Every page, title and query here is generated from the word lists below; nothing comes
from a real browser. The shape is what matters: a few pages visited many times a day,
a long tail of one-off pages and searches, pages discovered over time, tabs that drift
from day to day, and words in which short terms occur mid-word ("api" in "rapid", "pi"
in "pipeline", "gh" in "high"), as in real history. Output is deterministic per seed.
"""

from __future__ import annotations

import datetime
import random
import string
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import quote_plus

from tab_capsule.models import Tab, Windows

# A generic game and web developer's interests: mainstream tools and topics, nothing
# specific to any real person. Words were chosen so that common short queries also
# match inside unrelated words: "api" in "rapid", "pi" in "pixel" and "pipeline",
# "gh" in "lighting" and "high", "tab" in "hashtable", "model" in "remodel".
NOUNS = [
    "rest api", "rapid prototyping", "capitalization", "pipeline", "pixel shader",
    "loading spinner", "epic", "lighting", "shadow mapping", "high score table",
    "night mode", "weighted random", "throughput", "highlight", "game loop", "hashtable",
    "database", "stable release", "data model", "3d model", "remodel", "collision detection",
    "physics engine", "pathfinding", "tilemap", "sprite sheet", "particle system",
    "entity component system", "save system", "input handling", "camera shake",
    "level editor", "inventory system", "dialogue system", "state machine", "multiplayer",
    "netcode", "matchmaking", "leaderboard", "achievement", "frame rate", "vsync",
    "render loop", "shader graph", "normal map", "post processing", "audio mixer",
    "react hooks", "css grid", "flexbox", "dark mode", "responsive layout", "form validation",
    "authentication", "oauth", "jwt", "session cookie", "cors", "websocket", "rest endpoint",
    "graphql schema", "sql query", "database migration", "orm", "redis cache", "cdn",
    "docker image", "kubernetes", "ci pipeline", "unit test", "end-to-end test",
    "code review", "merge conflict", "git rebase", "memory leak", "profiler", "benchmark",
    "bundle size", "lazy loading", "service worker", "web worker", "typescript types",
    "async await", "promise", "event listener", "virtual list", "infinite scroll",
]  # fmt: skip
VERBS = [
    "debug", "profile", "speed up", "configure", "migrate", "test", "deploy", "write",
    "understand", "replace", "cache", "optimize", "refactor", "render", "sort", "animate",
]  # fmt: skip
ADJECTIVES = [
    "fast", "minimal", "practical", "simple", "scalable", "portable", "typed", "modern",
    "deterministic", "responsive", "robust", "tiny", "accessible", "legacy",
]  # fmt: skip
LANGUAGES = ["javascript", "typescript", "python", "c#", "c++", "java", "go", "php"]
OWNERS = [
    "acme", "openlab", "pixelforge", "webcraft", "brightside", "gamelab", "tidepool",
    "nightowl-studio", "devkit-org", "sunbeam", "northwind", "bluefox-games",
]  # fmt: skip
SUBREDDITS = ["webdev", "gamedev", "programming", "javascript", "Unity3D", "godot", "reactjs"]
PYTHON_MODULES = [
    ("json", "JSON encoder and decoder"),
    ("asyncio", "Asynchronous I/O"),
    ("pathlib", "Object-oriented filesystem paths"),
    ("unittest", "Unit testing framework"),
    ("datetime", "Basic date and time types"),
    ("re", "Regular expression operations"),
    ("typing", "Support for type hints"),
    ("logging", "Logging facility for Python"),
]
WEB_APIS = [
    "Fetch_API", "WebSocket", "Canvas_API", "Window/requestAnimationFrame",
    "IntersectionObserver", "Window/localStorage", "KeyboardEvent", "Web_Workers_API",
]  # fmt: skip
NPM_PACKAGES = ["react", "express", "vite", "axios", "lodash", "three", "phaser", "socket.io"]
ENGINE_DOCS = [
    ("docs.unity3d.com/Manual", "Unity - Manual"),
    ("docs.godotengine.org/en/stable/tutorials", "Godot Engine documentation"),
    ("dev.epicgames.com/documentation/unreal-engine", "Unreal Engine Documentation"),
]

# Pages opened on most days, with how many times a day each is visited on average.
DAILY = [
    ("https://mail.example.com/inbox", "Inbox - demo@example.com - Mail", 12.0),
    ("https://calendar.example.com/week", "Calendar - Week view", 4.0),
    ("https://github.com/notifications", "Notifications · GitHub", 6.0),
    ("https://chat.example.com/team/general", "general - Team chat", 9.0),
    ("http://localhost:3000/", "Dev server", 8.0),
]


@dataclass(frozen=True)
class Page:
    url: str
    title: str


@dataclass
class Profile:
    """One profile's archive: snapshots (taken_at, windows) and visits sorted by time."""

    snapshots: list[tuple[float, Windows]] = field(default_factory=list)
    visits: list[tuple[str, str, float, int]] = field(
        default_factory=list
    )  # url, title, t, transition


# Chrome's core transition types: link, typed, generated (search), reload.
LINK, TYPED, GENERATED, RELOAD = 0, 1, 5, 8


def _a(word: str) -> str:
    return ("an " if word[0] in "aeiou" else "a ") + word


def _phrase(rng: random.Random) -> str:
    noun, other = rng.sample(NOUNS, 2)
    return rng.choice(
        [
            f"How to {rng.choice(VERBS)} {_a(noun)} in {rng.choice(LANGUAGES)}",
            f"{_a(rng.choice(ADJECTIVES)).capitalize()} {noun} for {other}",
            f"{noun.capitalize()} vs {other}",
            f"Why your {noun} is slow",
            f"{noun.capitalize()}: {rng.choice(ADJECTIVES)} {other}",
            f"Building {_a(rng.choice(ADJECTIVES))} {noun}",
            f"{rng.choice(VERBS).capitalize()} {noun} with {other}",
        ]
    )


def _slug(text: str) -> str:
    keep = string.ascii_lowercase + string.digits
    return "-".join("".join(c for c in w if c in keep) for w in text.lower().split()).strip("-")


def _page(rng: random.Random) -> Page:
    """One catalogue page on a randomly chosen kind of site."""
    phrase = _phrase(rng)
    kind = rng.choices(
        [
            "github",
            "issue",
            "ghcli",
            "video",
            "question",
            "pydoc",
            "mdn",
            "wiki",
            "npm",
            "engine",
            "reddit",
            "blog",
        ],
        weights=[3, 3, 1, 9, 12, 2, 3, 8, 2, 3, 10, 30],
    )[0]
    owner, repo = rng.choice(OWNERS), _slug(rng.choice(NOUNS))
    number = rng.randrange(10, 99_999)
    if kind == "github":
        return Page(f"https://github.com/{owner}/{repo}", f"{owner}/{repo}: {phrase}")
    if kind == "issue":
        section = rng.choice(["issues", "pull"])
        label = "Issue" if section == "issues" else "Pull Request"
        return Page(
            f"https://github.com/{owner}/{repo}/{section}/{number}",
            f"{phrase} · {label} #{number} · {owner}/{repo}",
        )
    if kind == "ghcli":
        command = rng.choice(["pr create", "pr checkout", "issue list", "repo clone", "run watch"])
        return Page(
            f"https://cli.github.com/manual/gh_{command.replace(' ', '_')}",
            f"gh {command} | GitHub CLI",
        )
    if kind == "video":
        video = "".join(rng.choice(string.ascii_letters + string.digits + "-_") for _ in range(11))
        return Page(f"https://www.youtube.com/watch?v={video}", f"{phrase} - YouTube")
    if kind == "question":
        return Page(
            f"https://stackoverflow.com/questions/{number}/{_slug(phrase)}",
            f"{phrase} - Stack Overflow",
        )
    if kind == "pydoc":
        module, summary = rng.choice(PYTHON_MODULES)
        return Page(
            f"https://docs.python.org/3/library/{module}.html",
            f"{module} — {summary} — Python 3 documentation",
        )
    if kind == "mdn":
        api = rng.choice(WEB_APIS)
        name = api.split("/")[-1].replace("_", " ")
        return Page(
            f"https://developer.mozilla.org/en-US/docs/Web/API/{api}", f"{name} - Web APIs | MDN"
        )
    if kind == "wiki":
        topic = rng.choice(NOUNS).title()
        return Page(
            f"https://en.wikipedia.org/wiki/{topic.replace(' ', '_')}", f"{topic} - Wikipedia"
        )
    if kind == "npm":
        package = rng.choice(NPM_PACKAGES)
        return Page(f"https://www.npmjs.com/package/{package}", f"{package} - npm")
    if kind == "engine":
        path, site = rng.choice(ENGINE_DOCS)
        topic = rng.choice(NOUNS)
        return Page(f"https://{path}/{_slug(topic)}", f"{topic.capitalize()} - {site}")
    if kind == "reddit":
        sub = rng.choice(SUBREDDITS)
        return Page(
            f"https://www.reddit.com/r/{sub}/comments/{number:x}/{_slug(phrase)}/",
            f"{phrase} : r/{sub}",
        )
    host = rng.choice(["blog.example.org", "notes.example.net", "devlog.example.com"])
    return Page(f"https://{host}/{2020 + number % 7}/{_slug(phrase)}", phrase)


def _search(rng: random.Random) -> Page:
    query = " ".join(rng.sample([*NOUNS, *VERBS, *LANGUAGES], rng.randint(1, 3)))
    return Page(f"https://www.google.com/search?q={quote_plus(query)}", f"{query} - Google Search")


def _catalogue(seed: str, size: int, days: int) -> tuple[list[Page], list[int], list[float]]:
    """Pages shared by all profiles, each with a day it is discovered and a Zipf weight."""
    rng = random.Random(f"{seed}:catalogue")
    pages: dict[str, Page] = {}
    while len(pages) < size:
        page = _page(rng)
        pages.setdefault(page.url, page)
    found = [0 if i < size // 5 else rng.randrange(days) for i in range(size)]
    weights = [1 / (rank + 1) ** 0.85 for rank in range(size)]
    return list(pages.values()), found, weights


def generate(
    *,
    start: datetime.datetime,
    days: int,
    visits_per_day: int,
    profiles: int,
    seed: str = "tab-capsule",
) -> list[Profile]:
    """Visits per profile/day are exact; snapshots are taken at `start + day` (noon)."""
    size = max(60, min(60000, days * visits_per_day))
    pages, found, weights = _catalogue(seed, size, days)
    result = []
    for profile_index in range(profiles):
        rng = random.Random(f"{seed}:profile:{profile_index}")
        # Profiles share the web but not their tastes: rotate which pages are favourites.
        shift = profile_index * size // 7
        taste = weights[-shift:] + weights[:-shift] if shift else weights
        daily = DAILY if profile_index == 0 else DAILY[: 2 + profile_index % 3]
        profile = Profile()
        windows: Windows = [[]]
        seen: set[str] = set()
        for day in range(days):
            taken_at = start + datetime.timedelta(days=day)
            midnight = taken_at.replace(hour=0, minute=0, second=0, microsecond=0)
            # Waking hours; the last generated day ends before the demo's "now".
            earliest = midnight + datetime.timedelta(hours=7)
            span = 16 * 3600.0

            # Sample page indices first, then expand searches into query + result pairs.
            known = [i for i in range(size) if found[i] <= day]
            cumulative, total = [], 0.0
            for i in known:
                total += taste[i] * (3.0 if day - found[i] < 3 else 1.0)
                cumulative.append(total)
            daily_total = sum(rate for *_, rate in daily)
            visits: list[tuple[str, str, int]] = []
            while len(visits) < visits_per_day:
                roll = rng.random()
                if roll < daily_total / (daily_total + visits_per_day):
                    url, title, _ = rng.choices(daily, weights=[r for *_, r in daily])[0]
                    visits.append((url, title, TYPED if rng.random() < 0.6 else RELOAD))
                    continue
                page = pages[rng.choices(known, cum_weights=cumulative)[0]]
                if roll > 0.88 and len(visits) + 2 <= visits_per_day:
                    search = _search(rng)
                    visits.append((search.url, search.title, GENERATED))
                visits.append((page.url, page.title, LINK if rng.random() < 0.9 else RELOAD))

            times = sorted(earliest.timestamp() + rng.random() * span for _ in visits)
            used: set[tuple[str, int]] = set()
            for (url, title, transition), t in zip(visits, times):
                # Chrome's key is (profile, microsecond time, url); keep it unique.
                while (url, int(t * 1_000_000)) in used:
                    t += 0.001
                used.add((url, int(t * 1_000_000)))
                profile.visits.append((url, title, t, transition))
                seen.add(url)

            windows = _drift_tabs(rng, windows, visits, daily)
            profile.snapshots.append((taken_at.timestamp(), [list(w) for w in windows if w]))
        profile.visits.sort(key=lambda v: v[2])
        result.append(profile)
    return result


def _drift_tabs(
    rng: random.Random,
    windows: Windows,
    visits: Sequence[tuple[str, str, int]],
    daily: Sequence[tuple[str, str, float]],
) -> Windows:
    """Close some tabs, open some of today's pages, occasionally open another window."""
    pinned: list[Tab] = [{"url": u, "title": t, "pinned": True} for u, t, _ in daily[:2]]
    kept = [
        [tab for tab in window if not tab["pinned"] and rng.random() > 0.22] for window in windows
    ]
    if len(kept) < 3 and rng.random() < 0.12:
        kept.append([])
    candidates = [(u, t) for u, t, kind in visits if kind == LINK]
    for url, title in rng.sample(candidates, min(len(candidates), rng.randint(2, 7))):
        window = kept[rng.randrange(len(kept))]
        if len(window) < 28 and all(tab["url"] != url for w in kept for tab in w):
            window.append({"url": url, "title": title, "pinned": False})
    kept = [w for i, w in enumerate(kept) if w or i == 0]
    kept[0] = pinned + kept[0]
    return kept
