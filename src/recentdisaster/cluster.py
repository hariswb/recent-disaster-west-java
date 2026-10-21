"""Merge reports of the same event from different outlets into incidents."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cache

from rapidfuzz import fuzz

from recentdisaster.config import WIB
from recentdisaster.models import Report, Victims
from recentdisaster.normalize import strip_site_suffix
from recentdisaster.rules import Gazetteer, gazetteer, taxonomy

_STOP = set("""
di ke dari dan yang untuk dengan pada dalam oleh akibat karena usai saat setelah sejak hingga sampai
ini itu tak tidak belum sudah masih jadi jadi bisa akan ada para warga sejumlah puluhan ratusan
satu dua tiga empat lima enam tujuh delapan sembilan sepuluh orang hari jam kali kembali terjadi
api petugas damkar bpbd polisi polres pemkab pemkot pemprov berita terkini update news
kabupaten kab kota jawa barat jabar kecamatan kec desa kampung kelurahan
breaking breakingnews viral heboh tragis kronologi fakta begini
kerugian ditaksir capai juta miliar tewas tewaskan meninggal hebat merembet
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


def _stem(w: str) -> str:
    """Light Indonesian folding: 'muntah-muntah' -> 'muntah', 'anaknya' -> 'anak',
    'rongsokan' -> 'rongsok'."""
    w = re.sub(r"^(\w+)-\1$", r"\1", w)
    if w.endswith("nya") and len(w) > 6:
        w = w[:-3]
    return w[:-2] if w.endswith("an") and len(w) >= 8 else w


def title_tokens(title: str) -> frozenset[str]:
    title = re.sub(r"\s[-–|]\s[^-–|]{2,30}$", "", strip_site_suffix(title))  # trailing " - Publisher"
    noise = _noise_words()
    words = (w for w in _WORD.findall(title.lower()) if w not in noise)
    return frozenset(s for s in map(_stem, words) if len(s) > 2 and s not in noise and not s.isdigit())


def _title(r: Report) -> str:
    return strip_site_suffix(r.item.title).lower()


def _near_copy(a: Report, b: Report) -> bool:
    return fuzz.ratio(_title(a), _title(b)) >= 85


def _related(a: Report, b: Report) -> bool:
    ta, tb = title_tokens(a.item.title), title_tokens(b.item.title)
    shared = len(ta & tb)
    if _near_copy(a, b):
        return True
    return shared >= 3 or (shared >= 2 and shared / max(1, min(len(ta), len(tb))) >= 0.3)


def _same_place(a: Report, b: Report) -> bool:
    """Same desa, or same kecamatan for area events. (A shared kecamatan alone is too
    coarse for fires: a market fire and a pesantren fire in Surade are different events.)"""
    ea, eb = a.extraction, b.extraction
    norm = lambda d: re.sub(r"^(desa|kelurahan|kampung|dusun)\s+", "", (d or "").lower()).strip()  # noqa: E731
    gaz = gazetteer()
    if not (ea.kab_kota and gaz.base(ea.kab_kota) == gaz.base(eb.kab_kota)):
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

    @property
    def base(self) -> str | None:
        """Regency base name ('cirebon'): the LLM often mixes up Kota and Kabupaten."""
        gaz = gazetteer()
        vals = [gaz.base(r.extraction.kab_kota) for r in self.reports if r.extraction.kab_kota]
        return Counter(vals).most_common(1)[0][0] if vals else None

    @property
    def kecamatans(self) -> set[str]:
        """Kecamatan known to the gazetteer (drops kelurahan and lists the LLM put there)."""
        gaz = gazetteer()
        return {k.lower() for r in self.reports if (k := gaz.normalize_kec(None, r.extraction.kecamatan))}

    @property
    def counts(self) -> set[int]:
        """Victim counts large enough to fingerprint an event ("100 anak")."""
        return {v for r in self.reports for v in r.extraction.victims.model_dump().values() if v and v >= 10}

    @property
    def tokens(self) -> frozenset[str]:
        """Words in at least a third of the headlines: what the cluster is about,
        not a word one follow-up happened to use ("membubung", "armada")."""
        counts = Counter(w for r in self.reports for w in title_tokens(r.item.title))
        return frozenset(w for w, n in counts.items() if 3 * n >= len(self.reports))


# Slow-onset events cover a whole regency; one per regency and category at a time.
_REGENCY_WIDE = {"kekeringan"}
_MAX_GAP = timedelta(hours=36)


