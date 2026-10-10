import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationEntry, ObservationResult
from eventbreaker.sandbox.executor import SandboxExecutor
from eventbreaker.scenarios.models import ChaosScenario, TestConfig


# ── Temp Maven project pom.xml ────────────────────────────────────────────────
#
# No JUnit. Mockito is compile-scoped so it ends up in the fat JAR.
# maven-shade-plugin bundles everything into a single executable JAR.
# ServicesResourceTransformer preserves Mockito's META-INF/services files
# so the mock maker is found correctly at runtime.

_POM = """\
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0
             http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>eventbreaker.generated</groupId>
    <artifactId>scenario-execution</artifactId>
    <version>1.0.0</version>

    <properties>
        <maven.compiler.source>21</maven.compiler.source>
        <maven.compiler.target>21</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    </properties>

    <dependencies>
        <dependency>
            <groupId>org.mockito</groupId>
            <artifactId>mockito-core</artifactId>
            <version>5.14.2</version>
        </dependency>
        <dependency>
            <groupId>com.fasterxml.jackson.core</groupId>
            <artifactId>jackson-databind</artifactId>
            <version>2.17.1</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-shade-plugin</artifactId>
                <version>3.5.2</version>
                <executions>
                    <execution>
                        <phase>package</phase>
                        <goals><goal>shade</goal></goals>
                        <configuration>
                            <createDependencyReducedPom>false</createDependencyReducedPom>
                            <finalName>eventbreaker-execution</finalName>
                            <transformers>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ManifestResourceTransformer">
                                    <mainClass>eventbreaker.generated.EventBreakerExecution</mainClass>
                                </transformer>
                                <transformer implementation="org.apache.maven.plugins.shade.resource.ServicesResourceTransformer"/>
                            </transformers>
                        </configuration>
                    </execution>
                </executions>
            </plugin>
        </plugins>
    </build>
</project>
"""

# ── Generated execution class template ───────────────────────────────────────
#
# Single unified Java main class — no test framework.
# Uses plain string replacement (UPPER_CASE markers) to avoid
# escaping conflicts between Python f-strings and Java braces.
# Mockito is used purely as an invocation recorder.
#
# Marker ordering rules (to avoid substring replacement bugs):
#   CHAIN_STUBS must be replaced before STUBS (CHAIN_STUBS contains "STUBS")
#   FAULT_INJECTIONS and CALL_PATTERN carry the scenario-specific code.

_EXECUTION_TEMPLATE = """\
package eventbreaker.generated;

import org.mockito.Mockito;
import org.mockito.invocation.Invocation;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.*;

CONSUMER_IMPORT
EXTRA_IMPORTS

/**
 * EventBreaker generated execution harness.
 * Scenario: SCENARIO_TYPE
 *
 * Mocks all consumer dependencies as recorders, runs the consumer
 * under a controlled scenario, and reports invocation counts as JSON.
 */
public class EventBreakerExecution {

    public static void main(String[] args) {
        try {
            run();
        } catch (Exception e) {
            System.out.println("EVENTBREAKER_ERROR:" + e.getMessage());
            System.exit(1);
        }
    }

    private static Object eventAnswer(org.mockito.invocation.InvocationOnMock inv) throws Throwable {
        Class<?> rt = inv.getMethod().getReturnType();
        if (rt == int.class || rt == Integer.class) return 1;
        if (rt == long.class || rt == Long.class) return 1L;
        if (rt == double.class || rt == Double.class) return 1.0;
        if (rt == float.class || rt == Float.class) return 1.0f;
        if (rt == short.class || rt == Short.class) return (short) 1;
        if (rt == byte.class || rt == Byte.class) return (byte) 1;
        if (rt == boolean.class || rt == Boolean.class) return true;
        return RETURNS_DEFAULTS.answer(inv);
    }

    private static void run() throws Exception {

        // ── Instrumented dependencies (recorders, not simulators) ──────────
        //
        // Each dependency is replaced with a Mockito recorder that counts
        // every invocation, tracks call ordering, and whether each call threw.
        List<String> callSequence = Collections.synchronizedList(new ArrayList<>());
MOCK_DECLARATIONS

        // ── Stub methods that return objects (prevents NullPointerException)
STUBS

        // ── Instantiate the real consumer with recorder dependencies ───────
CONSUMER_INSTANTIATION

        // ── Create the event ───────────────────────────────────────────────
        // Numeric getters return 1 and boolean getters return true, so guard
        // clauses like `if (event.getAmount() <= 0) return;` don't short-circuit.
        EVENT_CLASS event = mock(EVENT_CLASS.class,
            withSettings().defaultAnswer(EventBreakerExecution::eventAnswer));
CHAIN_STUBS

        // ── Reset counters — exclude stubbing-phase invocations ────────────
        // when(...).thenReturn(...) triggers one invocation on the stub target.
        // Clear Mockito log, threwCounts, and callSequence so only scenario-time
        // calls appear in the final report.
RESET_INVOCATIONS

        // ── Fault injections ───────────────────────────────────────────────
FAULT_INJECTIONS

        // doThrow/doAnswer setup above fires invocation listeners — clear so
        // callSequence only contains actual scenario-time calls.
        callSequence.clear();

        // ── Execute scenario ───────────────────────────────────────────────
        AtomicBoolean consumerThrew = new AtomicBoolean(false);
CALL_PATTERN

        // ── Collect invocation counts from every recorder ──────────────────
        Map<String, Object> deps = new LinkedHashMap<>();
DEP_MAP_ENTRIES

        ObjectMapper mapper = new ObjectMapper();
        ArrayNode observations = mapper.createArrayNode();

        for (Map.Entry<String, Object> entry : deps.entrySet()) {
            Collection<Invocation> invocations =
                Mockito.mockingDetails(entry.getValue()).getInvocations();
            Map<String, Integer> counts = new LinkedHashMap<>();
            for (Invocation inv : invocations) {
                counts.merge(inv.getMethod().getName(), 1, Integer::sum);
            }
            for (Map.Entry<String, Integer> mc : counts.entrySet()) {
                ObjectNode obs = mapper.createObjectNode();
                obs.put("target", entry.getKey() + "." + mc.getKey());
                obs.put("callCount", mc.getValue());
                obs.put("threw", threwCounts.getOrDefault(entry.getKey() + "." + mc.getKey(), 0) > 0);
                observations.add(obs);
            }
        }

        ObjectNode result = mapper.createObjectNode();
        result.put("scenario", "SCENARIO_TYPE");
        result.put("consumerThrew", consumerThrew.get());
        ArrayNode seqNode = mapper.createArrayNode();
        callSequence.forEach(seqNode::add);
        result.set("callSequence", seqNode);
        result.set("observations", observations);

        System.out.println(
            "EVENTBREAKER_RESULT:" + mapper.writeValueAsString(result)
        );
    }
}
"""


