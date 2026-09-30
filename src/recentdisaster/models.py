from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CATEGORIES = (
    "banjir",
    "longsor",
    "gempa",
    "angin",
    "kebakaran",
    "kekeringan",
    "gunung",
    "mbg",
    "keracunan",
    "wabah",
    "perundungan",
    "kekerasan_seksual",
    "kekerasan_anak",
    "tawuran",
    "intoleransi",
    "tppo",
    "infrastruktur",
    "kecelakaan",
    "lainnya",
)
Category = Literal[CATEGORIES]  # type: ignore[valid-type]


class RawItem(BaseModel):
    """One article as seen in a source listing/feed."""

    source_id: str
    source_name: str
    url: str
    title: str
    summary: str = ""
    published: datetime | None = None
    jabar_only: bool = False
    publisher: str | None = None  # for aggregators (Google News)


class Victims(BaseModel):
    dead: int | None = None
    injured: int | None = None
    missing: int | None = None
    displaced: int | None = None
    affected: int | None = None
    houses: int | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _coerce(cls, v):
        if v in ("", None, "null", "unknown", "tidak diketahui"):
            return None
        if isinstance(v, str):
            digits = "".join(ch for ch in v if ch.isdigit())
            return int(digits) if digits else None
        if isinstance(v, float):
            return int(v)
        return v

    def any(self) -> bool:
        return any(v for v in self.model_dump().values())


class Entity(BaseModel):
    type: str
    name: str | None = None


class Extraction(BaseModel):
    """Structured facts about one article (from the LLM or from rules)."""

    is_incident: bool
    in_jabar: bool
    category: Category | None = None
    subcategory: str | None = None
    kab_kota: str | None = None
    kecamatan: str | None = None
    desa: str | None = None
    event_time: str | None = None
    victims: Victims = Field(default_factory=Victims)
    affected_entities: list[Entity] = Field(default_factory=list)
    summary: str | None = None
    confidence: float | None = None

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        if isinstance(v, str):
            v = v.strip().lower()
            return v if v in CATEGORIES else "lainnya"
        return v

    @field_validator("victims", mode="before")
    @classmethod
    def _victims(cls, v):
        return v or {}

    @field_validator("affected_entities", mode="before")
    @classmethod
    def _entities(cls, v):
        out = []
        for e in v or []:
            if isinstance(e, Entity):
                out.append(e)
            elif isinstance(e, str):
                out.append({"type": e})
            elif isinstance(e, dict) and e.get("type"):
                out.append(e)
        return out


class Report(BaseModel):
    """An article plus its extraction; what we cache and cluster."""

    item: RawItem
    first_seen: datetime
    extraction: Extraction
    method: Literal["llm", "rules"]
    provider: str | None = None

    @property
    def time(self) -> datetime:
        return self.item.published or self.first_seen
