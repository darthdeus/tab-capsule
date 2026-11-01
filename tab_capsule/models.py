"""Shared tab records and the compact data embedded in the HTML page."""

from collections.abc import Callable
from typing import TypeAlias, TypedDict


class _TabFields(TypedDict):
    url: str
    title: str
    pinned: bool


class Tab(_TabFields, total=False):
    last_active: float | None
    nav_time: float | None


Windows: TypeAlias = list[list[Tab]]
Log: TypeAlias = Callable[[str], None]


class PageSnapshot(TypedDict):
    p: int
    d: str
    t: int
    s: int
    w: list[list[list[int]]]


class PageData(TypedDict):
    S: list[str]
    P: list[str]
    V: list[list[int]]
    D: list[list[str | int]]
    T: list[PageSnapshot]
    generated: int
