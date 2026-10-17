from datetime import datetime
from pathlib import Path

from eventbreaker.classpath.resolver import project_root
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.diagnosis.analyzer import KIND_LABELS
from eventbreaker.models.observation import ObservationEntry, ObservationResult, ReliabilityFinding
from eventbreaker.scenarios.experiment import ExperimentPlan
from eventbreaker.scenarios.models import ChaosScenario, describe_test  # noqa: F401 (used by the CLI)


def generate(
    consumer_file: Path,
    analysis: ConsumerAnalysis,
    scenarios: list[ChaosScenario],
    executions: list[tuple[ChaosScenario, ObservationResult]],
    findings: list[ReliabilityFinding],
    theoretical: list[tuple[ChaosScenario, ExperimentPlan]],
    sandbox: bool,
    baselines: dict[str, ObservationResult] | None = None,
    dismissed: list[ReliabilityFinding] | None = None,
    harmless: dict[str, list[str]] | None = None,
    labels: dict | None = None,
) -> str:
    """Render a Markdown reliability report and return it as a string.

    baselines: scenarioType -> the fault-free run of the same setup, shown next to
    each scenario's counts so the reader can see exactly what changed.
    """
    baselines = baselines or {}
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
        sections.append(f"**Status:** {result.status.replace('_', ' ')}  \n")
        if result.notReached:
            sections.append(
                "**Not reached:** the listener never called "
                + ", ".join(f"`{t}`" for t in result.notReached)
                + " — execution stopped earlier (a guard, branch or exception), so this scenario"
                " neither confirms nor rules out the risk.  \n"
            )
        if result.consumerException:
            sections.append(f"**Listener exception:** `{result.consumerException}`  \n")
        sections.append(f"**What we did:** {describe_test(scenario)}  \n")
        setup = scenario.testConfig.returnValues if scenario.testConfig else []
        if setup:
            sections.append(
                "**Path setup** (what the fakes returned so execution reaches the risky code): "
                + ", ".join(f"`{rv.target}` → `{rv.returns}`" for rv in setup)
                + "  \n"
            )
        sections.append(f"**Why:** {scenario.reason}\n")
        if result.executionError:
            sections.append(f"\n**Error:** {result.executionError}\n")
            continue
        sections.append(
            "\n**Consumer outcome:** "
            + ("threw — the failure propagates (a broker redelivers the message; an in-process "
               "event's publisher sees the exception)"
               if result.consumerThrew else
               "completed normally — the event is treated as processed (a broker message is acknowledged)")
            + "  \n"
        )
        if result.callSequence:
            seq_str = " → ".join(f"`{s}`" for s in result.callSequence)
            sections.append(f"**Call sequence:** {seq_str}  \n")

        baseline = baselines.get(scenario.scenarioType)
        base_counts = {o.target: o.callCount for o in baseline.observations} if baseline else None
        observed = {o.target: o for o in result.observations}
        all_targets = expected_dep_calls + [t for t in observed if t not in expected_dep_calls]
        if base_counts is not None:
            all_targets += [t for t in base_counts if t not in all_targets]
            sections.append(
                "\n| Dependency.Method | Without fault | This scenario | Status |\n"
                "|-------------------|---------------|---------------|--------|\n"
            )
        else:
            sections.append(
                "\n| Dependency.Method | Calls | Status |\n"
                "|-------------------|-------|--------|\n"
            )
        for target in all_targets:
            obs = observed.get(target)
            count = obs.callCount if obs else 0
            status = _call_status(obs, count, base_counts.get(target, 0) if base_counts is not None else None)
            if base_counts is not None:
                sections.append(f"| `{target}` | {base_counts.get(target, 0)} | {count} | {status} |\n")
            else:
                sections.append(f"| `{target}` | {count} | {status} |\n")

    # ── Reliability findings (one per concern) ────────────────────────────────
    sections.append(f"\n---\n\n## Reliability Findings ({len(findings)})\n")
    _SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for i, finding in enumerate(
        sorted(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 9)), 1
    ):
        sections.append(
            f"\n### Finding {i} — `{finding.affectedMethod}`\n"
            f"**Severity:** {finding.severity}  \n"
            f"**Kind:** {KIND_LABELS.get(finding.kind, finding.kind)}  \n"
            f"**Observed:** {finding.summary}\n"
        )
        if finding.evidence:
            sections.append("\n**Measured evidence:**\n" + "".join(f"- {e}\n" for e in finding.evidence))
        sections.append(f"\n**Why it matters:** {finding.explanation}\n")
        sections.append(f"\n**Suggested fix:** {finding.suggestedFix}\n")
        if finding.severityNote:
            sections.append(f"\n**Severity basis (rules):** {finding.severityNote}\n")
        if finding.reviewNote:
            sections.append(f"\n**Reviewer (Nemotron Ultra):** {finding.reviewNote}\n")
        if finding.alsoSeenIn:
            sections.append(f"\n**Also seen in:** {', '.join(finding.alsoSeenIn)}\n")

    if harmless:
        sections.append(
            "\n### Harmless changes\n"
            "_Measured differences from the fault-free run that no finding covers (e.g. reads repeated, "
            "or work repeated by two simultaneous runs that cannot interfere — repeats are judged by the "
            "duplicate-delivery scenario). Listed so nothing measured is hidden._\n\n"
        )
        for scenario_type, items in harmless.items():
            sections.append(f"- **{scenario_type}:** {'; '.join(items)}\n")

    if labels:
        sections.append(
            "\n### What each dependency call does\n"
            "_Nemotron Super's answer to a closed question, asked 3× — majority shown with agreement._\n\n"
            "| Call | Effect | Impact | Votes | Reason |\n|---|---|---|---|---|\n"
        )
        for name, lb in sorted(labels.items()):
            sections.append(f"| `{name}` | {lb.effect} | {lb.impact} | {lb.agreement} | {lb.reason} |\n")

    if dismissed:
        sections.append(
            f"\n### Dismissed by the reviewer ({len(dismissed)})\n"
            "_Diagnosis reported these, but Nemotron Ultra judged them not to be production risks._\n\n"
        )
        for f in dismissed:
            sections.append(f"- `{f.affectedMethod}` ({f.scenario}, was {f.severity}): {f.reviewNote}\n")

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


