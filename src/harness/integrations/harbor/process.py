"""Running Harbor as a separate process: start, supervise, stop, clean up, resume.

A run directory holds

    run.json             shareable receipt: requested and resolved configuration, argv, Harbor
                         identity, capabilities (no credential values)
    state.json           lifecycle: status, timestamps, exit code, stop reason, cleanup, timing
    harbor.log           Harbor's stdout and stderr, redacted
    raw/, raw_index.json the job's records, redacted, with original and stored hashes
    trials.jsonl, harbor_run.json   the mapped records (see importer)
    local/               private (0700), never shared:
      job/<job_name>/    Harbor's own job directory, as Harbor wrote it
      harbor.raw.log     unredacted output
      process.json       process identity (pid, start time, boot); proves a process is ours
      lock               held (flock) by the supervising harness process for its lifetime
      cancel             a pending cancel request (its time in ns)

Harbor runs in its own process group. On a stop (wall clock, cancel, Ctrl-C) the group gets
SIGTERM, which Harbor 0.25.0 turns into a KeyboardInterrupt and handles by cancelling its
trials; after a grace period the group is killed. Then this job's own containers and networks
are removed: those whose compose project is named after one of the job's trial directories.
Nothing else is touched. Cleanup has one deadline; what it cannot confirm is reported as
unknown. The Harbor exit code is recorded but does not decide success; the trial records do.
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
import sys
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
RAW_LOG = "local/harbor.raw.log"
PROCESS = "local/process.json"
LOCK = "local/lock"
CANCEL = "local/cancel"
JOBS = "local/job"
MAX_LOG_BYTES = 200 * 1024 * 1024
TERMINAL = {"completed", "failed", "cancelled", "timed_out", "interrupted", "start_failed"}
RESUMABLE = {"cancelled", "interrupted", "timed_out", "failed"}
DOCKER = "docker"
GRACE_S = 60.0  # after SIGTERM, before SIGKILL
SETTLE_S = 5.0  # before looking for containers whose creation was under way
CLEANUP_S = 120.0  # one deadline for all Docker calls of a cleanup
# "proc" (Linux /proc), "ps" (portable), or None to choose by platform
IDENTITY_METHOD: str | None = None


class HarborRunError(Exception):
    pass


class RunLocked(HarborRunError):
    """Another harness process is supervising this run."""


class IdentityUnknown(HarborRunError):
    """Whether a process is the recorded one cannot be determined on this host."""


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


def _receipt(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / RECEIPT).read_text())


def _recorded_process(run_dir: Path) -> dict[str, Any] | None:
    path = run_dir / PROCESS
    return json.loads(path.read_text()) if path.is_file() else None


# --- process identity ---------------------------------------------------------------------


def _method() -> str:
    if IDENTITY_METHOD:
        return IDENTITY_METHOD
    return "proc" if Path("/proc/self/stat").is_file() else "ps"


def _boot_marker() -> str | None:
    """Changes on every boot, so a start time is never compared across reboots."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        pass
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                                 text=True, timeout=5)  # fmt: skip
        except (OSError, subprocess.TimeoutExpired):
            return None
        m = re.search(r"sec\s*=\s*(\d+)", out.stdout)
        return m.group(1) if m else None
    return None


def _identity_proc(pid: int) -> dict[str, Any] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise IdentityUnknown(f"cannot read /proc/{pid}/stat: {exc}") from None
    fields = stat[stat.rindex(")") + 2 :].split()
    if fields[0] == "Z":
        return None  # exited, not yet reaped
    return {"start": fields[19]}


def _identity_ps(pid: int) -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=,stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "LC_ALL": "C", "TZ": "UTC"},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IdentityUnknown(f"ps failed: {exc}") from None
    line = out.stdout.strip()
    if not line:
        if out.returncode in (0, 1):
            return None  # no such process
        raise IdentityUnknown(f"ps failed ({out.returncode}): {out.stderr.strip()[-200:]}")
    parts = line.split()
    if len(parts) < 6:
        raise IdentityUnknown(f"unexpected ps output {line!r}")
    if parts[5].startswith("Z"):
        return None
    return {"start": " ".join(parts[:5])}


