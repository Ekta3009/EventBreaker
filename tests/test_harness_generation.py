"""Java harness generation from a TestConfig — string-level checks, no Maven build.

Harness tests run the real static analyzer on real listener sources (Kill Bill,
Fineract copies and the analyzer test inputs), so they also catch
analyzer/executor drift.
"""
import json
import re

import pytest

from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
from eventbreaker.scenarios.executor import (
    _build_call_pattern, _build_named_faults, _generate_execution, _listener_arguments, _parse_output,
)
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, ReturnValue, TestConfig
from tests.conftest import LISTENERS, listener_file

MARKERS = [
    "EXTRA_IMPORTS", "MOCK_DECLARATIONS", "EVENT_STUBS", "CONSUMER_INSTANTIATION", "EVENT_CLASS",
    "DEP_MAP_ENTRIES", "RESET_INVOCATIONS", "FAULT_INJECTIONS", "CALL_PATTERN", "SCENARIO_TYPE",
]


def harness(analysis, name, *faults, pattern="SINGLE", returns=()) -> str:
    tc = TestConfig(
        callPattern=pattern,
        faultInjections=[FaultInjection(dep=d, method=m, fault=f) for d, m, f in faults],
        returnValues=[ReturnValue(target=t, returns=r) for t, r in returns],
    )
    return _generate_execution(analysis, listener_file(name), tc, "TEST")


# ── Faults and return values (registered by name on __EBInstantiate) ─────────

class TestNamedFaults:
    FILE = listener_file("OverdueListener")

    def build(self, *faults, returns=()):
        return _build_named_faults(TestConfig(
            callPattern="SINGLE",
            faultInjections=[FaultInjection(dep="pay", method="charge", fault=f, delayMs=d or 0) for f, d in faults],
            returnValues=[ReturnValue(target=t, returns=r) for t, r in returns],
        ), self.FILE)

    def test_throw(self):
        assert '__EBInstantiate.FAULTS.put("pay.charge", "THROW");' in self.build(("THROW", None))

    def test_throw_once(self):
        assert '__EBInstantiate.FAULTS.put("pay.charge", "THROW_ONCE");' in self.build(("THROW_ONCE", None))

    def test_delay_with_and_without_ms(self):
        assert '"DELAY:250"' in self.build(("DELAY", 250))
        assert '"DELAY:500"' in self.build(("DELAY", None))

    def test_string_return_is_escaped(self):
        out = self.build(returns=[("event.getName", 'STRING:say "hi"')])
        assert r'RETURNS.put("event.getName", "STRING:say \"hi\"");' in out

    def test_mock_return_is_a_class_literal_resolved_from_imports(self):
        out = self.build(returns=[("overdueConfigCache.getOverdueConfig", "MOCK:DefaultOverdueConfig")])
        assert ('RETURNS.put("overdueConfigCache.getOverdueConfig", '
                "org.killbill.billing.overdue.config.DefaultOverdueConfig.class);") in out

    def test_nothing_to_register(self):
        assert self.build() == ""


# ── Listener arguments and call patterns ─────────────────────────────────────

class TestListenerArguments:
    def test_extra_params_get_recorders(self):
        a = ConsumerAnalysis(className="C", methodName="on", eventType="Msg",
                             parameters=["@Payload Msg msg", "Acknowledgment ack"])
        extra, args = _listener_arguments(a)
        assert [(d.name, d.type) for d in extra] == [("ack", "Acknowledgment")]
        assert args == "event, ack"

    def test_param_named_like_a_field_is_renamed(self):
        a = ConsumerAnalysis(className="C", methodName="on", eventType="Msg",
                             dependencies=[DependencyInfo(name="ack", type="Ack")],
                             parameters=["Msg msg", "Acknowledgment ack"])
        assert _listener_arguments(a)[1] == "event, param_ack"

    def test_event_type_not_among_params_falls_back(self):
        a = ConsumerAnalysis(className="C", methodName="on", eventType="Msg",
                             parameters=["ConsumerRecord<String, Msg> record"])
        assert _listener_arguments(a) == ([], "event")


