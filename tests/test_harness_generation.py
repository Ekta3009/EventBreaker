"""Java harness generation from a TestConfig — string-level checks, no Maven build.

Most tests run the real static analyzer on the consumers in
src/test/resources/consumers, so they also catch analyzer/executor drift.
"""
import json
import re

import pytest

from eventbreaker.models.consumer import (
    ConsumerAnalysis, DependencyInfo, MethodCallInfo, VariableInitializationInfo,
)
from eventbreaker.scenarios.executor import (
    _analyze_chains, _build_call_pattern, _build_fault_injections, _chain_return_type,
    _generate_execution, _matcher_lists, _parse_output,
)
from eventbreaker.scenarios.models import ChaosScenario, FaultInjection, TestConfig
from tests.conftest import ALL_CONSUMERS, consumer_file

MARKERS = [
    "CONSUMER_IMPORT", "EXTRA_IMPORTS", "MOCK_DECLARATIONS", "CHAIN_STUBS", "STUBS",
    "CONSUMER_INSTANTIATION", "EVENT_CLASS", "DEP_MAP_ENTRIES", "RESET_INVOCATIONS",
    "FAULT_INJECTIONS", "CALL_PATTERN", "SCENARIO_TYPE",
]


def harness(analysis, name, *faults, pattern="SINGLE") -> str:
    tc = TestConfig(callPattern=pattern, faultInjections=[
        FaultInjection(dep=d, method=m, fault=f) for d, m, f in faults
    ])
    return _generate_execution(analysis, consumer_file(name), tc, "TEST")


def call(scope, method, n_args, nested=()):
    args = [f"a{i}" for i in range(n_args)]
    return MethodCallInfo(
        expression=f"{scope}.{method}({', '.join(args)})", scope=scope,
        methodName=method, arguments=args, nestedCalls=list(nested),
    )


# ── Argument matchers (one any() per argument) ───────────────────────────────

class TestMatcherLists:
    def base(self, calls=(), inits=()):
        return ConsumerAnalysis(
            className="C", methodName="handle", eventType="Ev",
            methodCalls=list(calls), variableInitializations=list(inits),
        )

    def test_one_any_per_argument(self):
        assert _matcher_lists(self.base([call("repo", "save", 3)]), "repo", "save") \
            == ["any(), any(), any()"]

    def test_zero_args(self):
        assert _matcher_lists(self.base([call("tx", "begin", 0)]), "tx", "begin") == [""]

    def test_one_list_per_distinct_arity(self):
        a = self.base([call("log", "info", 1), call("log", "info", 2), call("log", "info", 1)])
        assert _matcher_lists(a, "log", "info") == ["any()", "any(), any()"]

    def test_finds_nested_calls(self):
        a = self.base([call("outer", "wrap", 1, nested=[call("repo", "find", 2)])])
        assert _matcher_lists(a, "repo", "find") == ["any(), any()"]

    def test_finds_variable_initializers(self):
        vi = VariableInitializationInfo(
            variableName="o", variableType="Order", initializer="repo.find(a, b)",
            initializerMethodCall=call("repo", "find", 2),
        )
        assert _matcher_lists(self.base(inits=[vi]), "repo", "find") == ["any(), any()"]

    def test_unknown_call_defaults_to_one_matcher(self):
        assert _matcher_lists(self.base(), "repo", "find") == ["any()"]

    def test_matches_scope_and_method_together(self):
        a = self.base([call("a", "save", 1), call("b", "save", 2)])
        assert _matcher_lists(a, "b", "save") == ["any(), any()"]


# ── Fault injection and call patterns ────────────────────────────────────────

class TestFaultInjections:
    A = ConsumerAnalysis(
        className="C", methodName="handle", eventType="Ev",
        dependencies=[DependencyInfo(name="pay", type="Pay")],
        methodCalls=[call("pay", "charge", 2)],
    )

    def build(self, kind, delay=0):
        return _build_fault_injections(TestConfig(callPattern="SINGLE", faultInjections=[
            FaultInjection(dep="pay", method="charge", fault=kind, delayMs=delay)
        ]), self.A)

    def test_throw(self):
        assert '.when(pay).charge(any(), any());' in self.build("THROW")
        assert "doThrow(new RuntimeException" in self.build("THROW")

    def test_throw_once_uses_do_answer_not_do_nothing(self):
        out = self.build("THROW_ONCE")
        assert ".doAnswer(inv -> null).when(pay).charge(any(), any());" in out
        assert "doNothing" not in out

    def test_delay(self):
        assert "Thread.sleep(250)" in self.build("DELAY", delay=250)

    def test_delay_default(self):
        assert "Thread.sleep(500)" in self.build("DELAY")

    def test_no_faults(self):
        assert _build_fault_injections(TestConfig(callPattern="DUPLICATE"), self.A) == ""


