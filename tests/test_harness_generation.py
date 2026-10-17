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
    "DEP_MAP_ENTRIES", "RESET_INVOCATIONS", "FAULT_INJECTIONS", "VALUE_PARAMS", "CALL_PATTERN", "SCENARIO_TYPE",
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
                '"org.killbill.billing.overdue.listener.OverdueListener"), ') in src
        assert '"asyncPoster"' in src.split("__EBInstantiate.create(")[1].split(";")[0]  # named constructor fakes
        assert '__EBInstantiate.inject(consumer, "asyncPoster", asyncPoster);' in src
        assert '__EBInstantiate.call(consumer, "handlePaymentInfoEvent", event)' in src

    def test_every_dependency_is_a_recorder(self, analyze):
        analysis = analyze("OverdueListener", "handlePaymentInfoEvent")
        src = harness(analysis, "OverdueListener")
        for d in analysis.dependencies:
            assert f'__EBInstantiate.settings({d.type.split("<")[0]}.class, "{d.name}")' in src
            assert f'__ebDeps.put("{d.name}", {d.name});' in src

    def test_fault_and_path_setup_reach_the_harness(self, analyze):
        src = harness(analyze("OverdueListener", "handlePaymentInfoEvent"), "OverdueListener",
                      ("accountApi", "getAccountById", "THROW"),
                      returns=[("overdueConfigCache.getOverdueConfig", "MOCK:DefaultOverdueConfig")])
        assert 'FAULTS.put("accountApi.getAccountById", "THROW");' in src
        assert "DefaultOverdueConfig.class);" in src

    def test_acknowledgment_param_is_recorded_and_passed(self, analyze):
        src = harness(analyze("KafkaRemoteMessageListener"), "KafkaRemoteMessageListener")
        assert '__ebDeps.put("acknowledgment", acknowledgment);' in src
        assert '"onMessage", event, acknowledgment)' in src

    def test_event_booleans_come_from_the_default_answer(self, analyze):
        # No hard-coded when(event.isX()) stubs: they would override a path setup's "event.isX → false".
        src = harness(analyze("ConditionalConsumer"), "ConditionalConsumer")
        assert not re.search(r"when\(event\.", src)
        from eventbreaker.scenarios.executor import _INSTANTIATE_HELPER as helper
        # The fakes' default answer: a path-setup value first, then true for booleans.
        assert helper.index("if (RETURNS.containsKey(path)) {\n            Object spec") < helper.index(
            "if (rt == boolean.class || rt == Boolean.class) return true;")


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


def test_mock_class_not_on_classpath_is_skipped_not_compiled():
    tc = TestConfig(callPattern="SINGLE", returnValues=[
        ReturnValue(target="calendarRepository.findById", returns="MOCK:Calendar"),
        ReturnValue(target="overdueConfigCache.getOverdueConfig", returns="MOCK:DefaultOverdueConfig"),
    ])
    available = {"org/killbill/billing/overdue/config/DefaultOverdueConfig"}
    out = _build_named_faults(tc, listener_file("OverdueListener"), available)
    assert "Calendar" not in out and "DefaultOverdueConfig.class" in out


def test_library_javax_imports_need_the_classpath_jdk_ones_do_not():
    from eventbreaker.scenarios.executor import _import_available
    available = {"javax/inject/Inject", "javax/inject", "javax/annotation/PostConstruct", "javax/annotation",
                 "org/acme/Outer", "org/acme/Outer/Inner", "org/acme"}
    assert _import_available("import javax.sql.DataSource;", available)          # JDK
    assert _import_available("import javax.xml.parsers.DocumentBuilder;", available)
    assert not _import_available("import javax.xml.bind.annotation.XmlElement;", available)  # JAXB: not in JDK 11+
    assert _import_available("import javax.inject.Inject;", available)           # library, present
    assert not _import_available("import javax.annotation.Nullable;", available) # jsr305, compile-only
    assert _import_available("import java.util.UUID;", set())
    # A package match alone is not enough: javax/annotation exists, its Nullable does not.
    assert not _import_available("import javax.annotation.Nullable;", available)
    assert _import_available("import org.acme.Outer.Inner;", available)            # nested type
    assert _import_available("import static org.acme.Outer.helper;", available)    # static member
    assert _import_available("import org.acme.*;", available)                      # wildcard package
    assert not _import_available("import org.other.*;", available)