class TestCallPatterns:
    A = ConsumerAnalysis(className="C", methodName="onEvent", eventType="Ev")
    CALL = '__EBInstantiate.call(consumer, "onEvent", event)'

    def count(self, pattern):
        return _build_call_pattern(self.A, TestConfig(callPattern=pattern)).count(self.CALL)

    def test_single(self):
        assert self.count("SINGLE") == 1

    def test_duplicate(self):
        assert self.count("DUPLICATE") == 2

    def test_concurrent(self):
        out = _build_call_pattern(self.A, TestConfig(callPattern="CONCURRENT"))
        assert self.count("CONCURRENT") == 2
        assert "__eb_t1.join(); __eb_t2.join();" in out

    @pytest.mark.parametrize("pattern", ["SINGLE", "DUPLICATE", "CONCURRENT"])
    def test_consumer_exceptions_are_recorded(self, pattern):
        assert "consumerThrew.set(true)" in _build_call_pattern(self.A, TestConfig(callPattern=pattern))


# ── Generated harness on real listener sources ───────────────────────────────

class TestGeneratedHarness:
    @pytest.mark.parametrize("name", sorted(LISTENERS))
    @pytest.mark.parametrize("pattern", ["SINGLE", "DUPLICATE", "CONCURRENT"])
    def test_every_listener_fills_every_marker(self, analyze, name, pattern):
        src = harness(analyze(name), name, pattern=pattern)
        # Marker names can appear inside generated identifiers; check standalone tokens.
        leftover = [m for m in MARKERS if re.search(rf"(?<![\w.]){m}(?![\w])", src)]
        assert leftover == [], f"unreplaced template markers: {leftover}"

    def test_consumer_created_and_injected_reflectively(self, analyze):
        src = harness(analyze("OverdueListener", "handlePaymentInfoEvent"), "OverdueListener")
        assert ('__EBInstantiate.create(Class.forName('
                '"org.killbill.billing.overdue.listener.OverdueListener"));') in src
        assert '__EBInstantiate.inject(consumer, "asyncPoster", asyncPoster);' in src
        assert '__EBInstantiate.call(consumer, "handlePaymentInfoEvent", event)' in src

    def test_every_dependency_is_a_recorder(self, analyze):
        analysis = analyze("OverdueListener", "handlePaymentInfoEvent")
        src = harness(analysis, "OverdueListener")
        for d in analysis.dependencies:
            assert f'__EBInstantiate.settings({d.type.split("<")[0]}.class, "{d.name}")' in src
            assert f'deps.put("{d.name}", {d.name});' in src

    def test_fault_and_path_setup_reach_the_harness(self, analyze):
        src = harness(analyze("OverdueListener", "handlePaymentInfoEvent"), "OverdueListener",
                      ("accountApi", "getAccountById", "THROW"),
                      returns=[("overdueConfigCache.getOverdueConfig", "MOCK:DefaultOverdueConfig")])
        assert 'FAULTS.put("accountApi.getAccountById", "THROW");' in src
        assert "DefaultOverdueConfig.class);" in src

    def test_acknowledgment_param_is_recorded_and_passed(self, analyze):
        src = harness(analyze("KafkaRemoteMessageListener"), "KafkaRemoteMessageListener")
        assert 'deps.put("acknowledgment", acknowledgment);' in src
        assert '"onMessage", event, acknowledgment)' in src

    def test_event_boolean_getters_return_true(self, analyze):
        src = harness(analyze("ConditionalConsumer"), "ConditionalConsumer")
        assert re.search(r"when\(event\.is\w+\(\)\)\.thenReturn\(true\);", src)


# ── Output parsing ───────────────────────────────────────────────────────────

class TestParseOutput:
    S = ChaosScenario(scenarioType="DUPLICATE_DELIVERY", reason="r", expectedConcern="c")

    def test_parses_result_line(self):
        payload = {
            "observations": [{"target": "pay.charge", "callCount": 2, "threw": False}],
            "consumerThrew": True,
            "callSequence": ["pay.charge", "pay.charge"],
        }
        out = "[INFO] noise\nEVENTBREAKER_RESULT:" + json.dumps(payload) + "\nmore noise"
        r = _parse_output(out, self.S)
        assert r.status == "REPRODUCED"
        assert r.observations[0].callCount == 2
        assert r.consumerThrew is True
        assert r.callSequence == ["pay.charge", "pay.charge"]

    def test_missing_result_is_error(self):
        r = _parse_output("BUILD FAILURE", self.S)
        assert r.status == "ERROR"
        assert r.observations == []
