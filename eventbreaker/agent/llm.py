"""One cached Nemotron chat call, shared by diagnosis and review.

Temperature 0 + a seed, and a cache keyed by the exact model + messages + seed: the
same evidence gets the same answer on every run (and costs nothing the second time).
Hosted models are still not fully deterministic at temperature 0 (server-side batching),
so callers that need stability vote over several samples asked at temperature > 0
(at temperature 0 every seed returns nearly the same answer, so votes would be copies).
`fresh=True` re-asks. Only answers the caller accepts (`valid`) are cached.
"""
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from openai import OpenAI

from eventbreaker.agent import usage

BASE_URL = "https://api.tokenfactory.nebius.com/v1/"
ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
SUPER = "nvidia/nemotron-3-super-120b-a12b"
_CACHE_DIR = Path.home() / ".eventbreaker" / "cache" / "llm"


def chat(
    model: str, messages: list[dict], purpose: str,
    valid: Callable[[str], bool] = lambda raw: True, fresh: bool = False, seed: int = 0, temperature: float = 0,
) -> str:
    """The model's reply text. Raises EnvironmentError without NEBIUS_API_KEY, ValueError on an empty reply.

    seed: passed to the API (best-effort reproducibility) and part of the cache key, so votes
    asked with different seeds are separate samples.
    temperature: 0 by default; voters pass > 0 so their samples can actually differ.
    """
    key_parts = [model, messages, seed] + ([temperature] if temperature else [])
    key = hashlib.sha256(json.dumps(key_parts, sort_keys=True).encode()).hexdigest()[:20]
    cache_file = _CACHE_DIR / f"{key}.txt"
    if not fresh and cache_file.exists():
        return cache_file.read_text()

    api_key = os.environ.get("NEBIUS_API_KEY")
    if not api_key:
        raise EnvironmentError("NEBIUS_API_KEY environment variable is not set.")
    response = OpenAI(base_url=BASE_URL, api_key=api_key).chat.completions.create(
        model=model, messages=messages, temperature=temperature, seed=seed, max_tokens=8192,
    )
    usage.record(model, getattr(response, "usage", None), purpose)
    message = response.choices[0].message
    # Reasoning mode can leave content empty and put the answer in reasoning_content.
    raw = message.content or getattr(message, "reasoning_content", None) or ""
    if not raw.strip():
        raise ValueError(f"Nemotron returned no content ({purpose}).")
    if valid(raw):
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(raw)
    return raw
