"""``harness harbor``: run, import and inspect Harbor jobs. Thin; the work is in
``harness.integrations.harbor``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

harbor_app = typer.Typer(
    add_completion=False,
    help="Harbor backend: a pinned Harbor release run as a separate process.",
    no_args_is_help=True,
)
console = Console()


_CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def safe(text: object) -> str:
    """Text from Harbor records is untrusted: no terminal escape or control sequences."""
    return _CONTROL.sub("?", str(text))


def _say(text: str) -> None:
    console.print(safe(text), markup=False, highlight=False)


@harbor_app.command()
def doctor(
    config: Annotated[Path | None, typer.Option("--config", "-c", help="Harbor run config")] = None,
) -> None:
    """Check Harbor, Docker and a config without running anything or calling a model."""
    import os

    from harness.integrations.harbor import PINNED_VERSION
    from harness.integrations.harbor.config import capabilities, load_config, resolve_task
    from harness.integrations.harbor.runtime import HarborError, check_docker, probe

    problems = []
    executable = None
    cfg = root = None
    if config is not None:
        try:
            cfg, root = load_config(config)
            executable = cfg.harbor.executable
            console.print(f"[green]ok[/] config {escape(str(config))}")
        except Exception as exc:  # validation errors are user-facing
            problems.append(f"config: {exc}")
            _say(f"FAIL config {config}: {exc}")
    try:
        rt = probe(executable)
        console.print(f"[green]ok[/] Harbor {rt.version} at {escape(rt.executable)}")
        _say(f"   venv {rt.venv} python {rt.python} dist RECORD sha256 {rt.dist_record_sha256}")
    except HarborError as exc:
        problems.append(f"harbor: {exc}")
        _say(f"FAIL harbor (pinned {PINNED_VERSION}): {exc}")
    for name, res in check_docker().items():
        if res["ok"]:
            console.print(f"[green]ok[/] docker {name} {escape(res['detail'])}")
        else:
            problems.append(f"docker {name}: {res['detail']}")
            _say(f"FAIL docker {name}: {res['detail']}")
    if cfg is not None and root is not None:
        try:
            task = resolve_task(cfg, root)
            _say(f"ok task {task['source']} fingerprint {task['fingerprint']}")
        except Exception as exc:
            problems.append(f"task: {exc}")
            _say(f"FAIL task: {exc}")
        missing = [n for n in cfg.agent.env if n not in os.environ]
        if missing:
            problems.append(f"agent env not set: {missing}")
            _say(f"FAIL agent environment variables not set: {', '.join(missing)}")
        elif cfg.agent.env:
            _say(f"ok agent environment variables set: {', '.join(cfg.agent.env)} (values hidden)")
        table = Table("limit", "value", "how it is applied")
        for row in capabilities(cfg):
            table.add_row(row["limit"], row["value"], row["how"])
        console.print(table)
    if problems:
        _say(f"{len(problems)} problem(s); nothing was run.")
        raise typer.Exit(1)
    _say("ready; nothing was run.")


@harbor_app.command("import")
def import_(
    job_dir: Path,
    out: Annotated[Path, typer.Option("--out", "-o", help="Parent of the run directory")] = Path(
        "runs/harbor"
    ),
    replace: Annotated[
        bool, typer.Option("--replace", help="Rebuild an import whose job or importer changed")
    ] = False,
) -> None:
    """Import a finished (or partial) Harbor 0.25.0 job directory. Offline; the job is only read."""
    from harness.integrations.harbor.importer import HarborImportError, import_job

    try:
        res = import_job(job_dir, out, replace=replace)
    except HarborImportError as exc:
        _say(f"cannot import {job_dir}: {exc}")
        raise typer.Exit(1) from None
    rec = res["record"]
    job, raw = rec["job"], rec["raw"]
    _say(f"{res['action']}: {res['run_dir']}")
    _say(
        f"job {job['status']}: {job['n_trials_found']}/{job['n_total_trials']} trials, "
        f"outcomes {job['outcomes']}, retries {job['n_retries']}"
    )
    _say(
        f"snapshot {raw['dir']}/: {raw['n_files']} files, {raw['bytes']} bytes, "
        f"{len(raw['skipped'])} skipped, {raw['redactions']} value(s) redacted"
    )
    for problem in job["problems"]:
        _say(f"problem: {problem}")


@harbor_app.command()
def rebuild(run_dir: Path) -> None:
    """Rebuild an imported run's records from its own redacted snapshot with the current
    importer (e.g. after an importer fix). Offline; the snapshot is checked, not changed."""
    from harness.integrations.harbor.importer import HarborImportError
    from harness.integrations.harbor.importer import rebuild as rebuild_records

    try:
        res = rebuild_records(run_dir)
    except (HarborImportError, FileNotFoundError) as exc:
        _say(f"cannot rebuild {run_dir}: {exc}")
        raise typer.Exit(1) from None
    job = res["record"]["job"]
    previous = (res["record"]["rebuilt"]["previous_importer"] or {}).get("version")
    _say(f"rebuilt {run_dir} (importer {previous} -> {res['record']['importer']['version']})")
    _say(
        f"job {job['status']}: {job['n_trials_found']}/{job['n_total_trials']} trials, "
        f"outcomes {job['outcomes']}"
    )


def show_harbor(run_dir: Path, *, outcome: str | None, sample: str | None, n: int) -> None:
    import json

    from harness.results import open_run

    view = open_run(run_dir)
    acc, usage = view.accuracy(), view.usage()
    table = Table(title=f"{safe(view.run_id)} (harbor, {safe(view.status)})")
    for col in ("samples", "scored", "correct", "outcomes", "usage reported"):
        table.add_column(col)
    table.add_row(
        str(acc["n"]),
        str(acc["n_scored"]),
        str(acc["n_correct"]),
        safe(view.outcomes()),
        usage["coverage"],
    )
    console.print(table)
    for warning in view.warnings():
        _say(f"WARNING {warning}")
    _say(f"provenance: {json.dumps(view.provenance, sort_keys=True)}")
    shown = 0
    for s in view.samples:
        if sample is not None:
            if sample in (s.sample_id, s.key, s.native.get("trial_name")):
                _say(json.dumps(s.native, indent=2))
            continue
        if outcome is None or s.outcome != outcome or shown >= n:
            continue
        shown += 1
        exc = s.native.get("exception") or {}
        _say(
            f"{s.key} [{s.native.get('trial_name')}] {s.outcome} rewards={s.scores} "
            f"error={exc.get('type')}/{exc.get('category')}@{exc.get('phase')} "
            f"note={s.native.get('score_note')}"
        )


def diff_any(run_a: Path, run_b: Path, *, show_ids: int) -> None:
    from harness.results import open_run
    from harness.results.analysis import diff_views

    d = diff_views(open_run(run_a), open_run(run_b))
    console.rule(safe(f"{d['a']['run_id']} ({d['a']['backend']})  vs  "
                      f"{d['b']['run_id']} ({d['b']['backend']})"))  # fmt: skip
    for note in d["notes"]:
        _say(f"note: {note}")
    if d["config_changes"]:
        table = Table(title="configuration changes")
        for col in ("key", "a", "b"):
            table.add_column(col, overflow="fold")
        for key, va, vb in d["config_changes"]:
            table.add_row(safe(key), safe(repr(va)), safe(repr(vb)))
        console.print(table)
    for side in ("a", "b"):
        acc = d["accuracy"][side]
        _say(
            f"{side}: {acc['n_correct']}/{acc['n']} correct ({acc['n_scored']} scored), "
            f"outcomes {d['outcomes'][side]}, usage reported {d['usage'][side]['coverage']}"
        )
    p = d["paired"]
    _say(
        f"paired on {p['n']} scored samples: b better {p['b_better']}, worse {p['b_worse']}, "
        f"ties {p['ties']}, enough for claims: {p['enough_samples_for_claims']}"
    )
    for key, (oa, ob) in list(d["flips"].items())[:show_ids]:
        _say(f"  {key}: {oa} -> {ob}")


def _print_final(run_dir: Path, state: dict) -> None:
    _say(f"status: {state.get('status')} (stop reason {state.get('stop_reason')}, "
         f"Harbor exit code {state.get('exit_code')})")  # fmt: skip
    last = (state.get("runs") or [{}])[-1]
    cleanup = last.get("cleanup") or {}
    if cleanup:
        late = cleanup.get("late") or {}
        removed = len(cleanup.get("containers", [])) + len(late.get("containers", []))
        errors = cleanup.get("errors", []) + late.get("errors", [])
        _say(f"cleanup: {removed} container(s) of this job removed, "
             f"leftover {cleanup.get('leftover')}, errors {errors}")  # fmt: skip
    if state.get("imported"):
        from harness.results import open_run

        view = open_run(run_dir)
        _say(f"trials: {len(view.samples)}, outcomes {view.outcomes()}, "
             f"usage reported {view.usage()['coverage']}")  # fmt: skip
        for warning in view.warnings():
            _say(f"WARNING {warning}")
    elif state.get("import_error"):
        _say(f"no trial records: {state['import_error']}")
    _say(f"run directory: {run_dir}")


@harbor_app.command()
def run(
    config: Annotated[Path, typer.Option("--config", "-c", help="Harbor run config")],
) -> None:
    """Run a Harbor job in the foreground as a separate, supervised process."""
    from harness.integrations.harbor import process as hp
    from harness.integrations.harbor.config import load_config
    from harness.integrations.harbor.runtime import HarborError

    try:
        cfg, root = load_config(config)
        run_dir = hp.prepare(cfg, root, config)
    except (HarborError, hp.HarborRunError, ValueError, OSError) as exc:
        _say(f"not started: {exc}")
        raise typer.Exit(1) from None
    import json

    receipt = json.loads((run_dir / hp.RECEIPT).read_text())
    _say(f"backend harbor {receipt['harbor']['version']} ({receipt['harbor']['executable']})")
    _say(f"task {receipt['task']['source']} fingerprint {receipt['task']['fingerprint']}")
    _say(f"agent {cfg.agent.name} model {cfg.agent.model or '-'}; attempts {cfg.attempts}, "
         f"concurrency {cfg.concurrency}, retries {cfg.max_retries}")  # fmt: skip
    _say(
        f"wall clock {cfg.limits.wall_clock_s:.0f}s; "
        f"Harbor job directory {run_dir / receipt['job_dir']}"
    )
    env = {**hp.base_env(), **hp.agent_env(cfg)}
    try:
        state = hp.supervise(
            run_dir, receipt["argv"], env, wall_clock_s=cfg.limits.wall_clock_s,
            grace_s=cfg.limits.stop_grace_s, cleanup_s=cfg.limits.cleanup_s,
        )  # fmt: skip
    except hp.HarborRunError as exc:
        _say(f"not started: {exc}")
        raise typer.Exit(1) from None
    _print_final(run_dir, state)
    if state.get("status") != "completed":
        raise typer.Exit(1)


@harbor_app.command()
def status(run_dir: Path) -> None:
    """Lifecycle state of a Harbor run, checked against the live process."""
    import json

    from harness.integrations.harbor import process as hp

    state = hp.status(run_dir)
    shown = {k: state.get(k) for k in ("status", "stop_reason", "exit_code", "supervised",
                                       "harbor_process", "cancel_pending", "trial_dirs",
                                       "harbor_stats", "imported", "note")}  # fmt: skip
    _say(json.dumps(shown, indent=2))


@harbor_app.command()
def cancel(
    run_dir: Path,
    wait: Annotated[float, typer.Option(help="Seconds to wait for the supervisor")] = 120.0,
) -> None:
    """Stop a running Harbor job and remove this job's containers; nothing else is touched."""
    from harness.integrations.harbor import process as hp

    try:
        state = hp.cancel(run_dir, wait_s=wait)
    except hp.RunLocked as exc:
        _say(str(exc))
        raise typer.Exit(1) from None
    _say(f"cancel: {state.get('cancel')}")
    _print_final(run_dir, state)
    if str(state.get("cancel", "")).startswith(("refused", "requested", "failed")):
        raise typer.Exit(1)


