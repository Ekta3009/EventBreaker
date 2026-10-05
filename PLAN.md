# EventBreaker — Implementation Plan

**Hackathon:** Nebius × NVIDIA Global AI Hackathon 2026
**Deadline:** October 30, 2026 10:00 AM PDT
**Repo:** `/Users/ektachoudhary/EventBreaker`

> At the start of every session, read this file first.
> Find the first task marked `[ ]` and start there. Never skip ahead.
> Mark tasks `[x]` when complete and add a short note.

---

## Architecture

```
eventbreaker analyze Consumer.java
         │
         ▼
  [Static Analysis]     Java JAR (JavaParser) → JSON → Pydantic
         │
         ▼
  [Risk Identification] NVIDIA Nemotron via Nebius → ChaosScenario[]
         │
         ▼
  [Scenario Execution]  Generated Java harness → Maven build →
                        Local JVM or Nebius Sandbox → observations
         │
         ▼
  [Diagnosis]           NVIDIA Nemotron → ReliabilityFinding + fix
         │
         ▼
  [Report]              Terminal (Rich) + eventbreaker-report.md
```

**LLM:** `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` via Nebius Token Factory
**Sandbox:** Nebius ConTree SDK — `alpine:3.20` + `openjdk21-jre-headless`
**Env vars:** `NEBIUS_API_KEY` (required), `NEBIUS_PROJECT_ID` (optional, enables sandbox)

---

## Task List

### [x] Foundation & Static Analyzer
- Maven project, Java 21, `@EventBreakerConsumer` annotation
- `ConsumerAnalyzer` (JavaParser AST) → `ConsumerAnalysis` JSON
- `AnalyzerMain.java` fat JAR entry point (`target/eventbreaker-analyzer.jar`)

### [x] Python CLI + Analyzer Bridge
- `pyproject.toml`, `eventbreaker` package, Typer CLI
- `analyzer/bridge.py` — calls Java JAR, parses JSON → Pydantic models
- `cli/main.py` — `eventbreaker analyze <file>` command with Rich output

### [x] Nemotron Risk Identification
- `scenarios/models.py` — `ChaosScenario`, `ScenarioType`, `ScenarioAction`
- `agent/nemotron.py` — sends consumer analysis to Nemotron, returns validated scenarios
- `scenarios/validator.py` — allowlist validation of Nemotron output

### [x] Scenario Executor (DUPLICATE_EVENT)
- `scenarios/executor.py` — generates a Java main harness, builds it with Maven, runs it
- Delivers the same event twice with Mockito-mocked dependencies
- Counts invocations per dependency method, returns `ObservationResult`
- Supports: constructor injection, reflection field injection, package-private fields
- Supports: generic-typed dependencies (`Repository<Product>`), deep event method chains
- Iterative build loop: patches stub classes on compiler errors (up to 3 attempts)
- Cache at `~/.eventbreaker/cache/<hash>/scenario/` — skips Maven rebuild when JAR exists
- Stale cache recovery: auto-deletes and rebuilds if a previous session left corrupted stubs

### [x] Nebius Sandbox Executor
- `sandbox/executor.py` — uploads fat JAR to ConTree sandbox, runs `java -jar`
- Activated when `NEBIUS_PROJECT_ID` is set; otherwise falls back to local JVM

### [x] Nemotron Diagnosis
- `diagnosis/analyzer.py` — sends observations to Nemotron, returns `ReliabilityFinding`
- Fields: `severity`, `summary`, `explanation`, `affectedMethod`, `suggestedFix`

### [x] Report + End-to-End Pipeline
- `report/reporter.py` — generates `eventbreaker-report.md`
- `cli/main.py` — 5-step pipeline wired together with Rich status indicators
- `.env` auto-loaded via `python-dotenv` so API keys don't need manual `export`

