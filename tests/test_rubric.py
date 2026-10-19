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
    "totals.recalculate": Label("REFRESH", "DATA"),
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


def test_thrown_failure_in_a_transaction_rolls_back_all_but_sent_messages():
    seq = ["orders.find", "ledger.append", "mail.send", "orders.save"]
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1), E(target="orders.save", callCount=1, threw=True), threw=True, seq=seq)
    s = sc("SINGLE", "orders.save")
    args = (s, r, measured_changes(r, BASELINE), LABELS, "in-process", listener_outcome(r))
    [d] = assess(*args, transactional="unchecked").drafts
    assert "mail.send" in d.basis and "ledger.append" not in d.basis   # a rollback cannot unsend
    quiet = run(*r.observations, threw=True, seq=seq[:2] + seq[3:])
    assert assess(s, quiet, measured_changes(quiet, BASELINE), LABELS, "in-process",
                  listener_outcome(quiet), transactional="unchecked").drafts == []


def test_checked_exception_commits_unless_rollback_for_is_given():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1, threw=True), threw=True,
            seq=["orders.find", "ledger.append", "mail.send"])
    r.consumerExceptionChecked = True
    args = (sc("SINGLE", "mail.send"), r, measured_changes(r, BASELINE), LABELS, "broker", listener_outcome(r))
    assert assess(*args, transactional="unchecked").drafts[0].severity == "HIGH"   # Spring commits
    assert assess(*args, transactional="any").drafts == []


# ── Duplicate delivery ───────────────────────────────────────────────────────

DUP = run(E(target="orders.find", callCount=2), E(target="ledger.append", callCount=2), E(target="mail.send", callCount=2))


def test_duplicate_money_side_effect_is_high_on_broker():
    [d] = go(sc("DUPLICATE"), DUP).drafts
    assert (d.kind, d.severity, d.affectedMethod) == ("repeated", "HIGH", "ledger.append")
    assert any("mail.send" in e for e in d.evidence)  # every repeated side effect is evidence
    assert not any("orders.find" in e for e in d.evidence)


def test_duplicate_minor_side_effect_is_harmless():
    """MINOR impact = caches, logs, metrics (the label prompt's definition): counted twice, nothing to report."""
    base = run(E(target="stats.bump", callCount=1))
    a = go(sc("DUPLICATE"), run(E(target="stats.bump", callCount=2)), baseline=base)
    assert a.drafts == [] and a.harmless == ["stats.bump: called 2× (baseline 1×) — extra"]


def test_minor_work_never_drives_a_failure_finding():
    # Threw after only a metric was bumped: nothing that matters stays done.
    r = run(E(target="stats.bump", callCount=1), E(target="mail.send", callCount=1, threw=True), threw=True,
            seq=["stats.bump", "mail.send"])
    assert go(sc("SINGLE", "mail.send"), r, baseline=run(E(target="stats.bump", callCount=1),
                                                        E(target="mail.send", callCount=1))).drafts == []
    # Swallowed failure whose only "lost" work is a metric: no finding.
    r = run(E(target="orders.find", callCount=1, threw=True), E(target="stats.bump", callCount=0))
    base = run(E(target="orders.find", callCount=1), E(target="stats.bump", callCount=1))
    assert go(sc("SINGLE", "orders.find"), r, baseline=base).drafts == []


def test_a_metric_on_the_failure_path_is_not_a_recovery():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1, threw=True),
            E(target="mail.send", callCount=0), E(target="stats.bump", callCount=1))
    [d] = go(sc("SINGLE", "ledger.append"), r).drafts
    assert d.severity == "HIGH" and "silently lost" in d.basis


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


def test_race_without_interference_is_no_finding():
    # The repeated work is the duplicate scenario's finding; the race scenario would only repeat it.
    a = go(sc("CONCURRENT"), DUP, interference=Interference(False, [], "independent", "3/3"))
    assert a.drafts == [] and len(a.harmless) == 3


# ── Refresh / recompute ──────────────────────────────────────────────────────

def test_duplicate_refresh_is_low():
    base = run(E(target="orders.find", callCount=1), E(target="totals.recalculate", callCount=1))
    r = run(E(target="orders.find", callCount=2), E(target="totals.recalculate", callCount=2))
    [d] = go(sc("DUPLICATE"), r, baseline=base).drafts
    assert (d.kind, d.severity, d.affectedMethod) == ("repeated", "LOW", "totals.recalculate")


def test_duplicate_side_effect_outranks_refresh():
    base = run(E(target="totals.recalculate", callCount=1), E(target="ledger.append", callCount=1))
    r = run(E(target="totals.recalculate", callCount=2), E(target="ledger.append", callCount=2))
    [d] = go(sc("DUPLICATE"), r, baseline=base).drafts
    assert (d.severity, d.affectedMethod) == ("HIGH", "ledger.append")


def test_swallowed_failure_losing_a_refresh_is_high():
    base = run(E(target="orders.find", callCount=1), E(target="totals.recalculate", callCount=1))
    r = run(E(target="orders.find", callCount=1, threw=True), seq=["orders.find"])
    [d] = go(sc("SINGLE", "orders.find"), r, baseline=base).drafts
    assert d.severity == "HIGH" and "totals.recalculate" in d.basis


