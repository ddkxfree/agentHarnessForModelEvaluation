"""Markdown failure report for a run: outcome counts and a few sampled examples per failure
type, built only from saved results."""

from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

from harness.core.io import read_json, read_results
from harness.core.types import Outcome, SampleResult


def _tail(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def _fence(text: str) -> str:
    return "```text\n" + text.replace("```", "ˋˋˋ") + "\n```"


def _example(r: SampleResult, chars: int) -> list[str]:
    title = r.sample.id if r.epoch == 1 else f"{r.sample.id} (epoch {r.epoch})"
    lines = [f"#### `{title}`", ""]
    if r.error:
        lines.append(f"- error ({r.error_kind}): `{_tail(r.error, 300)}`")
    if r.score is not None:
        lines.append(f"- score {r.score.value:g}, extracted `{r.score.extracted}`")
    lines.append(f"- target: `{_tail(r.sample.target, 200)}`")
    if r.output is not None:
        lines.append(f"- finish reason: `{r.output.finish_reason}`")
    if r.steps:
        lines.append(f"- {len(r.steps)} steps; last commands:")
        for step in r.steps[-2:]:
            for call, code in zip(step.tool_calls, step.exit_codes, strict=False):
                lines.append(f"  - step {step.index}: `{_tail(call.arguments, 200)}` → {code}")
            if not step.tool_calls:
                text = _tail(step.text, 200).replace("`", "'").replace("\n", " ")
                lines.append(f"  - step {step.index}: no tool call; said `{text}`")
    if r.output is not None and r.output.text.strip():
        lines += ["", "Final output (tail):", "", _fence(_tail(r.output.text, chars))]
    lines.append("")
    return lines


def failure_report(run_dir: Path, *, per_outcome: int = 3, seed: int = 0, chars: int = 800) -> str:
    """Return the report; examples are drawn with a fixed seed so it is reproducible."""
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    rng = random.Random(seed)
    out = [
        f"# Failure report: {manifest['run_id']}",
        "",
        f"- model: `{manifest['model'].get('name')}`",
        f"- status: {manifest['status']}",
        f"- examples: up to {per_outcome} per outcome, drawn with seed {seed}",
        "",
    ]
    for entry in manifest["tasks"]:
        name = entry["name"]
        path = run_dir / name / "samples.jsonl"
        if not path.exists():
            continue
        results = read_results(path)
        groups: dict[str, list[SampleResult]] = defaultdict(list)
        for r in results:
            groups[r.outcome.value if r.outcome else "unknown"].append(r)
        n = len(results)
        out += [f"## {name}", "", "| outcome | count | share |", "|---|---|---|"]
        for o in [*[o.value for o in Outcome], "unknown"]:
            if groups.get(o):
                out.append(f"| {o} | {len(groups[o])} | {len(groups[o]) / n:.1%} |")
        out.append("")
        for o, members in groups.items():
            if o == Outcome.CORRECT.value:
                continue
            chosen = sorted(
                rng.sample(members, min(per_outcome, len(members))),
                key=lambda r: (r.sample.id, r.epoch),
            )
            out += [f"### {o} ({len(members)})", ""]
            for r in chosen:
                out += _example(r, chars)
    return "\n".join(out).rstrip() + "\n"
