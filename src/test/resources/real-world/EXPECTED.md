# Expected risks — hand-identified BEFORE running EventBreaker

Ground truth for STEP 10 Phase D scoring. Written 2026-10-11 from reading the source only;
no tool output was consulted. Do not edit after the first tool run except to append
clearly marked notes.

Severity is our own judgement of production impact. "Detectable" says whether the
current tool can observe the risk at all (dependency = injected field), so misses
caused by tool scope are not confused with misses caused by bad analysis.

---

## fineract / KafkaRemoteMessageListener

Remote Spring Batch partition worker: receives a `ContextualMessage`, converts it via
`inputInterceptor.beforeHandleMessage`, runs the step via `stepExecutionRequestHandler.handle`,
then always acknowledges the Kafka offset.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| F1 | Swallowed failure → message loss | `stepExecutionRequestHandler.handle` | HIGH | `catch (Exception e)` only logs; `acknowledgment.acknowledge()` runs anyway, so a failed partition step is committed and never redelivered. | Yes — THROW on `handle`, consumer does not throw. Ack itself is a parameter, not a field (see gaps). |
| F2 | Swallowed failure → message loss | `inputInterceptor.beforeHandleMessage` | HIGH | Same catch: if context setup fails, `handle` is never called and the offset is still acknowledged. | Yes — THROW on `beforeHandleMessage`, `handle` count 0, consumer does not throw. |
| F3 | Duplicate delivery | `stepExecutionRequestHandler.handle` | MEDIUM | Kafka is at-least-once (rebalance before ack). No dedup in the listener; whether a re-run step is harmful depends on Spring Batch step state, which is outside this file. | Yes — DUPLICATE → `handle` ×2. |
| F4 | Cleanup failure → redelivery | `inputInterceptor.afterHandleMessage` | LOW | Runs in `finally` before the ack; if it throws, the ack is skipped and the already-completed step is redelivered and re-run. | Yes — THROW on `afterHandleMessage`. |

Expected tool gaps: G1 (`@KafkaListener`), G4 (`Acknowledgment` 2nd param), G6/G7 (`@Component`,
`@Slf4j` → `log` field does not exist), field injection via `@Autowired` with no constructor.
New gap candidate: side effects on listener **parameters** (`acknowledgment.acknowledge()`) are not
observable today — F1's "acked anyway" half can only be inferred, not counted.

---

## killbill / OverdueListener

Six `@Subscribe @AllowConcurrentEvents` handlers. Each one checks `busDispatcherOptimizer.shouldDispatch`
and then queues an overdue REFRESH/CLEAR notification for the account, plus its parent and
payment-delegating children (private helper `insertBusEventIntoNotificationQueue`).

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| K1 | Swallowed partial failure | `accountApi.getAccountById` / `accountApi.getChildrenAccounts` / 2nd+ `asyncPoster.insertOverdueNotification` | HIGH | The account's own notification is inserted first; parent/children refresh runs in `try { … } catch (Exception e) { log.error }`. A failure there leaves related accounts' overdue state stale while the event counts as handled. A mid-loop failure refreshes some children but not others. | Partly — logic is in a private helper (G8); the children loop never runs with empty-list stubs (G10). |
| K2 | Silent drop on config error | `overdueConfigCache.getOverdueConfig` | MEDIUM | `OverdueApiException` is caught → config treated as null → `shouldInsertNotification` returns false → nothing queued and no error raised. A transient config/cache failure silently drops the refresh. | Partly — private helper (G8); needs a checked-exception THROW. |
| K3 | Duplicate delivery | `asyncPoster.insertOverdueNotification` | LOW | Redelivery queues another REFRESH for the same account. Overdue evaluation is a recompute of current state, so this is mostly extra work, not wrong state. A HIGH here would be a false positive. | Yes (if G8 is solved) — DUPLICATE → insert ×2. |
| K4 | Concurrent events for one account | `asyncPoster.insertOverdueNotification` | LOW | `@AllowConcurrentEvents` lets e.g. payment-info and invoice-adjustment events for the same account run in parallel → several REFRESH notifications. Same reasoning as K3: recompute, not corruption. | Yes (CONCURRENT) if G8 is solved. |

