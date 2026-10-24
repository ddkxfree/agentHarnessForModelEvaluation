from __future__ import annotations

import asyncio
import io
import posixpath
import tarfile
from collections.abc import Callable
from typing import Any, TypeVar

from harness.core.fingerprint import fingerprint_dir
from harness.sandbox.base import ExecResult, Sandbox, SandboxError, SandboxProvider, SandboxSpec

LABEL = "harness.sandbox"
TIMEOUT_EXIT_CODES = {124, 137}

T = TypeVar("T")


async def _docker(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    import docker

    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except docker.errors.DockerException as exc:
        raise SandboxError(f"{type(exc).__name__}: {exc}") from exc


class DockerSandbox(Sandbox):
    """One container per sample. Images must provide ``bash`` and coreutils ``timeout``."""

    def __init__(self, container: Any, workdir: str) -> None:
        self._container = container
        self.workdir = workdir

    @property
    def id(self) -> str:
        return self._container.short_id

    async def exec(self, cmd: str, *, timeout: float = 60.0) -> ExecResult:
        argv = ["timeout", "-k", "5", str(max(1, int(timeout))), "bash", "-lc", cmd]
        res = await _docker(self._container.exec_run, argv, demux=True, workdir=self.workdir)
        stdout, stderr = res.output if res.output else (None, None)
        return ExecResult(
            exit_code=res.exit_code,
            stdout=(stdout or b"").decode(errors="replace"),
            stderr=(stderr or b"").decode(errors="replace"),
            timed_out=res.exit_code in TIMEOUT_EXIT_CODES,
        )

    async def write_file(self, path: str, content: bytes | str) -> None:
        data = content.encode() if isinstance(content, str) else content
        path = posixpath.join(self.workdir, path)
        parent, name = posixpath.split(path)
        mkdir = await self.exec(f"mkdir -p '{parent}'")
        if mkdir.exit_code != 0:
            raise SandboxError(f"cannot create {parent}: {mkdir.stderr.strip()}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o755 if data.startswith(b"#!") else 0o644
            tar.addfile(info, io.BytesIO(data))
        if not await _docker(self._container.put_archive, parent, buf.getvalue()):
            raise SandboxError(f"failed to write {path} into container {self.id}")

    async def read_file(self, path: str) -> bytes:
        import docker

        path = posixpath.join(self.workdir, path)
        try:
            stream, _ = await asyncio.to_thread(self._container.get_archive, path)
        except docker.errors.NotFound as exc:
            raise FileNotFoundError(path) from exc
        buf = io.BytesIO(b"".join(stream))
        with tarfile.open(fileobj=buf) as tar:
            member = tar.next()
            f = tar.extractfile(member) if member else None
            if f is None:
                raise FileNotFoundError(path)
            return f.read()

    async def close(self) -> None:
        await _docker(self._container.remove, force=True)

    def describe(self) -> dict[str, Any]:
        attrs = self._container.attrs or {}
        return {
            "type": "docker",
            "container_id": self._container.id,
            "container_name": self._container.name,
            "image": attrs.get("Config", {}).get("Image"),
            "image_id": attrs.get("Image"),
        }


class DockerProvider(SandboxProvider):
    name = "docker"

    def __init__(self) -> None:
        self._client: Any = None
        self._images: set[str] = set()
        self._build_lock = asyncio.Lock()

    @property
    def client(self) -> Any:
        if self._client is None:
            import docker

            try:
                self._client = docker.from_env()
            except docker.errors.DockerException as exc:
                raise SandboxError(f"cannot connect to Docker: {exc}") from exc
        return self._client

    async def image_for(self, spec: SandboxSpec) -> str:
        if spec.image:
            return spec.image
        if spec.build_context is None:
            raise ValueError("SandboxSpec needs either image or build_context")
        tag = f"harness-sandbox:{fingerprint_dir(spec.build_context)}"
        async with self._build_lock:
            if tag not in self._images:
                await _docker(self._build, spec, tag)
                self._images.add(tag)
        return tag

    def _build(self, spec: SandboxSpec, tag: str) -> None:
        import docker

        try:
            self.client.images.get(tag)
        except docker.errors.ImageNotFound:
            self.client.images.build(path=str(spec.build_context), tag=tag, rm=True)

    async def create(self, spec: SandboxSpec, *, labels: dict[str, str]) -> Sandbox:
        image = await self.image_for(spec)
        container = await _docker(
            self.client.containers.run,
            image,
            command=["sleep", "infinity"],
            detach=True,
            init=True,
            working_dir=spec.workdir,
            network_mode="bridge" if spec.network else "none",
            mem_limit=spec.memory,
            nano_cpus=int(spec.cpus * 1e9),
            labels={LABEL: "1", **labels},
        )
        return DockerSandbox(container, spec.workdir)

    def cleanup(self) -> int:
        """Remove every container this harness started, e.g. after a crash."""
        containers = self.client.containers.list(all=True, filters={"label": LABEL})
        for c in containers:
            c.remove(force=True)
        return len(containers)
