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
[Agent 2: Path Setup — Nemotron Super 120B]   (real-classpath mode only)
  Input:  source + scenario target call
  Output: ReturnValue[] (true/false/number/STRING:/ENUM:/MOCK:<ConcreteType>/NULL)
  What each fake must return so execution reaches the risky call (string checks, casts, guards).
  If the fault-free baseline does not reach the target: ONE retry with feedback
  (calls that happened + the exception that stopped it).
     │
     ▼
[Executor — Parameter-driven Java Assembly]
  Real-classpath mode (`--classpath`, the product path): compiles against the project's real jars,
  uses the project's own compiled consumer class, inline mock maker, reflective construct/inject/call
  (package-private listeners), name-based faults + return values in the fakes' default answer,
  generic return types resolved (JpaRepository<T,ID>, Optional<T>, entity getId()).
  Stand-in mode (no classpath, legacy — to be removed in STEP 12): generated stub classes.
  Every scenario runs next to a FAULT-FREE BASELINE of the same setup.
  Runs in Nebius Sandbox (one minimized JAR: fat JAR + jdeps-reachable classpath classes) or local JVM.
     │
     ▼
[Observations]
  Per dep.method: call count + threw; call sequence; consumerThrew + exception;
  notReached (target never called → status NOT_REACHED, never diagnosed)
     │
     ▼
[Agent 3: Diagnostician — Nemotron Super 120B]
  Input:  source + scenario + observations DIFFED AGAINST THE BASELINE (missing / extra calls)
  Output: ReliabilityFinding[] with severity + kind (failure | repeated | other)
  Duplicate/concurrent scenarios get explicit "repetition alone is not a problem" guidance.
  Findings merged per (method, kind).
     │
     ▼
[Report — terminal + eventbreaker-reports/<Class>.<method>-<timestamp>.md]
  What we did, path setup, consumer outcome, without-fault vs this-scenario table, NOT REACHED notes.
