import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from recentdisaster import analysis
from recentdisaster.analysis import AnalysisExtraction, Store
from recentdisaster.config import load_yaml
from recentdisaster.llm import LLMUnavailable

BODY = (
    "Kekeringan melanda sejumlah wilayah di Jawa Barat sejak Agustus. Pakar hidrologi UGM, Dr. Budi Santoso, "
    "menduga ada kerusakan sistem daerah aliran sungai (DAS) di hulu. \"Tutupan hutan di hulu DAS Cimanuk dan "
    "Citarum terus berkurang sehingga air hujan tidak lagi tertahan,\" kata Budi di Yogyakarta, Senin. "
    "Menurut dia, Kabupaten Indramayu dan Kabupaten Garut termasuk yang paling terdampak. Ia menyarankan "
    "pemulihan vegetasi di hulu dan pembangunan embung. Di Jawa Barat, kekeringan tahun ini lebih panjang. "
) * 2


@pytest.fixture
def cfg():
    return load_yaml("analysis.yaml")


def _ext(**kw):
    base = dict(is_analysis=True, jabar_relevant=True, category="kekeringan", kind="pendapat_pakar",
                experts=[{"name": "Dr. Budi Santoso", "affiliation": "UGM"}],
                kab_kota=["Indramayu", "Kabupaten Garut", "Kota Bekasi"],
                finding="Kekeringan di Jawa Barat diduga terkait kerusakan DAS di hulu.",
                evidence="Tutupan hutan di hulu DAS Cimanuk dan Citarum terus berkurang sehingga air hujan tidak lagi tertahan",
                confidence=0.9)
    return AnalysisExtraction.model_validate({**base, **kw})


# ------------------------------------------------------------------ screening


@pytest.mark.parametrize("title,source,expected", [
    ("Jawa Barat Dilanda Kekeringan, Pakar UGM Duga Ada Kerusakan Sistem DAS", "gnews-analisis", None),
    ("Pakar ITB: Sesar Lembang Berpotensi Picu Gempa Besar", "gnews-analisis", None),
    ("Banjir rendam 100 rumah di Garut", "detik-jabar", "no expert marker"),
    ("Wisuda IPB: peneliti muda bahas banjir", "ipb", "excluded title"),
    ("Pakar: banjir Jakarta akibat drainase buruk", "kompas", "not in Jabar"),
])
def test_screen(make_item, title, source, expected):
    item = make_item(title, jabar_only=False)
    item.source_id = source
    assert analysis.screen(item) == expected


def test_capture_skips_known_and_syndicated(make_item, now):
    store = Store()
    a = make_item("Pakar UGM: Kekeringan di Jawa Barat akibat kerusakan DAS")
    b = make_item("Pakar UGM: Kekeringan di Jawa Barat akibat kerusakan DAS", url="https://other.id/x")
    c = make_item("Harga cabai naik di Bandung")
    assert analysis.capture(store, [a, c], now) == 1
    assert analysis.capture(store, [a, b], now + timedelta(hours=6)) == 0


# ------------------------------------------------------------------ grounding


def test_jabar_focus():
    assert analysis.jabar_focus("Pakar: kekeringan di Indramayu", "")
    assert analysis.jabar_focus("Pakar soroti kekeringan", BODY)
    passing = "Asap terdeteksi di Jakarta, Banten, dan Jawa Barat. Pakar menjelaskan angin di Kalimantan."
    assert not analysis.jabar_focus("Asap karhutla sampai Jakarta, ini kata pakar", passing)


def test_ground_accepts_and_cleans(cfg):
    ext, reason = analysis.ground(_ext(), "Pakar UGM soroti kekeringan Jabar", BODY, cfg)
    assert reason == "ok"
    assert ext.kab_kota == ["Kabupaten Indramayu", "Kabupaten Garut"]  # Bekasi is not in the text
    assert ext.experts[0].name == "Dr. Budi Santoso"


@pytest.mark.parametrize("override,reason", [
    ({"evidence": "Pemerintah harus segera membangun bendungan raksasa di setiap desa."}, "evidence quote not found in article"),
    ({"experts": [{"name": "Prof. Siti Rahma"}]}, "no expert name found in article"),
    ({"category": "gempa"}, "category gempa not in text"),
    ({"is_analysis": False}, "not analysis"),
    ({"jabar_relevant": False}, "not about Jabar"),
    ({"confidence": 0.5}, "low confidence 0.5"),
])
def test_ground_rejects(cfg, override, reason):
    ext, why = analysis.ground(_ext(**override), "Pakar UGM soroti kekeringan Jabar", BODY, cfg)
    assert ext is None and why == reason


def test_institutional_study_needs_no_person(cfg):
    ext, reason = analysis.ground(_ext(kind="kajian_lembaga", experts=[]), "Kajian kekeringan Jabar", BODY, cfg)
    assert reason == "ok" and ext.experts == []


# ------------------------------------------------------------------ verification


