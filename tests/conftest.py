"""Shared fixtures for the offline test suite.

No test here calls Nemotron or runs Maven. Fixtures that need the static
analyzer JAR skip cleanly when the JAR or a JVM is missing.
"""
import shutil
from pathlib import Path

import pytest

from eventbreaker.analyzer import bridge
from eventbreaker.models.consumer import ConsumerAnalysis

RESOURCES = Path(__file__).parent.parent / "src" / "test" / "resources"
# Listener source files: real-world copies (Kill Bill, Fineract) + analyzer test inputs.
LISTENERS = {
    "OverdueListener": RESOURCES / "real-world" / "killbill" / "OverdueListener.java",
    "KafkaRemoteMessageListener": RESOURCES / "real-world" / "fineract" / "KafkaRemoteMessageListener.java",
    **{p.stem: p for p in (RESOURCES / "analyzer").glob("*.java")},
}

_cache: dict[tuple[str, str | None], ConsumerAnalysis] = {}


def listener_file(name: str) -> Path:
    return LISTENERS[name]


@pytest.fixture(scope="session")
def analyze():
    """Return a function (name, method=None) -> ConsumerAnalysis, using the real Java analyzer."""
    if not bridge._JAR_PATH.exists():
        pytest.skip("analyzer JAR not built (run 'mvn package')")
    if shutil.which("java") is None:
        pytest.skip("java not on PATH")

    def _analyze(name: str, method: str | None = None) -> ConsumerAnalysis:
        if (name, method) not in _cache:
            _cache[(name, method)] = bridge.analyze(listener_file(name), method)
        return _cache[(name, method)]

    return _analyze
