"""``harness harbor``: run, import and inspect Harbor jobs. Thin; the work is in
``harness.integrations.harbor``."""

from __future__ import annotations

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


def _say(text: str) -> None:
    console.print(text, markup=False, highlight=False)


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
