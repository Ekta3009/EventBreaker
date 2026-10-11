"""Token usage + estimated cost for every Nemotron call.

Appends one row per call to ~/.eventbreaker/usage.csv and keeps per-process
totals so the CLI can print what a run cost. Prices are Nebius Token Factory
list prices (USD per 1M tokens) — estimates; the provider's bill is authoritative.
"""
import csv
from datetime import datetime
from pathlib import Path

USAGE_FILE = Path.home() / ".eventbreaker" / "usage.csv"

_PRICES = {  # model -> (input, output) USD per 1M tokens
    "nvidia/Nemotron-3-Ultra-550b-a55b": (1.00, 3.00),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
}

session = {"calls": 0, "input": 0, "output": 0, "cost": 0.0}


def record(model: str, usage, purpose: str) -> None:
    """Log one completion's usage. Never raises — accounting must not break a run."""
    try:
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        price_in, price_out = _PRICES.get(model, (0.0, 0.0))
        cost = (tokens_in * price_in + tokens_out * price_out) / 1_000_000

        session["calls"] += 1
        session["input"] += tokens_in
        session["output"] += tokens_out
        session["cost"] += cost

        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not USAGE_FILE.exists()
        with USAGE_FILE.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "model", "purpose", "input_tokens", "output_tokens", "est_cost_usd"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), model, purpose,
                        tokens_in, tokens_out, f"{cost:.6f}"])
    except Exception:
        pass


def total_logged() -> float:
    """Estimated cost of everything in the usage file."""
    try:
        with USAGE_FILE.open() as f:
            return sum(float(r["est_cost_usd"]) for r in csv.DictReader(f))
    except Exception:
        return 0.0
