from eventbreaker.scenarios.models import (
    ActionType,
    ChaosScenario,
    ScenarioType,
)

_ALLOWED_SCENARIO_TYPES = set(ScenarioType)
_ALLOWED_ACTION_TYPES = set(ActionType)


def validate(scenarios: list[ChaosScenario]) -> list[ChaosScenario]:
    """
    Filters scenarios to only those with allowed types and actions.
    Strips any actions with disallowed types rather than rejecting the
    whole scenario.
    """
    valid = []

    for scenario in scenarios:
        if scenario.scenarioType not in _ALLOWED_SCENARIO_TYPES:
            continue

        allowed_actions = [
            a for a in scenario.actions
            if a.type in _ALLOWED_ACTION_TYPES
        ]
        if not allowed_actions:
            continue

        valid.append(scenario.model_copy(update={"actions": allowed_actions}))

    return valid
