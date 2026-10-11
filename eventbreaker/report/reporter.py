from datetime import datetime
from pathlib import Path

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.scenarios.experiment import ExperimentPlan
from eventbreaker.scenarios.models import ChaosScenario


def generate(
    consumer_file: Path,
    analysis: ConsumerAnalysis,
    scenarios: list[ChaosScenario],
    executions: list[tuple[ChaosScenario, ObservationResult]],
    findings: list[ReliabilityFinding],
    theoretical: list[tuple[ChaosScenario, ExperimentPlan]],
    sandbox: bool,
) -> str:
    """Render a Markdown reliability report and return it as a string."""
    date = datetime.now().strftime("%Y-%m-%d %H:%M")
    exec_mode = "Nebius Sandbox" if sandbox else "local JVM"

    sections: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    sections.append(f"# EventBreaker Reliability Report\n")
    sections.append(f"**File:** `{consumer_file}`  \n**Date:** {date}\n")

    # ── Consumer summary ──────────────────────────────────────────────────────
    sections.append("---\n\n## Consumer\n")
    sections.append(
        f"**Class:** `{analysis.className}.{analysis.methodName}`  \n"
        f"**Event:** `{analysis.eventType}`\n"
    )

    if analysis.dependencies:
        sections.append("\n### Dependencies\n")
        sections.append("| Field | Type |\n|-------|------|\n")
        for dep in analysis.dependencies:
            sections.append(f"| `{dep.name}` | `{dep.type}` |\n")

    if analysis.methodCalls:
        sections.append("\n### Call order\n")
        for i, call in enumerate(analysis.methodCalls, 1):
            sections.append(f"{i}. `{call.expression}`\n")

    # ── Identified risks ──────────────────────────────────────────────────────
    sections.append("\n---\n\n## Identified Risks\n")
    for i, scenario in enumerate(scenarios, 1):
        target = ""
        if scenario.targetDependency:
            target = scenario.targetDependency
            if scenario.targetMethod:
                target += f".{scenario.targetMethod}"
        sections.append(
            f"\n### Risk {i} — {scenario.scenarioType}\n"
            f"**Reason:** {scenario.reason}  \n"
            + (f"**Target:** `{target}`  \n" if target else "")
            + f"**Concern:** {scenario.expectedConcern}\n"
        )

    # ── Scenario executions ───────────────────────────────────────────────────
    sections.append(f"\n---\n\n## Scenario Executions\n")
    sections.append(f"**Execution mode:** {exec_mode}\n")
    dep_names = {d.name for d in analysis.dependencies}
    expected_dep_calls: list[str] = []
    seen_calls: set[str] = set()
    for c in analysis.methodCalls:
        if c.scope in dep_names:
            key = f"{c.scope}.{c.methodName}"
            if key not in seen_calls:
                seen_calls.add(key)
                expected_dep_calls.append(key)

    for scenario, result in executions:
        sections.append(f"\n### {scenario.scenarioType}  \n")
        sections.append(f"**Status:** {result.status}  \n**Reason:** {scenario.reason}\n")
        if result.executionError:
            sections.append(f"\n**Error:** {result.executionError}\n")
        else:
            if result.consumerThrew:
                sections.append("**Consumer threw:** YES  \n")
            if result.callSequence:
                seq_str = " → ".join(f"`{s}`" for s in result.callSequence)
                sections.append(f"**Call sequence:** {seq_str}  \n")
            observed = {o.target: o.callCount for o in result.observations}
            all_targets = expected_dep_calls + [
                t for t in observed if t not in expected_dep_calls
            ]
            sections.append(
                "\n| Dependency.Method | Calls | Status |\n"
                "|-------------------|-------|--------|\n"
            )
            for target in all_targets:
                count = observed.get(target)
                if count is None:
                    sections.append(f"| `{target}` | 0 | SKIPPED |\n")
                elif count > 1:
                    sections.append(f"| `{target}` | {count} | ⚠ called {count}× |\n")
                else:
                    sections.append(f"| `{target}` | {count} | ✓ |\n")

    # ── Reliability findings (one per concern) ────────────────────────────────
    sections.append(f"\n---\n\n## Reliability Findings ({len(findings)})\n")
    _SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for i, finding in enumerate(
        sorted(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 9)), 1
    ):
        sections.append(
            f"\n### Finding {i} — `{finding.affectedMethod}`\n"
            f"**Severity:** {finding.severity}  \n"
            f"**Observed:** {finding.summary}\n"
        )
        sections.append(f"\n**Why it matters:** {finding.explanation}\n")
        sections.append(f"\n**Suggested fix:** {finding.suggestedFix}\n")
        if finding.alsoSeenIn:
            sections.append(f"\n**Also seen in:** {', '.join(finding.alsoSeenIn)}\n")

    # ── Theoretical risks ─────────────────────────────────────────────────────
    if theoretical:
        sections.append(f"\n---\n\n## Theoretical Risks ({len(theoretical)})\n")
        sections.append(
            "_These risks were identified by Nemotron but cannot be reproduced "
            "inside a single JVM harness. They should be reviewed manually._\n"
        )
        for i, (scenario, plan) in enumerate(theoretical, 1):
            sections.append(
                f"\n### Theoretical Risk {i} — {scenario.scenarioType}\n"
                f"**Reason:** {scenario.reason}  \n"
                f"**Concern:** {scenario.expectedConcern}  \n"
                f"**Why not reproducible:** {plan.reason or 'No reason provided'}\n"
            )

    # ── Footer ────────────────────────────────────────────────────────────────
    sections.append(
        "\n---\n\n*Generated by **EventBreaker** — "
        "AI-powered reliability analysis for event-driven consumers.*\n"
    )

    return "".join(sections)


def save(report: str, output_path: Path = Path("eventbreaker-report.md")) -> Path:
    output_path.write_text(report, encoding="utf-8")
    return output_path
