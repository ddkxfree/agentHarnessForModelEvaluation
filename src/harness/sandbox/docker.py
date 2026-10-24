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

    def __init__(self, container: Any, workdir: str, image: dict[str, Any] | None = None) -> None:
        self._container = container
        self.workdir = workdir
        self._image = image or {}

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
        host = attrs.get("HostConfig", {})
        return {
            "type": "docker",
            "container_id": self._container.id,
            "container_name": self._container.name,
            "image": attrs.get("Config", {}).get("Image"),
            "image_id": attrs.get("Image"),
            **self._image,
            # As applied by the daemon, not as requested.
            "limits": {
                "memory_bytes": host.get("Memory"),
                "nano_cpus": host.get("NanoCpus"),
                "network_mode": host.get("NetworkMode"),
            },
        }


class DockerProvider(SandboxProvider):
    name = "docker"

    def __init__(self) -> None:
        self._client: Any = None
        self._images: set[str] = set()
        self._build_lock = asyncio.Lock()
        self._pending: set[asyncio.Future[Any]] = set()

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
        # Until the sandbox is returned nobody else can remove the container, so every failure
        # or cancellation from here on must remove it.
        run = asyncio.ensure_future(
            _docker(
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
        )
        try:
            container = await asyncio.shield(run)
        except asyncio.CancelledError:
            # The SDK call keeps running in its thread; remove the container once it exists.
            self._pending.add(run)
            run.add_done_callback(self._remove_orphan)
            raise
        try:
            info = await _docker(self._image_info, image)
        except BaseException as exc:
            await asyncio.shield(self._discard(container, exc))
            raise
        return DockerSandbox(container, spec.workdir, info)

    async def _discard(self, container: Any, cause: BaseException) -> None:
        try:
            await asyncio.to_thread(container.remove, force=True)
        except Exception as exc:
            cause.add_note(f"removing container {container.id} also failed: {exc!r}")

    def _remove_orphan(self, run: asyncio.Future[Any]) -> None:
        self._pending.discard(run)
        if run.cancelled() or run.exception() is not None:
            return
        container = run.result()
        task = asyncio.ensure_future(asyncio.to_thread(container.remove, force=True))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        """Wait for removals of containers whose creation was cancelled."""
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)
            # let done callbacks run; they may schedule the removal itself
            await asyncio.sleep(0)

    def _image_info(self, image: str) -> dict[str, Any]:
        attrs = self.client.images.get(image).attrs
        return {
            # Registry digests identify pulled images across machines; locally built ones
            # have none and are identified by their content-addressed build context tag.
            "image_digests": attrs.get("RepoDigests") or [],
            "image_platform": f"{attrs.get('Os')}/{attrs.get('Architecture')}",
        }

    def describe(self) -> dict[str, Any]:
        desc: dict[str, Any] = {"name": self.name}
        try:
            info = self.client.info()
            version = self.client.version()
        except Exception as exc:
            desc["error"] = f"{type(exc).__name__}: {exc}"
            return desc
        desc["daemon"] = {
            "version": version.get("Version"),
            "api_version": version.get("ApiVersion"),
            "os": info.get("OperatingSystem"),
            "platform": f"{info.get('OSType')}/{info.get('Architecture')}",
            "kernel": info.get("KernelVersion"),
            "cpus": info.get("NCPU"),
            "memory_bytes": info.get("MemTotal"),
        }
        return desc

    def cleanup(self) -> int:
        """Remove every container this harness started, e.g. after a crash."""
        containers = self.client.containers.list(all=True, filters={"label": LABEL})
        for c in containers:
            c.remove(force=True)
        return len(containers)
