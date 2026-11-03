"""Running Harbor as a separate process: start, supervise, stop, clean up, resume.

A run directory holds

    run.json            shareable receipt: requested and resolved configuration, argv, Harbor
                        identity, capabilities (no credential values)
    state.json          lifecycle: status, timestamps, exit code, stop reason, cleanup
    harbor.log          Harbor's stdout and stderr
    job/<job_name>/     Harbor's own job directory
    local/process.json  host-local process identity (pid, process start time, boot id); used
                        to prove a process is ours before signalling it, never shared
    local/lock          held (flock) by the supervising harness process for its lifetime

Harbor runs in its own process group. On a stop (wall clock, cancel, Ctrl-C) the group gets
SIGTERM, which Harbor 0.25.0 turns into a KeyboardInterrupt and handles by cancelling its
trials; after a grace period the group is killed. Then this job's own containers and networks
are removed: those whose compose project is named after one of the job's trial directories.
Nothing else is touched. The Harbor exit code is recorded but does not decide success; the
per-trial records do.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.integrations.harbor import importer
from harness.integrations.harbor.config import (
    HarborRunConfig,
    build_argv,
    capabilities,
    resolve_task,
)
from harness.integrations.harbor.runtime import base_env, probe

RECEIPT = "run.json"
STATE = "state.json"
LOG = "harbor.log"
LOCAL = "local"
PROCESS = "local/process.json"
LOCK = "local/lock"
CANCEL = "local/cancel"
JOBS = "job"
MAX_LOG_BYTES = 200 * 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted", "start_failed"}
DOCKER = "docker"
GRACE_S = 60.0  # after SIGTERM, before SIGKILL
SETTLE_S = 5.0  # before looking for containers whose creation was under way


class HarborRunError(Exception):
    pass


class RunLocked(HarborRunError):
    """Another harness process is supervising this run."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def read_state(run_dir: Path) -> dict[str, Any]:
    return json.loads((Path(run_dir) / STATE).read_text())


def _update_state(run_dir: Path, **changes: Any) -> dict[str, Any]:
    state = read_state(run_dir) if (run_dir / STATE).is_file() else {}
    state.update(changes)
    state["updated_at"] = _now()
    _write_json(run_dir / STATE, state)
    return state


# --- process identity ---------------------------------------------------------------------


def _boot_id() -> str | None:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None


def process_identity(pid: int) -> dict[str, Any] | None:
    """What makes a process unique on this host: pid, its start time in clock ticks since
    boot, and the boot. None if no such process."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return None
    fields = stat[stat.rindex(")") + 2 :].split()
    if fields[0] == "Z":
        return None  # exited, not yet reaped
    return {
        "pid": pid,
        "start_ticks": int(fields[19]),
        "boot_id": _boot_id(),
        "host": socket.gethostname(),
        "argv0": cmdline[0].decode(errors="replace") if cmdline else "",
    }


def is_same_process(recorded: dict[str, Any] | None) -> bool:
    """True only if the recorded process is still running, i.e. not a reused pid."""
    if not recorded or recorded.get("host") != socket.gethostname():
        return False
    now = process_identity(int(recorded["pid"]))
    return bool(
        now
        and now["start_ticks"] == recorded["start_ticks"]
        and now["boot_id"] == recorded["boot_id"]
    )


# --- lock ---------------------------------------------------------------------------------


@contextmanager
def run_lock(run_dir: Path):
    """Exclusive while a harness process supervises the run; released when it exits, also
    when it crashes."""
    (run_dir / LOCAL).mkdir(parents=True, exist_ok=True)
    fd = os.open(run_dir / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RunLocked(f"{run_dir} is being supervised by another harness process") from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)


def is_supervised(run_dir: Path) -> bool:
    try:
        with run_lock(run_dir):
            return False
    except RunLocked:
        return True


# --- containers ---------------------------------------------------------------------------


def _compose_name(name: str) -> str:
    # the pinned release's _sanitize_docker_compose_project_name
    name = name.lower()
    if not re.match(r"^[a-z0-9]", name):
        name = "0" + name
    return re.sub(r"[^a-z0-9_-]", "-", name)


def owned_project_prefixes(job_dir: Path) -> list[str]:
    """Compose project prefixes of this job's trials (``<trial>__env``,
    ``<trial>__verifier__...``); trial directories are created before their environment."""
    if not job_dir.is_dir():
        return []
    return sorted(
        f"{_compose_name(d.name)}__"
        for d in job_dir.iterdir()
        if d.is_dir() and not d.is_symlink() and "__" in d.name
    )


def _docker(args: list[str], timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DOCKER, *args], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
    )


def cleanup_owned(job_dir: Path) -> dict[str, Any]:
    """Remove containers and networks of this job's compose projects; report what was done."""
    prefixes = owned_project_prefixes(job_dir)
    report: dict[str, Any] = {"prefixes": prefixes, "containers": [], "networks": [], "errors": []}
    if not prefixes:
        return report
    fmt = '{{.ID}} {{.Label "com.docker.compose.project"}}'
    label = "label=com.docker.compose.project"
    for kind, listing, remove in (
        ("containers", ["ps", "-a", "--filter", label, "--format", fmt], ["rm", "-f"]),
        ("networks", ["network", "ls", "--filter", label, "--format", fmt], ["network", "rm"]),
    ):
        try:
            out = _docker(listing)
        except (OSError, subprocess.TimeoutExpired) as exc:
            report["errors"].append(f"list {kind}: {exc}")
            continue
        if out.returncode != 0:
            report["errors"].append(f"list {kind}: {out.stderr.strip()[-300:]}")
            continue
        for line in out.stdout.splitlines():
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or not any(parts[1].startswith(p) for p in prefixes):
                continue
            try:
                res = _docker([*remove, parts[0]])
            except (OSError, subprocess.TimeoutExpired) as exc:
                report["errors"].append(f"remove {parts[0]}: {exc}")
                continue
            if res.returncode == 0:
                report[kind].append({"id": parts[0], "project": parts[1]})
            else:
                report["errors"].append(f"remove {parts[0]}: {res.stderr.strip()[-300:]}")
    return report


