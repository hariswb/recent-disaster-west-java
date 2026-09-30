import feedparser
import httpx

from recentdisaster.models import RawItem
from recentdisaster.normalize import clean_title, parse_date, strip_html


def parse_feed(content: bytes | str, source: dict) -> list[RawItem]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        link = e.get("link")
        title = e.get("title")
        if not link or not title:
            continue
        publisher = None
        if source["type"] == "gnews":
            publisher = (e.get("source") or {}).get("title")
        items.append(
            RawItem(
                source_id=source["id"],
                source_name=publisher or source["name"],
                url=link,
                title=clean_title(title, publisher),
                # Google News summaries only repeat the title + publisher name
                # (e.g. "Tribun Jabar"), which would fake a location match.
                summary="" if publisher else strip_html(e.get("summary"))[:1000],
                published=parse_date(e.get("published_parsed") or e.get("updated_parsed")),
                jabar_only=source.get("jabar_only", False),
                publisher=publisher,
            )
        )
    return items


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    r = c.get(source["url"])
    r.raise_for_status()
    return parse_feed(r.content, source)
