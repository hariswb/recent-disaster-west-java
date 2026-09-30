"""Deterministic layer: relevance prefilter and fallback extraction."""

import json
import re
from dataclasses import dataclass, field
from functools import cache

from recentdisaster.config import GAZETTEER_PATH, load_yaml
from recentdisaster.models import Entity, Extraction, RawItem, Victims

# --------------------------------------------------------------------------
# Gazetteer
# --------------------------------------------------------------------------

# When a bare name ("Bogor") could be either the kota or the kabupaten.
_AMBIGUOUS_DEFAULT = {"bandung": "Kota Bandung"}
_JABAR = re.compile(r"\b(jawa barat|jabar)\b", re.I)


@dataclass
class Location:
    kab_kota: str | None = None
    kecamatan: str | None = None
    lat: float | None = None
    lon: float | None = None
    jabar_mentioned: bool = False
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.kab_kota is not None


class Gazetteer:
    def __init__(self, data: dict):
        self.province = data["province"]
        self.regencies = data["regencies"]
        self.by_name = {r["name"]: r for r in self.regencies}
        bases: dict[str, list[dict]] = {}
        for r in self.regencies:
            bases.setdefault(r["base"].lower(), []).append(r)
        self._bases = bases

        # Regency patterns: explicit ("Kota Bogor", "Kab. Garut") and bare ("Garut").
        self._kab_explicit: list[tuple[re.Pattern, str]] = []
        for r in self.regencies:
            base = re.escape(r["base"])
            guard = self._longer_guard(r["base"])
            prefix = r"kota" if r["type"] == "kota" else r"(?:kabupaten|kab\.?)"
            self._kab_explicit.append((re.compile(rf"\b{prefix}\s+{base}\b{guard}", re.I), r["name"]))
            for alias in r.get("aliases", []):
                flags = 0 if alias.isupper() else re.I  # acronyms are case-sensitive
                self._kab_explicit.append((re.compile(rf"\b{re.escape(alias)}\b", flags), r["name"]))
        self._kab_bare: list[tuple[re.Pattern, str]] = []
        for base, regs in bases.items():
            if len(regs) == 1:
                name = regs[0]["name"]
            else:
                name = _AMBIGUOUS_DEFAULT.get(base) or next(r["name"] for r in regs if r["type"] == "kabupaten")
            guard = self._longer_guard(regs[0]["base"])
            self._kab_bare.append((re.compile(rf"\b{re.escape(base)}\b{guard}", re.I), name))

        # Districts
        self._districts: dict[str, list[tuple[dict, dict]]] = {}
        for r in self.regencies:
            for d in r["districts"]:
                self._districts.setdefault(d["name"].lower(), []).append((d, r))
        names = sorted(self._districts, key=len, reverse=True)
        alt = "|".join(re.escape(n) for n in names)
        self._kec_explicit = re.compile(rf"\b(?:kecamatan|kec\.)\s+({alt})\b", re.I)
        self._kec_bare = re.compile(rf"\b({alt})\b", re.I)

    def _longer_guard(self, base: str) -> str:
        """Negative lookahead so 'Bandung' does not match 'Bandung Barat'."""
        tails = [
            r["base"][len(base) :].strip()
            for r in self.regencies
            if r["base"].lower().startswith(base.lower() + " ")
        ]
        return "".join(rf"(?!\s+{re.escape(t)})" for t in tails)

    def centroid(self, kab_kota: str | None, kecamatan: str | None = None) -> tuple[float, float] | None:
        r = self.by_name.get(kab_kota or "")
        if r and kecamatan:
            for d in r["districts"]:
                if d["name"].lower() == kecamatan.lower():
                    return d["lat"], d["lon"]
        if r:
            return r["lat"], r["lon"]
        return None

    def normalize_kab(self, name: str | None) -> str | None:
        """Map free text ('Kab. Garut', 'garut', 'Kota Bogor') to a canonical name."""
        if not name:
            return None
        loc = self.locate(name)
        return loc.kab_kota

    def normalize_kec(self, kab_kota: str | None, name: str | None) -> str | None:
        if not name:
            return None
        key = re.sub(r"^(kecamatan|kec\.?)\s+", "", name.strip(), flags=re.I).lower()
        for d, r in self._districts.get(key, []):
            if kab_kota is None or r["name"] == kab_kota:
                return d["name"]
        return None

    def locate(self, text: str, title: str = "") -> Location:
        loc = Location(jabar_mentioned=bool(_JABAR.search(text) or _JABAR.search(title)))
        scores: dict[str, float] = {}

        def add(name: str, w: float, where: str) -> None:
            scores[name] = scores.get(name, 0) + (w * 2 if where == "title" else w)

        for where, t in (("title", title), ("text", text)):
            if not t:
                continue
            for rx, name in self._kab_explicit:
                if rx.search(t):
                    add(name, 3, where)
            for rx, name in self._kab_bare:
                if rx.search(t):
                    add(name, 1, where)

        kec_hits: list[tuple[str, float]] = []  # (district name lower, weight)
        for where, t in (("title", title), ("text", text)):
            if not t:
                continue
            for m in self._kec_explicit.finditer(t):
                kec_hits.append((m.group(1).lower(), 3 if where == "text" else 6))
            for m in self._kec_bare.finditer(t):
                n = m.group(1).lower()
                if len(n) >= 6 and n not in self._bases:
                    kec_hits.append((n, 0.5 if where == "text" else 1))

        # Districts vote for their regency; unique names vote harder.
        for n, w in kec_hits:
            owners = self._districts[n]
            for _, r in owners:
                if len(owners) == 1 or r["name"] in scores:
                    add(r["name"], w / len(owners), "text")

        loc.scores = scores
        if scores:
            best = max(scores.items(), key=lambda kv: kv[1])
            # Bare-only single weak hit in body text is not enough evidence.
            if best[1] >= 1:
                loc.kab_kota = best[0]
        if loc.kab_kota:
            ranked = sorted(kec_hits, key=lambda h: -h[1])
            for n, _ in ranked:
                for d, r in self._districts[n]:
                    if r["name"] == loc.kab_kota:
                        loc.kecamatan = d["name"]
                        break
                if loc.kecamatan:
                    break
            loc.lat, loc.lon = self.centroid(loc.kab_kota, loc.kecamatan)
        return loc


