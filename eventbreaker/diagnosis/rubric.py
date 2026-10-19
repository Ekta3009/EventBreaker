"""Severity rubric: code, not a model, decides which findings exist and how severe they are.

Inputs are all measured or voted: the changes against the fault-free baseline, whether the
listener threw, how events are delivered (transport), the voted label of each dependency call,
and — for concurrent scenarios — the voted interference answer. Same inputs, same result.

| Situation (measured)                                                      | Severity                     |
|---------------------------------------------------------------------------|------------------------------|
| Failure, listener did not throw, a write / side effect went missing or failed | HIGH (work silently lost) |
| … but the failure run made an extra write the normal run did not (retry queue, error topic, failure record) | NONE (recovery path) |
| Failure, listener threw, a SIDE_EFFECT completed before the failure       | HIGH if broker, else MEDIUM  |
| … but the listener is @Transactional and the exception rolls it back      | only EXTERNAL_MESSAGE side effects count (a rollback cannot unsend) |
| Failure, listener threw on a Guava bus (logged, dropped), writes missing  | MEDIUM (work lost, not silent) |
| … same for a detached Spring listener (@Async, after-commit transactional) | MEDIUM (work lost, not silent) |
| Failure, Kafka listener with default error handling threw, no side effect done, writes missing | MEDIUM (retried ~10×, then skipped) |
| Failure, listener threw, only READ / REFRESH / IDEMPOTENT_WRITE completed | NONE                         |
| Duplicate, extra SIDE_EFFECT                                              | HIGH if MONEY/SECURITY/EXTERNAL_MESSAGE, else MEDIUM; LOW if in-process or Guava bus |
| Duplicate, extra REFRESH (no extra SIDE_EFFECT)                           | LOW (extra work, same result)|
| Duplicate, extra calls only READ / IDEMPOTENT_WRITE                       | NONE                         |
| Race, the runs can interfere (on calls that actually ran)                 | HIGH if an involved call is MONEY/SECURITY, else MEDIUM |
| Race, the voted calls never ran in this scenario                          | NONE (not measured)          |
| Race, the runs cannot interfere                                           | NONE (repeated work is the duplicate scenario's finding) |
| Race, Guava subscriber without @AllowConcurrentEvents                     | NONE (the bus never runs it concurrently) |
| Any of the above when the only work involved has MINOR impact (caches, logs, metrics) | NONE (listed as harmless) |
| Changes covered by no finding                                             | listed as harmless changes   |
"""
from dataclasses import dataclass, field

from eventbreaker.diagnosis.classify import Interference, Label
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario

_WRITES = {"IDEMPOTENT_WRITE", "REFRESH", "SIDE_EFFECT"}
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
    serialized: bool = False,
    transactional: str = "",
) -> Assessment:
    tc = scenario.testConfig
    if tc is None or not changes and not result.consumerThrew:
        return Assessment([], [c.describe() for c in changes])
    if tc.callPattern == "SINGLE" and tc.faultInjections:
        drafts = _failure(scenario, result, changes, labels, transport, transactional)
    elif tc.callPattern == "DUPLICATE":
        drafts = _repeated(changes, labels, transport)
    elif tc.callPattern == "CONCURRENT":
        drafts = (_race(result, changes, labels, interference)
                  if interference and interference.interferes and not serialized else [])
    else:
        drafts = []
    covered = {e for d in drafts for e in d.evidence}
    for d in drafts:
        d.evidence.append(outcome)
    return Assessment(drafts, [c.describe() for c in changes if c.describe() not in covered])


def _label(labels: dict[str, Label], target: str) -> Label:
    return labels.get(target) or Label("SIDE_EFFECT", "DATA", "unclassified", "0/0")


def _matters(labels: dict[str, Label], target: str, effects) -> bool:
    """A write whose loss or repetition can matter: MINOR impact (caches, logs, metrics — the label prompt's own
    definition) never makes a failure or duplicate finding by itself; it is shown as a harmless change."""
    label = _label(labels, target)
    return label.effect in effects and label.impact != "MINOR"


