"""Offline import of a Harbor 0.25.0 job directory into a harness run directory.

The job directory is only read. Every file is treated as data: symbolic links are recorded but
never followed, special files and over-large files are not copied, nothing in it is executed
and no path inside it is trusted. A copy of the job ("raw/") is taken with credential-looking
values redacted, indexed with sizes and hashes, and checked against the source once more after
copying; a job that changed meanwhile does not produce a snapshot that claims to be complete.

The normalized records (``trials.jsonl``) and the run record (``harbor_run.json``) can always
be rebuilt from the snapshot (``rebuild``). Importing the same job again is a no-op only when
both the snapshot and the importer version are unchanged; a changed job or a record built by
another importer version is refused unless ``replace=True``, which rebuilds it.
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
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.integrations.harbor import JOB_LOCK_SCHEMA, PINNED_VERSION
from harness.integrations.harbor.mapping import MAPPING_VERSION, map_trial, number_repeats

# 2: planned trials without a directory are kept as ``missing`` (version 1 dropped them from
# the denominator); records built by an older version are flagged and can be rebuilt
# 3: deterministic run/attempt identities and independent generation/assessment states.
IMPORTER_VERSION = 3
# How the shareable snapshot (raw/) was redacted, apart from how records were mapped.
# 1: UTF-8 text only (invalid UTF-8, NUL and binary data were stored as found)
# 2: also invalid UTF-8, NUL and binary data, byte for byte
REDACTION_VERSION = 2
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
_SECRET_TEXT_SOURCES = [
    r"\bsk-[A-Za-z0-9_\-]{16,}",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bhf_[A-Za-z0-9]{30,}",
    r"\bAKIA[0-9A-Z]{16}\b",
    r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}",
]
_SECRET_ASSIGN_SOURCE = (
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)"
    r"(\s*[=:]\s*[\"']?)([^\s\"',}]{8,})"
)
_SECRET_TEXT = [re.compile(p) for p in _SECRET_TEXT_SOURCES]
_SECRET_ASSIGN = re.compile(_SECRET_ASSIGN_SOURCE)
# for bytes read as Latin-1: a non-ASCII byte must not count as a word character
_SECRET_TEXT_ASCII = [re.compile(p, re.ASCII) for p in _SECRET_TEXT_SOURCES]
_SECRET_ASSIGN_ASCII = re.compile(_SECRET_ASSIGN_SOURCE, re.ASCII)
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
    return all_secrets()


def all_secrets(extra: Iterable[str] = ()) -> list[str]:
    """Values to redact: the given ones and those of credential-named environment variables,
    longest first so a secret containing another is replaced whole."""
    values = {v for k, v in os.environ.items() if _ENV_SECRET_NAME.search(k) and len(v) >= 8}
    values |= {v for v in extra if v and len(v) >= 6}
    return sorted(values, key=len, reverse=True)


def redact_text(text: str, env_secrets: list[str], *, ascii: bool = False) -> tuple[str, int]:
    patterns, assign = (
        (_SECRET_TEXT_ASCII, _SECRET_ASSIGN_ASCII) if ascii else (_SECRET_TEXT, _SECRET_ASSIGN)
    )
    n = 0
    for value in env_secrets:
        if value in text:
            n += text.count(value)
            text = text.replace(value, REDACTED)
    for pattern in patterns:
        text, k = pattern.subn(REDACTED, text)
        n += k
    k = 0

    def assignment(m: re.Match) -> str:
        nonlocal k
        if m.group(3).startswith(REDACTED):
            return m.group(0)
        k += 1
        return f"{m.group(1)}{m.group(2)}{REDACTED}"

    text = assign.sub(assignment, text)
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
    """The bytes to store and how many values were redacted. UTF-8 text is redacted as text
    (JSON structurally); anything else (invalid UTF-8, NUL bytes, binary) byte for byte, so
    no file is passed through unredacted because it is not clean text."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is None or "\x00" in text:
        return _redact_raw_bytes(data, env_secrets)
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


def _redact_raw_bytes(data: bytes, env_secrets: list[str]) -> tuple[bytes, int]:
    # Latin-1 maps every byte to one character and back, so the secrets' UTF-8 bytes and the
    # ASCII credential patterns are found wherever they are; UTF-16 copies of the secrets too.
    n = 0
    for value in env_secrets:
        wide, mark = value.encode("utf-16-le"), REDACTED.encode("utf-16-le")
        if wide in data:
            n += data.count(wide)
            data = data.replace(wide, mark)
    latin = [v.encode("utf-8").decode("latin-1") for v in env_secrets]
    clean, k = redact_text(data.decode("latin-1"), latin, ascii=True)
    return (clean.encode("latin-1") if k else data), n + k


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