def leftover_owned(job_dir: Path) -> list[str] | None:
    """Containers of this job still present; None if Docker could not be asked."""
    prefixes = owned_project_prefixes(job_dir)
    if not prefixes:
        return []
    try:
        out = _docker(["ps", "-a", "--filter", "label=com.docker.compose.project", "--format",
                       '{{.ID}} {{.Label "com.docker.compose.project"}}'])  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return [
        line.split()[0]
        for line in out.stdout.splitlines()
        if len(line.split(maxsplit=1)) == 2
        and any(line.split(maxsplit=1)[1].startswith(p) for p in prefixes)
    ]


# --- preparation --------------------------------------------------------------------------


def new_run_name(cfg: HarborRunConfig) -> str:
    task = cfg.task.terminal_bench or Path(str(cfg.task.path)).name
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return re.sub(
        r"[^A-Za-z0-9._-]+", "-", f"{task}-{cfg.agent.name}-{stamp}-{secrets.token_hex(2)}"
    )


def agent_env(cfg: HarborRunConfig) -> dict[str, str]:
    missing = [n for n in cfg.agent.env if n not in os.environ]
    if missing:
        raise HarborRunError(f"agent environment variables not set: {', '.join(missing)}")
    return {n: os.environ[n] for n in cfg.agent.env}


def prepare(
    cfg: HarborRunConfig, root: Path, config_path: Path, *, print_config_timeout: float = 120
) -> Path:
    """Check everything, write the receipt and the initial state; returns the run directory.
    Nothing is started that would call a model or create a container."""
    runtime = probe(cfg.harbor.executable)
    env = {**base_env(), **agent_env(cfg)}
    task = resolve_task(cfg, root)
    out_root = cfg.output_dir if cfg.output_dir.is_absolute() else root / cfg.output_dir
    name = new_run_name(cfg)
    run_dir = out_root / name
    (run_dir / LOCAL).mkdir(parents=True)
    os.chmod(run_dir / LOCAL, 0o700)
    jobs_dir = (run_dir / JOBS).absolute()
    argv = build_argv(cfg, root, runtime.executable, task["path"], jobs_dir, name)
    resolved = subprocess.run(
        [*argv, "--print-config"],
        capture_output=True,
        text=True,
        timeout=print_config_timeout,
        env=env,
        stdin=subprocess.DEVNULL,
        cwd=run_dir,
    )
    if resolved.returncode != 0:
        raise HarborRunError(
            f"harbor --print-config failed ({resolved.returncode}): {resolved.stderr[-1000:]}"
        )
    try:
        job_config = json.loads(resolved.stdout)
    except ValueError:
        job_config = {"unparsed": resolved.stdout[-4000:]}
    job_config, _ = importer._redact_json(job_config, importer._env_secrets())
    receipt = {
        "schema_version": 1,
        "run_id": name,
        "created_at": _now(),
        "config_path": str(config_path),
        "project_root": str(root),
        "config": cfg.model_dump(mode="json"),
        "harbor": runtime.describe(),
        "task": task,
        "argv": argv,
        "agent_env_names": list(cfg.agent.env),
        "capabilities": capabilities(cfg),
        "resolved_job_config": job_config,
        "job_dir": f"{JOBS}/{name}",
    }
    _write_json(run_dir / RECEIPT, receipt)
    _update_state(run_dir, status="prepared", created_at=_now(), runs=[])
    return run_dir


