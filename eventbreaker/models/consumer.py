from __future__ import annotations
from pydantic import BaseModel


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


class AssignmentInfo(BaseModel):
    target: str
    value: str
    expression: str


class VariableInitializationInfo(BaseModel):
    variableName: str
    variableType: str
    initializer: str
    initializerMethodCall: MethodCallInfo | None = None
    initializerObjectCreation: ObjectCreationInfo | None = None


class ConsumerAnalysis(BaseModel):
    className: str
    methodName: str
    eventType: str
    parameters: list[str] = []
    dependencies: list[DependencyInfo] = []
    methodCalls: list[MethodCallInfo] = []
    objectCreations: list[ObjectCreationInfo] = []
    assignments: list[AssignmentInfo] = []
    variableInitializations: list[VariableInitializationInfo] = []
