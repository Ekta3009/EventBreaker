from pydantic import BaseModel

# Separates a build error's one-line summary (shown in the terminal) from the compiler output (kept in the report).
BUILD_DETAIL_MARKER = "\n\nCompiler errors:\n"


class ObservationEntry(BaseModel):
    target: str       # e.g., "paymentClient.charge"
    callCount: int
    threw: bool = False  # True if the method threw an exception when called


class ObservationResult(BaseModel):
    scenario: str
    status: str = "REPRODUCED"   # REPRODUCED | NOT_REACHED | ERROR
    observations: list[ObservationEntry] = []
    rawOutput: str | None = None
    executionError: str | None = None
    consumerThrew: bool = False
    callSequence: list[str] = []
    consumerException: str | None = None   # first exception the consumer threw, if any
    consumerExceptionChecked: bool = False # it was a checked exception (Spring commits instead of rolling back)
    notReached: list[str] = []             # targets the scenario was about but never called
    defaultAnswers: list[str] = []         # "path = value" a fake answered with no override (for path-setup retries)


class ReliabilityFinding(BaseModel):
    scenario: str                  # scenario type label from Nemotron
    summary: str                   # one-line: what was observed
    explanation: str               # why this is a production risk
    affectedMethod: str            # e.g., "paymentClient.charge"
    suggestedFix: str              # concrete fix advice referencing the actual code
    severity: str = "HIGH"        # HIGH | MEDIUM | LOW
    alsoSeenIn: list[str] = []     # other scenarios that reported this method (set by merge_findings)
    kind: str = "other"            # repeated | failure | other | unexplained — from how the scenario ran (set by diagnose)
    onFaultedCall: bool = False    # the scenario injected its fault on this very method
    callPattern: str = ""          # SINGLE | DUPLICATE | CONCURRENT of the scenario that produced it
    evidence: list[str] = []       # measured changes this finding explains — attached by code, not the LLM
    severityNote: str = ""         # why code adjusted the severity (e.g. in-process duplicate capped at LOW)
    reviewNote: str = ""           # what the reviewer (Nemotron Ultra) changed, if anything
