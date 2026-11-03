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
