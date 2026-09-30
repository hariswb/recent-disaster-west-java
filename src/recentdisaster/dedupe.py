from rapidfuzz import fuzz

from recentdisaster.models import RawItem
from recentdisaster.normalize import canonical_url

TITLE_SIMILARITY = 90


def dedupe(items: list[RawItem]) -> list[RawItem]:
    """Drop repeated URLs and near-identical titles.

    Direct feeds are preferred over aggregators (Google News), so aggregator items
    are considered last and dropped when a direct item with a matching title exists.
    """
    ordered = sorted(items, key=lambda i: i.publisher is not None)
    seen_urls: set[str] = set()
    kept: list[RawItem] = []
    for item in ordered:
        url = canonical_url(item.url)
        if url in seen_urls:
            continue
        title = item.title.lower()
        if any(fuzz.ratio(title, k.title.lower()) >= TITLE_SIMILARITY for k in kept):
            continue
        seen_urls.add(url)
        kept.append(item)
    return kept
