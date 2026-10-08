# EventBreaker — Implementation Plan

**Hackathon:** Nebius × NVIDIA Global AI Hackathon 2026
**Deadline:** October 30, 2026 10:00 AM PDT
**Repo:** `/Users/ektachoudhary/EventBreaker`

---

## How to Use This File (Instructions for Claude)

> At the start of every session: read this file first.
> Find the first task marked `[ ]` under "Remaining Work" and start there.
> Never skip ahead. Mark tasks `[x]` when done and add a one-line note.
> Each task has a "Test" section — always run it before marking done.
> Never fixate on one consumer. Every change must be validated on at least 3.

---

## Architecture

```
Consumer.java
     │
     ▼
[Static Analyzer — Java JAR]
  JavaParser → ConsumerAnalysis JSON → Python Pydantic
  Deterministic. No LLM. Works on any Java class.
     │
     ▼
[Agent 1: Risk Identifier — Nemotron Ultra 550B]
  Input:  ConsumerAnalysis + source code
  Output: ChaosScenario[] — each with embedded TestConfig
  TestConfig = { callPattern, faultInjections[], observeOrdering, detectSwallowing }
  LLM decides WHAT to test. Never writes Java.
     │
     ▼
[Python Feasibility Validator]
  Pure Python. No LLM call.
  Validates TestConfig dep/method names against ConsumerAnalysis.
  Splits scenarios into: feasible (executor can run) vs theoretical (explain why not).
     │
     ▼
[Executor — Parameter-driven Java Assembly]
  Input:  ConsumerAnalysis + TestConfig
  Output: Java harness that always compiles
  dep/method names come from static analysis — no hallucination possible.
  Fault injection and call pattern assembled from TestConfig vocabulary.
  Runs in local JVM or Nebius Sandbox (ConTree SDK).
     │
     ▼
[Observations]
  Per dep.method: call count + threw
  Call sequence (ordering)
  consumerThrew (swallowing detection)
     │
     ▼
[Agent 2: Diagnostician — Nemotron Super 120B]
  Input:  risk description + richer observations
  Output: ReliabilityFinding[] grounded in evidence
  Returns [] early if no anomaly signal (no speculative findings).
     │
     ▼
[Report — Terminal + eventbreaker-report.md]
```

**LLM:** Nemotron Ultra (risk ID) + Nemotron Super (diagnosis) via Nebius Token Factory
**Sandbox:** Nebius ConTree SDK — `alpine:3.20` + `openjdk21-jre-headless`
**Env vars:** `NEBIUS_API_KEY` (required), `NEBIUS_PROJECT_ID` (optional, enables sandbox)

### TestConfig Vocabulary

| callPattern | When to use | faultInjections |
|---|---|---|
| `DUPLICATE` | Idempotency — run consumer twice | Always empty `[]` |
| `SINGLE` | Downstream failure / partial exec — inject fault at specific dep.method | One or more entries |
| `CONCURRENT` | Race conditions — two threads simultaneously | Usually empty |

| fault type | Effect |
|---|---|
| `THROW` | Method always throws `RuntimeException` |
| `THROW_ONCE` | Throws on first call, succeeds on retry |
| `DELAY` | Sleeps N ms before returning |

---

## Completed Work

### [x] Foundation — Static Analyzer
- Maven project, Java 21, `@EventBreakerConsumer` annotation
- `ConsumerAnalyzer` (JavaParser AST) → `ConsumerAnalysis` JSON
- `AnalyzerMain.java` fat JAR entry point (`target/eventbreaker-analyzer.jar`)

### [x] Python CLI + Analyzer Bridge
- `pyproject.toml`, `eventbreaker` package, Typer CLI
- `analyzer/bridge.py` — calls Java JAR, parses JSON → Pydantic
- `cli/main.py` — `eventbreaker analyze <file>` command with Rich output

### [x] Nemotron Risk Identification (original)
- `scenarios/models.py` — `ChaosScenario` model
- `agent/nemotron.py` — sends consumer analysis to Nemotron, returns scenarios
- `scenarios/validator.py` — schema validation of LLM output

### [x] Scenario Executor — Infrastructure
- `scenarios/executor.py` — Maven project setup, fat JAR build, JVM execution
- Cache at `~/.eventbreaker/cache/<hash>/scenario/`
- Iterative stub patching loop (up to 3 Maven attempts)
- Constructor injection + reflection field injection
- Generic type handling, deep event chain stubs
- Stale cache recovery

### [x] Nebius Sandbox Executor
- `sandbox/executor.py` — ConTree SDK wrapper
- Activated when `NEBIUS_PROJECT_ID` is set, falls back to local JVM

