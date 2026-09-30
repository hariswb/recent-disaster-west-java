from dataclasses import dataclass
from datetime import datetime

import httpx
import trafilatura
from selectolax.parser import HTMLParser

from recentdisaster.normalize import parse_date, strip_html

_TIME_META = (
    "article:published_time",
    "og:article:published_time",
    "pubdate",
    "publishdate",
    "datePublished",
    "dtm:published",
    "content_PublishedDate",
)


@dataclass
class Article:
    url: str
    title: str | None
    description: str | None
    published: datetime | None
    text: str


def parse_article(html: str, url: str) -> Article:
    tree = HTMLParser(html)
    meta: dict[str, str] = {}
    for m in tree.css("meta"):
        key = m.attributes.get("property") or m.attributes.get("name") or m.attributes.get("itemprop")
        val = m.attributes.get("content")
        if key and val and key not in meta:
            meta[key] = val
    published = next((parse_date(meta[k]) for k in _TIME_META if k in meta), None)
    if published is None and (t := tree.css_first("time[datetime]")):
        published = parse_date(t.attributes.get("datetime"))
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=False) or ""
    title = meta.get("og:title") or (tree.css_first("title").text() if tree.css_first("title") else None)
    return Article(
        url=url,
        title=strip_html(title) or None,
        description=strip_html(meta.get("og:description") or meta.get("description")) or None,
        published=published,
        text=text,
    )


def fetch_article(c: httpx.Client, url: str) -> Article | None:
    try:
        r = c.get(url)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return parse_article(r.text, str(r.url))