# ─────────────────────────────────────────────────────────────────────────────

class ScenarioExecutor:

    def execute(
        self,
        scenario: ChaosScenario,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
    ) -> ObservationResult:
        # Use a stable cache directory keyed by a hash of the generated code.
        # Different scenarios (different testConfig) → different cache keys.
        execution_code = _generate_execution(
            analysis, consumer_file, scenario.testConfig, scenario.scenarioType
        )
        cache_key = hashlib.sha256(
            (execution_code + consumer_file.read_text(errors="replace")).encode()
        ).hexdigest()[:16]
        cache_dir = Path.home() / ".eventbreaker" / "cache" / cache_key
        project_dir = cache_dir / "scenario"

        freshly_created = not (project_dir / "pom.xml").exists()
        if freshly_created:
            self._create_project(project_dir, consumer_file)

        # Always overwrite execution class and stubs in case analysis changed
        self._write_execution(
            project_dir, analysis, consumer_file, scenario.testConfig, scenario.scenarioType
        )
        _generate_stubs(project_dir, analysis, consumer_file)
        _generate_chain_stubs(project_dir, analysis, consumer_file)

        result = self._run(
            project_dir, scenario, analysis, consumer_file, cached=_jar_exists(project_dir)
        )

        # Stale cache recovery: if the build failed on an inherited project dir,
        # the stubs may be corrupted from a previous session. Delete and retry once.
        if result.status == "ERROR" and not freshly_created and not _jar_exists(project_dir):
            shutil.rmtree(project_dir, ignore_errors=True)
            self._create_project(project_dir, consumer_file)
            self._write_execution(
                project_dir, analysis, consumer_file, scenario.testConfig, scenario.scenarioType
            )
            _generate_stubs(project_dir, analysis, consumer_file)
            _generate_chain_stubs(project_dir, analysis, consumer_file)
            result = self._run(project_dir, scenario, analysis, consumer_file, cached=False)

        return result

    # ── Project setup ─────────────────────────────────────────────────────────

    def _create_project(self, project_dir: Path, consumer_file: Path) -> None:
        project_dir.mkdir(parents=True)
        (project_dir / "pom.xml").write_text(_POM)

        main_src = project_dir / "src" / "main" / "java"
        main_src.mkdir(parents=True)

        # Tell Mockito to use subclass-based mocking (no JVM agent required).
        # This works for interfaces (standard proxy) and non-final concrete
        # classes (subclass) without any --add-opens or -javaagent flags.
        mockito_ext = (
            project_dir / "src" / "main" / "resources"
            / "mockito-extensions"
        )
        mockito_ext.mkdir(parents=True)
        (mockito_ext / "org.mockito.plugins.MockMaker").write_text(
            "mock-maker-subclass\n"
        )

        maven_root = _find_maven_root(consumer_file)
        if maven_root:
            origin_src = maven_root / "src" / "main" / "java"
            if origin_src.exists():
                for java_file in origin_src.rglob("*.java"):
                    # Skip analyzer — it depends on JavaParser
                    if "analyzer" not in java_file.parts:
                        dest = main_src / java_file.relative_to(origin_src)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(java_file, dest)

        # If the consumer file lives outside src/main/java (e.g. test resources),
        # copy it to the correct package directory in the temp project.
        if maven_root is None or not _is_under(
            consumer_file, maven_root / "src" / "main" / "java"
        ):
            pkg = _read_package(consumer_file) or ""
            pkg_dir = main_src / Path(*pkg.split(".")) if pkg else main_src
            pkg_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(consumer_file, pkg_dir / consumer_file.name)

    def _write_execution(
        self,
        project_dir: Path,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
        test_config: "TestConfig | None",
        scenario_type: str = "UNKNOWN",
    ) -> None:
        exec_dir = (
            project_dir
            / "src" / "main" / "java"
            / "eventbreaker" / "generated"
        )
        exec_dir.mkdir(parents=True, exist_ok=True)
        code = _generate_execution(analysis, consumer_file, test_config, scenario_type)
        (exec_dir / "EventBreakerExecution.java").write_text(code)

    # ── Build + run ───────────────────────────────────────────────────────────

    def _run(
        self,
        project_dir: Path,
        scenario: ChaosScenario,
        analysis: ConsumerAnalysis,
        consumer_file: Path,
        cached: bool = False,
    ) -> ObservationResult:

        # Step 1: compile + package into a self-contained fat JAR.
        # Skipped when a cached JAR already exists for this consumer.
        # Uses an iterative fix loop: if javac reports missing methods on stubs,
        # patch them in and retry (max 3 attempts).
        if not cached:
            last_output = ""
            built = False
            for _ in range(3):
                build = subprocess.run(
                    ["mvn", "package", "--no-transfer-progress", "-DskipTests", "-e"],
                    capture_output=True,
                    text=True,
                    cwd=project_dir,
                )
                last_output = build.stdout + "\n" + build.stderr
                if build.returncode == 0:
                    built = True
                    break
                fixable = (
                    "cannot find symbol" in last_output
                    or "cannot be applied to given types" in last_output
                    or "actual and formal argument lists differ in length" in last_output
                    or "incompatible types" in last_output
                )
                if fixable and _patch_stubs_from_errors(project_dir, last_output, analysis, consumer_file):
                    continue  # stubs were patched — retry
                break  # genuine error, stop

            if not built:
                return ObservationResult(
                    scenario=scenario.scenarioType,
                    status="ERROR",
                    rawOutput=last_output,
                    executionError=_extract_build_error(last_output),
                )

        # Step 2: run the fat JAR — in Nebius Sandbox if credentials available,
        # otherwise fall back to local java.
        jar = project_dir / "target" / "eventbreaker-execution.jar"

        if os.environ.get("NEBIUS_PROJECT_ID"):
            try:
                stdout, stderr = SandboxExecutor().execute(jar)
                output = stdout + "\n" + stderr
            except (RuntimeError, EnvironmentError) as e:
                return ObservationResult(
                    scenario=scenario.scenarioType,
                    status="ERROR",
                    executionError=f"Sandbox execution failed: {e}",
                )
        else:
            run = subprocess.run(
                ["java", "-jar", str(jar)],
                capture_output=True,
                text=True,
                cwd=project_dir,
            )
            output = run.stdout + "\n" + run.stderr

        return _parse_output(output, scenario)