def process_identity(pid: int, method: str | None = None) -> dict[str, Any] | None:
    """What makes a process unique on this host: pid, its start time and the boot. None if
    there is no such (live) process; IdentityUnknown if that cannot be determined."""
    method = method or _method()
    found = _identity_proc(pid) if method == "proc" else _identity_ps(pid)
    if found is None:
        return None
    return {"pid": pid, "method": method, "start": found["start"], "boot": _boot_marker(),
            "host": socket.gethostname()}  # fmt: skip


def process_state(recorded: dict[str, Any] | None) -> str:
    """none (never started), alive, gone, or unknown: the recorded process cannot be proven
    to be running or to have ended. A pid now held by another process means gone."""
    if recorded is None:
        return "none"
    if not recorded.get("start") or not recorded.get("method"):
        return "unknown"
    if recorded.get("host") != socket.gethostname():
        return "unknown"
    try:
        now = process_identity(int(recorded["pid"]), recorded["method"])
    except IdentityUnknown:
        return "unknown"
    if now is None:
        return "gone"
    if now["start"] != recorded["start"] or now["boot"] != recorded.get("boot"):
        return "gone"
    return "alive"


def is_same_process(recorded: dict[str, Any] | None) -> bool:
    return process_state(recorded) == "alive"


# --- lock and cancel requests -------------------------------------------------------------


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


def request_cancel(run_dir: Path) -> int:
    """Persist a cancel request; it stops whatever runs or is about to start after it."""
    (run_dir / LOCAL).mkdir(parents=True, exist_ok=True)
    at = time.time_ns()
    tmp = run_dir / LOCAL / f".cancel.{os.getpid()}.tmp"
    tmp.write_text(str(at))
    os.replace(tmp, run_dir / CANCEL)
    return at


def cancel_requested_at(run_dir: Path) -> int | None:
    try:
        text = (run_dir / CANCEL).read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return int(text)
    except ValueError:
        return time.time_ns()  # unreadable: treat as just requested


# --- containers ---------------------------------------------------------------------------


class _Deadline:
    def __init__(self, seconds: float):
        self.end = time.monotonic() + seconds

    def remaining(self) -> float:
        return self.end - time.monotonic()


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


def _docker(args: list[str], deadline: _Deadline) -> subprocess.CompletedProcess:
    left = deadline.remaining()
    if left <= 0:
        raise TimeoutError("cleanup deadline reached")
    return subprocess.run(
        [DOCKER, *args], capture_output=True, text=True, timeout=min(left, 60.0),
        stdin=subprocess.DEVNULL,
    )  # fmt: skip


_FMT = '{{.ID}} {{.Label "com.docker.compose.project"}}'
_LABEL = "label=com.docker.compose.project"


def _owned_lines(out: str, prefixes: list[str]) -> list[tuple[str, str]]:
    found = []
    for line in out.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and any(parts[1].startswith(p) for p in prefixes):
            found.append((parts[0], parts[1]))
    return found


def cleanup_owned(job_dir: Path, deadline: _Deadline) -> dict[str, Any]:
    """Remove containers and networks of this job's compose projects; report what was done."""
    prefixes = owned_project_prefixes(job_dir)
    report: dict[str, Any] = {"prefixes": prefixes, "containers": [], "networks": [], "errors": []}
    if not prefixes:
        return report
    for kind, listing, remove in (
        ("containers", ["ps", "-a", "--filter", _LABEL, "--format", _FMT], ["rm", "-f"]),
        ("networks", ["network", "ls", "--filter", _LABEL, "--format", _FMT], ["network", "rm"]),
    ):
        try:
            out = _docker(listing, deadline)
        except (OSError, subprocess.TimeoutExpired, TimeoutError) as exc:
            report["errors"].append(f"list {kind}: {exc}")
            continue
        if out.returncode != 0:
            report["errors"].append(f"list {kind}: {out.stderr.strip()[-300:]}")
            continue
        for rid, project in _owned_lines(out.stdout, prefixes):
            try:
                res = _docker([*remove, rid], deadline)
            except (OSError, subprocess.TimeoutExpired, TimeoutError) as exc:
                report["errors"].append(f"remove {rid}: {exc}")
                continue
            if res.returncode == 0:
                report[kind].append({"id": rid, "project": project})
            else:
                report["errors"].append(f"remove {rid}: {res.stderr.strip()[-300:]}")
    return report