# --- supervision --------------------------------------------------------------------------


def _stop_group(pgid: int, proc: subprocess.Popen | None, grace_s: float) -> str:
    """SIGTERM, then SIGKILL after ``grace_s``. Returns how the group ended."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return "already exited"
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            break
        if proc is None:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                break
        time.sleep(0.2)
    try:
        os.killpg(pgid, signal.SIGKILL)  # stragglers of the group, or Harbor itself
        if proc is not None:
            proc.wait(timeout=10)
        return (
            "killed after grace period" if (proc is None or proc.returncode == -9) else "terminated"
        )
    except ProcessLookupError:
        return "terminated"


def supervise(
    run_dir: Path,
    argv: list[str],
    env: dict[str, str],
    *,
    wall_clock_s: float,
    kind: str = "run",
    grace_s: float | None = None,
    settle_s: float | None = None,
    poll_s: float = 1.0,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start Harbor, wait for it while enforcing the wall clock and cancel requests, then
    clean up this job's resources and import its records. Returns the final state."""
    run_dir = Path(run_dir)
    grace_s = GRACE_S if grace_s is None else grace_s
    settle_s = SETTLE_S if settle_s is None else settle_s
    receipt = json.loads((run_dir / RECEIPT).read_text())
    job_dir = run_dir / receipt["job_dir"]
    with run_lock(run_dir):
        (run_dir / CANCEL).unlink(missing_ok=True)
        log = open(run_dir / LOG, "ab")  # noqa: SIM115
        log.write(f"\n=== {kind} {_now()} ===\n".encode())
        log.flush()
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                argv,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=run_dir,
                start_new_session=True,
            )
        except OSError as exc:
            log.close()
            return _update_state(run_dir, status="start_failed", error=str(exc))
        ident = process_identity(proc.pid) or {"pid": proc.pid}
        ident["pgid"] = proc.pid
        _write_json(run_dir / PROCESS, ident)
        os.chmod(run_dir / PROCESS, 0o600)
        entry = {"kind": kind, "started_at": _now(), "argv": argv}
        state = read_state(run_dir)
        runs = [*state.get("runs", []), entry]
        _update_state(run_dir, status="running", runs=runs, stop_reason=None, exit_code=None)

        stop_reason = None
        interrupted = []
        previous = {
            s: signal.signal(s, lambda signum, _f: interrupted.append(signum))
            for s in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if interrupted:
                    stop_reason = "cancelled"
                elif (run_dir / CANCEL).exists():
                    stop_reason = "cancelled"
                elif elapsed > wall_clock_s:
                    stop_reason = "timed_out"
                elif (run_dir / LOG).stat().st_size > MAX_LOG_BYTES:
                    stop_reason = "log_limit"
                if stop_reason:
                    _update_state(run_dir, status="stopping", stop_reason=stop_reason)
                    entry["stop"] = _stop_group(proc.pid, proc, grace_s)
                    break
                if on_tick is not None:
                    on_tick({"elapsed_s": elapsed})
                time.sleep(poll_s)
            proc.wait()
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)
            log.close()
        # whatever Harbor left in its group (e.g. docker compose children)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        entry.update(finished_at=_now(), exit_code=proc.returncode,
                     elapsed_s=round(time.monotonic() - started, 1))  # fmt: skip
        cleanup = cleanup_owned(job_dir)
        if settle_s:
            time.sleep(settle_s)  # a container whose creation was already under way
        late = cleanup_owned(job_dir)
        cleanup["late"] = {k: late[k] for k in ("containers", "networks", "errors")}
        cleanup["leftover"] = leftover_owned(job_dir)
        entry["cleanup"] = cleanup
        status = _final_status(stop_reason, proc.returncode, job_dir)
        state = _update_state(
            run_dir, status=status, stop_reason=stop_reason, exit_code=proc.returncode,
            runs=runs, finished_at=_now(),
        )  # fmt: skip
        state = _import(run_dir, job_dir, state)
    return state


def _final_status(stop_reason: str | None, exit_code: int, job_dir: Path) -> str:
    if stop_reason in ("cancelled",):
        return "cancelled"
    if stop_reason in ("timed_out", "log_limit"):
        return "timed_out"
    if not (job_dir / "lock.json").is_file():
        return "failed"
    if exit_code != 0:
        return "failed"
    return "completed" if (job_dir / "result.json").is_file() else "interrupted"


