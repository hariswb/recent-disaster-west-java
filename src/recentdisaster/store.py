import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from recentdisaster.config import CACHE_PATH
from recentdisaster.models import Report
from recentdisaster.normalize import parse_date


@dataclass
class Cache:
    """URL-keyed state carried between runs (committed to the repo).

    seen:    canonical url -> first time any source listed it
    reports: canonical url -> extraction of a candidate article
    """

    seen: dict[str, datetime] = field(default_factory=dict)
    reports: dict[str, Report] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path = CACHE_PATH) -> "Cache":
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text())
        return cls(
            seen={u: parse_date(t) for u, t in raw.get("seen", {}).items()},
            reports={u: Report.model_validate(r) for u, r in raw.get("reports", {}).items()},
        )

    def prune(self, cutoff: datetime) -> None:
        self.seen = {u: t for u, t in self.seen.items() if t >= cutoff}
        self.reports = {u: r for u, r in self.reports.items() if r.time >= cutoff or r.first_seen >= cutoff}

    def save(self, path: Path = CACHE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "seen": {u: t.isoformat() for u, t in sorted(self.seen.items())},
            "reports": {u: r.model_dump(mode="json", exclude_none=True) for u, r in sorted(self.reports.items())},
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
