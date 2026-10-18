import json
import logging
import threading
import time
from urllib.parse import quote_plus, urlsplit

import httpx
from selectolax.parser import HTMLParser

from recentdisaster.models import RawItem
from recentdisaster.sources.rss import parse_feed

log = logging.getLogger(__name__)

BASE = "https://news.google.com/rss/search?q={q}&hl=id&gl=ID&ceid=ID:id"
BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
RESOLVE_INTERVAL = 3.0  # seconds between decodes; Google answers bursts with 429 "sorry" pages


class Blocked(Exception):
    """Google News is rate limiting us; stop decoding for this run."""


_lock = threading.Lock()
_last = 0.0


def query_url(query: str, window: str = "1d") -> str:
    return BASE.format(q=quote_plus(f"{query} when:{window}"))


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    items: list[RawItem] = []
    errors = []
    for q in source["queries"]:
        try:
            r = c.get(query_url(q, source.get("window", "1d")))
            r.raise_for_status()
            items.extend(parse_feed(r.content, source))
        except httpx.HTTPError as e:
            errors.append(f"{q[:30]}…: {e}")
    if errors and not items:
        raise RuntimeError("; ".join(errors))
    return items


def resolve(c: httpx.Client, url: str) -> str | None:
    """Publisher URL behind a news.google.com/rss/articles/<id> link, or None.

    Google News no longer redirects; the target comes from the page's signature
    and timestamp sent to the batchexecute endpoint. Undocumented, so best effort."""
    if urlsplit(url).netloc != "news.google.com":
        return url
    global _last
    aid = urlsplit(url).path.rstrip("/").split("/")[-1]
    with _lock:
        wait = _last + RESOLVE_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last = time.monotonic()
    try:
        page = c.get(f"https://news.google.com/articles/{aid}")
        if page.status_code == 429 or "/sorry/" in str(page.url):
            raise Blocked("news.google.com returned 429")
        page.raise_for_status()
        el = HTMLParser(page.text).css_first("c-wiz > div[jscontroller]")
        sg, ts = el.attributes.get("data-n-a-sg"), el.attributes.get("data-n-a-ts")
        if not sg or not ts:
            return None
        req = ["Fbv4je", f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                         f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{ts},"{sg}"]']
        r = c.post(BATCH, data={"f.req": json.dumps([[req]])},
                   headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
        if r.status_code == 429:
            raise Blocked("batchexecute returned 429")
        r.raise_for_status()
        target = json.loads(json.loads(r.text.split("\n\n", 1)[1])[0][2])[1]
    except (httpx.HTTPError, AttributeError, IndexError, TypeError, ValueError) as e:
        log.debug("could not resolve %s: %s", url, e)
        return None
    return target if isinstance(target, str) and target.startswith("http") else None