# ── Code generator ────────────────────────────────────────────────────────────

def _generate_execution(
    analysis: ConsumerAnalysis,
    consumer_file: Path,
    test_config: "TestConfig | None",
    scenario_type: str = "UNKNOWN",
) -> str:
    consumer_pkg = _read_package(consumer_file) or ""
    consumer_import = (
        f"import {consumer_pkg}.{analysis.className};"
        if consumer_pkg else ""
    )

    # Start with whatever the consumer itself imports.
    consumer_imports = _read_imports(consumer_file)

    # The generated class lives in eventbreaker.generated — it cannot see the
    # consumer's package implicitly. Any type that the consumer uses without an
    # explicit import (i.e. same-package types) must be imported explicitly here.
    already_imported = {
        imp.split()[-1].rstrip(";").rsplit(".", 1)[-1]
        for imp in consumer_imports
        if imp.startswith("import ")
    }
    pkg_types: set[str] = set()
    pkg_types.add(analysis.eventType)
    for dep in analysis.dependencies:
        pkg_types.add(dep.type.split("<")[0].strip())
    for vi in analysis.variableInitializations:
        if vi.variableType:
            pkg_types.add(vi.variableType.split("<")[0].strip())
    for oc in analysis.objectCreations:
        if oc.type:
            pkg_types.add(oc.type.split("<")[0].strip())

    _skip = {
        "void", "boolean", "int", "long", "double", "float", "byte", "char",
        "String", "Integer", "Long", "Double", "Float", "Boolean", "Object",
        "List", "Map", "Set", "Collection", "Optional",
        "Exception", "RuntimeException",
    }
    extra_pkg_imports = [
        f"import {consumer_pkg}.{t};"
        for t in sorted(pkg_types)
        if t and t not in already_imported and t not in _skip and consumer_pkg
    ]

    extra_imports = "\n".join(consumer_imports + extra_pkg_imports)

    mock_decl_lines = ["        Map<String, Integer> threwCounts = new LinkedHashMap<>();"]
    for d in analysis.dependencies:
        dtype = d.type.split("<")[0].strip()
        mock_decl_lines.append(
            f"        {d.type} {d.name} = mock({dtype}.class, withSettings()\n"
            f'            .invocationListeners(report -> {{\n'
            f'                String __ebMn = ((org.mockito.invocation.Invocation) report.getInvocation()).getMethod().getName();\n'
            f'                callSequence.add("{d.name}." + __ebMn);\n'
            f'                if (report.threwException()) threwCounts.merge("{d.name}." + __ebMn, 1, Integer::sum);\n'
            f"            }}));"
        )
    mock_declarations = "\n".join(mock_decl_lines)

    # Final JDK types that Mockito cannot subclass — skip mock() for these.
    # The dependency mock already returns null for them by default, which is fine
    # since the result is passed to another mock anyway.
    _FINAL_JDK = frozenset({
        "String", "Integer", "Long", "Double", "Float", "Boolean",
        "Byte", "Character", "Short", "BigDecimal", "BigInteger",
        "UUID", "LocalDate", "LocalDateTime", "Instant",
    })

    dep_names = {d.name for d in analysis.dependencies}
    stubs_lines: list[str] = []

    # local_mocks: varName -> mockVarName for intermediate objects
    # e.g. "order" -> "mockOrder" when order = orderRepository.find(...)
    local_mocks: dict[str, str] = {}

    for vi in analysis.variableInitializations:
        call = vi.initializerMethodCall
        if call and call.scope in dep_names:
            vtype = vi.variableType or ""
            raw_type = vtype.split("<")[0].strip()
            if raw_type in _FINAL_JDK:
                continue  # can't mock final JDK types; null return is acceptable
            if raw_type in _PRIMITIVE_STUB_VALUES:
                ret_val = _PRIMITIVE_STUB_VALUES[raw_type]
            elif raw_type in _COLLECTION_STUB_VALUES:
                ret_val = _COLLECTION_STUB_VALUES[raw_type]
            else:
                mock_var = f"mock{raw_type}"
                local_mocks[vi.variableName] = mock_var
                decl = f"        {vtype} {mock_var} = mock({raw_type}.class);"
                if decl not in stubs_lines:
                    stubs_lines.append(decl)
                ret_val = mock_var
            for matchers in _matcher_lists(analysis, call.scope, call.methodName):
                stub_line = (
                    f"        when({call.scope}.{call.methodName}({matchers}))"
                    f".thenReturn({ret_val});"
                )
                if stub_line not in stubs_lines:
                    stubs_lines.append(stub_line)

    # Stub non-void methods called on intermediate mocks so consumers with
    # conditional branches (e.g. if (order.getStatus().equals("READY")))
    # don't NPE.  We only stub methods that are assigned to a variable
    # (i.e. they appear in variableInitializations), which guarantees
    # they are non-void.  No argument matchers — varargs stubs called with
    # zero args match the no-matcher form correctly.
    #
    # Return value strategy: use the first string literal found in any
    # .equals() call in the consumer, so conditions evaluate to true and
    # all branches execute.  Falls back to "" if none found.
    equals_literals: list[str] = []
    for mc in analysis.methodCalls:
        if mc.methodName == "equals" and mc.arguments:
            lit = mc.arguments[0].strip('"').strip("'")
            if lit:
                equals_literals.append(lit)

    for vi in analysis.variableInitializations:
        call = vi.initializerMethodCall
        if not call or call.scope not in local_mocks:
            continue
        if vi.variableType in _FINAL_JDK:
            # String/Integer etc — can't return a mock, return a literal
            ret_val = f'"{equals_literals[0]}"' if equals_literals else '""'
        else:
            continue  # non-void object types already handled by their own mock
        mock_var = local_mocks[call.scope]
        stub_line = (
            f"        when({mock_var}.{call.methodName}())"
            f".thenReturn({ret_val});"
        )
        if stub_line not in stubs_lines:
            stubs_lines.append(stub_line)

    # Method chains (event.getA().b(), dep.find(x).getB().c()): the chain root's
    # method returns the shared __EBChain mock, which returns itself for every
    # further chained call (RETURNS_SELF) so chains of any depth don't NPE.
    event_var = _get_event_var_name(analysis)
    chain_roots, _chain_methods, _typed = _analyze_chains(analysis)
    if chain_roots:
        stubs_lines.insert(0,
            "        eventbreaker.generated.__EBChain __ebChain ="
            " mock(eventbreaker.generated.__EBChain.class, RETURNS_SELF);"
        )
    for root, methods in sorted(chain_roots.items()):
        if root == event_var:
            continue
        for method in sorted(methods):
            for matchers in _matcher_lists(analysis, root, method):
                stubs_lines.append(
                    f"        when({root}.{method}({matchers})).thenReturn(__ebChain);"
                )

    stubs = "\n".join(stubs_lines)

    # Stub event method chains — must come AFTER 'event' is declared in the harness.
    event_stub_lines: list[str] = []
    for method in sorted(chain_roots.get(event_var, set())):
        event_stub_lines.append(
            f"        when(event.{method}()).thenReturn(__ebChain);"
        )

    # Stub boolean methods called directly on the event (e.g. event.isPriority()).
    # Mockito defaults all booleans to false, which locks conditional consumers into
    # the else-branch.  Return true so all branches execute and all risks are tested.
    _BOOL_PREFIX = re.compile(r'^(is|has|was|can|should|will)[A-Z_]')
    seen_bool_stubs: set[str] = set()
    for mc in analysis.methodCalls:
        if (
            mc.scope == event_var
            and (_BOOL_PREFIX.match(mc.methodName) or mc.methodName in _KNOWN_BOOLEAN_METHODS)
            and mc.methodName not in seen_bool_stubs
        ):
            seen_bool_stubs.add(mc.methodName)
            event_stub_lines.append(
                f"        when(event.{mc.methodName}()).thenReturn(true);"
            )

    event_stubs = "\n".join(event_stub_lines)

    dep_map_entries = "\n".join(
        f'        deps.put("{d.name}", {d.name});'
        for d in analysis.dependencies
    )

    dep_names_csv = ", ".join(d.name for d in analysis.dependencies)
    reset_invocations = (
        f"        threwCounts.clear();\n"
        f"        callSequence.clear();\n"
        f"        clearInvocations({dep_names_csv});"
        if analysis.dependencies else
        "        threwCounts.clear();\n        callSequence.clear();"
    )

    # Choose constructor injection vs. reflection field injection.
    # If the consumer source contains an explicit constructor that accepts the
    # dependencies, use it directly.  Otherwise (e.g. Spring field injection),
    # create the consumer with its default no-arg constructor and inject fields
    # via reflection — this avoids a compile error on a non-existent constructor.
    if _has_explicit_constructor(consumer_file, analysis.className):
        constructor_args = ", ".join(d.name for d in analysis.dependencies)
        consumer_instantiation = (
            f"        {analysis.className} consumer = "
            f"new {analysis.className}({constructor_args});"
        )
    else:
        lines = [
            f"        {analysis.className} consumer = new {analysis.className}();"
        ]
        for dep in analysis.dependencies:
            lines += [
                f"        {{",
                f'            java.lang.reflect.Field _f = consumer.getClass().getDeclaredField("{dep.name}");',
                f"            _f.setAccessible(true);",
                f"            _f.set(consumer, {dep.name});",
                f"        }}",
            ]
        consumer_instantiation = "\n".join(lines)

    fault_injections = _build_fault_injections(test_config, analysis) if test_config else ""
    call_pattern = _build_call_pattern(analysis, test_config) if test_config else (
        f"        try {{\n"
        f"            consumer.{analysis.methodName}(event);\n"
        f"        }} catch (Exception __eb_ex) {{\n"
        f"            consumerThrew.set(true);\n"
        f"        }}"
    )

    return (
        _EXECUTION_TEMPLATE
        .replace("CONSUMER_IMPORT", consumer_import)
        .replace("EXTRA_IMPORTS", extra_imports)
        .replace("MOCK_DECLARATIONS", mock_declarations)
        .replace("CHAIN_STUBS", event_stubs)
        .replace("STUBS", stubs)
        .replace("CONSUMER_INSTANTIATION", consumer_instantiation)
        .replace("EVENT_CLASS", analysis.eventType)
        .replace("DEP_MAP_ENTRIES", dep_map_entries)
        .replace("RESET_INVOCATIONS", reset_invocations)
        .replace("FAULT_INJECTIONS", fault_injections)
        .replace("CALL_PATTERN", call_pattern)
        .replace("SCENARIO_TYPE", scenario_type)
    )