def test_plain_parameters_are_values_read_after_the_path_setup_overrides(tmp_path):
    """A @Header String can't be faked; its value must be read after RETURNS.put("param.type", …)."""
    from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
    src_file = tmp_path / "DeviceListener.java"
    src_file.write_text("package org.acme;\nclass DeviceListener {}\n")
    analysis = ConsumerAnalysis(
        className="DeviceListener", methodName="onMessage", eventType="Message",
        dependencies=[DependencyInfo(name="devices", type="DeviceRepo")],
        parameters=["final Message message", '@Header(name = "type") final String type'],
    )
    tc = TestConfig(callPattern="SINGLE", returnValues=[ReturnValue(target="param.type", returns="STRING:EVENT")])
    src = _generate_execution(analysis, src_file, tc, "TEST")
    put = src.index('RETURNS.put("param.type", "STRING:EVENT");')
    read = src.index('String type = (String) eventbreaker.generated.__EBInstantiate.param("type", String.class);')
    assert put < read < src.index('"onMessage", event, type)')
    assert 'mock(String.class' not in src and '__ebDeps.put("type"' not in src



def test_constructor_params_from_source_skip_new_and_calls():
    from eventbreaker.scenarios.executor import constructor_params
    src = """package org.acme;
    class OrderListener extends Base {
        private final Repo repo;
        @Inject
        public OrderListener(final Template template, @Named("x") final Repo repo, Map<String, List<Integer>> m) {
            super(template);
            this.repo = repo;
        }
        OrderListener() { this(new Template(), null, null); }
        static OrderListener of() { return new OrderListener(null, null, null); }
    }"""
    assert constructor_params(src, "OrderListener") == [
        ("Template", "template"), ("Repo", "repo"), ("Map<String, List<Integer>>", "m")]


def test_project_static_helpers_only_from_the_project(tmp_path):
    from eventbreaker.scenarios.executor import static_helpers
    src = """package org.acme.orders.listener;
    import static org.acme.security.Access.asSystem;
    import org.acme.orders.util.Uris;
    import org.acme.orders.model.Status;
    import org.springframework.util.StringUtils;
    class L { void on() { asSystem(() -> 1); Uris.parse("x"); StringUtils.hasText("y"); Status s = null; } }"""
    # Root = first three segments (org.acme.orders): org.acme.security is not part of the project.
    assert static_helpers(src) == ["org.acme.orders.util.Uris"]


def test_fakes_remember_their_declared_generic_type():
    # Registry<PluginApi>.getServiceForName(..) returns T: Java erases T at runtime, so the helper records the
    # declared type of every fake (injected field, constructor argument, returned value) to resolve T.
    from eventbreaker.scenarios.executor import _INSTANTIATE_HELPER as helper
    assert "declare(value, f.getGenericType());" in helper                 # injected dependency fields
    assert "declare(args[i], generic[i]);" in helper                      # constructor arguments
    assert "declare(value, m.getGenericReturnType());" in helper          # fakes returned by fakes
    assert helper.count("generics(inv).resolveGenericReturnType(") == 2   # return type + element type use it


