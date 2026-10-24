"""Controlled comparisons: one task, one model, several conditions, repeated runs.

A comparison is either

* ``solver_comparison``: conditions differ only in the ``solver`` parameter. Solvers bring
  their own prompts, tools and output handling, so this compares whole agents, not a single
  mechanism;
* ``ablation``: the first condition is the baseline and every other condition changes exactly
  one parameter of it.

Every run is an ordinary run directory, so ``harness show``, ``diff`` and ``rescore`` work on
it. ``build_report`` reads them back offline.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from harness.analysis import environment
from harness.analysis.diff import diff_manifests
from harness.analysis.stats import bootstrap_ci, paired_difference
from harness.core.cache import DiskCache
from harness.core.io import read_json, read_results, write_json
from harness.core.model import Model
from harness.core.runner import run_eval
from harness.core.task import Task, get_task
from harness.core.types import GenerateConfig, Outcome, SampleResult
from harness.sandbox.base import SandboxProvider

Kind = Literal["solver_comparison", "ablation"]

NOTES = {
    "solver_comparison": (
        "Conditions differ only in the solver. Each solver has its own prompts, tools and "
        "output handling, so differences belong to the whole agent, not to one mechanism."
    ),
    "ablation": "Each condition changes exactly one parameter of the baseline (first) condition.",
}

# Parameters that limit how much work a solver may do on one sample. An ablation that changes
# one of them measures sensitivity to the budget, not the effect of a mechanism.
BUDGET_KEYS = ("max_steps", "time_limit", "token_limit", "command_timeout")

EXPERIMENT_NOTES = {
    "solver_comparison": NOTES["solver_comparison"],
    "budget_sensitivity": (
        "Conditions change a budget, not the agent: differences show how the result depends "
        "on the budget and say nothing about which mechanism is better."
    ),
    "mechanism_ablation": (
        "Conditions change one mechanism of the agent under the same budgets, so differences "
        "can be attributed to that mechanism (within the statistical uncertainty)."
    ),
    "mixed": "Some conditions change a budget and others a mechanism; read them separately.",
}


class Condition(BaseModel):
    label: str
    params: dict[str, Any] = Field(default_factory=dict)


class CompareSpec(BaseModel):
    kind: Kind
    model: str
    base_url: str | None = None
    task: str
    # Shared by every condition; a condition's params override them.
    params: dict[str, Any] = Field(default_factory=dict)
    conditions: list[Condition]
    repeats: int = Field(default=3, ge=1)
    generate: GenerateConfig = Field(default_factory=GenerateConfig)
    concurrency: int = 2
    retries: int = 3
    cache: bool = True
    # Repeat k runs as epoch k, so repeats never share cached outputs, while resuming an
    # interrupted comparison reuses what was already generated. A comparison started without
    # a namespace gets its own (its directory name), so it does not replay an earlier one.
    cache_policy: Literal["auto", "always", "never"] = "always"
    cache_namespace: str | None = None
    cache_dir: Path = Path(".cache/requests")
    output_dir: Path = Path("runs")

    @classmethod
    def from_yaml(cls, path: Path) -> CompareSpec:
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))

    @model_validator(mode="after")
    def _check(self) -> CompareSpec:
        labels = [c.label for c in self.conditions]
        if len(labels) < 2:
            raise ValueError("a comparison needs at least two conditions")
        if len(set(labels)) != len(labels):
            raise ValueError(f"condition labels must be unique: {labels}")
        if self.kind == "solver_comparison":
            for c in self.conditions:
                if set(c.params) != {"solver"}:
                    raise ValueError(
                        f"condition {c.label!r}: a solver comparison may only set 'solver', "
                        f"got {sorted(c.params)}"
                    )
            solvers = [c.params["solver"] for c in self.conditions]
            if len(set(solvers)) != len(solvers):
                raise ValueError(f"every condition needs a different solver: {solvers}")
        else:
            base = self.conditions[0]
            if base.params:
                raise ValueError("the baseline (first) condition must not override params")
            for c in self.conditions[1:]:
                changed = [k for k, v in c.params.items() if self.params.get(k, _MISSING) != v]
                if len(changed) != 1:
                    raise ValueError(
                        f"condition {c.label!r} must change exactly one parameter, "
                        f"changes {sorted(changed)}"
                    )
                if "solver" in changed:
                    raise ValueError(
                        "changing the solver is not a single-variable ablation; "
                        "use kind: solver_comparison"
                    )
        return self

    def params_for(self, condition: Condition) -> dict[str, Any]:
        return {**self.params, **condition.params}


_MISSING = object()

TaskFactory = Callable[..., Task]


async def run_comparison(
    spec: CompareSpec,
    model: Model,
    *,
    sandbox_provider: SandboxProvider | None = None,
    make_task: TaskFactory = get_task,
    progress: Any | None = None,
) -> Path:
    """Run every condition ``spec.repeats`` times, interleaving conditions within a repeat so
    that drift (server load, cache warmth of images) affects them alike."""
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    compare_dir = Path(spec.output_dir) / f"compare-{stamp}-{spec.task}"
    i = 1
    while compare_dir.exists():
        i += 1
        compare_dir = compare_dir.with_name(f"compare-{stamp}-{spec.task}-{i}")
    compare_dir.mkdir(parents=True)
    if spec.cache_namespace is None:
        spec = spec.model_copy(update={"cache_namespace": compare_dir.name})
    index: dict[str, Any] = {"spec": spec.model_dump(mode="json"), "runs": []}
    write_json(compare_dir / "index.json", index)
    return await _run_missing(
        compare_dir,
        index,
        model,
        sandbox_provider=sandbox_provider,
        make_task=make_task,
        progress=progress,
    )


async def resume_comparison(
    compare_dir: Path,
    model: Model,
    *,
    sandbox_provider: SandboxProvider | None = None,
    make_task: TaskFactory = get_task,
    progress: Any | None = None,
) -> Path:
    """Finish an interrupted comparison with its recorded spec.

    Runs that completed are kept. Missing or unfinished runs are run again in the same cache
    namespace and epoch, so with caching on, samples generated before the interruption are
    replayed instead of being sampled anew. Replaced runs are listed under ``superseded``.
    """
    compare_dir = Path(compare_dir)
    index = read_json(compare_dir / "index.json")
    kept = []
    for entry in index["runs"]:
        manifest = read_json(compare_dir / entry["run_dir"] / "manifest.json")
        (
            kept if manifest.get("status") == "completed" else index.setdefault("superseded", [])
        ).append(entry)
    index["runs"] = kept
    index.setdefault("resumed_at", []).append(datetime.now(UTC).isoformat())
    write_json(compare_dir / "index.json", index)
    return await _run_missing(
        compare_dir,
        index,
        model,
        sandbox_provider=sandbox_provider,
        make_task=make_task,
        progress=progress,
        resumed=True,
    )


async def _run_missing(
    compare_dir: Path,
    index: dict[str, Any],
    model: Model,
    *,
    sandbox_provider: SandboxProvider | None,
    make_task: TaskFactory,
    progress: Any | None,
    resumed: bool = False,
) -> Path:
    spec = CompareSpec.model_validate(index["spec"])
    done = {(e["label"], e["repeat"]) for e in index["runs"]}
    cache = DiskCache(spec.cache_dir) if spec.cache else None
    for repeat in range(1, spec.repeats + 1):
        for condition in spec.conditions:
            if (condition.label, repeat) in done:
                continue
            task = make_task(spec.task, **spec.params_for(condition))
            run_dir = await run_eval(
                model,
                [(task, spec.generate)],
                output_dir=compare_dir / condition.label,
                cache=cache,
                concurrency=spec.concurrency,
                retries=spec.retries,
                run_config={
                    **spec.model_dump(mode="json", exclude={"conditions"}),
                    "params": spec.params_for(condition),
                    "compare": {"label": condition.label, "repeat": repeat},
                },
                progress=progress,
                sandbox_provider=sandbox_provider,
                cache_policy=spec.cache_policy,
                first_epoch=repeat,
                namespace=condition_namespace(spec, condition),
            )
            index["runs"].append(
                {
                    "label": condition.label,
                    "repeat": repeat,
                    "run_dir": str(run_dir.relative_to(compare_dir)),
                    "resumed": resumed,
                }
            )
            write_json(compare_dir / "index.json", index)

    write_json(compare_dir / "compare.json", build_report(compare_dir))
    return compare_dir


def condition_namespace(spec: CompareSpec, condition: Condition) -> str | None:
    """Cache namespace of one condition. Conditions never share cached responses, even when
    their requests are identical (e.g. an ablation of max_steps leaves the first prompt
    unchanged); the epoch keeps repeats apart and lets a resumed run replay its own calls."""
    if spec.cache_namespace is None:
        return None
    return f"{spec.cache_namespace}/{condition.label}"


def cache_state(fresh: int, cached: int) -> str:
    if fresh + cached == 0:
        return "unrecorded"
    if cached == 0:
        return "fresh"
    return "cached" if fresh == 0 else "mixed"


def _solved(r: SampleResult) -> bool:
    return r.outcome == Outcome.CORRECT


def _run_summary(results: list[SampleResult]) -> dict[str, Any]:
    n = len(results)
    usages = [r.usage() for r in results]
    walls = [r.wall_time_s for r in results if r.wall_time_s is not None]
    return {
        "n": n,
        "accuracy": sum(r.score.value if r.score else 0.0 for r in results) / n if n else 0.0,
        "solved": sum(_solved(r) for r in results),
        "input_tokens": sum(u.input_tokens for u in usages),
        "output_tokens": sum(u.output_tokens for u in usages),
        "wall_time_s": sum(walls) if walls else None,
        "outcomes": dict(Counter(r.outcome.value for r in results if r.outcome)),
        "finish_reasons": dict(
            Counter((r.output.finish_reason or "none") if r.output else "none" for r in results)
        ),
        "mean_steps": sum(len(r.steps) for r in results) / n if n else 0.0,
        "steps_without_tool_calls": sum(1 for r in results for s in r.steps if not s.tool_calls),
        "cached_outputs": sum(1 for r in results if r.output and r.output.cached),
        "fresh_calls": sum(r.calls.fresh for r in results),
        "cached_calls": sum(r.calls.cached for r in results),
        "fresh_input_tokens": sum(r.calls.fresh_usage.input_tokens for r in results),
        "fresh_output_tokens": sum(r.calls.fresh_usage.output_tokens for r in results),
        "cache_state": cache_state(
            sum(r.calls.fresh for r in results), sum(r.calls.cached for r in results)
        ),
        "served_models": sorted({m for r in results for m in r.served_models}),
        "cached_served_models": sorted({m for r in results for m in r.cached_served_models}),
    }


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _std(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    m = sum(values) / len(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def _relevant_changes(a: dict[str, Any], b: dict[str, Any]) -> list[list[Any]]:
    return [
        [key, va, vb]
        for key, va, vb in diff_manifests(a, b)
        # repeat k is epoch k by design
        if not key.startswith("run_config.compare.") and not key.endswith(".epochs")
    ]


def budgets(manifest: dict[str, Any], task_name: str) -> dict[str, Any]:
    """The budgets a run actually used, read from its manifest."""
    entry = next((t for t in manifest.get("tasks", []) if t.get("name") == task_name), {})
    params = entry.get("params") or {}
    return {
        "max_steps": params.get("max_steps"),
        "command_timeout": params.get("command_timeout"),
        "time_limit": entry.get("time_limit"),
        "token_limit": entry.get("token_limit"),
    }


def experiment_type(spec: CompareSpec, condition: Condition) -> str:
    if spec.kind == "solver_comparison":
        return "solver_comparison"
    changed = [k for k, v in condition.params.items() if spec.params.get(k, _MISSING) != v]
    return "budget_sensitivity" if set(changed) & set(BUDGET_KEYS) else "mechanism_ablation"


def environment_drift(runs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Per sample, every sandbox identity field (image id, digests, platform, limits) that is
    not the same in all runs. Samples without a recorded sandbox are left out."""
    seen: dict[str, dict[str, dict[str, Any]]] = {}
    for label, label_runs in runs.items():
        for run in label_runs:
            for r in run["results"]:
                if r.sandbox is None:
                    continue
                fields = seen.setdefault(r.sample.id, {})
                for key, value in environment.sample_identity(r).items():
                    fields.setdefault(key, {})[f"{label}#{run['repeat']}"] = value
    drift = []
    for sample_id, fields in sorted(seen.items()):
        for key, values in sorted(fields.items()):
            if len({json.dumps(v, sort_keys=True, default=str) for v in values.values()}) > 1:
                drift.append({"sample_id": sample_id, "field": key, "values": values})
    return drift