def leftover_owned(job_dir: Path, deadline: _Deadline) -> list[str] | None:
    """Containers of this job still present; None if Docker could not be asked in time."""
    prefixes = owned_project_prefixes(job_dir)
    if not prefixes:
        return []
    try:
        out = _docker(["ps", "-a", "--filter", _LABEL, "--format", _FMT], deadline)
    except (OSError, subprocess.TimeoutExpired, TimeoutError):
        return None
    if out.returncode != 0:
        return None
    return [rid for rid, _ in _owned_lines(out.stdout, prefixes)]


def full_cleanup(job_dir: Path, budget_s: float, settle_s: float) -> dict[str, Any]:
    """Remove, wait for late creations, remove again, then check; all within ``budget_s``."""
    started = time.monotonic()
    deadline = _Deadline(budget_s)
    cleanup = cleanup_owned(job_dir, deadline)
    if settle_s:
        time.sleep(max(0.0, min(settle_s, deadline.remaining())))
    late = cleanup_owned(job_dir, deadline)
    cleanup["late"] = {k: late[k] for k in ("containers", "networks", "errors")}
    cleanup["leftover"] = leftover_owned(job_dir, deadline)
    cleanup["budget_s"] = budget_s
    cleanup["elapsed_s"] = round(time.monotonic() - started, 2)
    cleanup["confirmed"] = cleanup["leftover"] == []
    return cleanup


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


def secrets_of(receipt: dict[str, Any]) -> list[str]:
    """Values to redact from this run's output: those handed to Harbor (when this process
    can see them) and credential-named environment values."""
    names = receipt.get("agent_env_names") or []
    return importer.all_secrets(os.environ[n] for n in names if n in os.environ)


def prepare(cfg: HarborRunConfig, root: Path, config_path: Path) -> Path:
    """Check everything, write the receipt and the initial state; returns the run directory.
    Nothing is started that would call a model or create a container."""
    runtime = probe(cfg.harbor.executable)
    passed = agent_env(cfg)
    env = {**base_env(), **passed}
    redact = importer.all_secrets(passed.values())
    task = resolve_task(cfg, root)
    out_root = cfg.output_dir if cfg.output_dir.is_absolute() else root / cfg.output_dir
    name = new_run_name(cfg)
    run_dir = out_root / name
    (run_dir / LOCAL).mkdir(parents=True)
    os.chmod(run_dir / LOCAL, 0o700)
    _update_state(run_dir, status="preparing", created_at=_now(), prepared_ns=time.time_ns(),
                  runs=[])  # fmt: skip
    jobs_dir = (run_dir / JOBS).absolute()
    argv = build_argv(cfg, root, runtime.executable, task["path"], jobs_dir, name)
    started = time.monotonic()
    try:
        resolved = subprocess.run(
            [*argv, "--print-config"],
            capture_output=True,
            text=True,
            timeout=cfg.limits.prepare_s,
            env=env,
            stdin=subprocess.DEVNULL,
            cwd=run_dir,
        )
    except subprocess.TimeoutExpired:
        _update_state(run_dir, status="start_failed", error="--print-config timed out")
        raise HarborRunError(
            f"harbor --print-config did not finish in {cfg.limits.prepare_s}s"
        ) from None
    if resolved.returncode != 0:
        err = importer.redact_text(resolved.stderr[-1000:], redact)[0]
        _update_state(run_dir, status="start_failed", error=err)
        raise HarborRunError(f"harbor --print-config failed ({resolved.returncode}): {err}")
    try:
        job_config = json.loads(resolved.stdout)
    except ValueError:
        job_config = {"unparsed": resolved.stdout[-4000:]}
    job_config, _ = importer._redact_json(job_config, redact)
    receipt = {
        "schema_version": 2,
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
        "prepare_s": round(time.monotonic() - started, 2),
        "job_dir": f"{JOBS}/{name}",
    }
    _write_json(run_dir / RECEIPT, receipt)
    if cancel_requested_at(run_dir) is None:
        _update_state(run_dir, status="prepared")
    else:
        _update_state(run_dir, status="cancelled", stop_reason="cancelled", finished_at=_now(),
                      note="cancelled before Harbor started")  # fmt: skip
    return run_dir


# --- supervision --------------------------------------------------------------------------


