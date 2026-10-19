"""Diagnosis: turn one scenario's measurements into findings.

Code measures (every change against the fault-free baseline), Nemotron Super answers small
closed questions about each dependency call (classify.py, majority of 3 votes), code decides
which findings exist and how severe they are (rubric.py), and Super only writes their text.
"""
import json
import re
from dataclasses import dataclass

from eventbreaker.agent import llm
from eventbreaker.diagnosis.classify import Label, check_interference
from eventbreaker.diagnosis.rubric import Draft, assess
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.scenarios.models import ChaosScenario, describe_test

_MODEL = llm.SUPER

# ── How the event reaches the listener ────────────────────────────────────────

_BROKER = {"KafkaListener", "RabbitListener", "RabbitHandler", "JmsListener", "SqsListener", "StreamListener"}
_IN_PROCESS = {"EventListener", "TransactionalEventListener"}
_BUS = {"Subscribe"}

TRANSPORT_TEXT = {
    "broker": (
        "message broker (at-least-once): a message is redelivered after a failure or a consumer "
        "rebalance, so duplicate delivery is normal; an exception thrown by the listener causes redelivery; "
        "an exception the listener swallows means the message is acknowledged and the work is lost."
    ),
    "in-process": (
        "in-process Spring event: the listener is a synchronous method call inside the publishing code. "
        "There is NO broker and NO redelivery — the same event only runs twice if the code publishes it "
        "twice. Concurrent runs are still real (two requests at once). An exception thrown by the listener "
        "propagates to the code that published the event; one the listener swallows is simply lost."
    ),
    "guava-bus": (
        "in-process Guava EventBus (@Subscribe): no broker and no redelivery — the same event only runs "
        "twice if it is posted twice. An exception thrown by the subscriber is logged by the bus and the "
        "event is dropped (no retry), so work after the failure is lost. The bus calls this subscriber "
        "one event at a time unless it is annotated @AllowConcurrentEvents."
    ),
    "event-bus": (
        "event bus (@Subscribe): delivery guarantees depend on the bus — some persist and retry events, "
        "some are in-memory. Judge from the source; do not assume broker-style redelivery."
    ),
    "unknown": "unknown delivery mechanism.",
}


def transport(analysis: ConsumerAnalysis) -> str:
    """broker | in-process | event-bus | unknown, from the listener's annotation."""
    a = analysis.listenerAnnotation
    if a in _BROKER:
        return "broker"
    if a in _IN_PROCESS:
        return "in-process"
    if a in _BUS:
        return "guava-bus" if analysis.subscribeBus == "guava" else "event-bus"
    return "unknown"


# ── What was measured ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Change:
    """One measured difference that a finding must account for."""
    target: str                 # dependency.method
    kind: str                   # MISSING | EXTRA | THREW
    before: int | None = None   # calls in the fault-free baseline (None without a baseline)
    after: int = 0              # calls in this scenario

    def describe(self) -> str:
        if self.kind == "THREW":
            return f"{self.target} threw"
        base = f" (baseline {self.before}×)" if self.before is not None else ""
        word = "missing" if self.kind == "MISSING" else "extra"
        return f"{self.target}: called {self.after}×{base} — {word}"


def measured_changes(result: ObservationResult, baseline: ObservationResult | None) -> list[Change]:
    """Every call whose count differs from the baseline, plus every call that threw."""
    changes: list[Change] = []
    if baseline is not None:
        base = {o.target: o.callCount for o in baseline.observations}
        now = {o.target: o.callCount for o in result.observations}
        for t in sorted(base.keys() | now.keys()):
            b, n = base.get(t, 0), now.get(t, 0)
            if b != n:
                changes.append(Change(t, "MISSING" if n < b else "EXTRA", b, n))
    else:
        changes += [Change(o.target, "EXTRA", None, o.callCount) for o in result.observations if o.callCount > 1]
    changes += [Change(o.target, "THREW", None, o.callCount) for o in result.observations if o.threw]
    return changes


def listener_outcome(result: ObservationResult) -> str:
    """The measured outcome of the listener call itself — part of every finding's evidence."""
    if result.consumerThrew:
        return f"the listener THREW ({result.consumerException or 'an exception'}) — the failure was not swallowed"
    return "the listener did NOT throw — it completed normally or caught the exception"


# ── Writing the text ──────────────────────────────────────────────────────────

_WRITER_PROMPT = """\
You write findings for developers about a Java event listener. Each finding has ALREADY been decided —
its severity and the call it is about are fixed by measurement and rules; do not change or question
them. The listener was run with recording fakes for every dependency (no real database, broker or
network) and compared with a fault-free run; the measured evidence is given.

For each finding write:
- "summary": one sentence stating what was measured.
- "explanation": 2-3 sentences on what this means in production for this listener, using the source.
  Only state what the evidence shows or the source makes certain; describe production consequences
  as what WOULD happen — the fakes cannot show database, broker or network effects.
- "suggestedFix": a concrete fix referencing the actual code.

Return ONLY a JSON array with one object per finding, in the same order:
[{"summary": "...", "explanation": "...", "suggestedFix": "..."}]\
"""


