"""Structured extraction through a rotating pool of free OpenAI-compatible providers."""

import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

import openai
from openai import OpenAI
from pydantic import ValidationError

from recentdisaster.config import load_yaml
from recentdisaster.models import CATEGORIES, Extraction, RawItem

log = logging.getLogger(__name__)
T = TypeVar("T")


class LLMUnavailable(Exception):
    """Every provider is exhausted, failing, or unconfigured."""


class InvalidOutput(Exception):
    pass


class HardTimeout(Exception):
    """A request exceeded its wall-clock deadline."""


def call_with_deadline[R](fn: Callable[[], R], seconds: float) -> R:
    """Run fn in a daemon thread and give up after `seconds` of wall-clock time.

    The HTTP client's own timeout is per read, so a server that trickles
    keep-alive bytes (queued free-tier requests) can hold a call open forever.
    An abandoned daemon thread cannot block interpreter exit."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except BaseException as e:  # re-raised in the caller
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise HardTimeout(f"no response within {seconds:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["value"]


# --------------------------------------------------------------------------
# JSON parsing
# --------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def parse_json(text: str) -> dict:
    """Lenient: strips code fences / prose and takes the outermost {...}."""
    if not text:
        raise InvalidOutput("empty response")
    t = _FENCE.sub("", text.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise InvalidOutput(f"no JSON object in: {text[:120]!r}")
    try:
        return json.loads(t[start : end + 1])
    except json.JSONDecodeError as e:
        raise InvalidOutput(f"bad JSON: {e}") from e


# --------------------------------------------------------------------------
# Provider pool
# --------------------------------------------------------------------------


MAX_COOLDOWN_WAIT = 65  # seconds; longer resets (daily quotas) retire the model
SERVER_ERROR_COOLDOWN = 120  # seconds a model rests after a 5xx (longer than we ever wait)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    models: list[str]
    json_mode: bool = True
    rpm: int = 10
    max_calls: int = 50
    model_params: dict[str, dict] = field(default_factory=dict)
    timeout: float = 60
    client: OpenAI | None = None
    model_idx: int = 0
    calls: int = 0
    exhausted: str | None = None  # reason
    strikes: int = 0
    last_call: float = 0.0
    dead_models: dict[str, str] = field(default_factory=dict)  # model -> reason
    cooldown: dict[str, float] = field(default_factory=dict)  # model -> monotonic ready time
    errors: list[str] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.models[self.model_idx]

    @property
    def live_models(self) -> list[str]:
        return [m for m in self.models if m not in self.dead_models]

    @property
    def available(self) -> bool:
        return self.exhausted is None and self.calls < self.max_calls and bool(self.live_models)

    def kill_model(self, model: str, reason: str) -> None:
        self.dead_models[model] = reason
        self.errors.append(f"{model}: {reason}")
        if not self.live_models:
            self.exhausted = f"all models unavailable ({reason})"

    def pick_model(self) -> float:
        """Select the live model that is ready soonest (current one on ties).
        Returns seconds until it is ready."""
        now = time.monotonic()
        live = self.live_models
        best = min(live, key=lambda m: (self.cooldown.get(m, 0), m != self.model))
        self.model_idx = self.models.index(best)
        return max(0.0, self.cooldown.get(best, 0) - now)

    def throttle(self) -> None:
        wait = self.last_call + 60 / max(self.rpm, 1) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self.last_call = time.monotonic()


_DURATION = re.compile(r"([\d.]+)(ms|h|m|s)")


def _parse_duration(v: str) -> float | None:
    """'16.004s', '1m26.4s', '2h3m', '7' -> seconds."""
    v = v.strip()
    try:
        return float(v)
    except ValueError:
        pass
    parts = _DURATION.findall(v)
    if not parts:
        return None
    mult = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(n) * mult[u] for n, u in parts)


def _reset_seconds(err: openai.APIStatusError) -> float | None:
    """How long until a 429 clears. None = unknown (treat as quota gone)."""
    msg = str(err).lower()
    if any(w in msg for w in ("per day", "daily", "rpd", "tpd", "quota", "credits")):
        return None
    headers = getattr(getattr(err, "response", None), "headers", {}) or {}
    for h in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        if headers.get(h) and (d := _parse_duration(headers[h])) is not None:
            return d
    return None


def _is_model_error(err: openai.APIStatusError) -> bool:
    msg = str(err).lower()
    return isinstance(err, openai.NotFoundError) or (
        "model" in msg and any(w in msg for w in ("not found", "does not exist", "unknown", "invalid model", "not supported", "decommissioned"))
    )


class ProviderPool:
    def __init__(
        self,
        cfg: dict | None = None,
        env: dict | None = None,
        client_factory: Callable[..., OpenAI] = OpenAI,
        sleep: Callable[[float], None] = time.sleep,
    ):
        cfg = cfg or load_yaml("llm_providers.yaml")
        env = os.environ if env is None else env
        self.max_total = cfg.get("max_llm_calls_per_run", 60)
        # Wall-clock budget for the whole LLM phase; afterwards rules take over
        # (and the articles are retried with an LLM next run).
        self.deadline = time.monotonic() + cfg.get("time_budget_seconds", 600)
        self.temperature = cfg.get("temperature", 0)
        self.sleep = sleep
        self.total_calls = 0
        self.providers: list[Provider] = []
        for p in cfg["providers"]:
            key = env.get(p["api_key_env"])
            if not p.get("enabled", True) or not key:
                continue
            models = list(p["models"])
            # Cost guard: only models matching the allow-pattern are ever called.
            allow = p.get("model_allow") or (r":free$" if p.get("free_only") else None)
            if allow:
                rx = re.compile(allow)
                blocked = [m for m in models if not rx.search(m)]
                if blocked:
                    log.warning("provider %s: ignoring models %s (not matching %s)", p["name"], blocked, allow)
                models = [m for m in models if rx.search(m)]
                if not models:
                    continue
            prov = Provider(
                name=p["name"],
                base_url=p["base_url"],
                api_key=key,
                models=models,
                json_mode=p.get("json_mode", True),
                rpm=p.get("rpm", 10),
                max_calls=p.get("max_calls_per_run", 50),
                model_params=p.get("model_params") or {},
                timeout=p.get("timeout", 60),
            )
            prov.client = client_factory(base_url=prov.base_url, api_key=key, timeout=prov.timeout, max_retries=0)
            self.providers.append(prov)
        self._next = 0

    @property
    def available(self) -> bool:
        return (self.total_calls < self.max_total and time.monotonic() < self.deadline
                and any(p.available for p in self.providers))

    def _rotation(self) -> list[Provider]:
        n = len(self.providers)
        order = [self.providers[(self._next + i) % n] for i in range(n)]
        self._next = (self._next + 1) % max(n, 1)
        return order

    def _call(self, p: Provider, messages: list[dict]) -> str:
        """One provider: per-model cooldowns for per-minute limits, model fallback
        for unknown models / exhausted daily quotas."""
        transient_retry = False
        waits = 0
        while p.available:
            if self.total_calls >= self.max_total:
                raise LLMUnavailable("run-wide LLM call cap reached")
            if time.monotonic() >= self.deadline:
                raise LLMUnavailable("LLM time budget used up")
            wait = p.pick_model()
            if wait > 0:
                if wait > MAX_COOLDOWN_WAIT or waits >= 4:
                    raise LLMUnavailable(f"{p.name}: all models cooling down")
                waits += 1
                self.sleep(wait)
            model = p.model
            kwargs: dict = {"model": model, "messages": messages, "temperature": self.temperature}
            if p.json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            if params := p.model_params.get(model):
                kwargs["extra_body"] = params
            p.throttle()
            p.calls += 1
            self.total_calls += 1
            try:
                resp = call_with_deadline(lambda kw=kwargs: p.client.chat.completions.create(**kw), p.timeout)
                if not getattr(resp, "choices", None):
                    # HTTP 200 without choices: some gateways (OpenRouter) report
                    # upstream failures this way.
                    p.cooldown[model] = time.monotonic() + SERVER_ERROR_COOLDOWN
                    detail = getattr(resp, "error", None) or getattr(resp, "model_extra", None) or ""
                    self._strike(p, f"{model}: empty response {str(detail)[:150]}")
                return resp.choices[0].message.content or ""
            except HardTimeout as e:
                # Slow/queued model: rest it and try a sibling model or provider.
                p.cooldown[model] = time.monotonic() + SERVER_ERROR_COOLDOWN
                self._strike(p, f"{model}: {e}")
            except openai.RateLimitError as e:
                # A rejected request does not spend quota, so it does not count
                # against the per-run caps.
                p.calls -= 1
                self.total_calls -= 1
                reset = _reset_seconds(e)
                if reset is not None and reset <= MAX_COOLDOWN_WAIT:
                    p.cooldown[model] = time.monotonic() + reset + 0.5
                    continue
                p.kill_model(model, f"rate limited: {str(e)[:200]}")
            except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
                p.exhausted = f"auth: {str(e)[:200]}"
            except openai.APIStatusError as e:
                if _is_model_error(e):
                    p.kill_model(model, str(e)[:200])
                    continue
                if isinstance(e, openai.BadRequestError) and p.json_mode and "response_format" in str(e):
                    p.json_mode = False
                    continue
                if isinstance(e, openai.BadRequestError) and any(
                    w in str(e) for w in ("json_validate_failed", "Failed to generate JSON")
                ):
                    p.errors.append(f"{model}: model produced invalid JSON")
                    raise InvalidOutput(f"{p.name}/{model}: invalid JSON generation") from e
                if e.status_code >= 500:
                    # Overloaded ("high demand") model: rest it and use another model.
                    p.cooldown[model] = time.monotonic() + SERVER_ERROR_COOLDOWN
                    if len(p.live_models) > 1 and not transient_retry:
                        transient_retry = True
                        continue
                    self._strike(p, f"{model}: HTTP {e.status_code}: {str(e)[:200]}")
                p.exhausted = f"HTTP {e.status_code}: {str(e)[:200]}"
            except (openai.APITimeoutError, openai.APIConnectionError) as e:
                if not transient_retry:
                    transient_retry = True
                    continue
                self._strike(p, f"connection: {str(e)[:200]}")
            if p.exhausted:
                log.warning("provider %s disabled for this run: %s", p.name, p.exhausted)
        raise LLMUnavailable(p.exhausted or f"{p.name}: call cap reached")

    @staticmethod
    def _strike(p: Provider, reason: str) -> None:
        """Transient failure: give up on this call; disable after 3 strikes."""
        p.strikes += 1
        p.errors.append(reason)
        if p.strikes >= 3:
            p.exhausted = f"repeated failures: {reason}"
        raise LLMUnavailable(reason)

    def complete_json(self, messages: list[dict], validate: Callable[[dict], T]) -> tuple[T, str]:
        """Return (validated result, 'provider/model'). Invalid output is retried once on
        the next provider; rate limits/errors move on until every provider is exhausted."""
        invalid = 0
        last_err: Exception | None = None
        for p in self._rotation():
            if not p.available:
                continue
            try:
                text = self._call(p, messages)
            except LLMUnavailable as e:
                last_err = e
                if self.total_calls >= self.max_total or time.monotonic() >= self.deadline:
                    break
                continue
            except InvalidOutput as e:
                last_err = e
                invalid += 1
                if invalid >= 2:
                    break
                continue
            try:
                return validate(parse_json(text)), f"{p.name}/{p.model}"
            except (InvalidOutput, ValidationError, TypeError, ValueError) as e:
                p.errors.append(f"invalid output: {str(e)[:120]}")
                last_err = e
                invalid += 1
                if invalid >= 2:
                    break
        raise LLMUnavailable(f"no provider produced valid output ({last_err})")

    def summary(self) -> list[dict]:
        return [
            {"name": p.name, "model": p.model, "calls": p.calls, "exhausted": p.exhausted,
             "dead_models": list(p.dead_models), "errors": p.errors[-3:]}
            for p in self.providers
        ]


# --------------------------------------------------------------------------
# Incident extraction prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = f"""Anda adalah analis pemantauan bencana dan insiden untuk Provinsi Jawa Barat, Indonesia.
Tugas: baca satu artikel berita dan kembalikan HANYA satu objek JSON (tanpa teks lain) dengan skema:

