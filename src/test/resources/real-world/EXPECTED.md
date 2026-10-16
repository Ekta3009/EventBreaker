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

---

## STEP 14 — killbill, new listeners (`~/killbill-killbill-0.25.4`, release 0.25.4)

Written 2026-10-16 from source only, **before any EventBreaker run on these listeners**. These are the
generalization test: the prompts and rubric were never tuned on them. Besides each listener file, the
callee was read where needed to judge severity (`InvoiceDispatcher`, `InvoicePaymentControlPluginApi`);
noted per row. Kill Bill's internal bus is persistent and at-least-once: a handler that throws is
retried, a handler that returns counts as done.

### InvoiceListener (6 `@Subscribe` methods) — known tool limit

Every `@Subscribe` method is two lines: `busDispatcherOptimizer.shouldDispatch(event)` →
`retryableSubscriber.handleEvent(event)`. The real work is in anonymous `SubscriberAction`s registered in
the constructor on `subscriberQueueHandler`, and `retryableSubscriber` is built by the constructor itself.
The executor injects a fake into every field (`retryableSubscriber` included), so **the handler logic never
runs under the tool**. Recorded so the misses are counted as tool scope, not analysis quality (new gap
G17: listener delegates to a dispatcher object the class builds itself).

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| I1 | Duplicate → double billing | `dispatcher.processParentInvoiceForInvoiceGeneration` (via `handleChildrenInvoiceCreationEvent`) | HIGH | Read in `InvoiceDispatcher`: for an existing DRAFT parent invoice the child's amount is **added** to the parent item (`childInvoiceAmount.add(item.getAmount())`) with no check that this child invoice was already counted. A redelivery — e.g. a retry after `createParentChildInvoiceRelation` failed — bills the child amount to the parent twice. | **No** (G17). |
| I2 | Swallowed failure | `dispatcher.processSubscriptionForInvoiceGeneration` / `processAccountFromNotificationOrBusEvent` / `processParentInvoiceFor*` | MEDIUM | `InvoiceApiException` (and `AccountApiException`) is logged and swallowed → no retry, the invoice for that transition is not generated now. MEDIUM, not HIGH: generation recomputes everything due up to the target date, so the next event or billing-date notification catches up — late, not lost. RuntimeExceptions are rethrown (retried, then the account is parked) — correct. | **No** (G17). |
| I3 | Concurrency | `dispatcher.*` | NONE | `@AllowConcurrentEvents`, but the dispatcher takes a per-account global lock (`ACCNT_INV_PAY`). A concurrency finding would be a false positive. | No (G17). |

What the tool *will* see: `shouldDispatch` + `handleEvent` per method. Any finding on `retryableSubscriber.handleEvent`
is unprovable from here (G17) — score it as "extra", not as I1/I2.

### BeatrixListener.handleAllInternalKillbillEvents

Translates every internal bus event to an external one: builds metadata JSON with `objectMapper`
(constructed in the constructor, `protected` field), then `externalBus.post(externalEvent)`.
`EventBusException` → rethrown as RuntimeException (retried); `JsonProcessingException` → logged, swallowed.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| B1 | Swallowed failure → external event lost | `objectMapper.writeValueAsString` | MEDIUM | Serialization failure is swallowed → `externalBus.post` never called, plugins/webhooks never hear about the event, no retry. MEDIUM: the failure is deterministic (a retry would fail the same way) and it is logged. | Partly — needs a checked-exception THROW (`JsonProcessingException`); a RuntimeException THROW propagates instead. Field is constructor-built (G3). |
| B2 | Duplicate delivery | `externalBus.post` | MEDIUM | A redelivered internal event posts the external event again → webhooks/plugins act twice. The external bus is documented at-least-once, so consumers should cope — MEDIUM, not HIGH. | Yes (DUPLICATE). |
| B3 | Failure | `externalBus.post` | NONE | Rethrown → internal bus retries. A finding here would be a false positive. | Yes (needs checked `EventBusException`; a RuntimeException THROW also propagates — same outcome). |
| B4 | Concurrency | — | NONE | Stateless translation, no read-then-write. A race finding would be a false positive. | Yes. |

