"""Closed questions with a 3× majority vote: ties go to the cautious answer, agreement is recorded."""
import json

from eventbreaker.diagnosis import classify
from eventbreaker.diagnosis.classify import Label, check_interference, label_calls
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario, TestConfig

A = ConsumerAnalysis(className="C", methodName="on", eventType="Ev")
RACE = ChaosScenario(scenarioType="R", reason="r", expectedConcern="c", testConfig=TestConfig(callPattern="CONCURRENT"))


def replies(monkeypatch, *answers):
    """Answer i goes to seed i+1 (votes run in parallel, so call order is not fixed)."""
    seeds = []

    def chat(model, messages, purpose, valid=lambda r: True, fresh=False, seed=0, temperature=0):
        assert temperature > 0  # votes must be real samples, not copies of the greedy answer
        seeds.append(seed)
        return answers[seed - 1]
    monkeypatch.setattr(classify.llm, "chat", chat)
    return seeds


def lab(effect, impact="DATA"):
    return {"effect": effect, "impact": impact, "reason": effect.lower()}


def test_majority_label_and_agreement(monkeypatch):
    seeds = replies(monkeypatch, *[json.dumps({"repo.save": lab(e)}) for e in ("READ", "SIDE_EFFECT", "READ")])
    out = label_calls(A, "src", ["repo.save"])
    assert out["repo.save"] == Label("READ", "DATA", "read", "2/3")
    assert sorted(seeds) == [1, 2, 3]  # three separate samples


def test_tie_goes_to_the_cautious_label(monkeypatch):
    replies(monkeypatch, json.dumps({"m.x": lab("READ", "MINOR")}), json.dumps({"m.x": lab("SIDE_EFFECT", "MONEY")}),
            "not json")
    out = label_calls(A, "src", ["m.x"])
    assert (out["m.x"].effect, out["m.x"].impact) == ("SIDE_EFFECT", "MONEY")


def test_no_usable_vote_defaults_to_side_effect(monkeypatch):
    replies(monkeypatch, "{}", "nope", json.dumps({"m.x": {"effect": "WHATEVER"}}))
    assert label_calls(A, "src", ["m.x"])["m.x"] == Label(
        "SIDE_EFFECT", "DATA", "unclassified — treated as a side effect", "0/3")


def test_interference_majority_and_calls(monkeypatch):
    yes = json.dumps({"interferes": True, "calls": ["repo.find", "repo.save"], "reason": "lost update"})
    no = json.dumps({"interferes": False, "calls": [], "reason": "independent"})
    replies(monkeypatch, yes, no, yes, no, yes)
    i = check_interference(A, "src", RACE)
    assert i.interferes and i.calls == ["repo.find", "repo.save"] and i.agreement == "3/5"


def test_interference_no_majority(monkeypatch):
    no = json.dumps({"interferes": False, "calls": [], "reason": "independent"})
    yes = json.dumps({"interferes": True, "calls": ["a.b"], "reason": "x"})
    replies(monkeypatch, no, no, yes, no, yes)
    assert not check_interference(A, "src", RACE).interferes


def test_unusable_vote_is_asked_once_more(monkeypatch):
    """A reply that cannot be parsed is re-asked once, so it does not turn a majority into a tie."""
    no = json.dumps({"interferes": False, "calls": [], "reason": "independent"})
    yes = json.dumps({"interferes": True, "calls": ["a.b"], "reason": "x"})
    asked = {}

    def chat(model, messages, purpose, valid=lambda r: True, fresh=False, seed=0, temperature=0):
        asked[seed] = asked.get(seed, 0) + 1
        if seed == 1 and asked[seed] == 1:
            return "not json"
        return {1: no, 2: no, 3: yes, 4: yes, 5: no}[seed]
    monkeypatch.setattr(classify.llm, "chat", chat)
    i = check_interference(A, "src", RACE)
    assert asked[1] == 2 and not i.interferes and i.agreement == "3/5"


def test_refresh_is_a_label_and_ranks_between_side_effect_and_idempotent_write(monkeypatch):
    replies(monkeypatch, *[json.dumps({"t.refresh": lab(e)}) for e in ("REFRESH", "IDEMPOTENT_WRITE", "REFRESH")])
    assert label_calls(A, "src", ["t.refresh"])["t.refresh"].effect == "REFRESH"
    replies(monkeypatch, *[json.dumps({"t.refresh": lab(e)}) for e in ("REFRESH", "IDEMPOTENT_WRITE", "nope")])
    assert label_calls(A, "src", ["t.refresh"])["t.refresh"].effect == "REFRESH"  # tie → more cautious
