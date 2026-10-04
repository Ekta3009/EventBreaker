# EventBreaker — Implementation Plan

**Hackathon:** Nebius × NVIDIA Global AI Hackathon 2026
**Deadline:** October 30, 2026 10:00 AM PDT
**Repo:** `/Users/ektachoudhary/EventBreaker`

> This file is the source of truth for Claude Code. At the start of any session, read this file first. Pick up from the first task marked `[ ]`. Never skip ahead. Update status and notes after each task.

---

## Architecture Recap

```
eventbreaker analyze OrderConsumer.java
         │
         ▼
[Python CLI] ──subprocess──▶ [Java Fat JAR]
         │                        │
         │                    JavaParser AST
         │                        │
         ◀────── JSON stdout ──────┘
         │
    Pydantic model
         │
         ▼
[Nemotron Agent] ──▶ ChaosScenario JSON
         │
         ▼
[Scenario Validator] ──▶ validated scenario
         │
         ▼
[Scenario Executor] ──generates──▶ JUnit test code
         │
         ▼
[Sandbox Executor] ──ConTree SDK──▶ Nebius Sandbox
         │                              │
         │                        compile + run
         ◀─────── observations ─────────┘
         │
         ▼
[Nemotron Diagnosis] ──▶ finding + fix suggestion
         │
         ▼
[Report] ──▶ printed to terminal / Markdown file
```

**Key facts:**
- Java side: target consumer code + static analyzer (JavaParser). Maven project.
- Python side: EventBreaker tool (CLI, orchestration, LLM, scenarios, reporting).
- LLM: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` via Nebius Token Factory. API key in env var `NEBIUS_API_KEY`.
- Sandbox: Nebius ConTree SDK (access approved). Used for isolated JUnit execution.
- 4 scenario types: `DUPLICATE_EVENT`, `OUT_OF_ORDER`, `DOWNSTREAM_FAILURE`, `CRASH_AFTER_SIDE_EFFECT`.
- Demo consumer: `src/test/resources/consumers/OrderConsumer.java`

---

## Repo Structure (target)

```
EventBreaker/
├── src/                          Java target/example code (Maven)
│   ├── main/java/com/eventbreaker/
│   │   ├── analyzer/             JavaParser-based static analyzer
│   │   │   ├── AnalyzerMain.java     ← CLI entry point (outputs JSON)
│   │   │   ├── ConsumerAnalyzer.java
│   │   │   ├── ConsumerAnalysisReporter.java
│   │   │   ├── ConsumerAnalysisRunner.java
│   │   │   └── model/            Java records
│   │   ├── consumer/annotation/  @EventBreakerConsumer
│   │   ├── event/                OrderCreated, OrderProcessed
│   │   ├── model/                Order, OrderStatus
│   │   └── service/              OrderRepository, PaymentClient, EventPublisher
│   └── test/resources/consumers/ Example consumers for testing
├── target/
│   └── eventbreaker-analyzer.jar ← fat JAR (built via mvn package)
├── eventbreaker/                 Python package (to be created)
│   ├── __init__.py
│   ├── cli/
│   │   └── main.py               Typer CLI entry point
│   ├── analyzer/
│   │   └── bridge.py             Calls Java JAR, parses JSON → Pydantic
│   ├── agent/
│   │   └── nemotron.py           Nemotron LLM client
│   ├── scenarios/
│   │   ├── models.py             ChaosScenario, ScenarioAction Pydantic models
│   │   ├── validator.py          Allowlist validation
│   │   └── executor.py           Generates JUnit test from scenario
│   ├── sandbox/
│   │   └── executor.py           ConTree SDK wrapper
│   ├── diagnosis/
│   │   └── analyzer.py           Sends observations to Nemotron for diagnosis
│   ├── report/
│   │   └── reporter.py           Formats final report
│   └── models/
│       └── consumer.py           Pydantic ConsumerAnalysis model
├── tests/                        Python tests
├── examples/                     Polished demo consumers
├── pyproject.toml                Python package config
├── pom.xml                       Maven build
├── PLAN.md                       This file
└── README.md
```

---

## Tasks

### [x] TASK 0 — Java Foundation
**Status:** COMPLETE (pre-existing)
- Maven project, Java 21, JUnit 5
- `@EventBreakerConsumer` annotation
- `OrderConsumer` example with `OrderRepository`, `PaymentClient`, `EventPublisher`
- All tests pass

---

### [x] TASK 1 — Static Consumer Analyzer (Java)
**Status:** COMPLETE (pre-existing)
- `ConsumerAnalyzer` — JavaParser-based, finds `@EventBreakerConsumer`, extracts class, method, event type, dependencies, method calls, object creations, assignments, variable initializations
- `ConsumerAnalysis` — Java record, structured output
- `ConsumerAnalysisReporter` — human-readable formatted output
- `ConsumerAnalysisRunner` — directory-level analysis
- Full test coverage with 5 fixture consumers

---

### [x] TASK A — Java → JSON Bridge
**Status:** COMPLETE

**What was done:**
- Added `jackson-databind 2.17.1` to `pom.xml`
- Added `maven-shade-plugin` — builds `target/eventbreaker-analyzer.jar` (fat JAR with all deps)
- Added `exec-maven-plugin` for dev convenience (`mvn exec:java`)
- Created `AnalyzerMain.java` — takes a `.java` file path as CLI arg, runs `ConsumerAnalyzer`, serializes result to pretty-printed JSON on stdout. On error outputs `{"error": "..."}` and exits with code 1.

**How to build:**
```bash
cd /Users/ektachoudhary/EventBreaker
mvn package
```

**How to test:**
```bash
java -jar target/eventbreaker-analyzer.jar src/test/resources/consumers/OrderConsumer.java
# Should print structured JSON with className, methodName, eventType, dependencies, methodCalls, etc.

