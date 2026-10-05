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
from eventbreaker.scenarios.models import ChaosScenario, ScenarioType
from eventbreaker.scenarios.executor import ScenarioExecutor

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

    # ── Step 3: Execute highest-priority scenario ─────────────────────────────
    executable = next(
        (s for s in scenarios if s.scenarioType == ScenarioType.DUPLICATE_EVENT),
        None,
    )
    if executable is None:
        console.print(
            "[yellow]No DUPLICATE_EVENT scenario identified "
            "— skipping execution.[/yellow]"
        )
        raise typer.Exit(0)

    in_sandbox = bool(os.environ.get("NEBIUS_PROJECT_ID"))
    exec_mode = "[cyan]Nebius Sandbox[/cyan]" if in_sandbox else "[yellow]local JVM[/yellow]"
    with console.status(
        f"[bold green]Executing scenario: "
        f"{executable.scenarioType.value} ({exec_mode})..."
    ):
        try:
            result = ScenarioExecutor().execute(executable, analysis, file)
        except NotImplementedError as e:
            console.print(f"[yellow]Skipped:[/yellow] {e}")
            raise typer.Exit(0)
        except Exception as e:
            console.print(f"[bold red]Execution error:[/bold red] {e}")
            raise typer.Exit(1)

    console.print(
        f"[bold green]✓ Scenario executed — "
        f"status: {result.status}[/bold green]\n"
    )
    _print_observations(result)

    if result.status != "REPRODUCED" or not result.observations:
        raise typer.Exit(0)

    # ── Step 4: Nemotron diagnosis ────────────────────────────────────────────
    with console.status("[bold green]Asking Nemotron to diagnose the finding..."):
        try:
            finding = diagnose(analysis, executable, result, source_code)
        except EnvironmentError as e:
            console.print(f"[bold red]Config error:[/bold red] {e}")
            raise typer.Exit(1)
        except ValueError as e:
            console.print(f"[bold red]Diagnosis error:[/bold red] {e}")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[bold red]Unexpected error:[/bold red] {e}")
            raise typer.Exit(1)

    console.print("[bold green]✓ Diagnosis complete[/bold green]\n")
    _print_finding(finding)

    # ── Step 5: Save Markdown report ──────────────────────────────────────────
    report_md = reporter.generate(
        consumer_file=file,
        analysis=analysis,
        scenarios=scenarios,
        result=result,
        finding=finding,
        sandbox=in_sandbox,
    )
    report_path = reporter.save(report_md)
    console.print(
        f"\n[bold green]✓ Report saved →[/bold green] "
        f"[yellow]{report_path}[/yellow]\n"
    )


def _print_observations(result: ObservationResult) -> None:
    if result.executionError:
        console.print(
            Panel(
                result.executionError,
                title="[bold red]Execution Error[/bold red]",
                border_style="red",
            )
        )
        return

    obs_table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    obs_table.add_column("Dependency.Method", style="cyan")
    obs_table.add_column("Call Count", style="bold yellow", justify="right")

    for obs in result.observations:
        colour = "red" if obs.callCount > 1 else "green"
        obs_table.add_row(
            obs.target,
            f"[{colour}]{obs.callCount}[/{colour}]",
        )

    console.print(Panel(
        obs_table,
        title=f"[bold]Observations — {result.scenario}[/bold]",
        border_style="yellow",
    ))


def _print_finding(finding: ReliabilityFinding) -> None:
    _SEVERITY_COLOURS = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "green"}
    colour = _SEVERITY_COLOURS.get(finding.severity, "white")

    body = (
        f"[bold]Severity:[/bold]  [{colour}]{finding.severity}[/{colour}]\n"
        f"[bold]Observed:[/bold]  {finding.summary}\n\n"
        f"[bold]Why it matters:[/bold]\n{finding.explanation}\n\n"
        f"[bold]Affected:[/bold]  [cyan]{finding.affectedMethod}[/cyan]\n\n"
        f"[bold]Suggested fix:[/bold]\n[green]{finding.suggestedFix}[/green]"
    )

    console.print(Panel(
        body,
        title=f"[bold]Reliability Finding — {finding.scenario}[/bold]",
        border_style=colour,
    ))


def _print_scenarios(scenarios: list[ChaosScenario]) -> None:
    _SCENARIO_COLOURS = {
        "DUPLICATE_EVENT": "red",
        "OUT_OF_ORDER": "yellow",
        "DOWNSTREAM_FAILURE": "magenta",
        "CRASH_AFTER_SIDE_EFFECT": "orange3",
    }

    for i, scenario in enumerate(scenarios, 1):
        colour = _SCENARIO_COLOURS.get(scenario.scenarioType.value, "white")

        body = (
            f"[bold]Type:[/bold]    "
            f"[{colour}]{scenario.scenarioType.value}[/{colour}]\n"
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