class TestCallPatterns:
    A = ConsumerAnalysis(className="C", methodName="onEvent", eventType="Ev")

    def count(self, pattern):
        return _build_call_pattern(self.A, TestConfig(callPattern=pattern)).count("consumer.onEvent(event)")

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


# ── Chain analysis ───────────────────────────────────────────────────────────

class TestChains:
    def test_dependency_root_chain(self, analyze):
        roots, methods, typed = _analyze_chains(analyze("MixedCallConsumer"))
        assert roots == {"customerService": {"getCustomer"}}
        assert methods == {"getContactInfo", "getEmail"}
        assert typed == {"getEmail": "String"}

    def test_event_root_chain(self, analyze):
        roots, methods, _ = _analyze_chains(analyze("DeepNestedConsumer"))
        assert roots == {"event": {"getCustomer", "getOrder"}}
        assert {"withCustomer", "getId"} <= methods

    def test_no_chains(self, analyze):
        assert _analyze_chains(analyze("PackagePrivateConsumer")) == ({}, set(), {})

    @pytest.mark.parametrize("var_type, expected", [
        ("int", "int"),
        ("String", "String"),
        ("List<String>", "java.util.List"),
        ("Optional<Order>", "java.util.Optional"),
        ("Customer", "test.Customer"),
        ("Money", "com.acme.Money"),
    ])
    def test_chain_return_type(self, var_type, expected):
        assert _chain_return_type(var_type, "test", {"Money": "com.acme"}) == expected


# ── Generated harness on the real consumers ──────────────────────────────────

class TestGeneratedHarness:
    @pytest.mark.parametrize("name", ALL_CONSUMERS)
    @pytest.mark.parametrize("pattern", ["SINGLE", "DUPLICATE", "CONCURRENT"])
    def test_every_consumer_fills_every_marker(self, analyze, name, pattern):
        src = harness(analyze(name), name, pattern=pattern)
        # Marker names can appear inside generated identifiers; check standalone tokens.
        leftover = [m for m in MARKERS if re.search(rf"(?<![\w.]){m}(?![\w])", src)]
        assert leftover == [], f"unreplaced template markers: {leftover}"

    def test_primitive_and_collection_return_stubs(self, analyze):
        src = harness(analyze("MixedCallConsumer"), "MixedCallConsumer")
        assert "when(loyaltyService.calculatePoints(any())).thenReturn(1);" in src
        assert "when(emailClient.send(any(), any())).thenReturn(true);" in src
        assert "when(loyaltyService.perksFor(any())).thenReturn(new java.util.ArrayList<>());" in src
        assert "mock(int.class)" not in src and "mock(List" not in src

    def test_dependency_chain_returns_shared_chain_mock(self, analyze):
        src = harness(analyze("MixedCallConsumer"), "MixedCallConsumer")
        assert "mock(eventbreaker.generated.__EBChain.class, RETURNS_SELF)" in src
        assert "when(customerService.getCustomer(any())).thenReturn(__ebChain);" in src

    def test_event_chain_stubs(self, analyze):
        src = harness(analyze("DeepNestedConsumer"), "DeepNestedConsumer")
        assert "when(event.getCustomer()).thenReturn(__ebChain);" in src
        assert "when(event.getOrder()).thenReturn(__ebChain);" in src

    def test_chain_mock_declared_before_use(self, analyze):
        src = harness(analyze("DeepNestedConsumer"), "DeepNestedConsumer")
        assert src.index("__EBChain __ebChain =") < src.index("thenReturn(__ebChain)")

    def test_object_return_gets_a_typed_mock(self, analyze):
        src = harness(analyze("PackagePrivateConsumer"), "PackagePrivateConsumer")
        assert "when(userRepository.findById(any())).thenReturn(mockUser);" in src

    def test_event_mock_default_answer(self, analyze):
        src = harness(analyze("EarlyReturnConsumer"), "EarlyReturnConsumer")
        assert "defaultAnswer(EventBreakerExecution::eventAnswer)" in src

    def test_fault_on_multi_arg_call(self, analyze):
        src = harness(analyze("TryCatchConsumer"), "TryCatchConsumer",
                      ("shipmentService", "book", "THROW"))
        assert ".when(shipmentService).book(any(), any());" in src

    def test_fault_on_zero_arg_call(self, analyze):
        src = harness(analyze("TransactionalConsumer"), "TransactionalConsumer",
                      ("transactionManager", "commit", "THROW"))
        assert ".when(transactionManager).commit();" in src

    def test_field_injection_without_constructor(self, analyze):
        src = harness(analyze("PackagePrivateConsumer"), "PackagePrivateConsumer")
        assert 'getDeclaredField("userRepository")' in src


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