### PushNotificationListener.triggerPushNotifications (`profiles/killbill`)

Looks up the tenant's callback URLs (`tenantApi.getTenantValuesForKey`), serializes once with `mapper`, then POSTs
to each URL (`httpClient` is constructor-built). A failed POST is not lost: `saveRetryPushNotificationInQueue`
schedules a retry on a notification queue.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| P1 | Swallowed failure → all webhooks lost | `tenantApi.getTenantValuesForKey` | HIGH | `TenantApiException` is caught and only logged → no callback for this event is sent and nothing is retried (the retry queue covers POST failures only). A transient tenant/DB error loses every webhook for the event. | Partly — needs a checked-exception THROW (`TenantApiException`). |
| P2 | Swallowed failure | `mapper.writeValueAsString` | MEDIUM | `JsonProcessingException` (an `IOException`) is caught → webhooks lost. Deterministic, logged → MEDIUM. | Partly — checked exception. |
| P3 | POST failure | `httpClient.send` | NONE | Caught and handed to the retry queue (`notificationQueue.recordFutureNotification`). **Negative control:** a "work silently lost" finding here is a false positive — the send failed but a retry was scheduled. | Yes (THROW on `send`); G3 (constructor-built `httpClient`). |
| P4 | Retry scheduling failure | `notificationQueueService.getNotificationQueue` | LOW | `NoSuchNotificationQueue`/`IOException` while scheduling the retry are logged → that one webhook is lost after an HTTP failure. Needs two failures in a row → LOW. | Partly — checked exception; needs a failing POST first. |
| P5 | Duplicate delivery | `httpClient.send` | MEDIUM | Redelivery POSTs every webhook again. Kill Bill documents push notifications as at-least-once → MEDIUM. | Yes (DUPLICATE). |

### PaymentTagHandler.process_AUTO_PAY_OFF_removal

When the `AUTO_PAY_OFF` tag is removed from an account, calls
`((InvoicePaymentControlPluginApi) invoicePaymentControlPlugin).process_AUTO_PAY_OFF_removal(accountId, ctx)`
(via a private helper). The plugin field is obtained in the constructor from an OSGi registry. Read in the plugin:
it schedules a payment retry for every stored AUTO_PAY_OFF entry, then deletes the entries.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| T1 | Duplicate delivery | `invoicePaymentControlPlugin.process_AUTO_PAY_OFF_removal` | LOW | After a complete first run the entries are deleted, so a redelivery finds nothing and schedules nothing. A HIGH (money side effect done twice) would be an over-call. | Yes (DUPLICATE) — if the cast works (see below). |
| T2 | Concurrency / partial failure → double payment retry | same | MEDIUM | Inside the plugin: two concurrent runs can both read the entries before either deletes them, and a failure mid-loop (entries not yet deleted, exception propagates → bus retry) reschedules retries already scheduled. Payment attempts are tied to `attemptId`, so a real double charge is unlikely → MEDIUM. | **No** — the logic is inside the callee (a fake under the tool). |
| T3 | Failure | same | NONE | Exceptions propagate → bus retries. A finding would be a false positive. | Yes. |

Possible tool gap: the constructor casts the registry's service to `InvoicePaymentControlPluginApi`; a fake typed
as the field's interface (`PaymentControlPluginApi`) may make the cast fail (G18 if it happens).

### KillbillEventHandler.handleSubscriptionEvents (`jaxrs`) — negative control

No injected dependencies: copies the in-memory list of waiting REST requests and calls `onBusEvent` on each.
Expected: **no findings.** Any HIGH/MEDIUM is a false positive.

---

## STEP 14 — Eclipse hawkbit / AmqpMessageHandlerService.onMessage (`~/hawkbit`, master `7d4cdcc98`, 1.1.0+181)

