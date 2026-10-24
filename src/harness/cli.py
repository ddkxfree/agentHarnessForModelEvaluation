from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn
from rich.table import Table

from harness.core.config import RunConfig, TaskConfig
from harness.core.io import read_json, read_results
from harness.core.types import Outcome, SampleResult

app = typer.Typer(add_completion=False, help="A small, reproducible LLM evaluation harness.")
console = Console()


def _parse_params(items: list[str]) -> dict[str, dict[str, Any]]:
    """Parse ``task.key=value`` pairs; values are YAML so lists and numbers work."""
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        lhs, sep, rhs = item.partition("=")
        task, dot, key = lhs.partition(".")
        if not sep or not dot:
            raise typer.BadParameter(f"expected task.key=value, got {item!r}")
        out.setdefault(task, {})[key] = yaml.safe_load(rhs)
    return out


def _summary_table(results: dict[str, dict[str, Any]], title: str) -> Table:
    table = Table(title=title)
    for col in ["task", "n", "accuracy", "95% CI", *[o.value for o in Outcome], "cached"]:
        table.add_column(col, justify="left" if col == "task" else "right")
    for name, s in results.items():
        lo, hi = s["ci95"]
        table.add_row(
            name,
            str(s["n"]),
            f"{s['accuracy']:.3f}",
            f"[{lo:.3f}, {hi:.3f}]",
            *[str(s["outcomes"][o.value]) for o in Outcome],
            str(s["cached"]),
        )
    return table


@app.command("list-tasks")
def list_tasks() -> None:
    """List registered tasks."""
    import harness.tasks  # noqa: F401
    from harness.core.task import TASKS

    for name in sorted(TASKS):
        console.print(name)