def _write_text(
    analysis: ConsumerAnalysis, scenario: ChaosScenario, drafts: list[Draft],
    labels: dict[str, Label], source_code: str, fresh: bool,
) -> list[dict]:
    blocks = []
    for i, d in enumerate(drafts, 1):
        involved = sorted({e.split(":")[0].split(" ")[0] for e in d.evidence if "." in e.split(" ")[0]})
        label_lines = "".join(
            f"\n    {t}: {labels[t].describe()} — {labels[t].reason}" for t in involved if t in labels
        )
        blocks.append(
            f"Finding {i}: severity {d.severity} on {d.affectedMethod}\n"
            f"  Why this severity: {d.basis}\n"
            f"  Measured evidence: {'; '.join(d.evidence)}"
            + (f"\n  What the calls do:{label_lines}" if label_lines else "")
        )
    user = (
        f"Listener: {analysis.className}.{analysis.methodName}({analysis.eventType})\n"
        f"Delivery: {TRANSPORT_TEXT[transport(analysis)]}\n"
        f"Scenario: {scenario.scenarioType} — {describe_test(scenario).replace('`', '')}\n"
        f"Concern: {scenario.expectedConcern}\n\n"
        + "\n\n".join(blocks)
        + f"\n\nSource code:\n{source_code}\n\nReturn the JSON array of {len(drafts)} findings."
    )
    raw = llm.chat(
        _MODEL, [{"role": "system", "content": _WRITER_PROMPT}, {"role": "user", "content": user}],
        "diagnosis", valid=lambda r: len(_items(r) or []) == len(drafts), fresh=fresh,
    )
    texts = _items(raw) or []
    return texts if len(texts) == len(drafts) else [{} for _ in drafts]


def _items(raw: str) -> list[dict] | None:
    """The JSON array in a reply (a single object is wrapped), or None if there is none."""
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    for opener, closer in (("[", "]"), ("{", "}")):
        start, end = cleaned.find(opener), cleaned.rfind(closer) + 1
        if start == -1 or end == 0:
            continue
        try:
            data = json.loads(cleaned[start:end])
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        return [i for i in items if isinstance(i, dict)]
    return None


# ── Diagnose ──────────────────────────────────────────────────────────────────

@dataclass
class Diagnosis:
    findings: list[ReliabilityFinding]
    harmless: list[str]          # measured changes that no finding covers


def diagnose(
    analysis: ConsumerAnalysis,
    scenario: ChaosScenario,
    result: ObservationResult,
    source_code: str,
    labels: dict[str, Label],
    baseline: ObservationResult | None = None,
    fresh: bool = False,
) -> Diagnosis:
    """Findings for one executed scenario.

    Code decides which findings exist and their severity (rubric.assess) from the measured
    changes and the voted call labels; Nemotron Super only writes their text. Every measured
    change ends up either in a finding's evidence or in the harmless list.
    """
    changes = measured_changes(result, baseline)
    tc = scenario.testConfig
    interference = (
        check_interference(analysis, source_code, scenario, fresh=fresh)
        if tc is not None and tc.callPattern == "CONCURRENT" and changes else None
    )
    delivery = transport(analysis)
    assessment = assess(scenario, result, changes, labels, delivery,
                        listener_outcome(result), interference,
                        serialized=delivery == "guava-bus" and not analysis.allowConcurrentEvents,
                        transactional=analysis.transactionalRollback)
    if not assessment.drafts:
        return Diagnosis([], assessment.harmless)

    texts = _write_text(analysis, scenario, assessment.drafts, labels, source_code, fresh)
    findings = [
        ReliabilityFinding(
            scenario=scenario.scenarioType,
            summary=str(t.get("summary") or f"{d.affectedMethod}: " + "; ".join(d.evidence[:-1])),
            explanation=str(t.get("explanation") or d.basis),
            affectedMethod=d.affectedMethod,
            suggestedFix=str(t.get("suggestedFix") or "Review this call's error handling and idempotency."),
            severity=d.severity,
            kind=d.kind,
            onFaultedCall=d.affectedMethod in _fault_targets(scenario),
            callPattern=tc.callPattern if tc else "",
            evidence=d.evidence,
            severityNote=d.basis,
        )
        for d, t in zip(assessment.drafts, texts)
    ]
    return Diagnosis(findings, assessment.harmless)


_SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


KIND_LABELS = {
    "repeated": "work repeated (duplicate delivery)",
    "concurrent": "concurrent deliveries",
    "failure": "behaviour when a dependency fails",
    "other": "observed behaviour",
}


def _fault_targets(scenario: ChaosScenario) -> set[str]:
    tc = scenario.testConfig
    return {f"{fi.dep}.{fi.method}" for fi in tc.faultInjections} if tc else set()


def merge_findings(findings: list[ReliabilityFinding]) -> list[ReliabilityFinding]:
    """Collapse findings to one per (affectedMethod, kind).

    diagnose() runs per scenario, so the same problem on a method is reported once
    per scenario that hits it. Findings of a different kind on the same method —
    "lost after a failure" vs "repeated on redelivery" — are different problems
    and both stay. Within a group keep the most severe finding; on a tie prefer
    the one whose scenario injected its fault on that very method, then the first.
    Other scenarios go to alsoSeenIn. Order follows first appearance.
    """
    groups: dict[tuple[str, str], list[ReliabilityFinding]] = {}
    for f in findings:
        groups.setdefault((f.affectedMethod, f.kind), []).append(f)

    merged: list[ReliabilityFinding] = []
    for group in groups.values():
        best = min(group, key=lambda f: (_SEVERITY_RANK.get(f.severity, 9), not f.onFaultedCall))
        others = [
            f.scenario for f in group
            if f is not best and f.scenario != best.scenario
        ]
        merged.append(best.model_copy(update={"alsoSeenIn": list(dict.fromkeys(others))}))
    return merged
