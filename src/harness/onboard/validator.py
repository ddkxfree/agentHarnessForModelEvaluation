"""Independent validation of a candidate adapter, run by the controller, not by the agent.

The candidate runs in a scratch directory holding a copy of its files and a copy of the data;
the originals, the scoring rules, the acceptance cases and the expected samples are never
placed there. Cases and expected samples go in a payload file that the check process deletes
before it imports the candidate. The source package is hashed before and after.

By default the check runs in a Docker container (``DockerExecutor``): no network, a read-only
root, only the scratch directory writable, resource limits, and the container is removed
afterwards. ``SubprocessExecutor`` runs it as a host process with a scrubbed environment and no
filesystem isolation; it is only for trusted, scripted candidates (tests, the demo).
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import harness
from harness.core.fingerprint import fingerprint
from harness.onboard.check import CHECKER_VERSION, PROTECTED_VIOLATION
from harness.onboard.spec import OnboardSpec, source_fingerprint
from harness.onboard.workspace import sha256
from harness.sandbox.base import SandboxError

VALIDATOR_VERSION = f"onboard_validator_v2+{CHECKER_VERSION}"

ENV_ERROR = "env_error"
VALIDATION_TIMEOUT = "validation_timeout"
VALIDATION_CRASHED = "validation_crashed"
SOURCE_CHANGED = "source_changed"


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class Executor(Protocol):
    """Runs a check command for the validator.

    ``argv[0]`` is ``python``; the other paths are relative to ``workdir``, which is the only
    directory the command may write. ``run`` raises SandboxError when it cannot run at all, and
    kills everything it started when ``timeout`` passes or ``cancel`` is called. A cancelled
    executor kills whatever it starts afterwards too.
    """

    def run(self, argv: list[str], *, workdir: Path, timeout: float) -> ExecResult: ...

    def cancel(self) -> None: ...

    def visible_path(self, workdir: Path) -> str:
        """How ``workdir`` appears to the command, to scrub it from diagnoses."""
        ...

    def describe(self) -> dict[str, Any]: ...


def _clean_env(home: Path) -> dict[str, str]:
    """No credentials reach candidate code."""
    keep = ("PATH", "LANG", "LC_ALL", "PYTHONPATH", "VIRTUAL_ENV", "SYSTEMROOT", "TMPDIR")
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0")
    return env


def _text(data: bytes | str | None) -> str:
    return data.decode(errors="replace") if isinstance(data, bytes) else data or ""


class SubprocessExecutor:
    """A host Python process per check, in its own process group, with a scrubbed environment
    and a time limit. It does not isolate the filesystem or the network: trusted candidates
    only."""

    isolation = "none"

    def __init__(self) -> None:
        self._live: set[subprocess.Popen] = set()
        self._lock = threading.Lock()
        self._cancelled = False

    def run(self, argv: list[str], *, workdir: Path, timeout: float) -> ExecResult:
        argv = [sys.executable if a == "python" and i == 0 else a for i, a in enumerate(argv)]
        try:
            proc = subprocess.Popen(
                argv,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=_clean_env(workdir),
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxError(f"cannot start {argv[0]}: {exc}") from exc
        with self._lock:
            self._live.add(proc)
            if self._cancelled:
                self._kill(proc)
        try:
            out, err = proc.communicate(timeout=max(timeout, 0.001))
        except subprocess.TimeoutExpired:
            self._kill(proc)
            out, err = proc.communicate()
            return ExecResult(-1, _text(out), _text(err), timed_out=True)
        finally:
            with self._lock:
                self._live.discard(proc)
        return ExecResult(proc.returncode, _text(out), _text(err))

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            for proc in list(self._live):
                self._kill(proc)

    def visible_path(self, workdir: Path) -> str:
        return str(workdir)

    def describe(self) -> dict[str, Any]:
        return {
            "type": "subprocess",
            "isolation": self.isolation,
            "python": sys.executable,
            "version": sys.version.split()[0],
        }


# What the checks import inside the container, pinned to the versions the controller runs.
SANDBOX_PACKAGES = ("pydantic", "numpy", "pytest")


class DockerExecutor:
    """One container per check: no network, read-only root filesystem, only the scratch
    directory writable (mounted at /work), the harness package read-only, no capabilities,
    memory, CPU and process limits, the host user's uid, and removal afterwards."""

    isolation = "container"
    WORKDIR = "/work"
    HARNESS = "/opt/harness"

    def __init__(
        self,
        base_image: str = "python:3.12-slim",
        *,
        mem_limit: str = "1g",
        cpus: float = 1.0,
        pids_limit: int = 256,
    ) -> None:
        self.base_image, self.mem_limit, self.cpus = base_image, mem_limit, cpus
        self.pids_limit = pids_limit
        self._client: Any = None
        self._image: dict[str, Any] | None = None
        self._live: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._cancelled = False

    def dockerfile(self) -> str:
        pins = " ".join(f"{p}=={metadata.version(p)}" for p in SANDBOX_PACKAGES)
        return f"FROM {self.base_image}\nRUN pip install --no-cache-dir {pins}\n"

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            try:
                self._client = docker.from_env()
                self._client.ping()
            except docker.errors.DockerException as exc:
                raise SandboxError(f"Docker is not available: {exc}") from exc
        return self._client

    def image(self) -> dict[str, Any]:
        """The checker image, built once per Dockerfile content."""
        if self._image is None:
            import docker

            text = self.dockerfile()
            tag = f"harness-onboard:{fingerprint({'dockerfile': text})}"
            try:
                try:
                    img = self.client.images.get(tag)
                except docker.errors.ImageNotFound:
                    img, _ = self.client.images.build(
                        fileobj=io.BytesIO(text.encode()), tag=tag, rm=True
                    )
            except docker.errors.DockerException as exc:
                raise SandboxError(f"cannot build {tag}: {exc}") from exc
            self._image = {"tag": tag, "id": img.id, "dockerfile": text}
        return self._image

    def run(self, argv: list[str], *, workdir: Path, timeout: float) -> ExecResult:
        import docker
        import requests

        image = self.image()["tag"]
        package = Path(harness.__file__).resolve().parent
        try:
            container = self.client.containers.run(
                image,
                argv,
                detach=True,
                working_dir=self.WORKDIR,
                user=f"{os.getuid()}:{os.getgid()}",
                environment={
                    "PYTHONPATH": self.HARNESS,
                    "HOME": "/tmp",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONHASHSEED": "0",
                },
                volumes={
                    str(workdir.resolve()): {"bind": self.WORKDIR, "mode": "rw"},
                    str(package): {"bind": f"{self.HARNESS}/harness", "mode": "ro"},
                },
                network_mode="none",
                read_only=True,
                tmpfs={"/tmp": "size=64m"},
                mem_limit=self.mem_limit,
                memswap_limit=self.mem_limit,
                nano_cpus=int(self.cpus * 1e9),
                pids_limit=self.pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                labels={"harness.onboard": "check"},
            )
        except docker.errors.DockerException as exc:
            raise SandboxError(f"cannot start the check container: {exc}") from exc
        with self._lock:
            self._live[container.id] = container
            if self._cancelled:
                self._kill(container)
        timed_out = False
        try:
            try:
                code = container.wait(timeout=max(timeout, 0.001))["StatusCode"]
            except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                timed_out, code = True, -1
                self._kill(container)
            out = _text(container.logs(stdout=True, stderr=False))
            err = _text(container.logs(stdout=False, stderr=True))
        except docker.errors.DockerException as exc:
            raise SandboxError(f"lost the check container: {exc}") from exc
        finally:
            with self._lock:
                self._live.pop(container.id, None)
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
        return ExecResult(code, out, err, timed_out=timed_out)

    @staticmethod
    def _kill(container: Any) -> None:
        import docker

        try:
            container.kill()
        except docker.errors.DockerException:
            pass

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            for container in list(self._live.values()):
                self._kill(container)

    def visible_path(self, workdir: Path) -> str:
        return self.WORKDIR

    def describe(self) -> dict[str, Any]:
        return {
            "type": "docker",
            "isolation": self.isolation,
            "image": self.image(),
            "network": "none",
            "read_only_root": True,
            "writable": [self.WORKDIR, "/tmp (tmpfs)"],
            "mem_limit": self.mem_limit,
            "cpus": self.cpus,
            "pids_limit": self.pids_limit,
        }


