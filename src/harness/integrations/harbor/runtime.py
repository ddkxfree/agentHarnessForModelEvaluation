"""The Harbor installation: where its CLI is, which release it is, and the host tools it needs.

Harbor lives in its own virtual environment, installed from the hashed lock file:

    uv venv --python 3.12 ~/.local/share/agent-harness/harbor-0.25.0
    VIRTUAL_ENV=~/.local/share/agent-harness/harbor-0.25.0 \\
        uv pip install --require-hashes -r configs/harbor/requirements-0.25.0.lock

An explicitly given executable is checked, never swapped for another one found on PATH.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from harness.integrations.harbor import LOCK_FILE, PINNED_VERSION

INSTALL_HINT = (
    f"install Harbor {PINNED_VERSION} into its own environment:\n"
    f"  uv venv --python 3.12 ~/.local/share/agent-harness/harbor-{PINNED_VERSION}\n"
    f"  VIRTUAL_ENV=~/.local/share/agent-harness/harbor-{PINNED_VERSION} "
    f"uv pip install --require-hashes -r {LOCK_FILE}\n"
    "or point HARNESS_HARBOR_BIN / harbor.executable at an existing install of that version"
)


class HarborError(Exception):
    """Harbor cannot be used as configured."""


class HarborNotFound(HarborError):
    pass


class HarborVersionMismatch(HarborError):
    pass


def default_home() -> Path:
    base = os.environ.get("HARNESS_HARBOR_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share" / "agent-harness"
    return root / f"harbor-{PINNED_VERSION}"


def resolve_executable(executable: str | Path | None = None) -> Path:
    """The configured executable, else $HARNESS_HARBOR_BIN, else the default install."""
    chosen = executable or os.environ.get("HARNESS_HARBOR_BIN") or default_home() / "bin" / "harbor"
    return Path(chosen).expanduser()


def base_env() -> dict[str, str]:
    """What Harbor's own commands need from the environment; no credentials."""
    keep = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "DOCKER_HOST", "DOCKER_CONFIG",
            "DOCKER_CONTEXT", "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY", "SSL_CERT_FILE",
            "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
            "http_proxy", "https_proxy", "no_proxy")  # fmt: skip
    env = {k: v for k, v in os.environ.items() if k in keep}
    env.update(NO_COLOR="1", COLUMNS="200", PYTHONUNBUFFERED="1")
    return env


@dataclass
class HarborRuntime:
    executable: str
    version: str
    venv: str | None
    python: str | None
    dist_record_sha256: str | None

    def describe(self) -> dict[str, Any]:
        return asdict(self)


def _venv_info(executable: Path) -> tuple[Path | None, str | None, str | None]:
    venv = executable.resolve().parent.parent
    cfg = venv / "pyvenv.cfg"
    if not cfg.is_file():
        return None, None, None
    fields = {
        k.strip(): v.strip()
        for k, v in (line.split("=", 1) for line in cfg.read_text().splitlines() if "=" in line)
    }
    python = fields.get("version_info") or fields.get("version") or None
    dist_info = f"harbor-{PINNED_VERSION}.dist-info"
    records = sorted(venv.glob(f"lib/python*/site-packages/{dist_info}/RECORD"))
    digest = hashlib.sha256(records[0].read_bytes()).hexdigest() if records else None
    return venv, python, digest


def probe(executable: str | Path | None = None, *, timeout: float = 60.0) -> HarborRuntime:
    """Check that the executable is the pinned Harbor release; raises HarborError if not."""
    path = resolve_executable(executable)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise HarborNotFound(f"no Harbor executable at {path}; {INSTALL_HINT}")
    try:
        out = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=base_env(),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        raise HarborError(f"{path} --version did not answer in {timeout:.0f}s") from None
    except OSError as exc:
        raise HarborNotFound(f"cannot run {path}: {exc}") from exc
    version = out.stdout.strip().splitlines()[-1].strip() if out.stdout.strip() else ""
    if out.returncode != 0 or not version:
        raise HarborError(f"{path} --version failed ({out.returncode}): {out.stderr[-500:]}")
    if version != PINNED_VERSION:
        raise HarborVersionMismatch(
            f"{path} is Harbor {version}; only {PINNED_VERSION} is supported. {INSTALL_HINT}"
        )
    venv, python, digest = _venv_info(path)
    return HarborRuntime(str(path), version, str(venv) if venv else None, python, digest)


def check_docker(*, timeout: float = 20.0) -> dict[str, Any]:
    """Docker CLI, daemon and compose plugin, which Harbor's docker environment needs."""
    result: dict[str, Any] = {}
    for name, argv in (
        ("daemon", ["docker", "version", "--format", "{{.Server.Version}}"]),
        ("compose", ["docker", "compose", "version", "--short"]),
    ):
        try:
            out = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL
            )
        except FileNotFoundError:
            result[name] = {"ok": False, "detail": "docker CLI not found"}
            continue
        except subprocess.TimeoutExpired:
            result[name] = {"ok": False, "detail": f"no answer in {timeout:.0f}s"}
            continue
        text = (out.stdout or out.stderr).strip().splitlines()
        result[name] = {"ok": out.returncode == 0, "detail": text[-1][:300] if text else ""}
    return result