def _failure(scenario, result, changes, labels, transport, transactional="") -> list[Draft]:
    faulted = {f"{fi.dep}.{fi.method}" for fi in scenario.testConfig.faultInjections}
    threw = [c for c in changes if c.kind == "THREW"]
    root = next((c.target for c in threw if c.target in faulted), threw[0].target if threw else changes[0].target)
    evidence = [c.describe() for c in changes]

    if not result.consumerThrew:
        lost = [c for c in changes if c.kind in ("MISSING", "THREW") and _matters(labels, c.target, _WRITES)]
        if not lost:
            return []
        # The failure path wrote something the normal run did not (a retry queue, an error topic, a
        # failure record): the failure is handed on, not silently lost. Only voted labels count.
        lost_targets = {c.target for c in lost} | faulted
        recovery = [c for c in changes if c.kind == "EXTRA" and c.target not in lost_targets
                    and c.target in labels and _matters(labels, c.target, ("SIDE_EFFECT", "IDEMPOTENT_WRITE"))]
        if recovery:
            return []
        names = ", ".join(sorted({c.target for c in lost}))
        return [Draft("failure", "HIGH", root,
                      f"The listener did not throw, so this work was silently lost: {names}.", evidence)]

    lost = [c for c in changes if c.kind in ("MISSING", "THREW") and _matters(labels, c.target, _WRITES)]
    names = ", ".join(sorted({c.target for c in lost}))
    if transport in ("guava-bus", "in-process-detached"):
        # The exception never reaches a caller that could retry: the bus / Spring logs it and the event
        # is dropped, so writes that did not happen are lost — visible in the log, hence MEDIUM, not HIGH.
        if not lost:
            return []
        who = ("the Guava event bus logs the exception and drops the event" if transport == "guava-bus" else
               "Spring logs the exception of a detached listener (@Async / after-commit) and nothing retries")
        return [Draft("failure", "MEDIUM", root, f"The listener threw; {who}, so this work is lost: {names}.",
                      evidence)]

    # The listener threw: did a side effect complete before the failure?
    seq = result.callSequence
    failed_calls = {c.target for c in threw}
    first_fail = next((i for i, call in enumerate(seq) if call in failed_calls), len(seq))
    completed = sorted({call for call in seq[:first_fail] if _matters(labels, call, ("SIDE_EFFECT",))})
    # A @Transactional listener that throws rolls back its transactional work (Spring: RuntimeException
    # by default, checked exceptions only with rollbackFor). Only messages already sent stay done.
    rolled_back = transactional == "any" or (transactional == "unchecked" and not result.consumerExceptionChecked)
    if rolled_back:
        completed = [c for c in completed if _label(labels, c).impact == "EXTERNAL_MESSAGE"]
    if not completed:
        if transport == "broker-drops" and lost:
            return [Draft("failure", "MEDIUM", root,
                          f"The listener threw; with no Kafka error handler or dead-letter topic configured, the record "
                          f"is retried about 10 times and then logged and skipped, so this work is lost if the failure "
                          f"persists: {names}.", evidence)]
        return []
    broker = transport in ("broker", "broker-drops")
    severity = "HIGH" if broker else "MEDIUM"
    why = ("a broker redelivers the message and repeats it" if broker
           else "the failure is visible but this work stays done")
    return [Draft("failure", severity, root,
                  f"The listener threw after a side effect had completed ({', '.join(completed)}): {why}.",
                  evidence)]


def _repeated(changes, labels, transport) -> list[Draft]:
    side = [c for c in changes if c.kind == "EXTRA" and _matters(labels, c.target, ("SIDE_EFFECT",))]
    if not side:
        refresh = [c for c in changes if c.kind == "EXTRA" and _matters(labels, c.target, ("REFRESH",))]
        if not refresh:
            return []
        return [Draft("repeated", "LOW", refresh[0].target,
                      "Severity LOW: a refresh was requested twice — extra work, the recomputed result is the same.",
                      [c.describe() for c in refresh])]
    worst = min(side, key=lambda c: _IMPACT_ORDER.index(_label(labels, c.target).impact))
    impact = _label(labels, worst.target).impact
    if transport in ("in-process", "in-process-detached", "guava-bus"):
        severity, why = "LOW", "an in-process event is not redelivered, so it only runs twice if published twice"
    elif impact in _SERIOUS_IMPACT:
        severity, why = "HIGH", f"a side effect with {impact} impact was done twice"
    else:
        severity, why = "MEDIUM", f"a side effect with {impact} impact was done twice"
    return [Draft("repeated", severity, worst.target, f"Severity {severity}: {why}.",
                  [c.describe() for c in changes if c in side])]


def _race(result, changes, labels, interference: Interference) -> list[Draft]:
    # The vote reads the source, so it can name calls this run never reached (e.g. code behind
    # a dependency that is a fake here). Only calls that actually ran are evidence.
    ran = {o.target for o in result.observations if o.callCount > 0}
    if interference.calls:
        involved = [c for c in interference.calls if c in ran]
        if not involved:
            return []
    else:
        involved = [c.target for c in changes]
    impacts = {_label(labels, t).impact for t in involved}
    severity = "HIGH" if impacts & _RACE_HIGH_IMPACT else "MEDIUM"
    return [Draft(
        "concurrent", severity, involved[0],
        f"Two simultaneous runs can interfere ({interference.agreement} votes: {interference.reason}); "
        f"involved calls have impact {', '.join(sorted(impacts))}.",
        [c.describe() for c in changes],
    )]
