"""Read-only views of a run directory, whichever backend wrote it.

A Native run (``manifest.json``) is read through its saved SampleResults; a Harbor run
(``harbor_run.json``) through the trials its importer mapped. Reading never executes anything
or calls a model. Fields a backend did not record stay None; they are not filled in.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HARBOR_RECORD = "harbor_run.json"
NATIVE_MANIFEST = "manifest.json"


@dataclass
class SampleView:
    sample_id: str
    task: str
    # independent repeat number, from 0; None when the backend did not say
    repeat: int | None
    status: str
    outcome: str
    # True/False only for a single binary score; None for unscored, errors and partial scores
    correct: bool | None
    scores: dict[str, Any] | None
    error_type: str | None = None
    error_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    # reported, unknown or not_applicable (an agent that calls no model)
    usage_status: str = "unknown"
    wall_time_s: float | None = None
    task_identity: dict[str, Any] = field(default_factory=dict)
    native: Any = None

    @property
    def key(self) -> str:
        return self.sample_id if not self.repeat else f"{self.sample_id}#repeat={self.repeat}"


@dataclass
class RunView:
    backend: str
    run_id: str
    path: Path
    status: str
    provenance: dict[str, Any]
    samples: list[SampleView]
    # what describes the system under test, for diffing two runs of the same backend
    config: dict[str, Any] = field(default_factory=dict)

    def outcomes(self) -> dict[str, int]:
        return dict(sorted(Counter(s.outcome for s in self.samples).items()))

    def accuracy(self) -> dict[str, Any]:
        scored = [s for s in self.samples if s.correct is not None]
        n_correct = sum(1 for s in scored if s.correct)
        return {
            "n": len(self.samples),
            "n_scored": len(scored),
            "n_correct": n_correct,
            # over every planned sample: unscored, errored, unfinished and missing ones count
            # as not correct
            "accuracy": n_correct / len(self.samples) if self.samples else None,
            # over the scored samples only; not the result of an incomplete run
            "accuracy_of_scored": n_correct / len(scored) if scored else None,
        }

    def completeness(self) -> dict[str, Any]:
        missing = sum(1 for s in self.samples if s.outcome == "missing")
        unfinished = sum(1 for s in self.samples if s.outcome == "running")
        return {
            "planned": len(self.samples),
            "missing": missing,
            "unfinished": unfinished,
            "complete": missing == 0 and unfinished == 0,
        }

    def completeness_warning(self) -> str | None:
        c = self.completeness()
        if c["complete"]:
            return None
        return (
            f"incomplete run: {c['missing']} of {c['planned']} planned samples missing, "
            f"{c['unfinished']} unfinished; they count as not correct, and the accuracy of "
            "the scored samples is not the run's result"
        )

    def usage(self) -> dict[str, Any]:
        with_model = [s for s in self.samples if s.usage_status != "not_applicable"]
        reported = [s for s in with_model if s.usage_status == "reported"]

        def total(attr: str) -> int | float | None:
            values = [getattr(s, attr) for s in reported]
            return None if not values or any(v is None for v in values) else sum(values)

        return {
            "samples_with_model": len(with_model),
            "reported": len(reported),
            "coverage": f"{len(reported)}/{len(with_model)}",
            # totals only when every model sample reported them; partial sums would mislead
            "input_tokens": total("input_tokens") if len(reported) == len(with_model) else None,
            "output_tokens": total("output_tokens") if len(reported) == len(with_model) else None,
            "cost_usd": total("cost_usd") if len(reported) == len(with_model) else None,
        }


def backend_of(run_dir: Path) -> str:
    run_dir = Path(run_dir)
    if (run_dir / HARBOR_RECORD).is_file():
        return "harbor"
    if (run_dir / NATIVE_MANIFEST).is_file():
        return "native"
    raise FileNotFoundError(f"{run_dir} is neither a harness run nor an imported Harbor run")


def open_run(run_dir: Path) -> RunView:
    run_dir = Path(run_dir)
    return _harbor(run_dir) if backend_of(run_dir) == "harbor" else _native(run_dir)


def _harbor(run_dir: Path) -> RunView:
    record = json.loads((run_dir / HARBOR_RECORD).read_text())
    samples = []
    receipt = record.get("run") or {}
    fingerprint = (receipt.get("task") or {}).get("fingerprint")
    for line in (run_dir / "trials.jsonl").read_text().splitlines():
        t = json.loads(line)
        usage = t.get("usage") or {}
        exc = t.get("exception") or {}
        rewards = t.get("rewards")
        samples.append(
            SampleView(
                sample_id=t.get("task_name") or t.get("trial_name") or "unknown-planned-trial",
                task=(t.get("task_name") or "?").split("/", 1)[0],
                repeat=t.get("repeat"),
                status=t["status"],
                outcome=t["outcome"],
                correct={"correct": True, "wrong": False}.get(t["outcome"]),
                scores=rewards,
                error_type=exc.get("type"),
                error_category=exc.get("category"),
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cost_usd=usage.get("cost_usd"),
                usage_status=usage.get("status", "unknown"),
                wall_time_s=(t.get("timing_s") or {}).get("total"),
                task_identity={
                    "harness_fingerprint": fingerprint,
                    "harbor_checksum": t.get("task_checksum"),
                    "harbor_digest": t.get("task_digest"),
                },
                native=t,
            )
        )
    agents = sorted({json.dumps(s.native.get("agent"), sort_keys=True) for s in samples})
    job = record.get("job") or {}
    status = (record.get("state") or {}).get("status") or job.get("status") or "unknown"
    return RunView(
        backend="harbor",
        run_id=record["run_id"],
        path=run_dir,
        status=status,
        provenance={
            "harbor": record.get("harbor"),
            "importer": record.get("importer"),
            "source": record.get("source"),
            "raw": {k: record.get("raw", {}).get(k) for k in ("dir", "index_sha256", "n_files")},
        },
        samples=samples,
        config={
            "backend": "harbor",
            "harbor_version": (record.get("harbor") or {}).get("version"),
            "agents": [json.loads(a) for a in agents],
            "tasks": record.get("tasks"),
            "run": receipt.get("config"),
        },
    )


def _native(run_dir: Path) -> RunView:
    from harness.core.io import read_json, read_results

    manifest = read_json(run_dir / NATIVE_MANIFEST)
    samples = []
    for entry in manifest.get("tasks", []):
        name = entry["name"]
        path = run_dir / name / "samples.jsonl"
        if not path.exists():
            continue
        for r in read_results(path):
            score = r.score.value if r.score is not None else None
            u = r.usage()
            # the Native runner counts every model call, so none means none were made
            made_calls = r.calls.fresh + r.calls.cached > 0
            samples.append(
                SampleView(
                    sample_id=r.sample.id,
                    task=name,
                    repeat=r.epoch - 1,
                    status="error" if r.error else "completed",
                    outcome=str(r.outcome) if r.outcome is not None else "unknown",
                    correct=None if score is None or score not in (0.0, 1.0) else score == 1.0,
                    scores={"score": score} if score is not None else None,
                    error_type=str(r.error_kind) if r.error_kind else None,
                    error_category=str(r.error_kind) if r.error_kind else None,
                    input_tokens=u.input_tokens if made_calls else None,
                    output_tokens=u.output_tokens if made_calls else None,
                    usage_status="reported" if made_calls else "not_applicable",
                    wall_time_s=r.wall_time_s,
                    task_identity={
                        "harness_fingerprint": r.sample.metadata.get("task_fingerprint"),
                    },
                    native=r,
                )
            )
    return RunView(
        backend="native",
        run_id=manifest["run_id"],
        path=run_dir,
        status=manifest.get("status", "unknown"),
        provenance={"model": manifest.get("model"), "environment": manifest.get("environment")},
        samples=samples,
        config={
            "backend": "native",
            "model": manifest.get("model"),
            "tasks": manifest.get("tasks"),
        },
    )
