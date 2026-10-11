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

### [x] STEP 4 — Rewrite executor Java assembly (core change)

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

### [x] STEP 5 — Add consumerThrew + callSequence to observations

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

### [x] STEP 6 — Update CLI to use new signature

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

### [x] STEP 7 — Update diagnosis with richer observations

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

### [x] STEP 8 — Validate all 12 consumers

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

### [x] STEP 9 — Real-world consumer validation (4 new consumers)
> Done 2026-10-10. 4 consumers added; 16/16 run with 0 ERROR (59 scenarios). Fixed: arity-aware
> matchers (single `any()` silently never matched multi-arg varargs stubs — faults were no-ops),
> primitive/generic/collection return stubs, event numeric getters (default answer → 1/true),
> `bad operand types` patcher, dependency method chains via __EBChain, report labels/✗/always-save.
> Exposed precision issue → STEP 9b.

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

### [x] STEP 9b — Diagnosis precision (false positives on safe consumers)

Found in STEP 9 regression: ReadOnly / CacheRefresh / IdempotentGuard produce 5–7 findings each
(mostly HIGH), e.g. "orderRepository.findById invoked twice — HIGH". Causes:
1. Diagnosis treats any callCount > 1 as risk, including reads and idempotent writes.
2. Exception propagated before any write is flagged — that is the safe outcome (broker retries).
3. Stateless mocks: guard methods (`contains`) always return false, so DUPLICATE can't exercise guards.

Options (decide with user before implementing):
- A. Prompt calibration in `diagnosis/analyzer.py` (reads benign, HIGH only for duplicated/partial writes)
- B. Deterministic read/write classification by method name; drop read-only "called 2×" findings
- C. Stateful guard mocks for DUPLICATE (`thenReturn(false, true)` on guard-style booleans)

**Decision (2026-10-10): A + a call-order rule. B and C deferred** — both guess from method names,
which may not hold for real-world code. Revisit only if STEP 10's failure inventory shows they're needed.
- A: diagnosis prompt classifies each call from source (READ / IDEMPOTENT WRITE / NON-IDEMPOTENT
  SIDE EFFECT) with explicit severity rules; model returns `NONE` for benign observations, `_parse` drops them.
- Call-order rule (deterministic, name-free): consumer threw AND no dep call completed without
  throwing → no finding (nothing changed; redelivery is clean). Swallowed exceptions are NOT exempt.
- Known limitation until C: IdempotentGuard's DUPLICATE finding (stateless `contains` always false).

**Test:** safe consumers → 0 HIGH findings (concurrent check-then-act race on IdempotentGuard is valid
and may stay); OrderConsumer / TryCatch / Transactional findings unchanged.

**Result (2× full 16-consumer run):** ReadOnly 0 findings both runs; CacheRefresh 0 HIGH (1–3 MEDIUM on
idempotent `put` — prompt only partly followed); real bugs still HIGH (OrderConsumer charge, Transactional
debit/credit, TryCatch swallowed `book`, EarlyReturn issueRefund); audit logs now LOW. Left for STEP 10
inventory: residual MEDIUM on idempotent writes, run-to-run severity variance, IdempotentGuard `contains` (needs C).

---

### [x] STEP 9c — Merge repeated findings across scenarios

Same method is reported once per scenario (Transactional: `ledgerRepository.credit` 4–5×), inflating counts
(8 vs 13 HIGH between runs). Merge deterministically before printing/reporting.

**Done 2026-10-11.** Key = `affectedMethod` only: keep most severe finding (first on ties), other scenario
labels go to `alsoSeenIn` ("Also seen in" in terminal + report). `merge_findings()` in `diagnosis/analyzer.py`,
tests in `tests/test_merge_findings.py`. Offline check on saved TryCatch report: 15 findings → 4 (8 HIGH → 2).
Live 16-consumer run deliberately skipped (API budget) — covered by STEP 10 C2 regression.

---

### [ ] STEP 10 — Real-world consumer validation

**Goal:** prove EventBreaker works on consumers we did not write. Judges will ask
"does this work on real code?" — this step produces the answer, with numbers.

**Principle — measure before fixing.** Do not speculatively fix every gap listed
below. Collect real consumers first, run them unmodified, and build a failure
inventory. Only then fix — in one pass, ordered by how many consumers each fix
unblocks. This avoids reworking the analyzer/executor multiple times.

**Principle — files stay unmodified.** Real-world files are copied byte-for-byte
(plus a source/license header comment). Any adaptation (stripping framework
annotations, etc.) happens inside the tool, never by hand-editing the file.
If we hand-edit, the result does not count as "real-world".

#### Known gaps (from reading `ConsumerAnalyzer.java` + `executor.py`, 2026-10-10)

Expected to break on real code — confirm with the inventory before fixing:

