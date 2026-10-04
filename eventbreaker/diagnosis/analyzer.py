import json
import os
import re

from openai import OpenAI
from pydantic import ValidationError

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationResult, ReliabilityFinding
from eventbreaker.scenarios.models import ChaosScenario

_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

_SYSTEM_PROMPT = """\
You are an expert in distributed systems reliability and event-driven architecture.

You will receive:
1. A Java event consumer's structure (class, method, dependencies, call order)
2. The source code of that consumer
3. A chaos scenario that was executed against it
4. Observations — how many times each dependency method was called

Your task: produce a single ReliabilityFinding JSON object with this exact format:
{
  "scenario": "<the scenario type, e.g. DUPLICATE_EVENT>",
  "summary": "<one sentence: what was concretely observed, referencing actual method names and call counts>",
  "explanation": "<2-3 sentences: why this is a real production risk, what could go wrong for end users>",
  "affectedMethod": "<the single most critical dependency.method, e.g. paymentClient.charge>",
  "suggestedFix": "<concrete fix referencing the actual code: what to check, add, or change and where>",
  "severity": "<HIGH | MEDIUM | LOW>"
}

Rules:
- Reference actual class names, field names, and method names from the consumer code.
- suggestedFix must be actionable — name the specific check or pattern to add (e.g. idempotency key, \
existence check, try/catch, outbox pattern).
- Return ONLY the JSON object — no explanation, no markdown fences, no extra text.\
"""


def diagnose(
    analysis: ConsumerAnalysis,
    scenario: ChaosScenario,
    result: ObservationResult,
    source_code: str,
) -> ReliabilityFinding:
    """Send observations to Nemotron and return a structured ReliabilityFinding.

    Raises EnvironmentError if NEBIUS_API_KEY is not set.
    Raises ValueError if Nemotron returns nothing usable.
    """
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
        max_tokens=1024,
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
        for o in result.observations
    )
    doubled = [o for o in result.observations if o.callCount > 1]
    doubled_summary = ", ".join(
        f"{o.target} ({o.callCount}x)" for o in doubled
    ) or "none"

    return f"""\
Consumer:     {analysis.className}.{analysis.methodName}
Event type:   {analysis.eventType}
Dependencies: {deps}
Call order:   {calls}

Scenario executed: {scenario.scenarioType.value}
Scenario reason:   {scenario.reason}

Observations (call counts after scenario execution):
{obs_lines}

Methods called more than once: {doubled_summary}

Source code:
{source_code}

Produce a ReliabilityFinding JSON object for the most critical issue observed."""


def _parse(
    raw: str,
    scenario: ChaosScenario,
    result: ObservationResult,
) -> ReliabilityFinding:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError(
            f"Nemotron did not return a JSON object.\nRaw response:\n{raw}"
        )

    try:
        data = json.loads(cleaned[start:end])
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Nemotron returned invalid JSON: {e}\nRaw response:\n{raw}"
        ) from e

    # Fill in scenario from context if Nemotron omits it
    data.setdefault("scenario", scenario.scenarioType.value)

    # Fall back to most-called method if affectedMethod is missing
    if not data.get("affectedMethod") and result.observations:
        top = max(result.observations, key=lambda o: o.callCount)
        data["affectedMethod"] = top.target

    try:
        return ReliabilityFinding.model_validate(data)
    except ValidationError as e:
        raise ValueError(
            f"Nemotron response did not match ReliabilityFinding schema: {e}\n"
            f"Raw response:\n{raw}"
        ) from e