### [x] Nemotron Diagnosis
- `diagnosis/analyzer.py` — observations → `ReliabilityFinding[]`
- Early return when no anomaly signal (no speculative findings)

### [x] Report + End-to-End Pipeline
- `report/reporter.py` — generates `eventbreaker-report.md`
- `cli/main.py` — 6-step pipeline with Rich status indicators
- `.env` auto-loaded via `python-dotenv`

### [x] Consumer Library — 12 consumers
- Original 6: OrderConsumer, NotificationConsumer, SimpleConsumer, ConditionalConsumer,
  ComplexConsumer, DeepNestedConsumer
- Safe (no risk): ReadOnlyConsumer, CacheRefreshConsumer, IdempotentGuardConsumer
- Adversarial: MultiDepConsumer (4 deps), PackagePrivateConsumer (pkg-private fields),
  GenericDepConsumer (generic type)

### [x] TestConfig Prompt — Validated (2026-10-08)
- Tested `Nemotron-3-Ultra-550b-a55b` on OrderConsumer, NotificationConsumer, MultiDepConsumer
- Result: 3/3 pass, 11 scenarios, 0 validation errors, 0 hallucinated dep/method names
- Prompt: `/tmp/test_testconfig.py` — keep this file, re-run if prompt changes
- Confirmed: Ultra reliably uses exact dep/method names, follows callPattern rules

---

## Remaining Work

Work is ordered — each step builds on the previous. Do not skip ahead.
Each step is independently testable before moving to the next.

---

### [x] STEP 1 — Add TestConfig + FaultInjection models

**File:** `eventbreaker/scenarios/models.py`

Add alongside existing `ChaosScenario`:

```python
class FaultInjection(BaseModel):
    dep: str            # exact dep field name
    method: str         # exact method name on that dep
    fault: str          # THROW | THROW_ONCE | DELAY
    delayMs: int = 0    # only used when fault=DELAY

class TestConfig(BaseModel):
    callPattern: str                       # SINGLE | DUPLICATE | CONCURRENT
    faultInjections: list[FaultInjection] = []
    observeOrdering: bool = False
    detectSwallowing: bool = False

# Add to ChaosScenario:
# testConfig: TestConfig | None = None
```

Keep all existing `ChaosScenario` fields — just add `testConfig` as optional.
Remove the `actions` field from `ChaosScenario` — it is vestigial and unused.

**Test:**
```python
from eventbreaker.scenarios.models import FaultInjection, TestConfig, ChaosScenario
fi = FaultInjection(dep="paymentClient", method="charge", fault="THROW")
tc = TestConfig(callPattern="SINGLE", faultInjections=[fi])
s = ChaosScenario(scenarioType="DOWNSTREAM_FAILURE", reason="test",
                  targetDependency="paymentClient", targetMethod="charge",
                  expectedConcern="test", testConfig=tc)
print(s.model_dump())  # should serialize cleanly with testConfig nested
```

---

### [x] STEP 2 — Update Nemotron risk identifier

**File:** `eventbreaker/agent/nemotron.py`

Changes:
- Switch model to `nvidia/Nemotron-3-Ultra-550b-a55b`
- Replace system prompt with the TestConfig prompt from `/tmp/test_testconfig.py`
- Update `_build_prompt()` to include the dep→method map (copy from test script)
- Update `_parse()` to extract `testConfig` from each scenario JSON
- Update return type: `list[ChaosScenario]` with `testConfig` populated

**Test:**
```bash
# Run against OrderConsumer and print testConfig for each scenario
.venv/bin/python - <<'EOF'
from pathlib import Path
from eventbreaker.analyzer.bridge import analyze
from eventbreaker.agent.nemotron import identify_risks

analysis = analyze(Path("src/test/resources/consumers/OrderConsumer.java"))
source = Path("src/test/resources/consumers/OrderConsumer.java").read_text()
scenarios = identify_risks(analysis, source)
for s in scenarios:
    print(f"{s.scenarioType}: callPattern={s.testConfig.callPattern if s.testConfig else 'NONE'}")
    if s.testConfig:
        for fi in s.testConfig.faultInjections:
            print(f"  fault: {fi.dep}.{fi.method}:{fi.fault}")
EOF
```

Expected: 2-4 scenarios each with a valid non-None testConfig.

Also run `/tmp/test_testconfig.py` again after the change — it should still pass 3/3.

---

### [x] STEP 3 — Replace experiment.py with pure Python feasibility validator

**File:** `eventbreaker/scenarios/experiment.py`