# ── TestConfig → Java builders ────────────────────────────────────────────────

def _build_fault_injections(test_config: TestConfig, analysis: ConsumerAnalysis) -> str:
    """Build Mockito stub lines that inject faults declared in the TestConfig.

    Uses doThrow/doAnswer/doNothing syntax instead of when().thenX() so that
    void-returning dependency methods compile correctly ('void' type not allowed
    in when() expressions).

    One stub line is emitted per call arity seen in the consumer — a single any()
    only matches one-argument calls on varargs stubs.
    """
    if not test_config.faultInjections:
        return ""
    lines: list[str] = []
    for fi in test_config.faultInjections:
        target = f"{fi.dep}.{fi.method}"
        for matchers in _matcher_lists(analysis, fi.dep, fi.method):
            if fi.fault == "THROW":
                lines.append(
                    f'        doThrow(new RuntimeException("EventBreaker: THROW on {target}"))'
                    f'.when({fi.dep}).{fi.method}({matchers});'
                )
            elif fi.fault == "THROW_ONCE":
                # doAnswer(inv -> null) is safe for both void and non-void methods.
                # doNothing() is only valid for void methods and causes a runtime error
                # on Object-returning methods like findById, find, etc.
                lines.append(
                    f'        doThrow(new RuntimeException("EventBreaker: THROW_ONCE on {target}"))'
                    f'.doAnswer(inv -> null).when({fi.dep}).{fi.method}({matchers});'
                )
            elif fi.fault == "DELAY":
                delay_ms = fi.delayMs or 500
                lines.append(
                    f'        doAnswer(inv -> {{ Thread.sleep({delay_ms}); return null; }}'
                    f').when({fi.dep}).{fi.method}({matchers});'
                )
    return "\n".join(lines)


def _matcher_lists(analysis: ConsumerAnalysis, scope: str, method: str) -> list[str]:
    """Return one argument-matcher list per distinct arity of scope.method(...).

    Mockito 5 matches varargs element-by-element, so the matcher count must equal
    the argument count at the call site. Falls back to a single any() when the
    call isn't found in the analysis.
    """
    arities: set[int] = set()

    def visit(calls: list) -> None:
        for mc in calls:
            if mc.scope == scope and mc.methodName == method:
                arities.add(len(mc.arguments))
            visit(mc.nestedCalls)

    visit(analysis.methodCalls)
    for vi in analysis.variableInitializations:
        if vi.initializerMethodCall:
            visit([vi.initializerMethodCall])
    if not arities:
        arities = {1}
    return [", ".join(["any()"] * n) for n in sorted(arities)]


