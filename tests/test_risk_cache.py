"""Risk-identification response cache: hit, miss on prompt change, --fresh, bad responses not cached."""
import json
from types import SimpleNamespace

import pytest

from eventbreaker.agent import nemotron
from eventbreaker.models.consumer import ConsumerAnalysis

ANALYSIS = ConsumerAnalysis(className="C", methodName="consume", eventType="E")
GOOD = json.dumps([{
    "scenarioType": "DUPLICATE_DELIVERY", "reason": "r", "expectedConcern": "c",
    "testConfig": {"callPattern": "DUPLICATE", "faultInjections": []},
}])


@pytest.fixture
def fake_api(monkeypatch, tmp_path):
    """Patch the OpenAI client; return the list of prompts sent and a settable reply."""
    monkeypatch.setenv("NEBIUS_API_KEY", "test")
    monkeypatch.setattr(nemotron, "_CACHE_DIR", tmp_path)
    state = SimpleNamespace(calls=0, reply=GOOD)

    def create(**_):
        state.calls += 1
        msg = SimpleNamespace(content=state.reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(nemotron, "OpenAI", lambda **_: client)
    return state


def test_second_call_uses_cache(fake_api):
    first, cached1 = nemotron.identify_risks(ANALYSIS, "class C {}")
    second, cached2 = nemotron.identify_risks(ANALYSIS, "class C {}")
    assert (cached1, cached2) == (False, True)
    assert fake_api.calls == 1
    assert first == second


def test_source_change_misses_cache(fake_api):
    nemotron.identify_risks(ANALYSIS, "class C {}")
    _, cached = nemotron.identify_risks(ANALYSIS, "class C { int x; }")
    assert not cached
    assert fake_api.calls == 2


def test_fresh_bypasses_and_refreshes_cache(fake_api):
    nemotron.identify_risks(ANALYSIS, "class C {}")
    _, cached = nemotron.identify_risks(ANALYSIS, "class C {}", fresh=True)
    assert not cached
    assert fake_api.calls == 2


def test_unparseable_response_not_cached(fake_api):
    fake_api.reply = "not json"
    with pytest.raises(ValueError):
        nemotron.identify_risks(ANALYSIS, "class C {}")
    fake_api.reply = GOOD
    _, cached = nemotron.identify_risks(ANALYSIS, "class C {}")
    assert not cached
    assert fake_api.calls == 2
