"""Rule fixes and the LLM merge pass, on a real 24h window (2026-10-23) where one
event was filed under two categories (child abuse), two regencies (a mountain on a
border, an arrest in another city) or only by a thin follow-up."""

import json
from pathlib import Path

import pytest

from recentdisaster import merge
from recentdisaster.cluster import cluster, is_advisory
from recentdisaster.llm import LLMUnavailable
from recentdisaster.models import Report

FIXTURE = Path(__file__).parent / "fixtures" / "reports_2026-10-23.json"


@pytest.fixture(scope="module")
def reports():
    return [Report.model_validate(r) for r in json.loads(FIXTURE.read_text())]


@pytest.fixture(scope="module")
def groups(reports):
    return cluster([r for r in reports if not is_advisory(r)])


def _owner(groups, fragment):
    [i] = {i for i, g in enumerate(groups) for r in g if fragment.lower() in r.item.title.lower()}
    return i


class FakePool:
    """Answers every pair with `same(id)`; records the prompts."""

    def __init__(self, same=lambda n: True, fail=False):
        self.same, self.fail, self.messages = same, fail, []
        self.available = True

    def complete_json(self, messages, validate, exclude=()):
        if self.fail:
            raise LLMUnavailable("down")
        self.messages.append(messages)
        ids = [p["id"] for p in json.loads(messages[1]["content"])]
        return validate({"verdicts": [{"id": n, "same": self.same(n)} for n in ids]}), "fake/model"


def test_one_event_filed_under_two_categories_and_regencies_is_one_cluster(groups):
    # kekerasan_seksual vs kekerasan_anak; crime in Bandung, arrest in Bekasi
    dokter = {_owner(groups, t) for t in (
        "Begini Kronologi Kejadian hingga Penangkapan Dokter",
        "Kasus Dokter Cabuli Anak Kandung Balita, Pelaku Ditangkap di Bekasi",
        "Polda Jabar Tangkap Oknum Dokter Terduga Pelaku Pencabulan Anak Kandung di Bekasi",
    )}
    assert len(dokter) == 1


def test_mountain_on_a_regency_border_is_one_cluster(groups):
    assert _owner(groups, "Karhutla Landa Gunung Ciremai") == _owner(groups, "Gubernur Jabar KDM Pimpin Pemadaman Karhutla Gunung Ciremai")


def test_widespread_words_do_not_join_different_regencies(groups):
    # both clusters are about "mbg" and "keracunan", but they are two poisonings
    assert _owner(groups, "270 Orang Diduga Keracunan MBG di Cirebon") != _owner(groups, "103 Siswa Diduga Keracunan MBG di Bogor")


def test_advisories_are_not_incidents(reports):
    advisory = [r.item.title for r in reports if is_advisory(r)]
    assert any("Ingatkan Warga Waspada Kebakaran" in t for t in advisory)
    assert not any("Ciremai" in t or "Dokter" in t for t in advisory)


def test_candidates_are_the_fragments_of_one_event(groups):
    pairs = {frozenset((_owner(groups, a), _owner(groups, b))) for a, b in [
        ("Karhutla Landa Gunung Ciremai", "Gunung Ciremai Cirebon Juga Terbakar"),
        ("Begini Kronologi Kejadian hingga Penangkapan Dokter", "Kondisi Korban Pencabulan Dokter di Bandung"),
    ]}
    found = {frozenset(p) for p in merge.candidates(groups)}
    assert pairs <= found
    assert len(found) <= merge.MAX_PAIRS


def test_refine_merges_what_the_llm_calls_one_event(groups):
    pool = FakePool()
    out, stats = merge.refine(groups, pool, {})
    assert stats["merged"] > 0 and len(out) < len(groups)
    assert sum(map(len, out)) == sum(map(len, groups))  # no report lost or duplicated
    assert _owner(out, "Karhutla Landa Gunung Ciremai") == _owner(out, "Gunung Ciremai Cirebon Juga Terbakar")


def test_refine_keeps_clusters_apart_when_the_llm_says_no(groups):
    out, stats = merge.refine(groups, FakePool(same=lambda n: False), {})
    assert out == [sorted(g, key=lambda r: r.time) for g in groups] and stats["merged"] == 0


def test_verdicts_are_cached_between_runs(groups):
    verdicts: dict = {}
    first, second = FakePool(), FakePool()
    a, _ = merge.refine(groups, first, verdicts)
    b, stats = merge.refine(groups, second, verdicts)
    assert first.messages and not second.messages and stats["asked"] == 0
    assert sorted(map(len, a)) == sorted(map(len, b))


def test_refine_survives_an_unavailable_llm(groups):
    out, stats = merge.refine(groups, FakePool(fail=True), {})
    assert len(out) == len(groups) and stats["failed"] == 1


def test_ignores_malformed_answers(groups):
    class Garbled(FakePool):
        def complete_json(self, messages, validate, exclude=()):
            return validate({"verdicts": [{"id": 999, "same": True}]}), "fake/model"

    out, _ = merge.refine(groups, Garbled(), {})
    assert len(out) == len(groups)