def test_listener_outcome_is_always_the_last_evidence_line():
    [d] = go(sc("DUPLICATE"), DUP).drafts
    assert d.evidence[-1] == listener_outcome(DUP)


def test_unlabelled_call_is_treated_cautiously_as_a_side_effect():
    base = run(E(target="queue.put", callCount=1))
    [d] = go(sc("DUPLICATE"), run(E(target="queue.put", callCount=2)), baseline=base).drafts
    assert d.affectedMethod == "queue.put" and d.severity == "MEDIUM"


def test_race_on_calls_that_never_ran_is_no_finding():
    # The vote names a call the scenario never reached (logic behind a fake): nothing was measured.
    i = Interference(True, ["billing.charge"], "read then charge", "5/5")
    a = go(sc("CONCURRENT"), DUP, interference=i)
    assert a.drafts == []


def test_race_keeps_only_the_voted_calls_that_ran():
    i = Interference(True, ["billing.charge", "ledger.append"], "x", "4/5")
    [d] = go(sc("CONCURRENT"), DUP, interference=i).drafts
    assert d.affectedMethod == "ledger.append" and d.severity == "HIGH"


# ── Plain Guava event bus (no retry, logged + dropped, one call at a time) ─────

def test_guava_bus_failure_that_propagates_loses_the_work_medium():
    r = run(E(target="orders.find", callCount=1, threw=True), E(target="ledger.append", callCount=0),
            E(target="mail.send", callCount=0), threw=True)
    [d] = go(sc("SINGLE", "orders.find"), r, "guava-bus").drafts
    assert (d.kind, d.severity) == ("failure", "MEDIUM")
    assert "drops the event" in d.basis and "ledger.append" in d.basis
    assert go(sc("SINGLE", "orders.find"), r, "broker").drafts == []    # a broker retries instead


def test_guava_bus_duplicate_side_effect_is_low():
    [d] = go(sc("DUPLICATE"), DUP, "guava-bus").drafts
    assert d.severity == "LOW"


def test_guava_subscriber_without_allow_concurrent_events_has_no_race_finding():
    i = Interference(True, ["orders.find", "ledger.append"], "read then append", "3/3")
    args = (sc("CONCURRENT"), DUP, measured_changes(DUP, BASELINE), LABELS, "guava-bus", listener_outcome(DUP), i)
    assert assess(*args, serialized=True).drafts == []
    assert assess(*args, serialized=False).drafts[0].kind == "concurrent"   # @AllowConcurrentEvents


# ── Recovery path: the failure is handed on (retry queue / error topic), not lost ──

def test_swallowed_failure_with_an_extra_write_on_the_failure_path_is_recovery():
    labels = dict(LABELS, **{"retries.getQueue.schedule": Label("SIDE_EFFECT", "DATA")})
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1, threw=True), E(target="retries.getQueue.schedule", callCount=1))
    a = assess(sc("SINGLE", "mail.send"), r, measured_changes(r, BASELINE), labels, "broker", listener_outcome(r))
    assert a.drafts == [] and any("retries.getQueue.schedule" in h for h in a.harmless)


def test_extra_read_or_unlabelled_call_on_the_failure_path_is_not_recovery():
    labels = dict(LABELS, **{"cfg.getRetry.isEnabled": Label("READ", "MINOR")})
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1, threw=True), E(target="cfg.getRetry.isEnabled", callCount=1),
            E(target="unknown.call", callCount=1))
    [d] = assess(sc("SINGLE", "mail.send"), r, measured_changes(r, BASELINE), labels, "broker",
                 listener_outcome(r)).drafts
    assert d.severity == "HIGH"


# ── T5: deliveries where a thrown exception is lost ──────────────────────────

def test_detached_spring_listener_that_throws_loses_the_work_medium():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1, threw=True),
            E(target="mail.send", callCount=0), threw=True, seq=["orders.find", "ledger.append"])
    [d] = go(sc("SINGLE", "ledger.append"), r, "in-process-detached").drafts
    assert d.severity == "MEDIUM" and "detached" in d.basis and "mail.send" in d.basis
    assert go(sc("SINGLE", "ledger.append"), r, "in-process").drafts == []   # synchronous: the publisher sees it


def test_detached_duplicate_is_low_like_any_in_process_event():
    assert go(sc("DUPLICATE"), DUP, "in-process-detached").drafts[0].severity == "LOW"


def test_kafka_default_error_handling_drops_after_retries_medium():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1, threw=True),
            E(target="mail.send", callCount=0), threw=True, seq=["orders.find", "ledger.append"])
    [d] = go(sc("SINGLE", "ledger.append"), r, "broker-drops").drafts
    assert d.severity == "MEDIUM" and "retried about 10 times" in d.basis
    assert go(sc("SINGLE", "ledger.append"), r, "broker").drafts == []        # configured handler / DLT


def test_kafka_default_error_handling_after_a_side_effect_is_still_high():
    r = run(E(target="orders.find", callCount=1), E(target="ledger.append", callCount=1),
            E(target="mail.send", callCount=1, threw=True), threw=True,
            seq=["orders.find", "ledger.append", "mail.send"])
    assert go(sc("SINGLE", "mail.send"), r, "broker-drops").drafts[0].severity == "HIGH"
