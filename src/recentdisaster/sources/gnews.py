from urllib.parse import quote_plus

import httpx

from recentdisaster.models import RawItem
from recentdisaster.sources.rss import parse_feed

BASE = "https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"


def query_url(query: str, window: str = "1d") -> str:
    return BASE.format(q=quote_plus(f"{query} when:{window}"))


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    items: list[RawItem] = []
    errors = []
    for q in source["queries"]:
        try:
            r = c.get(query_url(q))
            r.raise_for_status()
            items.extend(parse_feed(r.content, source))
        except httpx.HTTPError as e:
            errors.append(f"{q[:30]}…: {e}")
    if errors and not items:
        raise RuntimeError("; ".join(errors))
    return items
