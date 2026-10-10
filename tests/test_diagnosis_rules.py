"""Deterministic parts of diagnosis: the gates before the LLM call and response parsing."""
import json

import pytest

from eventbreaker.diagnosis import analyzer
from eventbreaker.diagnosis.analyzer import _failed_before_any_completed_call, _parse, diagnose
from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo, MethodCallInfo
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario

SCENARIO = ChaosScenario(scenarioType="DOWNSTREAM_FAILURE", reason="r", expectedConcern="c")


def result(*obs: E, threw: bool = False) -> ObservationResult:
    return ObservationResult(scenario="X", observations=list(obs), consumerThrew=threw)


def finding(method: str, severity: str = "HIGH", summary: str | None = None) -> dict:
    return {
        "summary": summary if summary is not None else f"{method} was observed",
        "explanation": "e",
        "affectedMethod": method,
        "suggestedFix": "f",
        "severity": severity,
    }


# ── Call-order rule ──────────────────────────────────────────────────────────

class TestFailedBeforeAnyCompletedCall:
    def test_first_call_threw_and_propagated(self):
        assert _failed_before_any_completed_call(
            result(E(target="repo.find", callCount=1, threw=True), threw=True)
        )

    def test_no_dependency_calls_and_propagated(self):
        assert _failed_before_any_completed_call(result(threw=True))

    def test_a_call_completed_before_the_failure(self):
        assert not _failed_before_any_completed_call(result(
            E(target="repo.find", callCount=1),
            E(target="cache.put", callCount=1, threw=True),
            threw=True,
        ))

    def test_swallowed_exception_is_not_exempt(self):
        # The event is acknowledged — the work is lost, so this must reach diagnosis.
        assert not _failed_before_any_completed_call(
            result(E(target="repo.find", callCount=1, threw=True), threw=False)
        )

    def test_uncalled_entries_do_not_count_as_completed(self):
        assert _failed_before_any_completed_call(result(
            E(target="repo.find", callCount=1, threw=True),
            E(target="cache.put", callCount=0),
            threw=True,
        ))


class TestDiagnoseGates:
    """diagnose() must return [] without touching the API for gated results."""

    @pytest.fixture
    def analysis(self):
        return ConsumerAnalysis(
            className="C", methodName="handle", eventType="Ev",
            dependencies=[DependencyInfo(name="repo", type="Repo")],
            methodCalls=[MethodCallInfo(expression="repo.find(x)", scope="repo", methodName="find")],
        )

    @pytest.fixture(autouse=True)
    def no_api(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("diagnose() called the API for a gated result")
        monkeypatch.setattr(analyzer, "OpenAI", boom)
        monkeypatch.setenv("NEBIUS_API_KEY", "test")

    def test_failure_before_any_completed_call(self, analysis):
        r = result(E(target="repo.find", callCount=1, threw=True), threw=True)
        assert diagnose(analysis, SCENARIO, r, "src") == []

    def test_no_signal_and_nothing_skipped(self, analysis):
        r = result(E(target="repo.find", callCount=1))
        assert diagnose(analysis, SCENARIO, r, "src") == []


# ── Response parsing ─────────────────────────────────────────────────────────

class TestParse:
    R = result(E(target="repo.find", callCount=2), E(target="payment.charge", callCount=2))

    def test_none_severity_is_dropped(self):
        raw = json.dumps([finding("repo.find", "NONE"), finding("payment.charge", "HIGH")])
        assert [f.affectedMethod for f in _parse(raw, SCENARIO, self.R)] == ["payment.charge"]

    def test_none_is_case_insensitive(self):
        assert _parse(json.dumps([finding("repo.find", "none")]), SCENARIO, self.R) == []

    def test_all_none_returns_empty_not_error(self):
        raw = json.dumps([finding("repo.find", "NONE"), finding("payment.charge", "NONE")])
        assert _parse(raw, SCENARIO, self.R) == []

    def test_empty_array_still_raises(self):
        with pytest.raises(ValueError):
            _parse("[]", SCENARIO, self.R)

    def test_markdown_fences_and_single_object(self):
        raw = "```json\n" + json.dumps(finding("payment.charge")) + "\n```"
        assert [f.affectedMethod for f in _parse(raw, SCENARIO, self.R)] == ["payment.charge"]

    def test_affected_method_corrected_from_summary(self):
        f = finding("repo.find", summary="payment.charge ran twice")
        assert _parse(json.dumps([f]), SCENARIO, self.R)[0].affectedMethod == "payment.charge"

    def test_affected_method_kept_when_summary_names_two(self):
        f = finding("repo.find", summary="repo.find and payment.charge both ran twice")
        assert _parse(json.dumps([f]), SCENARIO, self.R)[0].affectedMethod == "repo.find"

    def test_scenario_defaults_to_scenario_type(self):
        assert _parse(json.dumps([finding("payment.charge")]), SCENARIO, self.R)[0].scenario \
            == "DOWNSTREAM_FAILURE"
