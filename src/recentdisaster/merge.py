"""Second opinion on clusters: the rules in cluster.py are strict (two fires in one
regency are two fires), so the same event reported with a different category, regency or
kecamatan stays split. An LLM judges only the borderline pairs the rules left apart."""

import hashlib
import json
import logging
from datetime import timedelta

from recentdisaster import llm
from recentdisaster.cluster import _Cluster, _gap, family, title_tokens
from recentdisaster.config import WIB
from recentdisaster.models import Report
from recentdisaster.normalize import canonical_url
from recentdisaster.rules import gazetteer

log = logging.getLogger(__name__)

MAX_GAP = timedelta(hours=48)
MAX_PAIRS = 40
PAIRS_PER_CALL = 10
HEADLINES = 4
CALL_RESERVE = 6  # extra LLM calls and seconds on top of the extraction budget
TIME_RESERVE = 120
RARE = 2  # a headline word used in at most this many regencies points at one event ("ciremai", not "mbg")

PROMPT = """Anda menilai apakah dua kelompok berita melaporkan KEJADIAN NYATA YANG SAMA.
Setiap pasangan berisi kelompok A dan B (kategori, lokasi, rentang waktu, beberapa judul).

"same": true jika keduanya membahas satu kejadian spesifik yang sama: kebakaran yang sama,
kasus yang sama, keracunan yang sama, bencana yang sama. Berita lanjutan (penanganan, korban,
tanggapan pejabat, penyebab) dari kejadian itu juga sama. Perbedaan label kategori atau lokasi
yang dipilih media TIDAK menjadikannya berbeda: lokasi kejadian dan lokasi penangkapan, gunung di
perbatasan dua kabupaten, atau "keracunan" vs "MBG" bisa satu kejadian.

"same": false jika keduanya dua kejadian terpisah yang kebetulan sejenis atau berdekatan
(dua kebakaran lahan di kecamatan berbeda, dua keracunan di sekolah berbeda). Jika ragu, false.

Balas HANYA JSON: {"verdicts": [{"id": <id pasangan>, "same": true|false}, ...]}"""


def _earliest(group: list[Report]) -> str:
    return canonical_url(min(group, key=lambda r: r.time).item.url)


def pair_key(a: list[Report], b: list[Report]) -> str:
    return hashlib.sha1("|".join(sorted((_earliest(a), _earliest(b)))).encode()).hexdigest()[:16]


def _tokens(group: list[Report]) -> frozenset[str]:
    return frozenset(w for r in group for w in title_tokens(r.item.title))


def candidates(groups: list[list[Report]], limit: int = MAX_PAIRS) -> list[tuple[int, int]]:
    """Pairs the rules kept apart but that could be one event: same category family,
    close in time, and sharing a word few clusters use. Best first."""
    gaz = gazetteer()
    clusters = [_Cluster(g) for g in groups]
    toks = [_tokens(g) for g in groups]
    places: dict[str, set] = {}  # word -> regencies whose headlines use it
    for g in groups:
        for r in g:
            if r.extraction.kab_kota:
                for w in title_tokens(r.item.title):
                    places.setdefault(w, set()).add(gaz.base(r.extraction.kab_kota))
    rare = {w for w, bases in places.items() if len(bases) <= RARE}
    scored = []
    for i, a in enumerate(clusters):
        for j in range(i + 1, len(clusters)):
            b = clusters[j]
            if family(a.category) != family(b.category) or _gap(a, b) > MAX_GAP:
                continue
            shared = toks[i] & toks[j] & rare
            if not shared:
                continue
            same_base = bool(a.base and a.base == b.base)
            scored.append((len(shared) + same_base, i, j))
    scored.sort(key=lambda s: -s[0])
    return [(i, j) for _, i, j in scored[:limit]]


def _describe(group: list[Report]) -> dict:
    c = _Cluster(group)
    times = [r.time.astimezone(WIB) for r in group]
    ordered = sorted(group, key=lambda r: r.time)
    pick = ordered[: HEADLINES - 1] + ordered[-1:] if len(ordered) > HEADLINES else ordered
    return {
        "kategori": c.category,
        "kab_kota": sorted({r.extraction.kab_kota for r in group if r.extraction.kab_kota}),
        "kecamatan": sorted({r.extraction.kecamatan for r in group if r.extraction.kecamatan}),
        "waktu": f"{min(times):%d/%m %H:%M}-{max(times):%d/%m %H:%M} WIB",
        "jumlah_berita": len(group),
        "judul": [r.item.title for r in pick],
    }


def build_messages(batch: list[tuple[int, list[Report], list[Report]]]) -> list[dict]:
    body = [{"id": n, "A": _describe(a), "B": _describe(b)} for n, a, b in batch]
    return [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": json.dumps(body, ensure_ascii=False)},
    ]


def _validate(d: dict) -> dict[int, bool]:
    return {int(v["id"]): bool(v["same"]) for v in d["verdicts"]}


def refine(groups: list[list[Report]], pool: "llm.ProviderPool", verdicts: dict[str, bool]) -> tuple[list[list[Report]], dict]:
    """Merge clusters the LLM says are one event. `verdicts` (pair key -> same) is the
    cross-run cache: a pair is asked once. Returns (groups, stats)."""
    stats = {"pairs": 0, "asked": 0, "merged": 0, "failed": 0}
    pairs = [(i, j, pair_key(groups[i], groups[j])) for i, j in candidates(groups)]
    stats["pairs"] = len(pairs)
    todo = [(i, j, k) for i, j, k in pairs if k not in verdicts]
    for start in range(0, len(todo), PAIRS_PER_CALL):
        chunk = todo[start : start + PAIRS_PER_CALL]
        if not pool.available:
            break
        batch = [(n, groups[i], groups[j]) for n, (i, j, _) in enumerate(chunk)]
        try:
            answer, _ = pool.complete_json(build_messages(batch), _validate)
        except llm.LLMUnavailable as e:
            log.info("cluster refinement stopped: %s", e)
            stats["failed"] += 1
            break
        stats["asked"] += len(chunk)
        for n, (_, _, key) in enumerate(chunk):
            if n in answer:
                verdicts[key] = answer[n]

    for key in set(verdicts) - {k for _, _, k in pairs}:  # pairs that left the window
        del verdicts[key]
    parent = list(range(len(groups)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, key in pairs:
        if verdicts.get(key) and find(i) != find(j):
            parent[find(j)] = find(i)
            stats["merged"] += 1
    merged: dict[int, list[Report]] = {}
    for i, g in enumerate(groups):
        merged.setdefault(find(i), []).extend(g)
    return [sorted(g, key=lambda r: r.time) for g in merged.values()], stats