Written 2026-10-16 from source only, before any EventBreaker run. Second product, second broker (RabbitMQ).
`@RabbitListener` on the DMF receiver queue; dispatches by the `type` header to register / delete a device
(`THING_CREATED` / `THING_REMOVED`), handle a device event (`EVENT` → action status, attributes, auto-confirm), or
answer `PING`. The class swallows nothing: every exception propagates. What happens next is the container's
error handler (`RequeueExceptionStrategy`, read for this): exceptions of a fixed "fatal" list
(`EntityNotFoundException`, quota, constraint violations, conversion errors, `AmqpRejectAndDontRequeueException`…)
reject the message; **anything else requeues it**, so work done before the failure is done again on redelivery.

| # | Type | Dep.method | Severity | Reason | Detectable |
|---|---|---|---|---|---|
| H1 | Failure after a write → repeated on requeue | `controllerManagement.addUpdateActionStatus` then `controllerManagement.findActiveActionWithHighestWeight` / `amqpMessageDispatcherService.sendUpdateMessageToTarget` | MEDIUM | The status is saved (own transaction — the listener is not `@Transactional`), then the follow-up command to the device fails transiently → requeue → the same status row is added again and the device is commanded again. Duplicate action-history rows, no lost or wrong deployment. | Yes — THROW on the send/lookup; path setup must make `shouldTargetProceed` true (action inactive) and supply `type`=EVENT, `topic`=UPDATE_ACTION_STATUS. |
| H2 | Duplicate delivery of a status update | `controllerManagement.addUpdateActionStatus` | MEDIUM | RabbitMQ is at-least-once: a redelivered status message adds a second status entry and may re-send the next command to the device. Same effect as H1. | Yes (DUPLICATE). |
| H3 | Duplicate `THING_CREATED` | `controllerManagement.findOrRegisterTargetIfItDoesNotExist` / `updateControllerAttributes` / `sendUpdateMessageToTarget` | LOW | Find-or-register and attribute update are idempotent; the device gets its current command once more (devices already handle this on reconnect). A HIGH would be an over-call. | Yes (DUPLICATE). |
| H4 | Concurrent `THING_CREATED` for the same device | `controllerManagement.findOrRegisterTargetIfItDoesNotExist` | LOW | Two consumers both find no target; the second insert fails with `EntityAlreadyExistsException` → rejected, not requeued → that message's attribute update is dropped. The device re-sends attributes later. | Partly — CONCURRENT with stateless fakes cannot produce the unique-key failure. |
| H5 | Failure after register | `controllerManagement.updateControllerAttributes` (after `findOrRegister…`) | NONE | Requeue re-runs an idempotent find-or-register. **Negative control:** "listener threw after a side effect on a broker → HIGH" would be an over-call if find-or-register is labelled a side effect. | Yes. |
| H6 | Poison message requeued forever | any non-fatal exception that always recurs | MEDIUM | A message that fails deterministically with an exception outside the fatal list is requeued without limit (no retry cap in this class), re-running its side effects and occupying a consumer. Container configuration, not this class. | No — the requeue policy lives in the container config. |
| H7 | Delete / ping | `controllerManagement.deleteExistingTarget`, `amqpMessageDispatcherService.sendPingResponseToDmfReceiver` | NONE | Delete of a missing target → `EntityNotFoundException` → rejected (fatal list). Ping is a reply only. A finding would be a false positive. | Yes. |

Expected tool difficulty (new gap G19 if it blocks): before any dependency call the method checks
`SystemManagementHolder.getInstance().isTenantSoftDeleted()` inside `asSystemAsTenant(...)` — a static singleton plus a
static security helper, neither of which is an injected field. If it throws outside Spring, no scenario reaches the
handlers. Also: `Message` (Spring AMQP) carries the routing data in headers, so path setup must build it; `rabbitTemplate`
goes to the superclass constructor.