def make_executor(kind: str) -> Executor:
    if kind == "docker":
        return DockerExecutor()
    if kind == "subprocess":
        return SubprocessExecutor()
    raise ValueError(f"unknown executor {kind!r}; use docker or subprocess")


def read_cases(spec: OnboardSpec) -> list[dict[str, Any]]:
    """The frozen acceptance cases, plus an empty output for every sample they name, which must
    never score as correct."""
    cases = []
    for n, line in enumerate(spec.path(spec.acceptance).read_text().splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if case.get("expect") not in ("correct", "wrong") or not isinstance(
            case.get("output"), str
        ):
            raise ValueError(f"{spec.acceptance}:{n}: needs 'output' and expect correct/wrong")
        cases.append(case)
    if not any(c["expect"] == "correct" for c in cases) or not any(
        c["expect"] == "wrong" for c in cases
    ):
        raise ValueError("acceptance needs both correct and wrong outputs")
    for sample_id in sorted({c["sample_id"] for c in cases}):
        cases.append({"sample_id": sample_id, "output": "", "expect": "wrong", "note": "empty"})
    return cases


def read_expected(spec: OnboardSpec) -> list[dict[str, Any]]:
    """The frozen expected samples: what each sample's input and target must be, and the
    prompt the model must be shown. Written by a person from the source description."""
    expected = []
    for n, line in enumerate(spec.path(spec.expected).read_text().splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not all(isinstance(row.get(k), str) for k in ("sample_id", "input", "target", "prompt")):
            raise ValueError(f"{spec.expected}:{n}: needs sample_id, input, target and prompt")
        expected.append(row)
    if not expected:
        raise ValueError(f"{spec.expected} has no samples")
    return expected


def _tail(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else "..." + text[-limit:]


def validate(
    spec: OnboardSpec,
    candidate: Path,
    executor: Executor | None = None,
    *,
    expected_source: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Validate the candidate files in ``candidate``; returns a structured report.

    ``timeout`` bounds the whole validation, self test included; each command also stays
    within ``budget.validation_timeout_s``. ``category`` is None when every check passed;
    otherwise one of the check categories (import, dependency, protocol, field, scoring,
    protected) or ``validation_timeout``, ``validation_crashed``, ``env_error`` or
    ``source_changed``.
    """
    started = time.monotonic()

    def left() -> float:
        limit = spec.budget.validation_timeout_s
        if timeout is None:
            return limit
        return min(limit, timeout - (time.monotonic() - started))

    executor = executor or make_executor(spec.executor)
    expected_source = expected_source or spec.source_sha256
    report: dict[str, Any] = {
        "validator_version": VALIDATOR_VERSION,
        "executor": {"type": type(executor).__name__},
        "candidate_files": {},
        "source_sha256_expected": expected_source,
        "source_sha256_before": source_fingerprint(spec),
        "self_test": None,
    }

    def finish(category: str | None, detail: str | None = None) -> dict[str, Any]:
        report["source_sha256_after"] = source_fingerprint(spec)
        if category is None and report["source_sha256_after"] != report["source_sha256_before"]:
            category, detail = PROTECTED_VIOLATION, "the source package changed during validation"
        report["passed"] = category is None
        report["category"] = category
        if detail is not None:
            report["detail"] = detail
        return report

    if report["source_sha256_before"] != expected_source:
        return finish(
            SOURCE_CHANGED,
            f"source package fingerprint is {report['source_sha256_before']}, expected "
            f"{expected_source}; an earlier validation does not carry over",
        )
    try:
        report["executor"] = executor.describe()
    except SandboxError as exc:
        return finish(ENV_ERROR, str(exc))
    payload = {"nonce": secrets.token_hex(16), "cases": read_cases(spec)}
    payload["expected"] = read_expected(spec)
    with tempfile.TemporaryDirectory(prefix="onboard-validate-") as tmp:
        scratch = Path(tmp)
        cand, data = scratch / "candidate", scratch / "data"
        shutil.copytree(candidate, cand)
        shutil.copytree(spec.path(spec.data), data)
        report["candidate_files"] = {
            p.name: sha256(p.read_bytes()) for p in sorted(cand.iterdir()) if p.is_file()
        }
        (scratch / "payload.json").write_text(json.dumps(payload))

        def scrub(text: str) -> str:
            # The scratch path differs every time; without it the diagnosis is reproducible.
            for path in (str(scratch), executor.visible_path(scratch)):
                text = re.sub(re.escape(path) + r"(?![\w.-])", "<sandbox>", text)
            return text

        argv = [
            "python",
            "-m",
            "harness.onboard.check",
            "--candidate",
            "candidate",
            "--data",
            "data",
            "--task",
            spec.task,
            "--allowed-imports",
            json.dumps(spec.allowed_imports),
            "--report",
            "report.json",
            "--payload",
            "payload.json",
        ]
        limit = left()
        if limit <= 0:
            return finish(VALIDATION_TIMEOUT, "no time left for validation")
        try:
            res = executor.run(argv, workdir=scratch, timeout=limit)
        except SandboxError as exc:
            return finish(ENV_ERROR, str(exc))
        res.stderr = scrub(res.stderr)
        if res.timed_out:
            return finish(
                VALIDATION_TIMEOUT, f"checks did not finish in {limit:.1f}s\n{_tail(res.stderr)}"
            )
        try:
            checked = json.loads(scrub((scratch / "report.json").read_text()))
        except (OSError, ValueError):
            checked = None
        if checked is None or checked.get("nonce") != payload["nonce"] or res.returncode != 0:
            return finish(
                VALIDATION_CRASHED,
                f"the check process exited with {res.returncode} without a valid report\n"
                f"{_tail(res.stderr)}",
            )
        checked.pop("nonce")
        report.update(checks=checked["checks"], cases=checked["cases"])
        report["data_sha256_before"] = checked["data_sha256_before"]
        report["data_sha256_after"] = checked["data_sha256_after"]
        report["self_test"] = _self_test(executor, scratch, scrub, left())
        if checked["passed"]:
            return finish(None)
        failed = [c for c in checked["checks"] if c["status"] == "failed"]
        return finish(checked["category"], failed[-1]["detail"] if failed else None)


def _self_test(executor: Executor, scratch: Path, scrub: Any, limit: float) -> dict[str, Any]:
    """The agent's own tests, run for information only: passing them is never enough."""
    if not (scratch / "candidate" / "test_adapter.py").is_file():
        return {"status": "missing"}
    if limit <= 0:
        return {"status": "skipped", "output": "no time left"}
    argv = [
        "python",
        "-c",
        "import os, sys, pytest; os.environ['ONBOARD_DATA_DIR'] = os.path.abspath(sys.argv[1]); "
        "sys.exit(pytest.main(['-q', '-p', 'no:cacheprovider', sys.argv[2]]))",
        "data",
        "candidate/test_adapter.py",
    ]
    try:
        res = executor.run(argv, workdir=scratch, timeout=limit)
    except SandboxError as exc:
        return {"status": "env_error", "output": str(exc)}
    if res.timed_out:
        return {"status": "timeout", "output": scrub(_tail(res.stdout))}
    return {
        "status": "passed" if res.returncode == 0 else "failed",
        "output": scrub(_tail(res.stdout + res.stderr)),
    }