def _build_call_pattern(analysis: ConsumerAnalysis, test_config: TestConfig) -> str:
    """Build the consumer invocation block matching the requested callPattern."""
    call = f"consumer.{analysis.methodName}(event)"
    if test_config.callPattern == "SINGLE":
        return (
            f"        try {{\n"
            f"            {call};\n"
            f"        }} catch (Exception __eb_ex) {{\n"
            f"            consumerThrew.set(true);\n"
            f"        }}"
        )
    if test_config.callPattern == "DUPLICATE":
        return (
            f"        try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); }}\n"
            f"        try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); }}"
        )
    if test_config.callPattern == "CONCURRENT":
        return (
            f"        Thread __eb_t1 = new Thread(() -> {{\n"
            f"            try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); }}\n"
            f"        }});\n"
            f"        Thread __eb_t2 = new Thread(() -> {{\n"
            f"            try {{ {call}; }} catch (Exception __eb_ex) {{ consumerThrew.set(true); }}\n"
            f"        }});\n"
            f"        __eb_t1.start(); __eb_t2.start();\n"
            f"        __eb_t1.join(); __eb_t2.join();"
        )
    # fallback — should not happen (feasibility check guards against unknown patterns)
    return f"        {call};"


# ── Output parser ─────────────────────────────────────────────────────────────

def _parse_output(output: str, scenario: ChaosScenario) -> ObservationResult:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("EVENTBREAKER_RESULT:"):
            data = json.loads(stripped[len("EVENTBREAKER_RESULT:"):])
            entries = [
                ObservationEntry(
                    target=o["target"],
                    callCount=o["callCount"],
                    threw=o.get("threw", False),
                )
                for o in data.get("observations", [])
            ]
            return ObservationResult(
                scenario=scenario.scenarioType,
                status="REPRODUCED",
                observations=entries,
                rawOutput=output,
                consumerThrew=data.get("consumerThrew", False),
                callSequence=data.get("callSequence", []),
            )

    return ObservationResult(
        scenario=scenario.scenarioType,
        status="ERROR",
        observations=[],
        rawOutput=output,
        executionError=(
            "No EVENTBREAKER_RESULT found. "
            "The build or execution may have failed — check rawOutput."
        ),
    )



# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_maven_root(start: Path) -> Path | None:
    current = start if start.is_dir() else start.parent
    for _ in range(10):
        if (current / "pom.xml").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _read_package(java_file: Path) -> str | None:
    for line in java_file.read_text(errors="replace").splitlines()[:20]:
        m = re.match(r"^\s*package\s+([\w.]+)\s*;", line)
        if m:
            return m.group(1)
    return None


def _extract_build_error(output: str) -> str:
    """Pull the most useful lines from Maven output for user-facing display."""
    error_lines = []
    missing_symbols: list[str] = []

    for line in output.splitlines():
        stripped = line.strip()
        # Java compiler errors: "[ERROR] /file.java:[7,19] ..." or "error: cannot find symbol"
        if stripped.lower().startswith("[error]") or "error:" in stripped.lower():
            error_lines.append(stripped)
        # Capture "symbol: class Foo" lines that follow cannot-find-symbol
        if stripped.startswith("symbol:") or stripped.startswith("location:"):
            error_lines.append(stripped)
        if "cannot find symbol" in stripped.lower():
            missing_symbols.append(stripped)

    if missing_symbols:
        unique = list(dict.fromkeys(error_lines))[:10]
        detail = "\n".join(unique)
        return (
            "Build failed: the consumer references types that EventBreaker "
            "could not find on the classpath.\n\n"
            "Tip: run EventBreaker from the root of the Maven project that "
            "contains the consumer so all dependency classes are available.\n\n"
            f"Compiler errors:\n{detail}"
        )

    if error_lines:
        return "Build failed:\n" + "\n".join(error_lines[:10])

    # Fallback: last non-empty lines of output
    last = [l for l in output.splitlines() if l.strip()][-8:]
    return "Build failed:\n" + "\n".join(last)


def _jar_exists(project_dir: Path) -> bool:
    return (project_dir / "target" / "eventbreaker-execution.jar").exists()


def _read_imports(java_file: Path) -> list[str]:
    imports = []
    for line in java_file.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            imports.append(stripped)
        elif re.match(r"^(public\s+)?(class|interface|enum|record)\s+", stripped):
            break
    return imports


def _has_explicit_constructor(java_file: Path, class_name: str) -> bool:
    """Return True if the Java source contains an explicit constructor declaration."""
    source = java_file.read_text(errors="replace")
    return bool(re.search(rf"\bpublic\s+{re.escape(class_name)}\s*\(", source))


# ── Stub generator ────────────────────────────────────────────────────────────

# JDK built-in types that never need stubs.
_JDK_TYPES: frozenset[str] = frozenset({
    "void", "boolean", "int", "long", "double", "float", "byte", "char", "short",
    "String", "Integer", "Long", "Double", "Float", "Boolean", "Byte", "Character",
    "Short", "Object", "Number", "Comparable",
    "List", "Map", "Set", "Collection", "Iterable", "Iterator",
    "Optional", "Stream",
    "Exception", "RuntimeException", "Throwable", "Error",
    "Override", "Deprecated", "SuppressWarnings",
})


def _generate_stubs(
    project_dir: Path,
    analysis: ConsumerAnalysis,
    consumer_file: Path,
) -> None:
    """Write minimal Java stub classes for every type the consumer references
    that isn't already present in the temp project's source tree.

    Stubs are concrete classes with a no-arg constructor — enough for the Java
    compiler to resolve the name and for Mockito to subclass at runtime.
    """
    consumer_pkg = _read_package(consumer_file) or ""

    # Build simple_name -> package map from the consumer's import statements.
    import_pkg: dict[str, str] = {}
    for imp in _read_imports(consumer_file):
        m = re.match(r"import\s+([\w.]+)\.([\w]+)\s*;", imp)
        if m:
            import_pkg[m.group(2)] = m.group(1)

    # Collect every type name the consumer analysis surfaces.
    # generic_types: raw names that appeared with type parameters (e.g. "ProductRepository"
    # from "ProductRepository<Product>") — their stub must declare <T>.
    type_names: set[str] = set()
    generic_types: set[str] = set()
    type_names.add(analysis.eventType)
    for dep in analysis.dependencies:
        raw = dep.type.split("<")[0].strip()
        type_names.add(raw)
        if "<" in dep.type:
            generic_types.add(raw)
    for vi in analysis.variableInitializations:
        if vi.variableType:
            raw = vi.variableType.split("<")[0].strip()
            type_names.add(raw)
            if "<" in vi.variableType:
                generic_types.add(raw)
    for oc in analysis.objectCreations:
        if oc.type:
            type_names.add(oc.type.split("<")[0].strip())

    # Never stub the consumer class itself — it already exists in the project.
    type_names.discard(analysis.className)

    main_src = project_dir / "src" / "main" / "java"

    for name in type_names:
        # Guard against anything with generics still slipping through.
        name = name.split("<")[0].strip()
        if not name or name in _JDK_TYPES:
            continue

        pkg = import_pkg.get(name, consumer_pkg)
        pkg_parts = pkg.split(".") if pkg else []
        dest = main_src.joinpath(*pkg_parts) / f"{name}.java" if pkg_parts else main_src / f"{name}.java"

        if dest.exists():
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_stub_source(pkg, name, generic=name in generic_types))