{{
  "is_incident": boolean,   // true HANYA jika artikel melaporkan satu KEJADIAN SPESIFIK yang nyata
                            // (lokasi tertentu, terjadi dalam ~3 hari sebelum tanggal terbit), termasuk
                            // berita lanjutan tentang kejadian itu (penanganan, korban, penyebab).
                            // false untuk: statistik/rekap periode ("catat 75 kejadian selama kemarau",
                            // "kasus melonjak 141 persen"), kondisi umum se-provinsi tanpa kejadian baru,
                            // simulasi, sosialisasi, imbauan/peringatan dini, prakiraan cuaca, analisis pakar,
                            // anggaran/program/evaluasi kebijakan (termasuk berita kebijakan MBG), hoaks yang
                            // dibantah, opini, kejadian di luar negeri, kasus bunuh diri, dan kriminalitas umum
                            // antar-orang dewasa (pencurian, perampokan, narkoba, penipuan, uang palsu,
                            // pembunuhan/penganiayaan biasa).
                            // TETAP dihitung sebagai insiden sosial: keracunan MBG, perundungan/bullying,
                            // kekerasan/pelecehan seksual, kekerasan terhadap anak/siswa/santri dan KDRT,
                            // tawuran/bentrok/kericuhan massa, intoleransi/persekusi/konflik sosial,
                            // TPPO dan penculikan. Kebakaran yang disengaja tetap dihitung sebagai kebakaran.
  "in_jabar": boolean,      // true jika lokasi kejadian di Provinsi Jawa Barat
  "category": one of {list(CATEGORIES)},
  "subcategory": string|null,       // mis. "karhutla", "miras oplosan", "jembatan putus", "banjir bandang"
  "kab_kota": string|null,          // nama kabupaten/kota Jawa Barat persis dari daftar yang diberikan
  "kecamatan": string|null,
  "desa": string|null,              // desa/kelurahan/kampung
  "event_time": string|null,        // waktu kejadian ISO 8601 bila disebut (zona WIB), jika tidak null
  "victims": {{"dead": int|null, "injured": int|null, "missing": int|null,
              "displaced": int|null, "affected": int|null, "houses": int|null}},
              // dead=meninggal, injured=luka/dirawat/sakit, missing=hilang, displaced=mengungsi,
              // affected=orang terdampak/keracunan (jika tidak termasuk kategori lain), houses=rumah/bangunan rusak/terendam
              // Gunakan null jika tidak disebutkan. Jangan menebak.
  "affected_entities": [{{"type": "sekolah|desa|pasar|pabrik|fasilitas_kesehatan|rumah_ibadah|kantor|jalan_jembatan|lahan|permukiman|dapur_mbg|lainnya", "name": string|null}}],
  "summary": string,                // 1-2 kalimat bahasa Indonesia: apa, di mana, kapan, dampak
  "confidence": number              // 0..1
}}