def _task_path(raw: bytes | None) -> str | None:
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    cfg = data.get("config") if isinstance(data.get("config"), dict) else data
    task = cfg.get("task") if isinstance(cfg.get("task"), dict) else {}
    path = task.get("path")
    return path if isinstance(path, str) else None


def build_records(
    job_dir: Path, files: dict[str, bytes], config: dict[str, Any], lock: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Trials and the job summary, from the (redacted) file contents keyed by relative path.

    Every trial the job planned has a record: trials without a directory yet (or whose
    directory is gone) get a ``missing`` placeholder, so they stay in every denominator.
    """
    trial_dirs = sorted({p.split("/", 1)[0] for p in files if p.count("/") >= 1})
    trials, problems = [], []
    for d in trial_dirs:
        if f"{d}/config.json" not in files and f"{d}/result.json" not in files:
            continue  # not a trial directory
        raw = files.get(f"{d}/result.json")
        path = _task_path(raw) or _task_path(files.get(f"{d}/config.json"))
        if raw is None:
            trials.append(_placeholder(d, "running", "no result.json yet", path))
            continue
        try:
            r = json.loads(raw)
            trial_lock = json.loads(files[f"{d}/lock.json"]) if f"{d}/lock.json" in files else None
        except ValueError as exc:
            problems.append(f"{d}: unreadable record ({exc})")
            trials.append(_placeholder(d, "invalid", f"unreadable result.json: {exc}", path))
            continue
        if not isinstance(r, dict) or r.get("trial_name") != d:
            problems.append(f"{d}: result.json belongs to {r.get('trial_name')!r}")
            trials.append(_placeholder(d, "invalid", "result.json names another trial", path))
            continue
        t = map_trial(r, trial_lock)
        t["task_path"] = path
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
    planned = [t.get("task") or {} for t in lock.get("trials") or [] if isinstance(t, dict)]
    reported_total = (result or {}).get("n_total_trials")
    expected = max(len(planned), reported_total if isinstance(reported_total, int) else 0)
    n_found = len(trials)

    # Names for placeholders: what Harbor called the task in a trial record, else the lock's.
    names = {
        t["task_path"]: t["task_name"] for t in trials if t.get("task_path") and t.get("task_name")
    }
    for t in trials:
        if t.get("task_name") is None:
            t["task_name"] = names.get(t.get("task_path")) or None
    found_by_path = Counter(t.get("task_path") for t in trials)
    for task in planned:
        path = task.get("path")
        if found_by_path[path] > 0:
            found_by_path[path] -= 1
            continue
        if len(trials) >= max(expected, n_found):
            break
        trials.append(
            _placeholder(None, "missing", "planned by the job; no trial directory",
                         path, names.get(path) or task.get("name"))
        )  # fmt: skip
    while len(trials) < expected:
        trials.append(_placeholder(None, "missing", "planned by the job; task unknown", None))
    _number_placeholders(trials)
    identity = run_id_for(config, lock)
    for t in trials:
        stable = str(
            t.get("trial_id")
            or t.get("trial_name")
            or f"{t.get('task_path')}:{t.get('task_name')}:{t.get('repeat')}"
        )
        t["run_id"] = identity
        t["attempt_id"] = (
            "harbor_" + hashlib.sha256(f"{identity}:{stable}".encode()).hexdigest()[:32]
        )

    outcomes = Counter(t["outcome"] for t in trials)
    with_model = [t for t in trials if t["usage"]["status"] != "not_applicable"]
    job = {
        "status": _job_status(result, n_found),
        "id": (result or {}).get("id"),
        "started_at": (result or {}).get("started_at"),
        "finished_at": (result or {}).get("finished_at"),
        "n_total_trials": max(expected, n_found),
        "n_trials_found": n_found,
        "n_missing": max(0, expected - n_found),
        "n_retries": stats.get("n_retries"),
        "outcomes": dict(sorted(outcomes.items())),
        "usage_reported": f"{sum(t['usage']['status'] == 'reported' for t in with_model)}"
        f"/{len(with_model)}",
        "harbor_stats": stats or None,
        "problems": problems,
    }
    return trials, job


def _number_placeholders(trials: list[dict[str, Any]]) -> None:
    """Placeholders continue the repeat numbering of their task, so every key is unique."""
    used: dict[str, set] = {}
    for t in trials:
        if t.get("repeat") is not None:
            used.setdefault(t.get("task_name") or "", set()).add(t["repeat"])
    for t in trials:
        if t.get("repeat") is None:
            taken = used.setdefault(t.get("task_name") or "", set())
            t["repeat"] = next(i for i in range(len(taken) + 1) if i not in taken)
            taken.add(t["repeat"])


def _placeholder(
    trial_name: str | None,
    status: str,
    note: str,
    task_path: str | None,
    task_name: str | None = None,
) -> dict[str, Any]:
    return {
        "trial_name": trial_name,
        "task_name": task_name,
        "task_path": task_path,
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
    replace: bool = False,
    secrets: Iterable[str] = (),
) -> dict[str, Any]:
    """Import ``job_dir``; returns ``{"run_dir", "action", "record"}`` where action is
    created, unchanged or replaced.

    ``secrets`` are values to redact besides the credential-looking ones (e.g. the values a
    run handed to Harbor); values shorter than 6 characters are ignored.
    """
    job_dir = Path(job_dir).absolute()
    config, lock = check_job(job_dir)
    run_id = run_id_for(config, lock)
    run_dir = Path(run_dir) if run_dir is not None else Path(out_root) / run_id
    if run_dir.absolute().is_relative_to(job_dir):
        raise HarborImportError("the run directory cannot be inside the job directory")

    env_secrets = all_secrets(secrets)
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
        stored, n = redact_bytes(e["path"], data, env_secrets)
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

    trials, job = build_records(job_dir, files, config, lock)
    index_text = json.dumps(index, indent=1)
    index_sha = hashlib.sha256(index_text.encode()).hexdigest()
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
            "dir": "raw",
            "index_sha256": index_sha,
            "n_files": len(files),
            "bytes": sum(e.get("size", 0) for e in index if "skipped" not in e),
            "skipped": [e for e in index if "skipped" in e],
            "redactions": redactions,
            "redaction": {"version": REDACTION_VERSION, "from": "original files"},
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
            and redaction_version(old) == REDACTION_VERSION
        )
        if same:
            return {"run_dir": run_dir, "action": "unchanged", "record": old}
        if not replace:
            old_version = (old.get("importer") or {}).get("version")
            reasons = []
            if old_version != IMPORTER_VERSION:
                reasons.append(
                    f"its records were built by importer version {old_version} (current "
                    f"{IMPORTER_VERSION}) and may be wrong"
                )
            if redaction_version(old) != REDACTION_VERSION:
                reasons.append(
                    f"its snapshot was redacted by redaction version {redaction_version(old)} "
                    f"(current {REDACTION_VERSION}) and may contain credentials"
                )
            if not reasons:
                reasons.append("it holds different content of this job")
            raise ImportConflict(
                f"{run_dir}: {'; '.join(reasons)}. Pass replace (--replace) to rebuild it from "
                "this job, or run `harness harbor rebuild` if the job is gone"
            )
    action = "replaced" if existing.is_file() else "created"
    run_dir.mkdir(parents=True, exist_ok=True)
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


def redaction_version(record: dict[str, Any]) -> int:
    raw = record.get("raw") or {}
    version = (raw.get("redaction") or {}).get("version")
    if version is not None:
        return int(version)
    # importer 2 introduced byte-level redaction before the snapshot recorded its own version
    return 2 if ((record.get("importer") or {}).get("version") or 0) >= 2 else 1


def _receipt_secrets(run_dir: Path) -> list[str]:
    """Values of the variables a ``harness harbor run`` handed to Harbor, if set here."""
    try:
        names = json.loads((run_dir / "run.json").read_text()).get("agent_env_names") or []
    except (OSError, ValueError):
        return []
    return [os.environ[n] for n in names if n in os.environ]


def _private_original(run_dir: Path, record: dict[str, Any]) -> Path | None:
    """Harbor's own job directory kept under the run's private local/, if it is still there."""
    rel = (record.get("source") or {}).get("job_dir") or ""
    if not rel.startswith("local/"):
        return None
    job_dir = run_dir / rel
    return job_dir if (job_dir / "lock.json").is_file() and not job_dir.is_symlink() else None


def rebuild(run_dir: Path, secrets: Iterable[str] = ()) -> dict[str, Any]:
    """Bring an imported run up to the current importer and redaction versions.

    From Harbor's original job directory when the run still keeps it (``local/job``): a fresh
    import, with original hashes. Otherwise from the run's own snapshot: an outdated snapshot is
    redacted again (credential patterns, credential-named environment values, ``secrets`` and
    the variables a harness run handed to Harbor), its index keeps the original hashes and gets
    new stored hashes, and the record says that only values known at migration were removed.
    Returns ``{"run_dir", "action", "record"}``."""
    run_dir = Path(run_dir)
    record = json.loads((run_dir / RUN_RECORD).read_text())
    redact = all_secrets([*secrets, *_receipt_secrets(run_dir)])
    previous = {"importer": record.get("importer"), "redaction": redaction_version(record)}

    original = _private_original(run_dir, record)
    if original is not None:
        res = import_job(original, run_dir=run_dir, replace=True, secrets=redact)
        new = res["record"]
        for key in ("run", "state"):
            if key in record:
                new[key] = record[key]
        new["source"]["job_dir"] = record["source"]["job_dir"]
        new["rebuilt"] = {"at": _now(), "from": "original job", "previous": previous}
        _write_atomic(run_dir / RUN_RECORD, json.dumps(new, indent=2))
        return {"run_dir": run_dir, "action": "rebuilt", "record": new}

    problems = verify_snapshot(run_dir)
    if problems:
        raise HarborImportError(
            f"{run_dir}: snapshot does not match its index ({', '.join(problems[:5])}); not rebuilt"
        )
    raw = run_dir / record["raw"]["dir"]
    index = json.loads((run_dir / INDEX).read_text())
    files = {e["path"]: _read_regular(raw / e["path"]) for e in index if "skipped" not in e}
    if previous["redaction"] < REDACTION_VERSION:
        files, index, n = _redact_snapshot_again(run_dir, raw, files, index, redact)
        index_text = json.dumps(index, indent=1)
        record["raw"]["index_sha256"] = hashlib.sha256(index_text.encode()).hexdigest()
        record["raw"]["redactions"] = (record["raw"].get("redactions") or 0) + n
        record["raw"]["redaction"] = {
            "version": REDACTION_VERSION,
            "from": "older snapshot",
            "migrated_from": previous["redaction"],
            "migrated_at": _now(),
            "redacted_at_migration": n,
            "known_values": len(redact),
        }
    config, lock = check_job(raw)
    trials, job = build_records(raw, files, config, lock)
    record["importer"] = {"version": IMPORTER_VERSION, "mapping_version": MAPPING_VERSION}
    record["job"] = job
    record["rebuilt"] = {"at": _now(), "from": "snapshot", "previous": previous}
    _write_atomic(run_dir / TRIALS, "".join(json.dumps(t) + "\n" for t in trials))
    _write_atomic(run_dir / RUN_RECORD, json.dumps(record, indent=2))
    return {"run_dir": run_dir, "action": "rebuilt", "record": record}


def _redact_snapshot_again(
    run_dir: Path,
    raw: Path,
    files: dict[str, bytes],
    index: list[dict[str, Any]],
    redact: list[str],
) -> tuple[dict[str, bytes], list[dict[str, Any]], int]:
    """Redact the stored files with the current rules; swap in the new snapshot and index
    together. ``sha256`` stays the hash of the original file, ``stored_sha256`` is updated."""
    total = 0
    changed: dict[str, bytes] = {}
    for e in index:
        if "skipped" in e:
            continue
        stored, n = redact_bytes(e["path"], files[e["path"]], redact)
        if n:
            changed[e["path"]] = stored
            e["redacted"] = e.get("redacted", 0) + n
            e["stored_sha256"] = hashlib.sha256(stored).hexdigest()
            total += n
    if not changed:
        return files, index, 0
    files = {**files, **changed}
    staging = Path(tempfile.mkdtemp(prefix=".raw-", dir=run_dir))
    for rel, data in files.items():
        dest = staging / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    old = run_dir / f".raw-old-{os.getpid()}"
    os.replace(raw, old)
    os.replace(staging, raw)
    _write_atomic(run_dir / INDEX, json.dumps(index, indent=1))
    shutil.rmtree(old)
    return files, index, total


def record_warnings(record: dict[str, Any]) -> list[str]:
    """What a reader must know about the stored records and snapshot themselves."""
    warnings = []
    version = (record.get("importer") or {}).get("version")
    if version != IMPORTER_VERSION:
        warnings.append(
            f"records built by importer version {version}, current is {IMPORTER_VERSION}; "
            "counts may be wrong. Rebuild with `harness harbor rebuild <run_dir>`"
        )
    redaction = redaction_version(record)
    migrated = ((record.get("raw") or {}).get("redaction") or {}).get("migrated_from")
    if redaction < REDACTION_VERSION:
        warnings.append(
            f"snapshot raw/ was redacted by redaction version {redaction} (non-text files were "
            "stored unredacted); do not share it. Redact it again with "
            "`harness harbor rebuild <run_dir>`"
        )
    elif migrated is not None:
        warnings.append(
            f"snapshot raw/ was redacted again from a version-{migrated} snapshot: only "
            "credential patterns and the secret values known at migration were removed; "
            "review raw/ before sharing"
        )
    return warnings
