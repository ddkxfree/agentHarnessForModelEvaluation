from __future__ import annotations

import math
from collections import Counter, defaultdict
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


# Below this many samples, paired results are reported but no significance claims are made.
MIN_SAMPLES_FOR_CLAIMS = 30


def sign_test(wins: int, losses: int) -> float:
    """Exact two-sided sign test (McNemar's exact test for paired binary outcomes); ties are
    dropped."""
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def paired_difference(a: dict[Any, float], b: dict[Any, float], *, seed: int = 0) -> dict[str, Any]:
    """Compare per-item scores of B against A on the items both have.

    The confidence interval resamples items (paired bootstrap), so it reflects which items
    happened to be in the set; the sign test counts items where B is higher or lower.
    """
    common = sorted(a.keys() & b.keys(), key=str)
    diffs = [b[k] - a[k] for k in common]
    wins = sum(d > 0 for d in diffs)
    losses = sum(d < 0 for d in diffs)
    lo, hi = bootstrap_ci(diffs, seed=seed)
    return {
        "n": len(common),
        "mean_a": sum(a[k] for k in common) / len(common) if common else None,
        "mean_b": sum(b[k] for k in common) / len(common) if common else None,
        "mean_difference": sum(diffs) / len(diffs) if diffs else None,
        "ci95": [lo, hi],
        "b_better": wins,
        "b_worse": losses,
        "ties": len(diffs) - wins - losses,
        "sign_test_p": sign_test(wins, losses),
        "enough_samples_for_claims": len(common) >= MIN_SAMPLES_FOR_CLAIMS,
    }


def epoch_stats(results: list[SampleResult]) -> dict[str, Any] | None:
    """Accuracy per epoch and its spread; None for single-epoch runs."""
    by_epoch: dict[int, list[float]] = defaultdict(list)
    by_sample: dict[str, list[float]] = defaultdict(list)
    for r in results:
        value = r.score.value if r.score else 0.0
        by_epoch[r.epoch].append(value)
        by_sample[r.sample.id].append(value)
    if len(by_epoch) < 2:
        return None
    per_epoch = {e: sum(v) / len(v) for e, v in sorted(by_epoch.items())}
    accs = list(per_epoch.values())
    mean = sum(accs) / len(accs)
    std = math.sqrt(sum((x - mean) ** 2 for x in accs) / (len(accs) - 1))
    # Items, not epochs, are the unit of resampling: average each sample over its epochs.
    lo, hi = bootstrap_ci([sum(v) / len(v) for v in by_sample.values()])
    return {
        "n_epochs": len(accs),
        "accuracy_per_epoch": {str(e): a for e, a in per_epoch.items()},
        "mean": mean,
        "std": std,
        "ci95_over_samples": [lo, hi],
        # Samples solved in some epochs but not others: where repetition matters.
        "inconsistent_samples": sum(1 for v in by_sample.values() if 0 < sum(v) < len(v)),
    }


def summarize(results: list[SampleResult]) -> dict[str, Any]:
    n = len(results)
    # API errors count as failures: dropping them would inflate accuracy
    values = [r.score.value if r.score else 0.0 for r in results]
    outcomes = Counter(r.outcome.value for r in results if r.outcome)
    methods = Counter((r.score.method or "none") for r in results if r.score)
    outputs = [r.output for r in results if r.output]
    fresh = [o.latency_s for o in outputs if not o.cached]
    # Resample samples, not (sample, epoch) pairs: repeated epochs of one item are not
    # independent evidence.
    per_sample: dict[str, list[float]] = defaultdict(list)
    for r, v in zip(results, values, strict=True):
        per_sample[r.sample.id].append(v)
    lo, hi = bootstrap_ci([sum(v) / len(v) for v in per_sample.values()])
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
        "epochs": epoch_stats(results),
    }