Remove entirely: `generate_experiment()`, `ExperimentPlan`, `_SYSTEM_PROMPT`, `_build_prompt()`, `_parse()`.

Replace with a single pure Python function:

```python
def check_feasibility(
    scenario: ChaosScenario,
    analysis: ConsumerAnalysis,
) -> tuple[bool, str]:
    """
    Returns (feasible, reason).
    Feasible = testConfig exists and all dep/method names are valid.
    Theoretical = testConfig is None, OR references unknown dep/method.
    No LLM call. Instant.
    """
```

Rules to implement:
- `testConfig is None` → theoretical: "Nemotron did not provide a test configuration"
- `callPattern not in {SINGLE, DUPLICATE, CONCURRENT}` → theoretical
- Any `faultInjection.dep` not in `analysis.dependencies[].name` → theoretical: "dep X not found"
- Any `faultInjection.method` not in methodCalls where scope=dep → theoretical: "method X not found on dep Y"
- All checks pass → feasible

Update `ExperimentPlan` to just be:
```python
class ExperimentPlan(BaseModel):
    feasible: bool
    reason: str | None = None
```
Keep this model — it's used in the CLI and reporter.

**Test:**
```python
from eventbreaker.scenarios.experiment import check_feasibility
# Test 1: valid testConfig → should return (True, None)
# Test 2: dep name typo in testConfig → should return (False, "dep X not found")
# Test 3: testConfig is None → should return (False, "...")
# Build ChaosScenario objects manually to cover each case
```

---

### [ ] STEP 4 — Rewrite executor Java assembly (core change)

**File:** `eventbreaker/scenarios/executor.py`

This is the most important step. Replace the `EXPERIMENT_BLOCK` injection with parameter-driven assembly.

**What to remove:**
- `EXPERIMENT_BLOCK` marker from `_EXECUTION_TEMPLATE`
- `RESET_INVOCATIONS` marker (no longer needed — no LLM `when()` calls)
- `_indent_experiment()` helper
- The `experiment_code: str` parameter from `_generate_execution()`, `_write_execution()`, `execute()`

**What to add — two new Python functions:**

```python
def _build_fault_injections(test_config: TestConfig) -> str:
    """Generate Mockito fault setup lines from TestConfig.
    Called AFTER stubs, BEFORE call pattern.
    Each line uses only dep/method names known to be valid."""
    lines = []
    for fi in test_config.faultInjections:
        if fi.fault == "THROW":
            lines.append(
                f"        doThrow(new RuntimeException())"
                f".when({fi.dep}).{fi.method}(any());"
            )
        elif fi.fault == "THROW_ONCE":
            lines.append(
                f"        doThrow(new RuntimeException()).doNothing()"
                f".when({fi.dep}).{fi.method}(any());"
            )
        elif fi.fault == "DELAY":
            ms = fi.delayMs or 500
            lines.append(
                f"        doAnswer(inv -> {{ Thread.sleep({ms}); return null; }})"
                f".when({fi.dep}).{fi.method}(any());"
            )
    return "\n".join(lines)


def _build_call_pattern(analysis: ConsumerAnalysis, test_config: TestConfig) -> str:
    """Generate the consumer invocation block from callPattern.
    Always wraps in try-catch to capture consumerThrew."""
    method = analysis.methodName
    cp = test_config.callPattern

    single = (
        f"        try {{\n"
        f"            consumer.{method}(event);\n"
        f"        }} catch (Exception e) {{\n"
        f"            consumerThrew.set(true);\n"
        f"        }}"
    )

    if cp == "DUPLICATE":
        return (
            f"        try {{ consumer.{method}(event); }} "
            f"catch (Exception ignored) {{}}\n"
            + single
        )
    elif cp == "CONCURRENT":
        return (
            f"        java.util.concurrent.CountDownLatch latch = "
            f"new java.util.concurrent.CountDownLatch(2);\n"
            f"        java.util.concurrent.ExecutorService pool = "
            f"java.util.concurrent.Executors.newFixedThreadPool(2);\n"
            f"        for (int i = 0; i < 2; i++) {{\n"
            f"            pool.submit(() -> {{\n"
            f"                try {{ latch.countDown(); latch.await(); "
            f"consumer.{method}(event); }}\n"
            f"                catch (Exception e) {{ consumerThrew.set(true); }}\n"
            f"            }});\n"
            f"        }}\n"
            f"        pool.shutdown();\n"
            f"        pool.awaitTermination(10, "
            f"java.util.concurrent.TimeUnit.SECONDS);"
        )
    else:  # SINGLE
        return single
```

