from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from harness.core.io import read_json, read_results
from harness.core.types import Outcome

VOLATILE_KEYS = {"run_id", "status", "started_at", "finished_at", "results"}


def flatten(obj: Any, prefix: str = "", items: set[str] | None = None) -> dict[str, Any]:
    """Flatten nested dicts to dotted keys; lists of named dicts are keyed by name.

    If ``items`` is given, the key prefix of every named list item is added to it.
    """
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}{k}.", items))
    elif isinstance(obj, list) and obj and all(isinstance(x, dict) and "name" in x for x in obj):
        for x in obj:
            item = f"{prefix}{x['name']}"
            if items is not None:
                items.add(item)
            out.update(flatten(x, f"{item}.", items))
    else:
        out[prefix.rstrip(".")] = obj
    return out


def _collapse(flat: dict[str, Any], item: str) -> None:
    for k in [k for k in flat if k.startswith(f"{item}.")]:
        del flat[k]
    flat[item] = "<present>"


def diff_manifests(a: dict[str, Any], b: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    items_a: set[str] = set()
    items_b: set[str] = set()
    fa = flatten({k: v for k, v in a.items() if k not in VOLATILE_KEYS}, items=items_a)
    fb = flatten({k: v for k, v in b.items() if k not in VOLATILE_KEYS}, items=items_b)
    # a task present in only one run is reported once rather than field by field
    for item in items_a - items_b:
        _collapse(fa, item)
    for item in items_b - items_a:
        _collapse(fb, item)
    keys = sorted(fa.keys() | fb.keys())
    return [(k, fa.get(k), fb.get(k)) for k in keys if fa.get(k) != fb.get(k)]


def _key(r: Any) -> str:
    return r.sample.id if r.epoch == 1 else f"{r.sample.id}#epoch={r.epoch}"


def diff_task(dir_a: Path, dir_b: Path, name: str) -> dict[str, Any]:
    from harness.analysis import environment
    from harness.analysis.stats import paired_difference

    ra = {_key(r): r for r in read_results(dir_a / name / "samples.jsonl")}
    rb = {_key(r): r for r in read_results(dir_b / name / "samples.jsonl")}
    common = [i for i in ra if i in rb]

    def correct(r: Any) -> bool:
        return r.outcome == Outcome.CORRECT

    regressions = [i for i in common if correct(ra[i]) and not correct(rb[i])]
    improvements = [i for i in common if not correct(ra[i]) and correct(rb[i])]
    transitions = Counter(
        f"{ra[i].outcome} -> {rb[i].outcome}" for i in common if ra[i].outcome != rb[i].outcome
    )
    same_text = sum(
        1
        for i in common
        if ra[i].output and rb[i].output and ra[i].output.text == rb[i].output.text
    )

    def acc(rs: dict[str, Any]) -> float:
        return sum(correct(rs[i]) for i in common) / len(common) if common else 0.0

    # Same output, different score: only the scorer (or the recorded target) can explain it.
    rescored = [
        i
        for i in common
        if ra[i].output
        and rb[i].output
        and ra[i].output.text == rb[i].output.text
        and (ra[i].score and ra[i].score.value) != (rb[i].score and rb[i].score.value)
    ]
    env_changed = [
        i
        for i in common
        if ra[i].sandbox
        and rb[i].sandbox
        and environment.sample_identity(ra[i]) != environment.sample_identity(rb[i])
    ]

    # Paired over samples: average each sample over its epochs first.
    def rates(rs: dict[str, Any]) -> dict[str, float]:
        by_id: dict[str, list[float]] = {}
        for i in common:
            by_id.setdefault(rs[i].sample.id, []).append(float(correct(rs[i])))
        return {k: sum(v) / len(v) for k, v in by_id.items()}

    return {
        "common_samples": len(common),
        "only_in_a": len(ra.keys() - rb.keys()),
        "only_in_b": len(rb.keys() - ra.keys()),
        "accuracy_a": acc(ra),
        "accuracy_b": acc(rb),
        "identical_outputs": same_text,
        "regressions": regressions,
        "improvements": improvements,
        "outcome_transitions": dict(transitions.most_common()),
        "same_output_different_score": rescored,
        "environment_changed": env_changed,
        "served_models_a": sorted({m for i in common for m in ra[i].served_models}),
        "served_models_b": sorted({m for i in common for m in rb[i].served_models}),
        "paired": paired_difference(rates(ra), rates(rb)),
    }


def diff_runs(dir_a: Path, dir_b: Path) -> dict[str, Any]:
    dir_a, dir_b = Path(dir_a), Path(dir_b)
    ma, mb = read_json(dir_a / "manifest.json"), read_json(dir_b / "manifest.json")
    names_a = [t["name"] for t in ma["tasks"]]
    names_b = {t["name"] for t in mb["tasks"]}
    tasks = {
        n: diff_task(dir_a, dir_b, n)
        for n in names_a
        if n in names_b
        and (dir_a / n / "samples.jsonl").exists()
        and (dir_b / n / "samples.jsonl").exists()
    }
    changes = diff_manifests(ma, mb)
    return {
        "a": ma["run_id"],
        "b": mb["run_id"],
        "manifest_changes": changes,
        "tasks": tasks,
        "likely_causes": likely_causes(changes, tasks),
    }


# Manifest key patterns, checked in order, and what a change there means.
CAUSES = [
    ("model.", "model or endpoint changed"),
    (".generate_config.", "generation settings changed"),
    (".solver.", "solver (prompt, tools or agent loop) changed"),
    (".scorer.", "scorer changed"),
    (".dataset.", "dataset or its revision changed"),
    (".sample_ids_sha256", "different samples"),
    (".n_samples", "different samples"),
    (".params.", "task parameters changed"),
    (".cache.", "cache policy changed (outputs may be replayed)"),
    (".epochs", "different epochs"),
    ("sandbox_provider.", "Docker host or daemon changed"),
    ("environment.", "harness code or Python packages changed"),
]


def likely_causes(changes: list[tuple[str, Any, Any]], tasks: dict[str, Any]) -> list[str]:
    """Plain-language hints for why two runs differ. They point where to look; they do not
    prove what caused a particular flip."""
    hints: list[str] = []
    seen: set[str] = set()
    for pattern, hint in CAUSES:
        keys = [k for k, _, _ in changes if pattern in f".{k}" or k.startswith(pattern)]
        if keys and hint not in seen:
            seen.add(hint)
            hints.append(f"{hint}: {', '.join(keys[:3])}" + (" ..." if len(keys) > 3 else ""))
    for name, t in tasks.items():
        if t["same_output_different_score"]:
            hints.append(
                f"{name}: {len(t['same_output_different_score'])} samples have identical "
                "outputs but different scores, so scoring changed"
            )
        if t["environment_changed"]:
            hints.append(
                f"{name}: actual sandbox environment differs for "
                f"{len(t['environment_changed'])} samples (image id, digest, platform or limits)"
            )
        if t["served_models_a"] != t["served_models_b"]:
            hints.append(
                f"{name}: server reported different models: "
                f"{t['served_models_a']} vs {t['served_models_b']}"
            )
    flips = any(t["regressions"] or t["improvements"] for t in tasks.values())
    if flips and not hints:
        hints.append(
            "no recorded configuration or environment difference: sampling or server-side "
            "nondeterminism, or a change the manifest does not capture"
        )
    return hints
