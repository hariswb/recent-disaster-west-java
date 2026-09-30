import re
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from recentdisaster.article import fetch_article
from recentdisaster.models import RawItem
from recentdisaster.normalize import canonical_url, strip_html


def listing_links(html: str, base_url: str, pattern: str) -> dict[str, str]:
    """Article URL -> longest anchor text found for it."""
    rx = re.compile(pattern)
    links: dict[str, str] = {}
    for a in HTMLParser(html).css("a[href]"):
        raw = (a.attributes.get("href") or "").strip()
        if not raw:
            continue
        href = urljoin(base_url, raw).split("#")[0]
        if not rx.search(href):
            continue
        text = strip_html(a.text(separator=" ")) or a.attributes.get("title") or ""
        if len(text) > len(links.get(href, "")):
            links[href] = text
        else:
            links.setdefault(href, "")
    return links


def _headline_worth_fetching(text: str) -> bool:
    # Imported lazily: rules loads the gazetteer/taxonomy.
    from recentdisaster.rules import classify, excluded

    if not text:
        return True  # image-only link: cannot judge, fetch it
    category, _, in_title = classify(text, "")
    return bool(category and in_title and not excluded(text))


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    if not source.get("link_pattern"):
        raise ValueError("html source without link_pattern")
    links: dict[str, str] = {}
    errors = []
    for url in source["urls"]:
        try:
            r = c.get(url)
            r.raise_for_status()
            for k, v in listing_links(r.text, str(r.url), source["link_pattern"]).items():
                if len(v) >= len(links.get(k, "")):
                    links[k] = v
        except httpx.HTTPError as e:
            errors.append(f"{url}: {e}")
    if errors and not links:
        raise RuntimeError("; ".join(errors))

    # Only headlines that look like incidents are fetched; the rest are skipped.
    todo = [u for u, text in links.items() if canonical_url(u) not in known and _headline_worth_fetching(text)]
    items = []
    for url in todo[: source.get("max_articles_per_html_source", 30)]:
        art = fetch_article(c, url)
        if not art or not art.title:
            continue
        items.append(
            RawItem(
                source_id=source["id"],
                source_name=source["name"],
                url=art.url,
                title=art.title,
                summary=(art.description or art.text[:500])[:1000],
                published=art.published,
                jabar_only=source.get("jabar_only", False),
            )
        )
    return items
