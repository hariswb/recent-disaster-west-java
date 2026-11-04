"""Rebuild the incident archive from every incidents.json committed to git.

Each data commit is a 24-hour snapshot, so replaying them oldest first gives the
archive the runs would have built. The last snapshot is the current file, which is
rewritten with the archive's ids and the past-incident fields.

The archive starts at SINCE: the duplicate-report fixes (2fed2df, 23 Oct) first ran on
24 Oct 08:33 WIB, so incidents first reported from 24 Oct on were all clustered by them.

    uv run python scripts/backfill_archive.py
"""

import json
import subprocess
from datetime import datetime, timedelta

from recentdisaster import archive
from recentdisaster.cluster import past_fields
from recentdisaster.config import ARCHIVE_DIR, OUTPUT_PATH, ROOT, WIB

SINCE = datetime(2026, 10, 24, tzinfo=WIB)
PATHS = ["docs/kebencanaan-jabar/data/incidents.json", "docs/data/incidents.json"]  # before the move to channels


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def snapshots():
    commits = git("log", "--reverse", "--format=%H", "--", *PATHS).stdout.split()
    for c in commits:
        for p in PATHS:
            r = git("show", f"{c}:{p}")
            if r.returncode == 0:
                yield json.loads(r.stdout)
                break


def prepare(inc: dict) -> dict:
    inc.setdefault("victims", {})
    inc.setdefault("affected_entities", [])
    if "past" not in inc:
        inc.update(past_fields(inc.get("event_time"), inc["first_reported"]))
    return inc


def main() -> None:
    for p in ARCHIVE_DIR.glob("*.json"):
        p.unlink()
    n = 0
    for snap in snapshots():
        now = datetime.fromisoformat(snap["generated_at"])
        if now < SINCE:
            continue
        cutoff = now - timedelta(hours=snap.get("window_hours", 24))
        incidents = [prepare(i) for i in snap["incidents"] if datetime.fromisoformat(i["first_reported"]) >= SINCE]
        ids = archive.update(incidents, now, cutoff, ARCHIVE_DIR)
        n += 1
    current = json.loads(OUTPUT_PATH.read_text())
    if current["generated_at"] == snap["generated_at"]:
        for inc in current["incidents"]:
            prepare(inc)
            inc["id"] = ids.get(inc["id"], inc["id"])
        OUTPUT_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=1))
    index = json.loads((ARCHIVE_DIR / "index.json").read_text())
    print(f"{n} snapshots replayed:", ", ".join(f"{m['month']} {m['incidents']}" for m in index["months"]))


if __name__ == "__main__":
    main()
