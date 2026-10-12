"""Diagnosis plumbing: what was measured, transport, and how diagnose() combines the rubric with the
text Nemotron writes (the model never decides severity)."""
import json

import pytest

from eventbreaker.diagnosis import analyzer, classify
from eventbreaker.diagnosis.analyzer import Change, diagnose, measured_changes, transport
from eventbreaker.diagnosis.classify import Interference, Label
from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig


def result(*obs: E, threw: bool = False) -> ObservationResult:
    return ObservationResult(scenario="X", observations=list(obs), consumerThrew=threw)


def _sc(pattern="SINGLE", faults=()):
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c",
                         testConfig=TestConfig(callPattern=pattern, faultInjections=list(faults)))


def listener(annotation="KafkaListener"):
    return ConsumerAnalysis(className="C", methodName="handle", eventType="Ev", listenerAnnotation=annotation,
                            dependencies=[DependencyInfo(name="repo", type="Repo")])


# ── What was measured ────────────────────────────────────────────────────────

class TestMeasuredChanges:
    BASE = result(E(target="api.get", callCount=1), E(target="bus.post", callCount=3))

    def test_missing_extra_and_threw_against_baseline(self):
        run = result(E(target="api.get", callCount=1, threw=True), E(target="bus.post", callCount=1))
        assert measured_changes(run, self.BASE) == [
            Change("bus.post", "MISSING", 3, 1), Change("api.get", "THREW", None, 1)]

    def test_calls_absent_from_one_side_count_as_zero(self):
        run = result(E(target="api.get", callCount=1), E(target="bus.post", callCount=3), E(target="log.x", callCount=2))
        assert measured_changes(run, self.BASE) == [Change("log.x", "EXTRA", 0, 2)]

    def test_without_baseline_repeated_calls_are_extra(self):
        assert measured_changes(result(E(target="a.b", callCount=2), E(target="c.d", callCount=1)), None) \
            == [Change("a.b", "EXTRA", None, 2)]

    def test_describe(self):
        assert Change("bus.post", "MISSING", 3, 1).describe() == "bus.post: called 1× (baseline 3×) — missing"
        assert Change("api.get", "THREW", None, 1).describe() == "api.get threw"


class TestTransport:
    @pytest.mark.parametrize("annotation, expected", [
        ("KafkaListener", "broker"), ("RabbitListener", "broker"), ("SqsListener", "broker"),
        ("EventListener", "in-process"), ("TransactionalEventListener", "in-process"),
        ("Subscribe", "event-bus"), ("", "unknown"),
    ])
    def test_from_annotation(self, annotation, expected):
        assert transport(listener(annotation)) == expected


# ── diagnose(): rubric decides, the model only writes ────────────────────────

LABELS = {"repo.save": Label("SIDE_EFFECT", "DATA", "inserts", "3/3"), "bus.post": Label("SIDE_EFFECT", "EXTERNAL_MESSAGE")}
BASE = result(E(target="repo.save", callCount=1), E(target="bus.post", callCount=1))
SWALLOWED = result(E(target="repo.save", callCount=1, threw=True), E(target="bus.post", callCount=0))
FAULT = _sc("SINGLE", [FaultInjection(dep="repo", method="save", fault="THROW")])


class FakeChat:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, model, messages, purpose, valid=lambda r: True, fresh=False, seed=0):
        self.calls.append(messages)
        return self.replies.pop(0)


TEXT = json.dumps([{"summary": "S", "explanation": "E", "suggestedFix": "F", "severity": "LOW"}])


def test_no_finding_means_no_model_call(monkeypatch):
    monkeypatch.setattr(analyzer.llm, "chat", FakeChat())  # any call would fail
    d = diagnose(listener(), FAULT, BASE, "src", LABELS, baseline=BASE)
    assert d.findings == [] and d.harmless == []


def test_severity_comes_from_the_rubric_not_the_model(monkeypatch):
    fake = FakeChat(TEXT)
    monkeypatch.setattr(analyzer.llm, "chat", fake)
    [f] = diagnose(listener(), FAULT, SWALLOWED, "src", LABELS, baseline=BASE).findings
    assert f.severity == "HIGH"  # the model said LOW; ignored
    assert (f.summary, f.explanation, f.suggestedFix) == ("S", "E", "F")
    assert f.severityNote.startswith("The listener did not throw")
    assert f.evidence[-1].startswith("the listener did NOT throw")
    assert "severity HIGH on repo.save" in fake.calls[0][1]["content"]
    assert "repo.save: SIDE_EFFECT, impact DATA (3/3 votes) — inserts" in fake.calls[0][1]["content"]


def test_unusable_text_falls_back_to_the_measured_facts(monkeypatch):
    monkeypatch.setattr(analyzer.llm, "chat", FakeChat("no json"))
    [f] = diagnose(listener(), FAULT, SWALLOWED, "src", LABELS, baseline=BASE).findings
    assert f.severity == "HIGH" and "bus.post" in f.summary


def test_interference_is_asked_only_for_concurrent_scenarios(monkeypatch):
    asked = []
    monkeypatch.setattr(analyzer, "check_interference",
                        lambda *a, **k: asked.append(1) or Interference(False, [], "", "3/3"))
    monkeypatch.setattr(analyzer.llm, "chat", FakeChat(TEXT, TEXT))
    double = result(E(target="repo.save", callCount=2), E(target="bus.post", callCount=2))
    diagnose(listener(), _sc("DUPLICATE"), double, "src", LABELS, baseline=BASE)
    assert asked == []
    diagnose(listener(), _sc("CONCURRENT"), double, "src", LABELS, baseline=BASE)
    assert asked == [1]


def test_classify_module_is_used_for_interference():
    assert analyzer.check_interference is classify.check_interference
