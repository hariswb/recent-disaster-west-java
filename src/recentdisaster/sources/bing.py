"""Bing News RSS search. Links point straight at the publisher (unlike Google News),
so the article text can always be read for verification."""

from itertools import product
from urllib.parse import parse_qs, quote_plus, urlsplit

import feedparser
import httpx

from recentdisaster.models import RawItem
from recentdisaster.normalize import clean_title, parse_date, strip_html

BASE = "https://www.bing.com/news/search?q={q}&format=rss&setlang=id&cc=ID&qft=interval%3d%22{interval}%22"
INTERVAL = {"1d": 7, "7d": 8, "30d": 9}  # Bing's freshness codes


def queries(source: dict) -> list[str]:
    """Explicit `queries`, plus every `terms` × `topics` combination."""
    qs = list(source.get("queries", []))
    qs += [f"{t} {p}" for t, p in product(source.get("terms", []), source.get("topics", []))]
    return qs


def _target(link: str) -> str:
    """Bing wraps links in apiclick.aspx?url=…"""
    return parse_qs(urlsplit(link).query).get("url", [link])[0]


def parse(content: bytes, source: dict) -> list[RawItem]:
    out = []
    for e in feedparser.parse(content).entries:
        if not e.get("link") or not e.get("title"):
            continue
        out.append(RawItem(
            source_id=source["id"],
            source_name=e.get("news_source") or source["name"],
            url=_target(e.link),
            title=clean_title(e.title),
            summary=strip_html(e.get("summary"))[:1000],
            published=parse_date(e.get("published_parsed")),
            jabar_only=False,
        ))
    return out


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    interval = INTERVAL.get(source.get("window", "7d"), 8)
    items, errors = [], []
    for q in queries(source):
        try:
            r = c.get(BASE.format(q=quote_plus(q), interval=interval))
            r.raise_for_status()
            items.extend(parse(r.content, source))
        except httpx.HTTPError as e:
            errors.append(f"{q[:30]}…: {e}")
    if errors and not items:
        raise RuntimeError("; ".join(errors))
    return items
