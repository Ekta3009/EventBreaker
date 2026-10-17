import os
from pathlib import Path

import typer
import atexit

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))   # .env in or above the directory you run from
load_dotenv()                           # EventBreaker's own .env (dev checkout); never overrides
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from eventbreaker.analyzer.bridge import analyze, scan
from eventbreaker.agent.nemotron import identify_risks
from eventbreaker.classpath.resolver import ClasspathError, find_module, resolve
from eventbreaker.agent.path_setup import (
    deeper_setup, forget_plan, not_reached, plan_path, reach_feedback, reusable_setup, target_calls,
)
from eventbreaker.diagnosis.analyzer import KIND_LABELS, diagnose, merge_findings
from eventbreaker.diagnosis.classify import label_calls
from eventbreaker.diagnosis.reviewer import review
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.report import reporter
from eventbreaker.scenarios.models import ChaosScenario, TestConfig
from eventbreaker.scenarios.executor import ScenarioExecutor, inherited_source
from eventbreaker.scenarios.coverage import dependency_calls, failure_scenarios
from eventbreaker.scenarios.experiment import ExperimentPlan, check_feasibility

app = typer.Typer(
    name="eventbreaker",
    help="AI-powered reliability analysis for event-driven consumers.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


@app.callback()
def _callback(
    version: bool = typer.Option(
        False, "--version", is_eager=True, help="Show the version and exit.",
        callback=lambda value: _print_version() if value else None,
    ),
) -> None:
    """EventBreaker — break your consumers before production does."""


def _print_version() -> None:
    from importlib.metadata import version
    console.print(f"eventbreaker {version('eventbreaker')}")
    raise typer.Exit()


@app.command("analyze")
def analyze_consumer(
    file: Path = typer.Argument(
        ...,
        help="Path to the Java consumer file to analyze.",
        exists=True,
        readable=True,
    ),
    method: str | None = typer.Option(
        None, "--method", help="Listener method to analyze when the class has several (default: the first).",
    ),
    classpath: str | None = typer.Option(
        None, "--classpath",
        help="Override the dependency jars (path-separated, or @file containing that). "
             "Default: read from the listener's own Maven/Gradle module.",
    ),
    fresh: bool = typer.Option(
        False, "--fresh", help="Ignore cached Nemotron answers (risks, diagnosis, review) and ask again.",
    ),
) -> dict:
    """Analyze an event consumer and identify reliability risks."""
    _register_usage_print()

    console.print(
        f"\n[bold cyan]EventBreaker[/bold cyan] — analyzing "
        f"[yellow]{file}[/yellow]\n"
    )

    # ── Step 1: Static analysis ───────────────────────────────────────────────
    with console.status("[bold green]Running static analysis..."):
        try:
            analysis = analyze(file, method)
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
    if analysis.otherListenerMethods:
        console.print(
            f"[dim]Other listener methods in this class (not analysed): "
            f"{', '.join(analysis.otherListenerMethods)} — select one with --method[/dim]"
        )

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

    # --classpath wins; otherwise ask the listener's own build.
    # Resolved before any Nemotron call so a build problem costs nothing.
    jars = _parse_classpath(classpath) if classpath else _auto_classpath(file)

    # ── Step 2: Nemotron risk identification ──────────────────────────────────
    if not os.environ.get("NEBIUS_API_KEY"):
        console.print(
            "[bold yellow]NEBIUS_API_KEY not set — "
            "skipping AI risk identification.[/bold yellow]\n"
            "Export it to run the full analysis: "
            "[dim]export NEBIUS_API_KEY=<your-key>[/dim]"
        )
        raise typer.Exit(0)

    source_code = file.read_text()

    with console.status(
        "[bold green]Asking Nemotron to identify reliability risks..."
    ):
        try:
            scenarios, from_cache = identify_risks(analysis, source_code, fresh=fresh)
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
        f"[/bold green]"
        + (" [dim](cached response — use --fresh to re-ask Nemotron)[/dim]" if from_cache else "")
    )
    # Coverage must not depend on which risks Nemotron happened to propose.
    added = failure_scenarios(analysis, scenarios)
    scenarios = scenarios + added
    if added:
        console.print(f"[dim]+ {len(added)} failure scenario(s) added by code for untested dependency calls[/dim]")
    console.print()

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
    executor = ScenarioExecutor(jars)
    # Plan what the fakes must return so each scenario reaches its risky call,
    # and run the same setup without faults as a baseline.
    baselines: dict[str, ObservationResult] = {}
    setups: dict[str, list] = {}          # setup key → its overrides
    def run_baseline(values: list) -> ObservationResult:
        key = _values_key(values)
        setups[key] = values
        if key not in baselines:
            with console.status(f"[bold green]Executing fault-free baseline ({exec_mode})..."):
                baselines[key] = executor.execute(
                    ChaosScenario(
                        scenarioType="BASELINE", reason="fault-free run of the same setup",
                        expectedConcern="none",
                        testConfig=TestConfig(callPattern="SINGLE", returnValues=values),
                    ),
                    analysis, file,
                )
        return baselines[key]

    def tried() -> list:
        return [(setups[key], run) for key, run in baselines.items()]

    def missing_for(scenario: ChaosScenario, values: list) -> list[str]:
        baseline = run_baseline(values)
        return not_reached(baseline, target_calls(scenario)) if baseline.status == "REPRODUCED" else []

    def show_setup(scenario: ChaosScenario, values: list, note: str = "") -> None:
        if values:
            console.print(
                f"[dim]  Path setup for {scenario.scenarioType}{note}: "
                + ", ".join(f"{rv.target} → {rv.returns}" for rv in values)
                + "[/dim]"
            )

    planned = [sc for sc, _ in feasible if sc.testConfig is not None]
    inherited = inherited_source(file)   # parent classes' code, so path setup sees inherited helpers
    for scenario in planned:
        # A setup another scenario already proved reaches this target costs no LLM call.
        values = reusable_setup(target_calls(scenario), tried())
        if values is not None:
            scenario.testConfig.returnValues = values
            show_setup(scenario, values, " (reused)")
            continue
        with console.status(f"[bold green]Planning path setup: {scenario.scenarioType}..."):
            values = plan_path(analysis, source_code, scenario, inherited=inherited)
        missing = missing_for(scenario, values)
        if missing:
            # One retry: show Nemotron what its setup actually did.
            console.print(
                f"[dim]  Path setup did not reach {', '.join(missing)} — "
                f"asking Nemotron to revise it (1 retry)[/dim]"
            )
            feedback = reach_feedback(values, baselines[_values_key(values)], missing)
            with console.status(f"[bold green]Revising path setup: {scenario.scenarioType}..."):
                retry = plan_path(analysis, source_code, scenario, feedback=feedback, inherited=inherited)
            if retry and retry != values:
                values = retry
                missing = missing_for(scenario, values)
            if missing:
                forget_plan(analysis, source_code, scenario, feedback, inherited)
        scenario.testConfig.returnValues = values
        show_setup(scenario, values)

    # A scenario planned early may be reachable with a setup found for a later one.
    for scenario in planned:
        if missing_for(scenario, scenario.testConfig.returnValues):
            values = reusable_setup(target_calls(scenario), tried())
            if values is not None:
                scenario.testConfig.returnValues = values
                show_setup(scenario, values, " (reused from another scenario)")

    # A failure is only measurable if the run gets far enough for its effects to go missing:
    # prefer a setup that does everything this one does and more (e.g. a callback URL, so sends happen).
    for scenario in planned:
        values = deeper_setup(target_calls(scenario), scenario.testConfig.returnValues, tried())
        if values is not None:
            scenario.testConfig.returnValues = values
            show_setup(scenario, values, " (deeper setup from another scenario)")

    for scenario, plan in feasible:
        targets = target_calls(scenario)
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

        # A run that never called the scenario's target proves nothing about it.
        if result.status == "REPRODUCED":
            result.notReached = not_reached(result, targets)
            if result.notReached:
                result.status = "NOT_REACHED"
        console.print(f"[dim]  {reporter.describe_test(scenario).replace('`', '')}[/dim]")
        colour, mark = {
            "REPRODUCED": ("green", "✓"), "NOT_REACHED": ("yellow", "○"),
        }.get(result.status, ("red", "✗"))
        console.print(
            f"[bold {colour}]{mark} {scenario.scenarioType} — {result.status.replace('_', ' ')}"
            + (f" (never called {', '.join(result.notReached)})" if result.notReached else "")
            + f"[/bold {colour}]"
        )
        _print_observations(result, analysis)
        executions.append((scenario, result))

    if not executions:
        raise typer.Exit(0)

    console.print()

    # ── Step 5: Nemotron diagnosis for every reproduced scenario ──────────────
    # Closed questions first: what each dependency call does (3 votes), shared by all scenarios.
    reproduced = [(sc, r) for sc, r in executions if r.status == "REPRODUCED" and r.observations]
    # Every dependency call, not only those the scenarios hit: the question (and so its
    # answer) stays the same from run to run.
    methods = set(dependency_calls(analysis)) | {o.target for _, r in reproduced for o in r.observations} | {
        o.target for b in baselines.values() for o in b.observations
    }
    labels = {}
    if reproduced:
        with console.status("[bold green]Classifying dependency calls (Nemotron Super, 3 votes)..."):
            try:
                labels = label_calls(analysis, source_code, sorted(methods), fresh=fresh)
            except EnvironmentError as e:
                console.print(f"[bold red]Config error:[/bold red] {e}")
                raise typer.Exit(1)
        for m, lb in sorted(labels.items()):
            console.print(f"[dim]  {m}: {lb.describe()}[/dim]")

    all_findings: list[ReliabilityFinding] = []
    harmless: dict[str, list[str]] = {}
    for scenario, result in executions:
        if result.status != "REPRODUCED" or not result.observations:
            continue
        with console.status(
            f"[bold green]Diagnosing {scenario.scenarioType}..."
        ):
            try:
                baseline = baselines.get(_setup_key(scenario)) if scenario.testConfig else None
                if baseline is not None and baseline.status != "REPRODUCED":
                    baseline = None
                diagnosis = diagnose(analysis, scenario, result, source_code, labels,
                                     baseline=baseline, fresh=fresh)
                scenario_findings = diagnosis.findings
                if diagnosis.harmless:
                    harmless[scenario.scenarioType] = diagnosis.harmless
                if not scenario_findings:
                    console.print(
                        f"[dim]  {scenario.scenarioType}: no production risk observed[/dim]"
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

    all_findings = merge_findings(all_findings)

    # ── Step 5b: Nemotron Ultra reviews every finding against its evidence ───
    dismissed: list[ReliabilityFinding] = []
    if all_findings:
        with console.status("[bold green]Reviewing findings against the evidence (Nemotron Ultra)..."):
            try:
                all_findings, dismissed = review(analysis, source_code, all_findings, fresh=fresh)
            except Exception as e:  # the findings stand unreviewed rather than being lost
                console.print(f"[yellow]  Review skipped: {e}[/yellow]")
        for f in dismissed:
            console.print(f"[dim]  Reviewer dismissed {f.affectedMethod} ({f.scenario}): {f.reviewNote}[/dim]")
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
        dismissed=dismissed,
        harmless=harmless,
        labels=labels,
        theoretical=theoretical,
        sandbox=in_sandbox,
        baselines={
            sc.scenarioType: baselines[_setup_key(sc)]
            for sc, _ in executions
            if sc.testConfig is not None and _setup_key(sc) in baselines
            and baselines[_setup_key(sc)].status == "REPRODUCED"
        },
    )
    report_path = reporter.save(report_md, analysis, reporter.reports_dir(file))
    console.print(
        f"\n[bold green]✓ Report saved →[/bold green] [yellow]{report_path}[/yellow]\n"
        f"[dim]  Open it: open -a \"IntelliJ IDEA\" \"{report_path}\"  (or any Markdown viewer)[/dim]\n"
    )
    return {
        "report": report_path,
        "findings": all_findings,
        "reproduced": sum(1 for _, r in executions if r.status == "REPRODUCED"),
        "errors": sum(1 for _, r in executions if r.status == "ERROR"),
        "not_reached": sum(1 for _, r in executions if r.status == "NOT_REACHED"),
    }


