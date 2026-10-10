"""Shared fixtures for the offline test suite.

No test here calls Nemotron or runs Maven. Fixtures that need the static
analyzer JAR skip cleanly when the JAR or a JVM is missing.
"""
import shutil
from pathlib import Path

import pytest

from eventbreaker.analyzer import bridge
from eventbreaker.models.consumer import ConsumerAnalysis

CONSUMERS_DIR = Path(__file__).parent.parent / "src" / "test" / "resources" / "consumers"
ALL_CONSUMERS = sorted(p.stem for p in CONSUMERS_DIR.glob("*.java"))

_cache: dict[str, ConsumerAnalysis] = {}


def consumer_file(name: str) -> Path:
    return CONSUMERS_DIR / f"{name}.java"


@pytest.fixture(scope="session")
def analyze():
    """Return a function name -> ConsumerAnalysis, using the real Java analyzer."""
    if not bridge._JAR_PATH.exists():
        pytest.skip("analyzer JAR not built (run 'mvn package')")
    if shutil.which("java") is None:
        pytest.skip("java not on PATH")

    def _analyze(name: str) -> ConsumerAnalysis:
        if name not in _cache:
            _cache[name] = bridge.analyze(consumer_file(name))
        return _cache[name]

    return _analyze