@cache
def gazetteer() -> Gazetteer:
    return Gazetteer(json.loads(GAZETTEER_PATH.read_text()))


# --------------------------------------------------------------------------
# Taxonomy
# --------------------------------------------------------------------------


def _alt(patterns: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(patterns) + r")\b", re.I)


def _mixed_case(terms: list[str]) -> re.Pattern:
    """Acronyms (containing capitals) match case-sensitively, words do not."""
    parts = [t if any(c.isupper() for c in t) else f"(?i:{t})" for t in terms]
    return re.compile(r"\b(?:" + "|".join(parts) + r")\b")


@dataclass
class Taxonomy:
    order: list[str]
    patterns: dict[str, re.Pattern]
    subcategories: dict[str, dict[str, re.Pattern]]
    labels: dict[str, str]
    groups: dict[str, str]
    exclude: re.Pattern
    entities: dict[str, re.Pattern]


@cache
def taxonomy() -> Taxonomy:
    cfg = load_yaml("taxonomy.yaml")
    cats = {k: v for k, v in cfg["categories"].items() if v.get("enabled", True)}
    return Taxonomy(
        order=[c for c in cfg["order"] if c in cats],
        patterns={k: _alt(v["patterns"]) for k, v in cats.items()},
        subcategories={
            k: {s: _alt(p) for s, p in v.get("subcategories", {}).items()} for k, v in cats.items()
        },
        labels={k: v["label"] for k, v in cfg["categories"].items()},
        groups={k: v["group"] for k, v in cfg["categories"].items()},
        exclude=_alt(cfg["exclude_title"]),
        entities={k: _mixed_case(v) for k, v in cfg["entities"].items()},
    )


