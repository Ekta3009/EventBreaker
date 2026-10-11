"""Report shows what each scenario did and how its counts differ from the fault-free baseline."""
from eventbreaker.models.observation import ObservationEntry as E
from eventbreaker.report.reporter import _call_status, describe_test
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig


def sc(pattern, faults=()):
    return ChaosScenario(scenarioType="X", reason="r", expectedConcern="c",
                         testConfig=TestConfig(callPattern=pattern, faultInjections=list(faults)))


def test_describe_fault_scenario():
    text = describe_test(sc("SINGLE", [FaultInjection(dep="handler", method="handle", fault="THROW")]))
    assert text == "Delivered the event once and made `handler.handle` throw on every call"


def test_describe_duplicate():
    assert describe_test(sc("DUPLICATE")).startswith("Delivered the same event twice in a row")


def test_status_threw_is_visible_even_at_one_call():
    assert _call_status(E(target="h.handle", callCount=1, threw=True), 1, None) == "⚠ threw"


def test_status_against_baseline():
    assert _call_status(E(target="p.insert", callCount=1), 1, 3) == "⚠ 2 missing"
    assert _call_status(E(target="p.insert", callCount=6), 6, 3) == "⚠ 3 extra"
    assert _call_status(E(target="p.insert", callCount=3), 3, 3) == "✓"
    assert _call_status(None, 0, 1) == "⚠ 1 missing"


def test_save_never_overwrites(tmp_path):
    from eventbreaker.models.consumer import ConsumerAnalysis
    from eventbreaker.report.reporter import save
    a = ConsumerAnalysis(className="KafkaRemoteMessageListener", methodName="onMessage", eventType="E")
    first, second = save("one", a, tmp_path), save("two", a, tmp_path)
    assert first != second
    assert first.name.startswith("KafkaRemoteMessageListener.onMessage-")
    assert (first.read_text(), second.read_text()) == ("one", "two")