| # | Gap | Where | Real-world trigger |
|---|---|---|---|
| G1 | Only `@EventBreakerConsumer` recognised | `ConsumerAnalyzer.isConsumerMethod` | `@KafkaListener`, `@RabbitListener`, `@JmsListener`, `@SqsListener`, `@StreamListener`, `@EventListener` |
| G2 | `eventType` read from annotation string | `ConsumerAnalyzer.extractEventType` | Real listeners have no such attr — derive from parameter type; unwrap `ConsumerRecord<K,V>`, `Message<T>`, `List<T>` (batch) |
| G3 | Every field becomes a dependency | `ConsumerAnalyzer.extractDependencies` | `static final Logger log`, constants, `ObjectMapper`, `@Value String topic` — pollutes mocks + constructor args |
| G4 | Harness calls `consumer.m(event)` with 1 arg | `executor._build_call_pattern` | `(@Payload T, @Header String key, Acknowledgment ack)` |
| G5 | Only first listener method analysed | `ConsumerAnalyzer.analyze` | Classes with several `@KafkaListener` methods |
| G6 | Framework annotations/imports don't compile | stub generation | `@Service`, `@KafkaListener`, `@Slf4j`, `@Transactional` — not on classpath, not stubbed |
| G7 | Lombok | consumer instantiation | `@RequiredArgsConstructor` + `final` fields (no constructor in source → won't compile); `@Slf4j` → `log` field doesn't exist |
| G8 | Dep calls inside private helpers invisible | analysis scope = listener method only | `handle(e) { validate(e); process(e); }` — feasibility check rejects faults on helper-only calls |
| G9 | Thin listeners | — | `listener → orderService.handle(event)` — only one dep call, little to test. **Selection criterion, not a fix.** |
| G10 | Collection returns are empty → loop bodies never run | `_COLLECTION_STUB_VALUES` | `for (f : friendService.getFriends(id)) { ws.send(f) }` (ruoyi AdminUserProfileUpdateConsumer) |
| G11 | Dep calls inside lambdas passed to static utils never execute | stubs | `TenantUtils.execute(id, () -> { deviceService... })` (ruoyi IotDeviceMessageSubscriber) |
| G12 | Listener found by interface, not annotation | analyzer | `implements ApplicationListener<E>` (fineract BulkImportEventListener), custom bus interfaces |

#### Phase A — Source selection (no code changes)

- [ ] **A1. Shortlist 8–10 candidate consumers** from public repos. Criteria:
  - Permissive license (Apache-2.0 / MIT) — record license per file
  - Reputable / recognisable source (Spring samples, Eventuate, Confluent examples, well-starred demo shops)
  - Listener does real work: **≥2 side-effecting dep calls in the listener body** (G9 — skip thin listeners)
  - Spread across frameworks: Kafka, RabbitMQ, at least one other (JMS/SQS/Spring events)
  - At least one with a known reliability smell (no idempotency, dual write, swallowed exception)
- [x] **A2. Pick final 4–6**, maximising framework + pattern variety.
  Picked (2026-10-10), one distinct insight each: fineract `KafkaRemoteMessageListener` (swallow + ack),
  ruoyi `AdminUserProfileUpdateConsumer` (@TransactionalEventListener + @Async, per-item swallow),
  ddd-library `SheetsReadModel` (negative control), killbill `OverdueListener` (@AllowConcurrentEvents),
  mall `CancelOrderReceiver` (class-level @RabbitListener + @RabbitHandler, thin). fineract
  `BulkImportEventListener` dropped (overlaps #1) — so G12 is not covered by this set.
- [ ] **A3. Copy into `src/test/resources/real-world/<repo-name>/`**, unmodified, with header:
  `// Source: <url @ commit sha>  License: <license>`. Add `real-world/SOURCES.md` table.
- [ ] **A4. Write expected risks BEFORE running the tool** — `real-world/EXPECTED.md`,
  one section per consumer: hand-identified risks (type + dep.method + one-line reason).
  This is the ground truth used for scoring in Phase D.

#### Phase B — Failure inventory (no code changes)

- [ ] **B1. Run every real-world consumer through the pipeline as-is.**
- [ ] **B2. Record per consumer, per stage** (analyze / risk ID / feasibility / build / run / diagnose):
  pass or the exact failure → `real-world/INVENTORY.md`. Map each failure to a gap (G1–G9) or a new G#.
- [ ] **B3. Rank gaps** by number of consumers each blocks. Decide fix vs documented limitation
  (confirm with user before implementing — see working rules).

#### Phase C — Fix in one pass

Expected shape (finalise after B3):
- Analyzer: recognise listener annotations (G1), derive event type from param type with wrapper
  unwrapping (G2), skip `static`/constant/logger fields (G3), `--method` option or analyse all
  listener methods (G5), optionally inline same-class private helper calls (G8).
- Executor: pass mocks for extra listener params (G4), strip framework annotations from the
  copied consumer + inject `log` field / synthesise constructor for Lombok (G6, G7).
- [ ] **C1. Implement agreed fixes.**
- [ ] **C2. Regression: all 16 synthetic consumers still pass** (same checks as STEP 8).
  Run `.venv/bin/python -m pytest` after every fix (offline, ~4s) before the full pipeline run.

#### Phase D — Score + results

- [ ] **D1. Re-run all real-world consumers.** Every run REPRODUCED or THEORETICAL with a reason — never ERROR
  (or ERROR documented as a known limitation in INVENTORY.md).
- [ ] **D2. Score against EXPECTED.md:** per consumer — expected risks found / missed / extra findings
  (extras reviewed by hand: valid or false positive).
- [ ] **D3. Write `real-world/RESULTS.md`** — the table used in README + demo:
  "N consumers from M open-source repos, X/Y expected risks reproduced, Z new valid findings, K false positives."
- [ ] **D4. Pick the demo consumer** — the real-world one with the clearest, most visual finding.

**Done when:** RESULTS.md exists with honest numbers, no unexplained ERRORs, synthetic suite still green.

---

### [ ] STEP 11 — Demo polish + submission prep

1. Capture terminal screenshots of full pipeline on OrderConsumer
2. Record 3-minute demo video:
   - 0:00–0:30 — problem statement (duplicate payment bug)
   - 0:30–1:30 — full pipeline run on OrderConsumer (live terminal)
   - 1:30–2:30 — run on a real-world open-source consumer (picked in STEP 10 D4)
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
