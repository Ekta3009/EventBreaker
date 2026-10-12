"""Closed questions to Nemotron Super, asked 3× with different seeds and decided by majority vote.

Hosted models are not deterministic even at temperature 0, and they are least reliable on
open-ended judgement. So the model only answers small questions with fixed answers:
  - per dependency method: what it does to external state, and how bad it is if it goes wrong;
  - per concurrent scenario: can two simultaneous runs of the listener interfere?
Severity is then computed by code from these labels (see rubric.py).
"""
import json
import re
from collections import Counter
from dataclasses import dataclass, field

from eventbreaker.agent import llm
from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario

_MODEL = llm.SUPER
VOTES = 3

# Most cautious first: ties resolve to the label that reports more, not less.
EFFECTS = ("SIDE_EFFECT", "IDEMPOTENT_WRITE", "READ")
IMPACTS = ("MONEY", "SECURITY", "EXTERNAL_MESSAGE", "DATA", "MINOR")


@dataclass(frozen=True)
class Label:
    effect: str          # one of EFFECTS
    impact: str          # one of IMPACTS
    reason: str = ""
    agreement: str = ""  # e.g. "3/3"; "0/3" when no vote could be used (cautious default)

    def describe(self) -> str:
        return f"{self.effect}, impact {self.impact} ({self.agreement} votes)"


@dataclass(frozen=True)
class Interference:
    interferes: bool
    calls: list[str] = field(default_factory=list)
    reason: str = ""
    agreement: str = ""


_LABEL_PROMPT = """\
You classify the dependency calls of a Java event listener. Answer only from the source code.

For each "dependencyField.method" listed, give:
- effect — what the call does to state outside the listener:
  READ             returns data and changes nothing
  IDEMPOTENT_WRITE changes state, but repeating it with the same input leaves the same state
                   (overwrite, set, upsert, clear, delete-if-present)
  SIDE_EFFECT      repeating it changes state again or is visible outside (insert, append, increment,
                   send, publish, enqueue, charge, reserve)
- impact — the worst consequence if this call's work is lost or done twice:
  MONEY            payments, balances, charges, invoices
  SECURITY         authentication, authorization, lockouts, credentials, audit
  EXTERNAL_MESSAGE a message, event, notification or job that other systems act on
  DATA             a stored record or state of this system
  MINOR            caches, logs, metrics, context objects, lookups

Return ONLY a JSON object: {"dependencyField.method": {"effect": "...", "impact": "...", "reason": "<short>"}, ...}\
"""

_INTERFERENCE_PROMPT = """\
A Java event listener can be run twice at the same time for the same event (two threads). Answer only
from the source code: can the two runs interfere with each other's result — for example one run reading
state, the other changing it, and the first then writing based on what it read? Running the same
independent work twice is NOT interference.

Return ONLY a JSON object: {"interferes": true|false, "calls": ["dependencyField.method", ...], "reason": "<short>"}
"calls" lists the dependency calls involved (empty when false).\
"""


def label_calls(
    analysis: ConsumerAnalysis, source_code: str, methods: list[str], fresh: bool = False,
) -> dict[str, Label]:
    """Majority-vote labels for every dependency method (cautious default when no vote is usable)."""
    methods = sorted(set(methods))
    if not methods:
        return {}
    user = (
        f"Listener: {analysis.className}.{analysis.methodName}({analysis.eventType})\n"
        f"Dependency calls to classify:\n" + "\n".join(f"  {m}" for m in methods)
        + f"\n\nSource code:\n{source_code}"
    )
    messages = [{"role": "system", "content": _LABEL_PROMPT}, {"role": "user", "content": user}]
    samples = [
        _json_object(llm.chat(_MODEL, messages, "classify", valid=lambda r: _json_object(r) is not None,
                              fresh=fresh, seed=seed)) or {}
        for seed in range(1, VOTES + 1)
    ]
    return {m: _vote_label([s.get(m) for s in samples]) for m in methods}


def check_interference(
    analysis: ConsumerAnalysis, source_code: str, scenario: ChaosScenario, fresh: bool = False,
) -> Interference:
    """Majority vote on whether two simultaneous runs can interfere (asked for a CONCURRENT scenario)."""
    deps = ", ".join(f"{d.name} ({d.type})" for d in analysis.dependencies)
    user = (
        f"Listener: {analysis.className}.{analysis.methodName}({analysis.eventType})\n"
        f"Dependencies: {deps}\n"
        f"Concern raised for this scenario: {scenario.expectedConcern}\n\n"
        f"Source code:\n{source_code}"
    )
    messages = [{"role": "system", "content": _INTERFERENCE_PROMPT}, {"role": "user", "content": user}]
    samples = [
        _json_object(llm.chat(_MODEL, messages, "interference", valid=lambda r: _json_object(r) is not None,
                              fresh=fresh, seed=seed))
        for seed in range(1, VOTES + 1)
    ]
    votes = [s for s in samples if s is not None and isinstance(s.get("interferes"), bool)]
    yes = [s for s in votes if s["interferes"]]
    # Ties (or no usable vote) resolve to "interferes": reporting a possible race is the cautious side.
    interferes = len(yes) * 2 >= len(votes) if votes else True
    chosen = yes if interferes else [s for s in votes if not s["interferes"]]
    calls = Counter(c for s in chosen for c in (s.get("calls") or []) if isinstance(c, str))
    return Interference(
        interferes=interferes,
        calls=[c for c, n in calls.most_common() if n * 2 >= len(chosen)] or [c for c, _ in calls.most_common(1)],
        reason=str(chosen[0].get("reason", "")) if chosen else "no usable answer",
        agreement=f"{len(chosen)}/{VOTES}",
    )


def _vote_label(answers: list) -> Label:
    valid = [a for a in answers if isinstance(a, dict)]
    effects = [str(a.get("effect", "")).upper() for a in valid if str(a.get("effect", "")).upper() in EFFECTS]
    impacts = [str(a.get("impact", "")).upper() for a in valid if str(a.get("impact", "")).upper() in IMPACTS]
    if not effects:
        return Label("SIDE_EFFECT", "DATA", "unclassified — treated as a side effect", f"0/{VOTES}")
    effect, n = _majority(effects, EFFECTS)
    impact, _ = _majority(impacts, IMPACTS) if impacts else ("DATA", 0)
    reason = next((str(a.get("reason", "")) for a in valid if str(a.get("effect", "")).upper() == effect), "")
    return Label(effect, impact, reason, f"{n}/{VOTES}")


def _majority(values: list[str], cautious_order: tuple[str, ...]) -> tuple[str, int]:
    """Most common value; ties go to the value earliest in cautious_order."""
    counts = Counter(values)
    top = max(counts.values())
    winner = min((v for v, n in counts.items() if n == top), key=cautious_order.index)
    return winner, top


def _json_object(raw: str) -> dict | None:
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        return None
    try:
        data = json.loads(cleaned[start:end])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
