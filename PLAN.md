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

### [ ] TASK F — Sandbox Executor
**Status:** TODO — START HERE

**Goal:** Move scenario execution from local temp dir into Nebius Sandbox via ConTree Python SDK.

**Steps:**
1. Install ConTree SDK: `pip install contree` (verify package name from Nebius docs)
2. Create `eventbreaker/sandbox/executor.py` — `SandboxExecutor` class
3. API: `execute(project_dir: Path) -> ExecutionResult` — uploads files, runs `mvn test`, returns stdout/stderr
4. Replace local `mvn test` in TASK E with `SandboxExecutor.execute()`

**Exit condition:** Same `DUPLICATE_EVENT` scenario that worked locally now runs in Nebius Sandbox.

**Notes:**
- Do not start this until TASK E works locally
- Sandbox beta access is confirmed

---

### [ ] TASK G — Observation Model + Nemotron Diagnosis
**Status:** TODO

**Goal:** Parse execution output into structured observations. Feed to Nemotron for diagnosis + fix suggestion.

**Steps:**
1. Parse JUnit test stdout JSON → `ObservationResult` Pydantic model
2. Create `eventbreaker/diagnosis/analyzer.py`
3. Prompt: send original consumer analysis + scenario + observations to Nemotron
4. Ask Nemotron: what happened, why it matters, what to investigate, suggested fix
5. Parse response into `ReliabilityFinding`

**Exit condition:** Nemotron produces a grounded diagnosis like:
```
paymentClient.charge("ORD-123") was invoked twice.
Verify whether PaymentClient.charge() is idempotent.
```

---

### [ ] TASK H — End-to-End Wiring + Report
**Status:** TODO

**Goal:** Connect all components into one CLI command. Add human-readable report output.

**Steps:**
1. Wire in `cli/main.py`: analyze → Nemotron risks → validate scenario → execute in sandbox → observe → diagnose → report
2. Create `eventbreaker/report/reporter.py` — formats `ReliabilityFinding` as Markdown report
3. Add progress indicators to CLI (Rich or simple print statements)
4. Save report to `eventbreaker-report.md` + print summary to terminal

**Exit condition:**
```bash
eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
# Runs full loop, prints progress, saves report
```

---

### [ ] TASK I — README + Demo Polish
**Status:** TODO

**Goal:** Submission-ready README and demo setup.

**Steps:**
1. Write README: problem, architecture diagram, NVIDIA/Nebius usage, install steps, example output
2. Add `examples/` directory with a polished demo consumer
3. Verify no secrets in repo
4. Prepare for ≤3-minute demo video

---

## Deferred (implement only after TASK H is complete)

- `OUT_OF_ORDER` scenario executor
- `DOWNSTREAM_FAILURE` scenario executor
- `CRASH_AFTER_SIDE_EFFECT` scenario executor
- Re-verification loop (fix → re-run scenario)
- JUnit regression artifact generation

---

## How to Resume a Session

1. Read this file (`PLAN.md`)
2. Find the first task marked `[ ]`
3. Read all files mentioned in that task's "Key files" section
4. Implement step by step
5. Update this file when done (mark `[x]`, add "What was done", "How to test")
