"""Review: Nemotron Ultra checks every finding against the evidence that was measured.

Diagnosis (Nemotron Super) writes the findings; a different, stronger model then
reads each one next to its measured evidence and the source, and keeps, rewrites or
drops it. Severity is computed by code (rubric.py) and never changed here: if the
reviewer disagrees, its view is shown as a note. One call per listener.
"""
from eventbreaker.agent import llm
from eventbreaker.diagnosis.analyzer import TRANSPORT_TEXT, _items, transport
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ReliabilityFinding

_MODEL = llm.ULTRA

_SYSTEM_PROMPT = """\
You review reliability findings about a Java event listener before they are shown to its developers.
Each finding was written by another model from MEASURED evidence: the listener ran with recording fakes
for every dependency (no real database, broker or network), and each dependency call was counted and
compared with a fault-free run. You get the listener source, how events are delivered to it, and for
every finding: how its scenario was run, the measured evidence, and the finding text.

For each finding decide:
- KEEP: accurate, and the severity fits.
- REVISE: the problem is real but the text claims something that was not measured (an effect that
  only a real database, broker or network could show) or the severity is wrong. Return the
  corrected fields; describe production consequences as what WOULD happen, not as observed.
- DROP: not a real production risk (the measured behaviour is correct or harmless for this listener).

Severity: HIGH = data loss, money, security, or messages lost/duplicated externally; MEDIUM = real but
contained (a visible failure, a recoverable inconsistency); LOW = minor (extra work, recompute/refresh
done twice). A duplicate only matters if the delivery mechanism can actually deliver the event twice.

Return ONLY a JSON array, one entry per finding:
[{"id": <n>, "verdict": "KEEP|REVISE|DROP", "severity": "<HIGH|MEDIUM|LOW>", "summary": "...",
  "explanation": "...", "suggestedFix": "...", "note": "<one sentence: what you changed and why, or empty>"}]
For KEEP, the text fields may be omitted.\
"""


def review(
    analysis: ConsumerAnalysis, source_code: str, findings: list[ReliabilityFinding], fresh: bool = False,
) -> tuple[list[ReliabilityFinding], list[ReliabilityFinding]]:
    """(kept findings, dropped findings). Findings the reviewer doesn't mention are kept unchanged."""
    if not findings:
        return [], []
    blocks = []
    for i, f in enumerate(findings, 1):
        blocks.append(
            f"Finding {i}  [{f.severity}]  on {f.affectedMethod}\n"
            f"  Scenario: {f.scenario} ({f.callPattern or 'n/a'})"
            + (f", also seen in {', '.join(f.alsoSeenIn)}" if f.alsoSeenIn else "") + "\n"
            f"  Measured evidence: {'; '.join(f.evidence) or '(none listed)'}\n"
            f"  Severity basis (computed by rules from the evidence): {f.severityNote or 'n/a'}\n"
            f"  Summary: {f.summary}\n"
            f"  Explanation: {f.explanation}\n"
            f"  Suggested fix: {f.suggestedFix}"
        )
    user = (
        f"Listener: {analysis.className}.{analysis.methodName}({analysis.eventType})\n"
        f"Delivery: {TRANSPORT_TEXT[transport(analysis)]}\n\n"
        + "\n\n".join(blocks)
        + f"\n\nSource code:\n{source_code}\n\nReturn the JSON array of {len(findings)} verdicts."
    )
    raw = llm.chat(
        _MODEL, [{"role": "system", "content": _SYSTEM_PROMPT}, {"role": "user", "content": user}],
        "review", valid=lambda r: _items(r) is not None, fresh=fresh,
    )
    verdicts = {}
    for v in _items(raw) or []:
        try:
            verdicts[int(v.get("id"))] = v
        except (TypeError, ValueError):
            continue

    kept: list[ReliabilityFinding] = []
    dropped: list[ReliabilityFinding] = []
    for i, f in enumerate(findings, 1):
        updated, keep = apply_verdict(f, verdicts.get(i))
        (kept if keep else dropped).append(updated)
    return kept, dropped


def apply_verdict(finding: ReliabilityFinding, verdict: dict | None) -> tuple[ReliabilityFinding, bool]:
    """(finding with the reviewer's corrections, whether it is kept)."""
    if not verdict:
        return finding, True
    kind = str(verdict.get("verdict", "KEEP")).upper()
    note = str(verdict.get("note") or "").strip()
    if kind == "DROP":
        return finding.model_copy(update={"reviewNote": note or "Dropped by reviewer."}), False
    update: dict = {"reviewNote": note} if note else {}
    severity = str(verdict.get("severity") or "").upper()
    if severity in ("HIGH", "MEDIUM", "LOW") and severity != finding.severity:
        # Shown, not applied: severity comes from the rubric so it is the same on every run.
        update["reviewNote"] = (f"Reviewer would rate {severity}" + (f": {note}" if note else ".")).strip()
    if kind == "REVISE":
        for field in ("summary", "explanation", "suggestedFix"):
            value = verdict.get(field)
            if isinstance(value, str) and value.strip():
                update[field] = value.strip()
    return finding.model_copy(update=update), True