def group_members(pgid: int) -> list[int] | None:
    """Live (not zombie) processes of a process group; None if ``ps`` cannot tell."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    members = []
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == str(pgid) and not parts[2].startswith("Z"):
            members.append(int(parts[0]))
    return members


def group_state(pgid: int) -> str:
    """alive, gone or unknown. A signal probe alone is not enough: on macOS a group whose only
    members are unreaped zombies answers EPERM, as does a live group of another user; Linux
    accepts signals for zombies. The member list decides; without it EPERM is unknown."""
    try:
        os.killpg(pgid, 0)
        probe = "ok"
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        probe = "denied"
    members = group_members(pgid)
    if members is None:
        return "alive" if probe == "ok" else "unknown"
    return "alive" if members else "gone"


def _signal_group(pgid: int, sig: int) -> str:
    try:
        os.killpg(pgid, sig)
        return "sent"
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "denied"


KILL_WAIT_S = 5.0  # after SIGKILL, for the group to disappear


def _stop_group(pgid: int, proc: subprocess.Popen | None, grace_s: float) -> str:
    """SIGTERM, then SIGKILL after ``grace_s``. Returns how the group ended; never raises for
    a group that is gone or cannot be signalled (the caller checks ``group_state``)."""

    def ended() -> bool:
        if proc is not None:
            return proc.poll() is not None
        return group_state(pgid) == "gone"

    sent = _signal_group(pgid, signal.SIGTERM)
    if sent == "gone" or (sent == "denied" and ended()):
        return "already exited"
    deadline = time.monotonic() + grace_s
    exited = False
    while time.monotonic() < deadline:
        if ended():
            exited = True
            break
        time.sleep(0.2)
    killed = _signal_group(pgid, signal.SIGKILL)  # stragglers of the group, or Harbor itself
    if proc is not None:
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return "still running after SIGKILL"
        return "killed after grace period" if proc.returncode == -9 else "terminated"
    if exited:
        return "terminated"
    kill_deadline = time.monotonic() + KILL_WAIT_S
    while time.monotonic() < kill_deadline:
        if ended():
            return "killed after grace period"
        time.sleep(0.2)
    return f"still running after SIGKILL ({'permission denied' if killed == 'denied' else killed})"


def _check_startable(run_dir: Path, kind: str, requested_ns: int) -> str | None:
    """Under the lock: why this start must not happen, or None. Records a cancel that came
    after the request; drops an older one, which this explicit start supersedes."""
    state = read_state(run_dir)
    at = cancel_requested_at(run_dir)
    if at is not None and at >= requested_ns:
        return "cancelled before Harbor started"
    current = state.get("status")
    if kind == "run" and current != "prepared":
        return f"run is {current}, not prepared"
    previous = process_state(_recorded_process(run_dir))
    if previous in ("alive", "unknown"):
        return f"the previous Harbor process is {previous}; not starting another on this job"
    if current in ("running", "stopping"):
        current = "interrupted"  # the lock is ours, so its supervisor is gone, and so is Harbor
    if kind == "resume" and current not in RESUMABLE:
        return f"run is {current}; only {sorted(RESUMABLE)} can be resumed"
    if at is not None:
        (run_dir / CANCEL).unlink(missing_ok=True)
        _update_state(run_dir, superseded_cancel_ns=at)
    return None


def supervise(
    run_dir: Path,
    argv: list[str],
    env: dict[str, str],
    *,
    wall_clock_s: float,
    kind: str = "run",
    requested_ns: int | None = None,
    grace_s: float | None = None,
    settle_s: float | None = None,
    cleanup_s: float | None = None,
    poll_s: float = 1.0,
    on_tick: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Start Harbor, wait for it while enforcing the wall clock and cancel requests, then
    clean up this job's resources and import its records. Returns the final state.

    ``requested_ns`` is when the user asked for this start (default: when the run was
    prepared); a cancel request made after it prevents the start.
    """
    run_dir = Path(run_dir)
    grace_s = GRACE_S if grace_s is None else grace_s
    settle_s = SETTLE_S if settle_s is None else settle_s
    cleanup_s = CLEANUP_S if cleanup_s is None else cleanup_s
    receipt = _receipt(run_dir)
    job_dir = run_dir / receipt["job_dir"]
    redact = importer.all_secrets(env.get(n, "") for n in receipt.get("agent_env_names") or [])
    with run_lock(run_dir):
        if requested_ns is None:
            requested_ns = read_state(run_dir).get("prepared_ns") or 0
        refusal = _check_startable(run_dir, kind, requested_ns)
        if refusal == "cancelled before Harbor started":
            return _update_state(run_dir, status="cancelled", stop_reason="cancelled",
                                 note=refusal, finished_at=_now())  # fmt: skip
        if refusal:
            raise HarborRunError(refusal)
        log = open(run_dir / RAW_LOG, "ab")  # noqa: SIM115
        os.chmod(run_dir / RAW_LOG, 0o600)
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
        try:
            ident = process_identity(proc.pid) or {
                "pid": proc.pid,
                "method": _method(),
                "start": None,
                "note": "exited at once",
            }
        except IdentityUnknown as exc:
            ident = {"pid": proc.pid, "method": _method(), "start": None, "note": str(exc)}
        ident["pgid"] = proc.pid
        _write_json(run_dir / PROCESS, ident)
        os.chmod(run_dir / PROCESS, 0o600)
        entry: dict[str, Any] = {"kind": kind, "started_at": _now(), "argv": argv}
        runs = [*read_state(run_dir).get("runs", []), entry]
        _update_state(run_dir, status="running", runs=runs, stop_reason=None, exit_code=None)

        stop_reason = None
        interrupted: list[int] = []
        previous = {
            s: signal.signal(s, lambda signum, _f: interrupted.append(signum))
            for s in (signal.SIGINT, signal.SIGTERM)
        }
        stop_started = None
        try:
            while proc.poll() is None:
                elapsed = time.monotonic() - started
                if interrupted or (run_dir / CANCEL).exists():
                    stop_reason = "cancelled"
                elif elapsed > wall_clock_s:
                    stop_reason = "timed_out"
                elif (run_dir / RAW_LOG).stat().st_size > MAX_LOG_BYTES:
                    stop_reason = "log_limit"
                if stop_reason:
                    _update_state(run_dir, status="stopping", stop_reason=stop_reason)
                    stop_started = time.monotonic()
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
        ended = time.monotonic()
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # whatever Harbor left in its group
        except ProcessLookupError:
            pass
        entry.update(finished_at=_now(), exit_code=proc.returncode)
        entry["cleanup"] = full_cleanup(job_dir, cleanup_s, settle_s)
        entry["timing_s"] = {
            "harbor": round((stop_started or ended) - started, 2),
            "stop": round(ended - stop_started, 2) if stop_started else 0.0,
            "cleanup": entry["cleanup"]["elapsed_s"],
        }
        status = _final_status(stop_reason, proc.returncode, job_dir)
        _update_state(
            run_dir, status=status, stop_reason=stop_reason, exit_code=proc.returncode,
            runs=runs, finished_at=_now(),
        )  # fmt: skip
        return _publish(run_dir, job_dir, redact)


