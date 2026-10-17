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
from eventbreaker.scenarios.executor import constructor_params, static_helpers, value_params
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
| LIST:<value> (a one-element list/set holding a true/false/number/STRING/ENUM value, e.g. LIST:STRING:https://example.com/hook)
Return ONLY a JSON array: [{"target": "...", "returns": "...", "why": "<one short reason>"}]. Return [] if no override is needed."""

_SCALAR = r"true|false|-?\d+(\.\d+)?|STRING:.*|ENUM:[A-Za-z_]\w*"
_VALUE = re.compile(rf"^({_SCALAR}|NULL|MOCK:[A-Za-z_][\w.]*|LIST:({_SCALAR}))$")


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


def _called(run: ObservationResult) -> set[str]:
    return {o.target for o in run.observations if o.callCount > 0}


def deeper_setup(
    targets: list[str], values: list[ReturnValue], tried: list[tuple[list[ReturnValue], ObservationResult]],
) -> list[ReturnValue] | None:
    """A setup whose baseline makes every call this one's does and more, still reaching every target; else None.

    A failure only shows what it loses if the run gets far enough for that work to happen at all:
    with an empty callback list no webhook is ever sent, so "the lookup failed" loses nothing measurable.
    Requiring a superset of calls keeps the scenario on the same path; it only adds downstream work.
    """
    current = next((b for v, b in tried if v == values), None)
    if current is None or current.status != "REPRODUCED":
        return None
    base = _called(current)
    deeper = [(v, b) for v, b in tried
              if b.status == "REPRODUCED" and not not_reached(b, targets) and _called(b) > base]
    return max(deeper, key=lambda vb: (len(_called(vb[1])), -len(vb[0])))[0] if deeper else None


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
    feedback: str | None = None, inherited: str = "",
) -> list[ReturnValue]:
    """Return validated ReturnValue overrides for this scenario ([] if none needed or on any failure).

    feedback: what happened when a previous setup was executed (see reach_feedback);
    used for one retry when the target was not reached.
    inherited: source of the listener's parent classes in the same project (see inherited_source).
    """
    user = _prompt(analysis, source_code, scenario, feedback, inherited)
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
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario, feedback: str, inherited: str = "",
) -> None:
    """Drop a cached retry answer that did not reach the target, so the next run asks again."""
    user = _prompt(analysis, source_code, scenario, feedback, inherited)
    if user:
        _cache_file(user).unlink(missing_ok=True)


def _prompt(
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario, feedback: str | None,
    inherited: str = "",
) -> str:
    """The user prompt for this scenario ("" when it has no target call)."""
    target = _target(scenario)
    if not target:
        return ""
    deps = "\n".join(f"  {d.name}: {d.type}" for d in analysis.dependencies)
    # Only listeners with plain-value parameters get this section, so other prompts (and their
    # cached answers) are unchanged.
    params = value_params(analysis)
    param_lines = (
        "Other listener parameters (plain values; set one with target \"param.<name>\", default as for fakes):\n"
        + "\n".join(f"  param.{p.name}: {p.type}" for p in params) + "\n"
    ) if params else ""
    statics = [f.rsplit(".", 1)[-1] for f in static_helpers(source_code)]
    static_lines = (
        "Static helpers of the project (run for real; if one fails because no application is running, it "
        "answers like a fake; override with target \"<Class>.<method>[.<getter>…]\"): "
        + ", ".join(statics) + "\n"
    ) if statics else ""
    ctor_only = _constructor_only(analysis, source_code)
    ctor_lines = (
        "Constructor arguments that are not fields (faked too; override as \"<name>.<getter>…\"):\n"
        + "\n".join(f"  {name}: {ptype}" for ptype, name in ctor_only) + "\n"
    ) if ctor_only else ""
    return (
        f"Listener method: {analysis.className}.{analysis.methodName}({analysis.eventType} event)\n"
        f"Dependencies (field: type):\n{deps}\n"
        + param_lines
        + static_lines
        + ctor_lines
        + f"TARGET call that must be reached: {target}\n"
        f"Scenario concern: {scenario.expectedConcern}\n\n"
        + (f"{feedback}\n\n" if feedback else "")
        + f"Source:\n{source_code}"
        # Only listeners with a project parent class get this section, so other prompts are unchanged.
        + (f"\n\nInherited code (the listener's parent classes; methods it calls without a receiver may be "
           f"here):\n{inherited}" if inherited else "")
    )


def _constructor_only(analysis: ConsumerAnalysis, source_code: str) -> list[tuple[str, str]]:
    """Constructor parameters that are not injected fields (e.g. passed to super, or used to look a service up)."""
    fields = {d.name for d in analysis.dependencies}
    return [(t, n) for t, n in constructor_params(source_code, analysis.className) if n not in fields]


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
    roots = ({d.name for d in analysis.dependencies} | {"event"} | ({"param"} if value_params(analysis) else set())
             | {f.rsplit(".", 1)[-1] for f in static_helpers(source_code)}
             | {name for _, name in _constructor_only(analysis, source_code)})
    # Names inside string literals and comments are not types the consumer uses.
    code = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', " ", source_code, flags=re.S)
    values: list[ReturnValue] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        # Method names only: the model sometimes writes the call with its argument ("get(\"topic\")").
        target = str(item.get("target", "")).strip()
        while re.search(r"\([^()]*\)", target):
            target = re.sub(r"\([^()]*\)", "", target)
        returns = str(item.get("returns", "")).strip()
        if target.split(".")[0] not in roots or "." not in target or not _VALUE.match(returns):
            continue
        # A MOCK type must be one the consumer actually names (compile-safe, no invention).
        if returns.startswith("MOCK:") and not re.search(rf"\b{re.escape(returns[5:].split('.')[-1])}\b", code):
            continue
        values.append(ReturnValue(target=target, returns=returns))
    return values
