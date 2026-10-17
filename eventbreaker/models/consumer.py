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
