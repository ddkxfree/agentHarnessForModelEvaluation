"""Offline import of a Harbor 0.25.0 job directory into a harness run directory.

The job directory is only read. Every file is treated as data: symbolic links are recorded but
never followed, special files and over-large files are not copied, nothing in it is executed
and no path inside it is trusted. A copy of the job ("raw/") is taken with credential-looking
values redacted, indexed with sizes and hashes, and checked against the source once more after
copying; a job that changed meanwhile does not produce a snapshot that claims to be complete.

The normalized records (``trials.jsonl``) and the run record (``harbor_run.json``) can always
be rebuilt from the snapshot. Importing the same job again is a no-op when nothing changed;
a changed job or a newer importer needs ``replace=True``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.integrations.harbor import JOB_LOCK_SCHEMA, PINNED_VERSION
from harness.integrations.harbor.mapping import MAPPING_VERSION, map_trial, number_repeats

IMPORTER_VERSION = 1
RUN_RECORD = "harbor_run.json"
TRIALS = "trials.jsonl"
INDEX = "raw_index.json"
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|authorization|access[_-]?token|auth[_-]?token"
    r"|^token$|^key$)"
)
_SECRET_TEXT = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
]
_SECRET_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)(\s*[=:]\s*[\"']?)([^\s\"',}]{8,})"
)
_ENV_SECRET_NAME = re.compile(r"(?i)(key|token|secret|password)")


class HarborImportError(Exception):
    """The directory cannot be imported as a Harbor job."""


class IncompatibleJob(HarborImportError):
    pass


class SourceChanged(HarborImportError):
    pass


class ImportConflict(HarborImportError):
    pass


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- redaction ----------------------------------------------------------------------------


def _env_secrets() -> list[str]:
    return sorted(
        {v for k, v in os.environ.items() if _ENV_SECRET_NAME.search(k) and len(v) >= 8},
        key=len,
        reverse=True,
    )


def redact_text(text: str, env_secrets: list[str]) -> tuple[str, int]:
    n = 0
    for value in env_secrets:
        if value in text:
            n += text.count(value)
            text = text.replace(value, REDACTED)
    for pattern in _SECRET_TEXT:
        text, k = pattern.subn(REDACTED, text)
        n += k
    text, k = _SECRET_ASSIGN.subn(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    return text, n + k


def _redact_json(value: Any, env_secrets: list[str]) -> tuple[Any, int]:
    if isinstance(value, dict):
        out, n = {}, 0
        for k, v in value.items():
            if (
                isinstance(k, str)
                and _SECRET_KEY.search(k)
                and not k.endswith("_name")
                and isinstance(v, str)
                and v
                and v != REDACTED
            ):
                out[k], n = REDACTED, n + 1
                continue
            out[k], m = _redact_json(v, env_secrets)
            n += m
        return out, n
    if isinstance(value, list):
        items = [_redact_json(v, env_secrets) for v in value]
        return [v for v, _ in items], sum(m for _, m in items)
    if isinstance(value, str):
        return redact_text(value, env_secrets)
    return value, 0


def redact_bytes(name: str, data: bytes, env_secrets: list[str]) -> tuple[bytes, int]:
    """The bytes to store and how many values were redacted. Binary data is kept as is."""
    if b"\x00" in data[:8192]:
        return data, 0
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, 0
    if name.endswith(".json"):
        try:
            parsed = json.loads(text)
        except ValueError:
            pass
        else:
            clean, n = _redact_json(parsed, env_secrets)
            return (json.dumps(clean, indent=2).encode() if n else data), n
    clean, n = redact_text(text, env_secrets)
    return (clean.encode() if n else data), n


# --- scanning -----------------------------------------------------------------------------


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise HarborImportError(f"{path} is not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as f:
            return f.read()
    finally:
        os.close(fd)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan(job_dir: Path) -> list[dict[str, Any]]:
    """Every entry below job_dir, without following links; sorted by relative path."""
    entries = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(job_dir, followlinks=False):
        here = Path(dirpath)
        for name in sorted(dirnames):
            p = here / name
            if p.is_symlink():
                entries.append({"path": p.relative_to(job_dir).as_posix(), "skipped": "symlink"})
        dirnames[:] = sorted(d for d in dirnames if not (here / d).is_symlink())
        for name in sorted(filenames):
            p = here / name
            rel = p.relative_to(job_dir).as_posix()
            if any(ord(c) < 32 for c in rel):
                entries.append({"path": rel.encode("unicode_escape").decode(), "skipped": "name"})
                continue
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                entries.append({"path": rel, "skipped": "symlink"})
                continue
            if not stat.S_ISREG(st.st_mode):
                entries.append({"path": rel, "skipped": "special file"})
                continue
            entry = {"path": rel, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            if st.st_size > MAX_FILE_BYTES:
                entry["skipped"] = "too large"
            else:
                total += st.st_size
            if total > MAX_TOTAL_BYTES:
                raise HarborImportError(f"{job_dir} holds more than {MAX_TOTAL_BYTES} bytes")
            entries.append(entry)
    return sorted(entries, key=lambda e: e["path"])


# --- job identity and checks --------------------------------------------------------------


def _load_json(data: bytes, what: str) -> Any:
    try:
        return json.loads(data)
    except ValueError as exc:
        raise IncompatibleJob(f"{what} is not valid JSON: {exc}") from None


def check_job(job_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """The job's config.json and lock.json; raises IncompatibleJob if this is not a job of the
    pinned Harbor release with the lock schema it was checked against."""
    if not job_dir.is_dir() or job_dir.is_symlink():
        raise HarborImportError(f"{job_dir} is not a directory")
    try:
        config = _load_json(_read_regular(job_dir / "config.json"), "config.json")
        lock = _load_json(_read_regular(job_dir / "lock.json"), "lock.json")
    except FileNotFoundError as exc:
        raise IncompatibleJob(f"{job_dir} is not a Harbor job directory: {exc}") from None
    except OSError as exc:
        raise IncompatibleJob(f"{job_dir}: cannot read {exc.filename}: {exc.strerror}") from None
    if not isinstance(lock, dict) or not isinstance(config, dict):
        raise IncompatibleJob(f"{job_dir}: config.json and lock.json must be objects")
    version = (lock.get("harbor") or {}).get("version")
    if version != PINNED_VERSION:
        raise IncompatibleJob(
            f"job written by Harbor {version!r}; only {PINNED_VERSION} is supported"
        )
    if lock.get("schema_version") != JOB_LOCK_SCHEMA:
        raise IncompatibleJob(
            f"job lock schema {lock.get('schema_version')!r}; only {JOB_LOCK_SCHEMA} is supported"
        )
    if not isinstance(lock.get("trials"), list):
        raise IncompatibleJob("lock.json has no trial list")
    return config, lock


def run_id_for(config: dict[str, Any], lock: dict[str, Any]) -> str:
    """Stable for the job's lifetime: lock.json is written once, when the job starts."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", str(config.get("job_name") or "job"))[:60] or "job"
    key = hashlib.sha256(f"{config.get('job_name')}\0{lock.get('created_at')}".encode()).hexdigest()
    return f"{name}-{key[:8]}"


