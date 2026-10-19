from __future__ import annotations
from pydantic import BaseModel

# Fields the analyzer JAR emits but nothing reads (assignments, variable names, raw initializers) are
# left out; Pydantic ignores unknown keys.


class MethodCallInfo(BaseModel):
    expression: str
    scope: str | None = None
    methodName: str
    arguments: list[str] = []
    nestedCalls: list[MethodCallInfo] = []


class ObjectCreationInfo(BaseModel):
    type: str
    expression: str
    arguments: list[str] = []
    nestedCalls: list[MethodCallInfo] = []


class DependencyInfo(BaseModel):
    name: str
    type: str


class VariableInitializationInfo(BaseModel):
    variableType: str
    initializerMethodCall: MethodCallInfo | None = None


class ConsumerAnalysis(BaseModel):
    className: str
    methodName: str
    eventType: str
    parameters: list[str] = []
    dependencies: list[DependencyInfo] = []
    methodCalls: list[MethodCallInfo] = []
    objectCreations: list[ObjectCreationInfo] = []
    variableInitializations: list[VariableInitializationInfo] = []
    otherListenerMethods: list[str] = []   # listener methods in the class not analysed this run
    listenerAnnotation: str = ""           # e.g. KafkaListener, EventListener, Subscribe
    # @Subscribe only, set from the classpath/source: "persistent" (Kill Bill's retrying bus)
    # or "guava" (plain Guava EventBus: no retry, one call at a time unless @AllowConcurrentEvents).
    subscribeBus: str = ""
    allowConcurrentEvents: bool = False
    # @Transactional on the listener method or class, set from the source: "unchecked" (rolls back on
    # RuntimeException), "any" (rollbackFor given), or "" (no transaction / no rollback assumed).
    transactionalRollback: str = ""
    # Spring listener whose exceptions never reach the publisher (set from the source): @Async, or a
    # @TransactionalEventListener in an after-commit/rollback/completion phase — a thrown exception is logged and dropped.
    detachedDelivery: bool = False
    # @KafkaListener only: "configured" when the project (or its own platform library) wires a Kafka error handler /
    # dead-letter topic, "default" when it does not — Spring Kafka then retries ~10 times, logs and skips the record.
    kafkaErrorHandling: str = ""
