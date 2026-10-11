"""STEP 9c: findings are merged to one per affectedMethod across scenarios."""
from eventbreaker.diagnosis.analyzer import merge_findings
from eventbreaker.models.observation import ReliabilityFinding


def f(method: str, scenario: str, severity: str = "HIGH", summary: str = "s") -> ReliabilityFinding:
    return ReliabilityFinding(
        scenario=scenario, summary=summary, explanation="e",
        affectedMethod=method, suggestedFix="fix", severity=severity,
    )


def test_one_finding_per_method():
    merged = merge_findings([
        f("ledger.credit", "DUPLICATE_DELIVERY"),
        f("ledger.debit", "DUPLICATE_DELIVERY"),
        f("ledger.credit", "PARTIAL_FAILURE"),
        f("ledger.credit", "CONCURRENT_DELIVERY"),
    ])
    assert [m.affectedMethod for m in merged] == ["ledger.credit", "ledger.debit"]
    assert merged[0].alsoSeenIn == ["PARTIAL_FAILURE", "CONCURRENT_DELIVERY"]
    assert merged[1].alsoSeenIn == []


def test_keeps_highest_severity_finding():
    merged = merge_findings([
        f("repo.save", "A", "LOW", summary="low"),
        f("repo.save", "B", "HIGH", summary="high"),
        f("repo.save", "C", "MEDIUM", summary="medium"),
    ])
    assert len(merged) == 1
    assert merged[0].severity == "HIGH"
    assert merged[0].summary == "high"
    assert merged[0].scenario == "B"
    assert merged[0].alsoSeenIn == ["A", "C"]


def test_tie_keeps_first_and_dedupes_scenarios():
    merged = merge_findings([
        f("repo.save", "A", summary="first"),
        f("repo.save", "A", summary="second"),
        f("repo.save", "B"),
        f("repo.save", "B"),
    ])
    assert merged[0].summary == "first"
    assert merged[0].alsoSeenIn == ["B"]


def test_empty():
    assert merge_findings([]) == []


# ── Kind-aware merge (STEP 10: real consumers) ───────────────────────────────

def fk(method, scenario, kind, severity="HIGH", summary="s", on_fault=False):
    return f(method, scenario, severity, summary).model_copy(update={"kind": kind, "onFaultedCall": on_fault})


def test_different_kinds_on_same_method_both_stay():
    # Kill Bill: notifications lost after a failure (K1) vs duplicated on redelivery (K3)
    merged = merge_findings([
        fk("asyncPoster.insertOverdueNotification", "SILENT_DATA_LOSS", "failure", summary="lost"),
        fk("asyncPoster.insertOverdueNotification", "IDEMPOTENCY_VIOLATION", "repeated", summary="dup"),
    ])
    assert [m.summary for m in merged] == ["lost", "dup"]


def test_same_kind_still_merges_across_scenarios():
    merged = merge_findings([
        fk("repo.save", "A", "failure"),
        fk("repo.save", "B", "failure"),
    ])
    assert len(merged) == 1 and merged[0].alsoSeenIn == ["B"]


def test_tie_prefers_finding_from_scenario_that_faulted_that_method():
    # Fineract: "beforeHandleMessage failed, work lost" must win over a same-severity
    # finding from a scenario where the fault was elsewhere.
    merged = merge_findings([
        fk("inputInterceptor.beforeHandleMessage", "PARTIAL_PROCESSING", "failure", summary="side note"),
        fk("inputInterceptor.beforeHandleMessage", "INCONSISTENT_STATE", "failure", summary="work lost", on_fault=True),
    ])
    assert merged[0].summary == "work lost"
    assert merged[0].alsoSeenIn == ["PARTIAL_PROCESSING"]


def test_severity_still_beats_fault_target():
    merged = merge_findings([
        fk("repo.save", "A", "failure", "MEDIUM", summary="faulted", on_fault=True),
        fk("repo.save", "B", "failure", "HIGH", summary="severe"),
    ])
    assert merged[0].summary == "severe"
