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