### [x] Universal Consumer Support
- Auto-generates stub `.java` classes for any type not already on the classpath
- `_patch_stubs_from_errors()` — adds missing methods to stubs after compiler errors
- Generic type handling: strips `<T>` for `mock()` calls and imports; stubs declare `<T>`
- Chain stub generation: `__EBChain` universal return type for `event.getA().doB()` patterns

### [x] Consumer Test Suite (12 consumers)
- **Original 6:** OrderConsumer, NotificationConsumer, SimpleConsumer, ConditionalConsumer, ComplexConsumer, DeepNestedConsumer
- **Safe (no risk):** ReadOnlyConsumer, CacheRefreshConsumer, IdempotentGuardConsumer
- **Adversarial:** MultiDepConsumer (4 deps), PackagePrivateConsumer (pkg-private fields), GenericDepConsumer (generic type)
- All 12 produce `REPRODUCED` status

### [x] README
- Full README with problem statement, architecture, install steps, example output, compatibility table

---

## Remaining Tasks

### [ ] TASK — Codebase Cleanup & Refactor
**Goal:** Remove dead code, unused files, and anything that doesn't belong in the final submission.

**Steps:**
1. Audit `src/main/java/` — check if any Java classes are dead weight (unused by the analyzer JAR)
2. Audit `eventbreaker/` Python package — check for unused imports, redundant helpers, leftover debug code
3. Check `src/test/` — remove any JUnit tests that test internal implementation details no longer relevant
4. Remove temp/debug files (`/tmp/test_*.py` are ephemeral but check for any committed debug artifacts)
5. Check `docs/` directory — decide if it should be kept or removed
6. Verify `pyproject.toml` dependencies are all still used
7. Verify `.gitignore` covers `.venv/`, `.env`, `target/`, `~/.eventbreaker/`

### [ ] TASK I — Demo Polish
**Goal:** Prepare for the submission demo.

**Steps:**
1. Create `examples/` with a single polished consumer that demonstrates the full pipeline clearly
2. Verify no secrets are in the repo (`git log --all -S "NEBIUS"`)
3. Test the full end-to-end pipeline once on a clean machine / after `rm -rf ~/.eventbreaker`
4. Prepare talking points: problem, solution, Nebius+NVIDIA integration, live demo

### [ ] TASK J — DOWNSTREAM_FAILURE Executor
**Goal:** Execute `DOWNSTREAM_FAILURE` scenarios identified by Nemotron.

Inject a `RuntimeException` on the first call to `targetDependency.targetMethod`.
Run the consumer, observe which side effects completed before the failure.

### [ ] TASK K — CRASH_AFTER_SIDE_EFFECT Executor
**Goal:** Simulate a JVM crash after a specific side effect — strongest sandbox justification.

Use Mockito `doAnswer` to call `System.exit(1)` after `targetMethod` completes.
Run in Nebius Sandbox (crashes the sandbox JVM, not the local machine).
Report: which operations completed, which were lost.

### [ ] TASK L — OUT_OF_ORDER Executor
**Goal:** Deliver two events with swapped sequence, observe whether state is correct.

Run consumer with event A then B, then again with B then A.
Compare final observations — if they differ, an ordering assumption is present.

---

## Key Files

| File | Purpose |
|------|---------|
| `eventbreaker/scenarios/executor.py` | Harness generation, Maven build, execution |
| `eventbreaker/cli/main.py` | 5-step pipeline CLI |
| `eventbreaker/agent/nemotron.py` | Nemotron risk identification |
| `eventbreaker/diagnosis/analyzer.py` | Nemotron diagnosis |
| `eventbreaker/analyzer/bridge.py` | Java JAR → Python bridge |
| `eventbreaker/report/reporter.py` | Markdown report generator |
| `eventbreaker/models/consumer.py` | ConsumerAnalysis Pydantic models |
| `eventbreaker/scenarios/models.py` | ChaosScenario Pydantic models |
| `target/eventbreaker-analyzer.jar` | Java fat JAR (static analyzer) |
| `src/test/resources/consumers/` | 12 example consumers |
