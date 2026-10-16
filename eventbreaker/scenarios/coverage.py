"""Coverage that does not depend on the model: every dependency call gets a failure scenario.

Nemotron Ultra proposes scenarios from what it thinks is risky, and a re-ask can propose a
different set — a call it skipped is never tested. Code enumerates the candidates instead
("code enumerates, the LLM judges"): for each dependency call the static analysis found,
if no Ultra scenario already makes that call fail, add one that does. The rubric then
decides from the measured run whether the failure matters.
"""
from eventbreaker.models.consumer import ConsumerAnalysis, MethodCallInfo
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig

SCENARIO_TYPE = "DEPENDENCY_FAILURE"


def dependency_calls(analysis: ConsumerAnalysis) -> list[str]:
    """Every "dependencyField.method" the listener calls, in source order, without repeats."""
    deps = {d.name for d in analysis.dependencies}
    found: list[str] = []

    def walk(calls: list[MethodCallInfo]) -> None:
        for c in calls:
            if c.scope in deps:
                found.append(f"{c.scope}.{c.methodName}")
            walk(c.nestedCalls)

    walk(analysis.methodCalls)
    walk([v.initializerMethodCall for v in analysis.variableInitializations if v.initializerMethodCall])
    for o in analysis.objectCreations:
        walk(o.nestedCalls)
    return list(dict.fromkeys(found))


def failure_scenarios(analysis: ConsumerAnalysis, existing: list[ChaosScenario]) -> list[ChaosScenario]:
    """One THROW scenario per dependency call that no existing scenario already makes fail."""
    covered = {
        f"{fi.dep}.{fi.method}"
        for s in existing if s.testConfig and s.testConfig.callPattern == "SINGLE"
        for fi in s.testConfig.faultInjections if fi.fault in ("THROW", "THROW_ONCE")
    }
    added = []
    for call in dependency_calls(analysis):
        if call in covered:
            continue
        dep, method = call.split(".", 1)
        added.append(ChaosScenario(
            scenarioType=f"{SCENARIO_TYPE} ({call})",
            reason=f"Added by code: every dependency call is tested failing; no proposed scenario failed {call}.",
            targetDependency=dep,
            targetMethod=method,
            expectedConcern=f"What the listener does when {call} throws.",
            testConfig=TestConfig(
                callPattern="SINGLE",
                faultInjections=[FaultInjection(dep=dep, method=method, fault="THROW")],
                detectSwallowing=True,
            ),
        ))
    return added
