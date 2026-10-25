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
    cache_policy: Annotated[
        str | None,
        typer.Option(help="auto (reuse only at temperature 0), always or never"),
    ] = None,
    output_dir: Annotated[Path | None, typer.Option()] = None,
    epochs: Annotated[int | None, typer.Option(help="Run every sample N times")] = None,
    cache_namespace: Annotated[
        str | None, typer.Option(help="Keep cached outputs apart from other namespaces")
    ] = None,
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
        ("cache_policy", cache_policy),
        ("epochs", epochs),
        ("cache_namespace", cache_namespace),
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
                cache_policy=cfg.cache_policy,
                epochs=cfg.epochs,
                namespace=cfg.cache_namespace,
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
    if r.judge:
        for call, verdict in zip(r.judge.calls, r.judge.verdicts, strict=False):
            served = call.served_model or call.model.get("name")
            note = verdict.get("error") or verdict.get("reason") or ""
            console.print(
                f"[bold magenta]judge {call.judge}[/] {call.purpose} ({served}"
                f"{', cached' if call.cached else ''}): {verdict.get('status')}"
            )
            plain(f"{ {k: v for k, v in verdict.items() if k in ('scores', 'preferred')} } {note}")


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
def rescore(
    run_dir: Path,
    param: Annotated[
        list[str] | None,
        typer.Option("--param", "-p", help="task.key=value; only params that affect scoring"),
    ] = None,
) -> None:
    """Re-score saved outputs with the current scorer code, without calling the model."""
    from harness.analysis.rescore import rescore_run

    try:
        changes = rescore_run(run_dir, _parse_params(param or []))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    for name, c in changes.items():
        if "skipped" in c:
            console.print(f"{name}: skipped ({c['skipped']})")
            continue
        console.print(
            f"{name}: accuracy {c['accuracy_before']:.3f} -> {c['accuracy_after']:.3f}"
            f" ({c['samples_changed']} samples changed, {c['scorer_errors']} scorer errors)"
        )


@app.command()
def verify(
    run_dir: Path,
    task: Annotated[list[str] | None, typer.Option("--task", "-t")] = None,
    sample: Annotated[list[str] | None, typer.Option(help="Only these sample ids")] = None,
    concurrency: Annotated[int, typer.Option()] = 2,
    update: Annotated[
        bool, typer.Option("--update", help="Replace the recorded grades with the new ones")
    ] = False,
) -> None:
    """Re-grade saved submissions (e.g. SWE-bench patches) in fresh containers, no model calls."""
    from harness.analysis.verify import verify_run
    from harness.sandbox.docker import DockerProvider

    report = asyncio.run(
        verify_run(
            run_dir,
            DockerProvider(),
            tasks=task,
            sample_ids=sample,
            concurrency=concurrency,
            update=update,
        )
    )
    for name, r in report.items():
        if "skipped" in r:
            console.print(f"{name}: skipped ({r['skipped']})")
            continue
        console.print(f"{name}: {r['n']} samples, {r['statuses']}")
        if r["disagreements"]:
            console.print(f"[red]disagree:[/] {', '.join(r['disagreements'])}")
        if r["environment_changed"]:
            console.print(
                "[yellow]graded in a different environment than recorded "
                f"(see verify.jsonl):[/] {', '.join(r['environment_changed'])}"
            )
        fp = r["scorer_fingerprint"]
        if fp["recorded"] != fp["current"]:
            console.print(f"[yellow]scorer changed since the run[/]: {fp}")


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
        p = t["paired"]
        if p["mean_difference"] is not None:
            lo, hi = p["ci95"]
            console.print(
                f"paired b - a: {p['mean_difference']:+.3f} [{lo:+.3f}, {hi:+.3f}], "
                f"sign test p={p['sign_test_p']:.3f}"
                + ("" if p["enough_samples_for_claims"] else " (too few samples for claims)")
            )
    if d["likely_causes"]:
        console.rule("likely causes")
        for hint in d["likely_causes"]:
            console.print(f"- {hint}")


