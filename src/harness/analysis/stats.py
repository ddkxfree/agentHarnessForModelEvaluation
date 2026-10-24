from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

from harness.core.types import Outcome, SampleResult


def bootstrap_ci(
    values: list[float], *, n_boot: int = 1000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    arr = np.asarray(values, dtype=float)
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, len(arr), size=(n_boot, len(arr)))].mean(axis=1)
    lo, hi = np.quantile(means, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def summarize(results: list[SampleResult]) -> dict[str, Any]:
    n = len(results)
    # API errors count as failures: dropping them would inflate accuracy
    values = [r.score.value if r.score else 0.0 for r in results]
    outcomes = Counter(r.outcome.value for r in results if r.outcome)
    methods = Counter((r.score.method or "none") for r in results if r.score)
    outputs = [r.output for r in results if r.output]
    fresh = [o.latency_s for o in outputs if not o.cached]
    lo, hi = bootstrap_ci(values)
    usages = [r.usage() for r in results]
    walls = [r.wall_time_s for r in results if r.wall_time_s is not None]

    agent: dict[str, Any] | None = None
    agent_results = [r for r in results if r.steps]
    if agent_results:
        exit_codes = [c for r in agent_results for s in r.steps for c in s.exit_codes]
        commands = [c for c in exit_codes if c is not None]
        agent = {
            "finish_reasons": dict(
                Counter(r.output.finish_reason for r in agent_results if r.output).most_common()
            ),
            "mean_steps": sum(len(r.steps) for r in agent_results) / len(agent_results),
            "commands": len(commands),
            "failed_commands": sum(c != 0 for c in commands),
        }

    return {
        "n": n,
        "accuracy": sum(values) / n if n else 0.0,
        "ci95": [lo, hi],
        "outcomes": {o.value: outcomes.get(o.value, 0) for o in Outcome},
        "rates": {o.value: (outcomes.get(o.value, 0) / n if n else 0.0) for o in Outcome},
        "extraction_methods": dict(methods.most_common()),
        "tokens": {
            "input": sum(u.input_tokens for u in usages),
            "output": sum(u.output_tokens for u in usages),
        },
        "wall_time_s": {
            "total": sum(walls) if walls else None,
            "mean": sum(walls) / len(walls) if walls else None,
        },
        "cached": sum(o.cached for o in outputs),
        "cleanup_errors": sum(r.cleanup_error is not None for r in results),
        # Model identities the server reported, counted per sample.
        "served_models": dict(Counter(m for r in results for m in r.served_models).most_common()),
        "cached_served_models": dict(
            Counter(m for r in results for m in r.cached_served_models).most_common()
        ),
        "mean_latency_s": sum(fresh) / len(fresh) if fresh else None,
        "agent": agent,
    }
