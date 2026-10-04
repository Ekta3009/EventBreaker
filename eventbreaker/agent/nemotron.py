import json
import os
import re

from openai import OpenAI
from pydantic import ValidationError

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario
from eventbreaker.scenarios.validator import validate

_MODEL = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"

_SYSTEM_PROMPT = """\
You are an expert in distributed systems reliability and event-driven architecture.

Your task: analyze a Java event consumer and identify the most important reliability \
risks that could occur under real distributed-system conditions — duplicate event \
delivery, out-of-order events, downstream service failures, and partial processing \
or crashes mid-method.

For each risk, produce a ChaosScenario object in this exact JSON format:
{
  "scenarioType": "<DUPLICATE_EVENT | OUT_OF_ORDER | DOWNSTREAM_FAILURE | CRASH_AFTER_SIDE_EFFECT>",
  "reason": "<specific reason this is a risk based on this consumer's actual code>",
  "targetDependency": "<the dependency field name most relevant, e.g. paymentClient>",
  "targetMethod": "<the method name most relevant, e.g. charge>",
  "actions": [
    {"type": "<DELIVER_EVENT | INJECT_FAILURE | INJECT_DELAY | CRASH_AFTER>", "target": "<optional: specific target>"}
  ],
  "expectedConcern": "<one sentence: what the developer should verify>"
}

Rules:
- Only use the exact scenarioType and action type values shown above. No others.
- Reference actual field names and method names from the consumer code.
- Return 2 to 4 scenarios prioritising the highest-severity risks.
- Return ONLY a valid JSON array — no explanation, no markdown fences, no extra text.\
"""


def identify_risks(
    analysis: ConsumerAnalysis,
    source_code: str,
) -> list[ChaosScenario]:
    """
    Sends the consumer analysis to Nemotron and returns validated ChaosScenarios.
    Raises EnvironmentError if NEBIUS_API_KEY is not set.
    Raises ValueError if Nemotron returns nothing usable.
    """
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "NEBIUS_API_KEY environment variable is not set. "
            "Export it before running: export NEBIUS_API_KEY=<your-key>"
        )

    client = OpenAI(base_url=_BASE_URL, api_key=api_key)

    response = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_prompt(analysis, source_code)},
        ],
        temperature=0.2,
        max_tokens=4096,
    )

    message = response.choices[0].message

    # Nemotron reasoning mode puts the answer in content and thinking in a
    # separate field. If content is None, fall back to reasoning_content
    # (some model/SDK combinations surface only the reasoning text).
    raw = message.content
    if raw is None:
        raw = getattr(message, "reasoning_content", None)
    if raw is None:
        finish_reason = response.choices[0].finish_reason
        raise ValueError(
            f"Nemotron returned no text content "
            f"(finish_reason={finish_reason!r}). "
            "Check that the model endpoint is reachable and the prompt is valid."
        )

    return _parse(raw.strip())


def _build_prompt(analysis: ConsumerAnalysis, source_code: str) -> str:
    deps = ", ".join(
        f"{d.name} ({d.type})" for d in analysis.dependencies
    )
    calls = " → ".join(c.expression for c in analysis.methodCalls)

    return f"""\
Analyze this Java event consumer for reliability risks.

Consumer:   {analysis.className}.{analysis.methodName}
Event type: {analysis.eventType}
Dependencies: {deps}
Method calls in order: {calls}

Source code:
{source_code}

Return a JSON array of ChaosScenarios for the most important risks."""


def _parse(raw: str) -> list[ChaosScenario]:
    # Strip markdown code fences if the model wrapped the JSON
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

    # Find the JSON array boundaries
    start = cleaned.find("[")
    end = cleaned.rfind("]") + 1
    if start == -1 or end == 0:
        raise ValueError(
            f"Nemotron did not return a JSON array.\nRaw response:\n{raw}"
        )

    try:
        data = json.loads(cleaned[start:end])
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Nemotron returned invalid JSON: {e}\nRaw response:\n{raw}"
        )

    scenarios = []
    for item in data:
        try:
            scenarios.append(ChaosScenario.model_validate(item))
        except ValidationError:
            # Skip items that don't match our schema
            continue

    validated = validate(scenarios)
    if not validated:
        raise ValueError(
            "No valid scenarios remained after schema validation.\n"
            f"Raw response:\n{raw}"
        )

    return validated
