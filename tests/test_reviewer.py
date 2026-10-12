"""Reviewer (Nemotron Ultra): applying verdicts, and code rules that the reviewer cannot undo."""
import json

from eventbreaker.diagnosis import reviewer
from eventbreaker.diagnosis.reviewer import apply_verdict, review
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ReliabilityFinding


def f(method="repo.save", severity="HIGH", kind="failure", pattern="SINGLE", summary="s"):
    return ReliabilityFinding(scenario="X", summary=summary, explanation="e", affectedMethod=method,
                              suggestedFix="fix", severity=severity, kind=kind, callPattern=pattern,
                              evidence=[f"{method} threw"])


SPRING = ConsumerAnalysis(className="C", methodName="on", eventType="Ev", listenerAnnotation="EventListener")


class TestApplyVerdict:
    def test_no_verdict_keeps_finding(self):
        assert apply_verdict(f(), None) == (f(), True)

    def test_drop(self):
        out, keep = apply_verdict(f(), {"verdict": "DROP", "note": "read only"})
        assert not keep and out.reviewNote == "read only"

    def test_revise_rewrites_text_only(self):
        out, keep = apply_verdict(f(), {"verdict": "REVISE", "summary": "save called twice",
                                        "explanation": "", "note": "claim was not measured"})
        assert keep and out.summary == "save called twice"
        assert out.explanation == "e"  # empty fields leave the original
        assert out.reviewNote == "claim was not measured"

    def test_severity_disagreement_is_a_note_never_applied(self):
        out, _ = apply_verdict(f(), {"verdict": "KEEP", "severity": "LOW", "summary": "ignored", "note": "minor"})
        assert out.severity == "HIGH" and out.summary == "s"
        assert out.reviewNote == "Reviewer would rate LOW: minor"

    def test_invalid_severity_ignored(self):
        out, _ = apply_verdict(f(), {"verdict": "REVISE", "severity": "NONE"})
        assert out.severity == "HIGH" and out.reviewNote == ""


def fake_chat(reply):
    def chat(model, messages, purpose, valid=lambda r: True, fresh=False, seed=0):
        chat.messages = messages
        return reply
    return chat


def test_review_keeps_order_and_drops(monkeypatch):
    findings = [f("a.x"), f("b.y"), f("c.z")]
    monkeypatch.setattr(reviewer.llm, "chat", fake_chat(json.dumps([
        {"id": 1, "verdict": "DROP", "note": "harmless"},
        {"id": 3, "verdict": "KEEP"},
    ])))
    kept, dropped = review(SPRING, "src", findings)
    assert [x.affectedMethod for x in kept] == ["b.y", "c.z"]
    assert [x.affectedMethod for x in dropped] == ["a.x"]
    assert "Finding 3  [HIGH]  on c.z" in reviewer.llm.chat.messages[1]["content"]
