from pydantic import BaseModel

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.scenarios.models import ChaosScenario

_VALID_CALL_PATTERNS = {"SINGLE", "DUPLICATE", "CONCURRENT"}
_VALID_FAULTS = {"THROW", "THROW_ONCE", "DELAY"}


class ExperimentPlan(BaseModel):
    feasible: bool
    reason: str | None = None   # set when feasible=False


def check_feasibility(
    scenario: ChaosScenario,
    analysis: ConsumerAnalysis,
) -> ExperimentPlan:
    """Pure Python feasibility check. No LLM call. Instant.

    Returns ExperimentPlan(feasible=True) if the testConfig can be executed
    by the parameter-driven executor, or ExperimentPlan(feasible=False, reason=...)
    if it cannot.
    """
    tc = scenario.testConfig

    if tc is None:
        return ExperimentPlan(
            feasible=False,
            reason="Nemotron did not provide a test configuration for this scenario.",
        )

    if tc.callPattern not in _VALID_CALL_PATTERNS:
        return ExperimentPlan(
            feasible=False,
            reason=f"Unknown callPattern '{tc.callPattern}' — must be SINGLE, DUPLICATE, or CONCURRENT.",
        )

    dep_names = {d.name for d in analysis.dependencies}

    dep_methods: dict[str, set[str]] = {}
    for c in analysis.methodCalls:
        if c.scope in dep_names:
            dep_methods.setdefault(c.scope, set()).add(c.methodName)

    for fi in tc.faultInjections:
        if fi.dep not in dep_names:
            return ExperimentPlan(
                feasible=False,
                reason=(
                    f"faultInjection references dep '{fi.dep}' which is not a known "
                    f"dependency of {analysis.className}."
                ),
            )
        if fi.method not in dep_methods.get(fi.dep, set()):
            return ExperimentPlan(
                feasible=False,
                reason=(
                    f"faultInjection references method '{fi.dep}.{fi.method}' which "
                    f"is not called on '{fi.dep}' in {analysis.className}."
                ),
            )
        if fi.fault not in _VALID_FAULTS:
            return ExperimentPlan(
                feasible=False,
                reason=f"Unknown fault type '{fi.fault}' — must be THROW, THROW_ONCE, or DELAY.",
            )

    return ExperimentPlan(feasible=True)
