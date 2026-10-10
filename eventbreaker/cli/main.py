import os
from pathlib import Path

import typer
from dotenv import load_dotenv

load_dotenv()
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from eventbreaker.analyzer.bridge import analyze
from eventbreaker.agent.nemotron import identify_risks
from eventbreaker.diagnosis.analyzer import diagnose
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.report import reporter
from eventbreaker.scenarios.models import ChaosScenario
from eventbreaker.scenarios.executor import ScenarioExecutor
from eventbreaker.scenarios.experiment import ExperimentPlan, check_feasibility

app = typer.Typer(
    name="eventbreaker",
    help="AI-powered reliability analysis for event-driven consumers.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _callback() -> None:
    """EventBreaker — break your consumers before production does."""


@app.command("analyze")
def analyze_consumer(
    file: Path = typer.Argument(
        ...,
        help="Path to the Java consumer file to analyze.",
        exists=True,
        readable=True,
    ),
) -> None:
    """Analyze an event consumer and identify reliability risks."""

    console.print(
        f"\n[bold cyan]EventBreaker[/bold cyan] — analyzing "
        f"[yellow]{file}[/yellow]\n"
    )

    # ── Step 1: Static analysis ───────────────────────────────────────────────
    with console.status("[bold green]Running static analysis..."):
        try:
            analysis = analyze(file)
        except FileNotFoundError as e:
            console.print(f"[bold red]Setup error:[/bold red] {e}")
            raise typer.Exit(1)
        except ValueError as e:
            console.print(f"[bold red]Analysis failed:[/bold red] {e}")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]Unexpected error:[/bold red] {e}")
            raise typer.Exit(1)

    console.print("[bold green]✓ Consumer found[/bold green]")

    # Consumer summary
    console.print(Panel(
        f"[bold]{analysis.className}.{analysis.methodName}[/bold]\n"
        f"Event: [yellow]{analysis.eventType}[/yellow]",
        title="Consumer",
        border_style="green",
    ))

    # Dependencies table
    if analysis.dependencies:
        dep_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
        dep_table.add_column("Field", style="cyan")
        dep_table.add_column("Type", style="yellow")
        for dep in analysis.dependencies:
            dep_table.add_row(dep.name, dep.type)
        console.print(Panel(dep_table, title="Dependencies", border_style="dim"))

    # Method calls table
    if analysis.methodCalls:
        calls_table = Table(
            box=box.SIMPLE, show_header=True, header_style="bold"
        )
        calls_table.add_column("Expression", style="white")
        calls_table.add_column("Scope", style="cyan")
        calls_table.add_column("Method", style="yellow")
        for call in analysis.methodCalls:
            calls_table.add_row(
                call.expression,
                call.scope or "—",
                call.methodName,
            )
        console.print(Panel(calls_table, title="Method Calls", border_style="dim"))

    console.print("[bold green]✓ Static analysis complete[/bold green]\n")

    # ── Step 2: Nemotron risk identification ──────────────────────────────────
    if not os.environ.get("NEBIUS_API_KEY"):
        console.print(
            "[yellow]⚠  NEBIUS_API_KEY not set — "
            "skipping AI risk identification.[/yellow]\n"
            "Export it to run the full analysis: "
            "[dim]export NEBIUS_API_KEY=<your-key>[/dim]"
        )
        raise typer.Exit(0)

    source_code = file.read_text()

    with console.status(
        "[bold green]Asking Nemotron to identify reliability risks..."
    ):
        try:
            scenarios = identify_risks(analysis, source_code)
        except EnvironmentError as e:
            console.print(f"[bold red]Config error:[/bold red] {e}")
            raise typer.Exit(1)
        except ValueError as e:
            console.print(f"[bold red]Nemotron error:[/bold red] {e}")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]Unexpected error:[/bold red] {e}")
            raise typer.Exit(1)

    console.print(
        f"[bold green]✓ {len(scenarios)} reliability risk(s) identified"
        f"[/bold green]\n"
    )

    _print_scenarios(scenarios)

    # ── Step 3: Feasibility check (pure Python, no LLM call) ─────────────────
    experiment_plans: list[tuple[ChaosScenario, ExperimentPlan]] = [
        (scenario, check_feasibility(scenario, analysis))
        for scenario in scenarios
    ]

    feasible = [(s, p) for s, p in experiment_plans if p.feasible]
    theoretical = [(s, p) for s, p in experiment_plans if not p.feasible]

    console.print(
        f"[bold green]✓ {len(feasible)} reproducible, "
        f"{len(theoretical)} theoretical[/bold green]\n"
    )

    if theoretical:
        _print_theoretical(theoretical)

    if not feasible:
        console.print("[yellow]No reproducible scenarios — skipping execution.[/yellow]")
        raise typer.Exit(0)

    # ── Step 4: Execute reproducible scenarios ─────────────────────────────────
    in_sandbox = bool(os.environ.get("NEBIUS_PROJECT_ID"))
    exec_mode = "[cyan]Nebius Sandbox[/cyan]" if in_sandbox else "[yellow]local JVM[/yellow]"

    executions: list[tuple[ChaosScenario, ObservationResult]] = []
    executor = ScenarioExecutor()
    for scenario, plan in feasible:
        with console.status(
            f"[bold green]Executing: {scenario.scenarioType} ({exec_mode})..."
        ):
            try:
                result = executor.execute(scenario, analysis, file)
            except Exception as e:
                console.print(
                    f"[bold red]  Error executing {scenario.scenarioType}:[/bold red] {e}"
                )
                continue

        ok = result.status == "REPRODUCED"
        colour, mark = ("green", "✓") if ok else ("red", "✗")
        console.print(
            f"[bold {colour}]{mark} {scenario.scenarioType} — {result.status}[/bold {colour}]"
        )
        _print_observations(result, analysis)
        executions.append((scenario, result))

    if not executions:
        raise typer.Exit(0)

    console.print()

    # ── Step 5: Nemotron diagnosis for every reproduced scenario ──────────────
    all_findings: list[ReliabilityFinding] = []
    for scenario, result in executions:
        if result.status != "REPRODUCED" or not result.observations:
            continue
        with console.status(
            f"[bold green]Diagnosing {scenario.scenarioType}..."
        ):
            try:
                scenario_findings = diagnose(analysis, scenario, result, source_code)
                if not scenario_findings:
                    console.print(
                        f"[dim]  {scenario.scenarioType}: no anomaly signal — "
                        f"scenario likely requires return-value control (theoretical)[/dim]"
                    )
                all_findings.extend(scenario_findings)
            except EnvironmentError as e:
                console.print(f"[bold red]Config error:[/bold red] {e}")
                raise typer.Exit(1)
            except ValueError as e:
                console.print(
                    f"[yellow]  Diagnosis skipped for {scenario.scenarioType}: {e}[/yellow]"
                )
            except Exception as e:
                console.print(
                    f"[yellow]  Diagnosis skipped for {scenario.scenarioType}: {e}[/yellow]"
                )

    if all_findings:
        console.print(
            f"[bold green]✓ Diagnosis complete — {len(all_findings)} finding(s) total[/bold green]\n"
        )
        _print_findings(all_findings)
    else:
        console.print("[dim]No reliability findings.[/dim]\n")

    # ── Step 6: Save Markdown report ──────────────────────────────────────────
    report_md = reporter.generate(
        consumer_file=file,
        analysis=analysis,
        scenarios=scenarios,
        executions=executions,
        findings=all_findings,
        theoretical=theoretical,
        sandbox=in_sandbox,
    )
    report_path = reporter.save(report_md)
    console.print(
        f"\n[bold green]✓ Report saved →[/bold green] "
        f"[yellow]{report_path}[/yellow]\n"
    )


