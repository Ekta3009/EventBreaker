# Real-world failure inventory (STEP 10 Phase B)

Per consumer, per stage: pass, or the exact failure mapped to a gap (G#, see docs/PLAN.md STEP 10).

## Run 1 — 2026-10-11, before any real-world fixes

| Consumer | Analyze | Risk ID | Feasibility | Build | Run | Diagnose |
|---|---|---|---|---|---|---|
| fineract `KafkaRemoteMessageListener` | ✗ G1 | — | — | — | — | — |
| killbill `OverdueListener` | ✗ G1 | — | — | — | — | — |

Both: `No @EventBreakerConsumer method found`. Failed before any LLM call (no cost).

## Run 2 — 2026-10-11, after G1 + G2 + G5 (analyzer recognises real listener annotations, event type from parameter, `--method`)

| Consumer | Analyze | Risk ID | Feasibility | Build | Run | Diagnose |
|---|---|---|---|---|---|---|
| fineract `KafkaRemoteMessageListener` | ✓ (2 deps, event `ContextualMessage`) | ✓ 4 scenarios | ✓ 4 reproducible | ✗ 4/4 compile errors | — | — |
| killbill `OverdueListener` (`handleTagInsert`) | ✓ (5 other listeners listed) | ✓ 4 scenarios | ✓ 3 reproducible, 1 theoretical | ✗ 3/3 compile errors | — | — |

### Build failures — root cause

The consumer is compiled verbatim, and stubs are generated only for types the analyzer knows about
(dependencies, event type, types of called methods). Real consumers import many more types:

| # | Failure | Consumer | Gap |
|---|---|---|---|
| B-1 | `package lombok.extern.slf4j / org.springframework.* / jakarta.inject does not exist` — framework **annotations** imported but never stubbed (`@Slf4j @Component @Conditional @Autowired @KafkaListener @Payload @Inject @Named @Subscribe @AllowConcurrentEvents`) | both | G6 |
| B-2 | Imported **non-dependency types** never stubbed: `Acknowledgment`, `KafkaWorkerCondition`, `ObjectType`, `ControlTagType`, `Account`, `OverdueConfig`, `DefaultOverdueState`, `CacheType`, `CallOrigin`, `UserType`, `BusEvent`, nested `OverdueAsyncBusNotificationKey.OverdueAsyncBusNotificationAction` … | both | new **G13** |
| B-3 | **Enum constants** used in logic (`ControlTagType.OVERDUE_ENFORCEMENT_OFF`, `ObjectType.ACCOUNT`, `CallOrigin.INTERNAL`) need enum stubs with those constants | killbill | G13 |
| B-4 | **JDK type stubbed**: generator wrote `src/main/java/java/util/UUID.java` → `package exists in another module: java.base` | killbill | new bug **G14** |
| B-5 | **Generic arity**: `CacheController<String, UUID>` but stub declares one type parameter → `wrong number of type arguments; required 1` | killbill | new bug **G15** |
| B-6 | **Stub return types** too weak for real logic: `UUID cannot be converted to Long` (`event.getSearchKey1()` passed where `Long` is expected) | killbill | G13 (patcher territory) |
| B-7 | Lombok `@Slf4j` → `log` used but never declared (masked by B-1 today) | fineract | G7 |

Expected next layer once it compiles (not yet observed): G4 (`onMessage(payload, acknowledgment)` called with one
arg), G8 (killbill side effects in private helpers), G10 (children loop), constructor with `@Named` param and a
field computed from a constructor argument (`objectIdCacheController`).

## Run 3 — 2026-10-11, real classpath (`--classpath`, Kill Bill 0.25.4 jars from Maven Central)

Building Kill Bill from source failed (master needs an unpublished SNAPSHOT parent POM). Release 0.25.4 has a
byte-identical `OverdueListener`, so the consumer is pinned to that tag and compiled against its published jars.

| Step | Result |
|---|---|
| Compile against real jars | ✓ — all B-1…B-7 build failures gone (no stubs needed) |
| Run, default fakes | ✓ executes, but real code is not steered: `handleTagInsert` string/enum branches never taken (→ one **false positive** from diagnosis); `handlePaymentInfoEvent` dies on `(DefaultOverdueConfig)` cast before any side effect |
| Static analysis | G3 fixed (static fields are not dependencies); G8 fixed (calls in same-class helpers inlined → `asyncPoster.insertOverdueNotification` ×3 and `accountApi.getChildrenAccounts` visible) |
| Path setup (`returnValues`), hand-written | ✓ `getOverdueConfig → MOCK:DefaultOverdueConfig` → full path: insert ×3 (account, parent, child) |
| K1 — THROW `accountApi.getChildrenAccounts` | ✓ consumer swallows, inserts 2 of 3 → diagnosis **HIGH, correct cause + fix** (after baseline-differential signal) |
| K3 — DUPLICATE | ✓ inserts ×6 → diagnosis HIGH (EXPECTED says LOW — severity over-call) |
| Path setup from Nemotron (proof B) | Super: correct on both cases (incl. `STRING:OVERDUE_ENFORCEMENT_OFF` for the hard branch) — executing its answer verbatim reaches insert ×3. Ultra: ran out of tokens on case 1, missed the string on case 2. |
| Sandbox | ✗ upload of 91 jars (41 MB) timed out — next: minimized single fat JAR |

New gaps found and fixed in this run: diagnosis keyed on "called >1×" (wrong for fan-out code) → replaced by
**diff against a fault-free baseline run**; diagnosis `max_tokens` 4096 → 8192 (truncated JSON).
Open: K2 needs a checked exception type (`OverdueApiException`) — THROW only raises RuntimeException.

## Run 4 — 2026-10-11, full CLI end to end in the Nebius sandbox

`eventbreaker analyze …/OverdueListener.java --method handlePaymentInfoEvent --classpath @cp.txt`
Pipeline: Ultra risk ID (cached) → Super path setup per scenario → fault-free baseline → scenarios → Super diagnosis vs baseline.
Sandbox upload fixed: one 8.8 MB JAR (fat JAR + jdeps-reachable classpath classes) + SDK HTTP timeout 10s → 120s.
4 min wall time, 9 Nemotron calls, ~$0.04 (measured from usage log).

| Scenario (Ultra) | Path setup (Super) | Execution | Diagnosis | vs EXPECTED |
|---|---|---|---|---|
| SILENT_DATA_LOSS — THROW `accountApi.getAccountById` | `getOverdueConfig → MOCK:DefaultOverdueConfig` | ✓ insert 1 (baseline 3), exception swallowed | MEDIUM on `getAccountById` (swallowed; parent+children skipped) | K1 ✓ found (severity low-ish; the "2 notifications lost" HIGH is hidden by the merge — see below) |
| IDEMPOTENCY_VIOLATION — DUPLICATE | same | ✓ insert 6 (baseline 3) | HIGH duplicate inserts | K3 found, **over-rated** (EXPECTED LOW) |
| RACE_CONDITION — CONCURRENT | same | ✓ insert 6 | merged into the HIGH above | K4 found, over-rated |
| PARTIAL_PROCESSING — THROW `asyncPoster.insertOverdueNotification` | none | ✓ first insert threw, consumer threw (propagates) | none (call-order rule: nothing committed, redelivery is clean) | ✓ correct no-finding |

Open issues found by this run:
- **Merge hides distinct problems on one method** (STEP 9c key = affectedMethod): K1 "notifications missing" and K3 "notifications duplicated" are both on `insertOverdueNotification`; only the first HIGH is shown.
- Duplicate/concurrent severity over-call (recompute-style side effects rated HIGH).
- A fault on a method called several times hits every call (can't fail only the parent insert).

## Run 5 — 2026-10-11, Fineract end to end in the Nebius sandbox

Fineract is not on Maven Central. Built from the **1.15.0 source archive** (listener byte-identical to develop@c5cc48a;
develop needs Java 25, 1.15.0 needs 21) with Gradle; classpath exported with `~/eb-classpaths/print-classpath.init.gradle`
(`:fineract-provider:ebClasspath`, 370 entries / 221 MB incl. build/classes dirs → auto-packed into cached jars).

Gaps hit and fixed:
| Failure | Fix |
|---|---|
| Lombok `@Slf4j` (`log` missing; Lombok is compile-only, not on runtime classpath) — G7 | Classpath mode uses the **project's own compiled consumer class** when present, not the copied source (annotation processors already applied) |
| `onMessage(ContextualMessage, Acknowledgment)` called with 1 arg — G4 | Non-event listener params get **recorder fakes**; `acknowledgment.acknowledge` is now observed |
| Harness re-imported consumer's compile-only imports | Keep only imports that resolve on the classpath / JDK |

| Scenario (Ultra) | Execution | Diagnosis | vs EXPECTED |
|---|---|---|---|
| THROW `stepExecutionRequestHandler.handle` | threw, consumer did not throw, `acknowledge` ×1 | HIGH — swallowed, Kafka message acknowledged; fix: rethrow / ack inside try | **F1 ✓** |
| THROW `inputInterceptor.beforeHandleMessage` | `handle` skipped, `acknowledge` ×1 | HIGH — step lost, acknowledged anyway | **F2 ✓** |
| DUPLICATE | `handle` ×2, `acknowledge` ×2 | merged into F1 ("Also seen in") | F3 — hidden by merge |
| (F4 afterHandleMessage cleanup failure) | not generated by Ultra | — | F4 ✗ missed |

1 min 44 s, 4 Nemotron calls, ~$0.008. Kill Bill re-verified after these changes: 4/4 REPRODUCED.

## Run 6 — 2026-10-11, `eventbreaker scan ~/fineract-1.15.0` (analysed in place, no copies)

`scan` found 7 consumer methods (lifecycle `onStartup` hooks skipped). Iterations needed (all general fixes):
| Failure | Fix |
|---|---|
| `LoginAttemptEventListener` missing from scan | JavaParser language level → Java 21 (`instanceof AppUser user`) |
| `ClassCastException Object → AppUser` | Fakes use the most specific return type (covariant override in `AppUserRepository`) |
| `NoteListener` / `LegacyMeetingAttendanceListener is not public` | Reflective create + call (package-private classes/methods) |
| Sandbox: consumer class missing (`Class.forName`) | Consumer class files added as jdeps roots |
| Sandbox jar reused from a previous run | Jar name keyed by builder version + fat-jar mtime + roots |
| `Cannot mock final class JsonCommand` | Inline mock maker in classpath mode (verified to self-attach in the sandbox JRE) |
| `TypeNotPresentException SimpleReflectiveProcessor` (annotation value) | Interfaces faked `withoutAnnotations()` (inline maker rejects it for classes) |
| Legacy: never past `"collectionsheet".equalsIgnoreCase(entityName)` | Path-setup retry with feedback → `event.getEntityName → STRING:collectionsheet` |
| `Object → Calendar` (`findById` → `Optional<T>`) | Element type of Optional/List/Set resolved against the faked repository |
| `Serializable → Long` (entity `getId()` returns `<ID extends Serializable>`) | Any type-variable return resolved (bounded too) |
| Helper fixes not picked up by cached builds | Build cache key includes the `__EBInstantiate` helper source |

| Listener | Scenarios | Findings | vs EXPECTED |
|---|---|---|---|
| `LoginAttemptEventListener.onAuthenticationFailure` | 4/4 | HIGH `saveAndFlush` — lost update on failed-attempt counter (fix: `@Version` / atomic UPDATE) | **L1 ✅** |
| `LoginAttemptEventListener.onAuthenticationSuccess` | 3/3 | none | ✅ correct |
| `NoteListener.onCreate` | 3/3 | HIGH duplicate `createNote` | N1 ✅ over-rated (LOW expected: in-process event) |
| `NoteListener.onUpdate` | 4/4 | MEDIUM duplicate `createNote`; Ultra risk "onUpdate calls createNote" | N1 ✅; N2 half-seen (risk only) |
| `NoteListener.onDelete` | 3/3 | MEDIUM duplicate `createNote`; Ultra risk "onDelete calls createNote" | N1 ✅; N2 half-seen |
| `LegacyMeetingAttendanceListener.onUpdate` | 2/3 (1 NOT REACHED) | HIGH `meetingRepositoryWrapper.save` — check-then-create race vs unique constraint | **M1 ✅** (MEDIUM expected); M3 correctly no finding |

Scan cost ~$0.13 + re-runs; all real-world work today ~$0.55 (usage log).
Open: Legacy scenario 1 NOT REACHED (its retry prompt was identical to a cached one) → STEP 10b setup reuse.

## Run 7 — 2026-10-12, path-setup reuse (STEP 10b)

Change: setups are planned for every scenario before any runs. A scenario first reuses a setup whose fault-free
baseline already reached its target (no LLM call), else asks Super (+1 retry); a second pass gives scenarios that
still miss a setup found later. A retry answer that still misses is removed from the cache.

| Listener | Reached | How the setups were found | Findings | Cost |
|---|---|---|---|---|
| `LegacyMeetingAttendanceListener.onUpdate` | **3/3** (was 2/3) | RACE: Super; DOWNSTREAM: reused; IDEMPOTENCY: Super + retry both missed → reused in pass 2 | HIGH duplicate `meetingRepositoryWrapper.save` (also seen in RACE) — M1 + M2; DOWNSTREAM correctly none | ~$0.02 |
| killbill `OverdueListener.handleTagInsert` | **4/4** (first CLI run; was a hand-run proof) | IDEMPOTENCY: Super retry (`STRING:OVERDUE_ENFORCEMENT_OFF`, `ENUM:ACCOUNT`, `MOCK:DefaultOverdueConfig`); RACE: reused; both SILENT_DATA_LOSS: reused in pass 2 | HIGH THROW `getAccountById` → `insertOverdueNotification` 1× vs baseline 3× (K1), + 2 MEDIUM on the same root cause; HIGH duplicate insert 6× vs 3× (K3, also seen in RACE = K4); THROW `getOverdueConfig` correctly none | ~$0.07 |

Open (→ STEP 13): one root cause reported 3× (`handleTagInsert` K1); Legacy finding claims a "unique constraint
violation" that stateless fakes cannot show — only "save called twice" was observed.

## Score per listener (after run 7)

Scored against EXPECTED.md. ✅ found · ⬆ found but severity over-rated · ✖ missed · ✔ negative control held
(no finding where none is expected). Risks are counted once even when several listeners show them.

| Listener | Expected | Result |
|---|---|---|
| fineract `KafkaRemoteMessageListener.onMessage` | F1 F2 F3 F4 | F1 ✅ · F2 ✅ · F3 ✅ (merged into F1) · F4 ✖ (Ultra never generated it) |
| killbill `OverdueListener.handlePaymentInfoEvent` | K1 K2 K3 K4 | K1 ✅ · K2 ✖ (needs checked-exception THROW) · K3 ⬆ HIGH vs LOW · K4 ⬆ HIGH vs LOW |
| killbill `OverdueListener.handleTagInsert` | (K1 K3 K4 — class-wide) | K1 ✅ · K3 ⬆ · K4 ⬆ — confirms the class-wide risks on a second handler |
| fineract `LoginAttemptEventListener.onAuthenticationFailure` | L1 L2 L3 | L1 ✅ · L2 ⬆ HIGH vs LOW (same finding as L1) · L3 ✔ |
| fineract `LoginAttemptEventListener.onAuthenticationSuccess` | — | ✔ no finding |
| fineract `NoteListener.onCreate/onUpdate/onDelete` | N1 N2 | N1 ⬆ (HIGH on onCreate, MEDIUM on the others; LOW expected) · N2 ✖ (class-level, G16 — Ultra named it as a risk on onUpdate/onDelete but nothing measured it) |
| fineract `LegacyMeetingAttendanceListener.onUpdate` | M1 M2 M3 | M1 ⬆ HIGH vs MEDIUM · M2 ⬆ HIGH vs LOW (newly reached in run 7) · M3 ✔ |

**Total: 11 / 14 expected risks found** (F1 F2 F3 K1 K3 K4 L1 L2 N1 M1 M2); missed F4, K2, N2.
**Negative controls: 3 / 3 held** (L3, M3, `onAuthenticationSuccess`); plus the Kill Bill PARTIAL_PROCESSING and
`getOverdueConfig` THROW scenarios correctly gave no finding.
**Severity:** 6 of the 11 are over-rated (K3 K4 L2 N1 M1 M2), every one on a duplicate/concurrent risk → STEP 13 calibration.
The earlier "11 / 14" (STEP 10) was not itemised per risk; this table is the itemised count.

## Run 8 — 2026-10-12, auto classpath (STEP 11), no `--classpath` flag

| Listener | Build | Classpath | Result |
|---|---|---|---|
| fineract `LoginAttemptEventListener.onAuthenticationFailure` (`scan . --only …` from `~/fineract-1.15.0`) | Gradle wrapper + bundled init script, 32 s first time | 370 entries, identical to the hand-made one | 4/4, HIGH `saveAndFlush` (L1/L2), L3 held — same as run 6; report written into the Fineract checkout |
| killbill `OverdueListener.handlePaymentInfoEvent` (0.25.4 release source, `overdue` module) | `mvn compile` once, then `dependency:build-classpath`, 7 s | 83 entries (runtime scope) | first try ✗ compile errors → fixed (below) → 4/4 |

Failures and fixes:
| Failure | Fix |
|---|---|
| Maven default scope pulled the module's test Mockito 5.3.1 + TestNG onto the classpath | `-DincludeScope=runtime` (the user property has no `mdep.` prefix — `-Dmdep.includeScope` is silently ignored) |
| Kill Bill: `javax.annotation.Nullable` / `com.google.inject` not found in `DefaultOverdueConfigCache`, `DefaultOverdueModule` | Executor copied the whole Maven module's sources (stand-in-mode behaviour); in real-classpath mode only the listener is copied, the module is already compiled on the classpath |

Diagnosis drift (→ STEP 13): Kill Bill gave 6 HIGH this time — 4 on one swallowed `getAccountById`/children failure —
and a HIGH on PARTIAL_PROCESSING, which run 4 correctly left empty.

## Run 9 — 2026-10-12, after removing stand-in mode (STEP 12)

Same commands as run 8. Execution unchanged: every scenario REPRODUCED and the per-call counts are identical to run 8
(diffed). Fineract: same single HIGH `saveAndFlush` finding. Kill Bill: 3 findings (HIGH `insertOverdueNotification`
missing, MEDIUM `getAccountById` swallowed, HIGH duplicate insert) vs 6 in run 8 on the **same** observations, and
PARTIAL_PROCESSING correctly empty again → diagnosis is not stable run to run (STEP 13).

## Run 10 — 2026-10-13, STEP 13 hybrid diagnosis (runs A and B; B re-asked every Nemotron answer)

Diagnosis now: code measures changes → Super labels each dependency call (effect + impact, 3 votes) and, for race
scenarios, answers "can two runs interfere?" (3 votes) → code rubric decides findings + severity → Super writes text →
Ultra reviewer may only rewrite/drop. Also fixed before this run: generic `save()` fake returns its argument (login
listener no longer crashes after the save; `onAuthenticationSuccess` reaches 3/3), MOCK path-setup types ignore
strings/comments, sandbox retries once. 9 listener methods, 0 errors, 0 not reached. Cost ~$0.36 for both runs.

**Run-to-run agreement (A vs B): 15 findings each; 14/15 same call + kind, 12/15 same severity.** All differences are
race findings: Super labelled the same read `impact DATA` in A and `impact SECURITY` in B (3/3 votes within each run),
which moves the race rule between MEDIUM and HIGH. Votes inside one run agree; across runs the impact label drifts.

| Listener | Findings (A / B where different) | vs EXPECTED |
|---|---|---|
| fineract `KafkaRemoteMessageListener` | HIGH swallowed `handle` failure; MEDIUM duplicate `handle`; MEDIUM concurrent (on `handle` / `beforeHandleMessage`) | F1 ✅ exact · F3 ✅ exact · F2 ✖ (Ultra did not generate that scenario this time) · F4 ✖ · concurrent = extra |
| fineract `LoginAttemptEventListener.onAuthenticationFailure` | concurrent lost update on the failed-login counter MEDIUM / HIGH | L1 ✅ (A under-rated) · L2 ✖ (`saveAndFlush` labelled IDEMPOTENT_WRITE → harmless) · L3 ✔ |
| fineract `…onAuthenticationSuccess` | none (now reaches all 3 scenarios) | ✔ |
| fineract `NoteListener` ×3 | LOW duplicate `createNote` (cap: in-process); MEDIUM concurrent on onCreate/onUpdate | N1 ✅ exact · N2 ✖ · concurrent ×2 = extra |
| fineract `LegacyMeetingAttendanceListener` | MEDIUM concurrent check-then-create | M1 ✅ exact · M2 ✖ (save labelled IDEMPOTENT_WRITE) · M3 ✔ |
| killbill `handlePaymentInfoEvent` | HIGH swallowed `getAccountById`; HIGH duplicate insert; concurrent MEDIUM / HIGH | K1 ✅ exact · K3 ⬆ HIGH vs LOW · K4 ⬆ · K2 ✖ |
| killbill `handleTagInsert` | HIGH swallowed `getChildrenAccounts`; HIGH duplicate insert | K1 ✅ · K3 ⬆ |

**Score: 8 / 14 found** (was 11/14), **exact severity 5–6 of 8** (was 5 of 11), **over-rated 2** (K3, K4; was 6),
**negative controls 3/3 + onAuthenticationSuccess**, false positives from run 8/9 gone (Legacy/LoginAttempt config
failures, Kill Bill "silently skipped"). Lost vs before: F2 (scenario not generated — the risk prompt changed because
the analysis now carries `listenerAnnotation`, so Ultra was re-asked), L2 + M2 (LOW duplicates on saves the labeller
calls idempotent). Extras: concurrent findings that repeat the duplicate one (Kafka, NoteListener).
