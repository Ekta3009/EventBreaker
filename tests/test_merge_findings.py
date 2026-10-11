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
