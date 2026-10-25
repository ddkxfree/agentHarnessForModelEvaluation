from __future__ import annotations

from pathlib import Path
from typing import Any

from harness.analysis.stats import summarize
from harness.core.io import read_json, read_results, write_json, write_results
from harness.core.judge import JudgeError, requires_judges
from harness.core.manifest import now
from harness.core.scorer import check_score, grades_in_fresh_sandbox, requires_sandbox
from harness.core.task import get_task
from harness.core.types import ErrorKind, SampleResult, Score, classify
from harness.judge.common import NeedsNewJudgement

NEEDS_NEW_JUDGEMENT = (
    "the judges' inputs changed (rubric, prompt, judge model or settings); re-parsing the saved "
    "replies would not judge the new setup, so this needs new judge calls (run again)"
)


def _scoring_state(r: SampleResult) -> dict[str, Any]:
    return {
        "score": r.score.model_dump() if r.score is not None else None,
        "error": r.error,
        "error_kind": r.error_kind,
        "outcome": r.outcome,
    }


def reached_scoring(r: SampleResult) -> bool:
    """Only these may be rescored: model, solver and environment failures keep their error,
    so a rescore can never turn them into successes."""
    return r.output is not None and r.error_kind in (None, ErrorKind.SCORER, ErrorKind.JUDGE)


def apply_rescore(
    r: SampleResult, new: Score | Exception, *, at: str, from_fingerprint: str | None
) -> bool:
    """Replace the scoring state of ``r`` with a new score or scorer failure.

    A previous scorer failure is cleared on success; the replaced state goes to the sample's
    ``rescore_history``. Returns whether anything changed.
    """
    before = _scoring_state(r)
    if isinstance(new, Exception):
        r.score = None
        kind = ErrorKind.JUDGE if isinstance(new, JudgeError) else ErrorKind.SCORER
        r.error, r.error_kind = f"{type(new).__name__}: {new}", kind
    else:
        r.score = new
        r.error = r.error_kind = None
    r.outcome = classify(r)
    if _scoring_state(r) == before:
        return False
    r.rescore_history.append({"at": at, "scorer_fingerprint": from_fingerprint, **before})
    return True


def rescore_sample(r: SampleResult, scorer: Any, *, at: str, from_fingerprint: str | None) -> bool:
    """Re-applies ``scorer`` to one saved result; returns whether its scoring state changed.

    A failure is recorded as ``scorer_error`` on that sample only.
    """
    if not reached_scoring(r):
        return False
    assert r.output is not None
    new: Score | Exception
    try:
        if requires_judges(scorer):
            if r.judge is None:
                return False
            # Saved judge replies are parsed and aggregated again; no judge is called.
            new = check_score(scorer.reparse(r.sample, r.output.text, r.judge))
        else:
            new = check_score(scorer(r.sample, r.output.text))
    except NeedsNewJudgement:
        raise
    except Exception as exc:
        new = exc
    return apply_rescore(r, new, at=at, from_fingerprint=from_fingerprint)


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
            reason = "scorer needs the sandbox, which no longer exists"
            if grades_in_fresh_sandbox(scorer):
                reason = "grades saved submissions in a fresh sandbox; use `harness verify`"
            changes[name] = {"skipped": reason}
            continue
        if requires_judges(scorer) and (
            entry["scorer"].get("inputs_fingerprint") != scorer.inputs_fingerprint()
        ):
            changes[name] = {"skipped": NEEDS_NEW_JUDGEMENT}
            continue
        results = read_results(samples_path)

        at = now()
        old_fp = entry["scorer"].get("fingerprint")
        try:
            changed = sum(
                rescore_sample(r, scorer, at=at, from_fingerprint=old_fp) for r in results
            )
        except NeedsNewJudgement:
            changes[name] = {"skipped": NEEDS_NEW_JUDGEMENT}
            continue
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
        if requires_judges(scorer):
            changes[name]["judge_errors"] = sum(r.error_kind == ErrorKind.JUDGE for r in results)

    write_json(manifest_path, manifest)
    return changes
