from eventbreaker.scenarios.models import ChaosScenario


def validate(scenarios: list[ChaosScenario]) -> list[ChaosScenario]:
    """Keep scenarios that have the minimum required fields.

    testConfig may be None here — nemotron.py will be updated in STEP 2
    to populate it. Until then, filter only on type and reason so the
    existing pipeline continues to work during the migration.
    """
    return [
        s for s in scenarios
        if s.scenarioType and s.reason
    ]
