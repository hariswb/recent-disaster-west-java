"""Expert analysis and research about Jabar incidents: capture on every run, verify weekly.

Accuracy over volume: an article is published only when its full text is readable, a first LLM
extracts a finding with a verbatim evidence quote that is found in the text, and a second LLM
from a different provider confirms the finding. See config/analysis.yaml.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from rapidfuzz import fuzz

from recentdisaster import llm, rules
from recentdisaster.article import fetch_article
from recentdisaster.config import ANALYSIS_CACHE_PATH, ANALYSIS_OUTPUT_PATH, WIB, load_yaml
from recentdisaster.dedupe import dedupe
from recentdisaster.models import CATEGORIES, RawItem
from recentdisaster.net import client
from recentdisaster.normalize import canonical_url, parse_date
from recentdisaster.sources import fetch_all, gnews

log = logging.getLogger(__name__)

KINDS = ("riset", "pendapat_pakar", "kajian_lembaga")


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class Expert(BaseModel):
    name: str
    affiliation: str | None = None


class AnalysisExtraction(BaseModel):
    is_analysis: bool
    jabar_relevant: bool
    category: str | None = None
    kind: Literal[KINDS] | None = None  # type: ignore[valid-type]
    experts: list[Expert] = Field(default_factory=list)
    kab_kota: list[str] = Field(default_factory=list)
    finding: str | None = None
    evidence: str | None = None
    confidence: float | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        v = v.strip().lower() if isinstance(v, str) else v
        return v if v in CATEGORIES and v != "lainnya" else None

    @field_validator("kind", mode="before")
    @classmethod
    def _kind(cls, v):
        return v if v in KINDS else None

    @field_validator("experts", "kab_kota", mode="before")
    @classmethod
    def _list(cls, v):
        return [x for x in (v or []) if x]


class CrossCheck(BaseModel):
    supported: bool
    is_analysis: bool
    jabar_relevant: bool
    category_ok: bool
    reason: str = ""


class Record(BaseModel):
    """One candidate article and what the weekly run decided about it."""

    item: RawItem
    first_seen: datetime
    status: Literal["pending", "accepted", "rejected"] = "pending"
    reason: str | None = None
    attempts: int = 0
    resolved_url: str | None = None
    published: datetime | None = None  # from the article page when the feed had none
    entry: dict | None = None

    @property
    def time(self) -> datetime:
        return self.item.published or self.published or self.first_seen


@dataclass
class Store:
    records: dict[str, Record] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Store":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls({u: Record.model_validate(r) for u, r in raw.get("records", {}).items()})

    def prune(self, cutoff: datetime) -> None:
        self.records = {u: r for u, r in self.records.items() if r.time >= cutoff}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"records": {u: r.model_dump(mode="json", exclude_none=True) for u, r in sorted(self.records.items())}}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


# --------------------------------------------------------------------------
# Screening (rules only, runs on every incident run)
# --------------------------------------------------------------------------


@dataclass
class Rules:
    expert: re.Pattern
    exclude: re.Pattern
    extra: dict[str, re.Pattern]


@cache
def _rules() -> Rules:
    cfg = load_yaml("analysis.yaml")
    return Rules(
        expert=rules._mixed_case(cfg["expert_markers"]),
        exclude=rules._alt(cfg["exclude_title"]),
        extra={k: rules._alt(v) for k, v in cfg.get("extra_patterns", {}).items()},
    )


def category_in(text: str) -> str | None:
    tx = rules.taxonomy()
    for c in tx.order:
        if tx.patterns[c].search(text) or (c in _rules().extra and _rules().extra[c].search(text)):
            return c
    return None


def load_sources(schedule: str | None = None) -> list[dict]:
    cfg = load_yaml("analysis.yaml")
    defaults = load_yaml("sources.yaml").get("defaults", {})
    return [{**defaults, **s} for s in cfg["sources"]
            if s.get("enabled", True) and (schedule is None or s.get("schedule", "daily") == schedule)]


@cache
def _source_ids() -> frozenset[str]:
    return frozenset(s["id"] for s in load_sources())


def screen(item: RawItem) -> str | None:
    """Reason to skip, or None for a candidate. Feeds dedicated to research may defer the
    Jabar check to the article text; general news needs the topic in the title."""
    r = _rules()
    if r.exclude.search(item.title):
        return "excluded title"
    research_feed = item.source_id in _source_ids()
    head = f"{item.title}\n{item.summary}"
    if not (category_in(head) if research_feed else category_in(item.title)):
        return "no category keyword"
    if not r.expert.search(head):
        return "no expert marker"
    if not research_feed:
        loc = rules.gazetteer().locate(item.summary, item.title)
        if not (item.jabar_only or loc.found or loc.jabar_mentioned):
            return "not in Jabar"
    return None


def capture(store: Store, items: list[RawItem], now: datetime) -> int:
    """Queue new candidates; returns how many were added."""
    added = 0
    titles = [_norm(r.item.title) for r in store.records.values()]
    for it in dedupe(items):
        key = canonical_url(it.url)
        if key in store.records or screen(it):
            continue
        t = _norm(it.title)
        if any(fuzz.ratio(t, o) >= 90 for o in titles):  # same story syndicated elsewhere
            continue
        titles.append(t)
        store.records[key] = Record(item=it, first_seen=now)
        added += 1
    return added


def capture_into(path: Path, items: list[RawItem], now: datetime, *, fetch_daily: bool = True) -> int:
    """Incident-run hook: screen its items plus the daily research feeds, update the queue."""
    cfg = load_yaml("analysis.yaml")
    store = Store.load(path)
    extra: list[RawItem] = []
    if fetch_daily:
        extra, statuses = fetch_all(load_sources("daily"), known=set())
        for s in statuses:
            if not s["ok"]:
                log.info("analysis source %s failed: %s", s["id"], s["error"])
    added = capture(store, items + extra, now)
    store.prune(now - timedelta(days=cfg["retention_days"]))
    store.save(path)
    return added


# --------------------------------------------------------------------------
# Verification (weekly)
# --------------------------------------------------------------------------

_KIND_TEXT = "riset (hasil penelitian/studi), pendapat_pakar (analisis atau penjelasan pakar), kajian_lembaga (kajian resmi lembaga riset/universitas/BMKG/BRIN)"

EXTRACT_PROMPT = f"""Anda adalah kurator riset kebencanaan dan isu sosial untuk Provinsi Jawa Barat.
Baca satu artikel dan kembalikan HANYA satu objek JSON dengan skema:

