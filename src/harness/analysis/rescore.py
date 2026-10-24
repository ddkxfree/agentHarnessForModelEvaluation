from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.analysis.stats import summarize
from harness.core.io import read_json, read_results, write_json, write_results
from harness.core.manifest import now
from harness.core.scorer import check_score, requires_sandbox
from harness.core.task import get_task
from harness.core.types import ErrorKind, SampleResult, classify


def _scoring_state(r: SampleResult) -> dict[str, Any]:
    return {
        "score": r.score.model_dump() if r.score is not None else None,
        "error": r.error,
        "error_kind": r.error_kind,
        "outcome": r.outcome,
    }


def rescore_sample(r: SampleResult, scorer: Any, *, at: str, from_fingerprint: str | None) -> bool:
    """Re-applies ``scorer`` to one saved result; returns whether its scoring state changed.

    Only samples that reached scoring are touched: model, solver and environment failures keep
    their error, so a rescore can never turn them into successes. A previous scorer failure is
    cleared on success, and a new failure is recorded as ``scorer_error`` on that sample only.
    """
    if r.output is None or r.error_kind not in (None, ErrorKind.SCORER):
        return False
    before = _scoring_state(r)
    try:
        r.score = check_score(scorer(r.sample, r.output.text))
        r.error = r.error_kind = None
    except Exception as exc:
        r.score = None
        r.error, r.error_kind = f"{type(exc).__name__}: {exc}", ErrorKind.SCORER
    r.outcome = classify(r)
    if _scoring_state(r) == before:
        return False
    r.rescore_history.append({"at": at, "scorer_fingerprint": from_fingerprint, **before})
    return True


def rescore_run(run_dir: Path) -> dict[str, Any]:
    """Re-apply each task's current scorer to the saved raw outputs, without calling the model.

    Use this after changing extraction or scoring logic.
    """
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    changes: dict[str, Any] = {}

    for entry in manifest["tasks"]:
        name = entry["name"]
        samples_path = run_dir / name / "samples.jsonl"
        if not samples_path.exists():
            continue
        scorer = get_task(name, **entry["params"]).scorer
        if requires_sandbox(scorer):
            changes[name] = {"skipped": "scorer needs the sandbox, which no longer exists"}
            continue
        results = read_results(samples_path)

        at = now()
        old_fp = entry["scorer"].get("fingerprint")
        changed = sum(rescore_sample(r, scorer, at=at, from_fingerprint=old_fp) for r in results)
        scorer_errors = sum(r.error_kind == ErrorKind.SCORER for r in results)

        summary = summarize(results)
        write_results(samples_path, results)
        write_json(run_dir / name / "summary.json", summary)

        entry["scorer"] = scorer.describe()
        new_fp = entry["scorer"]["fingerprint"]
        if new_fp != old_fp:
            entry.setdefault("rescore_history", []).append(
                {"at": at, "from_fingerprint": old_fp, "to_fingerprint": new_fp}
            )
        before_acc = manifest["results"].get(name, {}).get("accuracy")
        manifest["results"][name] = summary
        changes[name] = {
            "accuracy_before": before_acc,
            "accuracy_after": summary["accuracy"],
            "samples_changed": changed,
            "scorer_errors": scorer_errors,
        }

    write_json(manifest_path, manifest)
    return changes
