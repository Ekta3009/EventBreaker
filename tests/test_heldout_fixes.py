"""Generic gaps found on the held-out set (INVENTORY run 15, T1–T4, T6). Synthetic sources only."""
import subprocess

from eventbreaker.models.consumer import ConsumerAnalysis, DependencyInfo
from eventbreaker.scenarios import executor
from eventbreaker.scenarios.executor import (
    _generate_execution, _read_package, event_is_value, value_params,
)
from eventbreaker.scenarios.models import ReturnValue, TestConfig


def _listener(tmp_path, event_type: str, parameters: list[str]) -> tuple[ConsumerAnalysis, object]:
    src = tmp_path / "PaymentConsumer.java"
    src.write_text("package org.acme;\nclass PaymentConsumer {}\n")
    return ConsumerAnalysis(
        className="PaymentConsumer", methodName="listen", eventType=event_type,
        dependencies=[DependencyInfo(name="starter", type="WorkflowStarter")], parameters=parameters,
    ), src


# T1: a Kafka payload declared as String / byte[] is a value, not a fake.

def test_string_event_is_a_value_read_after_the_overrides(tmp_path):
    analysis, src_file = _listener(tmp_path, "String", ["String message"])
    tc = TestConfig(callPattern="SINGLE", returnValues=[ReturnValue(target="param.event", returns='STRING:{"amount":5}')])
    src = _generate_execution(analysis, src_file, tc, "TEST")
    put = src.index('RETURNS.put("param.event", "STRING:{\\"amount\\":5}");')
    read = src.index('String event = (String) eventbreaker.generated.__EBInstantiate.param("event", String.class);')
    assert put < read < src.index('"listen", event)')
    assert "mock(eventbreaker.generated.__EBInstantiate.typeFor(String.class" not in src


def test_byte_array_event_is_a_value_too(tmp_path):
    analysis, src_file = _listener(tmp_path, "byte[]", ["byte[] body"])
    assert event_is_value(analysis)
    src = _generate_execution(analysis, src_file, TestConfig(callPattern="DUPLICATE"), "TEST")
    assert 'byte[] event = (byte[]) eventbreaker.generated.__EBInstantiate.param("event", byte[].class);' in src


def test_value_event_is_offered_to_path_setup_and_typed_events_are_not(tmp_path):
    analysis, _ = _listener(tmp_path, "String", ["@Payload String message", "Acknowledgment ack"])
    assert [p.name for p in value_params(analysis)] == ["event"]
    typed, _ = _listener(tmp_path, "OrderPlaced", ["OrderPlaced e"])
    assert value_params(typed) == [] and not event_is_value(typed)


# T2: the package line can come after a licence header of any length.

def test_package_after_a_long_licence_header(tmp_path):
    f = tmp_path / "A.java"
    f.write_text("/*\n" + " * licence line\n" * 40 + " */\n\n// package not.this;\npackage org.hisp.dhis.audit;\n")
    assert _read_package(f) == "org.hisp.dhis.audit"


# T3: `@EventListener(X.class) void m()` is called with no arguments.

def test_no_parameter_listener_is_called_without_arguments(tmp_path):
    analysis, src_file = _listener(tmp_path, "ReloadHooks", [])
    src = _generate_execution(analysis, src_file, TestConfig(callPattern="DUPLICATE"), "TEST")
    assert '__EBInstantiate.call(__ebConsumer, "listen")' in src
    assert '"listen", )' not in src


# T4 + T6: every harness JVM accepts newer class files and has the sandbox's time limit.

def test_local_run_passes_the_bytebuddy_flag_and_times_out(tmp_path, monkeypatch):
    seen = {}

    def blocked(cmd, **kwargs):
        seen["cmd"], seen["timeout"] = cmd, kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr(executor.subprocess, "run", blocked)
    assert executor._run_local(tmp_path / "h.jar", [tmp_path / "dep.jar"], tmp_path) is None
    assert seen["cmd"][:2] == ["java", "-Dnet.bytebuddy.experimental=true"] and seen["timeout"] == 180


def test_sandbox_command_passes_the_bytebuddy_flag():
    import inspect
    from eventbreaker.sandbox import executor as sandbox
    assert "java {BYTEBUDDY_ANY_JAVA} -jar" in inspect.getsource(sandbox.SandboxExecutor._run_once)


# T7: a logger in an instance field is the listener's own real object, never a faked dependency.

def test_instance_logger_field_is_not_a_dependency(tmp_path):
    from eventbreaker.analyzer import bridge
    f = tmp_path / "Importer.java"
    f.write_text(
        "package org.acme;\n"
        "public class Importer {\n"
        "    private org.slf4j.Logger logger = org.slf4j.LoggerFactory.getLogger(getClass());\n"
        "    private final Log log2 = null;\n"
        "    @Autowired private Client client;\n"
        "    @KafkaListener(topics = \"t\")\n"
        "    public void listen(String raw) { logger.debug(raw); client.index(raw); }\n"
        "}\n")
    a = bridge.analyze(f, "listen")
    assert [d.name for d in a.dependencies] == ["client"]
