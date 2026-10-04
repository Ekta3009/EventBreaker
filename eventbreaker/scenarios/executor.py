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
from eventbreaker.scenarios.models import ChaosScenario, ScenarioType


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
# Plain Java main class — no test framework.
# Uses plain string replacement (UPPER_CASE markers) to avoid
# escaping conflicts between Python f-strings and Java braces.
# Mockito is used purely as an invocation recorder, not a test helper.

_DUPLICATE_EVENT_EXECUTION = """\
package eventbreaker.generated;

import org.mockito.Mockito;
import org.mockito.invocation.Invocation;
import static org.mockito.Mockito.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import java.util.*;

CONSUMER_IMPORT
EXTRA_IMPORTS

/**
 * EventBreaker generated execution harness.
 *
 * Runs the consumer under a DUPLICATE_EVENT scenario:
 * delivers the same event twice with instrumented dependencies,
 * then reports how many times each dependency method was called.
 *
 * This is NOT a test — it is a controlled execution of the consumer
 * with dependency recorders in place of real services.
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

    private static void run() throws Exception {

        // ── Instrumented dependencies (recorders, not simulators) ──────────
        //
        // Each dependency is replaced with a recorder that:
        //   - Does nothing when void methods are called
        //   - Returns null / default values for return-type methods
        //   - Counts every invocation made to it
        //
        // We are NOT simulating real service behaviour.
        // We are observing HOW MANY TIMES the consumer calls each service.
MOCK_DECLARATIONS

        // ── Stub methods that return objects (prevents NullPointerException)
        //
        // The consumer assigns the return value of some dependency calls
        // to local variables and calls further methods on them.
        // We provide a recorder for those too.
STUBS

        // ── Instantiate the real consumer with recorder dependencies ───────
CONSUMER_INSTANTIATION

        // ── Create the event ───────────────────────────────────────────────
        //
        // Mocked so we don't need to know its constructor arguments.
        // The consumer reads fields from it via getters — those return
        // null / default, which is fine since all downstream calls
        // go to recorders anyway.
        EVENT_CLASS event = mock(EVENT_CLASS.class);

        // ── DUPLICATE_EVENT: deliver the same event twice ──────────────────
        //
        // Simulates Kafka / SQS at-least-once delivery redelivering
        // the same message. The consumer must handle this safely.
        try { consumer.CONSUMER_METHOD(event); } catch (Exception ignored) {}
        try { consumer.CONSUMER_METHOD(event); } catch (Exception ignored) {}

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
                observations.add(obs);
            }
        }

        ObjectNode result = mapper.createObjectNode();
        result.put("scenario", "DUPLICATE_EVENT");
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
        if scenario.scenarioType != ScenarioType.DUPLICATE_EVENT:
            raise NotImplementedError(
                f"Scenario {scenario.scenarioType.value} is not yet supported. "
                "Only DUPLICATE_EVENT is implemented in this version."
            )

        # Use a stable cache directory keyed by a hash of the generated code.
        # If the consumer hasn't changed, Maven won't rebuild — just re-run the JAR.
        execution_code = _generate_execution(analysis, consumer_file)
        cache_key = hashlib.sha256(
            (execution_code + consumer_file.read_text(errors="replace")).encode()
        ).hexdigest()[:16]
        cache_dir = Path.home() / ".eventbreaker" / "cache" / cache_key
        project_dir = cache_dir / "scenario"

        if not (project_dir / "pom.xml").exists():
            self._create_project(project_dir, consumer_file)

        # Always overwrite execution class and stubs in case analysis changed
        self._write_execution(project_dir, analysis, consumer_file)
        _generate_stubs(project_dir, analysis, consumer_file)
        return self._run(project_dir, scenario, analysis, consumer_file, cached=_jar_exists(project_dir))

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
    ) -> None:
        exec_dir = (
            project_dir
            / "src" / "main" / "java"
            / "eventbreaker" / "generated"
        )
        exec_dir.mkdir(parents=True, exist_ok=True)
        code = _generate_execution(analysis, consumer_file)
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
                )
                if fixable and _patch_stubs_from_errors(project_dir, last_output, analysis, consumer_file):
                    continue  # stubs were patched — retry
                break  # genuine error, stop

            if not built:
                return ObservationResult(
                    scenario=scenario.scenarioType.value,
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
                    scenario=scenario.scenarioType.value,
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
        pkg_types.add(dep.type)
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

    mock_declarations = "\n".join(
        f"        {d.type} {d.name} = mock({d.type}.class);"
        for d in analysis.dependencies
    )

    dep_names = {d.name for d in analysis.dependencies}
    stubs_lines: list[str] = []
    for vi in analysis.variableInitializations:
        call = vi.initializerMethodCall
        if call and call.scope in dep_names:
            mock_var = f"mock{vi.variableType}"
            stubs_lines.append(
                f"        {vi.variableType} {mock_var} = mock({vi.variableType}.class);"
            )
            stubs_lines.append(
                f"        when({call.scope}.{call.methodName}(any()))"
                f".thenReturn({mock_var});"
            )
    stubs = "\n".join(stubs_lines)

    dep_map_entries = "\n".join(
        f'        deps.put("{d.name}", {d.name});'
        for d in analysis.dependencies
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

    return (
        _DUPLICATE_EVENT_EXECUTION
        .replace("CONSUMER_IMPORT", consumer_import)
        .replace("EXTRA_IMPORTS", extra_imports)
        .replace("MOCK_DECLARATIONS", mock_declarations)
        .replace("STUBS", stubs)
        .replace("CONSUMER_CLASS", analysis.className)
        .replace("CONSUMER_INSTANTIATION", consumer_instantiation)
        .replace("EVENT_CLASS", analysis.eventType)
        .replace("CONSUMER_METHOD", analysis.methodName)
        .replace("DEP_MAP_ENTRIES", dep_map_entries)
    )


# ── Output parser ─────────────────────────────────────────────────────────────

def _parse_output(output: str, scenario: ChaosScenario) -> ObservationResult:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("EVENTBREAKER_RESULT:"):
            data = json.loads(stripped[len("EVENTBREAKER_RESULT:"):])
            entries = [
                ObservationEntry(target=o["target"], callCount=o["callCount"])
                for o in data.get("observations", [])
            ]
            return ObservationResult(
                scenario=scenario.scenarioType.value,
                status="REPRODUCED",
                observations=entries,
                rawOutput=output,
            )

    return ObservationResult(
        scenario=scenario.scenarioType.value,
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
        # Java compiler errors: "error: cannot find symbol", "error: class X"
        if "error:" in stripped.lower():
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
    type_names: set[str] = set()
    type_names.add(analysis.eventType)
    for dep in analysis.dependencies:
        type_names.add(dep.type)
    for vi in analysis.variableInitializations:
        if vi.variableType:
            type_names.add(vi.variableType)
    for oc in analysis.objectCreations:
        if oc.type:
            type_names.add(oc.type.split("<")[0].strip())

    # Never stub the consumer class itself — it already exists in the project.
    type_names.discard(analysis.className)

    main_src = project_dir / "src" / "main" / "java"

    for name in type_names:
        # Strip generic parameters, e.g. "List<Order>" → "Order" already handled,
        # but guard against anything slipping through.
        name = name.split("<")[0].strip()
        if not name or name in _JDK_TYPES:
            continue

        pkg = import_pkg.get(name, consumer_pkg)
        pkg_parts = pkg.split(".") if pkg else []
        dest = main_src.joinpath(*pkg_parts) / f"{name}.java" if pkg_parts else main_src / f"{name}.java"

        if dest.exists():
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(_stub_source(pkg, name))


def _stub_source(pkg: str, name: str) -> str:
    """Minimal compilable Java class — just enough for the compiler and Mockito.

    Uses a varargs constructor so any call — new Foo(), new Foo(a), new Foo(a,b) —
    compiles without needing to know the real constructor signature.
    Mockito (Objenesis) does not call constructors when creating mocks, so this
    is safe at runtime.
    """
    pkg_line = f"package {pkg};\n\n" if pkg else ""
    return f"{pkg_line}public class {name} {{\n    public {name}(Object... args) {{}}\n}}\n"


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

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

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
                    ret = known_returns.get((var_name, name), "Object") if var_name else "Object"
                    patches[fqn].append(
                        f"    public {ret} {name}(Object... args) {{ return null; }}"
                    )
                else:
                    patches[fqn].append(
                        f"    public {class_name}(Object... args) {{}}"
                    )
        i += 1

    if not patches:
        return False

    # Never modify the real consumer file or the generated execution harness.
    protected = {analysis.className, "EventBreakerExecution"}

    patched = False
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

        # Only add methods not already present
        new_methods = [
            m for m in methods
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
