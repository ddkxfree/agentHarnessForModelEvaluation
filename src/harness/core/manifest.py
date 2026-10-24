from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import harness
from harness.core.fingerprint import fingerprint
from harness.core.task import Task
from harness.core.types import Sample

TRACKED_PACKAGES = ["openai", "datasets", "pydantic", "numpy"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=Path(harness.__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _find_lockfile() -> Path | None:
    for parent in Path(harness.__file__).resolve().parents:
        if (parent / "uv.lock").exists():
            return parent / "uv.lock"
    return None


def environment_info() -> dict[str, Any]:
    packages = {}
    for name in TRACKED_PACKAGES:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    lock = _find_lockfile()
    status = _git("status", "--porcelain")
    return {
        "harness_version": harness.__version__,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(status) if status is not None else None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": packages,
        "uv_lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest() if lock else None,
    }


def task_entry(task: Task, samples: list[Sample]) -> dict[str, Any]:
    return {
        "name": task.name,
        "params": task.params,
        "dataset": task.dataset.model_dump(),
        "solver": task.solver.describe(),
        "scorer": task.scorer.describe(),
        "time_limit": task.time_limit,
        "token_limit": task.token_limit,
        # How the solver talks to the model, if it says (not part of its fingerprint).
        "solver_protocol": task.solver.protocol() if hasattr(task.solver, "protocol") else None,
        "n_samples": len(samples),
        "sample_ids_sha256": fingerprint([s.id for s in samples]),
    }