```

**LLM:** Nemotron Ultra (risk ID) + Nemotron Super (path setup, diagnosis) via Nebius Token Factory.
Ultra risk-ID and Super path-setup responses are cached (`~/.eventbreaker/cache/`), `--fresh` re-asks.
Every call is logged with tokens + estimated cost in `~/.eventbreaker/usage.csv`.
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

### [x] STEP 10 — Real-world consumer validation (first pass)

**Done 2026-10-11.** Details: `src/test/resources/real-world/` → `SOURCES.md`, `EXPECTED.md` (written before
running), `INVENTORY.md` (runs 1–5, every failure and fix). Real products only (no course/demo repos),
English, Apache/MIT/EPL/BSD. User does all git; GitHub token is in `.env` (revoke after use).

**Results — 9 listener methods from 2 products, 8 end to end in the Nebius sandbox:**

| Product | Listener | Status | Expected risks |
|---|---|---|---|
| Kill Bill 0.25.4 | `OverdueListener.handlePaymentInfoEvent` | ✅ full CLI, sandbox | K1 ✅, K3 ✅ (over-rated), K4 ✅ (over-rated), K2 ❌ (needs checked exception type) |
| Kill Bill | `OverdueListener.handleTagInsert` | ⚠ proven by hand-run proof only | — (re-run through CLI in STEP 13) |
| Fineract 1.15.0 | `KafkaRemoteMessageListener.onMessage` | ✅ | F1 ✅ F2 ✅ F3 ✅, F4 ❌ |
| Fineract | `LoginAttemptEventListener.onAuthenticationFailure` | ✅ | L1 ✅ (security: lockout counter lost update) |
| Fineract | `LoginAttemptEventListener.onAuthenticationSuccess` | ✅ | correctly no finding |
| Fineract | `NoteListener.onCreate/onUpdate/onDelete` | ✅ | N1 ✅ (over-rated); Ultra flagged update/delete calling createNote; N2 (class-level) ❌ — needs cross-method analysis (G16) |
| Fineract | `LegacyMeetingAttendanceListener.onUpdate` | ✅ 2/3 (1 NOT REACHED) | M1 ✅ (meeting create race) |

**Score: 11 / 14 expected risks found.** Cost of all real-world work: ~$0.55 measured.

**How it got there (all general fixes, see INVENTORY.md):** analyzer — real listener annotations,
`--method`, event type from parameter, static fields not deps, same-class helper calls inlined, Java 21
parser level, lifecycle hooks skipped, `--scan` mode; executor — `--classpath` real-types mode, project's own
compiled consumer class (Lombok etc. already applied), extra listener params as recorders (Kafka
`Acknowledgment`), inline mock maker (final classes), interfaces faked `withoutAnnotations`, reflective
create/inject/call (package-private), generic return-type resolution, minimized sandbox JAR via jdeps
(+ consumer as root), SDK transport timeout 120s, content-keyed caches; agents — Super path setup + 1 retry
with feedback, baseline diff in diagnosis, kind-aware merge, duplicate guidance; CLI — `scan`, usage log,
timestamped reports, NOT REACHED status.

**Classpaths (in `~/eb-classpaths/`):** `killbill-0.25.4.txt` (Maven Central, `killbill-0.25.4.pom.xml`);
`fineract-1.15.0.txt` (Gradle build of `~/fineract-1.15.0` source archive via `print-classpath.init.gradle`;
Fineract is not on Maven Central; develop needs Java 25, 1.15.0 needs 21).

**Known open issues (carried into steps below):** severity one notch high on duplicate/concurrent and
in-process (Spring) events; several findings per root cause (e.g. the read that threw + the lost write);
a fault hits every call of a method (can't fail only the Nth); THROW is always RuntimeException (K2);
per-method analysis misses class-level bugs (G16); path-setup retry reuses a cached answer when its
prompt is identical.

---

### [ ] STEP 10b — Quick wins + bookkeeping (Oct 12)

- [x] Path setup: before asking Super, **reuse a setup from another scenario whose baseline already reached
  this scenario's target** (fixes Legacy scenario 1); don't cache a retry answer that didn't help.
  *Done 2026-10-12:* `reusable_setup` (fewest overrides wins) + `forget_plan` in `path_setup.py`; CLI plans all
  setups first, then a second pass reuses later-found setups for scenarios still not reached. Verify via re-run below.
- [x] Re-run `LegacyMeetingAttendanceListener` and Kill Bill `handleTagInsert` through the CLI.
  *Done 2026-10-12:* Legacy 3/3 reached (scenario 1 via second-pass reuse), M1 HIGH on `meetingRepositoryWrapper.save`.
  `handleTagInsert` 4/4 reached (2 via reuse), 4 findings: swallowed `getAccountById` failure skips parent/child
  notifications (3 findings for one root cause → STEP 13), duplicate `insertOverdueNotification` 6× vs 3×.
- [x] INVENTORY.md run 6 (Fineract scan) + per-listener score table; keep EXPECTED.md untouched except notes.
  *Done 2026-10-12:* run 6 was already written; added run 7 (re-runs) + score table: 11/14, negative controls 3/3,
  6/11 over-rated (all duplicate/concurrent).
- [ ] User commits today's work (see session notes for the file list).

**Test:** `.venv/bin/python -m pytest` green; Legacy 3/3 reached.

---

### [ ] STEP 11 — "Run it in your repo" (Oct 13–15)

Goal: a Fineract/Kill Bill developer runs `eventbreaker analyze <Listener.java>` (or `scan .`) inside their
own checkout with **no `--classpath` flag** and gets a report in `<repo>/eventbreaker-reports/`.

- [x] **Auto classpath:** walk up from the listener to its module; `pom.xml` → `mvn -q dependency:build-classpath`
  (+ `target/classes`); `build.gradle(.kts)` → bundled init script (`ebClasspath` task, as in
  `~/eb-classpaths/print-classpath.init.gradle`). Cache per module keyed by build-file mtimes. `scan` resolves
  a classpath per module. Clear message if the module isn't built ("run ./gradlew classes / mvn compile first").
- [x] `--classpath` stays as an override.
- [x] **Packaging:** `pipx install` works; analyzer JAR shipped as package data (bridge must not assume our
  repo layout); `eventbreaker --version`.
- [x] Docs: one-paragraph "run it in your repo" in README.
- [x] **Decided 2026-10-12 (user):** analyzer JAR is gitignored (build output); shipped via a release wheel (STEP 15).

*Done 2026-10-12:* `eventbreaker/classpath/resolver.py` — module = dir above `src/main/java` with pom/build.gradle;
Maven `dependency:build-classpath -DincludeScope=runtime` (+ `target/classes`; test-scope Mockito/TestNG would clash);
Gradle bundled init script via the project's `./gradlew`, run from the settings root; cache keyed by build-file mtimes.
JAR built into the package by pom.xml (`outputFile`), `package-data` in pyproject, `--version`, `.env` loaded from cwd.
Executor: real-classpath mode no longer copies the whole Maven module's sources (they needed provided-scope Guice/jsr305).
Verified: Fineract `scan . --only LoginAttempt…onAuthenticationFailure` from `~/fineract-1.15.0` (Gradle, 370 entries,
4/4, same finding as before, report in the Fineract checkout); Kill Bill `handlePaymentInfoEvent` from the 0.25.4
release source `~/killbill-killbill-0.25.4` (Maven, 83 entries, 4/4); clean `pip install .` in a fresh venv works.
Open: provided-scope jars are not on the runtime classpath (fine so far); Kill Bill diagnosis this run gave 6 HIGH
(4 for one swallowed failure) and a HIGH on PARTIAL_PROCESSING that run 4 correctly left empty → STEP 13 stability.

**Test:** from `~/fineract-1.15.0`: `eventbreaker scan . --only LoginAttemptEventListener.onAuthenticationFailure`
with no classpath flag; same for a Kill Bill module (requires a Kill Bill checkout that builds — use release jars
if source build stays broken).

---

### [x] STEP 12 — Cleanup: remove stand-in mode (after STEP 11)

Decided 2026-10-11 (option 2): once auto-classpath works, real-classpath is the only mode.
- [x] Delete stand-in code in `executor.py` (`_generate_stubs`, `_stub_source`, `_generate_chain_stubs`,
  `_retype_to_chain`, `_analyze_chains`, `_patch_stubs_from_errors`, return-type guessing, `__EBChain`,
  subclass mock maker, non-real_types branches) — ~530 lines.
- [x] Delete `src/test/resources/consumers/` (16 synthetic), `src/main/java/com/eventbreaker/{consumer,event,model,service}`
  (`@EventBreakerConsumer` + Order sample classes), `@EventBreakerConsumer` support in the analyzer
  (keep listener detection for real annotations), tests that only cover stand-in mode.
- [x] Regression suite = real-world listeners (Fineract 7 + Kill Bill) via `scan`; keep offline pytest.

**Test:** pytest + Java tests green; Fineract + Kill Bill scan results unchanged.

*Done 2026-10-12:* executor.py 2004 → 1171 lines (stub generator, chain stubs, stub patcher + retry loop, return-type
guessing, subclass mock maker, module-source copy, matcher/argument-list builders removed); `ScenarioExecutor` requires
a classpath; CLI errors clearly when a file has no module and no `--classpath`. User chose to keep 6 toy consumers as
analyzer parser inputs (`src/test/resources/analyzer/`, now `@EventListener`); other 10 + sample `Order*` classes +
`@EventBreakerConsumer` deleted. Harness tests rewritten for real-classpath mode on Kill Bill/Fineract sources
(named faults, MOCK class literals, Kafka ack recorder, reflective create/inject/call). pytest 109, Java 15 green.
Regression: Fineract + Kill Bill call counts identical before/after (diffed); Fineract finding identical.

---

### [ ] STEP 13 — Accuracy (Oct 16–17)

#### ▶ START HERE next session (stopped 2026-10-16 ~19:30)
STEP 13 + STEP 14 done (INVENTORY runs 11 and 12); code uncommitted until the user commits (message given in session).
Next: **STEP 15** (RESULTS.md, demo, owner one-pager, README, release wheel). STEP 14b (reach fixes) only after the MVP.

#### Decisions 2026-10-16 (user) — implemented, measurement run 11 done

1. **Impact-label drift → three fixes, keep 3 votes.** Found: votes were asked at temperature 0, so the 3 seeds
   returned near-identical answers (always 3/3) — 5 votes would only have been 5 copies. And the list of calls
   to label came from the scenarios Ultra happened to generate, so the question changed between runs.
   Now: votes at temperature 0.7 (`classify.VOTE_TEMPERATURE`, real samples), asked in parallel (no extra wall
   time), and every dependency call from the static analysis is labelled (stable question). Go to 5 votes only
   if run 11 still drifts.
2. **Coverage → code adds a THROW scenario for every dependency call** no Ultra scenario already fails
   (`scenarios/coverage.py`, scenario type `DEPENDENCY_FAILURE (dep.method)`). F2/F4 now always tested.
3. **Race without interference → no finding** (the repeated work is the duplicate scenario's finding; the changes
   are listed under harmless changes with a note).
4. **New effect label `REFRESH`** (asks for derived state to be recomputed from the source of truth). Duplicate
   with only an extra REFRESH → LOW; a lost REFRESH counts as lost work; threw after a REFRESH → no finding.
5. **L2/M2 harmless → accepted** (EXPECTED rates both LOW); disclosed in RESULTS.

**Run 11 (INVENTORY):** 10/14 found (was 8), 17 of 18–19 findings identical A vs B, no severity drift, over-rated 3
(F4, K3, K4). Follow-up decisions (user, 2026-10-16): interference question → 5 votes + an unusable reply is re-asked
once (done, 144 tests); REFRESH never chosen for K3 → accept + disclose, watch STEP 14; F4 HIGH vs expected LOW →
keep the generic rule, record as a judgement disagreement in RESULTS. Verify the 5-vote change on Kafka +
`handlePaymentInfoEvent` (A + `--fresh` B), then user commits.

**Testing routine (user, 2026-10-16):** test rounds run on the local JVM (`NEBIUS_PROJECT_ID=` empty — `.env`
never overrides it); the Nebius sandbox is used only for final result runs and the demo (each sandbox run
installs the JRE first, ~30 s). Check a change on the 2–3 listeners it affects; the full 9-listener A/B round
only before a commit. Later options, not done: `--score-only` (skip reviewer + text, ~80% of LLM cost),
cheaper run B (re-ask only the votes), sandbox image with Java preinstalled.

Tests: 143 green (new `test_coverage.py`, REFRESH + race rows in `test_rubric.py`, parallel/temperature votes).
Next: read run 11 A/B → INVENTORY run 11 → user commits → STEP 14.

#### Results so far (run 10 vs run 8/9)

| | Before | Now |
|---|---|---|
| Expected risks found | 11/14 | 8/14 (lost F2 = not generated, L2/M2 = harmless) |
| Over-rated | 6 | 2 (K3, K4) |
| False positives (config failures, Kill Bill "silently skipped") | 3 | 0 |
| Negative controls | 3/3 | 3/3 + `onAuthenticationSuccess` now reaches 3/3 |
| Run-to-run (A vs B, all answers re-asked) | finding counts differed | 12/15 identical, 14/15 same call + kind |

#### How diagnosis works now (decisions 2026-10-12, user)
Research (LLM non-determinism): temperature 0 is not deterministic on batched servers; the common pattern is
"code decides, the LLM answers small closed questions". So: code measures every change vs the fault-free baseline →
Super labels each dependency call (effect READ / IDEMPOTENT_WRITE / SIDE_EFFECT, impact MONEY / SECURITY /
EXTERNAL_MESSAGE / DATA / MINOR; 3 seeds, majority, ties → cautious; `diagnosis/classify.py`) and, for each race
scenario, "can two runs interfere?" (3 votes) → fixed rubric in code decides findings + severity
(`diagnosis/rubric.py`, table in its docstring; gap choices: swallowed failed write = lost work; race without
interference = duplicate rule without in-process cap; unknown/tie → cautious) → Super writes text only → Ultra
reviewer may rewrite text or DROP, severity disagreement shown as a note → temperature 0 + seed + cache
(`agent/llm.py`, `--fresh` re-asks). Transport from the listener annotation (analyzer `listenerAnnotation`).
Superseded from round 1: per-root-cause grouping by the LLM, coverage re-ask / "unexplained change".
**Rules:** never tune prompts toward EXPECTED.md; prompt changes only follow scenarios the agent created at runtime;
rubric tested with synthetic listeners only. EXPECTED.md listeners are now a tuning set — STEP 14's new listeners
(EXPECTED written first) are the real generalization test.

Also fixed this session (user-approved): generic `<S> S save(S)` fake returns its argument (login listener crashed
after the save in every scenario since STEP 10); path-setup MOCK types ignore strings/comments and are skipped if not on
the classpath; sandbox retries once on a transport error.

#### Checklist
- [x] Severity calibration — transport + in-process cap + rubric + REFRESH label (2026-10-16).
- [x] One finding per root cause per scenario (rubric groups by scenario; all changes kept as evidence).
- [ ] Optional: THROW with a declared checked exception type (K2); fail only the Nth call (parent insert).
- [~] Stability — run 10: 12/15 identical; fixes for label drift done 2026-10-16, run 11 measures them.
- [x] Coverage — code-generated failure scenario per dependency call (2026-10-16).

---

### [ ] STEP 14 — Breadth (Oct 18–20)

- [x] EXPECTED written first (2026-10-16, before any run): InvoiceListener (known limit G17 — logic behind a
  constructor-built `retryableSubscriber`), BeatrixListener, PushNotificationListener, PaymentTagHandler,
  KillbillEventHandler (negative control). Severities judged from the listener + callee where noted.
- [x] Compile `invoice,beatrix,payment,profiles/killbill,jaxrs` (only `overdue` was built), then run these 5 classes
  (local JVM) and score against EXPECTED — no prompt/rubric changes before scoring. *Done → INVENTORY run 12.*
  *Fixes found on the way (user-approved 2026-10-16):* (1) harness import filter kept every `javax.*` import and accepted
  a bare package match → `javax.annotation.Nullable` (jsr305, compile-only) broke BeatrixListener; now only JDK javax
  packages pass and a class import must match a type; the class index also reads class folders. (2) Maven resolver
  overrides `excludeGroupIds`/`excludeArtifactIds`/… with empty values — hawkbit sets `excludeGroupIds=org.eclipse`
  for its license tool, which silently dropped all hawkbit modules from the classpath. 146 tests.
  *First STEP 14 run (17:15–17:50):* mostly tool gaps — PushNotification harness name clash (`mapper`), PaymentTagHandler
  crash hidden behind "No EVENTBREAKER_RESULT", hawkbit 18/18 errors (`@Header String` params were faked; Mockito cannot
  fake String = **G20**), InvoiceListener race findings named calls that never ran. Fixed (user-approved): harness
  locals prefixed `__eb`; harness reports `Throwable` + first frame and the CLI shows it; plain-value listener
  params get values, settable by path setup as `param.<name>` (prompt section only for such listeners); a race
  finding only names voted calls that actually ran. 150 tests. 5-vote check: Kafka A = B; `handlePaymentInfoEvent`
  race still flips (4/5 yes in A, none in B). Beatrix runs now but most scenarios NOT REACHED; its MEDIUM race on a
  READ/MINOR call contradicts EXPECTED B4 (false positive). Re-run of all new listeners in progress.
  *hawkbit (user: option 1, timebox 21:00):* G19 static gate fixed generically — the project's own static helpers
  (same first 3 package segments; found via static imports / `Imported.method(`) run for real inside a Mockito
  `mockStatic` opened around each listener call (thread-local); only on IllegalStateException/NPE ("not
  initialized") do they answer like fakes; path setup may override `<Class>.<method>…`; unfakeable classes print
  `EB_STATIC_SKIPPED`. Also: plain params were read before the overrides were registered (fixed + test); MOCK of JDK
  types was dropped (fixed); constructor-argument fakes are named after the source's parameters (`rabbitTemplate`)
  and listed for path setup when they are not fields (G21). Manual check: duplicate THING_CREATED now measured
  (findOrRegister ×2, cancel message ×2). 153 tests. Prompt sections only appear for listeners that need them;
  Legacy (has project statics) needs a regression re-run.
- [x] Eclipse hawkbit `AmqpMessageHandlerService` (RabbitMQ, EPL-2.0) — EXPECTED first; run 12: 2/18 reached (see STEP 14b).
- [ ] If time: Apereo CAS `RegisteredServiceKafkaDistributedCacheListener` (Kafka) or DHIS2 `ApiAuditConsumer` (JMS).
  (Earlier GitHub search: 8 product repos of 1,598; only CAS / DHIS2 / KIE real products — raw results lost with the temp dir.)

---

### [ ] STEP 14b — Reach fixes (after the MVP is complete; user decision 2026-10-16)

STEP 14 showed the tool rarely *drives* unseen listeners to the risky line: path setup can only answer with
text / numbers / booleans / enums / "object of class X" / null, so code that needs anything else stops early
("not reached"). All fixes are generic capabilities, not prompt tuning:
- [ ] **Generic registry type** (PaymentTagHandler, G18): a fake of `Registry<PluginApi>` returns a fake `PluginApi`
  (resolve the constructor/field's type arguments). ~20 min, no LLM.
- [ ] **Checked exceptions on THROW** (Kill Bill K2, Push P1/P2, Beatrix B1): when the faulted method declares checked
  exceptions, throw one of them, so the listener's own catch-and-swallow path is what gets tested. ~40 min.
- [ ] **List answers** (Push P5, G23): value vocabulary `LIST:<value>` = one-element list holding that value. ~20 min.
- [ ] **Event subtype** (Beatrix B2, G22): path setup may choose the event's concrete class (must be a subtype named in
  the source); the harness fakes that class. ~45 min.
**Honesty rule:** after these fixes the STEP 14 listeners are no longer unseen. RESULTS reports both numbers — before
(STEP 14 first runs) and after — and, if time allows, a few untouched listeners (remaining Kill Bill, hawkbit
`AmqpMessageDispatcherService`) with EXPECTED written first.

---

### [ ] STEP 15 — Results + submission (Oct 21–30)

- [ ] `real-world/RESULTS.md`: "N listeners, M products, X/Y expected risks, Z valid extras, K false positives".
- [ ] Demo consumer: Fineract `KafkaRemoteMessageListener` (ack after failure) or `LoginAttemptEventListener` (lockout race).
- [ ] **Owner one-pager** (`docs/EXPLAINER.md`, plain language, for the user — not judges): problem → how it works
  (Ultra fills a fixed TestConfig, never writes code → executor builds + runs it in the Nebius sandbox → Super
  diagnoses against a fault-free baseline) → evidence (score table) → known limits. Then a mock Q&A: Claude asks
  likely judge questions, user answers, gaps get explained. Do this BEFORE recording the video.
- [ ] README (what / how / run it in your repo / results / Nebius sandbox + Nemotron roles), MIT license.
- [ ] 3-minute video: problem → `scan` on Fineract → report evidence → sandbox + Nemotron callouts.
- [ ] **Release wheel:** `mvn package` → `python -m build` → check the wheel contains `analyzer/eventbreaker-analyzer.jar`
  + `classpath/print-classpath.init.gradle` → fresh-venv install test → user creates GitHub Release `v0.1.0` with the
  wheel attached. README install = `pipx install <release wheel URL>` (dev install from source stays documented).
- [ ] Secrets check (`git log --all -S "NEBIUS"`, `.env` ignored), clean install test, push.
- [ ] Buffer Oct 28–30. **Submit before Oct 30, 10:00 AM PDT.**

---

## Key Files Reference

| File | Purpose |
|---|---|
| `eventbreaker/cli/main.py` | CLI: `analyze` (+ `--method`, `--classpath`, `--fresh`), `scan` (+ `--all`, `--only`) |
| `eventbreaker/agent/nemotron.py` | Nemotron Ultra risk identification + TestConfig (cached) |
| `eventbreaker/agent/path_setup.py` | Nemotron Super path setup (returnValues), reachability, retry feedback |
| `eventbreaker/agent/usage.py` | Token/cost log → `~/.eventbreaker/usage.csv` |
| `eventbreaker/diagnosis/analyzer.py` | Nemotron Super diagnosis, baseline diff, kind-aware merge |
| `eventbreaker/scenarios/executor.py` | Harness generation (`__EBInstantiate` helper), Maven build, sandbox JAR, execution |
| `eventbreaker/scenarios/models.py` | ChaosScenario, TestConfig, FaultInjection, ReturnValue |
| `eventbreaker/scenarios/experiment.py` | Pure Python feasibility validator |
| `eventbreaker/sandbox/executor.py` | Nebius ConTree sandbox run (transport timeout 120s) |
| `eventbreaker/analyzer/bridge.py` | Java JAR → Python bridge (`analyze`, `scan`) |
| `eventbreaker/report/reporter.py` | Markdown report (timestamped, never overwritten) |
| `src/main/java/com/eventbreaker/analyzer/` | Static analyzer (listener detection, helper inlining, scan) |
| `src/test/resources/real-world/` | Real-world consumers + SOURCES / EXPECTED / INVENTORY |
| `src/test/resources/consumers/` | 16 synthetic consumers (stand-in mode — removed in STEP 12) |
| `~/eb-classpaths/` | Classpath files + Gradle init script (outside repo) |