REPORTS_DIR_NAME = "eventbreaker-reports"
LATEST = "latest.md"


def reports_dir(listener: Path) -> Path:
    """Where a listener's reports go: <project root>/eventbreaker-reports, wherever the command is run from.

    Listeners outside a Maven/Gradle project (loose copies) fall back to ./eventbreaker-reports.
    """
    root = project_root(listener)
    return (root if root else Path.cwd()) / REPORTS_DIR_NAME


def save(report: str, analysis: ConsumerAnalysis, output_dir: Path) -> Path:
    """Write the report to <dir>/<Class>.<method>/latest.md and keep a dated copy beside it.

    latest.md is always the newest run; <YYYY-MM-DD_HH-MM-SS>.md files keep the history.
    Returns the path of latest.md.
    """
    folder = output_dir / f"{analysis.className}.{analysis.methodName}"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dated = folder / f"{stamp}.md"
    n = 2
    while dated.exists():  # two runs in the same second
        dated = folder / f"{stamp}-{n}.md"
        n += 1
    dated.write_text(report, encoding="utf-8")
    latest = folder / LATEST
    latest.write_text(report, encoding="utf-8")
    return latest


def _call_status(obs: ObservationEntry | None, count: int, baseline: int | None) -> str:
    """Status cell: threw / missing / extra vs the fault-free baseline (or plain counts without one)."""
    parts: list[str] = []
    if obs is not None and obs.threw:
        parts.append("**threw**")
    if baseline is not None:
        if count < baseline:
            parts.append(f"**{baseline - count} missing**")
        elif count > baseline:
            parts.append(f"**{count - baseline} extra**")
    elif count == 0:
        parts.append("SKIPPED")
    elif count > 1:
        parts.append(f"**called {count}×**")
    return ", ".join(parts) or "✓"
