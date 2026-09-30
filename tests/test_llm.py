import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from recentdisaster.llm import LLMUnavailable, ProviderPool, parse_json
from recentdisaster.models import Extraction

VALID = json.dumps({"is_incident": True, "in_jabar": True, "category": "banjir", "kab_kota": "Kabupaten Garut",
                    "victims": {"dead": "2 orang"}, "affected_entities": ["desa"], "summary": "Banjir di Garut."})


def _err(cls, status, headers=None):
    resp = httpx.Response(status, headers=headers or {}, request=httpx.Request("POST", "https://x/v1/chat/completions"))
    return cls(f"error {status}", response=resp, body=None)


class FakeClient:
    """Scripted responses per provider: str -> content, Exception -> raised."""

    script: dict[str, list] = {}
    calls: list[tuple[str, str, dict]] = []

    def __init__(self, base_url, api_key, **_):
        self.base_url = base_url
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        FakeClient.calls.append((self.base_url, kw["model"], kw))
        step = FakeClient.script[self.base_url].pop(0)
        if isinstance(step, Exception):
            raise step
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=step))])


def make_pool(script, providers=("a", "b"), **cfg):
    FakeClient.script = {f"https://{p}": list(s) for p, s in script.items()}
    FakeClient.calls = []
    conf = {
        "max_llm_calls_per_run": cfg.get("max_total", 50),
        "providers": [
            {"name": p, "base_url": f"https://{p}", "api_key_env": f"{p.upper()}_KEY",
             "models": cfg.get("models", ["m1", "m2"]), "rpm": 100000, "max_calls_per_run": 10}
            for p in providers
        ],
    }
    env = {f"{p.upper()}_KEY": "k" for p in providers}
    return ProviderPool(conf, env=env, client_factory=FakeClient, sleep=lambda s: None)


def test_parse_json_lenient():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Berikut hasilnya: {"a": {"b": 2}} semoga membantu') == {"a": {"b": 2}}


def test_valid_and_coerced():
    pool = make_pool({"a": [VALID]})
    ext, used = pool.complete_json([], Extraction.model_validate)
    assert used == "a/m1"
    assert ext.victims.dead == 2
    assert ext.affected_entities[0].type == "desa"


def test_round_robin():
    pool = make_pool({"a": [VALID, VALID], "b": [VALID, VALID]})
    used = [pool.complete_json([], Extraction.model_validate)[1] for _ in range(4)]
    assert used == ["a/m1", "b/m1", "a/m1", "b/m1"]