def _final_status(stop_reason: str | None, exit_code: int, job_dir: Path) -> str:
    if stop_reason == "cancelled":
        return "cancelled"
    if stop_reason in ("timed_out", "log_limit"):
        return "timed_out"
    if not (job_dir / "lock.json").is_file():
        return "failed"
    if exit_code != 0:
        return "failed"
    return "completed" if (job_dir / "result.json").is_file() else "interrupted"


def _publish_log(run_dir: Path, redact: list[str]) -> dict[str, Any] | None:
    raw_path = run_dir / RAW_LOG
    if not raw_path.is_file():
        return None
    import hashlib

    data = raw_path.read_bytes()
    stored, n = importer.redact_bytes("harbor.log", data, redact)
    tmp = run_dir / f".{LOG}.tmp"
    tmp.write_bytes(stored)
    os.replace(tmp, run_dir / LOG)
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "stored_sha256": hashlib.sha256(stored).hexdigest(), "redacted": n}  # fmt: skip


def _publish(run_dir: Path, job_dir: Path, redact: list[str]) -> dict[str, Any]:
    """Redacted log and a redacted snapshot of the job's records, also after a failure; the
    unredacted originals stay under local/."""
    _update_state(run_dir, log=_publish_log(run_dir, redact))
    if not (job_dir / "lock.json").is_file():
        return _update_state(run_dir, imported=False, import_error="Harbor wrote no job")
    try:
        importer.import_job(job_dir, run_dir=run_dir, replace=True, secrets=redact)
    except importer.HarborImportError as exc:
        return _update_state(run_dir, imported=False, import_error=str(exc))
    record_path = run_dir / importer.RUN_RECORD
    record = json.loads(record_path.read_text())
    receipt = _receipt(run_dir)
    record["source"]["job_dir"] = receipt["job_dir"]
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
    proc_state = process_state(_recorded_process(run_dir))
    supervised = is_supervised(run_dir)
    state["harbor_process"] = proc_state
    state["supervised"] = supervised
    if state.get("status") in ("running", "stopping") and not supervised:
        state["status"] = {"alive": "orphaned", "unknown": "unknown"}.get(proc_state, "interrupted")
        state["note"] = "the supervising harness process is gone" + {
            "alive": "; Harbor is still running (harness harbor cancel stops it)",
            "unknown": "; whether Harbor still runs cannot be checked on this host, so cancel "
            "and resume are refused",
        }.get(proc_state, "")
    if cancel_requested_at(run_dir) is not None and state.get("status") not in TERMINAL:
        state["cancel_pending"] = True
    receipt = _receipt(run_dir) if (run_dir / RECEIPT).is_file() else {}
    job_dir = run_dir / receipt.get("job_dir", JOBS)
    state["trial_dirs"] = len(owned_project_prefixes(job_dir))
    if (job_dir / "result.json").is_file():
        try:
            stats = json.loads((job_dir / "result.json").read_text()).get("stats") or {}
            state["harbor_stats"] = {k: v for k, v in stats.items() if k.startswith("n_")}
        except ValueError:
            pass
    return state


