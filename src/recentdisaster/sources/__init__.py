import logging
import time
from concurrent.futures import ThreadPoolExecutor

from recentdisaster.config import load_yaml
from recentdisaster.models import RawItem
from recentdisaster.net import client
from recentdisaster.sources import gnews, html, rss, wp_json

log = logging.getLogger(__name__)

FETCHERS = {"rss": rss.fetch, "gnews": gnews.fetch, "wp_json": wp_json.fetch, "html": html.fetch}


def load_sources(only: list[str] | None = None, include_disabled: bool = False) -> list[dict]:
    cfg = load_yaml("sources.yaml")
    defaults = cfg.get("defaults", {})
    out = []
    for s in cfg["sources"]:
        if only and s["id"] not in only:
            continue
        if not s.get("enabled", True) and not (include_disabled or only):
            continue
        out.append({**defaults, **s})
    return out


def _run(source: dict, known: set[str]) -> tuple[list[RawItem], dict]:
    start = time.monotonic()
    status = {"id": source["id"], "name": source["name"], "ok": True, "items": 0, "error": None}
    try:
        with client(source.get("timeout", 20)) as c:
            items = FETCHERS[source["type"]](source, c, known)
        status["items"] = len(items)
    except Exception as e:  # one broken source must not break the run
        log.warning("source %s failed: %s", source["id"], e)
        items = []
        status.update(ok=False, error=f"{type(e).__name__}: {e}"[:300])
    status["seconds"] = round(time.monotonic() - start, 1)
    return items, status


def fetch_all(sources: list[dict], known: set[str]) -> tuple[list[RawItem], list[dict]]:
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda s: _run(s, known), sources))
    items = [i for its, _ in results for i in its]
    return items, [st for _, st in results]