class FakePool:
    """Two providers; answers are queued per prompt kind."""

    def __init__(self, extractions, checks, providers=("a", "b")):
        self.providers = [SimpleNamespace(name=n, available=True) for n in providers]
        self.extractions, self.checks = list(extractions), list(checks)
        self.calls = []

    @property
    def available(self):
        return True

    def complete_json(self, messages, validate, exclude=()):
        kind = "check" if messages[0]["content"] == analysis.CHECK_PROMPT else "extract"
        name = next(p.name for p in self.providers if p.name not in exclude)
        self.calls.append((kind, name))
        queue = self.checks if kind == "check" else self.extractions
        if not queue:
            raise LLMUnavailable("empty")
        return validate(queue.pop(0)), f"{name}/model"

    def summary(self):
        return []


def _store(make_item, now, *titles):
    store = Store()
    for n, t in enumerate(titles):
        it = make_item(t, url=f"https://ex.id/{n}", published=now - timedelta(days=n + 1))
        store.records[f"https://ex.id/{n}"] = analysis.Record(item=it, first_seen=now)
    return store


def _read(body=BODY):
    return lambda item, cfg: (item.url, body, None)


OK_CHECK = {"supported": True, "is_analysis": True, "jabar_relevant": True, "category_ok": True, "reason": "ok"}


def test_verify_accepts_with_independent_second_opinion(make_item, now, cfg):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar")
    pool = FakePool([_ext().model_dump()], [OK_CHECK])
    counts = analysis.verify(store, pool, now, cfg, read=_read())
    assert counts["accepted"] == 1
    assert pool.calls == [("extract", "a"), ("check", "b")]  # different providers
    entry = next(iter(store.records.values())).entry
    assert entry["verified_by"] == ["a/model", "b/model"] and entry["category"] == "kekeringan"


def test_verify_rejects_when_cross_check_disagrees(make_item, now, cfg):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar")
    pool = FakePool([_ext().model_dump()], [{**OK_CHECK, "supported": False, "reason": "angka tidak ada"}])
    analysis.verify(store, pool, now, cfg, read=_read())
    rec = next(iter(store.records.values()))
    assert rec.status == "rejected" and rec.reason.startswith("cross-check failed")


def test_verify_drops_duplicate_story(make_item, now, cfg):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar", "Kekeringan Jabar, pakar UGM soroti DAS")
    pool = FakePool([_ext().model_dump(), _ext().model_dump()], [OK_CHECK, OK_CHECK])
    counts = analysis.verify(store, pool, now, cfg, read=_read())
    assert counts["accepted"] == 1 and counts["rejected"] == 1
    assert any(r.reason and r.reason.startswith("duplicate of") for r in store.records.values())


def test_verify_waits_for_two_providers(make_item, now, cfg):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar")
    pool = FakePool([_ext().model_dump()], [OK_CHECK], providers=("a",))
    counts = analysis.verify(store, pool, now, cfg, read=_read())
    assert counts["deferred"] == 1 and pool.calls == []
    assert next(iter(store.records.values())).status == "pending"


def test_unreadable_article_is_retried_then_dropped(make_item, now, cfg):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar")
    pool = FakePool([], [])
    for _ in range(cfg["max_attempts"]):
        analysis.verify(store, pool, now, cfg, read=_read(""))
    rec = next(iter(store.records.values()))
    assert rec.status == "rejected" and rec.reason == "article text not readable" and pool.calls == []


def test_run_publishes_and_expires_after_retention(make_item, now, cfg, tmp_path, monkeypatch):
    store = _store(make_item, now, "Pakar UGM soroti kekeringan Jabar")
    cache, out = tmp_path / "analysis-cache.json", tmp_path / "analysis.json"
    store.save(cache)
    monkeypatch.setattr(analysis.llm, "ProviderPool", lambda: FakePool([_ext().model_dump()], [OK_CHECK]))
    monkeypatch.setattr(analysis, "_article_text", lambda item, cfg: (item.url, BODY, None))
    analysis.run(fetch=False, now=now, cache_path=cache, output_path=out)
    data = json.loads(out.read_text())
    assert [e["category"] for e in data["entries"]] == ["kekeringan"] and data["retention_days"] == 30
    # 30 days after publication it is gone from the page and the cache.
    analysis.run(fetch=False, use_llm=False, now=now + timedelta(days=cfg["retention_days"]), cache_path=cache, output_path=out)
    assert json.loads(out.read_text())["entries"] == []
    assert json.loads(cache.read_text())["records"] == {}


def test_pool_exclude_skips_named_provider():
    from recentdisaster.llm import ProviderPool

    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kw):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    cfg = {"providers": [{"name": n, "base_url": "http://x", "api_key_env": "K", "models": ["m"], "rpm": 6000}
                         for n in ("a", "b")]}
    pool = ProviderPool(cfg, env={"K": "k"}, client_factory=Client, sleep=lambda s: None)
    _, used = pool.complete_json([{"role": "user", "content": "x"}], lambda d: d, exclude={"a"})
    assert used == "b/m"
