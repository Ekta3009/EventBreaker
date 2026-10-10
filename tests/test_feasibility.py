"""Pure-Python feasibility check (pipeline step 3)."""
import pytest

from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo, MethodCallInfo
from eventbreaker.scenarios.experiment import check_feasibility
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig

ANALYSIS = ConsumerAnalysis(
    className="OrderConsumer", methodName="handle", eventType="OrderPlaced",
    dependencies=[DependencyInfo(name="paymentClient", type="PaymentClient")],
    methodCalls=[MethodCallInfo(
        expression="paymentClient.charge(a, b)", scope="paymentClient",
        methodName="charge", arguments=["a", "b"],
    )],
)


def scenario(tc: TestConfig | None) -> ChaosScenario:
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c", testConfig=tc)


def fault(dep="paymentClient", method="charge", kind="THROW") -> TestConfig:
    return TestConfig(callPattern="SINGLE", faultInjections=[FaultInjection(dep=dep, method=method, fault=kind)])


@pytest.mark.parametrize("pattern", ["SINGLE", "DUPLICATE", "CONCURRENT"])
def test_valid_call_patterns(pattern):
    assert check_feasibility(scenario(TestConfig(callPattern=pattern)), ANALYSIS).feasible


@pytest.mark.parametrize("kind", ["THROW", "THROW_ONCE", "DELAY"])
def test_valid_fault_types(kind):
    assert check_feasibility(scenario(fault(kind=kind)), ANALYSIS).feasible


@pytest.mark.parametrize("tc, reason_part", [
    (None, "did not provide"),
    (TestConfig(callPattern="REPLAY"), "Unknown callPattern"),
    (fault(dep="ghostClient"), "not a known dependency"),
    (fault(method="refund"), "not called on"),
    (fault(kind="CORRUPT"), "Unknown fault type"),
])
def test_infeasible(tc, reason_part):
    plan = check_feasibility(scenario(tc), ANALYSIS)
    assert not plan.feasible
    assert reason_part in plan.reason