def _stub_source(pkg: str, name: str, generic: bool = False) -> str:
    """Minimal compilable Java class — just enough for the compiler and Mockito.

    Uses a varargs constructor so any call — new Foo(), new Foo(a), new Foo(a,b) —
    compiles without needing to know the real constructor signature.
    Mockito (Objenesis) does not call constructors when creating mocks, so this
    is safe at runtime.

    When generic=True the class is declared with <T> so parameterised usages like
    Repository<Product> compile without "does not take parameters" errors.
    """
    pkg_line = f"package {pkg};\n\n" if pkg else ""
    decl = f"{name}<T>" if generic else name
    return f"{pkg_line}public class {decl} {{\n    public {name}(Object... args) {{}}\n}}\n"


_KNOWN_BOOLEAN_METHODS: frozenset[str] = frozenset({
    # Common Java method names that always return boolean regardless of prefix
    "contains", "containsKey", "containsValue",
    "exists", "isEmpty", "isBlank", "isPresent", "isAbsent",
    "startsWith", "endsWith", "matches", "equals", "equalsIgnoreCase",
    "remove", "add",  # Collection.remove/add return boolean
})


# Harness return values for dependency calls assigned to primitive variables.
# Non-zero / true so guard clauses (`if (points <= 0) return;`, `if (!sent) ...`)
# take the side-effect path.
_PRIMITIVE_STUB_VALUES = {
    "int": "1", "long": "1L", "short": "(short) 1", "byte": "(byte) 1",
    "double": "1.0", "float": "1.0f", "char": "'a'", "boolean": "true",
}

# Collection-typed results get real empty instances — a mocked List returns a
# null iterator and NPEs on the first for-each or String.join.
_COLLECTION_STUB_VALUES = {
    "List": "new java.util.ArrayList<>()",
    "Collection": "new java.util.ArrayList<>()",
    "Iterable": "new java.util.ArrayList<>()",
    "Set": "new java.util.HashSet<>()",
    "Map": "new java.util.HashMap<>()",
    "Optional": "java.util.Optional.empty()",
}


_JAVA_LANG_TYPES = frozenset({
    "String", "Integer", "Long", "Double", "Float", "Boolean",
    "Byte", "Character", "Short", "Object",
})


def _infer_return_type(method_name: str, default: str) -> str:
    """Infer the correct return type for a stub method.

    Uses Java naming conventions and a list of well-known boolean methods
    so we don't generate `public Object contains(...)` which breaks
    `if (store.contains(...))` compilation.
    """
    if default != "Object":
        return default
    if re.match(r"^(is|has|was|can|should|will|are|did|check|verify)[A-Z_]", method_name):
        return "boolean"
    if method_name in _KNOWN_BOOLEAN_METHODS:
        return "boolean"
    return "Object"


def _default_return_value(ret_type: str) -> str:
    """Return the appropriate zero/false/null literal for the given Java type."""
    if ret_type == "boolean":
        return "false"
    if ret_type in {"int", "long", "double", "float", "byte", "char", "short"}:
        return "0"
    return "null"


def _get_event_var_name(analysis: ConsumerAnalysis) -> str:
    """Extract the event parameter variable name from the consumer method signature."""
    for param in analysis.parameters:
        parts = param.strip().split()
        if len(parts) >= 2 and parts[0] == analysis.eventType:
            return parts[-1]
    return "event"


def _analyze_chains(
    analysis: ConsumerAnalysis,
) -> tuple[dict[str, set[str]], set[str], dict[str, str]]:
    """Return (chain_roots, chain_methods, typed_terminals).

    chain_roots:     root variable (the event or a dependency) -> methods called on
                     it whose results are further chained,
                     e.g. {"event": {"getOrder"}, "customerService": {"getCustomer"}}.
    chain_methods:   method names called on the result of a root call
                     (e.g. withCustomer, getContactInfo, getEmail).
    typed_terminals: chain method -> declared type, for chains assigned to a
                     variable (String email = dep.a(x).getEmail() -> getEmail: String).
    """
    roots = {_get_event_var_name(analysis)} | {d.name for d in analysis.dependencies}
    chain_roots: dict[str, set[str]] = {}
    chain: set[str] = set()

    def root_call(scope: str) -> tuple[str, str] | None:
        m = re.match(r"^(\w+)\.(\w+)\(", scope)
        if m and m.group(1) in roots:
            return m.group(1), m.group(2)
        return None

    def collect(calls: list) -> None:
        for call in calls:
            rc = root_call(call.scope or "")
            if rc:
                chain_roots.setdefault(rc[0], set()).add(rc[1])
                chain.add(call.methodName)
            collect(call.nestedCalls)

    collect(analysis.methodCalls)

    typed: dict[str, str] = {}
    for vi in analysis.variableInitializations:
        c = vi.initializerMethodCall
        if c and vi.variableType and root_call(c.scope or ""):
            typed[c.methodName] = vi.variableType
    return chain_roots, chain, typed


def _chain_return_type(var_type: str, consumer_pkg: str, import_pkg: dict[str, str]) -> str:
    """Java return type for a typed chain terminal declared on __EBChain."""
    raw = var_type.split("<")[0].strip()
    if raw in _PRIMITIVE_STUB_VALUES or raw in _JAVA_LANG_TYPES:
        return raw
    if raw in _COLLECTION_STUB_VALUES:
        return f"java.util.{raw}"
    pkg = import_pkg.get(raw, consumer_pkg)
    return f"{pkg}.{raw}" if pkg else raw