def _import(run_dir: Path, job_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Map whatever trials exist, also after a failure, and attach receipt and state."""
    if not (job_dir / "lock.json").is_file():
        return _update_state(run_dir, imported=False, import_error="Harbor wrote no job")
    try:
        importer.import_job(job_dir, run_dir=run_dir, copy=False, replace=True)
    except importer.HarborImportError as exc:
        return _update_state(run_dir, imported=False, import_error=str(exc))
    record_path = run_dir / importer.RUN_RECORD
    record = json.loads(record_path.read_text())
    receipt = json.loads((run_dir / RECEIPT).read_text())
    record["run"] = {k: receipt[k] for k in ("config", "task", "harbor", "argv", "created_at")}
    state = _update_state(run_dir, imported=True, import_error=None)
    record["state"] = {k: state.get(k) for k in ("status", "stop_reason", "exit_code", "runs")}
    _write_json(record_path, record)
    return state


# --- cancel, status, resume from another harness process ----------------------------------


def status(run_dir: Path) -> dict[str, Any]:
    """The recorded state, corrected by what can be checked now."""
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    proc = json.loads((run_dir / PROCESS).read_text()) if (run_dir / PROCESS).is_file() else None
    alive = is_same_process(proc)
    supervised = is_supervised(run_dir)
    state["harbor_process_alive"] = alive
    state["supervised"] = supervised
    if state.get("status") in ("running", "stopping") and not supervised:
        state["status"] = "orphaned" if alive else "interrupted"
        state["note"] = "the supervising harness process is gone" + (
            "; Harbor is still running (harness harbor cancel stops it)" if alive else ""
        )
    receipt = json.loads((run_dir / RECEIPT).read_text())
    job_dir = run_dir / receipt["job_dir"]
    state["trial_dirs"] = len(owned_project_prefixes(job_dir))
    if (job_dir / "result.json").is_file():
        try:
            stats = json.loads((job_dir / "result.json").read_text()).get("stats") or {}
            state["harbor_stats"] = {k: v for k, v in stats.items() if k.startswith("n_")}
        except ValueError:
            pass
    return state


def cancel(run_dir: Path, *, wait_s: float = 120.0, grace_s: float | None = None) -> dict[str, Any]:
    """Ask the supervisor to stop; if there is none, stop the recorded Harbor process group
    ourselves, but only after proving it is the same process."""
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    if state.get("status") in TERMINAL and not is_supervised(run_dir):
        return {**state, "cancel": "nothing to cancel"}
    (run_dir / LOCAL).mkdir(exist_ok=True)
    (run_dir / CANCEL).write_text(_now())
    deadline = time.monotonic() + wait_s
    while is_supervised(run_dir) and time.monotonic() < deadline:
        time.sleep(0.5)
    if is_supervised(run_dir):
        return {**read_state(run_dir), "cancel": "requested; supervisor still stopping"}
    if read_state(run_dir).get("status") in TERMINAL:
        return {**read_state(run_dir), "cancel": "stopped by supervisor"}
    # no supervisor: act ourselves, under the lock so no resume starts meanwhile
    with run_lock(run_dir):
        proc = (
            json.loads((run_dir / PROCESS).read_text()) if (run_dir / PROCESS).is_file() else None
        )
        how = "harbor process already gone"
        if is_same_process(proc):
            how = _stop_group(int(proc["pgid"]), None, GRACE_S if grace_s is None else grace_s)
        elif proc:
            how = "recorded pid no longer belongs to this run; not signalled"
        receipt = json.loads((run_dir / RECEIPT).read_text())
        job_dir = run_dir / receipt["job_dir"]
        cleanup = cleanup_owned(job_dir)
        cleanup["leftover"] = leftover_owned(job_dir)
        state = _update_state(
            run_dir, status="cancelled", stop_reason="cancelled", finished_at=_now(),
            cancel={"how": how, "cleanup": cleanup},
        )  # fmt: skip
        state = _import(run_dir, run_dir / receipt["job_dir"], state)
    return {**state, "cancel": how}


def resume_argv(run_dir: Path) -> tuple[list[str], Path]:
    receipt = json.loads((run_dir / RECEIPT).read_text())
    job_dir = (run_dir / receipt["job_dir"]).absolute()
    if not (job_dir / "config.json").is_file():
        raise HarborRunError(f"{job_dir} has no Harbor job to resume")
    runtime = probe(receipt["config"]["harbor"].get("executable"))
    if runtime.executable != receipt["harbor"]["executable"]:
        raise HarborRunError(
            f"Harbor executable changed: run used {receipt['harbor']['executable']}, "
            f"now {runtime.executable}"
        )
    return [runtime.executable, "job", "resume", "-p", str(job_dir)], job_dir
