"""Every incident the monitor has published, kept after it leaves the 24-hour window,
for the weekly and monthly recaps.

Files (served with the dashboard):
    data/archive/YYYY-MM.json  incidents by the WIB month of their first report
    data/archive/index.json    months on file and their counts

Each run owns the sources inside its window. An incident is merged with the archived
entries it shares a source with; the merged entry keeps the oldest entry's id and its
sources from before the window, plus this run's. An archived source inside the window
that no incident of this run claims was dropped (re-extracted as no incident, or split
into another cluster), so it leaves the archive too.

`refs` holds every source as a short url hash and its time (matching, counts); `sources`
keeps at most MAX_SOURCES links to show, publisher links before Google News ones, which
are long and make up most sources.
"""

import hashlib
import itertools
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from recentdisaster.cluster import past_fields
from recentdisaster.config import ARCHIVE_DIR, WIB
from recentdisaster.models import Victims
from recentdisaster.normalize import canonical_url

log = logging.getLogger(__name__)

MAX_SOURCES = 8
# Reports stay in the cache for 48 hours, so an incident's entry is at most this old.
_LOOKBACK = timedelta(days=3)


def _t(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _month(s: str | datetime) -> str:
    return (_t(s) if isinstance(s, str) else s).astimezone(WIB).strftime("%Y-%m")


def _ref(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:12]


def _shown(sources: list[dict]) -> list[dict]:
    pick = sorted(sources, key=lambda s: ("news.google.com" in s["url"], _t(s["published"])))[:MAX_SOURCES]
    return sorted(pick, key=lambda s: _t(s["published"]))


def _finish(e: dict, refs: dict[str, str], sources: list[dict]) -> dict:
    times = sorted(refs.values(), key=_t)
    e["refs"] = dict(sorted(refs.items(), key=lambda kv: _t(kv[1])))
    uniq: dict[str, dict] = {}
    for s in sources:
        if (h := _ref(s["url"])) in refs:
            uniq.setdefault(h, s)
    e["sources"] = _shown(list(uniq.values()))
    e["report_count"] = len(refs)
    e["first_reported"], e["last_reported"] = times[0], times[-1]
    e.update(past_fields(e.get("event_time"), e["first_reported"]))
    return e


def _entry(inc: dict) -> dict:
    """What a recap needs: no confidence, no per-source headline."""
    e = {k: v for k, v in inc.items() if k not in ("confidence", "sources")}
    sources = [{k: v for k, v in s.items() if k != "title"} for s in inc["sources"]]
    return _finish(e, {_ref(s["url"]): s["published"] for s in sources}, sources)


def _merge(inc: dict, olds: list[dict], cutoff: datetime) -> dict:
    e = _entry(inc)
    if not olds:
        return e
    olds = sorted(olds, key=lambda o: _t(o["first_reported"]))
    e["id"] = olds[0]["id"]  # links shared earlier keep working
    before = {h: t for o in olds for h, t in o["refs"].items() if _t(t) < cutoff}
    if not before:
        return e  # every report is in this run's window: this run's version stands
    e["event_time"] = e.get("event_time") or next((o["event_time"] for o in olds if o.get("event_time")), None)
    e["victims"] = {
        k: n for k in Victims.model_fields
        if (n := max([e["victims"].get(k) or 0] + [o["victims"].get(k) or 0 for o in olds]))
    }
    seen = {(x["type"], x.get("name")) for x in e["affected_entities"]}
    e["affected_entities"] = (e["affected_entities"] + [
        x for o in olds for x in o["affected_entities"] if (x["type"], x.get("name")) not in seen])[:10]
    return _finish(e, {**before, **e["refs"]}, [*e["sources"], *(s for o in olds for s in o["sources"])])


def _load(root: Path, month: str) -> list[dict]:
    p = root / f"{month}.json"
    return json.loads(p.read_text())["incidents"] if p.exists() else []


def _dump(month: str, entries: list[dict]) -> str:
    """One incident per line: readable diffs in the data commits."""
    rows = ",\n".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) for e in entries)
    return f'{{"month":"{month}","incidents":[\n{rows}\n]}}\n'


def update(incidents: list[dict], now: datetime, cutoff: datetime, root: Path = ARCHIVE_DIR) -> dict[str, str]:
    """Fold this run's incidents into the archive. Returns run id -> archive id."""
    months = {_month(now - _LOOKBACK), _month(now)}
    alive = dict(enumerate(e for m in sorted(months) for e in _load(root, m)))
    owner = {h: n for n, e in alive.items() for h in e["refs"]}
    fresh = itertools.count(len(alive))
    ids: dict[str, str] = {}
    touched: set[int] = set()
    for inc in incidents:
        hits = sorted({n for s in inc["sources"] if (n := owner.get(_ref(s["url"]))) in alive})
        e = _merge(inc, [alive.pop(n) for n in hits], cutoff)
        if not hits and any(o["id"] == e["id"] for o in alive.values()):
            # Same category|kab|kec|date as an archived, unrelated incident.
            e["id"] = hashlib.sha1(f"{e['id']}|{inc['sources'][0]['url']}".encode()).hexdigest()[:10]
        n = next(fresh)
        alive[n] = e
        touched.add(n)
        owner.update(dict.fromkeys(e["refs"], n))
        ids[inc["id"]] = e["id"]
    for n in [n for n in alive if n not in touched]:
        e = alive[n]
        kept = {h: t for h, t in e["refs"].items() if _t(t) < cutoff}
        if len(kept) == len(e["refs"]):
            continue
        if kept:
            alive[n] = _finish(e, kept, e["sources"])
        else:
            del alive[n]

    by_month: dict[str, list[dict]] = {m: [] for m in months}
    for e in alive.values():
        by_month.setdefault(_month(e["first_reported"]), []).append(e)
    root.mkdir(parents=True, exist_ok=True)
    for month, entries in by_month.items():
        p = root / f"{month}.json"
        if entries:
            text = _dump(month, sorted(entries, key=lambda e: (_t(e["first_reported"]), e["id"])))
            if not p.exists() or p.read_text() != text:
                p.write_text(text)
        elif p.exists():
            p.unlink()
    _write_index(root, now)
    return ids


def _write_index(root: Path, now: datetime) -> None:
    months = []
    for p in sorted(root.glob("????-??.json")):
        entries = json.loads(p.read_text())["incidents"]
        first = min((e["first_reported"] for e in entries), key=_t)
        months.append({"month": p.stem, "incidents": len(entries), "first_reported": first})
    index = {"updated_at": now.isoformat(timespec="seconds"), "months": months}
    (root / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1) + "\n")