def _print_comparison(report: dict[str, Any]) -> None:
    console.rule(f"{report['kind']}: {report['task']} with {report['model']}")
    console.print(report["note"])
    if "interpretation" in report:
        supported = report["interpretation"]["supported"]
        console.print(f"requested: {report['experiment_type']}; supported: {supported}")
        console.print(report["experiment_note"])
        for reason in report["interpretation"]["reasons"]:
            console.print(f"[yellow]- {reason}[/]")
        console.print("budgets:")
        for label, c in report["conditions"].items():
            budget = ", ".join(f"{k}={v}" for k, v in (c.get("budgets") or {}).items())
            console.print(f"  {label}: {budget or 'not recorded'}")
    table = Table(title=f"{report['repeats']} repeat(s) per condition")
    cols = ["condition", "accuracy (min-max)", "in tok", "out tok", "wall s", "steps"]
    for col in [*cols, "outcomes", "finish reasons"]:
        table.add_column(col, overflow="fold")
    for label, c in report["conditions"].items():
        acc = c["accuracy"]

        def num(v: float | None, fmt: str = ".0f") -> str:
            return "-" if v is None else format(v, fmt)

        table.add_row(
            label,
            f"{num(acc['mean'], '.3f')} ({num(acc['min'], '.2f')}-{num(acc['max'], '.2f')})",
            num(c["mean_input_tokens"]),
            num(c["mean_output_tokens"]),
            num(c["mean_wall_time_s"], ".1f"),
            num(c["mean_steps"], ".1f"),
            ", ".join(f"{k}={v}" for k, v in c["outcomes"].items() if v),
            ", ".join(f"{k}={v}" for k, v in c["finish_reasons"].items()),
        )
    console.print(table)
    per_sample = Table(title="solved / runs per sample")
    per_sample.add_column("sample", overflow="fold")
    for label in report["conditions"]:
        per_sample.add_column(label)
    for sample_id, row in report["per_sample_solved"].items():
        per_sample.add_row(sample_id, *row.values())
    console.print(per_sample)
    for label, p in report["paired_vs_baseline"].items():
        if p["mean_difference"] is None:
            continue
        lo, hi = p["ci95"]
        console.print(
            f"{label} - {report['baseline']}: {p['mean_difference']:+.3f} "
            f"[{lo:+.3f}, {hi:+.3f}] on {p['n']} paired samples; better on {p['b_better']}, "
            f"worse on {p['b_worse']}, sign test p={p['sign_test_p']:.3f}"
            + ("" if p["enough_samples_for_claims"] else " (too few samples for claims)")
        )
    for label, changes in report["config_changes_vs_baseline"].items():
        keys = ", ".join(k for k, _, _ in changes)
        console.print(f"{label} vs {report['baseline']} differs in: {keys}")
    for label, c in report["conditions"].items():
        for r in c["repeat_config_changes"]:
            console.print(f"[yellow]{label} repeat {r['repeat']} config differs:[/] {r['changes']}")
    if not report["same_samples_in_every_run"]:
        console.print("[yellow]runs did not all evaluate the same samples[/]")
    if not report["complete"]:
        console.print("[yellow]comparison is incomplete (missing or interrupted runs)[/]")
    for d in report["environment_drift"]:
        console.print(f"[yellow]environment drift[/] {d['sample_id']} {d['field']}: {d['values']}")
    console.print(f"scores comparable: {report['scores_comparable']}")
    console.print(f"wall time / tokens comparable: {report['performance_comparable']}")
    replayed = {k: v for k, v in report["cache_states"].items() if v != "fresh"}
    if replayed:
        console.print(
            f"[yellow]runs with replayed (cached) responses, not timed as inference:[/] {replayed}"
        )