def classify(title: str, text: str) -> tuple[str | None, str | None, bool]:
    """Return (category, subcategory, matched_in_title)."""
    tx = taxonomy()
    category, in_title = None, False
    for c in tx.order:
        if tx.patterns[c].search(title):
            category, in_title = c, True
            break
    if category is None:
        for c in tx.order:
            if tx.patterns[c].search(text):
                category = c
                break
    if category is None:
        return None, None, False
    sub = next(
        (s for s, rx in tx.subcategories.get(category, {}).items() if rx.search(title) or rx.search(text)),
        None,
    )
    return category, sub, in_title


def excluded(title: str) -> bool:
    return bool(taxonomy().exclude.search(title))


# --------------------------------------------------------------------------
# Victims
# --------------------------------------------------------------------------

_NUM_WORDS = {
    "seorang": 1, "satu": 1, "dua": 2, "tiga": 3, "empat": 4, "lima": 5, "enam": 6,
    "tujuh": 7, "delapan": 8, "sembilan": 9, "sepuluh": 10, "sebelas": 11,
    "belasan": 10, "puluhan": 20, "ratusan": 100, "ribuan": 1000,
}
_NUM = r"(?:\d+(?:[.,]\d+)?\s+(?:ribu|juta)|\d{1,3}(?:\.\d{3})+|\d+|" + "|".join(_NUM_WORDS) + r")"
_STATUS = {
    "dead": r"tewas|meninggal(?: dunia)?|wafat|kehilangan nyawa|ditemukan tewas|ditemukan meninggal",
    "injured": r"luka(?:-luka)?(?: berat| ringan)?|terluka|mengalami luka|cedera|dirawat|dilarikan ke|dibawa ke (?:rumah sakit|puskesmas|rsud)",
    "missing": r"hilang|belum ditemukan|masih dicari|tertimbun",
    "displaced": r"mengungsi|diungsikan|dievakuasi|kehilangan tempat tinggal",
    "affected": r"keracunan|terdampak|mengalami gejala|mual|terpapar",
    "houses": r"rusak(?: berat| ringan| sedang)?|terendam|terbakar|hangus|roboh|ambruk|tertimbun|hanyut",
}
_PEOPLE = r"orang|warga|siswa|murid|pelajar|santri|korban|jiwa|anak|balita|lansia|penumpang|pekerja|buruh|karyawan|pengendara|pengunjung|penghuni|guru|jemaah|keluarga|KK|kepala keluarga|pasien|petani|nelayan|pemuda|remaja|mahasiswa"
_HOUSES = r"rumah|bangunan|unit|kios|ruko|gedung|kelas|lapak|toko"
_VICTIM_RX = re.compile(
    rf"(?P<num>{_NUM})\s+(?P<noun>(?:[\w-]+\s+){{0,3}}?)(?:di antaranya\s+|lainnya\s+|telah\s+|sudah\s+|masih\s+|yang\s+)*(?P<status>{'|'.join(f'(?P<{k}>{v})' for k, v in _STATUS.items())})\b",
    re.I,
)
_PEOPLE_RX = re.compile(rf"\b(?:{_PEOPLE})\b", re.I)
_HOUSES_RX = re.compile(rf"\b(?:{_HOUSES})\b", re.I)


def _to_int(s: str) -> int:
    s = s.lower()
    if s in _NUM_WORDS:
        return _NUM_WORDS[s]
    if m := re.fullmatch(r"(\d+(?:[.,]\d+)?)\s+(ribu|juta)", s):
        return int(float(m.group(1).replace(",", ".")) * (1000 if m.group(2) == "ribu" else 1_000_000))
    return int(s.replace(".", ""))


