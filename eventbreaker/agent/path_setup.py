"""Path setup: what each fake must return so a scenario reaches its risky call.

Real listeners branch on strings, enums, nulls and casts to concrete classes;
default fakes can't satisfy those, so execution would stop before the side
effect under test. Nemotron Super reads the source
and returns a small list of ReturnValue overrides in a fixed vocabulary — the
executor turns them into fake answers (no LLM-written code).
"""
import hashlib
import json
import os
import re
from pathlib import Path

from openai import OpenAI

from eventbreaker.agent import usage
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationResult
from eventbreaker.scenarios.models import ChaosScenario, ReturnValue

_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
_CACHE_DIR = Path.home() / ".eventbreaker" / "cache" / "path_setup"

_SYSTEM_PROMPT = """\
You plan test setups for a Java event listener that runs with all dependencies replaced by fakes.
By default fakes return: true for booleans, 1 for numbers, "" for String, the FIRST constant for enums,
a one-element array/list of fakes for arrays and collections, and otherwise a fake that implements the
DECLARED return type (so a cast to a concrete subclass fails with ClassCastException).
Your job: list the minimal return-value overrides needed so that, for the given event, execution reaches the
TARGET call. Read the source carefully: string equality checks, enum comparisons, null checks, casts to
concrete classes, early returns.
Target syntax: "<dep>.<method>" for a dependency call, or a getter path starting at a dependency or "event",
e.g. "event.getTagDefinition.getName" (method names only, no parentheses or arguments).
Value vocabulary: true | false | <number> | STRING:<text> | ENUM:<CONSTANT_NAME> | MOCK:<ConcreteClassSimpleName> | NULL
Return ONLY a JSON array: [{"target": "...", "returns": "...", "why": "<one short reason>"}]. Return [] if no override is needed."""

_VALUE = re.compile(r"^(true|false|NULL|-?\d+(\.\d+)?|STRING:.*|ENUM:[A-Za-z_]\w*|MOCK:[A-Za-z_][\w.]*)$")


def target_calls(scenario: ChaosScenario) -> list[str]:
    """The dep.method calls a scenario is about: its injected faults, else its declared target."""
    tc = scenario.testConfig
    if tc and tc.faultInjections:
        return [f"{fi.dep}.{fi.method}" for fi in tc.faultInjections]
    if scenario.targetDependency and scenario.targetMethod:
        return [f"{scenario.targetDependency}.{scenario.targetMethod}"]
    return []


def not_reached(result: ObservationResult, targets: list[str]) -> list[str]:
    """Targets the run never called (empty when all were reached, or there are no targets)."""
    called = {o.target for o in result.observations if o.callCount > 0}
    return [t for t in targets if t not in called]


def reusable_setup(
    targets: list[str], tried: list[tuple[list[ReturnValue], ObservationResult]],
) -> list[ReturnValue] | None:
    """A setup whose fault-free baseline already reached every target (fewest overrides first), else None.

    tried: (overrides, baseline run) for each setup executed so far. Reusing one costs no LLM call.
    """
    if not targets:
        return None
    reaching = [values for values, baseline in tried
                if baseline.status == "REPRODUCED" and not not_reached(baseline, targets)]
    return min(reaching, key=len) if reaching else None


def reach_feedback(values: list[ReturnValue], result: ObservationResult, missing: list[str]) -> str:
    """What happened with the previous setup — sent back to Nemotron for one retry."""
    tried = ", ".join(f"{v.target} → {v.returns}" for v in values) or "(no overrides)"
    calls = " → ".join(result.callSequence) or "(no dependency calls at all)"
    stop = (
        f"The listener threw: {result.consumerException}"
        if result.consumerException else
        "The listener returned normally (a guard or branch skipped the target)."
    )
    return (
        "A previous setup was executed and did NOT reach the target.\n"
        f"Previous overrides: {tried}\n"
        f"Calls that happened, in order: {calls}\n"
        f"{stop}\n"
        f"Never called: {', '.join(missing)}\n"
        "Find what stopped execution before the target (string/enum checks on the event, null checks, "
        "casts, early returns) and return a COMPLETE corrected list of overrides."
    )


def plan_path(
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario,
    feedback: str | None = None,
) -> list[ReturnValue]:
    """Return validated ReturnValue overrides for this scenario ([] if none needed or on any failure).

    feedback: what happened when a previous setup was executed (see reach_feedback);
    used for one retry when the target was not reached.
    """
    user = _prompt(analysis, source_code, scenario, feedback)
    if not user:
        return []
    cache_file = _cache_file(user)
    if cache_file.exists():
        raw = cache_file.read_text()
    else:
        api_key = os.environ.get("NEBIUS_API_KEY")
        if not api_key:
            return []
        try:
            response = OpenAI(base_url=_BASE_URL, api_key=api_key).chat.completions.create(
                model=_MODEL,
                messages=[{"role": "system", "content": _SYSTEM_PROMPT},
                          {"role": "user", "content": user}],
                temperature=0.2,
                max_tokens=8192,
            )
        except Exception:
            return []
        usage.record(_MODEL, getattr(response, "usage", None), "path_setup")
        msg = response.choices[0].message
        raw = msg.content or getattr(msg, "reasoning_content", None) or ""
        if _parse(raw, analysis, source_code) is not None:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(raw)
    return _parse(raw, analysis, source_code) or []


def forget_plan(
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario, feedback: str,
) -> None:
    """Drop a cached retry answer that did not reach the target, so the next run asks again."""
    user = _prompt(analysis, source_code, scenario, feedback)
    if user:
        _cache_file(user).unlink(missing_ok=True)


def _prompt(
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario, feedback: str | None,
) -> str:
    """The user prompt for this scenario ("" when it has no target call)."""
    target = _target(scenario)
    if not target:
        return ""
    deps = "\n".join(f"  {d.name}: {d.type}" for d in analysis.dependencies)
    return (
        f"Listener method: {analysis.className}.{analysis.methodName}({analysis.eventType} event)\n"
        f"Dependencies (field: type):\n{deps}\n"
        f"TARGET call that must be reached: {target}\n"
        f"Scenario concern: {scenario.expectedConcern}\n\n"
        + (f"{feedback}\n\n" if feedback else "")
        + f"Source:\n{source_code}"
    )


def _cache_file(user: str) -> Path:
    key = hashlib.sha256(f"{_MODEL}\0{_SYSTEM_PROMPT}\0{user}".encode()).hexdigest()[:16]
    return _CACHE_DIR / f"{key}.json"


def _target(scenario: ChaosScenario) -> str:
    """The call the scenario is about, as prompt text."""
    return ", ".join(target_calls(scenario))


def _parse(raw: str, analysis: ConsumerAnalysis, source_code: str) -> list[ReturnValue] | None:
    """Validated overrides, or None if the response is not a JSON array."""
    match = re.search(r"\[.*\]", raw, re.S)
    if not match:
        return None
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    roots = {d.name for d in analysis.dependencies} | {"event"}
    # Names inside string literals and comments are not types the consumer uses.
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', " ", source_code, flags=re.S)
    values: list[ReturnValue] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target", "")).replace("()", "").strip()
        returns = str(item.get("returns", "")).strip()
        if target.split(".")[0] not in roots or "." not in target or not _VALUE.match(returns):
            continue
        # A MOCK type must be one the consumer actually names (compile-safe, no invention).
        if returns.startswith("MOCK:") and not re.search(rf"\b{re.escape(returns[5:].split('.')[-1])}\b", code):
            continue
        values.append(ReturnValue(target=target, returns=returns))
    return values