**Template changes:**
- Replace `EXPERIMENT_BLOCK` marker with two new markers: `FAULT_INJECTIONS` and `CALL_PATTERN`
- Add `AtomicBoolean consumerThrew = new AtomicBoolean(false);` to declarations
- Add `consumerThrew` to the JSON output
- Keep marker ordering rule: `CHAIN_STUBS` before `STUBS`; `FAULT_INJECTIONS` before `CALL_PATTERN`

**Update `execute()` signature:**
```python
def execute(
    self,
    scenario: ChaosScenario,      # now provides testConfig
    analysis: ConsumerAnalysis,
    consumer_file: Path,
    # experiment_code: str  ← REMOVE THIS PARAMETER
) -> ObservationResult:
```

**Test (no API key needed):**
```bash
.venv/bin/python - <<'EOF'
from pathlib import Path
from eventbreaker.scenarios.executor import ScenarioExecutor
from eventbreaker.scenarios.models import ChaosScenario, TestConfig, FaultInjection
from eventbreaker.analyzer.bridge import analyze

f = Path("src/test/resources/consumers/OrderConsumer.java")
analysis = analyze(f)

# Test 1: DUPLICATE — idempotency
s = ChaosScenario(
    scenarioType="IDEMPOTENCY_VIOLATION",
    reason="no guard",
    targetDependency="paymentClient",
    targetMethod="charge",
    expectedConcern="double charge",
    testConfig=TestConfig(callPattern="DUPLICATE", faultInjections=[]),
)
result = ScenarioExecutor().execute(s, analysis, f)
print(f"DUPLICATE: {result.status}")
for o in result.observations:
    print(f"  {o.target}: {o.callCount}x threw={o.threw}")

# Test 2: SINGLE with THROW — downstream failure
s2 = ChaosScenario(
    scenarioType="DOWNSTREAM_FAILURE",
    reason="charge fails",
    targetDependency="paymentClient",
    targetMethod="charge",
    expectedConcern="save skipped",
    testConfig=TestConfig(
        callPattern="SINGLE",
        faultInjections=[FaultInjection(dep="paymentClient", method="charge", fault="THROW")],
    ),
)
result2 = ScenarioExecutor().execute(s2, analysis, f)
print(f"SINGLE+THROW: {result2.status}")
for o in result2.observations:
    print(f"  {o.target}: {o.callCount}x threw={o.threw}")
print(f"  consumerThrew: {result2.consumerThrew}")
EOF
```

Expected:
- DUPLICATE: all 4 methods at 2×
- SINGLE+THROW: paymentClient.charge at 1× threw=True, orderRepository.save at 0 (skipped)

---

### [ ] STEP 5 — Add consumerThrew + callSequence to observations

**File:** `eventbreaker/models/observation.py`

```python
class ObservationResult(BaseModel):
    scenario: str
    status: str = "REPRODUCED"
    observations: list[ObservationEntry] = []
    consumerThrew: bool = False          # new
    callSequence: list[str] = []         # new — ordered dep.method calls
    rawOutput: str | None = None
    executionError: str | None = None
```

**File:** `eventbreaker/scenarios/executor.py` — update harness template

Add to JSON output in the harness:
```java
result.put("consumerThrew", consumerThrew.get());

// callSequence: ordered list of dep.method invocations
ArrayNode sequence = mapper.createArrayNode();
for (Map.Entry<String, Object> entry : deps.entrySet()) {
    for (Invocation inv : Mockito.mockingDetails(entry.getValue()).getInvocations()) {
        sequence.add(entry.getKey() + "." + inv.getMethod().getName());
    }
}
result.set("callSequence", sequence);
```

Update `_parse_output()` to extract `consumerThrew` and `callSequence` from JSON.

**Test:**
```python
# After STEP 4 test, add:
print(f"callSequence: {result2.callSequence}")
# Expected: ["orderRepository.get", "paymentClient.charge"]
# (save and publish never reached because charge threw)
```

---

### [ ] STEP 6 — Update CLI to use new signature

**File:** `eventbreaker/cli/main.py`

Step 3 (generate experiment plans) becomes a pure Python feasibility check — no LLM call, instant:

```python
# Old step 3: one Nemotron call per scenario
# New step 3: pure Python validation

from eventbreaker.scenarios.experiment import check_feasibility

feasible = []
theoretical = []
for scenario in scenarios:
    ok, reason = check_feasibility(scenario, analysis)
    if ok:
        feasible.append(scenario)
    else:
        theoretical.append((scenario, reason))
```

Update step 4 `executor.execute()` call — remove `plan.experimentCode` argument.

Update diagnosis step — pass `result.consumerThrew` and `result.callSequence` context (STEP 7).