def _job_status(result: dict[str, Any] | None, n_trial_dirs: int) -> str:
    if result is None:
        return "incomplete"
    stats = result.get("stats") or {}
    if (
        stats.get("n_running_trials")
        or stats.get("n_pending_trials")
        or not result.get("finished_at")
    ):
        return "incomplete"
    if stats.get("n_cancelled_trials"):
        return "cancelled"
    return "completed"


# --- import -------------------------------------------------------------------------------


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def build_records(
    job_dir: Path, files: dict[str, bytes], config: dict[str, Any], lock: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trials and the job summary, from the (redacted) file contents keyed by relative path."""
    trial_dirs = sorted({p.split("/", 1)[0] for p in files if p.count("/") >= 1})
    trials, problems = [], []
    for d in trial_dirs:
        if f"{d}/config.json" not in files and f"{d}/result.json" not in files:
            continue  # not a trial directory
        raw = files.get(f"{d}/result.json")
        if raw is None:
            trials.append(_placeholder(d, "running", "no result.json yet"))
            continue
        try:
            r = json.loads(raw)
            trial_lock = json.loads(files[f"{d}/lock.json"]) if f"{d}/lock.json" in files else None
        except ValueError as exc:
            problems.append(f"{d}: unreadable record ({exc})")
            trials.append(_placeholder(d, "invalid", f"unreadable result.json: {exc}"))
            continue
        if not isinstance(r, dict) or r.get("trial_name") != d:
            problems.append(f"{d}: result.json belongs to {r.get('trial_name')!r}")
            trials.append(_placeholder(d, "invalid", "result.json names another trial"))
            continue
        t = map_trial(r, trial_lock)
        t["files"] = sorted(p[len(d) + 1 :] for p in files if p.startswith(f"{d}/"))
        t["trajectory"] = next(
            (p for p in t["files"] if p.startswith("agent/") and p.endswith("trajectory.json")),
            None,
        )
        trials.append(t)
    number_repeats([t for t in trials if t.get("task_name") is not None])

    result = None
    if "result.json" in files:
        result = json.loads(files["result.json"])
    stats = (result or {}).get("stats") or {}
    expected = (result or {}).get("n_total_trials")
    if expected is None:
        expected = len(lock.get("trials") or [])
    outcomes = Counter(t["outcome"] for t in trials)
    with_model = [t for t in trials if t["usage"]["status"] != "not_applicable"]
    job = {
        "status": _job_status(result, len(trials)),
        "id": (result or {}).get("id"),
        "started_at": (result or {}).get("started_at"),
        "finished_at": (result or {}).get("finished_at"),
        "n_total_trials": expected,
        "n_trials_found": len(trials),
        "n_missing": max(0, expected - len(trials)),
        "n_retries": stats.get("n_retries"),
        "outcomes": dict(sorted(outcomes.items())),
        "usage_reported": f"{sum(t['usage']['status'] == 'reported' for t in with_model)}"
        f"/{len(with_model)}",
        "harbor_stats": stats or None,
        "problems": problems,
    }
    return trials, job


def _placeholder(trial_name: str, status: str, note: str) -> dict[str, Any]:
    return {
        "trial_name": trial_name,
        "task_name": None,
        "status": status,
        "outcome": status,
        "rewards": None,
        "score_note": note,
        "exception": None,
        "usage": {"status": "unknown", "source": note},
        "agent": {"name": None, "model": None},
        "repeat": None,
    }


def import_job(
    job_dir: Path,
    out_root: Path = Path("runs/harbor"),
    *,
    run_dir: Path | None = None,
    copy: bool = True,
    replace: bool = False,
) -> dict[str, Any]:
    """Import ``job_dir``; returns ``{"run_dir", "action", "record"}`` where action is
    created, unchanged or replaced.

    With ``copy=False`` the job directory must already sit inside ``run_dir`` (a run started
    by ``harness harbor run``); it is indexed in place instead of copied.
    """
    job_dir = Path(job_dir).absolute()
    config, lock = check_job(job_dir)
    run_id = run_id_for(config, lock)
    run_dir = Path(run_dir) if run_dir is not None else Path(out_root) / run_id
    if not copy and not job_dir.is_relative_to(run_dir.absolute()):
        raise HarborImportError("copy=False needs the job directory inside the run directory")
    if copy and run_dir.absolute().is_relative_to(job_dir):
        raise HarborImportError("the run directory cannot be inside the job directory")

    env_secrets = _env_secrets()
    before = scan(job_dir)
    files: dict[str, bytes] = {}
    index, redactions = [], 0
    for e in before:
        if "skipped" in e:
            entry = dict(e)
            if e["skipped"] == "too large":
                entry["sha256"] = _sha256_file(job_dir / e["path"])
            index.append(entry)
            continue
        data = _read_regular(job_dir / e["path"])
        stored, n = redact_bytes(e["path"], data, env_secrets) if copy else (data, 0)
        redactions += n
        files[e["path"]] = stored
        entry = {"path": e["path"], "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if n:
            entry |= {"redacted": n, "stored_sha256": hashlib.sha256(stored).hexdigest()}
        index.append(entry)
    after = scan(job_dir)
    changed = [a["path"] for a, b in zip(before, after, strict=False) if a != b] + (
        [] if len(before) == len(after) else ["<file list>"]
    )
    if not changed:
        changed = [
            e["path"]
            for e in index
            if "sha256" in e
            and "skipped" not in e
            and _sha256_file(job_dir / e["path"]) != e["sha256"]
        ]
    if changed:
        raise SourceChanged(
            f"{job_dir} changed while it was read ({', '.join(changed[:5])}); "
            "no snapshot written. Import again once the job has finished."
        )

    if not copy:
        # A job written by our own run: secrets never reach it (they are passed through the
        # process environment), but its records still go through redaction before mapping.
        files = {p: redact_bytes(p, b, env_secrets)[0] for p, b in files.items()}
    trials, job = build_records(job_dir, files, config, lock)
    index_text = json.dumps(index, indent=1)
    index_sha = hashlib.sha256(index_text.encode()).hexdigest()
    raw_rel = "raw" if copy else job_dir.relative_to(run_dir.absolute()).as_posix()
    record = {
        "kind": "harbor_run",
        "schema_version": 1,
        "run_id": run_dir.name,
        "backend": "harbor",
        "harbor": {"version": lock["harbor"].get("version"), "lock_schema": lock["schema_version"]},
        "importer": {"version": IMPORTER_VERSION, "mapping_version": MAPPING_VERSION},
        "source": {
            "job_dir": str(job_dir),
            "job_name": config.get("job_name"),
            "lock_created_at": lock.get("created_at"),
        },
        "raw": {
            "dir": raw_rel,
            "copied": copy,
            "index_sha256": index_sha,
            "n_files": len(files),
            "bytes": sum(e.get("size", 0) for e in index if "skipped" not in e),
            "skipped": [e for e in index if "skipped" in e],
            "redactions": redactions,
        },
        "tasks": [
            {k: (t.get("task") or {}).get(k) for k in ("name", "digest", "path")}
            for t in lock["trials"]
        ],
        "agents": sorted({json.dumps((t.get("agent") or {}).get("name")) for t in lock["trials"]}),
        "job": job,
        "imported_at": _now(),
    }
    record["agents"] = [json.loads(a) for a in record["agents"]]

    existing = run_dir / RUN_RECORD
    if existing.is_file():
        old = json.loads(existing.read_text())
        same = (
            old.get("raw", {}).get("index_sha256") == index_sha
            and old.get("importer") == (record["importer"])
        )
        if same:
            return {"run_dir": run_dir, "action": "unchanged", "record": old}
        if not replace:
            raise ImportConflict(
                f"{run_dir} already holds an import of this job with different content or "
                "importer version; pass replace to rebuild it"
            )
    action = "replaced" if existing.is_file() else "created"
    run_dir.mkdir(parents=True, exist_ok=True)
    if copy:
        staging = Path(tempfile.mkdtemp(prefix=".raw-", dir=run_dir))
        for rel, data in files.items():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        if (run_dir / "raw").exists():
            shutil.rmtree(run_dir / "raw")
        os.replace(staging, run_dir / "raw")
    _write_atomic(run_dir / INDEX, index_text)
    _write_atomic(run_dir / TRIALS, "".join(json.dumps(t) + "\n" for t in trials))
    _write_atomic(run_dir / RUN_RECORD, json.dumps(record, indent=2))
    return {"run_dir": run_dir, "action": action, "record": record}


def verify_snapshot(run_dir: Path) -> list[str]:
    """Files of the stored snapshot that are missing or differ from the index."""
    record = json.loads((run_dir / RUN_RECORD).read_text())
    raw = run_dir / record["raw"]["dir"]
    problems = []
    for e in json.loads((run_dir / INDEX).read_text()):
        if "skipped" in e:
            continue
        p = raw / e["path"]
        if p.is_symlink() or not p.is_file():
            problems.append(f"missing {e['path']}")
            continue
        if _sha256_file(p) != e.get("stored_sha256", e["sha256"]):
            problems.append(f"modified {e['path']}")
    return problems
