"""Severity rubric: code, not a model, decides which findings exist and how severe they are.

Inputs are all measured or voted: the changes against the fault-free baseline, whether the
listener threw, how events are delivered (transport), the voted label of each dependency call,
and — for concurrent scenarios — the voted interference answer. Same inputs, same result.

| Situation (measured)                                                      | Severity                     |
|---------------------------------------------------------------------------|------------------------------|
| Failure, listener did not throw, a write / side effect went missing or failed | HIGH (work silently lost) |
| Failure, listener threw, a SIDE_EFFECT completed before the failure       | HIGH if broker, else MEDIUM  |
| Failure, listener threw, only READ / IDEMPOTENT_WRITE completed           | NONE                         |
| Duplicate, extra SIDE_EFFECT                                              | HIGH if MONEY/SECURITY/EXTERNAL_MESSAGE, else MEDIUM; LOW if in-process |
| Race without interference, extra SIDE_EFFECT                              | as duplicate, without the in-process cap |
| Duplicate / race, extra calls only READ / IDEMPOTENT_WRITE                | NONE                         |
| Race, the runs can interfere                                              | HIGH if an involved call is MONEY/SECURITY, else MEDIUM |
| Changes covered by no finding                                             | listed as harmless changes   |
"""
from dataclasses import dataclass, field

from eventbreaker.diagnosis.classify import Interference, Label
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario

_WRITES = {"IDEMPOTENT_WRITE", "SIDE_EFFECT"}
_SERIOUS_IMPACT = {"MONEY", "SECURITY", "EXTERNAL_MESSAGE"}
_RACE_HIGH_IMPACT = {"MONEY", "SECURITY"}
_IMPACT_ORDER = ("MONEY", "SECURITY", "EXTERNAL_MESSAGE", "DATA", "MINOR")


@dataclass
class Draft:
    """A finding decided by the rubric, before its text is written."""
    kind: str                   # failure | repeated | concurrent
    severity: str               # HIGH | MEDIUM | LOW
    affectedMethod: str
    basis: str                  # why this severity, in one sentence (shown in the report)
    evidence: list[str] = field(default_factory=list)


@dataclass
class Assessment:
    drafts: list[Draft]
    harmless: list[str]         # measured changes no finding covers (shown, never dropped)


def assess(
    scenario: ChaosScenario,
    result: ObservationResult,
    changes: list,               # analyzer.Change
    labels: dict[str, Label],
    transport: str,
    outcome: str,
    interference: Interference | None = None,
) -> Assessment:
    tc = scenario.testConfig
    if tc is None or not changes and not result.consumerThrew:
        return Assessment([], [c.describe() for c in changes])
    if tc.callPattern == "SINGLE" and tc.faultInjections:
        drafts = _failure(scenario, result, changes, labels, transport)
    elif tc.callPattern == "DUPLICATE":
        drafts = _repeated(changes, labels, transport, "repeated")
    elif tc.callPattern == "CONCURRENT":
        drafts = (
            _race(changes, labels, interference)
            if interference is not None and interference.interferes
            else _repeated(changes, labels, transport, "concurrent")
        )
    else:
        drafts = []
    covered = {e for d in drafts for e in d.evidence}
    for d in drafts:
        d.evidence.append(outcome)
    return Assessment(drafts, [c.describe() for c in changes if c.describe() not in covered])


def _label(labels: dict[str, Label], target: str) -> Label:
    return labels.get(target) or Label("SIDE_EFFECT", "DATA", "unclassified", "0/0")


def _failure(scenario, result, changes, labels, transport) -> list[Draft]:
    faulted = {f"{fi.dep}.{fi.method}" for fi in scenario.testConfig.faultInjections}
    threw = [c for c in changes if c.kind == "THREW"]
    root = next((c.target for c in threw if c.target in faulted), threw[0].target if threw else changes[0].target)
    evidence = [c.describe() for c in changes]

    if not result.consumerThrew:
        lost = [c for c in changes if c.kind in ("MISSING", "THREW") and _label(labels, c.target).effect in _WRITES]
        if not lost:
            return []
        names = ", ".join(sorted({c.target for c in lost}))
        return [Draft("failure", "HIGH", root,
                      f"The listener did not throw, so this work was silently lost: {names}.", evidence)]

    # The listener threw: did a side effect complete before the failure?
    seq = result.callSequence
    failed_calls = {c.target for c in threw}
    first_fail = next((i for i, call in enumerate(seq) if call in failed_calls), len(seq))
    completed = sorted({call for call in seq[:first_fail] if _label(labels, call).effect == "SIDE_EFFECT"})
    if not completed:
        return []
    severity = "HIGH" if transport == "broker" else "MEDIUM"
    why = ("a broker redelivers the message and repeats it" if transport == "broker"
           else "the failure is visible but this work stays done")
    return [Draft("failure", severity, root,
                  f"The listener threw after a side effect had completed ({', '.join(completed)}): {why}.",
                  evidence)]


def _repeated(changes, labels, transport, kind) -> list[Draft]:
    side = [c for c in changes if c.kind == "EXTRA" and _label(labels, c.target).effect == "SIDE_EFFECT"]
    if not side:
        return []
    worst = min(side, key=lambda c: _IMPACT_ORDER.index(_label(labels, c.target).impact))
    impact = _label(labels, worst.target).impact
    if transport == "in-process" and kind == "repeated":
        severity, why = "LOW", "an in-process event is not redelivered, so it only runs twice if published twice"
    elif impact in _SERIOUS_IMPACT:
        severity, why = "HIGH", f"a side effect with {impact} impact was done twice"
    else:
        severity, why = "MEDIUM", f"a side effect with {impact} impact was done twice"
    return [Draft(kind, severity, worst.target, f"Severity {severity}: {why}.",
                  [c.describe() for c in changes if c in side])]


def _race(changes, labels, interference: Interference) -> list[Draft]:
    involved = interference.calls or [c.target for c in changes]
    impacts = {_label(labels, t).impact for t in involved}
    severity = "HIGH" if impacts & _RACE_HIGH_IMPACT else "MEDIUM"
    return [Draft(
        "concurrent", severity, involved[0],
        f"Two simultaneous runs can interfere ({interference.agreement} votes: {interference.reason}); "
        f"involved calls have impact {', '.join(sorted(impacts))}.",
        [c.describe() for c in changes],
    )]