def cancel(
    run_dir: Path,
    *,
    wait_s: float = 120.0,
    grace_s: float | None = None,
    cleanup_s: float | None = None,
) -> dict[str, Any]:
    """Stop the run. A supervising process is asked to stop; without one, the recorded Harbor
    process group is stopped here, only after proving it is the same process. A run that has
    not started yet will not start."""
    run_dir = Path(run_dir)
    state = read_state(run_dir)
    if state.get("status") in TERMINAL and not is_supervised(run_dir):
        return {**state, "cancel": "nothing to cancel"}
    request_cancel(run_dir)
    deadline = time.monotonic() + wait_s
    while is_supervised(run_dir) and time.monotonic() < deadline:
        time.sleep(0.2)
    try:
        lock = run_lock(run_dir)
        lock.__enter__()
    except RunLocked:
        return {**read_state(run_dir), "cancel": "requested; the supervisor is still stopping"}
    try:
        state = read_state(run_dir)
        if state.get("status") in TERMINAL:
            return {**state, "cancel": "stopped by supervisor"}
        proc = _recorded_process(run_dir)
        proc_state = process_state(proc)
        if proc_state == "unknown":
            return {**state, "cancel": "refused: cannot verify the recorded Harbor process on "
                    "this host; nothing signalled, status unchanged, request kept"}  # fmt: skip
        how = {"none": "harbor was never started", "gone": "harbor process already gone"}.get(
            proc_state, ""
        )
        if proc_state == "alive":
            pgid = int(proc["pgid"])
            how = _stop_group(pgid, None, GRACE_S if grace_s is None else grace_s)
            group = group_state(pgid)
            if group != "gone":
                # Harbor may still own its containers: removing them under it would only race
                state = _update_state(run_dir, cancel={"how": how, "group": group, "at": _now()})
                why = (f"failed: Harbor's process group is {group} after {how}; containers "
                       "left to it, status unchanged, request kept")  # fmt: skip
                return {**state, "cancel": why}
        receipt = _receipt(run_dir) if (run_dir / RECEIPT).is_file() else {}
        job_dir = run_dir / receipt.get("job_dir", JOBS)
        cleanup = full_cleanup(job_dir, CLEANUP_S if cleanup_s is None else cleanup_s, 0.0)
        _update_state(
            run_dir, status="cancelled", stop_reason="cancelled", finished_at=_now(),
            cancel={"how": how, "cleanup": cleanup},
        )  # fmt: skip
        if receipt:
            state = _publish(run_dir, job_dir, secrets_of(receipt))
        else:
            state = read_state(run_dir)
        return {**state, "cancel": how}
    finally:
        lock.__exit__(None, None, None)


def resume_argv(run_dir: Path) -> tuple[list[str], Path]:
    receipt = _receipt(run_dir)
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
