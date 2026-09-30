import os
from functools import cache
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

WIB = ZoneInfo("Asia/Jakarta")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 recentdisaster-monitor"
)


def project_root() -> Path:
    if env := os.environ.get("RECENTDISASTER_ROOT"):
        return Path(env)
    for p in [Path.cwd(), *Path.cwd().parents]:
        if (p / "config" / "sources.yaml").exists():
            return p
    return Path(__file__).resolve().parents[2]


ROOT = project_root()
CONFIG_DIR = ROOT / "config"
GAZETTEER_PATH = ROOT / "data" / "gazetteer" / "jabar.json"
CACHE_PATH = ROOT / "data" / "cache" / "processed.json"
OUTPUT_PATH = ROOT / "docs" / "data" / "incidents.json"


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (existing vars win).
    Local convenience only; in GitHub Actions secrets arrive as env vars."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.split(" #", 1)[0].strip().strip("'\"")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


@cache
def load_yaml(name: str) -> dict:
    return yaml.safe_load((CONFIG_DIR / name).read_text())
