from enum import Enum
from pydantic import BaseModel


class ScenarioType(str, Enum):
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    DOWNSTREAM_FAILURE = "DOWNSTREAM_FAILURE"
    CRASH_AFTER_SIDE_EFFECT = "CRASH_AFTER_SIDE_EFFECT"


class ActionType(str, Enum):
    DELIVER_EVENT = "DELIVER_EVENT"
    INJECT_FAILURE = "INJECT_FAILURE"
    INJECT_DELAY = "INJECT_DELAY"
    CRASH_AFTER = "CRASH_AFTER"


class ScenarioAction(BaseModel):
    type: ActionType
    target: str | None = None


class ChaosScenario(BaseModel):
    scenarioType: ScenarioType
    reason: str
    targetDependency: str | None = None
    targetMethod: str | None = None
    actions: list[ScenarioAction]
    expectedConcern: str