class _Spread:
    """Which (category, regency) groups use a title word. A word seen in only one
    group ("bpn", "pasirbajing", "pecel") points at one event there; one seen
    across regencies ("rumah", "sampah", "desak") does not. Kecamatan names only
    say where, and two fires in Surade are still two fires."""

    def __init__(self, reports: list[Report]):
        gaz = gazetteer()
        self._groups: dict[str, set] = {}
        for r in reports:
            if r.extraction.kab_kota:
                key = (r.extraction.category, gaz.base(r.extraction.kab_kota))
                for w in title_tokens(r.item.title):
                    if not gaz.regencies_of_kec(w):
                        self._groups.setdefault(w, set()).add(key)

    def distinctive(self, words: frozenset[str], category, base) -> bool:
        return any(w in self._groups and self._groups[w] <= {(category, base)} for w in words)


def _guessed_place(r: Report) -> bool:
    """The headline names no regency, so its location came from elsewhere (body, outlet)."""
    return gazetteer().locate(_title(r)).kab_kota is None


def _gap(a: _Cluster, b: _Cluster) -> timedelta:
    a0, a1 = min(r.time for r in a.reports), max(r.time for r in a.reports)
    b0, b1 = min(r.time for r in b.reports), max(r.time for r in b.reports)
    return max(b0 - a1, a0 - b1, timedelta(0))


def _same_event(a: _Cluster, b: _Cluster, spread: _Spread) -> bool:
    pairs = [(x, y) for x in a.reports for y in b.reports]
    if a.base and b.base and a.base != b.base:
        # Only a near-copy headline that never named a place may cross regencies.
        return any(_near_copy(x, y) and (_guessed_place(x) or _guessed_place(y)) for x, y in pairs)
    if any(_near_copy(x, y) for x, y in pairs):
        return True
    if a.category != b.category:
        return False
    # Two known, different kecamatan: two fires in one regency, not one.
    if a.kecamatans and b.kecamatans and not a.kecamatans & b.kecamatans:
        return False
    if any(_same_place(x, y) or _related(x, y) for x, y in pairs):
        return True
    # Most follow-ups only name the regency: fall back to regency + time + a telling word.
    if _gap(a, b) > _MAX_GAP:
        return False
    if a.category in _REGENCY_WIDE:
        return True
    if not (a.base and b.base):
        return False
    if any(abs(x - y) <= 0.1 * max(x, y) for x in a.counts for y in b.counts):
        return True
    return spread.distinctive(a.tokens & b.tokens, a.category, a.base)


def cluster(reports: list[Report]) -> list[list[Report]]:
    spread = _Spread(reports)
    clusters: list[_Cluster] = []
    for r in sorted(reports, key=lambda r: r.time):
        one = _Cluster([r])
        target = next((c for c in clusters if _same_event(c, one, spread)), None)
        if target:
            target.reports.append(r)
        else:
            clusters.append(one)
    # A report can bridge two clusters that formed before it arrived.
    merged = True
    while merged:
        merged = False
        for i, a in enumerate(clusters):
            j = next((j for j in range(i + 1, len(clusters)) if _same_event(a, clusters[j], spread)), None)
            if j is not None:
                a.reports.extend(clusters.pop(j).reports)
                merged = True
                break
    return [sorted(c.reports, key=lambda r: r.time) for c in clusters]


def _best(reports: list[Report]) -> Report:
    return max(reports, key=lambda r: (r.method == "llm", r.extraction.confidence or 0, -r.time.timestamp()))


def to_incident(reports: list[Report], gaz: Gazetteer) -> dict:
    c = _Cluster(reports)
    best = _best(reports)
    kab = c.kab_kota
    # A named kecamatan settles Kota vs Kabupaten better than a vote (Harjamukti is in Kota Cirebon).
    by_kec = Counter(k for r in reports for k in gaz.regencies_of_kec(r.extraction.kecamatan) if gaz.base(k) == c.base)
    if len(by_kec) == 1:
        kab = next(iter(by_kec))
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


def unique_ids(incidents: list[dict]) -> list[dict]:
    """Ids hash category|kab|kec|date, so two incidents can share one. The earliest
    keeps it (shared links stay valid); later ones also hash their first source url."""
    taken: set[str] = set()
    for inc in sorted(incidents, key=lambda i: (i["first_reported"], i["sources"][0]["url"])):
        if inc["id"] in taken:
            inc["id"] = hashlib.sha1(f"{inc['id']}|{inc['sources'][0]['url']}".encode()).hexdigest()[:10]
        taken.add(inc["id"])
    return incidents
