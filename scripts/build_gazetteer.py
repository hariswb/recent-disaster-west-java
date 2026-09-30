"""Build data/gazetteer/jabar.json from cahyadsn/wilayah_boundaries (MIT).

Run once (or when administrative boundaries change):
    uv run python scripts/build_gazetteer.py
"""

import json
import re
from pathlib import Path

import httpx

BASE = "https://raw.githubusercontent.com/cahyadsn/wilayah_boundaries/main/db"
URLS = {
    "kab": f"{BASE}/kab/wilayah_boundaries_kab_32.sql",
    "kec": f"{BASE}/kec/wilayah_boundaries_kec_32.sql",
}
ROW = re.compile(r"\('(32(?:\.\d+)+)','((?:[^']|'')*)',(-?[\d.]+),(-?[\d.]+)")
OUT = Path(__file__).resolve().parents[1] / "data" / "gazetteer" / "jabar.json"

# Common short forms used in news text, mapped to kab/kota code.
EXTRA_ALIASES = {
    "32.17": ["KBB"],
}


def rows(url: str) -> list[tuple[str, str, float, float]]:
    text = httpx.get(url, timeout=120, follow_redirects=True).text
    return [
        (code, name.replace("''", "'"), round(float(lat), 5), round(float(lon), 5))
        for code, name, lat, lon in ROW.findall(text)
    ]


def main() -> None:
    kab_rows = rows(URLS["kab"])
    kec_rows = rows(URLS["kec"])
    regencies = []
    for code, name, lat, lon in kab_rows:
        is_kota = name.startswith("Kota ")
        base = re.sub(r"^(Kabupaten|Kota)\s+", "", name)
        regencies.append(
            {
                "code": code,
                "name": name,
                "base": base,
                "type": "kota" if is_kota else "kabupaten",
                "lat": lat,
                "lon": lon,
                "aliases": EXTRA_ALIASES.get(code, []),
                "districts": [
                    {"code": kc, "name": kn, "lat": klat, "lon": klon}
                    for kc, kn, klat, klon in kec_rows
                    if kc.startswith(code + ".")
                ],
            }
        )
    data = {
        "province": {"code": "32", "name": "Jawa Barat", "lat": -6.92, "lon": 107.6},
        "source": "https://github.com/cahyadsn/wilayah_boundaries (MIT)",
        "regencies": regencies,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    n_kec = sum(len(r["districts"]) for r in regencies)
    print(f"wrote {OUT}: {len(regencies)} kab/kota, {n_kec} kecamatan")


if __name__ == "__main__":
    main()