def test_cast_targets_found_for_event_dependencies_and_constructor_args(tmp_path):
    from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
    from eventbreaker.scenarios.executor import cast_targets
    src = tmp_path / "L.java"
    src.write_text("""package com.acme;
import com.acme.events.AccountCreated;
public class L {
    private final Plugin plugin;
    public L(Registry<Plugin> registry) { this.plugin = registry.get(); }
    public void on(final BusEvent busEvent) {
        AccountCreated a = (AccountCreated) busEvent;          // event cast: imported type
        ((InvoicePlugin) plugin).run();                        // dependency cast: same-package type
        Object o = (Other) this.plugin;                        // this.<field>
        String s = (String) busEvent.getName();                // a call result, not the root: ignored
        // (Ignored) plugin  -- comments are ignored
        Registry r = (Registry) registry;                      // constructor argument
    }
}
""")
    a = ConsumerAnalysis(className="L", methodName="on", eventType="BusEvent",
                         parameters=["final BusEvent busEvent"],
                         dependencies=[DependencyInfo(name="plugin", type="Plugin")])
    assert cast_targets(a, src) == {
        "event": ["com.acme.events.AccountCreated"],
        "plugin": ["com.acme.InvoicePlugin", "com.acme.Other"],
        "registry": ["com.acme.Registry"],
    }


def test_fakes_are_built_cast_aware(analyze):
    # Dependencies and the event are faked through typeFor(..), after CASTS is filled.
    src = harness(analyze("SimpleConsumer"), "SimpleConsumer")
    assert "__EBInstantiate.typeFor(" in src and "(EVENT_CLASS)" not in src
    from eventbreaker.scenarios.executor import _EXECUTION_TEMPLATE as t
    assert t.index("CAST_TYPES") < t.index("MOCK_DECLARATIONS")


def test_list_values_are_one_element_collections():
    from eventbreaker.scenarios.executor import _INSTANTIATE_HELPER as helper
    assert 'list.startsWith("LIST:")' in helper and "listOf(convert(list.substring(5), elementType(inv)" in helper


def test_injected_failures_throw_the_declared_checked_exception():
    from eventbreaker.scenarios.executor import _INSTANTIATE_HELPER as helper
    assert 'throw failure(inv.getMethod(), "EventBreaker: " + fault + " on " + path);' in helper
    assert "m.getExceptionTypes()" in helper and "return new RuntimeException(message);" in helper


def test_enum_builtins_are_not_static_helpers():
    from eventbreaker.scenarios.executor import static_helpers
    src = """package org.acme.app;
import org.acme.app.api.MessageType;
import org.acme.app.ctx.Tenant;
class L { void on(String t) { MessageType.valueOf(t); MessageType.values(); Tenant.current(); } }
"""
    assert static_helpers(src) == ["org.acme.app.ctx.Tenant"]   # valueOf/values only → an enum's built-ins


def test_inherited_source_finds_project_parents(tmp_path):
    from eventbreaker.scenarios.executor import inherited_source
    (tmp_path / "pom.xml").write_text("<project/>")
    pkg = tmp_path / "src" / "main" / "java" / "org" / "acme"
    pkg.mkdir(parents=True)
    (pkg / "Listener.java").write_text("package org.acme;\npublic class Listener extends Base { }\n")
    (pkg / "Base.java").write_text("package org.acme;\nabstract class Base extends Root { String header() { return null; } }\n")
    (pkg / "Root.java").write_text("package org.acme;\nabstract class Root extends java.util.AbstractList<String> { }\n")
    text = inherited_source(pkg / "Listener.java")
    assert "// Base.java" in text and "// Root.java" in text and text.index("Base.java") < text.index("Root.java")
    assert inherited_source(pkg / "Root.java") == ""   # parent outside the project: nothing


def test_primitive_arrays_bytes_and_nested_classes():
    from eventbreaker.scenarios.executor import _INSTANTIATE_HELPER as helper
    assert "if (rt.isArray()) {" in helper                                   # byte[] body: one element, not null
    assert "rt == byte[].class ? text.getBytes(" in helper                   # STRING:<json> sets a byte[] body
    assert helper.count("load(name)") == 2 and 'n.substring(0, dot) + "$"' in helper   # statics + casts
