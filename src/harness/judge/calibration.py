"""Agreement between saved judgements and independent labels (e.g. human ratings).

Labels are JSON lines ``{"sample_id": ..., "label": ...}`` (optionally ``"epoch"``):

* pairwise runs: ``label`` is ``candidate``, ``reference`` or ``tie``;
* rubric runs: ``label`` is a score in [0, 1]; agreement is on pass/fail at ``threshold``,
  and the mean absolute difference of the scores is reported too.

Only samples with a label count. Samples without a usable judgement (judge errors) and, for
pairwise, judges that changed their pick with the order are reported apart, not dropped.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from harness.core.io import read_json, read_results
from harness.core.types import ErrorKind, SampleResult

PAIRWISE_LABELS = ("candidate", "reference", "tie")


def read_labels(path: Path) -> dict[tuple[str, int], Any]:
    labels: dict[tuple[str, int], Any] = {}
    for n, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if "sample_id" not in row or "label" not in row:
            raise ValueError(f"{path}:{n}: needs 'sample_id' and 'label'")
        key = (row["sample_id"], int(row.get("epoch", 1)))
        if key in labels:
            raise ValueError(f"{path}:{n}: duplicate label for {key}")
        labels[key] = row["label"]
    return labels


def _rubric_label(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"rubric labels must be numbers in [0, 1], got {value!r}")
    if not 0 <= value <= 1:
        raise ValueError(f"rubric labels must be in [0, 1], got {value!r}")
    return float(value)


def agreement(
    results: list[SampleResult], labels: dict[tuple[str, int], Any], mode: str, threshold: float
) -> dict[str, Any]:
    labelled = [r for r in results if (r.sample.id, r.epoch) in labels]
    judged = [r for r in labelled if r.score is not None and r.error_kind is None]
    invalid = [r for r in labelled if r.error_kind == ErrorKind.JUDGE]
    report: dict[str, Any] = {
        "mode": mode,
        "labels": len(labels),
        "labelled_samples": len(labelled),
        "labels_without_sample": len(labels) - len(labelled),
        "valid_judgements": len(judged),
        "invalid_judgements": len(invalid),
        "other_errors": len(labelled) - len(judged) - len(invalid),
    }
    if mode == "pairwise":
        for value in labels.values():
            if value not in PAIRWISE_LABELS:
                raise ValueError(f"pairwise labels must be one of {PAIRWISE_LABELS}, got {value!r}")
        # judgements that are neither a stable pick nor a stable tie cannot agree with a label
        unstable = [r for r in judged if r.score and r.score.extracted not in PAIRWISE_LABELS]
        stable = [r for r in judged if r not in unstable]
        agree = sum(r.score.extracted == labels[(r.sample.id, r.epoch)] for r in stable)  # type: ignore[union-attr]
        report |= {
            "unstable_judgements": len(unstable),
            "unstable_by_kind": {
                kind: sum(r.score.extracted == kind for r in unstable)  # type: ignore[union-attr]
                for kind in ("inconsistent", "disagreement")
            },
            "compared": len(stable),
            "agreement": agree / len(stable) if stable else None,
        }
    elif mode == "rubric":
        pairs = [(r.score.value, _rubric_label(labels[(r.sample.id, r.epoch)])) for r in judged]  # type: ignore[union-attr]
        agree = sum((j >= threshold) == (h >= threshold) for j, h in pairs)
        report |= {
            "threshold": threshold,
            "compared": len(pairs),
            "agreement": agree / len(pairs) if pairs else None,
            "mean_abs_difference": sum(abs(j - h) for j, h in pairs) / len(pairs)
            if pairs
            else None,
        }
    else:
        raise ValueError(f"unknown judge mode {mode!r}")
    return report


def run_agreement(
    run_dir: Path, labels_path: Path, *, task: str | None = None, threshold: float = 0.5
) -> dict[str, Any]:
    """Agreement of one task's saved judgements in ``run_dir`` with the labels file; no model
    is called. The report says which labels file and judge setup it describes."""
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    entries = [e for e in manifest["tasks"] if e.get("judges")]
    if task is not None:
        entries = [e for e in entries if e["name"] == task]
    if len(entries) != 1:
        names = [e["name"] for e in entries]
        raise ValueError(f"pick one judged task with --task; judged tasks here: {names}")
    entry = entries[0]
    results = read_results(run_dir / entry["name"] / "samples.jsonl")
    report = agreement(results, read_labels(labels_path), entry["scorer"]["mode"], threshold)
    return {
        "run_dir": str(run_dir),
        "task": entry["name"],
        "labels_file": str(labels_path),
        "scorer_fingerprint": entry["scorer"].get("fingerprint"),
        "judges": entry["judges"],
        **report,
    }