**Test:**
```bash
# Full pipeline, no API key needed for steps 1+3+4
# Set NEBIUS_API_KEY for the full run
.venv/bin/eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
```

Expected: step 3 runs instantly (no "Generating experiment plans..." spinner per scenario).

---

### [ ] STEP 7 — Update diagnosis with richer observations

**File:** `eventbreaker/diagnosis/analyzer.py`

Switch model to `nvidia/Nemotron-3-Super-120b-a12b`.

Update `_build_prompt()` to include:
- `callSequence` — ordered list of calls actually made
- `consumerThrew` — did the consumer propagate or swallow the exception

Update `_has_observation_signal()` to also check `result.consumerThrew` as a signal.

The early-return (no signal → return []) stays.

**Test:**
```bash
.venv/bin/eventbreaker analyze src/test/resources/consumers/OrderConsumer.java
```

Verify findings reference the actual sequence and consumerThrew where relevant.

---

### [ ] STEP 8 — Validate all 12 consumers

Run the full pipeline on every consumer in `src/test/resources/consumers/`.
All must complete without errors. Status must be REPRODUCED or THEORETICAL (never ERROR).

```bash
for f in src/test/resources/consumers/*.java; do
    echo "=== $f ==="
    .venv/bin/eventbreaker analyze "$f"
done
```

For each consumer check:
- No `ERROR` status
- DUPLICATE scenarios → all methods at 2×
- SINGLE+THROW scenarios → target threw=True, downstream methods skipped
- Theoretical scenarios have a clear reason
- Findings reference real dep/method names

---

### [ ] STEP 9 — Real-world consumer validation (4 new consumers)

Write 4 consumers in `src/test/resources/consumers/` that test patterns not currently covered.
Each must be written BEFORE running the tool — not tuned to match the tool's output.

1. **TryCatchConsumer.java** — wraps every dep call in try-catch.
   Tests: executor still captures counts even when consumer swallows exceptions.

2. **TransactionalConsumer.java** — calls `transactionManager.begin()` before side effects,
   `commit()` after. Tests: tool identifies commit-without-begin risk.

3. **EarlyReturnConsumer.java** — multiple `if/return` paths, only one performs a side effect.
   Tests: tool correctly identifies which path is risky.

4. **MixedCallConsumer.java** — void calls, return-value calls, chained calls in same method.
   Stress-tests stub generator across all call patterns in one consumer.

**Test for each consumer:**
- Static analysis completes → ConsumerAnalysis looks correct
- `identify_risks()` returns relevant scenarios with valid testConfig
- `executor.execute()` returns REPRODUCED with meaningful observations
- Diagnosis returns at least one grounded finding

---

### [ ] STEP 10 — Demo polish + submission prep

1. Capture terminal screenshots of full pipeline on OrderConsumer
2. Record 3-minute demo video:
   - 0:00–0:30 — problem statement (duplicate payment bug)
   - 0:30–1:30 — full pipeline run on OrderConsumer (live terminal)
   - 1:30–2:30 — run on an unfamiliar consumer (one of the 4 new ones)
   - 2:30–3:00 — Nebius Token Factory + Nemotron model tier callouts
3. Verify no secrets in repo: `git log --all -S "NEBIUS" --oneline`
4. Test clean install from scratch: `rm -rf ~/.eventbreaker .venv && python -m venv .venv && ...`
5. Push final code, add open-source license (MIT), verify README renders on GitHub

---

## Key Files Reference

| File | Purpose |
|---|---|
| `eventbreaker/scenarios/executor.py` | Harness generation, Maven build, execution |
| `eventbreaker/agent/nemotron.py` | Nemotron Ultra risk identification + TestConfig |
| `eventbreaker/scenarios/models.py` | ChaosScenario, TestConfig, FaultInjection |
| `eventbreaker/scenarios/experiment.py` | Pure Python feasibility validator |
| `eventbreaker/cli/main.py` | 6-step pipeline CLI |
| `eventbreaker/diagnosis/analyzer.py` | Nemotron Super diagnosis |
| `eventbreaker/analyzer/bridge.py` | Java JAR → Python bridge |
| `eventbreaker/report/reporter.py` | Markdown report generator |
| `eventbreaker/models/consumer.py` | ConsumerAnalysis Pydantic models |
| `eventbreaker/models/observation.py` | ObservationEntry, ObservationResult, ReliabilityFinding |
| `target/eventbreaker-analyzer.jar` | Java static analyzer fat JAR |
| `src/test/resources/consumers/` | 12 example consumers |
| `/tmp/test_testconfig.py` | TestConfig prompt validation script — keep this |