@app.command("scan")
def scan_repository(
    directory: Path = typer.Argument(
        ..., help="Repository or module directory to search for listener methods.",
        exists=True, file_okay=False,
    ),
    run_all: bool = typer.Option(False, "--all", help="Analyze every listener found."),
    only: str | None = typer.Option(
        None, "--only",
        help="Analyze only these listeners: numbers from the list and/or Class.method names, comma-separated "
             "(e.g. --only 1,3 or --only NoteListener.onCreate).",
    ),
    classpath: str | None = typer.Option(
        None, "--classpath", help="Override the dependency jars for every listener (default: each listener's own module).",
    ),
    fresh: bool = typer.Option(False, "--fresh", help="Ignore cached Nemotron answers and ask again."),
) -> None:
    """List every listener method in a repository, and optionally analyze them."""
    _register_usage_print()
    with console.status("[bold green]Scanning for listener methods..."):
        try:
            listeners = scan(directory)
        except Exception as e:
            console.print(f"[bold red]Scan failed:[/bold red] {e}")
            raise typer.Exit(1)

    if not listeners:
        console.print(f"[yellow]No listener methods found under {directory}.[/yellow]")
        raise typer.Exit(0)

    table = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Listener", style="cyan", no_wrap=True)
    table.add_column("Event", style="yellow")
    table.add_column("File", style="dim")
    for i, l in enumerate(listeners, 1):
        table.add_row(str(i), f"{l.className}.{l.methodName}", l.eventType, Path(l.file).name)
    console.print(Panel(table, title=f"[bold]{len(listeners)} listener method(s) in {directory}[/bold]",
                        border_style="cyan"))

    selected = _select_listeners(listeners, run_all, only)
    if not selected:
        console.print("[dim]Analyze them with --all, or pick some with --only 1,3 / --only Class.method[/dim]")
        return

    summary: list[tuple] = []
    for n, l in enumerate(selected, 1):
        console.rule(f"[bold cyan]{n}/{len(selected)}  {l.className}.{l.methodName}[/bold cyan]")
        try:
            outcome = analyze_consumer(Path(l.file), method=l.methodName, classpath=classpath, fresh=fresh)
        except typer.Exit:
            outcome = None
        except Exception as e:
            console.print(f"[bold red]Failed:[/bold red] {e}")
            outcome = None
        summary.append((l, outcome))

    _print_scan_summary(summary)


