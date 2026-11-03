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
