import json
import os
import re

from openai import OpenAI
from pydantic import ValidationError

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.scenarios.models import ChaosScenario

_MODEL = "nvidia/nemotron-3-super-120b-a12b"
_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

_SYSTEM_PROMPT = """\
You are an expert in distributed systems reliability and event-driven architecture.

You will receive:
1. A Java event consumer's structure (class, method, dependencies, call order)
2. The source code of that consumer
3. A chaos scenario that was executed against it
4. Observations — for each dependency method: how many times it was called, and whether it threw

Your task: produce a JSON array of ReliabilityFinding objects — one for EACH \
method listed as a concern target. Use the observation signals to reason:
- called > 1: the method executed multiple times — explain the duplicate-call risk
- called = 0: the method was never reached — explain the skipped-call risk
- threw = true: the method was called but threw — explain what was left inconsistent
- called = 1, threw = false: normal execution — explain the atomicity or sequencing risk
  (e.g. this call succeeded but a later call failed, leaving state partially committed)

Each finding must use this exact format:
{
  "scenario": "<the scenario type>",
  "summary": "<one sentence: what was concretely observed for this specific method>",
  "explanation": "<2-3 sentences: why this specific method being called N times or being skipped is a production risk>",
  "affectedMethod": "<dependency.method for this finding, e.g. paymentClient.charge>",
  "suggestedFix": "<concrete fix referencing the actual code for this specific method>",
  "severity": "<HIGH | MEDIUM | LOW | NONE>"
}

Judge whether each observation is a real production risk. Decide from the consumer's source code what each dependency call does to external state — do not assume a risk just because a method ran more than once or an exception occurred:
- READ: returns data and changes nothing (a lookup or query). Running it again is harmless.
- IDEMPOTENT WRITE: repeating it with the same input leaves the same state (overwriting a value under the same key, setting a field, an upsert).
- NON-IDEMPOTENT SIDE EFFECT: repeating it changes state again or is visible externally (charging, sending a message or email, publishing an event, inserting a row, incrementing, reserving, appending).

Severity rules:
- A READ called more than once → severity NONE.
- An IDEMPOTENT WRITE repeated with the same input → severity NONE. Under concurrency, at most LOW, and only if the code shows two deliveries could write different values in the wrong order.
- A NON-IDEMPOTENT SIDE EFFECT executed more than once → HIGH (money, inventory, external messages) or MEDIUM.
- The consumer threw (the exception propagated, so the broker will redeliver) and the only calls that completed before the failure were READs or IDEMPOTENT WRITEs → severity NONE. This is the safe outcome: nothing was left half-done and the retry repeats it cleanly.
- A NON-IDEMPOTENT SIDE EFFECT completed before a failure → HIGH or MEDIUM: the retry repeats it, or state is left partially committed.
- The consumer did NOT throw (it completed or swallowed the exception), but a call failed or was skipped → the message is acknowledged and that work is lost: HIGH or MEDIUM, unless the skipped work is only a READ.
- LOW is for real but minor risks (extra latency, a harmless log line written twice).
- Use NONE whenever the observation is the expected, safe behaviour. A finding with severity NONE is discarded and not shown to the user, so do not inflate severity to make it count.

Rules:
- Produce one finding per concerning method — do not merge multiple methods into one finding.
- Reference actual class names, field names, and method names from the consumer code.
- suggestedFix must be actionable — name the specific check or pattern (idempotency key, existence check, outbox pattern, compensating transaction, etc).
- affectedMethod MUST be exactly "dependencyFieldName.methodName" — e.g. "paymentClient.charge".
  NO parentheses, NO arguments, NO full expressions. Just field name dot method name.
- Return ONLY the JSON array — no explanation, no markdown fences, no extra text.\
"""


def _has_observation_signal(result: ObservationResult) -> bool:
    """Return True if observations contain at least one anomalous signal.

    A signal means: any method called more than once, any method that threw,
    or any expected dependency method that was never called (skipped).
    Without a signal, diagnosis would be speculative rather than evidence-based.
    """
    return any(o.callCount > 1 or o.threw for o in result.observations) or result.consumerThrew


