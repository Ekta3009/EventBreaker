"""Closed questions with a 3× majority vote: ties go to the cautious answer, agreement is recorded."""
import json

from eventbreaker.diagnosis import classify
from eventbreaker.diagnosis.classify import Label, check_interference, label_calls
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario, TestConfig

A = ConsumerAnalysis(className="C", methodName="on", eventType="Ev")
RACE = ChaosScenario(scenarioType="R", reason="r", expectedConcern="c", testConfig=TestConfig(callPattern="CONCURRENT"))


def replies(monkeypatch, *answers):
    seeds = []

    def chat(model, messages, purpose, valid=lambda r: True, fresh=False, seed=0):
        seeds.append(seed)
        return answers[len(seeds) - 1]
    monkeypatch.setattr(classify.llm, "chat", chat)
    return seeds


def lab(effect, impact="DATA"):
    return {"effect": effect, "impact": impact, "reason": effect.lower()}


def test_majority_label_and_agreement(monkeypatch):
    seeds = replies(monkeypatch, *[json.dumps({"repo.save": lab(e)}) for e in ("READ", "SIDE_EFFECT", "READ")])
    out = label_calls(A, "src", ["repo.save"])
    assert out["repo.save"] == Label("READ", "DATA", "read", "2/3")
    assert seeds == [1, 2, 3]  # three separate samples


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
    replies(monkeypatch, yes, no, yes)
    i = check_interference(A, "src", RACE)
    assert i.interferes and i.calls == ["repo.find", "repo.save"] and i.agreement == "2/3"


def test_interference_no_majority(monkeypatch):
    no = json.dumps({"interferes": False, "calls": [], "reason": "independent"})
    replies(monkeypatch, no, no, json.dumps({"interferes": True, "calls": ["a.b"], "reason": "x"}))
    assert not check_interference(A, "src", RACE).interferes
