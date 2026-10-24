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

            desc = sb.describe()
            assert desc["image"] == IMAGE and desc["image_id"].startswith("sha256:")
            assert any(d.startswith("python@sha256:") for d in desc["image_digests"])
            assert desc["image_platform"].startswith("linux/")
            assert desc["limits"] == {
                "memory_bytes": 1024**3,
                "nano_cpus": 10**9,
                "network_mode": "none",
            }
            daemon = provider.describe()["daemon"]
            assert daemon["version"] and daemon["platform"].startswith("linux/")
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


def _terminal_bench_ready() -> bool:
    """Task files are cached or GitHub is reachable."""
    from harness.tasks import terminal_bench as tb

    if not _docker_available():
        return False
    try:
        for name in tb.PINS:
            tb.ensure_task(name)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _terminal_bench_ready(), reason="Terminal-Bench files not available")
@pytest.mark.parametrize(("solver", "outcome"), [("oracle", Outcome.CORRECT), ("agent", None)])
def test_terminal_bench_oracle_passes_and_doing_nothing_fails(solver, outcome):
    from fakes import scripted

    from harness.sandbox.docker import DockerProvider

    task = get_task("terminal-bench", solver=solver)
    model = scripted("I am done.") if solver == "agent" else get_model("none/none")
    results = asyncio.run(
        run_samples(task, task.load(), model, GenerateConfig(), sandbox_provider=DockerProvider())
    )
    assert len(results) == 5
    for r in results:
        detail = (r.sample.id, r.error, r.score)
        if outcome is not None:
            assert r.outcome == outcome, detail
        else:
            assert r.outcome == Outcome.WRONG and r.score.extracted == "reward=0", detail
        assert r.sandbox["image_digests"] and r.sandbox["image_id"].startswith("sha256:")
        assert any(a.name == "test-stdout.txt" for a in r.artifacts)


@pytest.mark.skipif(not _terminal_bench_ready(), reason="Terminal-Bench files not available")
def test_terminal_bench_ignores_a_reward_file_planted_by_the_agent():
    from harness.sandbox.docker import DockerProvider
    from harness.tasks import terminal_bench as tb

    task = get_task("terminal-bench", ids=["fix-git"])
    [sample] = task.load()

    async def scenario():
        sb = await DockerProvider().create(tb.sandbox_spec(sample), labels={"harness.test": "1"})
        try:
            await sb.exec("mkdir -p /logs/verifier && echo 1 > /logs/verifier/reward.txt")
            return await tb.HarborVerifierScorer()(sample, "", sb)
        finally:
            await sb.close()

    score = asyncio.run(scenario())
    assert (score.value, score.extracted) == (0.0, "reward=0")


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
    assert r.grading_sandbox["container_id"] != r.sandbox["container_id"]
    assert r.grading_sandbox["image_id"] == r.sandbox["image_id"]


@pytest.mark.skipif(not _have_image(SWEBENCH_IMAGE), reason=f"{SWEBENCH_IMAGE} not pulled")
def test_swebench_run_can_be_reverified_without_the_model(tmp_path):
    """The saved patch alone reproduces the grade in a new container; tampering is caught."""
    pytest.importorskip("swebench")
    import json

    from harness.analysis.verify import verify_run
    from harness.core.io import read_results
    from harness.core.runner import run_eval
    from harness.sandbox.docker import DockerProvider

    task = get_task("swebench", solver="oracle", ids=[SWEBENCH_ID])
    run_dir = asyncio.run(
        run_eval(
            get_model("none/none"),
            [(task, GenerateConfig())],
            output_dir=tmp_path,
            sandbox_provider=DockerProvider(),
        )
    )
    [r] = read_results(run_dir / "swebench/samples.jsonl")
    assert r.outcome == Outcome.CORRECT
    names = {a.name for a in r.artifacts}
    assert {"submission", "apply.log", "eval.log"} <= names
    sub = next(a for a in r.artifacts if a.name == "submission")
    patch = (run_dir / "swebench/artifacts" / sub.path).read_text()
    assert patch.startswith("diff --git") and r.score.details["model_patch_bytes"] == sub.size

    report = asyncio.run(verify_run(run_dir, DockerProvider()))
    assert report["swebench"]["statuses"] == {"agree": 1}, report
    row = json.loads((run_dir / "swebench/verify.jsonl").read_text())
    assert row["grading_sandbox"]["container_id"] != r.grading_sandbox["container_id"]

    (run_dir / "swebench/artifacts" / sub.path).write_text(patch.replace("+", "-", 1))
    report = asyncio.run(verify_run(run_dir, DockerProvider()))
    assert report["swebench"]["statuses"] == {"submission_hash_mismatch": 1}