def _generate_chain_stubs(
    project_dir: Path,
    analysis: ConsumerAnalysis,
    consumer_file: Path,
) -> None:
    """Generate __EBChain.java and retype chain-root methods on the event/dep stubs.

    When the consumer contains chained calls like event.getA().doSomething() or
    dep.find(id).getB().getC(), the root method must return a type that declares
    the chained methods. __EBChain is that universal return type — it declares
    every chained method and returns itself so the chain can extend arbitrarily
    deep without NPE. Chain terminals assigned to a typed variable return that type.
    """
    chain_roots, chain_methods, typed = _analyze_chains(analysis)
    if not chain_methods:
        return  # no chains — nothing to do

    consumer_pkg = _read_package(consumer_file) or ""
    import_pkg: dict[str, str] = {}
    for imp in _read_imports(consumer_file):
        m = re.match(r"import\s+([\w.]+)\.([\w]+)\s*;", imp)
        if m:
            import_pkg[m.group(2)] = m.group(1)

    # Write __EBChain.java to eventbreaker/generated (same package as the harness)
    chain_dir = (
        project_dir / "src" / "main" / "java" / "eventbreaker" / "generated"
    )
    chain_dir.mkdir(parents=True, exist_ok=True)
    intermediate = {m for ms in chain_roots.values() for m in ms}
    body_lines = []
    for m in sorted(chain_methods):
        if m in typed and m not in intermediate:
            ret = _chain_return_type(typed[m], consumer_pkg, import_pkg)
            body_lines.append(
                f"    public {ret} {m}(Object... args) {{ return {_default_return_value(ret)}; }}"
            )
        else:
            body_lines.append(f"    public __EBChain {m}(Object... args) {{ return this; }}")
    (chain_dir / "__EBChain.java").write_text(
        f"package eventbreaker.generated;\n\n"
        f"public class __EBChain {{\n" + "\n".join(body_lines) + "\n}\n"
    )

    # Retype each chain-root method on its stub (event or dependency) to return __EBChain.
    root_types = {_get_event_var_name(analysis): analysis.eventType}
    root_types.update({d.name: d.type.split("<")[0].strip() for d in analysis.dependencies})
    main_src = project_dir / "src" / "main" / "java"
    for root, methods in chain_roots.items():
        type_name = root_types[root]
        pkg = import_pkg.get(type_name, consumer_pkg)
        stub_path = main_src.joinpath(*pkg.split(".")) if pkg else main_src
        stub_path = stub_path / f"{type_name}.java"
        if stub_path.exists():
            _retype_to_chain(stub_path, pkg, methods)


def _retype_to_chain(stub_path: Path, pkg: str, methods: set[str]) -> None:
    """Make each method on a generated stub return __EBChain (adding it if absent)."""
    content = stub_path.read_text()
    if "public " not in content or "(Object... args) {}" not in content:
        return  # not one of our generated stubs — a real class, leave untouched

    if "import eventbreaker.generated.__EBChain" not in content:
        pkg_stmt = f"package {pkg};" if pkg else ""
        if pkg_stmt and pkg_stmt in content:
            content = content.replace(
                pkg_stmt,
                f"{pkg_stmt}\n\nimport eventbreaker.generated.__EBChain;",
            )
        else:
            content = "import eventbreaker.generated.__EBChain;\n\n" + content

    last_brace = content.rfind("}")
    for method in sorted(methods):
        method_decl = f"public __EBChain {method}("
        if method_decl not in content:
            # Replace Object return variant if already patched, else add fresh
            old_variant = f"public Object {method}(Object... args) {{ return null; }}"
            new_variant = f"public __EBChain {method}(Object... args) {{ return null; }}"
            if old_variant in content:
                content = content.replace(old_variant, new_variant)
            else:
                content = (
                    content[:last_brace]
                    + f"\n    {new_variant}\n"
                    + content[last_brace:]
                )
                last_brace = content.rfind("}")

    stub_path.write_text(content)