def _select_listeners(listeners: list, run_all: bool, only: str | None) -> list:
    if run_all:
        return listeners
    if not only:
        return []
    chosen = []
    for token in (t.strip() for t in only.split(",") if t.strip()):
        if token.isdigit() and 1 <= int(token) <= len(listeners):
            match = [listeners[int(token) - 1]]
        else:
            match = [l for l in listeners if f"{l.className}.{l.methodName}" == token or l.className == token]
        if not match:
            raise typer.BadParameter(f"--only: no listener matches '{token}'")
        chosen += [m for m in match if m not in chosen]
    return chosen


def _print_scan_summary(summary: list[tuple]) -> None:
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold")
    t.add_column("Listener", style="cyan")
    t.add_column("Scenarios", justify="right")
    t.add_column("HIGH", justify="right", style="red")
    t.add_column("MEDIUM", justify="right", style="yellow")
    t.add_column("LOW", justify="right", style="green")
    t.add_column("Report", style="dim")
    for l, outcome in summary:
        name = f"{l.className}.{l.methodName}"
        if outcome is None:
            t.add_row(name, "[red]not run[/red]", "—", "—", "—", "—")
            continue
        sev = [f.severity for f in outcome["findings"]]
        ran = (
            f"{outcome['reproduced']} ok"
            + (f", [yellow]{outcome['not_reached']} not reached[/yellow]" if outcome["not_reached"] else "")
            + (f", [red]{outcome['errors']} error[/red]" if outcome["errors"] else "")
        )
        t.add_row(name, ran, str(sev.count("HIGH")), str(sev.count("MEDIUM")), str(sev.count("LOW")),
                  str(outcome["report"]).replace(str(Path.home()), "~", 1))
    console.print(Panel(t, title="[bold]Scan summary[/bold]", border_style="cyan"))