def _print_theoretical(
    theoretical: list[tuple["ChaosScenario", "ExperimentPlan"]],
) -> None:
    from rich.table import Table
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("Scenario", style="yellow")
    t.add_column("Why not reproducible", style="dim")
    for scenario, plan in theoretical:
        t.add_row(scenario.scenarioType, plan.reason or "—")
    console.print(Panel(
        t,
        title="[bold]Theoretical Risks (not reproducible in JVM)[/bold]",
        border_style="dim",
    ))


def _print_observations(result: ObservationResult, analysis=None) -> None:
    if result.executionError:
        console.print(
            Panel(
                result.executionError,
                title="[bold red]Execution Error[/bold red]",
                border_style="red",
            )
        )
        return

    # Compute expected dep calls so we can show skipped (never-called) methods.
    observed = {o.target: o.callCount for o in result.observations}
    expected_order: list[str] = []
    if analysis:
        dep_names = {d.name for d in analysis.dependencies}
        seen: set[str] = set()
        for c in analysis.methodCalls:
            if c.scope in dep_names:
                key = f"{c.scope}.{c.methodName}"
                if key not in seen:
                    seen.add(key)
                    expected_order.append(key)
    # Add anything observed that wasn't in the expected list (shouldn't happen, but safe)
    for t in observed:
        if t not in expected_order:
            expected_order.append(t)

    obs_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    obs_table.add_column("Dependency.Method", style="cyan")
    obs_table.add_column("Calls", justify="right")
    obs_table.add_column("Status")

    threw_targets = {o.target for o in result.observations if o.threw}

    for target in expected_order:
        count = observed.get(target)
        if count is None:
            obs_table.add_row(target, "[dim]0[/dim]", "[red]SKIPPED[/red]")
        elif count > 1:
            obs_table.add_row(target, f"[red]{count}[/red]", f"[red]⚠ called {count}×[/red]")
        elif target in threw_targets:
            obs_table.add_row(target, f"[yellow]{count}[/yellow]", "[yellow]⚠ threw[/yellow]")
        else:
            obs_table.add_row(target, f"[green]{count}[/green]", "[green]✓[/green]")

    console.print(Panel(
        obs_table,
        title=f"[bold]Observations — {result.scenario}[/bold]",
        border_style="yellow",
    ))


