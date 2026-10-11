import hashlib
import json
import os
import re
from pathlib import Path

from openai import OpenAI
from pydantic import ValidationError

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig

_MODEL = "nvidia/Nemotron-3-Ultra-550b-a55b"
_BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
_CACHE_DIR = Path.home() / ".eventbreaker" / "cache" / "risks"

_SYSTEM_PROMPT = """\
You are an expert in distributed systems reliability and event-driven architecture.

Analyze a Java event consumer and identify the most important reliability risks.
Think broadly: duplicate delivery, downstream failures, partial processing,
missing idempotency guards, silent data loss, race conditions, or any other
concern visible in the code.

For each risk, return a JSON object in this EXACT format:
{
  "scenarioType": "<ALL_CAPS label, e.g. IDEMPOTENCY_VIOLATION, SILENT_DATA_LOSS>",
  "reason": "<specific reason based on this consumer's actual code>",
  "targetDependency": "<exact dependency field name>",
  "targetMethod": "<exact method name on that dependency>",
  "expectedConcern": "<one sentence: what the developer should verify>",
  "testConfig": {
    "callPattern": "<DUPLICATE | SINGLE | CONCURRENT>",
    "faultInjections": [],
    "observeOrdering": false,
    "detectSwallowing": false
  }
}

callPattern rules:
  DUPLICATE   — deliver the same event twice with NO fault injection.
                Use when the risk is: what happens if this consumer runs twice?
                faultInjections MUST be empty [].

  SINGLE      — deliver once WITH fault injection on a specific dependency method.
                Use when the risk is: what if dependency X fails mid-processing?
                faultInjections MUST have at least one entry.

  CONCURRENT  — two threads deliver the event simultaneously.
                Use when the risk is a race condition.
                faultInjections may be empty.

faultInjections rules — CRITICAL:
  - dep:    MUST be one of the exact field names listed under "Dependency field names"
  - method: MUST be one of the exact method names listed under "Methods per dependency"
  - fault:  MUST be exactly THROW, THROW_ONCE, or DELAY
  - THROW:      method always throws RuntimeException
  - THROW_ONCE: method throws once, succeeds on retry
  - DELAY:      method sleeps before completing

observeOrdering: set true ONLY if the risk is about call ORDER (e.g. commit before begin).
detectSwallowing: set true ONLY if the risk is the consumer silently catching an exception.

STRICT RULES:
  - dep and method MUST exactly match the names provided — do NOT invent or guess names.
  - faultInjections MUST be [] when callPattern is DUPLICATE.
  - Return ONLY a valid JSON array — no explanation, no markdown fences, no extra text.
  - Return 2 to 4 scenarios, highest severity first.\
"""


def identify_risks(
    analysis: ConsumerAnalysis,
    source_code: str,
    fresh: bool = False,
) -> tuple[list[ChaosScenario], bool]:
    """Send consumer analysis to Nemotron Ultra and return validated ChaosScenarios.

    Each returned scenario has testConfig populated. The raw response is cached,
    keyed by model + full prompt (so any change to source, analysis or prompt
    misses); fresh=True bypasses the cache. Returns (scenarios, from_cache).
    Raises EnvironmentError if NEBIUS_API_KEY is not set.
    Raises ValueError if Nemotron returns nothing usable.
    """
    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "NEBIUS_API_KEY environment variable is not set. "
            "Export it before running: export NEBIUS_API_KEY=<your-key>"
        )

    user_prompt = _build_prompt(analysis, source_code)
    cache_key = hashlib.sha256(
        f"{_MODEL}\0{_SYSTEM_PROMPT}\0{user_prompt}".encode()
    ).hexdigest()[:16]
    cache_file = _CACHE_DIR / f"{cache_key}.txt"
    if not fresh and cache_file.exists():
        return _parse(cache_file.read_text(), analysis), True

    client = OpenAI(base_url=_BASE_URL, api_key=api_key)

    response = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=8192,
    )

    message = response.choices[0].message
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

    scenarios = _parse(raw.strip(), analysis)
    # Cache only usable responses, so a bad response is retried on the next run.
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(raw.strip())
    return scenarios, False


def _build_prompt(analysis: ConsumerAnalysis, source_code: str) -> str:
    dep_names = {d.name for d in analysis.dependencies}

    dep_lines = "\n".join(
        f"  {d.name}  ({d.type})" for d in analysis.dependencies
    )

    # Explicit dep→methods map — prevents hallucination of unknown names
    dep_methods: dict[str, list[str]] = {}
    for c in analysis.methodCalls:
        if c.scope in dep_names:
            if c.methodName not in dep_methods.get(c.scope, []):
                dep_methods.setdefault(c.scope, []).append(c.methodName)

    method_lines = "\n".join(
        f"  {dep}: {', '.join(methods)}"
        for dep, methods in dep_methods.items()
    )

    call_order = " → ".join(c.expression for c in analysis.methodCalls)

    return f"""\
Consumer: {analysis.className}.{analysis.methodName}
Event type: {analysis.eventType}

Dependency field names (use EXACTLY these in testConfig.faultInjections[].dep):
{dep_lines}

Methods per dependency (use EXACTLY these in testConfig.faultInjections[].method):
{method_lines}

Call order: {call_order}

Source code:
{source_code}

Return a JSON array of 2-4 reliability scenarios with testConfig for each.\
"""


def _parse(raw: str, analysis: ConsumerAnalysis) -> list[ChaosScenario]:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()

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

    dep_names = {d.name for d in analysis.dependencies}
    dep_methods: dict[str, set[str]] = {}
    for c in analysis.methodCalls:
        if c.scope in dep_names:
            dep_methods.setdefault(c.scope, set()).add(c.methodName)

    scenarios: list[ChaosScenario] = []
    for item in data:
        # Parse and validate testConfig — drop scenarios with invalid dep/method names
        tc_data = item.get("testConfig")
        test_config: TestConfig | None = None

        if isinstance(tc_data, dict):
            cp = tc_data.get("callPattern", "")
            raw_fis = tc_data.get("faultInjections", [])

            valid_fis: list[FaultInjection] = []
            fi_valid = True
            for fi in raw_fis:
                dep = fi.get("dep", "")
                method = fi.get("method", "")
                fault = fi.get("fault", "")

                if dep not in dep_names:
                    fi_valid = False
                    break
                if method not in dep_methods.get(dep, set()):
                    fi_valid = False
                    break
                if fault not in {"THROW", "THROW_ONCE", "DELAY"}:
                    fi_valid = False
                    break

                valid_fis.append(FaultInjection(
                    dep=dep,
                    method=method,
                    fault=fault,
                    delayMs=fi.get("delayMs", 0),
                ))

            if fi_valid and cp in {"SINGLE", "DUPLICATE", "CONCURRENT"}:
                test_config = TestConfig(
                    callPattern=cp,
                    faultInjections=valid_fis,
                    observeOrdering=bool(tc_data.get("observeOrdering", False)),
                    detectSwallowing=bool(tc_data.get("detectSwallowing", False)),
                )

        try:
            scenario = ChaosScenario(
                scenarioType=item.get("scenarioType", ""),
                reason=item.get("reason", ""),
                targetDependency=item.get("targetDependency"),
                targetMethod=item.get("targetMethod"),
                expectedConcern=item.get("expectedConcern", ""),
                testConfig=test_config,
            )
            if scenario.scenarioType and scenario.reason:
                scenarios.append(scenario)
        except (ValidationError, Exception):
            continue

    if not scenarios:
        raise ValueError(
            "No valid scenarios parsed from Nemotron response.\n"
            f"Raw response:\n{raw}"
        )

    return scenarios