def _patch_stubs_from_errors(
    project_dir: Path,
    compile_output: str,
    analysis: ConsumerAnalysis,
    consumer_file: Path | None = None,
) -> bool:
    """Parse 'cannot find symbol' javac errors and add missing methods to stubs.

    Returns True if at least one stub was patched (caller should retry build).

    Strategy:
    - method errors: add `public <ReturnType> methodName(Object... args) { return null; }`
      Return type is looked up from variableInitializations when available, else Object.
    - constructor errors: add `public ClassName(Object... args) {}`
    """
    # Pre-compute known return types from analysis:
    # variableInitializations tells us e.g. userStore.find() returns User.
    known_returns: dict[tuple[str, str], str] = {}
    for vi in analysis.variableInitializations:
        c = vi.initializerMethodCall
        if c and c.scope and c.methodName:
            known_returns[(c.scope, c.methodName)] = vi.variableType

    main_src = project_dir / "src" / "main" / "java"
    lines = compile_output.splitlines()

    # patches[fqn] = list of Java method lines to insert
    patches: dict[str, list[str]] = {}

    # incompatible_fixes[stub_file_path] = {method_name: correct_return_type}
    # Built from "incompatible types: Object cannot be converted to X" errors.
    incompatible_fixes: dict[Path, dict[str, str]] = {}

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Pattern: "[ERROR] /path/File.java:[L,C] incompatible types: Object cannot be converted to T"
        # Happens when _patch_stubs_from_errors() previously added a method returning Object
        # but the call site requires a specific type (boolean, String, etc.).
        # NOTE: javac reports FQNs like "java.lang.String" — capture ([\w.]+) then strip package.
        incompat = re.match(
            r"\[ERROR\]\s+(.+?\.java):\[(\d+),\d+\]\s+incompatible types.*"
            r"java\.lang\.Object cannot be converted to ([\w.]+)",
            stripped,
        )
        if incompat:
            error_file_path = Path(incompat.group(1))
            error_line_no = int(incompat.group(2))
            target_type_raw = incompat.group(3)
            # Strip java.lang prefix: "java.lang.String" → "String". Other packages keep
            # the FQN ("java.util.List") because stubs carry no imports.
            target_type = (
                target_type_raw.rsplit(".", 1)[-1]
                if target_type_raw.startswith("java.lang.") or "." not in target_type_raw
                else target_type_raw
            )
            # Only fix non-Object targets; skip Object→Object (no-op)
            if target_type not in {"Object"} and error_file_path.exists():
                try:
                    src_lines = error_file_path.read_text().splitlines()
                    bad_line = src_lines[error_line_no - 1] if error_line_no <= len(src_lines) else ""
                    # Find which stub file has a method whose name appears on the bad line
                    for stub_file in main_src.rglob("*.java"):
                        class_name = stub_file.stem
                        if class_name in {analysis.className, "EventBreakerExecution", "__EBChain"}:
                            continue
                        stub_content = stub_file.read_text()
                        for obj_m in re.finditer(r"public Object (\w+)\(", stub_content):
                            mname = obj_m.group(1)
                            if mname + "(" in bad_line:
                                if stub_file not in incompatible_fixes:
                                    incompatible_fixes[stub_file] = {}
                                incompatible_fixes[stub_file][mname] = target_type
                except Exception:
                    pass
            i += 1
            continue

        # Pattern: "[ERROR] /path/File.java:[L,C] bad operand types for binary operator '<='"
        #          followed by "first type: java.lang.Object" / "second type: int".
        # A stub getter returns Object but the call site uses it in arithmetic or a
        # comparison (e.g. `event.getAmount() <= 0`). Retype the getter adjacent to
        # the operator as the numeric operand type.
        bad_op = re.match(
            r"\[ERROR\]\s+(.+?\.java):\[(\d+),\d+\]\s+bad operand types for binary operator '([^']+)'",
            stripped,
        )
        if bad_op:
            error_file_path = Path(bad_op.group(1))
            error_line_no = int(bad_op.group(2))
            op = bad_op.group(3)
            operand_types = []
            for j in range(i + 1, min(i + 3, len(lines))):
                t = re.search(r"(?:first|second) type:\s+([\w.]+)", lines[j])
                if t:
                    operand_types.append(t.group(1).rsplit(".", 1)[-1])
            numeric = {"int", "long", "double", "float", "short", "byte"}
            target_type = next((t for t in operand_types if t in numeric), "int")
            if error_file_path.exists():
                try:
                    src_lines = error_file_path.read_text().splitlines()
                    bad_line = src_lines[error_line_no - 1] if error_line_no <= len(src_lines) else ""
                    op_re = re.escape(op)
                    adjacent = set(re.findall(rf"(\w+)\([^()]*\)\s*{op_re}", bad_line))
                    adjacent |= set(re.findall(rf"{op_re}\s*[\w.]*?(\w+)\([^()]*\)", bad_line))
                    for stub_file in main_src.rglob("*.java"):
                        if stub_file.stem in {analysis.className, "EventBreakerExecution", "__EBChain"}:
                            continue
                        stub_content = stub_file.read_text()
                        for obj_m in re.finditer(r"public Object (\w+)\(", stub_content):
                            if obj_m.group(1) in adjacent:
                                incompatible_fixes.setdefault(stub_file, {})[obj_m.group(1)] = target_type
                except Exception:
                    pass
            i += 1
            continue

        # Pattern 2: "constructor Foo in class pkg.Foo cannot be applied to given types"
        # Handles multi-arg constructor calls on stubs that only have a no-arg constructor.
        cannot_apply = re.search(
            r"constructor\s+(\w+)\s+in\s+class\s+([\w.]+)\s+cannot be applied", stripped
        )
        if cannot_apply:
            class_name = cannot_apply.group(1)
            fqn = cannot_apply.group(2)
            if fqn not in patches:
                patches[fqn] = []
            # Replace no-arg constructor with varargs in this stub
            patches[fqn].append(f"    public {class_name}(Object... args) {{}}")
            i += 1
            continue

        sym = re.match(r"symbol:\s+(method|constructor)\s+(\w+)", stripped)
        if sym:
            kind, name = sym.group(1), sym.group(2)
            fqn = None
            var_name = None
            for j in range(i + 1, min(i + 5, len(lines))):
                loc = lines[j].strip()
                m1 = re.search(
                    r"location:.*variable\s+(\w+)\s+of\s+type\s+([\w.]+)", loc
                )
                m2 = re.search(r"location:\s+class\s+([\w.]+)", loc)
                if m1:
                    var_name, fqn = m1.group(1), m1.group(2)
                    break
                if m2:
                    fqn = m2.group(1)
                    break

            if fqn:
                class_name = fqn.rsplit(".", 1)[-1]
                if fqn not in patches:
                    patches[fqn] = []
                if kind == "method":
                    raw_ret = known_returns.get((var_name, name), "Object") if var_name else "Object"
                    ret = _infer_return_type(name, raw_ret)
                    if ret.split("<")[0].strip() in _COLLECTION_STUB_VALUES:
                        ret = f"java.util.{ret}"  # stubs carry no imports
                    ret_val = _default_return_value(ret)
                    patches[fqn].append(
                        f"    public {ret} {name}(Object... args) {{ return {ret_val}; }}"
                        if ret != "void" else
                        f"    public void {name}(Object... args) {{}}"
                    )
                else:
                    patches[fqn].append(
                        f"    public {class_name}(Object... args) {{}}"
                    )
        i += 1

    if not patches and not incompatible_fixes:
        return False

    # Never modify the real consumer file or the generated execution harness.
    protected = {analysis.className, "EventBreakerExecution"}

    patched = False

    # Apply incompatible-type fixes: update existing methods from Object to the correct type.
    for stub_file, fixes in incompatible_fixes.items():
        if not stub_file.exists():
            continue
        content = stub_file.read_text()
        for mname, target_type in fixes.items():
            ret_val = _default_return_value(target_type)
            old = f"public Object {mname}(Object... args) {{ return null; }}"
            new = f"public {target_type} {mname}(Object... args) {{ return {ret_val}; }}"
            if old in content and new not in content:
                content = content.replace(old, new)
                stub_file.write_text(content)
                patched = True
    for fqn, methods in patches.items():
        parts = fqn.split(".")
        if len(parts) < 2:
            continue
        pkg_parts, class_name = parts[:-1], parts[-1]
        if class_name in protected:
            continue
        stub_file = main_src.joinpath(*pkg_parts) / f"{class_name}.java"
        if not stub_file.exists():
            continue

        content = stub_file.read_text()
        last_brace = content.rfind("}")
        if last_brace == -1:
            continue

        # Deduplicate by method name — Maven reports the same error multiple
        # times (once in [INFO] section, once in [ERROR] Caused-by section),
        # so the patches list may contain duplicates.
        seen_names: set[str] = set()
        deduped: list[str] = []
        for m in methods:
            nm = m.split("(")[0].strip().split()[-1]
            if nm not in seen_names:
                seen_names.add(nm)
                deduped.append(m)

        # Only add methods not already present
        new_methods = [
            m for m in deduped
            if m.split("(")[0].strip().split()[-1] + "(" not in content
        ]
        if new_methods:
            content = (
                content[:last_brace]
                + "\n"
                + "\n".join(new_methods)
                + "\n"
                + content[last_brace:]
            )
            stub_file.write_text(content)
            patched = True

    return patched
