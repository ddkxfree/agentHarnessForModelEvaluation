"""Diff and report over RunViews, so runs of either backend (or one of each) can be read
side by side. Nothing here executes recorded content or calls a model."""

from __future__ import annotations

import json
import re
from typing import Any

from harness.results import RunView, SampleView

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text: Any) -> str:
    """Recorded text is untrusted: no terminal escape or control sequences."""
    return _CONTROL.sub("?", str(text))


def _same_task(a: SampleView, b: SampleView) -> str:
    """same, different or unverified, from the identities both backends recorded."""
    ia, ib = a.task_identity, b.task_identity
    for key in ("harness_fingerprint", "harbor_checksum"):
        va, vb = ia.get(key), ib.get(key)
        if va and vb:
            return "same" if va == vb else "different"
    return "unverified"


def _config_changes(a: RunView, b: RunView) -> list[tuple[str, Any, Any]]:
    from harness.analysis.diff import flatten

    fa, fb = flatten(a.config), flatten(b.config)
    return [
        (k, fa.get(k), fb.get(k)) for k in sorted(fa.keys() | fb.keys()) if fa.get(k) != fb.get(k)
    ]


def diff_views(a: RunView, b: RunView) -> dict[str, Any]:
    from harness.analysis.stats import paired_difference

    by_key_a = {s.key: s for s in a.samples}
    by_key_b = {s.key: s for s in b.samples}
    common = sorted(by_key_a.keys() & by_key_b.keys())
    identity = {k: _same_task(by_key_a[k], by_key_b[k]) for k in common}
    comparable = [k for k in common if identity[k] != "different"]
    scored = [
        k for k in comparable if by_key_a[k].correct is not None and by_key_b[k].correct is not None
    ]
    flips = {
        k: (by_key_a[k].outcome, by_key_b[k].outcome)
        for k in comparable
        if by_key_a[k].outcome != by_key_b[k].outcome
    }
    notes = []
    if a.backend != b.backend:
        notes.append(
            f"{a.backend} vs {b.backend}: different execution systems (agent loop, prompts, "
            "environment setup and scoring code all differ); a difference is a system "
            "difference, not an engine difference"
        )
    if any(v == "different" for v in identity.values()):
        notes.append("some samples have the same name but different task content; not paired")
    if any(v == "unverified" for v in identity.values()):
        notes.append("task content of some pairs could not be verified as identical")
    ua, ub = a.usage(), b.usage()
    cost_comparable = (
        ua["samples_with_model"] == ua["reported"] and ub["samples_with_model"] == ub["reported"]
    )
    if not cost_comparable:
        notes.append(
            f"usage coverage {ua['coverage']} vs {ub['coverage']}: cost and tokens not comparable"
        )
    for side, view in (("a", a), ("b", b)):
        notes.extend(f"{side}: {w}" for w in view.warnings())
    unpaired = sorted(by_key_a.keys() ^ by_key_b.keys())
    if unpaired:
        notes.append(f"{len(unpaired)} sample(s) present in only one run are not compared")
    return {
        "a": {"run_id": a.run_id, "backend": a.backend, "status": a.status},
        "b": {"run_id": b.run_id, "backend": b.backend, "status": b.status},
        "config_changes": _config_changes(a, b) if a.backend == b.backend else [],
        "accuracy": {"a": a.accuracy(), "b": b.accuracy()},
        "completeness": {"a": a.completeness(), "b": b.completeness()},
        "outcomes": {"a": a.outcomes(), "b": b.outcomes()},
        "usage": {"a": ua, "b": ub, "comparable": cost_comparable},
        "task_identity": identity,
        "flips": flips,
        "unpaired": unpaired,
        "paired": paired_difference(
            {k: float(by_key_a[k].correct) for k in scored},
            {k: float(by_key_b[k].correct) for k in scored},
        ),
        "notes": notes,
    }


def report_view(view: RunView, *, chars: int = 800) -> str:
    """A Markdown report of a run read through its view (used for Harbor runs)."""
    acc = view.accuracy()
    usage = view.usage()
    lines = [
        f"# Report: {safe(view.run_id)}",
        "",
        f"- backend: {view.backend}, status: {safe(view.status)}",
        f"- provenance: `{safe(json.dumps(view.provenance, sort_keys=True))}`",
        f"- samples: {acc['n']}, scored: {acc['n_scored']}, correct: {acc['n_correct']}",
        *[f"- **{safe(w)}**" for w in view.warnings()],
        f"- usage reported: {usage['coverage']} samples that use a model",
        "",
        "| outcome | count |",
        "|---|---|",
        *(f"| {safe(k)} | {v} |" for k, v in view.outcomes().items()),
        "",
        "## Samples",
        "",
        "| sample | repeat | outcome | scores | error | time (s) |",
        "|---|---|---|---|---|---|",
    ]
    for s in view.samples:
        error = f"{s.error_type} ({s.error_category})" if s.error_type else ""
        scores = safe(json.dumps(s.scores)).replace("|", "/")
        lines.append(
            f"| {safe(s.sample_id)} | {s.repeat} | {safe(s.outcome)} | {scores} | {safe(error)} "
            f"| {s.wall_time_s} |"
        )
    failures = [s for s in view.samples if s.outcome not in ("correct",)]
    if failures:
        lines += ["", "## Failures", ""]
    for s in failures:
        native = s.native if isinstance(s.native, dict) else {}
        exc = native.get("exception") or {}
        note = native.get("score_note")
        lines += [f"### {safe(s.key)}: {safe(s.outcome)}", ""]
        if note:
            lines.append(f"- note: {safe(note)}")
        if exc:
            lines.append(f"- exception: {safe(exc.get('type'))} in phase {safe(exc.get('phase'))}")
            msg = safe(exc.get("message", ""))[:chars].replace("`", "'")
            lines += ["", "```text", msg, "```"]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
