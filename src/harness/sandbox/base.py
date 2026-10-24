from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class SandboxError(Exception):
    """The environment failed, as opposed to the model. Reported as ``env_error``."""


class SandboxSpec(BaseModel):
    """What environment a sample needs. Either a ready image or a Docker build context."""

    image: str | None = None
    build_context: Path | None = None
    workdir: str = "/app"
    network: bool = False
    memory: str = "1g"
    cpus: float = 1.0

    def describe(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ExecResult(BaseModel):
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    def combined(self, limit: int | None = None) -> str:
        text = self.stdout + (f"\n[stderr]\n{self.stderr}" if self.stderr.strip() else "")
        if limit is not None and len(text) > limit:
            half = limit // 2
            omitted = len(text) - limit
            text = f"{text[:half]}\n... [{omitted} characters omitted] ...\n{text[-half:]}"
        return text


class Sandbox(ABC):
    """An isolated environment that one sample's solver and scorer act on."""

    @abstractmethod
    async def exec(self, cmd: str, *, timeout: float = 60.0) -> ExecResult: ...

    @abstractmethod
    async def write_file(self, path: str, content: bytes | str) -> None: ...

    @abstractmethod
    async def read_file(self, path: str) -> bytes: ...

    async def copy_dir(self, src: Path, dest: str) -> None:
        for file in sorted(Path(src).rglob("*")):
            if file.is_file():
                await self.write_file(
                    f"{dest}/{file.relative_to(src).as_posix()}", file.read_bytes()
                )

    @abstractmethod
    async def close(self) -> None: ...

    def describe(self) -> dict[str, Any]:
        """Identity of this sandbox, recorded with the sample (e.g. container and image ids)."""
        return {"type": type(self).__name__}


class SandboxProvider(ABC):
    name: str

    @abstractmethod
    async def create(self, spec: SandboxSpec, *, labels: dict[str, str]) -> Sandbox: ...

    def describe(self) -> dict[str, Any]:
        return {"name": self.name}

    async def drain(self) -> None:  # noqa: B027 - optional hook, a no-op by default
        """Finish background cleanup, e.g. of sandboxes whose creation was cancelled."""
