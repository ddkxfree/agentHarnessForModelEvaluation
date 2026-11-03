"""The Harbor run configuration: what to run, with which agent, under which limits.

It is deliberately small. Every field maps to a CLI option of the pinned Harbor release that
was checked against that release; there is no free-form argument pass-through. Limits Harbor
cannot enforce are refused before anything starts.

Relative paths are taken from the project root: the nearest parent of the config file that
holds ``pyproject.toml`` (else the config file's directory), never from the current directory.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from harness.core.fingerprint import fingerprint_dir
from harness.integrations.harbor import PINNED_VERSION

_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")
_KWARG_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarborBinary(_Strict):
    version: Literal["0.25.0"] = PINNED_VERSION
    executable: str | None = None


class TaskRef(_Strict):
    """One task: a pinned Terminal-Bench 2.0 task of this project, or a local Harbor task
    directory pinned by content fingerprint."""

    terminal_bench: str | None = None
    path: Path | None = None
    fingerprint: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> TaskRef:
        if (self.terminal_bench is None) == (self.path is None):
            raise ValueError("task needs exactly one of 'terminal_bench' or 'path'")
        if self.path is not None and not self.fingerprint:
            raise ValueError("a local task 'path' must be pinned with 'fingerprint'")
        return self


class AgentSpec(_Strict):
    name: str
    model: str | None = None
    # passed as ``--agent-kwarg key=value``; must not carry credentials
    kwargs: dict[str, str] = Field(default_factory=dict)
    # names of host environment variables the agent needs (e.g. an API key). Their values are
    # handed to the Harbor process environment only: never argv, files or manifests.
    env: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not _NAME.fullmatch(v):
            raise ValueError(f"invalid agent name {v!r}")
        return v

    @field_validator("kwargs")
    @classmethod
    def _kwargs(cls, v: dict[str, str]) -> dict[str, str]:
        for key, value in v.items():
            if not _KWARG_KEY.fullmatch(key):
                raise ValueError(f"invalid agent kwarg name {key!r}")
            if re.search(r"(?i)key|token|secret|password", key):
                raise ValueError(f"agent kwarg {key!r} looks like a credential; use 'env'")
            if "\n" in value or "\x00" in value:
                raise ValueError(f"agent kwarg {key!r} has a control character")
        return v

    @field_validator("env")
    @classmethod
    def _env(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(f"invalid environment variable name {name!r}")
        return v


class Limits(_Strict):
    # wall clock for the whole job, enforced by this harness: the Harbor process group is
    # stopped and this job's containers removed
    wall_clock_s: float = Field(default=3600.0, gt=0)
    # Harbor's own per-phase timeouts come from each task; this scales them all
    timeout_multiplier: float = Field(default=1.0, gt=0)
    # checking the resolved Harbor config (``--print-config``) before anything starts
    prepare_s: float = Field(default=120.0, gt=0)
    # after SIGTERM on a stop, before the process group is killed
    stop_grace_s: float = Field(default=60.0, ge=0)
    # one deadline for every Docker call that removes or checks this job's resources
    cleanup_s: float = Field(default=120.0, gt=0)
    # Not enforceable through the pinned CLI; setting them is an error, not a silent no-op.
    token_limit: int | None = None
    cost_limit_usd: float | None = None
    max_steps: int | None = None

    @model_validator(mode="after")
    def _unsupported(self) -> Limits:
        names = ("token_limit", "cost_limit_usd", "max_steps")
        asked = [n for n in names if getattr(self, n) is not None]
        if asked:
            raise ValueError(
                f"{', '.join(asked)} cannot be enforced through the Harbor {PINNED_VERSION} CLI; "
                "remove them (usage is still recorded when the agent reports it)"
            )
        return self


class Environment(_Strict):
    type: Literal["docker"] = "docker"
    # extra compose files merged into each trial's environment (``--extra-docker-compose``)
    extra_docker_compose: list[Path] = Field(default_factory=list)
    # The harness removes this job's containers whenever the job ends, so keeping them
    # (Harbor's --no-delete) cannot be honoured and is refused rather than ignored.
    delete: bool = True

    @field_validator("delete")
    @classmethod
    def _delete(cls, v: bool) -> bool:
        if not v:
            raise ValueError(
                "environment.delete=false is not supported: the harness always removes this "
                "job's containers when the job ends; run Harbor directly to keep them"
            )
        return v


class HarborRunConfig(_Strict):
    schema_version: Literal[1]
    harbor: HarborBinary = Field(default_factory=HarborBinary)
    task: TaskRef
    agent: AgentSpec
    attempts: int = Field(default=1, ge=1, le=100)
    concurrency: int = Field(default=1, ge=1, le=64)
    max_retries: int = Field(default=0, ge=0, le=10)
    limits: Limits = Field(default_factory=Limits)
    environment: Environment = Field(default_factory=Environment)
    output_dir: Path = Path("runs/harbor")


def project_root(config_path: Path) -> Path:
    start = config_path.resolve().parent
    for d in (start, *start.parents):
        if (d / "pyproject.toml").is_file():
            return d
    return start


def load_config(path: Path) -> tuple[HarborRunConfig, Path]:
    """The validated config and the root its relative paths are taken from."""
    raw = yaml.safe_load(Path(path).read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping")
    return HarborRunConfig(**raw), project_root(Path(path))


def _abs(root: Path, p: Path) -> Path:
    p = p.expanduser()
    return p if p.is_absolute() else root / p


def resolve_task(cfg: HarborRunConfig, root: Path) -> dict[str, Any]:
    """The task directory and its pinned identity; raises if the content does not match."""
    if cfg.task.terminal_bench is not None:
        from harness.tasks import terminal_bench as tb

        name = cfg.task.terminal_bench
        if name not in tb.PINS:
            raise ValueError(f"{name!r} is not a pinned Terminal-Bench task: {sorted(tb.PINS)}")
        task_dir = tb.ensure_task(name)  # downloads once, then checks the content fingerprint
        pin = tb.PINS[name]
        return {
            "source": f"github:{tb.REPO}@{tb.COMMIT}/{name}",
            "path": str(task_dir),
            "fingerprint": pin.fingerprint,
            "pinned_image": pin.image,
        }
    task_dir = _abs(root, cfg.task.path)  # type: ignore[arg-type]
    if not (task_dir / "task.toml").is_file():
        raise ValueError(f"{task_dir} is not a Harbor task directory (no task.toml)")
    actual = fingerprint_dir(task_dir)
    if actual != cfg.task.fingerprint:
        raise ValueError(
            f"{task_dir} has fingerprint {actual}, the config pins {cfg.task.fingerprint}"
        )
    return {"source": f"local:{task_dir}", "path": str(task_dir), "fingerprint": actual,
            "pinned_image": None}  # fmt: skip


def capabilities(cfg: HarborRunConfig) -> list[dict[str, str]]:
    """How each limit and count is applied, for display and for the run manifest."""
    m = cfg.limits.timeout_multiplier
    return [
        {"limit": "prepare_s", "value": str(cfg.limits.prepare_s),
         "how": "enforced by harness: --print-config check before the job starts"},
        {"limit": "wall_clock_s", "value": str(cfg.limits.wall_clock_s),
         "how": "enforced by harness: Harbor process group stopped, this job's containers removed"},
        {"limit": "stop_grace_s", "value": str(cfg.limits.stop_grace_s),
         "how": "enforced by harness: SIGTERM, then SIGKILL of the process group"},
        {"limit": "cleanup_s", "value": str(cfg.limits.cleanup_s),
         "how": "enforced by harness: one deadline for all Docker cleanup calls; "
                "what is not confirmed in time is reported as unknown"},
        {"limit": "environment", "value": "deleted",
         "how": "this job's containers and networks are always removed when it ends"},
        {"limit": "phase timeouts", "value": f"task.toml x {m}",
         "how": "enforced by Harbor (environment build, agent setup, agent, verifier)"},
        {"limit": "attempts", "value": str(cfg.attempts), "how": "enforced by Harbor (-k)"},
        {"limit": "concurrency", "value": str(cfg.concurrency), "how": "enforced by Harbor (-n)"},
        {"limit": "max_retries", "value": str(cfg.max_retries),
         "how": "enforced by Harbor (-r); retries are recorded apart from attempts"},
        {"limit": "token / cost / steps", "value": "-",
         "how": "not supported by the pinned CLI; refused if set, usage recorded if reported"},
    ]  # fmt: skip


def build_argv(
    cfg: HarborRunConfig, root: Path, executable: str, task_dir: str, jobs_dir: Path, job_name: str
) -> list[str]:
    a = cfg.agent
    argv = [executable, "run", "--path", task_dir, "--agent", a.name]
    if a.model:
        argv += ["--model", a.model]
    for key, value in sorted(a.kwargs.items()):
        argv += ["--agent-kwarg", f"{key}={value}"]
    argv += [
        "--n-attempts", str(cfg.attempts),
        "--n-concurrent", str(cfg.concurrency),
        "--max-retries", str(cfg.max_retries),
        "--timeout-multiplier", str(cfg.limits.timeout_multiplier),
        "--env", cfg.environment.type,
        "--delete",
    ]  # fmt: skip
    for f in cfg.environment.extra_docker_compose:
        argv += ["--extra-docker-compose", str(_abs(root, f))]
    argv += ["--jobs-dir", str(jobs_dir), "--job-name", job_name, "--yes", "--quiet"]
    return argv