@harbor_app.command()
def resume(run_dir: Path) -> None:
    """Continue an interrupted or cancelled job with Harbor's own resume (completed trials are
    kept, cancelled ones are run again)."""
    import json
    import time

    from harness.integrations.harbor import process as hp
    from harness.integrations.harbor.config import HarborRunConfig
    from harness.integrations.harbor.runtime import HarborError

    requested_ns = time.time_ns()  # a cancel after this moment stops the resume
    state = hp.status(run_dir)
    if state["status"] not in hp.RESUMABLE:
        _say(f"cannot resume a run that is {state['status']}")
        raise typer.Exit(1)
    try:
        receipt = json.loads((run_dir / hp.RECEIPT).read_text())
        cfg = HarborRunConfig(**receipt["config"])
        argv, _ = hp.resume_argv(run_dir)
        env = {**hp.base_env(), **hp.agent_env(cfg)}
        state = hp.supervise(
            run_dir, argv, env, wall_clock_s=cfg.limits.wall_clock_s, kind="resume",
            requested_ns=requested_ns, grace_s=cfg.limits.stop_grace_s,
            cleanup_s=cfg.limits.cleanup_s,
        )  # fmt: skip
    except (HarborError, hp.HarborRunError) as exc:
        _say(f"not resumed: {exc}")
        raise typer.Exit(1) from None
    _print_final(run_dir, state)
    if state.get("status") != "completed":
        raise typer.Exit(1)
