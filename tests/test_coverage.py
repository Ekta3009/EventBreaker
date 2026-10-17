"""Code-enumerated failure scenarios: every dependency call is tested failing, whatever Ultra proposed.

Synthetic listener only (an imaginary order listener).
"""
from eventbreaker.models.consumer import (
    ConsumerAnalysis, DependencyInfo, MethodCallInfo, VariableInitializationInfo,
)
from eventbreaker.scenarios.coverage import dependency_calls, failure_scenarios
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig


def call(scope, name, nested=()):
    return MethodCallInfo(expression=f"{scope}.{name}()", scope=scope, methodName=name, nestedCalls=list(nested))


A = ConsumerAnalysis(
    className="OrderListener", methodName="on", eventType="OrderPlaced",
    dependencies=[DependencyInfo(name="orders", type="OrderRepo"), DependencyInfo(name="ledger", type="Ledger")],
    methodCalls=[
        call("log", "info"),                                   # not a dependency
        call("ledger", "append", [call("orders", "total")]),   # nested call on a dependency
        call("event", "getId"),                                # the event, not a dependency
        call("ledger", "append"),                              # repeat
    ],
    variableInitializations=[VariableInitializationInfo(
        variableType="Order", initializerMethodCall=call("orders", "find"))],
)


def scenario(pattern, dep=None, method=None, fault="THROW"):
    faults = [FaultInjection(dep=dep, method=method, fault=fault)] if dep else []
    return ChaosScenario(scenarioType="U", reason="r", expectedConcern="c",
                         testConfig=TestConfig(callPattern=pattern, faultInjections=faults))


def test_dependency_calls_are_unique_and_only_on_dependency_fields():
    assert dependency_calls(A) == ["ledger.append", "orders.total", "orders.find"]


def test_one_throw_scenario_per_uncovered_call():
    added = failure_scenarios(A, [scenario("SINGLE", "ledger", "append", "THROW_ONCE"), scenario("DUPLICATE")])
    assert [(s.targetDependency, s.targetMethod) for s in added] == [("orders", "total"), ("orders", "find")]
    tc = added[0].testConfig
    assert tc.callPattern == "SINGLE" and [(f.dep, f.method, f.fault) for f in tc.faultInjections] == [
        ("orders", "total", "THROW")]
    assert len({s.scenarioType for s in added}) == len(added)  # report keys are per scenario type


def test_a_delay_does_not_count_as_covering_a_failure():
    added = failure_scenarios(A, [scenario("SINGLE", "ledger", "append", "DELAY")])
    assert "ledger.append" in {f"{s.targetDependency}.{s.targetMethod}" for s in added}
