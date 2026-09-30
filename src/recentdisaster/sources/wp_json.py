from datetime import UTC

import httpx

from recentdisaster.models import RawItem
from recentdisaster.normalize import clean_title, parse_date, strip_html


def fetch(source: dict, c: httpx.Client, known: set[str]) -> list[RawItem]:
    r = c.get(source["url"])
    r.raise_for_status()
    items = []
    for post in r.json():
        published = parse_date(post.get("date_gmt"))
        if published is not None:  # date_gmt is naive UTC
            published = published.replace(tzinfo=UTC)
        items.append(
            RawItem(
                source_id=source["id"],
                source_name=source["name"],
                url=post["link"],
                title=clean_title(post["title"]["rendered"]),
                summary=strip_html(post.get("excerpt", {}).get("rendered"))[:1000],
                published=published,
                jabar_only=source.get("jabar_only", False),
            )
        )
    return items
