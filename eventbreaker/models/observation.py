from pydantic import BaseModel


class ObservationEntry(BaseModel):
    target: str       # e.g., "paymentClient.charge"
    callCount: int


class ObservationResult(BaseModel):
    scenario: str
    status: str = "REPRODUCED"   # REPRODUCED | NOT_REPRODUCED | ERROR
    observations: list[ObservationEntry] = []
    rawOutput: str | None = None
    executionError: str | None = None


class ReliabilityFinding(BaseModel):
    scenario: str                  # e.g., "DUPLICATE_EVENT"
    summary: str                   # one-line: what was observed
    explanation: str               # why this is a production risk
    affectedMethod: str            # e.g., "paymentClient.charge"
    suggestedFix: str              # concrete fix advice referencing the actual code
    severity: str = "HIGH"        # HIGH | MEDIUM | LOW
