"""DockerProvider.create must not leak a started container, using a fake Docker client."""

import asyncio
import threading

import pytest

pytest.importorskip("docker")

from harness.sandbox.base import SandboxError, SandboxSpec  # noqa: E402
from harness.sandbox.docker import DockerProvider  # noqa: E402


class FakeContainer:
    id = "c0ffee"
    short_id = "c0ffee"
    name = "fake"
    attrs: dict = {}

    def __init__(self):
        self.removed = False

    def remove(self, force=False):
        self.removed = True


class FakeImages:
    def __init__(self, error=None, gate=None):
        self.error = error
        self.gate = gate

    def get(self, image):
        if self.gate is not None:
            self.gate.wait(5)
        if self.error is not None:
            raise self.error
        return type("Image", (), {"attrs": {"RepoDigests": [], "Os": "linux"}})()


class FakeContainers:
    def __init__(self, gate=None):
        self.gate = gate
        self.started: list[FakeContainer] = []

    def run(self, image, **kwargs):
        if self.gate is not None:
            self.gate.wait(5)
        c = FakeContainer()
        self.started.append(c)
        return c


class FakeClient:
    def __init__(self, run_gate=None, image_error=None, image_gate=None):
        self.containers = FakeContainers(run_gate)
        self.images = FakeImages(image_error, image_gate)


def provider(client):
    p = DockerProvider()
    p._client = client
    return p


SPEC = SandboxSpec(image="img")


def test_normal_create_keeps_the_container():
    client = FakeClient()
    sandbox = asyncio.run(provider(client).create(SPEC, labels={}))
    assert sandbox.describe()["container_id"] == "c0ffee"
    assert not client.containers.started[0].removed


@pytest.mark.parametrize("error", [RuntimeError("boom")])
def test_image_lookup_failure_removes_the_container_and_keeps_the_error(error):
    import docker

    client = FakeClient(image_error=error)
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(provider(client).create(SPEC, labels={}))
    assert client.containers.started[0].removed

    client = FakeClient(image_error=docker.errors.ImageNotFound("gone"))
    with pytest.raises(SandboxError, match="gone"):
        asyncio.run(provider(client).create(SPEC, labels={}))
    assert client.containers.started[0].removed


def test_failed_removal_is_noted_on_the_original_error():
    client = FakeClient(image_error=RuntimeError("boom"))

    def broken_remove(self, force=False):
        raise OSError("daemon gone")

    FakeContainerBroken = type("Broken", (FakeContainer,), {"remove": broken_remove})
    client.containers.run = lambda image, **kw: FakeContainerBroken()
    with pytest.raises(RuntimeError, match="boom") as info:
        asyncio.run(provider(client).create(SPEC, labels={}))
    assert any("daemon gone" in note for note in info.value.__notes__)


def test_cancelled_while_starting_removes_the_container_once_it_exists():
    gate = threading.Event()
    client = FakeClient(run_gate=gate)
    p = provider(client)

    async def scenario():
        task = asyncio.create_task(p.create(SPEC, labels={}))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.containers.started == []
        gate.set()
        await p.drain()

    asyncio.run(scenario())
    [container] = client.containers.started
    assert container.removed


def test_cancelled_while_reading_image_info_removes_the_container():
    gate = threading.Event()
    client = FakeClient(image_gate=gate)
    p = provider(client)

    async def scenario():
        task = asyncio.create_task(p.create(SPEC, labels={}))
        while not client.containers.started:
            await asyncio.sleep(0.01)
        task.cancel()
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert client.containers.started[0].removed
