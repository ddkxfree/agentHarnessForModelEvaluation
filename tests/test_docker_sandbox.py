"""Integration tests against a real Docker daemon. Skipped when Docker is unavailable."""

import asyncio

import pytest

from harness.core.model import get_model
from harness.core.runner import run_samples
from harness.core.task import get_task
from harness.core.types import GenerateConfig, Outcome
from harness.sandbox.base import SandboxError, SandboxSpec


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _docker_available(), reason="Docker is not available")

IMAGE = "python:3.12-slim"


def test_exec_files_timeout_and_isolation():
    from harness.sandbox.docker import DockerProvider

    async def scenario():
        provider = DockerProvider()
        sb = await provider.create(SandboxSpec(image=IMAGE), labels={"harness.test": "1"})
        try:
            res = await sb.exec("echo out; echo err >&2; exit 3")
            assert (res.exit_code, res.stdout, res.stderr) == (3, "out\n", "err\n")

            await sb.write_file("sub/dir/f.txt", "content")
            assert await sb.read_file("/app/sub/dir/f.txt") == b"content"
            assert (await sb.exec("cat sub/dir/f.txt")).stdout == "content"
            with pytest.raises(FileNotFoundError):
                await sb.read_file("/nope")

            slow = await sb.exec("sleep 10", timeout=1)
            assert slow.timed_out

            net = await sb.exec(
                "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 2)\""
            )
            assert net.exit_code != 0
        finally:
            await sb.close()
        containers = provider.client.containers.list(all=True, filters={"label": "harness.test"})
        assert containers == []

    asyncio.run(scenario())


def test_missing_image_is_sandbox_error():
    from harness.sandbox.docker import DockerProvider

    async def scenario():
        await DockerProvider().create(SandboxSpec(image="harness-does-not-exist:never"), labels={})

    with pytest.raises(SandboxError):
        asyncio.run(scenario())


def test_oracle_solves_every_builtin_terminal_task():
    from harness.sandbox.docker import DockerProvider

    task = get_task("terminal", solver="oracle")
    results = asyncio.run(
        run_samples(
            task,
            task.load(),
            get_model("none/none"),
            GenerateConfig(),
            sandbox_provider=DockerProvider(),
        )
    )
    assert [r.outcome for r in results] == [Outcome.CORRECT] * len(results), [
        (r.sample.id, r.error, r.score) for r in results
    ]


def test_untouched_environment_fails_every_builtin_terminal_task():
    """Guards against tests that pass without the agent doing anything."""
    from fakes import scripted

    from harness.sandbox.docker import DockerProvider

    task = get_task("terminal")
    results = asyncio.run(
        run_samples(
            task,
            task.load(),
            scripted("I am done."),
            GenerateConfig(),
            sandbox_provider=DockerProvider(),
        )
    )
    assert [r.outcome for r in results] == [Outcome.WRONG] * len(results)


@pytest.mark.parametrize("solver", ["agent", "mini"])
def test_time_limit_stops_a_hung_agent_and_removes_its_container(solver):
    if solver == "mini":
        pytest.importorskip("minisweagent")
    import time

    from fakes import call, scripted, tool_turn

    from harness.sandbox.docker import LABEL, DockerProvider

    task = get_task("terminal", solver=solver, ids=["hello-file"], time_limit=3)
    model = scripted(tool_turn(call("bash", '{"command": "sleep 600"}')))
    provider = DockerProvider()
    start = time.monotonic()
    [r] = asyncio.run(
        run_samples(task, task.load(), model, GenerateConfig(), sandbox_provider=provider)
    )
    assert time.monotonic() - start < 60
    assert r.outcome == Outcome.TIME_LIMIT, (r.error, r.score)
    assert r.cleanup_error is None
    assert r.sandbox["image_id"].startswith("sha256:")
    assert r.sandbox["image"].startswith("harness-sandbox:")
    left = provider.client.containers.list(all=True, filters={"label": LABEL})
    assert r.sandbox["container_id"] not in {c.id for c in left}


SWEBENCH_ID = "pytest-dev__pytest-5227"
SWEBENCH_IMAGE = "swebench/sweb.eval.x86_64.pytest-dev_1776_pytest-5227:latest"


def _have_image(name: str) -> bool:
    import docker

    try:
        docker.from_env().images.get(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _have_image(SWEBENCH_IMAGE), reason=f"{SWEBENCH_IMAGE} not pulled")
@pytest.mark.parametrize(
    ("solver", "model_reply", "outcome", "extracted"),
    [
        ("oracle", None, Outcome.CORRECT, "resolved"),
        ("agent", "I am done.", Outcome.WRONG, "empty_patch"),
    ],
)
def test_swebench_gold_patch_resolves_and_no_change_does_not(
    solver, model_reply, outcome, extracted
):
    pytest.importorskip("swebench")
    from fakes import scripted

    from harness.sandbox.docker import DockerProvider

    task = get_task("swebench", solver=solver, ids=[SWEBENCH_ID])
    model = scripted(model_reply) if model_reply else get_model("none/none")
    [r] = asyncio.run(
        run_samples(task, task.load(), model, GenerateConfig(), sandbox_provider=DockerProvider())
    )
    assert (r.outcome, r.score.extracted) == (outcome, extracted), (r.error, r.score)