{{
  "is_analysis": boolean,     // true HANYA jika inti artikel adalah analisis, penjelasan sebab/dampak/risiko,
                              // atau temuan riset dari pakar, peneliti, akademisi, atau lembaga riset.
                              // false untuk: laporan kejadian biasa, penyaluran bantuan, kegiatan seremonial,
                              // pernyataan pejabat pemerintah tanpa analisis, prakiraan cuaca rutin, promosi kampus,
                              // atau artikel yang hanya menyebut pakar sekilas.
  "jabar_relevant": boolean,  // true HANYA jika Jawa Barat atau wilayah di dalamnya adalah POKOK analisis.
                              // false jika Jawa Barat hanya disebut sekilas (mis. dalam daftar provinsi terdampak)
                              // sementara analisis berfokus pada daerah lain (Jakarta, Kalimantan, nasional).
  "category": one of {[c for c in CATEGORIES if c != "lainnya"]},  // jenis kejadian yang dianalisis
  "kind": one of {list(KINDS)},  // {_KIND_TEXT}
  "experts": [{{"name": string, "affiliation": string|null}}],  // nama PERSIS seperti tertulis di artikel
  "kab_kota": [string],       // kabupaten/kota Jawa Barat yang dibahas, dari daftar yang diberikan
  "finding": string,          // 1-2 kalimat bahasa Indonesia baku: temuan/analisis utama untuk Jawa Barat. HANYA dari artikel,
                              // tanpa menambah fakta, angka, atau kesimpulan sendiri.
  "evidence": string,         // kutipan VERBATIM dari artikel (salin persis, 1-2 kalimat, maks 300 karakter)
                              // yang paling mendukung "finding"
  "confidence": number        // 0..1
}}