def build_report(compare_dir: Path) -> dict[str, Any]:
    """Aggregate a comparison directory; reads only saved results, never calls the model."""
    compare_dir = Path(compare_dir)
    index = read_json(compare_dir / "index.json")
    spec = CompareSpec.model_validate(index["spec"])
    task_name = spec.task

    runs: dict[str, list[dict[str, Any]]] = {c.label: [] for c in spec.conditions}
    for entry in index["runs"]:
        run_dir = compare_dir / entry["run_dir"]
        manifest = read_json(run_dir / "manifest.json")
        samples_path = run_dir / task_name / "samples.jsonl"
        results = read_results(samples_path) if samples_path.exists() else []
        runs[entry["label"]].append(
            {
                "repeat": entry["repeat"],
                "run_dir": entry["run_dir"],
                "status": manifest.get("status"),
                "resumed": entry.get("resumed", False),
                "manifest": manifest,
                "results": results,
            }
        )

    sample_sets = {
        f"{label}#{run['repeat']}": tuple(sorted(r.sample.id for r in run["results"]))
        for label, label_runs in runs.items()
        for run in label_runs
    }
    sample_ids = sorted({i for ids in sample_sets.values() for i in ids})

    conditions: dict[str, Any] = {}
    per_sample: dict[str, dict[str, str]] = {i: {} for i in sample_ids}
    # Per condition: fraction of its runs that solved each sample.
    rates: dict[str, dict[str, float]] = {}
    for condition_index, condition in enumerate(spec.conditions):
        label_runs = sorted(runs[condition.label], key=lambda r: r["repeat"])
        summaries = [_run_summary(r["results"]) for r in label_runs]
        totals: Counter[str] = Counter()
        finish: Counter[str] = Counter()
        for s in summaries:
            totals.update(s["outcomes"])
            finish.update(s["finish_reasons"])
        accuracies = [s["accuracy"] for s in summaries]
        walls = [s["wall_time_s"] for s in summaries if s["wall_time_s"] is not None]
        first = label_runs[0]["manifest"] if label_runs else {}
        conditions[condition.label] = {
            "params": spec.params_for(condition),
            "experiment_type": experiment_type(spec, condition) if condition_index else None,
            "budgets": budgets(first, task_name) if first else None,
            "runs": [
                {
                    "repeat": r["repeat"],
                    "run_dir": r["run_dir"],
                    "status": r["status"],
                    "resumed": r["resumed"],
                    **s,
                }
                for r, s in zip(label_runs, summaries, strict=True)
            ],
            "accuracy": {
                "mean": _mean(accuracies),
                "min": min(accuracies) if accuracies else None,
                "max": max(accuracies) if accuracies else None,
                "std_over_repeats": _std(accuracies),
            },
            "mean_input_tokens": _mean([s["input_tokens"] for s in summaries]),
            "mean_output_tokens": _mean([s["output_tokens"] for s in summaries]),
            "mean_wall_time_s": _mean(walls),
            "mean_steps": _mean([s["mean_steps"] for s in summaries]),
            "steps_without_tool_calls": sum(s["steps_without_tool_calls"] for s in summaries),
            "outcomes": {o.value: totals.get(o.value, 0) for o in Outcome},
            "finish_reasons": dict(finish.most_common()),
            "cached_outputs": sum(s["cached_outputs"] for s in summaries),
            "fresh_calls": sum(s["fresh_calls"] for s in summaries),
            "cached_calls": sum(s["cached_calls"] for s in summaries),
            "mean_fresh_input_tokens": _mean([s["fresh_input_tokens"] for s in summaries]),
            "mean_fresh_output_tokens": _mean([s["fresh_output_tokens"] for s in summaries]),
            "cache_states": [s["cache_state"] for s in summaries],
            "served_models": sorted({m for s in summaries for m in s["served_models"]}),
            "cached_served_models": sorted(
                {m for s in summaries for m in s["cached_served_models"]}
            ),
            # Repeats must differ only in randomness: any configuration change is listed here.
            "repeat_config_changes": [
                {"repeat": r["repeat"], "changes": _relevant_changes(first, r["manifest"])}
                for r in label_runs[1:]
                if _relevant_changes(first, r["manifest"])
            ],
        }
        rates[condition.label] = {}
        for i in sample_ids:
            present = [r for r in label_runs if any(x.sample.id == i for x in r["results"])]
            solved = sum(any(_solved(x) for x in r["results"] if x.sample.id == i) for r in present)
            per_sample[i][condition.label] = f"{solved}/{len(present)}"
            if present:
                rates[condition.label][i] = solved / len(present)
        lo, hi = bootstrap_ci(list(rates[condition.label].values()))
        conditions[condition.label]["accuracy"]["ci95_over_samples"] = [lo, hi]

    drift = environment_drift(runs)
    cache_states = {
        f"{label}#{run['repeat']}": state
        for label, c in conditions.items()
        for run, state in zip(c["runs"], c["cache_states"], strict=True)
    }
    all_fresh = all(state == "fresh" for state in cache_states.values())
    repeat_changes = any(c["repeat_config_changes"] for c in conditions.values())
    complete = all(
        len(runs[c.label]) == spec.repeats
        and all(r["status"] == "completed" for r in runs[c.label])
        for c in spec.conditions
    )
    same_samples = len(set(sample_sets.values())) <= 1
    scores_comparable = complete and same_samples and not repeat_changes and not drift

    baseline = spec.conditions[0].label
    paired = {
        c.label: paired_difference(rates[baseline], rates[c.label]) for c in spec.conditions[1:]
    }
    base_manifest = runs[baseline][0]["manifest"] if runs[baseline] else {}
    condition_changes = {
        c.label: _relevant_changes(base_manifest, runs[c.label][0]["manifest"])
        for c in spec.conditions[1:]
        if runs[c.label] and base_manifest
    }

    types = {c["experiment_type"] for c in conditions.values() if c["experiment_type"]}
    kind = types.pop() if len(types) == 1 else "mixed"
    recorded = [c["budgets"] for c in conditions.values() if c["budgets"] is not None]
    equal_budget = len(recorded) == len(conditions) and all(b == recorded[0] for b in recorded)

    return {
        "kind": spec.kind,
        "note": NOTES[spec.kind],
        "experiment_type": kind,
        "experiment_note": EXPERIMENT_NOTES[kind],
        # Every condition ran with the same max_steps, time, token and command limits. A solver
        # comparison or mechanism ablation without equal budgets is confounded by them.
        "equal_budget": equal_budget,
        "task": task_name,
        "model": spec.model,
        "generate": spec.generate.model_dump(),
        "cache_policy": spec.cache_policy if spec.cache else "disabled",
        "repeats": spec.repeats,
        "baseline": baseline,
        # Each check is reported on its own.
        "complete": complete,
        "same_samples_in_every_run": same_samples,
        "repeat_config_changes": repeat_changes,
        "environment_drift": drift,
        # Scores may be compared when the runs are complete and set up alike, even if some
        # responses were replayed from the cache (e.g. after resuming).
        "scores_comparable": scores_comparable,
        # Wall time and tokens spent describe inference only when every model call was made
        # in that run; replayed responses take no inference time and cost nothing.
        "performance_comparable": scores_comparable and all_fresh,
        "cache_states": cache_states,
        "samples": sample_ids,
        "conditions": conditions,
        "per_sample_solved": per_sample,
        # Per-sample solve rates of each condition against the baseline on the same samples.
        "paired_vs_baseline": paired,
        # Manifest fields that differ from the baseline's first run: the intended change plus
        # anything else that differs, which would confound the comparison.
        "config_changes_vs_baseline": condition_changes,
    }
