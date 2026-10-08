from pydantic import BaseModel


class ObservationEntry(BaseModel):
    target: str       # e.g., "paymentClient.charge"
    callCount: int
    threw: bool = False  # True if the method threw an exception when called


class ObservationResult(BaseModel):
    scenario: str
    status: str = "REPRODUCED"   # REPRODUCED | NOT_REPRODUCED | ERROR
    observations: list[ObservationEntry] = []
    rawOutput: str | None = None
    executionError: str | None = None
    consumerThrew: bool = False
    callSequence: list[str] = []


class ReliabilityFinding(BaseModel):
    scenario: str                  # scenario type label from Nemotron
    summary: str                   # one-line: what was observed
    explanation: str               # why this is a production risk
    affectedMethod: str            # e.g., "paymentClient.charge"
    suggestedFix: str              # concrete fix advice referencing the actual code
    severity: str = "HIGH"        # HIGH | MEDIUM | LOW
