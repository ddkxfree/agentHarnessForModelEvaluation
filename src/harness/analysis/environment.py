"""The actual execution environment of a sample, as recorded from its sandbox.

Manifests record what was requested (an image tag, a build context); ``SampleResult.sandbox``
and ``grading_sandbox`` record what ran: image id and digests, platform and the limits the
daemon applied. Two runs with identical manifests can still differ here, e.g. when a floating
tag such as ``:latest`` resolves to a new image.
"""

from __future__ import annotations

from typing import Any

from harness.analysis.diff import flatten
from harness.core.types import SampleResult

# Differ for every container by construction.
PER_CONTAINER = {"container_id", "container_name"}


def identity(sandbox: dict[str, Any] | None) -> dict[str, Any]:
    if not sandbox:
        return {}
    return flatten({k: v for k, v in sandbox.items() if k not in PER_CONTAINER})


def sample_identity(r: SampleResult) -> dict[str, Any]:
    """Flattened identity of the agent sandbox and, if any, the grading sandbox."""
    out = {f"sandbox.{k}": v for k, v in identity(r.sandbox).items()}
    out.update({f"grading_sandbox.{k}": v for k, v in identity(r.grading_sandbox).items()})
    return out


def changes(a: dict[str, Any], b: dict[str, Any]) -> list[list[Any]]:
    return [[k, a.get(k), b.get(k)] for k in sorted(a.keys() | b.keys()) if a.get(k) != b.get(k)]