_usage_print_registered = False


def _register_usage_print() -> None:
    global _usage_print_registered
    if not _usage_print_registered:
        atexit.register(_print_usage)
        _usage_print_registered = True


def _setup_key(scenario: ChaosScenario) -> str:
    """Scenarios with the same path setup share one baseline run."""
    return _values_key(scenario.testConfig.returnValues if scenario.testConfig else [])


def _values_key(values: list) -> str:
    return "|".join(sorted(f"{rv.target}={rv.returns}" for rv in values))


def _print_usage() -> None:
    from eventbreaker.agent import usage
    if usage.session["calls"]:
        console.print(
            f"[dim]Nemotron usage this run: {usage.session['calls']} call(s), "
            f"{usage.session['input']:,} in / {usage.session['output']:,} out tokens, "
            f"~${usage.session['cost']:.4f} (all runs logged: ~${usage.total_logged():.2f} "
            f"in {usage.USAGE_FILE})[/dim]"
        )


def _auto_classpath(file: Path) -> list[Path]:
    """The listener module's classpath from Maven/Gradle."""
    module = find_module(file)
    if module is None:
        console.print(
            "[bold red]No classpath:[/bold red] this file is not under a Maven/Gradle module's "
            "src/main/java. Run EventBreaker on the listener inside its project, or pass the "
            "project's jars with --classpath."
        )
        raise typer.Exit(1)
    with console.status(
        f"[bold green]Resolving classpath for {module.dir.name} ({module.tool}; "
        f"first time per module can take a few minutes)..."
    ):
        try:
            jars = resolve(module)
        except ClasspathError as e:
            console.print(f"[bold red]Classpath error:[/bold red] {e}")
            raise typer.Exit(1)
    console.print(f"[bold green]✓ Classpath:[/bold green] {len(jars)} entries from {module.tool} "
                  f"([dim]{module.dir}[/dim])\n")
    return jars


