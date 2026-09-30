"""Merge reports of the same event from different outlets into incidents."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import cache

from rapidfuzz import fuzz

from recentdisaster.config import WIB
from recentdisaster.models import Report, Victims
from recentdisaster.rules import Gazetteer, gazetteer, taxonomy

_STOP = set("""
di ke dari dan yang untuk dengan pada dalam oleh akibat karena usai saat setelah sejak hingga sampai
ini itu tak tidak belum sudah masih jadi jadi bisa akan ada para warga sejumlah puluhan ratusan
satu dua tiga empat lima enam tujuh delapan sembilan sepuluh orang hari jam kali kembali terjadi
api petugas damkar bpbd polisi polres pemkab pemkot pemprov berita terkini update news
kabupaten kab kota jawa barat jabar kecamatan kec desa kampung kelurahan
""".split())
_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


@cache
def _noise_words() -> frozenset[str]:
    """Category keywords and regency names: shared by unrelated reports."""
    words = set(_STOP)
    for pat in taxonomy().patterns.values():
        words.update(w for w in _WORD.findall(pat.pattern.lower()) if len(w) > 2)
    # Objects like rumah/jembatan/pasar identify an event; keep them.
    for pat in taxonomy().entities.values():
        words.difference_update(_WORD.findall(pat.pattern.lower()))
    for r in gazetteer().regencies:
        words.update(_WORD.findall(r["base"].lower()))
    return frozenset(words)


def title_tokens(title: str) -> frozenset[str]:
    title = re.sub(r"\s[-–|]\s[^-–|]{2,30}$", "", title)  # trailing " - Publisher"
    noise = _noise_words()
    return frozenset(w for w in _WORD.findall(title.lower()) if len(w) > 2 and w not in noise and not w.isdigit())


def _related(a: Report, b: Report) -> bool:
    ta, tb = title_tokens(a.item.title), title_tokens(b.item.title)
    shared = len(ta & tb)
    if fuzz.ratio(a.item.title.lower(), b.item.title.lower()) >= 85:
        return True
    return shared >= 3 or (shared >= 2 and shared / max(1, min(len(ta), len(tb))) >= 0.3)


def _same_place(a: Report, b: Report) -> bool:
    """Same desa, or same kecamatan for area events. (A shared kecamatan alone is too
    coarse for fires: a market fire and a pesantren fire in Surade are different events.)"""
    ea, eb = a.extraction, b.extraction
    norm = lambda d: re.sub(r"^(desa|kelurahan|kampung|dusun)\s+", "", (d or "").lower()).strip()  # noqa: E731
    if not (ea.kab_kota and ea.kab_kota == eb.kab_kota):
        return False
    if norm(ea.desa) and norm(ea.desa) == norm(eb.desa):
        return True
    if not (ea.kecamatan and eb.kecamatan and ea.kecamatan.lower() == eb.kecamatan.lower()):
        return False
    # Area events (flood, landslide, quake…) in one kecamatan are one event;
    # point events (fire, poisoning, collapse) also need a shared word that is
    # not the place name ("rumah", "spbu", "ponpes").
    if taxonomy().groups.get(ea.category or "") == "alam":
        return True
    place = set(_WORD.findall(f"{ea.kecamatan} {ea.desa or ''} {eb.desa or ''}".lower()))
    return bool((title_tokens(a.item.title) & title_tokens(b.item.title)) - place)


@dataclass
class _Cluster:
    reports: list[Report] = field(default_factory=list)

    @property
    def category(self):
        return Counter(r.extraction.category for r in self.reports).most_common(1)[0][0]

    def _common(self, attr: str) -> str | None:
        vals = [getattr(r.extraction, attr) for r in self.reports if getattr(r.extraction, attr)]
        return Counter(vals).most_common(1)[0][0] if vals else None

    @property
    def kab_kota(self):
        return self._common("kab_kota")


def _same_event(c: _Cluster, r: Report) -> bool:
    e = r.extraction
    kab = c.kab_kota
    if e.kab_kota and kab and e.kab_kota != kab:
        return False
    same_cat = e.category == c.category
    for x in c.reports:
        if fuzz.ratio(x.item.title.lower(), r.item.title.lower()) >= 85:
            return True
        if same_cat and (_same_place(x, r) or _related(x, r)):
            return True
    return False


def cluster(reports: list[Report]) -> list[list[Report]]:
    clusters: list[_Cluster] = []
    for r in sorted(reports, key=lambda r: r.time):
        for c in clusters:
            if _same_event(c, r):
                c.reports.append(r)
                break
        else:
            clusters.append(_Cluster([r]))
    return [c.reports for c in clusters]


def _best(reports: list[Report]) -> Report:
    return max(reports, key=lambda r: (r.method == "llm", r.extraction.confidence or 0, -r.time.timestamp()))


def to_incident(reports: list[Report], gaz: Gazetteer) -> dict:
    c = _Cluster(reports)
    best = _best(reports)
    kab = c.kab_kota
    kec = next((r.extraction.kecamatan for r in reports if r.extraction.kab_kota == kab and r.extraction.kecamatan), None)
    desa = next((r.extraction.desa for r in reports if r.extraction.kab_kota == kab and r.extraction.desa), None)
    coords = gaz.centroid(kab, kec)
    victims = {
        k: max((getattr(r.extraction.victims, k) or 0 for r in reports), default=0) or None
        for k in Victims.model_fields
    }
    entities: dict[tuple, dict] = {}
    for r in reports:
        for e in r.extraction.affected_entities:
            key = (e.type, (e.name or "").lower())
            if e.name and (e.type, "") in entities:
                entities.pop((e.type, ""))
            if not e.name and any(k[0] == e.type for k in entities):
                continue
            entities.setdefault(key, e.model_dump())
    times = [r.time for r in reports]
    first = min(times)
    category = c.category
    key = f"{category}|{kab}|{kec}|{first.astimezone(WIB).date()}"
    return {
        "id": hashlib.sha1(key.encode()).hexdigest()[:10],
        "category": category,
        "subcategory": best.extraction.subcategory,
        "title": best.item.title,
        "summary": best.extraction.summary,
        "kab_kota": kab,
        "kecamatan": kec,
        "desa": desa,
        "lat": coords[0] if coords else None,
        "lon": coords[1] if coords else None,
        "location_precision": "kecamatan" if coords and kec else ("kab_kota" if coords else None),
        "event_time": next((r.extraction.event_time for r in reports if r.extraction.event_time), None),
        "first_reported": first.isoformat(),
        "last_reported": max(times).isoformat(),
        "victims": {k: v for k, v in victims.items() if v},
        "affected_entities": list(entities.values())[:10],
        "sources": [
            {"name": r.item.source_name, "url": r.item.url, "title": r.item.title,
             "published": r.time.isoformat()}
            for r in sorted(reports, key=lambda r: r.time)
        ],
        "report_count": len(reports),
        "extraction": "llm" if any(r.method == "llm" for r in reports) else "rules",
        "confidence": max((r.extraction.confidence or 0 for r in reports), default=None),
    }