def extract_victims(text: str) -> Victims:
    found: dict[str, int] = {}
    for m in _VICTIM_RX.finditer(text):
        noun = m.group("noun") or ""
        status = next(k for k in _STATUS if m.group(k))
        is_house = bool(_HOUSES_RX.search(noun))
        is_people = bool(_PEOPLE_RX.search(noun))
        if status == "houses":
            if not is_house:
                continue
        elif is_house or not is_people:  # "2024 hilang" is not a missing person
            continue
        n = _to_int(m.group("num"))
        if n > 50_000_000:
            continue
        found[status] = max(found.get(status, 0), n)
    return Victims(**found)


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------

_NAMED = [
    ("sekolah", re.compile(r"\b((?:SDN|SMPN|SMAN|SMKN|SD|SMP|SMA|SMK|MTsN|MTs|MI|MAN|MA|TK|PAUD)\s+(?:Negeri\s+)?\d*\s?[A-Z][\w]*(?:\s[A-Z][\w]*)?)")),
    ("sekolah", re.compile(r"\b((?:Pondok Pesantren|Ponpes|Pesantren)\s+[A-Z][\w-]*(?:\s[A-Z][\w-]*){0,2})")),
    ("desa", re.compile(r"\b((?:Desa|Kampung|Dusun|Kelurahan)\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")),
    ("pasar", re.compile(r"\b(Pasar\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")),
    ("fasilitas_kesehatan", re.compile(r"\b((?:RSUD|RS|Puskesmas)\s+[A-Z][\w]*(?:\s[A-Z][\w]*)?)")),
]
_DESA_RX = re.compile(r"\b(?:Desa|Kelurahan)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)")


def extract_entities(text: str) -> list[Entity]:
    out: dict[tuple[str, str | None], None] = {}
    for typ, rx in _NAMED:
        for m in rx.finditer(text):
            out.setdefault((typ, m.group(1).strip()), None)
    named_types = {t for t, _ in out}
    for typ, rx in taxonomy().entities.items():
        if typ not in named_types and rx.search(text):
            out.setdefault((typ, None), None)
    return [Entity(type=t, name=n) for t, n in list(out)[:8]]


# --------------------------------------------------------------------------
# Prefilter + fallback extraction
# --------------------------------------------------------------------------


@dataclass
class Screen:
    candidate: bool
    category: str | None
    subcategory: str | None
    in_title: bool
    location: Location
    reason: str


def screen(item: RawItem) -> Screen:
    text = item.summary
    category, sub, in_title = classify(item.title, text)
    loc = gazetteer().locate(text, item.title)
    if category is None:
        return Screen(False, None, None, False, loc, "no category keyword")
    if excluded(item.title):
        return Screen(False, category, sub, in_title, loc, "excluded title")
    in_jabar = item.jabar_only or loc.found or loc.jabar_mentioned
    if not in_jabar:
        return Screen(False, category, sub, in_title, loc, "not in Jabar")
    return Screen(True, category, sub, in_title, loc, "ok")


def rule_extract(item: RawItem, body: str = "") -> Extraction:
    text = f"{item.summary}\n{body}"
    category, sub, in_title = classify(item.title, text)
    loc = gazetteer().locate(text, item.title)
    desa = _DESA_RX.search(f"{item.title}\n{text}")
    return Extraction(
        is_incident=bool(category) and in_title and not excluded(item.title),
        # Feeds like ANTARA Jabar also carry national/world news, so the rules
        # need an actual Jabar place name; jabar_only only widens the prefilter.
        in_jabar=loc.found or loc.jabar_mentioned,
        category=category,
        subcategory=sub,
        kab_kota=loc.kab_kota,
        kecamatan=loc.kecamatan,
        desa=desa.group(1) if desa else None,
        victims=extract_victims(f"{item.title}. {text}"),
        affected_entities=extract_entities(f"{item.title}. {text}"),
        summary=(item.summary or body)[:300] or None,
        confidence=0.4,
    )
