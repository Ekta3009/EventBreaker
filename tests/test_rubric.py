"""Severity rubric — one test per row of the table in rubric.py.

All inputs are synthetic (an imaginary order listener). Nothing here is taken from the
real-world evaluation set, so the rubric is not tuned toward its expected findings.
"""
from eventbreaker.diagnosis.analyzer import listener_outcome, measured_changes
from eventbreaker.diagnosis.classify import Interference, Label
from eventbreaker.diagnosis.rubric import assess
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig

LABELS = {
    "orders.find": Label("READ", "MINOR"),
    "orders.save": Label("IDEMPOTENT_WRITE", "DATA"),
    "ledger.append": Label("SIDE_EFFECT", "MONEY"),
    "mail.send": Label("SIDE_EFFECT", "EXTERNAL_MESSAGE"),
    "stats.bump": Label("SIDE_EFFECT", "MINOR"),
}


def run(*obs, threw=False, seq=()):
    return ObservationResult(scenario="X", observations=list(obs), consumerThrew=threw, callSequence=list(seq))


def sc(pattern, fault=None):
    faults = [FaultInjection(dep=fault.split(".")[0], method=fault.split(".")[1], fault="THROW")] if fault else []
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c",
                         testConfig=TestConfig(callPattern=pattern, faultInjections=faults))


BASELINE = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
               E(target="mail.send", callCount=1))


def go(scenario, result, transport="broker", interference=None, baseline=BASELINE):
    return assess(scenario, result, measured_changes(result, baseline), LABELS, transport,
                  listener_outcome(result), interference)


# ── Failure scenarios ────────────────────────────────────────────────────────

def test_swallowed_failure_losing_a_write_is_high():
    r = run(E(target="orders.find", callCount=1, threw=True), E(target="ledger.append", callCount=0),
            E(target="mail.send", callCount=0))
    [d] = go(sc("SINGLE", "orders.find"), r).drafts
    assert (d.kind, d.severity, d.affectedMethod) == ("failure", "HIGH", "orders.find")
    assert "silently lost" in d.basis and "ledger.append" in d.basis and "mail.send" in d.basis


def test_swallowed_failure_of_a_write_itself_is_high():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1, threw=True),
            E(target="mail.send", callCount=1))
    [d] = go(sc("SINGLE", "ledger.append"), r).drafts
    assert d.severity == "HIGH" and d.affectedMethod == "ledger.append"


def test_swallowed_failure_losing_only_reads_is_no_finding():
    base = run(E(target="orders.find", callCount=2))
    r = run(E(target="orders.find", callCount=1, threw=True))
    a = go(sc("SINGLE", "orders.find"), r, baseline=base)
    assert a.drafts == [] and a.harmless  # still listed, never hidden


def test_thrown_failure_after_a_side_effect_high_on_broker_medium_otherwise():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1, threw=True), threw=True,
            seq=["orders.find", "ledger.append", "mail.send"])
    assert go(sc("SINGLE", "mail.send"), r, "broker").drafts[0].severity == "HIGH"
    assert go(sc("SINGLE", "mail.send"), r, "in-process").drafts[0].severity == "MEDIUM"


def test_thrown_failure_after_only_reads_is_no_finding():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1, threw=True),
            E(target="mail.send", callCount=0), threw=True, seq=["orders.find", "ledger.append"])
    a = go(sc("SINGLE", "ledger.append"), r)
    assert a.drafts == [] and len(a.harmless) == 2


def test_side_effect_after_the_failure_point_does_not_count():
    r = run(E(target="orders.find", callCount=1, threw=True), E(target="ledger.append", callCount=1), threw=True,
            seq=["orders.find", "ledger.append"])
    assert go(sc("SINGLE", "orders.find"), r).drafts == []


# ── Duplicate delivery ───────────────────────────────────────────────────────

DUP = run(E(target="orders.find", callCount=2), E(target="ledger.append", callCount=2), E(target="mail.send", callCount=2))


def test_duplicate_money_side_effect_is_high_on_broker():
    [d] = go(sc("DUPLICATE"), DUP).drafts
    assert (d.kind, d.severity, d.affectedMethod) == ("repeated", "HIGH", "ledger.append")
    assert any("mail.send" in e for e in d.evidence)  # every repeated side effect is evidence
    assert not any("orders.find" in e for e in d.evidence)


def test_duplicate_minor_side_effect_is_medium():
    base = run(E(target="stats.bump", callCount=1))
    [d] = go(sc("DUPLICATE"), run(E(target="stats.bump", callCount=2)), baseline=base).drafts
    assert d.severity == "MEDIUM"


def test_duplicate_on_in_process_listener_is_low():
    assert go(sc("DUPLICATE"), DUP, "in-process").drafts[0].severity == "LOW"


def test_duplicate_of_reads_and_idempotent_writes_is_harmless():
    base = run(E(target="orders.find", callCount=1), E(target="orders.save", callCount=1))
    r = run(E(target="orders.find", callCount=2), E(target="orders.save", callCount=2))
    a = go(sc("DUPLICATE"), r, baseline=base)
    assert a.drafts == [] and len(a.harmless) == 2


# ── Concurrent delivery ──────────────────────────────────────────────────────

def test_race_with_interference_on_money_is_high():
    i = Interference(True, ["orders.find", "ledger.append"], "read then append", "3/3")
    [d] = go(sc("CONCURRENT"), DUP, "in-process", interference=i).drafts
    assert (d.kind, d.severity, d.affectedMethod) == ("concurrent", "HIGH", "orders.find")
    assert "3/3" in d.basis


def test_race_with_interference_on_data_only_is_medium():
    base = run(E(target="orders.find", callCount=1), E(target="orders.save", callCount=1))
    r = run(E(target="orders.find", callCount=2), E(target="orders.save", callCount=2))
    i = Interference(True, ["orders.find", "orders.save"], "check then save", "2/3")
    assert go(sc("CONCURRENT"), r, interference=i, baseline=base).drafts[0].severity == "MEDIUM"


def test_race_without_interference_falls_back_to_the_duplicate_rule_without_in_process_cap():
    i = Interference(False, [], "independent", "3/3")
    [d] = go(sc("CONCURRENT"), DUP, "in-process", interference=i).drafts
    assert (d.kind, d.severity) == ("concurrent", "HIGH")  # two real requests at once: not capped


def test_listener_outcome_is_always_the_last_evidence_line():
    [d] = go(sc("DUPLICATE"), DUP).drafts
    assert d.evidence[-1] == listener_outcome(DUP)


def test_unlabelled_call_is_treated_cautiously_as_a_side_effect():
    base = run(E(target="queue.put", callCount=1))
    [d] = go(sc("DUPLICATE"), run(E(target="queue.put", callCount=2)), baseline=base).drafts
    assert d.affectedMethod == "queue.put" and d.severity == "MEDIUM"