java -jar target/eventbreaker-analyzer.jar /nonexistent.java
# Should print {"error": "File not found: /nonexistent.java"} and exit 1
```

**Key file:** `src/main/java/com/eventbreaker/analyzer/AnalyzerMain.java`

---

### [x] TASK B — Python Package + CLI Skeleton
**Status:** COMPLETE

**What was done:**
- Created `pyproject.toml` with `setuptools.build_meta` backend, dependencies: `typer>=0.12`, `pydantic>=2.7`, `openai>=1.35`, `rich>=13.7`. Entry point: `eventbreaker = "eventbreaker.cli.main:app"`
- Created `eventbreaker/` package with subdirs: `cli/`, `analyzer/`, `agent/`, `scenarios/`, `sandbox/`, `diagnosis/`, `report/`, `models/` — each with `__init__.py`
- Created `eventbreaker/models/consumer.py` — Pydantic v2 models mirroring the Java JSON: `ConsumerAnalysis`, `DependencyInfo`, `MethodCallInfo`, `ObjectCreationInfo`, `AssignmentInfo`, `VariableInitializationInfo`
- Created `eventbreaker/analyzer/bridge.py` — resolves JAR path via `__file__`, calls `java -jar`, parses stdout JSON → `ConsumerAnalysis`. Returns structured error if JAR missing or analyzer fails.
- Created `eventbreaker/cli/main.py` — Typer app with `analyze` subcommand. Uses Rich for formatted output: consumer panel, dependencies table, method calls table. Requires `@app.callback()` to force Typer into subcommand mode (without it, single-command apps collapse to root).
- Created `.venv/` at repo root, installed package in editable mode.

**How to activate venv:**
```bash
source .venv/bin/activate
```

**How to test:**
```bash
# From repo root, with venv active (or use .venv/bin/eventbreaker directly):
.venv/bin/eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Shows: Consumer found panel, Dependencies table, Method Calls table

.venv/bin/eventbreaker --help
# Shows available commands

