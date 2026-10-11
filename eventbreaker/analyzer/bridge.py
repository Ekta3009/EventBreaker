import json
import subprocess
from pathlib import Path

from pydantic import BaseModel

from eventbreaker.models.consumer import ConsumerAnalysis

# Resolve JAR path relative to the repo root (two levels up from this file)
_REPO_ROOT = Path(__file__).parent.parent.parent
_JAR_PATH = _REPO_ROOT / "target" / "eventbreaker-analyzer.jar"


def analyze(consumer_file: Path, method: str | None = None) -> ConsumerAnalysis:
    """
    Calls the Java analyzer JAR on the given consumer file.
    method selects a listener method by name; default is the first one found.
    Returns a parsed ConsumerAnalysis or raises on failure.
    """
    if not _JAR_PATH.exists():
        raise FileNotFoundError(
            f"Analyzer JAR not found at {_JAR_PATH}. "
            "Run 'mvn package' in the repo root first."
        )

    result = subprocess.run(
        ["java", "-jar", str(_JAR_PATH), str(consumer_file), *([method] if method else [])],
        capture_output=True,
        text=True,
    )

    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError(
            f"Analyzer produced no output.\nstderr: {result.stderr.strip()}"
        )

    data = json.loads(raw)

    if "error" in data:
        raise ValueError(f"Analyzer error: {data['error']}")

    return ConsumerAnalysis.model_validate(data)


class ListenerInfo(BaseModel):
    file: str
    className: str
    methodName: str
    eventType: str


def scan(root: Path) -> list[ListenerInfo]:
    """Every listener method under root (test sources skipped), via the Java analyzer."""
    if not _JAR_PATH.exists():
        raise FileNotFoundError(
            f"Analyzer JAR not found at {_JAR_PATH}. Run 'mvn package' in the repo root first."
        )
    result = subprocess.run(
        ["java", "-jar", str(_JAR_PATH), "--scan", str(root)],
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout.strip() or '{"error": "no output"}')
    if "error" in data:
        raise ValueError(f"Analyzer error: {data['error']}")
    return [ListenerInfo.model_validate(item) for item in data["listeners"]]