def _parse_classpath(value: str | None) -> list[Path]:
    """'a.jar:b.jar' or '@cp.txt' (as written by mvn dependency:build-classpath)."""
    if not value:
        return []
    if value.startswith("@"):
        value = Path(value[1:]).read_text().strip()
    jars = [Path(p) for p in value.split(os.pathsep) if p.strip()]
    missing = [str(j) for j in jars if not j.exists()]
    if missing:
        raise typer.BadParameter(f"classpath entries not found: {', '.join(missing[:3])}")
    return jars


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
            obs_table.add_row(target, f"[red]{count}[/red]", f"[bold red]called {count}×[/bold red]")
        elif target in threw_targets:
            obs_table.add_row(target, f"[yellow]{count}[/yellow]", "[bold magenta]threw[/bold magenta]")
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
                f"[cyan]{f.affectedMethod}[/cyan]  [dim]({KIND_LABELS.get(f.kind, f.kind)})[/dim]\n"
                f"  {f.summary}\n"
                + (f"  [dim]Evidence: {'; '.join(f.evidence)}[/dim]\n" if f.evidence else "")
                + f"  [dim]{f.explanation}[/dim]\n"
                f"  [green]Fix: {f.suggestedFix}[/green]"
                + (f"\n  [dim]Severity basis: {f.severityNote}[/dim]" if f.severityNote else "")
                + (f"\n  [dim]Reviewer: {f.reviewNote}[/dim]" if f.reviewNote else "")
                + (f"\n  [dim]Also seen in: {', '.join(f.alsoSeenIn)}[/dim]" if f.alsoSeenIn else "")
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