_SEVERITY_COLOURS = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}

# Colour pool for dynamically-typed scenarios Nemotron invents.
_COLOUR_POOL = ["magenta", "cyan", "blue", "orange3", "red", "yellow", "green"]
_SCENARIO_COLOURS: dict[str, str] = {
    "DUPLICATE_EVENT": "red",
    "OUT_OF_ORDER": "yellow",
    "DOWNSTREAM_FAILURE": "magenta",
    "CRASH_AFTER_SIDE_EFFECT": "orange3",
}


def _scenario_colour(scenario_type: str) -> str:
    if scenario_type not in _SCENARIO_COLOURS:
        _SCENARIO_COLOURS[scenario_type] = _COLOUR_POOL[
            len(_SCENARIO_COLOURS) % len(_COLOUR_POOL)
        ]
    return _SCENARIO_COLOURS[scenario_type]


def _print_findings(findings: list[ReliabilityFinding]) -> None:
    """Group all findings by scenario type and print one panel per group."""
    from collections import defaultdict
    _RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

    groups: dict[str, list[ReliabilityFinding]] = defaultdict(list)
    for f in findings:
        groups[f.scenario].append(f)

    for scenario_type, group in groups.items():
        colour = _scenario_colour(scenario_type)
        sorted_group = sorted(group, key=lambda f: _RANK.get(f.severity, 9))

        bullets: list[str] = []
        for f in sorted_group:
            sev_colour = _SEVERITY_COLOURS.get(f.severity, "white")
            bullets.append(
                f"[{sev_colour}]▸ {f.severity}[/{sev_colour}]  "
                f"[cyan]{f.affectedMethod}[/cyan]\n"
                f"  {f.summary}\n"
                f"  [dim]{f.explanation}[/dim]\n"
                f"  [green]Fix: {f.suggestedFix}[/green]"
            )

        console.print(Panel(
            "\n\n".join(bullets),
            title=f"[bold]Reliability Findings — {scenario_type}[/bold]",
            border_style=colour,
        ))


def _print_scenarios(scenarios: list[ChaosScenario]) -> None:
    for i, scenario in enumerate(scenarios, 1):
        colour = _scenario_colour(scenario.scenarioType)

        body = (
            f"[bold]Type:[/bold]    "
            f"[{colour}]{scenario.scenarioType}[/{colour}]\n"
            f"[bold]Reason:[/bold]  {scenario.reason}\n"
        )

        if scenario.targetDependency:
            body += (
                f"[bold]Target:[/bold]  "
                f"[cyan]{scenario.targetDependency}"
            )
            if scenario.targetMethod:
                body += f".{scenario.targetMethod}"
            body += "[/cyan]\n"

        body += f"[bold]Concern:[/bold] [dim]{scenario.expectedConcern}[/dim]"

        console.print(Panel(
            body,
            title=f"[bold]Risk #{i}[/bold]",
            border_style=colour,
        ))