def test_quota_429_retires_models_then_provider():
    rl = lambda: _err(openai.RateLimitError, 429)  # noqa: E731  (no reset info = quota gone)
    pool = make_pool({"a": [rl(), rl()], "b": [VALID, VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"
    assert pool.providers[0].exhausted and set(pool.providers[0].dead_models) == {"m1", "m2"}
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"


def test_per_minute_limit_switches_to_other_model():
    pool = make_pool({"a": [_err(openai.RateLimitError, 429, {"retry-after": "30"}), VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/m2"
    assert pool.providers[0].exhausted is None and not pool.providers[0].dead_models


def test_per_minute_limit_waits_when_single_model():
    slept = []
    pool = make_pool({"a": [_err(openai.RateLimitError, 429, {"x-ratelimit-reset-tokens": "1m2.5s"}), VALID]},
                     providers=("a",), models=["only"])
    pool.sleep = slept.append
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/only"
    assert slept and 60 < slept[0] <= 64


def test_daily_limit_message_retires_model_even_with_reset_header():
    err = openai.RateLimitError("Rate limit reached: requests per day (RPD)",
                                response=httpx.Response(429, headers={"retry-after": "5"},
                                                        request=httpx.Request("POST", "https://a")), body=None)
    pool = make_pool({"a": [err, VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/m2"
    assert "m1" in pool.providers[0].dead_models


def test_model_params_sent_as_extra_body():
    pool = make_pool({"a": [VALID]}, providers=("a",))
    pool.providers[0].model_params = {"m1": {"reasoning_effort": "low"}}
    pool.complete_json([], Extraction.model_validate)
    assert FakeClient.calls[-1][2]["extra_body"] == {"reasoning_effort": "low"}


def test_unknown_model_falls_through_model_list():
    pool = make_pool({"a": [_err(openai.NotFoundError, 404), VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/m2"


def test_invalid_output_retried_once_on_next_provider():
    pool = make_pool({"a": ["not json at all"], "b": [VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"


def test_invalid_twice_gives_up():
    pool = make_pool({"a": ['{"foo": 1}'], "b": ["garbage"]}, providers=("a", "b"))
    with pytest.raises(LLMUnavailable):
        pool.complete_json([], Extraction.model_validate)


def test_all_exhausted():
    pool = make_pool({"a": [_err(openai.AuthenticationError, 401)],
                      "b": [_err(openai.RateLimitError, 429), _err(openai.RateLimitError, 429)]})
    with pytest.raises(LLMUnavailable):
        pool.complete_json([], Extraction.model_validate)
    assert not pool.available


def test_overloaded_model_switches_to_sibling_model():
    pool = make_pool({"a": [_err(openai.InternalServerError, 503), VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/m2"
    assert pool.providers[0].exhausted is None and pool.providers[0].strikes == 0


def test_all_models_overloaded_is_a_strike_not_a_disable():
    pool = make_pool({"a": [_err(openai.InternalServerError, 503), _err(openai.InternalServerError, 503)],
                      "b": [VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"
    assert pool.providers[0].exhausted is None and pool.providers[0].strikes == 1


def test_json_generation_failure_moves_on_without_disabling():
    err = openai.BadRequestError("Error code: 400 - json_validate_failed", body=None,
                                 response=httpx.Response(400, request=httpx.Request("POST", "https://a")))
    pool = make_pool({"a": [err], "b": [VALID]})
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"
    assert pool.providers[0].exhausted is None


def test_free_only_drops_paid_models():
    conf = {"providers": [{"name": "or", "base_url": "https://or", "api_key_env": "K", "free_only": True,
                           "models": ["paid/model", "x/y:free"]}]}
    pool = ProviderPool(conf, env={"K": "k"}, client_factory=FakeClient)
    assert pool.providers[0].models == ["x/y:free"]
    conf["providers"][0]["models"] = ["paid/model"]
    assert ProviderPool(conf, env={"K": "k"}, client_factory=FakeClient).providers == []


def test_json_mode_dropped_when_unsupported():
    pool = make_pool({"a": [_err(openai.BadRequestError, 400), VALID]}, providers=("a",))
    pool.providers[0].json_mode = True
    FakeClient.script["https://a"][0] = openai.BadRequestError(
        "response_format not supported", response=httpx.Response(400, request=httpx.Request("POST", "https://a")), body=None)
    assert pool.complete_json([], Extraction.model_validate)[1] == "a/m1"
    assert "response_format" not in FakeClient.calls[-1][2]


def test_unconfigured_providers_are_skipped():
    conf = {"providers": [{"name": "x", "base_url": "https://x", "api_key_env": "NOPE", "models": ["m"]}]}
    pool = ProviderPool(conf, env={}, client_factory=FakeClient)
    assert pool.providers == [] and not pool.available


def test_model_allow_pattern_blocks_paid_tiers():
    conf = {"providers": [{"name": "glm", "base_url": "https://g", "api_key_env": "K", "model_allow": r"-flash$",
                           "models": ["glm-4.7-flash", "glm-5.3-flashx", "glm-5"]}]}
    assert ProviderPool(conf, env={"K": "k"}, client_factory=FakeClient).providers[0].models == ["glm-4.7-flash"]


def test_time_budget_stops_llm_use():
    pool = make_pool({"a": [VALID]})
    pool.deadline = 0  # already past
    assert not pool.available
    with pytest.raises(LLMUnavailable):
        pool.complete_json([], Extraction.model_validate)


def test_hard_deadline_abandons_trickling_request():
    import time as _t

    class Slow(FakeClient):
        def _create(self, **kw):
            if self.base_url == "https://a":
                _t.sleep(5)  # simulates a server trickling keep-alive bytes
            return super()._create(**kw)

    FakeClient.script = {"https://a": [VALID], "https://b": [VALID]}
    conf = {"providers": [
        {"name": p, "base_url": f"https://{p}", "api_key_env": "K", "models": ["m1"], "rpm": 100000, "timeout": 0.3}
        for p in ("a", "b")]}
    pool = ProviderPool(conf, env={"K": "k"}, client_factory=Slow, sleep=lambda s: None)
    start = _t.monotonic()
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"
    assert _t.monotonic() - start < 2
    assert pool.providers[0].strikes == 1


def test_empty_choices_is_transient_and_moves_on():
    class Empty(FakeClient):
        def _create(self, **kw):
            if self.base_url == "https://a":
                FakeClient.calls.append((self.base_url, kw["model"], kw))
                return SimpleNamespace(choices=None, error={"message": "upstream error"})
            return super()._create(**kw)

    FakeClient.script = {"https://b": [VALID]}
    conf = {"providers": [{"name": p, "base_url": f"https://{p}", "api_key_env": "K", "models": ["m1"], "rpm": 100000}
                          for p in ("a", "b")]}
    pool = ProviderPool(conf, env={"K": "k"}, client_factory=Empty, sleep=lambda s: None)
    assert pool.complete_json([], Extraction.model_validate)[1] == "b/m1"
    assert pool.providers[0].strikes == 1 and pool.providers[0].exhausted is None