def _failed_before_any_completed_call(result: ObservationResult) -> bool:
    """Return True if the consumer threw and no dependency call completed without throwing.

    Uses only the observed calls, never method names: if every call that ran threw,
    no external state was changed before the exception propagated.
    """
    completed = [o for o in result.observations if o.callCount > 0 and not o.threw]
    return result.consumerThrew and not completed


def diagnose(
    analysis: ConsumerAnalysis,
    scenario: ChaosScenario,
    result: ObservationResult,
    source_code: str,
) -> list[ReliabilityFinding]:
    """Send observations to Nemotron and return one ReliabilityFinding per concern.

    Returns an empty list if there is no observation signal to diagnose.
    Raises EnvironmentError if NEBIUS_API_KEY is not set.
    Raises ValueError if Nemotron returns nothing usable.
    """
    # No anomalous signal — diagnosing would produce speculative, low-quality findings.
    dep_names = {d.name for d in analysis.dependencies}
    observed_targets = {o.target for o in result.observations}
    expected_dep_calls = [
        c.expression for c in analysis.methodCalls
        if c.scope in dep_names
    ]
    skipped = [
        expr for expr in expected_dep_calls
        if not any(
            expr.startswith(t) or (
                t.split(".")[0] == expr.split(".")[0]
                and t.split(".")[-1] in expr
            )
            for t in observed_targets
        )
    ]
    if not _has_observation_signal(result) and not skipped:
        return []

    # The exception reached the broker before any dependency call completed:
    # nothing was changed, so redelivery repeats the work cleanly. Not a finding.
    if _failed_before_any_completed_call(result):
        return []

    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "NEBIUS_API_KEY environment variable is not set."
        )

    client = OpenAI(base_url=_BASE_URL, api_key=api_key)

    response = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(analysis, scenario, result, source_code)},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    message = response.choices[0].message
    raw = message.content
    if raw is None:
        raw = getattr(message, "reasoning_content", None)
    if raw is None:
        raise ValueError("Nemotron returned no content for diagnosis.")

    return _parse(raw.strip(), scenario, result)


def _build_prompt(
    analysis: ConsumerAnalysis,
    scenario: ChaosScenario,
    result: ObservationResult,
    source_code: str,
) -> str:
    deps = ", ".join(f"{d.name} ({d.type})" for d in analysis.dependencies)
    calls = " → ".join(c.expression for c in analysis.methodCalls)

    obs_lines = "\n".join(
        f"  {o.target}: called {o.callCount} time(s)"
        + (" [THREW]" if o.threw else "")
        for o in result.observations
    )
    doubled = [o for o in result.observations if o.callCount > 1]
    doubled_summary = (
        ", ".join(f"{o.target} ({o.callCount}x)" for o in doubled) or "none"
    )
    consumer_threw_line = (
        "Consumer threw an exception: YES (it propagated — the broker will redeliver the event)"
        if result.consumerThrew
        else "Consumer threw an exception: NO (the handler completed or swallowed the exception "
        "— the event is acknowledged)"
    )
    call_seq_line = (
        "Call sequence (in order): " + " → ".join(result.callSequence)
        if result.callSequence else "Call sequence: (empty — consumer may have thrown before any dep call)"
    )

    # Compute which dep methods were skipped entirely (never appeared in observations).
    observed_targets = {o.target for o in result.observations}
    dep_names = {d.name for d in analysis.dependencies}
    expected_dep_calls = [
        c.expression for c in analysis.methodCalls
        if c.scope in dep_names
    ]
    skipped = [
        expr for expr in expected_dep_calls
        if not any(expr.startswith(t) or t.split(".")[0] == expr.split(".")[0]
                   and t.split(".")[-1] in expr
                   for t in observed_targets)
    ]
    threw = [o for o in result.observations if o.threw]

    # Determine which methods need a finding based on the actual observation signals.
    if doubled:
        concern_targets = [o.target for o in doubled]
        concern_description = "called more than once"
        skipped_section = ""
    elif skipped:
        concern_targets = skipped
        concern_description = "skipped after the failure"
        skipped_section = (
            "\nDependency calls SKIPPED after failure (never executed):\n"
            + "\n".join(f"  {s}" for s in skipped)
        )
    elif threw:
        # A method was called but threw — focus on what preceded it (committed)
        # and the method that threw (lost the operation).
        committed = [o.target for o in result.observations if not o.threw and o.callCount > 0]
        concern_targets = committed + [o.target for o in threw]
        concern_description = "executed before or at the failure point"
        skipped_section = (
            "\nMethods that THREW an exception:\n"
            + "\n".join(f"  {o.target}" for o in threw)
            + "\nMethods that committed successfully before the throw:\n"
            + "\n".join(f"  {t}" for t in committed)
        )
    else:
        # No strong anomaly signal — only report on methods directly relevant
        # to the scenario concern (avoid findings for healthy, unrelated methods).
        concern_targets = [o.target for o in result.observations if o.callCount > 0][:2]
        concern_description = "observed"
        skipped_section = ""

    target_list = "\n".join(f"  - {t}" for t in concern_targets)

    return f"""\
Consumer:     {analysis.className}.{analysis.methodName}
Event type:   {analysis.eventType}
Dependencies: {deps}
Call order:   {calls}

Scenario type:    {scenario.scenarioType}
Scenario reason:  {scenario.reason}
Expected concern: {scenario.expectedConcern}

Observations (call counts after scenario execution):
{obs_lines}

{consumer_threw_line}
{call_seq_line}

Methods called more than once: {doubled_summary}{skipped_section}

Source code:
{source_code}

IMPORTANT: Your findings must specifically address the concern above ("{scenario.expectedConcern}").
Do not produce generic idempotency findings if this scenario is about something else \
(e.g. silent data loss, transaction boundaries, partial failures).
Tailor the summary, explanation, and suggestedFix to this specific concern.

These dependency methods were {concern_description} and each needs its own finding:
{target_list}

Produce exactly {len(concern_targets)} ReliabilityFinding objects — one per method listed above.
Apply the severity rules: use NONE for any method whose observation is not a production risk.
Return a JSON array of exactly {len(concern_targets)} findings."""