@app.command()
def run(
    config: Annotated[Path | None, typer.Option("--config", "-c", help="YAML run config")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="provider/model")] = None,
    task: Annotated[list[str] | None, typer.Option("--task", "-t")] = None,
    limit: Annotated[int | None, typer.Option(help="Max samples per task")] = None,
    param: Annotated[list[str] | None, typer.Option("--param", "-p", help="task.key=value")] = None,
    base_url: Annotated[str | None, typer.Option()] = None,
    temperature: Annotated[float | None, typer.Option()] = None,
    max_tokens: Annotated[int | None, typer.Option()] = None,
    seed: Annotated[int | None, typer.Option(help="Sampling seed sent to the model")] = None,
    concurrency: Annotated[int | None, typer.Option()] = None,
    retries: Annotated[int | None, typer.Option()] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Evaluate a model on one or more tasks."""
    from harness.core.cache import DiskCache
    from harness.core.model import get_model
    from harness.core.runner import run_eval
    from harness.core.task import get_task

    if config is not None:
        cfg = RunConfig.from_yaml(config)
    elif model and task:
        cfg = RunConfig(model=model, tasks=[TaskConfig(name=t) for t in task])
    else:
        raise typer.BadParameter("pass --config, or both --model and --task")

    updates: dict[str, Any] = {}
    for key, value in [
        ("model", model),
        ("base_url", base_url),
        ("concurrency", concurrency),
        ("retries", retries),
        ("output_dir", output_dir),
    ]:
        if value is not None:
            updates[key] = value
    if no_cache:
        updates["cache"] = False
    gen_updates = {
        k: v
        for k, v in {"temperature": temperature, "max_tokens": max_tokens, "seed": seed}.items()
        if v is not None
    }
    if gen_updates:
        updates["generate"] = cfg.generate.model_copy(update=gen_updates)
    if task and config is not None:
        updates["tasks"] = [t for t in cfg.tasks if t.name in task] or [
            TaskConfig(name=t) for t in task
        ]
    cfg = cfg.model_copy(update=updates)

    overrides = _parse_params(param or [])
    for t in cfg.tasks:
        if limit is not None:
            t.params["limit"] = limit
        t.params.update(overrides.get(t.name, {}))

    llm = get_model(cfg.model, base_url=cfg.base_url)
    tasks = [(get_task(t.name, **t.params), cfg.generate_for(t)) for t in cfg.tasks]
    cache = DiskCache(cfg.cache_dir) if cfg.cache else None
    sandbox_provider = None
    if any(t.sandbox is not None for t, _ in tasks):
        from harness.sandbox.docker import DockerProvider

        sandbox_provider = DockerProvider()

    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        run_dir = asyncio.run(
            run_eval(
                llm,
                tasks,
                output_dir=cfg.output_dir,
                cache=cache,
                concurrency=cfg.concurrency,
                retries=cfg.retries,
                run_config=cfg.model_dump(mode="json"),
                progress=progress,
                sandbox_provider=sandbox_provider,
            )
        )

    manifest = read_json(run_dir / "manifest.json")
    console.print(_summary_table(manifest["results"], f"{cfg.model}  ({run_dir})"))


def _print_sample(r: SampleResult, full: bool) -> None:
    def plain(text: str) -> None:
        console.print(text if full else text[-800:], markup=False, highlight=False)

    console.rule(f"{r.sample.id}  outcome={r.outcome}")
    extracted = r.score.extracted if r.score else None
    method = r.score.method if r.score else None
    console.print(f"target={r.sample.target!r}  extracted={extracted!r}  method={method}")
    if r.error:
        console.print(f"[red]{r.error}[/]")
    if r.cleanup_error:
        console.print(f"[red]sandbox cleanup failed: {r.cleanup_error}[/]  {r.sandbox}")
    artifacts = {a.name: a for a in r.artifacts}
    for step in r.steps:
        tokens = step.usage.input_tokens + step.usage.output_tokens
        console.print(f"[bold cyan]step {step.index}[/]  ({tokens} tokens)")
        if step.text.strip():
            plain(step.text)
        raw = step.raw_outputs or [None] * len(step.observations)
        for call, obs, raw_name in zip(step.tool_calls, step.observations, raw, strict=False):
            plain(f"$ {call.name} {call.arguments}")
            plain(obs)
            if raw_name and (a := artifacts.get(raw_name)):
                console.print(f"[dim]full output ({a.size} bytes): artifacts/{a.path}[/]")
    if not r.steps:
        plain(r.output.text if r.output else "")
    if r.score:
        for key, value in r.score.details.items():
            console.print(f"[bold]{key}[/]:")
            plain(str(value))


@app.command()
def show(
    run_dir: Path,
    task: Annotated[str | None, typer.Option("--task", "-t")] = None,
    outcome: Annotated[str | None, typer.Option(help="Only show samples with this outcome")] = None,
    sample: Annotated[str | None, typer.Option(help="Show one sample by id, in full")] = None,
    n: Annotated[int, typer.Option("-n", help="Number of samples to print")] = 5,
) -> None:
    """Print a run's summary and example samples, e.g. all extract_failed cases.

    Agent samples are printed with their full trajectory of tool calls and observations.
    """
    manifest = read_json(run_dir / "manifest.json")
    console.print(_summary_table(manifest["results"], manifest["run_id"]))

    for name, s in manifest["results"].items():
        console.print(f"[bold]{name}[/] extraction methods: {s['extraction_methods']}")
        if s.get("agent"):
            console.print(f"[bold]{name}[/] agent: {s['agent']}")

    if outcome is None and sample is None:
        return
    names = [task] if task else list(manifest["results"])
    for name in names:
        shown = 0
        for r in read_results(run_dir / name / "samples.jsonl"):
            if sample is not None:
                if r.sample.id == sample:
                    _print_sample(r, full=True)
                continue
            if r.outcome != outcome or shown >= n:
                continue
            shown += 1
            _print_sample(r, full=False)


@app.command("sandbox-cleanup")
def sandbox_cleanup() -> None:
    """Remove leftover sandbox containers, e.g. after a crashed run."""
    from harness.sandbox.docker import DockerProvider

    console.print(f"removed {DockerProvider().cleanup()} container(s)")


@app.command()
def rescore(run_dir: Path) -> None:
    """Re-score saved outputs with the current scorer code, without calling the model."""
    from harness.analysis.rescore import rescore_run

    changes = rescore_run(run_dir)
    for name, c in changes.items():
        if "skipped" in c:
            console.print(f"{name}: skipped ({c['skipped']})")
            continue
        console.print(
            f"{name}: accuracy {c['accuracy_before']:.3f} -> {c['accuracy_after']:.3f}"
            f" ({c['samples_changed']} samples changed, {c['scorer_errors']} scorer errors)"
        )


@app.command()
def diff(run_a: Path, run_b: Path, show_ids: Annotated[int, typer.Option()] = 10) -> None:
    """Compare two runs: configuration/environment changes and per-sample flips."""
    from harness.analysis.diff import diff_runs

    d = diff_runs(run_a, run_b)
    console.rule(f"{d['a']}  vs  {d['b']}")
    if d["manifest_changes"]:
        table = Table(title="manifest changes")
        for col in ["key", "a", "b"]:
            table.add_column(col, overflow="fold")
        for key, va, vb in d["manifest_changes"]:
            table.add_row(key, repr(va), repr(vb))
        console.print(table)
    else:
        console.print("manifests identical (ignoring run id and timestamps)")

    for name, t in d["tasks"].items():
        console.rule(name)
        console.print(
            f"accuracy {t['accuracy_a']:.3f} -> {t['accuracy_b']:.3f} "
            f"on {t['common_samples']} common samples "
            f"(only in a: {t['only_in_a']}, only in b: {t['only_in_b']})"
        )
        console.print(f"identical outputs: {t['identical_outputs']}/{t['common_samples']}")
        console.print(f"regressions ({len(t['regressions'])}): {t['regressions'][:show_ids]}")
        console.print(f"improvements ({len(t['improvements'])}): {t['improvements'][:show_ids]}")
        if t["outcome_transitions"]:
            console.print(f"outcome transitions: {t['outcome_transitions']}")


if __name__ == "__main__":
    app()
