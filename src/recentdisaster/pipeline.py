import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

from recentdisaster import llm, rules
from recentdisaster.article import fetch_article
from recentdisaster.cluster import cluster, to_incident
from recentdisaster.config import CACHE_PATH, OUTPUT_PATH, WIB, load_yaml
from recentdisaster.dedupe import dedupe
from recentdisaster.models import Extraction, RawItem, Report
from recentdisaster.net import client
from recentdisaster.normalize import canonical_url
from recentdisaster.sources import fetch_all, load_sources
from recentdisaster.store import Cache

log = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)
CACHE_WINDOW = timedelta(hours=48)
MAX_CANDIDATES_PER_RUN = 150


def _fetch_bodies(items: list[RawItem]) -> dict[str, str]:
    """Article text for candidates; aggregator links (Google News) are skipped."""
    direct = [i for i in items if i.publisher is None]

    def one(item: RawItem) -> tuple[str, str]:
        with client(20) as c:
            art = fetch_article(c, item.url)
        return item.url, (art.text if art else "")

    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, direct))


def _finalize_llm(ext: Extraction, fallback: Extraction, gaz: rules.Gazetteer) -> Extraction:
    """Snap LLM locations to the gazetteer; fill gaps from the rule extraction."""
    kab = gaz.normalize_kab(ext.kab_kota) if ext.kab_kota else None
    if ext.kab_kota and kab is None:
        log.debug("LLM kab_kota %r not in gazetteer", ext.kab_kota)
    kab = kab or fallback.kab_kota
    kec = gaz.normalize_kec(kab, ext.kecamatan) or ext.kecamatan or (fallback.kecamatan if kab == fallback.kab_kota else None)
    victims = ext.victims if ext.victims.any() else fallback.victims
    return ext.model_copy(update={"kab_kota": kab, "kecamatan": kec, "desa": ext.desa or fallback.desa, "victims": victims})


def run(
    *,
    use_llm: bool = True,
    dry_run: bool = False,
    only_sources: list[str] | None = None,
    now: datetime | None = None,
    output_path: Path = OUTPUT_PATH,
    cache_path: Path = CACHE_PATH,
) -> dict:
    now = now or datetime.now(WIB)
    cutoff = now - WINDOW
    cache = Cache.load(cache_path)
    gaz = rules.gazetteer()

    # 1. fetch + normalize
    sources = load_sources(only_sources)
    items, statuses = fetch_all(sources, known=set(cache.seen))
    items = dedupe(items)
    for it in items:
        cache.seen.setdefault(canonical_url(it.url), now)

    def item_time(it: RawItem) -> datetime:
        t = it.published or cache.seen[canonical_url(it.url)]
        return min(t, now)  # clamp bogus future dates

    recent = [it for it in items if item_time(it) >= cutoff]

    # 2. rule prefilter
    screened = [(it, rules.screen(it)) for it in recent]
    candidates = [(it, s) for it, s in screened if s.candidate]

    # 3. extraction (new candidates, plus rule-only ones when an LLM is available)
    pool = llm.ProviderPool() if use_llm else None
    llm_ok = bool(pool and pool.providers)
    todo = []
    for it, s in candidates:
        cached = cache.reports.get(canonical_url(it.url))
        if cached is None or (llm_ok and cached.method == "rules"):
            todo.append((it, s))
    # LLM budget goes to the strongest candidates first.
    todo.sort(key=lambda p: (not p[1].in_title, not p[0].jabar_only, -item_time(p[0]).timestamp()))
    todo = todo[:MAX_CANDIDATES_PER_RUN]

    llm_cfg = load_yaml("llm_providers.yaml")
    kab_list = [r["name"] for r in gaz.regencies]
    bodies = _fetch_bodies([it for it, _ in todo]) if todo else {}
    counts = {"llm": 0, "rules": 0, "llm_failed": 0}
    for it, s in todo:
        key = canonical_url(it.url)
        body = bodies.get(it.url, "")
        fallback = rules.rule_extract(it, body)
        method, provider, ext = "rules", None, fallback
        if llm_ok and pool.available:
            hints = {"category": s.category, "kab_kota": fallback.kab_kota, "kecamatan": fallback.kecamatan,
                     "victims": fallback.victims.model_dump(exclude_none=True)}
            try:
                raw, provider = llm.extract(pool, it, body, hints, kab_list, llm_cfg.get("max_body_chars", 3000))
                ext, method = _finalize_llm(raw, fallback, gaz), "llm"
            except llm.LLMUnavailable as e:
                log.info("LLM unavailable for %s: %s", it.url, e)
                counts["llm_failed"] += 1
            except Exception:  # a provider quirk must never kill the run
                log.exception("unexpected LLM error for %s; using rules", it.url)
                counts["llm_failed"] += 1
        counts[method] += 1
        prev = cache.reports.get(key)
        cache.reports[key] = Report(
            item=it, first_seen=prev.first_seen if prev else cache.seen.get(key, now),
            extraction=ext, method=method, provider=provider,
        )

    # 4. incidents from everything in the window (not only this run's feed items)
    in_window = [
        r for r in cache.reports.values()
        if r.time >= cutoff and r.extraction.is_incident and r.extraction.in_jabar and r.extraction.category
    ]
    incidents = [to_incident(group, gaz) for group in cluster(in_window)]
    incidents.sort(key=lambda i: i["last_reported"], reverse=True)

    tax = load_yaml("taxonomy.yaml")
    output = {
        "generated_at": now.isoformat(timespec="seconds"),
        "window_hours": int(WINDOW.total_seconds() // 3600),
        "categories": {k: {"label": v["label"], "group": v["group"]} for k, v in tax["categories"].items()},
        "stats": {
            "items_fetched": len(items),
            "items_in_window": len(recent),
            "candidates": len(candidates),
            "extracted_this_run": counts,
            "reports_in_window": len(in_window),
            "incidents": len(incidents),
        },
        "llm_providers": pool.summary() if pool else [],
        "source_status": statuses,
        "incidents": incidents,
    }

    cache.prune(now - CACHE_WINDOW)
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=1))
        cache.save(cache_path)
    output["_candidates"] = [(it.title, s.category, s.location.kab_kota) for it, s in candidates]
    return output