.venv/bin/eventbreaker analyze --help
# Shows analyze command usage
```

**Key files:**
- `pyproject.toml`
- `eventbreaker/models/consumer.py`
- `eventbreaker/analyzer/bridge.py`
- `eventbreaker/cli/main.py`

**Gotcha:** Typer with a single `@app.command("analyze")` collapses subcommands unless you add `@app.callback()`. Always keep that callback present when adding future commands.

---

### [x] TASK C — Pydantic Models (ConsumerAnalysis)
**Status:** COMPLETE — done as part of TASK B.
`ConsumerAnalysis` and all related models live in `eventbreaker/models/consumer.py`.
Remaining models (`ChaosScenario`, `ObservationResult`, `ReliabilityFinding`) will be created in TASK D and TASK G respectively.

---

### [x] TASK D — Nemotron Integration (Risk Identification)
**Status:** COMPLETE

**What was done:**
- Created `eventbreaker/scenarios/models.py` — Pydantic models: `ScenarioType` (enum), `ActionType` (enum), `ScenarioAction`, `ChaosScenario`
- Created `eventbreaker/scenarios/validator.py` — `validate()` filters scenarios against allowlists. Strips invalid actions rather than rejecting the whole scenario.
- Created `eventbreaker/agent/nemotron.py`:
  - `identify_risks(analysis, source_code)` — main entry point
  - Builds a structured prompt with the consumer's class name, event type, dependencies, ordered method calls, and raw source code
  - Calls `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` via Nebius Token Factory (OpenAI-compatible API, `temperature=0.2`)
  - `_parse()` — strips markdown fences if present, extracts JSON array, validates each item against `ChaosScenario` schema, skips invalid items
  - Raises `EnvironmentError` if `NEBIUS_API_KEY` not set, `ValueError` if nothing valid comes back
- Updated `eventbreaker/cli/main.py` to:
  - Call `identify_risks()` after static analysis
  - Show a warning and exit cleanly if `NEBIUS_API_KEY` is not set
  - Display each scenario as a coloured Rich panel (DUPLICATE_EVENT=red, OUT_OF_ORDER=yellow, DOWNSTREAM_FAILURE=magenta, CRASH_AFTER_SIDE_EFFECT=orange)

**How to test:**
```bash
cd /Users/ektachoudhary/EventBreaker
export NEBIUS_API_KEY=<your-key>
source .venv/bin/activate
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Should show static analysis panels, then 2-4 risk panels from Nemotron
# Risk #1 should be DUPLICATE_EVENT referencing paymentClient.charge
```

**Without API key (safe fallback):**
```bash
.venv/bin/eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Shows static analysis only, then prints warning about missing key
```

**Key files:**
- `eventbreaker/scenarios/models.py`
- `eventbreaker/scenarios/validator.py`
- `eventbreaker/agent/nemotron.py`
- `eventbreaker/cli/main.py` (updated)

**AI concepts introduced:**
- Structured output prompting (asking LLM to return JSON matching a schema)
- Output validation (Pydantic + allowlist after LLM response)
- Temperature control (0.2 for deterministic structured output)

---

### [x] TASK E — Scenario Executor (DUPLICATE_EVENT, local)
**Status:** COMPLETE

**What was done:**
- Created `eventbreaker/models/observation.py` — `ObservationEntry` (target + callCount), `ObservationResult` (scenario, status, observations list, rawOutput, executionError)
- Created `eventbreaker/scenarios/executor.py` — `ScenarioExecutor` class:
  - `execute(scenario, analysis, consumer_file)` — dispatches to scenario-specific handler
  - `_create_project(project_dir, consumer_file)` — creates a temp Maven project: writes `pom.xml` (JUnit 5 + Mockito 5 + Jackson), copies `src/main/java` from the parent Maven project (excluding `analyzer` package which depends on JavaParser), copies consumer file into the correct package directory if it's outside `src/main/java`
  - `_write_test(project_dir, analysis, consumer_file)` — generates and writes `DuplicateEventTest.java`
  - `_run(project_dir, scenario)` — runs `mvn test --no-transfer-progress -Dtest=DuplicateEventTest`, captures stdout+stderr, parses `EVENTBREAKER_RESULT:` JSON line
  - `_generate_duplicate_event_test()` — builds JUnit test using plain string replacement (avoids f-string/Java-brace conflicts): mocks all dependencies, stubs return values from `variableInitializations`, delivers event twice via `consumer.consume(event)`, collects all Mockito invocations and prints as JSON with marker `EVENTBREAKER_RESULT:`
  - Surefire configured with `useFile=false` so test stdout appears in Maven output
- Updated `eventbreaker/cli/main.py` — after Nemotron risks, picks first DUPLICATE_EVENT scenario and executes it; shows observations table (call count > 1 highlighted red)

**How to test:**
```bash
cd /Users/ektachoudhary/EventBreaker
export NEBIUS_API_KEY=<your-key>
source .venv/bin/activate
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Full pipeline: analyze → Nemotron → execute → observe
# Observations panel should show paymentClient.charge callCount=2 (red)
```

**First run is slow** (~30–60s) — Maven downloads Mockito. Subsequent runs are fast.

**Key files:**
- `eventbreaker/models/observation.py`
- `eventbreaker/scenarios/executor.py`
- `eventbreaker/cli/main.py` (updated)

**Goal:** Given a validated `ChaosScenario`, generate a JUnit test that executes it against the consumer with mocked dependencies, captures interaction counts.

**Steps:**
1. Create `eventbreaker/scenarios/executor.py`
2. For `DUPLICATE_EVENT`: generate a JUnit 5 test that:
   - Creates Mockito mocks for each dependency
   - Instantiates the consumer with mocks
   - Delivers the same event twice
   - Captures how many times each mock method was called
   - Prints observations as JSON to stdout
3. Write the generated test + consumer source to a temp directory as a Maven project
4. Run `mvn test` locally, capture output

**Exit condition:** Running a `DUPLICATE_EVENT` scenario against `OrderConsumer` produces an observation showing `paymentClient.charge` called twice.

**Notes:**
- Use Mockito for mocks (add to generated pom.xml, not main pom.xml)
- Observations printed as JSON: `{"scenario": "DUPLICATE_EVENT", "observations": [{"target": "paymentClient.charge", "callCount": 2}]}`
- Keep generated project in a temp dir, clean up after

---

### [x] TASK F — Sandbox Executor
**Status:** COMPLETE

**What was done:**
- Installed `contree-sdk` (package name confirmed), added to `pyproject.toml`
- Created `eventbreaker/sandbox/executor.py` — `SandboxExecutor` class:
  - `execute(jar_path: Path) -> tuple[str, str]` — uploads pre-built fat JAR to sandbox, installs `openjdk21-jre-headless` via apk, runs `java -jar`, returns `(stdout, stderr)`
  - Uses `alpine:3.20` image (lightest available with JDK support)
  - `NEBIUS_API_KEY` + `NEBIUS_PROJECT_ID` read automatically from env by SDK
  - Timeout: 180s (covers ~30s apk install + JAR execution)
- Updated `eventbreaker/scenarios/executor.py`:
  - If `NEBIUS_PROJECT_ID` is set → runs in Nebius Sandbox
  - If `NEBIUS_PROJECT_ID` is not set → falls back to local `java -jar`
  - Maven build (`mvn package`) always runs locally (no Maven image in sandbox)
- Updated `eventbreaker/cli/main.py` — shows "(Nebius Sandbox)" or "(local JVM)" in execution status
- Updated `.env.example` — added `NEBIUS_PROJECT_ID`

**How to test (sandbox):**
```bash
cd /Users/ektachoudhary/EventBreaker
export NEBIUS_API_KEY=<your-key>
export NEBIUS_PROJECT_ID=tenantuseraccount-e00wrxgtbrqbtk69w9
source .venv/bin/activate
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Should show: "Executing scenario: DUPLICATE_EVENT (Nebius Sandbox)..."
# First run: ~60–90s (apk install on first sandbox spin-up)
```

**How to test (local fallback — no NEBIUS_PROJECT_ID):**
```bash
unset NEBIUS_PROJECT_ID
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Shows: "Executing scenario: DUPLICATE_EVENT (local JVM)..."
```

**Key files:**
- `eventbreaker/sandbox/executor.py`
- `eventbreaker/scenarios/executor.py` (updated `_run()`)
- `eventbreaker/cli/main.py` (execution mode indicator)

---

### [x] TASK G — Observation Model + Nemotron Diagnosis
**Status:** COMPLETE

**What was done:**
- Added `ReliabilityFinding` Pydantic model to `eventbreaker/models/observation.py`:
  `scenario`, `summary`, `explanation`, `affectedMethod`, `suggestedFix`, `severity`
- Created `eventbreaker/diagnosis/analyzer.py` — `diagnose()` function:
  - Builds a structured prompt: consumer structure + source code + scenario + observations (call counts)
  - Calls Nemotron (`temperature=0.3`, `max_tokens=1024`) asking for a single `ReliabilityFinding` JSON
  - `_parse()` strips markdown fences, extracts JSON object, fills in defaults if Nemotron omits fields
- Updated `eventbreaker/cli/main.py`:
  - Step 4 added after observations: calls `diagnose()`, shows `_print_finding()` panel
  - Panel shows: Severity, Observed summary, Why it matters, Affected method, Suggested fix

**How to test:**
```bash
cd /Users/ektachoudhary/EventBreaker
export NEBIUS_API_KEY=<your-key>
source .venv/bin/activate
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# After the Observations panel, should show a Reliability Finding panel
# with summary, explanation, affectedMethod=paymentClient.charge, and a fix suggestion
```

**Key files:**
- `eventbreaker/models/observation.py` (added ReliabilityFinding)
- `eventbreaker/diagnosis/analyzer.py`
- `eventbreaker/cli/main.py` (Step 4 + _print_finding)

---

### [x] TASK H — End-to-End Wiring + Report
**Status:** COMPLETE

**What was done:**
- Created `eventbreaker/report/reporter.py`:
  - `generate(consumer_file, analysis, scenarios, result, finding, sandbox)` → Markdown string
  - Sections: header, consumer summary, identified risks, scenario observations, reliability finding, footer
  - `save(report, output_path)` → writes `eventbreaker-report.md` and returns the path
- Updated `eventbreaker/cli/main.py`:
  - Step 5 added: generates + saves Markdown report after finding, prints path to terminal
- Added Maven build cache to `eventbreaker/scenarios/executor.py`:
  - Hashes generated execution code + consumer file content → 16-char key
  - Stores project dir at `~/.eventbreaker/cache/<hash>/scenario/`
  - If JAR already exists for that hash, skips `mvn package` entirely
  - Subsequent runs on the same consumer are fast (just `java -jar` or sandbox upload)

**How to test:**
```bash
cd /Users/ektachoudhary/EventBreaker
export NEBIUS_API_KEY=<your-key>
source .venv/bin/activate
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Full pipeline runs, ends with:
# ✓ Report saved → eventbreaker-report.md
# Second run is significantly faster (Maven cache hit)
```

**Key files:**
- `eventbreaker/report/reporter.py`
- `eventbreaker/cli/main.py` (Step 5)
- `eventbreaker/scenarios/executor.py` (Maven cache)

---

### [ ] TASK I — README + Demo Polish
**Status:** TODO — START HERE

**Goal:** Submission-ready README and demo setup.

**Steps:**
1. Write README: problem, architecture diagram, NVIDIA/Nebius usage, install steps, example output
2. Add `examples/` directory with a polished demo consumer
3. Verify no secrets in repo
4. Prepare for ≤3-minute demo video

---

## Necessary (implement after TASK H, before TASK I)

These are required for the submission — they complete the sandbox justification story
(fault injection scenarios that are dangerous to run locally) and demonstrate the full
4-scenario capability of EventBreaker.

### [~] TASK M — Stub Class Generator (Universal Consumer Support)
**Status:** PARTIALLY COMPLETE — NotificationConsumer and OrderConsumer work; see TASK N for remaining issues

**Goal:** Make EventBreaker work with ANY annotated Java consumer, not just ones backed
by `com.eventbreaker.*` classes. Consumers like `NotificationConsumer`, `ComplexConsumer`
etc. reference types (`UserStore`, `User`, `Notification`) that don't exist in the copied
source tree, causing Maven to fail with "cannot find symbol" errors.

**Solution:** Before running `mvn package`, auto-generate minimal Java stub classes for
every type the consumer references that isn't already on the classpath. Since all
dependencies are mocked by Mockito, stubs only need the right name, package, and a
no-arg constructor — no real methods needed.

**Steps:**
1. Add `_generate_stubs(project_dir, analysis, consumer_file)` to `executor.py`
2. For each type in: dependency types, event type, variable initialization types:
   - Resolve its package from the consumer's import statements (or use consumer's own package)
   - Check if a `.java` file already exists for it in the temp project
   - If not, write a minimal stub: `public class X { public X() {} }`
3. Call `_generate_stubs()` in `execute()` after `_create_project()` and `_write_execution()`

**What was done:**
- Added `_generate_stubs(project_dir, analysis, consumer_file)` to `executor.py`
- Builds a `simple_name → package` map from the consumer's import statements
- Collects all referenced types from analysis: dependency types, event type, variable types
- For each type not already present in the temp project's `src/main/java`, writes a
  minimal stub: `public class X { public X() {} }`
- Also improved Maven build error messages via `_extract_build_error()` — shows compiler
  error lines instead of "see rawOutput" for actionable user feedback
- Called from `execute()` after `_create_project()` and `_write_execution()`

**Exit condition:** `eventbreaker analyze src/test/resources/consumers/NotificationConsumer.java`
runs the full pipeline without a build error.

**Key file:** `eventbreaker/scenarios/executor.py`

---

### [ ] TASK N — Fix Remaining Consumer Compatibility Issues
**Status:** TODO — START HERE (replaces TASK I temporarily; do this before README)

**Context:** During TASK M debugging (session 2026-10-04), we confirmed `OrderConsumer` and `NotificationConsumer` work end-to-end. The following bugs were found and **already fixed** in `eventbreaker/scenarios/executor.py`:

**Already fixed this session:**
1. `[Errno 17] File exists` on `exec_dir` — `_write_execution()` was calling `exec_dir.mkdir(parents=True)` without `exist_ok=True`. Fixed.
2. Stub generator was creating a stub for the consumer class itself — the real file is already copied. Fixed with `type_names.discard(analysis.className)`.
3. `_patch_stubs_from_errors()` was patching the real consumer file when the consumer had no explicit constructor. Fixed with a `protected` set (`{analysis.className, "EventBreakerExecution"}`).
4. Consumers with no explicit constructor (field injection, e.g. `NotificationConsumer`) caused a compile error because generated code called `new ConsumerClass(dep1, dep2)`. Fixed with `_has_explicit_constructor()` + reflection-based field injection.
5. `objectCreations` types added to `extra_pkg_imports` in `_generate_execution()`.

**Still broken (need investigation):**
- `SimpleConsumer`, `ConditionalConsumer`, `ComplexConsumer` — all fail with a generic "Build failed" that falls through to the stacktrace fallback in `_extract_build_error()`. This means `_extract_build_error()` isn't finding "cannot find symbol" or "error:" patterns in Maven output, so the actual compiler errors are invisible. Need to add a temporary `rawOutput` dump (or print full output) to diagnose.
- `DeepNestedConsumer` — fails with "cannot find symbol" (types on classpath). Same pattern as was fixed for NotificationConsumer; likely needs deeper inspection of what types it uses vs. what stubs are generated.

**Stale cache UX problem:**
- If a user ran EventBreaker BEFORE the fixes in this session, their `~/.eventbreaker/cache/` may contain corrupted consumer files (e.g. `NotificationConsumer.java` with duplicate constructors added by the old buggy patch function). The new code won't fix these because `_create_project()` only runs when `pom.xml` is missing — stale caches skip it.
- Fix: if `mvn package` fails, delete the cache dir and try a fresh build once before giving up.

**Steps:**
1. Add stale-cache recovery: on build failure, if the cache dir was not freshly created this run, delete it and retry `_create_project` + rebuild (once).
2. Debug `SimpleConsumer` / `ConditionalConsumer` / `ComplexConsumer`: temporarily write `last_output` to `/tmp/eventbreaker-build-debug.txt` in `_run()`, run, inspect.
3. Fix whatever compiler errors are found (likely similar stub/constructor issues).
4. Debug `DeepNestedConsumer` the same way.
5. Verify all 6 consumer fixtures (including `OrderConsumer`, `NotificationConsumer`) pass with `REPRODUCED` status.
6. Remove the debug dump.

**How to run all consumers manually (after starting venv):**
```python
# Run /tmp/test_all_consumers.py (already written) — needs NEBIUS_API_KEY unset or set.
# It directly calls ScenarioExecutor so it bypasses the API key check.
.venv/bin/python /tmp/test_all_consumers.py
```
> Note: `/tmp/test_all_consumers.py` exists from this session. If gone, recreate it using the instructions in MEMORY.md.

**Exit condition:** All 6 consumer fixtures (OrderConsumer, NotificationConsumer, SimpleConsumer, ConditionalConsumer, ComplexConsumer, DeepNestedConsumer) produce `Status: REPRODUCED` with at least one observation having `callCount=2`.

**Key file:** `eventbreaker/scenarios/executor.py`

---

### [ ] TASK O — Validate Against Unknown / Real-World Consumers
**Status:** TODO (do after TASK N, before TASK I)

**Goal:** Prove EventBreaker is a general-purpose tool, not one tuned to the 6 fixture consumers. Test it against consumers written independently that it has never "seen".

**Why this matters:** The fixture consumers were written alongside the tool, so they implicitly match its assumptions. A real user's consumer will have different annotation styles, package structures, constructor patterns, dependency counts, and types. We need to find and fix any hard-coded assumptions before the submission.

**Steps:**
1. Write 2–3 new "adversarial" consumer files in `src/test/resources/consumers/` that stress-test edge cases:
   - `MultiDepConsumer.java` — 4+ constructor-injected dependencies (tests mock generation at scale)
   - `PackagePrivateConsumer.java` — dependencies with no-arg constructor but package-private fields (tests reflection injection)
   - `GenericDepConsumer.java` — a dependency typed as `Repository<Order>` or similar (tests generic type stripping in stub generator)
2. Run `eventbreaker analyze` on each new consumer (with `NEBIUS_API_KEY` set) and verify the full pipeline completes without errors.
3. Also test with a consumer that has **no variable initializations** (just direct void calls) — simplest possible case.
4. Fix any new failures found. Common expected failure modes:
   - Generic type parameters leaking into stub class names (e.g. `Repository<Order>` → stub named `Repository<Order>.java`)
   - `extra_pkg_imports` generating duplicate imports for types already imported by the consumer
   - Reflection injection failing if the consumer's fields are `private final` (would need `setAccessible` + JVM `--add-opens` flag, or constructor fallback)
5. Document any permanent limitations in README (e.g. "consumer must be compilable with Java 21").

**Exit condition:** At least 3 freshly written consumers (not from the original fixtures) run through the full pipeline and produce `REPRODUCED` status with correct observations.

**Key file:** `eventbreaker/scenarios/executor.py`

---

### [ ] TASK J — DOWNSTREAM_FAILURE Scenario Executor
**Status:** TODO — next after TASK I
**Goal:** Inject a mid-execution exception into the target dependency mock, observe
whether the consumer leaves the system in a consistent state.
- Generate a new Java harness variant where `targetDependency.targetMethod()` throws
  a `RuntimeException` on the first call
- Run the consumer, collect which methods completed before the failure
- Observations: which side effects already happened when the failure occurred

### [ ] TASK K — CRASH_AFTER_SIDE_EFFECT Scenario Executor
**Goal:** Simulate a JVM crash after a specific side effect completes but before the
consumer finishes. This is the strongest sandbox justification — we deliberately call
`System.exit(1)` inside a Mockito Answer after the target method, then report what
state was committed vs what was lost.
- Inject a `System.exit(1)` via Mockito `doAnswer` after `targetMethod` is called
- Run in sandbox (crashes the sandbox JVM safely, not local machine)
- Report: which operations completed, which were lost

### [ ] TASK L — OUT_OF_ORDER Scenario Executor
**Goal:** Deliver two events with swapped sequence numbers / timestamps, observe
whether the consumer produces correct final state.
- Run consumer with event A then event B, then again with B then A
- Compare final observations — if they differ, ordering assumption is present

## Deferred (nice to have, implement only if time allows before deadline)

- Re-verification loop (fix → re-run scenario)
- JUnit regression artifact generation

---

## How to Resume a Session

1. Read this file (`PLAN.md`)
2. Find the first task marked `[ ]`
3. Read all files mentioned in that task's "Key files" section
4. Implement step by step
5. Update this file when done (mark `[x]`, add "What was done", "How to test")
