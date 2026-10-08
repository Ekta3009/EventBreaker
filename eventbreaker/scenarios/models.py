from pydantic import BaseModel


class FaultInjection(BaseModel):
    dep: str            # exact dependency field name from ConsumerAnalysis
    method: str         # exact method name called on that dep
    fault: str          # THROW | THROW_ONCE | DELAY
    delayMs: int = 0    # milliseconds — only used when fault=DELAY


class TestConfig(BaseModel):
    callPattern: str                          # SINGLE | DUPLICATE | CONCURRENT
    faultInjections: list[FaultInjection] = []
    observeOrdering: bool = False             # true if risk is about call order
    detectSwallowing: bool = False            # true if risk is about silent exception swallowing


class ChaosScenario(BaseModel):
    scenarioType: str
    reason: str
    targetDependency: str | None = None
    targetMethod: str | None = None
    expectedConcern: str
    testConfig: TestConfig | None = None      # populated by Nemotron Ultra; None = theoretical
