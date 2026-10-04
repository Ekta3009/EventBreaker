import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from eventbreaker.models.consumer import ConsumerAnalysis
from eventbreaker.models.observation import ObservationEntry, ObservationResult
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
        CONSUMER_CLASS consumer = new CONSUMER_CLASS(CONSTRUCTOR_ARGS);

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

        with tempfile.TemporaryDirectory(prefix="eventbreaker-") as tmpdir:
            project_dir = Path(tmpdir) / "scenario"
            self._create_project(project_dir, consumer_file)
            self._write_execution(project_dir, analysis, consumer_file)
            return self._run(project_dir, scenario)

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
        exec_dir.mkdir(parents=True)
        code = _generate_execution(analysis, consumer_file)
        (exec_dir / "EventBreakerExecution.java").write_text(code)

    # ── Build + run ───────────────────────────────────────────────────────────

    def _run(
        self,
        project_dir: Path,
        scenario: ChaosScenario,
    ) -> ObservationResult:

        # Step 1: compile + package into a self-contained fat JAR
        build = subprocess.run(
            ["mvn", "package", "--no-transfer-progress", "-q", "-DskipTests"],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )
        if build.returncode != 0:
            return ObservationResult(
                scenario=scenario.scenarioType.value,
                status="ERROR",
                rawOutput=build.stdout + "\n" + build.stderr,
                executionError=(
                    "Maven build failed. See rawOutput for details."
                ),
            )

        # Step 2: run the fat JAR — pure stdout, zero Maven noise
        jar = project_dir / "target" / "eventbreaker-execution.jar"
        run = subprocess.run(
            ["java", "-jar", str(jar)],
            capture_output=True,
            text=True,
            cwd=project_dir,
        )

        return _parse_output(run.stdout + "\n" + run.stderr, scenario)


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

    extra_imports = "\n".join(_read_imports(consumer_file))

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

    constructor_args = ", ".join(d.name for d in analysis.dependencies)

    dep_map_entries = "\n".join(
        f'        deps.put("{d.name}", {d.name});'
        for d in analysis.dependencies
    )

    return (
        _DUPLICATE_EVENT_EXECUTION
        .replace("CONSUMER_IMPORT", consumer_import)
        .replace("EXTRA_IMPORTS", extra_imports)
        .replace("MOCK_DECLARATIONS", mock_declarations)
        .replace("STUBS", stubs)
        .replace("CONSUMER_CLASS", analysis.className)
        .replace("CONSTRUCTOR_ARGS", constructor_args)
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


def _read_imports(java_file: Path) -> list[str]:
    imports = []
    for line in java_file.read_text(errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            imports.append(stripped)
        elif re.match(r"^(public\s+)?(class|interface|enum|record)\s+", stripped):
            break
    return imports