Expected tool gaps: G1 (`@Subscribe`, Kill Bill event bus), G3 (`static final Logger log`, `Clock`,
fields assigned from constructor-computed values like `objectIdCacheController`), G5 (six listener
methods — which one is analysed?), G8 (all side effects are inside private helpers), G10 (children loop).
Negative-control aspect: K3/K4 should come out LOW — HIGH duplicate/concurrency findings here count as false positives.

---

## fineract (scan) — listeners analysed in place in `~/fineract-1.15.0` (tag 1.15.0)

Written 2026-10-11 from source only, before running `eventbreaker scan`. All three classes are
**synchronous Spring `@EventListener`s**: there is no broker and no redelivery — an exception propagates
straight back to whoever published the event. Duplicate-delivery risks are therefore LOW in practice
(only a double publish would trigger them); failure and concurrency matter more.

### LoginAttemptEventListener.onAuthenticationFailure (+ onAuthenticationSuccess)

Reads the user, `user.registerFailedLoginAttempt(maxRetries)` (increments a counter, locks at the limit),
`appUserRepository.saveAndFlush(user)`, then evicts two caches. `@Transactional`.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| L1 | Concurrency — lost update | `appUserRepository.findAppUserByName` → `saveAndFlush` | HIGH (security) | Read-modify-write of the failed-attempt counter with no lock/version check visible here: parallel failed logins for one user can each read N and write N+1, so an attacker gets more guesses than `maxRetries` before lockout. | Partly — CONCURRENT shows two read/save pairs; fakes are stateless, so the lost update itself can only be inferred from source. |
| L2 | Duplicate | `saveAndFlush` | LOW | A double-published failure event counts one attempt twice (earlier lockout). No redelivery for in-process events. | Yes (DUPLICATE). HIGH would be an over-call. |
| L3 | Failure after write | `cacheManager.getCache` / `Cache.clear` | NONE/LOW | Eviction runs inside the transaction before commit; if it throws, the save rolls back with it — consistent. A finding here would be a false positive. | Yes. |

### NoteListener.onCreate / onUpdate / onDelete

Each method is one call: `noteWritePlatformService.createNote(request)` — thin listener (G9).

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| N1 | Duplicate | `noteWritePlatformService.createNote` | LOW | Double publish → duplicate note. | Yes (DUPLICATE). |
| N2 | **Class-level bug** | all three methods | HIGH (functional) | All three methods take `NoteCreateRequest`, and Spring dispatches by parameter type — so **every** `NoteCreateRequest` runs all three listeners → `createNote` ×3 per event. `onUpdate`/`onDelete` also *create* rather than update/delete. | **No** — analysis is per method; this needs cross-method reasoning (new gap G16). Recorded so a miss is not mistaken for a pass. |

### LegacyMeetingAttendanceListener.onUpdate (deprecated for removal)

Acts only when `command.getEntityName()` equals `"collectionsheet"` (path setup must supply that string),
then finds-or-creates a meeting, merges attendance, `meetingRepositoryWrapper.save(meeting)`.
`JpaSystemException`/`DataIntegrityViolationException` are caught and **rethrown** as platform exceptions.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| M1 | Concurrency — duplicate create | `meetingRepository.findByCalendarInstanceIdAndMeetingDate` → `meetingRepositoryWrapper.save` | MEDIUM | Two concurrent updates for a date with no meeting both create one; the unique constraint rejects the second, which surfaces as a "meeting already exists" error to that caller. Data stays consistent, the request fails. | Partly (CONCURRENT; stateless fakes). |
| M2 | Duplicate | `meetingRepositoryWrapper.save` | LOW | Re-running sets the same attendance values (idempotent merge). | Yes. HIGH would be an over-call. |
| M3 | Failure | `meetingRepositoryWrapper.save` | NONE | Exceptions are rethrown, not swallowed → the caller's transaction fails visibly. | Yes. |

Expected tool difficulty: Gson `JsonObject`/`JsonArray` are final classes (subclass fakes can't mock them) and
much logic is in helpers with static calls (`CalendarEntityType.isCenter`) — execution may not reach `save`.