def _parse(
    raw: str,
    scenario: ChaosScenario,
    result: ObservationResult,
) -> list[ReliabilityFinding]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Accept either a JSON array or a single object (wrap single object in list)
    start_arr = cleaned.find("[")
    start_obj = cleaned.find("{")

    if start_arr != -1 and (start_obj == -1 or start_arr < start_obj):
        end = cleaned.rfind("]") + 1
        if end == 0:
            raise ValueError(
                f"Nemotron did not return a complete JSON array.\nRaw response:\n{raw}"
            )
        try:
            data = json.loads(cleaned[start_arr:end])
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Nemotron returned invalid JSON: {e}\nRaw response:\n{raw}"
            ) from e
        items = data if isinstance(data, list) else [data]
    elif start_obj != -1:
        end = cleaned.rfind("}") + 1
        try:
            items = [json.loads(cleaned[start_obj:end])]
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Nemotron returned invalid JSON: {e}\nRaw response:\n{raw}"
            ) from e
    else:
        raise ValueError(
            f"Nemotron did not return a JSON object or array.\nRaw response:\n{raw}"
        )

    findings: list[ReliabilityFinding] = []
    benign = 0
    for item in items:
        if str(item.get("severity", "")).upper() == "NONE":
            benign += 1
            continue
        item.setdefault("scenario", scenario.scenarioType)
        if not item.get("affectedMethod") and result.observations:
            top = max(result.observations, key=lambda o: o.callCount)
            item["affectedMethod"] = top.target
        # The model sometimes copies the scenario's target into every finding.
        # When the summary names exactly one observed dep.method, trust the summary.
        mentioned = [
            o.target for o in result.observations
            if o.target in str(item.get("summary", ""))
        ]
        if len(mentioned) == 1 and item.get("affectedMethod") != mentioned[0]:
            item["affectedMethod"] = mentioned[0]
        try:
            findings.append(ReliabilityFinding.model_validate(item))
        except ValidationError:
            continue

    if not findings and not benign:
        raise ValueError(
            f"No valid ReliabilityFinding objects in Nemotron response.\n"
            f"Raw response:\n{raw}"
        )

    return findings