Kategori: banjir, longsor, gempa, angin (puting beliung/angin kencang), kebakaran (termasuk karhutla),
kekeringan (krisis air), gunung (erupsi), mbg (keracunan/makanan basi-berulat dari program Makan Bergizi
Gratis atau dapur SPPG), keracunan (makanan non-MBG, miras oplosan, gas), wabah (KLB/DBD/penyakit menular),
perundungan (bullying), kekerasan_seksual (pelecehan, pencabulan, pemerkosaan), kekerasan_anak (penganiayaan
anak/siswa/santri, KDRT), tawuran (tawuran pelajar, geng motor, perang sarung, bentrok/ricuh massa),
intoleransi (pembubaran ibadah, perusakan rumah ibadah, persekusi, konflik agraria/penggusuran),
tppo (perdagangan orang, PMI ilegal, penculikan), infrastruktur (jembatan/jalan/tanggul/bangunan runtuh,
gangguan listrik/air), kecelakaan, lainnya.

Privasi: pada "summary" dan "affected_entities" JANGAN tulis nama korban maupun pelaku kekerasan seksual,
perundungan, atau kekerasan anak/KDRT, dan jangan tulis nama anak di bawah umur. Nama lembaga (sekolah,
pesantren, SPPG) boleh."""


def build_messages(item: RawItem, body: str, hints: dict, kab_list: list[str], max_chars: int = 3000) -> list[dict]:
    text = (body or item.summary or "")[:max_chars]
    user = (
        f"Daftar kabupaten/kota Jawa Barat: {', '.join(kab_list)}\n\n"
        f"Petunjuk dari aturan otomatis (boleh salah): {json.dumps(hints, ensure_ascii=False)}\n\n"
        f"Sumber: {item.source_name}\n"
        f"Diterbitkan: {item.published.isoformat() if item.published else 'tidak diketahui'}\n"
        f"Judul: {item.title}\n"
        f"Isi:\n{text}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def extract(pool: ProviderPool, item: RawItem, body: str, hints: dict, kab_list: list[str], max_chars: int = 3000) -> tuple[Extraction, str]:
    return pool.complete_json(build_messages(item, body, hints, kab_list, max_chars), Extraction.model_validate)


# --------------------------------------------------------------------------
# llm-check
# --------------------------------------------------------------------------


def check(env: dict | None = None) -> list[dict]:
    """Send one tiny prompt to each configured provider; list models on failure."""
    cfg = load_yaml("llm_providers.yaml")
    env = os.environ if env is None else env
    results = []
    for pc in cfg["providers"]:
        row = {"name": pc["name"], "enabled": pc.get("enabled", True), "key": bool(env.get(pc["api_key_env"]))}
        if not row["key"]:
            row["status"] = f"skipped ({pc['api_key_env']} not set)"
            results.append(row)
            continue
        single = {**cfg, "providers": [{**pc, "enabled": True, "rpm": 600}]}
        pool = ProviderPool(single, env)
        try:
            out, used = pool.complete_json(
                [{"role": "user", "content": 'Balas hanya dengan JSON: {"ok": true, "kota": "ibu kota Jawa Barat"}'}],
                lambda d: d,
            )
            row["status"] = f"OK via {used}: {json.dumps(out, ensure_ascii=False)[:80]}"
        except LLMUnavailable as e:
            row["status"] = f"FAILED: {e}"
            row["errors"] = pool.providers[0].errors if pool.providers else []
            try:
                models = pool.providers[0].client.models.list()
                row["available_models"] = sorted(m.id for m in models.data)[:60]
            except Exception as me:  # listing is best effort
                row["available_models"] = f"could not list: {str(me)[:100]}"
        results.append(row)
    return results