Jika ragu, pilih false. Jangan menulis nama korban kekerasan atau anak di bawah umur."""

CHECK_PROMPT = """Anda adalah pemeriksa fakta. Diberikan sebuah artikel dan ringkasan yang diklaim berasal darinya.
Kembalikan HANYA JSON:
{
  "supported": boolean,      // true jika SELURUH isi ringkasan dinyatakan atau tersirat jelas di artikel,
                             // tanpa fakta, angka, atau atribusi tambahan. Satu bagian tidak didukung = false.
  "is_analysis": boolean,    // true jika artikel memuat analisis/temuan riset pakar atau lembaga riset,
                             // bukan sekadar laporan kejadian atau pernyataan pejabat
  "jabar_relevant": boolean, // true HANYA jika Jawa Barat atau wilayah di dalamnya adalah pokok analisis,
                             // bukan sekadar disebut dalam daftar daerah
  "category_ok": boolean,    // true jika jenis kejadian yang diklaim memang yang dianalisis
  "reason": string           // satu kalimat
}"""


def _norm(s: str) -> str:
    s = s.lower().replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", s).strip()


_JABAR_WORD = re.compile(r"\b(?:jawa barat|jabar)\b", re.I)


def jabar_focus(title: str, body: str, min_mentions: int = 3) -> bool:
    """Jabar is a subject of the article, not a passing mention: a Jabar place in the title,
    or at least `min_mentions` mentions of the province or its kab/kota in the text."""
    gaz = rules.gazetteer()
    head = gaz.locate("", title)
    if head.found or head.jabar_mentioned:
        return True
    hits = len(_JABAR_WORD.findall(body))
    for rx, _ in gaz._kab_explicit:
        hits += len(rx.findall(body))
    return hits >= min_mentions


def in_text(snippet: str, text: str, threshold: int = 90) -> bool:
    if not snippet or len(snippet.strip()) < 3:
        return False
    return fuzz.partial_ratio(_norm(snippet), _norm(text)) >= threshold


def _labels() -> dict[str, str]:
    return rules.taxonomy().labels


def ground(ext: AnalysisExtraction, title: str, body: str, cfg: dict) -> tuple[AnalysisExtraction | None, str]:
    """Deterministic checks on the LLM answer against the article text. Returns (cleaned, reason)."""
    if not ext.is_analysis:
        return None, "not analysis"
    if not ext.jabar_relevant:
        return None, "not about Jabar"
    if (ext.confidence or 0) < cfg["min_confidence"]:
        return None, f"low confidence {ext.confidence}"
    if not ext.category or not ext.kind or not ext.finding:
        return None, "incomplete extraction"
    text = f"{title}\n{body}"
    cat_rx = rules.taxonomy().patterns.get(ext.category)
    extra_rx = _rules().extra.get(ext.category)
    if not ((cat_rx and cat_rx.search(text)) or (extra_rx and extra_rx.search(text))):
        return None, f"category {ext.category} not in text"
    if not in_text(ext.evidence or "", body, cfg["evidence_similarity"]):
        return None, "evidence quote not found in article"
    experts = [Expert(name=e.name.strip(), affiliation=e.affiliation if e.affiliation and in_text(e.affiliation, text) else None)
               for e in ext.experts if in_text(e.name, text, 95)]
    if not experts and ext.kind != "kajian_lembaga":
        return None, "no expert name found in article"
    gaz = rules.gazetteer()
    loc = gaz.locate(body, title)
    kabs = []
    for k in ext.kab_kota:
        n = gaz.normalize_kab(k)
        if n and n in loc.scores and n not in kabs:
            kabs.append(n)
    return ext.model_copy(update={"experts": experts, "kab_kota": kabs}), "ok"


def _article_text(item: RawItem, cfg: dict) -> tuple[str | None, str, datetime | None]:
    """(publisher url, article text, published) — empty text when unreadable.
    Raises gnews.Blocked when Google News rate limits link decoding."""
    with client(25) as c:
        url = gnews.resolve(c, item.url) if item.publisher else item.url
        if not url:
            return None, "", None
        art = fetch_article(c, url)
    if not art:
        return url, "", None
    return url, art.text, art.published


def _messages(system: str, item: RawItem, body: str, extra: str = "") -> list[dict]:
    user = (f"{extra}Sumber: {item.source_name}\n"
            f"Diterbitkan: {item.published.isoformat() if item.published else 'tidak diketahui'}\n"
            f"Judul: {item.title}\nIsi:\n{body}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def duplicate_of(store: Store, rec: Record, ext: AnalysisExtraction) -> str | None:
    """Another outlet's write-up of the same analysis that is already published: same category and
    a near-identical title or finding, the same expert, or the same place within a few days with a
    similar finding."""
    names = {_norm(x.name) for x in ext.experts}
    for r in store.records.values():
        e = r.entry
        if r.status != "accepted" or not e or e["category"] != ext.category:
            continue
        if (fuzz.token_set_ratio(_norm(rec.item.title), _norm(e["title"])) >= 80
                or fuzz.token_set_ratio(_norm(ext.finding), _norm(e["finding"])) >= 75):
            return e["id"]
        if any(fuzz.token_set_ratio(n, _norm(x["name"])) >= 90 for n in names for x in e["experts"]):
            return e["id"]
        near = abs((rec.time - r.time).total_seconds()) <= 3 * 86400
        if near and set(ext.kab_kota) & set(e["kab_kota"]) and fuzz.token_set_ratio(_norm(ext.finding), _norm(e["finding"])) >= 55:
            return e["id"]
    return None


def _priority(rec: Record) -> tuple:
    head = rules.gazetteer().locate("", rec.item.title)
    return (not (head.found or head.jabar_mentioned), -rec.time.timestamp())


def _entry(key: str, rec: Record, ext: AnalysisExtraction, providers: list[str]) -> dict:
    return {
        "id": hashlib.sha1(key.encode()).hexdigest()[:10],
        "category": ext.category,
        "kind": ext.kind,
        "title": rec.item.title,
        "url": rec.resolved_url or rec.item.url,
        "source": rec.item.source_name,
        "published": rec.time.isoformat(timespec="seconds"),
        "experts": [e.model_dump(exclude_none=True) for e in ext.experts],
        "kab_kota": ext.kab_kota,
        "finding": ext.finding,
        "evidence": ext.evidence,
        "verified_by": providers,
    }


def verify(store: Store, pool: llm.ProviderPool | None, now: datetime, cfg: dict, read=None) -> dict:
    read = read or _article_text
    counts = {"accepted": 0, "rejected": 0, "deferred": 0}
    cutoff = now - timedelta(days=cfg["retention_days"])
    kab_list = [r["name"] for r in rules.gazetteer().regencies]
    # Articles with a Jabar place in the title first, then newest.
    pending = sorted(((k, r) for k, r in store.records.items() if r.status == "pending"),
                     key=lambda kr: _priority(kr[1]))[: cfg["max_articles_per_run"]]
    gnews_blocked = False
    for key, rec in pending:
        if gnews_blocked and rec.item.publisher:
            counts["deferred"] += 1
            continue
        if not (pool and pool.available and len([p for p in pool.providers if p.available]) >= 2):
            counts["deferred"] += 1  # an independent second opinion needs two providers
            continue

        def reject(reason: str, rec=rec) -> None:
            rec.status, rec.reason = "rejected", reason
            counts["rejected"] += 1
            log.info("rejected %s: %s", rec.item.title[:80], reason)

        try:
            url, body, published = read(rec.item, cfg)
        except gnews.Blocked as e:
            log.warning("%s; Google News articles stay queued", e)
            gnews_blocked = True
            counts["deferred"] += 1
            continue
        rec.resolved_url, rec.published = url, rec.published or published
        if len(body) < cfg["min_body_chars"]:
            rec.attempts += 1
            if rec.attempts >= cfg["max_attempts"]:
                reject("article text not readable")
            else:
                counts["deferred"] += 1
                log.info("deferred (text not readable, %s chars): %s", len(body), rec.item.url[:100])
            continue
        if rec.time < cutoff:
            reject("older than retention window")
            continue
        text = f"{rec.item.title}\n{body}"
        if not _rules().expert.search(text) or not category_in(text):
            reject("no expert marker or category in article")
            continue
        if not jabar_focus(rec.item.title, body):
            reject("Jabar is not a subject of the article")
            continue

        body_in = body[: cfg["max_body_chars"]]
        try:
            ext, first = pool.complete_json(
                _messages(EXTRACT_PROMPT, rec.item, body_in, f"Daftar kabupaten/kota Jawa Barat: {', '.join(kab_list)}\n\n"),
                AnalysisExtraction.model_validate)
            ext, reason = ground(ext, rec.item.title, body, cfg)
            if ext is None:
                reject(reason)
                continue
            claim = {"jenis_kejadian": _labels().get(ext.category, ext.category), "finding": ext.finding,
                     "pakar": [e.model_dump(exclude_none=True) for e in ext.experts], "kab_kota": ext.kab_kota}
            check, second = pool.complete_json(
                _messages(CHECK_PROMPT, rec.item, body_in, f"Ringkasan yang diklaim: {json.dumps(claim, ensure_ascii=False)}\n\n"),
                CrossCheck.model_validate, exclude={first.split("/", 1)[0]})
        except llm.LLMUnavailable as e:
            rec.attempts += 1
            log.info("LLM unavailable for %s: %s", rec.item.url, e)
            if rec.attempts >= cfg["max_attempts"]:
                reject("LLM verification unavailable")
            else:
                counts["deferred"] += 1
            continue
        if not (check.supported and check.is_analysis and check.jabar_relevant and check.category_ok):
            reject(f"cross-check failed: {check.reason[:200]}")
            continue
        if dup := duplicate_of(store, rec, ext):
            reject(f"duplicate of {dup}")
            continue
        rec.status, rec.reason = "accepted", None
        rec.entry = _entry(key, rec, ext, [first, second])
        counts["accepted"] += 1
        log.info("accepted [%s] %s", ext.category, rec.item.title[:80])
    return counts


def run(*, use_llm: bool = True, dry_run: bool = False, fetch: bool = True, now: datetime | None = None,
        cache_path: Path = ANALYSIS_CACHE_PATH, output_path: Path = ANALYSIS_OUTPUT_PATH) -> dict:
    now = now or datetime.now(WIB)
    cfg = load_yaml("analysis.yaml")
    store = Store.load(cache_path)
    statuses: list[dict] = []
    added = 0
    if fetch:
        items, statuses = fetch_all(load_sources(), known=set())
        added = capture(store, items, now)
    cutoff = now - timedelta(days=cfg["retention_days"])
    store.prune(cutoff)

    pool = llm.ProviderPool() if use_llm else None
    counts = verify(store, pool, now, cfg)

    entries = sorted((r.entry for r in store.records.values() if r.status == "accepted" and r.entry
                      and parse_date(r.entry["published"]) >= cutoff),
                     key=lambda e: e["published"], reverse=True)
    output = {
        "generated_at": now.isoformat(timespec="seconds"),
        "retention_days": cfg["retention_days"],
        "stats": {"queued_this_run": added, **counts,
                  "pending": sum(r.status == "pending" for r in store.records.values()),
                  "published": len(entries)},
        "llm_providers": pool.summary() if pool else [],
        "source_status": statuses,
        "entries": entries,
    }
    if not dry_run:
        store.save(cache_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=1))
    output["_rejected"] = [(r.item.title, r.reason) for r in store.records.values() if r.status == "rejected"]
    return output