@app.command()
def compare(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="YAML comparison spec")
    ] = None,
    resume: Annotated[
        Path | None, typer.Option(help="Finish an interrupted comparison directory")
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", "-m", help="Override the spec's model (provider/name)"),
    ] = None,
    base_url: Annotated[str | None, typer.Option(help="Override the spec's base_url")] = None,
) -> None:
    """Run each condition of a comparison several times and report them side by side."""
    from harness.analysis.compare import CompareSpec, resume_comparison, run_comparison
    from harness.core.model import get_model
    from harness.core.task import get_task

    if (config is None) == (resume is None):
        raise typer.BadParameter("pass either --config or --resume")
    if resume is not None:
        if model is not None or base_url is not None:
            raise typer.BadParameter("a resumed comparison keeps its recorded model")
        spec = CompareSpec.model_validate(read_json(resume / "index.json")["spec"])
    else:
        assert config is not None
        spec = CompareSpec.from_yaml(config)
        overrides = {k: v for k, v in {"model": model, "base_url": base_url}.items() if v}
        spec = spec.model_copy(update=overrides)
    llm = get_model(spec.model, base_url=spec.base_url)
    sandbox_provider = None
    if get_task(spec.task, **spec.params_for(spec.conditions[0])).sandbox is not None:
        from harness.sandbox.docker import DockerProvider

        sandbox_provider = DockerProvider()
    with Progress(
        TextColumn("{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        compare_dir = asyncio.run(
            resume_comparison(resume, llm, sandbox_provider=sandbox_provider, progress=progress)
            if resume is not None
            else run_comparison(spec, llm, sandbox_provider=sandbox_provider, progress=progress)
        )
    _print_comparison(read_json(compare_dir / "compare.json"))
    console.print(f"results in {compare_dir}")


@app.command()
def report(
    run_dir: Path,
    per_outcome: Annotated[int, typer.Option(help="Examples per failure outcome")] = 3,
    seed: Annotated[int, typer.Option(help="Seed for choosing examples")] = 0,
) -> None:
    """Write <run_dir>/report.md: outcome counts and sampled failures, no model calls."""
    from harness.analysis.report import failure_report

    path = run_dir / "report.md"
    path.write_text(failure_report(run_dir, per_outcome=per_outcome, seed=seed))
    console.print(f"wrote {path}")


@app.command("judge-agreement")
def judge_agreement(
    run_dir: Path,
    labels: Annotated[Path, typer.Option(help="JSON lines of {sample_id, label}")],
    task: Annotated[str | None, typer.Option("--task", "-t")] = None,
    threshold: Annotated[float, typer.Option(help="Pass mark for rubric scores")] = 0.5,
) -> None:
    """Agreement of a run's saved judgements with independent labels; calls no model."""
    from harness.judge.calibration import run_agreement

    try:
        report = run_agreement(run_dir, labels, task=task, threshold=threshold)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print_json(data={k: v for k, v in report.items() if k != "judges"})


@app.command()
def onboard(
    config: Annotated[Path, typer.Option("--config", "-c", help="Onboarding spec (YAML)")],
    model: Annotated[str | None, typer.Option("--model", "-m", help="provider/model")] = None,
    base_url: Annotated[str | None, typer.Option()] = None,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    cache_dir: Annotated[Path, typer.Option()] = Path(".cache/requests"),
    output_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Let a model write a Task adapter for a frozen source, validated independently.

    The result is a candidate diff for a person to review; nothing is applied to the repo.
    """
    from harness.core.cache import DiskCache
    from harness.core.model import get_model
    from harness.onboard.agent import OnboardAgent
    from harness.onboard.spec import load_spec

    spec = load_spec(config, model=model, output_dir=output_dir)
    if spec.model is None:
        raise typer.BadParameter("pass --model or set 'model' in the spec")
    agent = OnboardAgent(
        spec,
        get_model(spec.model, base_url=base_url),
        cache=None if no_cache else DiskCache(cache_dir),
        spec_path=config,
    )
    attempt = asyncio.run(agent.run())
    _print_attempt(read_json(attempt / "attempt.json"), attempt)


def _print_attempt(record: dict[str, Any], attempt: Path) -> None:
    color = "green" if record["status"] == "passed" else "red"
    console.print(
        f"[bold {color}]{record['status']}[/] ({record['stop_reason']}"
        f"{', ' + record['category'] if record['category'] else ''}) {attempt}"
    )
    if record.get("stop_detail"):
        console.print(record["stop_detail"], markup=False, highlight=False)
    for rnd in record["rounds"]:
        console.print(f"round {rnd['round']}: {rnd.get('category') or 'passed'}")
    for q in record.get("questions", []):
        console.print(f"[yellow]question:[/] {q}", highlight=False)
    usage = record["usage"]
    console.print(
        f"{len(record['steps'])} model steps, {usage['input_tokens']}+{usage['output_tokens']}"
        " tokens"
    )
    if record.get("diff"):
        console.print(f"candidate diff for review: {attempt / record['diff']}")


@app.command("onboard-verify")
def onboard_verify(attempt: Path) -> None:
    """Validate a saved onboarding attempt's candidate again, without calling a model."""
    from harness.onboard.agent import verify_attempt

    report = verify_attempt(attempt)
    status, color = ("passed", "green") if report["passed"] else ("failed", "red")
    console.print(f"[bold {color}]{status}[/] {report.get('category') or ''}")
    if report.get("detail"):
        console.print(report["detail"], markup=False, highlight=False)
    if not report["passed"]:
        raise typer.Exit(1)


@app.command("compare-report")
def compare_report(compare_dir: Path) -> None:
    """Rebuild a comparison's report from its saved runs, without calling the model."""
    from harness.analysis.compare import build_report
    from harness.core.io import write_json

    report = build_report(compare_dir)
    write_json(compare_dir / "compare.json", report)
    _print_comparison(report)


if __name__ == "__main__":
    app()
