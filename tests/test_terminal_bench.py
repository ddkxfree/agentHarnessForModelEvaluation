"""Terminal-Bench 2.0 adapter: pinning, Harbor's reward rules and verifier flow, offline."""

import asyncio
import json

import pytest
from fakes import FakeSandbox

from harness.core.fingerprint import fingerprint_dir
from harness.core.task import TASKS, get_task
from harness.core.types import Sample
from harness.tasks import terminal_bench as tb


@pytest.mark.parametrize(
    ("reward_json", "reward_txt", "expected"),
    [
        ('{"reward": 1}', None, 1.0),
        ("0.5", None, 0.5),
        (None, "1\n", 1.0),
        (None, "0", 0.0),
        ('{"reward": 0}', "1", 0.0),
    ],
)
def test_reward_files_follow_harbor(reward_json, reward_txt, expected):
    assert tb.parse_reward(reward_json, reward_txt) == expected


@pytest.mark.parametrize(
    ("reward_json", "reward_txt"),
    [
        (None, None),
        (None, "nan"),
        (None, "inf"),
        (None, "yes"),
        ('{"reward": "1"}', None),
        ('{"reward": true}', None),
        ('{"score": 1}', None),
        ("NaN", None),
    ],
)
def test_missing_or_invalid_rewards_are_rejected(reward_json, reward_txt):
    with pytest.raises(ValueError):
        tb.parse_reward(reward_json, reward_txt)


def make_task_dir(root, name="demo", workdir_lines=("WORKDIR /app", "WORKDIR site")):
    d = root / name
    (d / "environment").mkdir(parents=True)
    (d / "tests").mkdir()
    (d / "solution").mkdir()
    (d / "instruction.md").write_text("Fix the thing.\n")
    (d / "task.toml").write_text(
        '[metadata]\ndifficulty = "easy"\ncategory = "demo"\n'
        "[verifier]\ntimeout_sec = 30.0\n[agent]\ntimeout_sec = 60.0\n"
        "[environment]\ncpus = 2\nmemory_mb = 512\nallow_internet = false\n"
    )
    (d / "environment/Dockerfile").write_text("FROM x\n" + "\n".join(workdir_lines) + "\n")
    (d / "tests/test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
    (d / "solution/solve.sh").write_text("#!/bin/bash\ntrue\n")
    return d


def test_loader_reads_harbor_metadata(tmp_path):
    d = make_task_dir(tmp_path)
    s = tb.load_task(d, "img@sha256:abc")
    assert s.id == "terminal-bench/demo" and s.input == "Fix the thing.\n"
    assert s.metadata["workdir"] == "/app/site"
    assert s.metadata["verifier_timeout"] == 30.0 and s.metadata["agent_timeout"] == 60.0
    spec = tb.sandbox_spec(s)
    assert (spec.image, spec.workdir, spec.network, spec.memory, spec.cpus) == (
        "img@sha256:abc",
        "/app/site",
        False,
        "512m",
        2.0,
    )


def test_downloaded_tasks_must_match_the_pinned_fingerprint(tmp_path, monkeypatch):
    source = make_task_dir(tmp_path / "upstream")
    calls = []

    def fake_download(name, dest):
        calls.append(name)
        dest.mkdir(parents=True)
        for f in source.rglob("*"):
            if f.is_file():
                (dest / f.relative_to(source)).parent.mkdir(parents=True, exist_ok=True)
                (dest / f.relative_to(source)).write_bytes(f.read_bytes())

    monkeypatch.setattr(tb, "download_task", fake_download)
    monkeypatch.setitem(tb.PINS, "demo", tb.Pin("img", fingerprint_dir(source)))
    cache = tmp_path / "cache"
    assert tb.ensure_task("demo", cache) == cache / "demo"
    assert tb.ensure_task("demo", cache) == cache / "demo"
    assert calls == ["demo"]

    (cache / "demo/tests/test.sh").write_text("#!/bin/bash\necho 1\n")
    with pytest.raises(ValueError, match="expected"):
        tb.ensure_task("demo", cache)


class HarborSandbox(FakeSandbox):
    """Runs nothing; the test script writes the configured reward files."""

    def __init__(self, rewards):
        super().__init__()
        self.rewards = rewards

    async def exec(self, cmd, *, timeout=60.0):
        self.commands.append(cmd)
        if cmd.startswith("/tests/test.sh"):
            self.files["/logs/verifier/test-stdout.txt"] = b"1 passed"
            self.files.update(self.rewards)
        from harness.sandbox.base import ExecResult

        return ExecResult(exit_code=0, stdout="", stderr="")

    async def read_file(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


def score(tmp_path, rewards):
    sample = tb.load_task(make_task_dir(tmp_path), "img")
    sandbox = HarborSandbox(rewards)
    return asyncio.run(tb.HarborVerifierScorer()(sample, "", sandbox)), sandbox


def test_verifier_uploads_tests_and_reads_the_reward(tmp_path):
    s, sandbox = score(tmp_path, {"/logs/verifier/reward.txt": b"1\n"})
    assert (s.value, s.extracted) == (1.0, "reward=1")
    assert "/tests/test.sh" in sandbox.files
    assert sandbox.commands[0].startswith("mkdir -p /logs/agent /logs/verifier && rm -f")
    assert sandbox.commands[-1] == "/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1"
    assert s.details["test_stdout_tail"] == "1 passed"


def test_no_reward_file_is_a_verifier_failure(tmp_path):
    with pytest.raises(ValueError, match="no reward file"):
        score(tmp_path, {})


def test_task_is_registered_with_pinned_revision():
    assert "terminal-bench" in TASKS
    task = get_task("terminal-bench", solver="oracle")
    assert task.dataset.revision == tb.COMMIT
    assert task.time_limit == 900.0
    assert all("@sha256:" in pin.image for pin in tb.PINS.values())
    assert len(tb.PINS) == 5
    desc = task.scorer.describe()
    assert desc["deviations_from_harbor"] == tb.DEVIATIONS
    with pytest.raises(KeyError, match="not pinned"):
        get_task("terminal-bench", ids=["hello-world"])


def test_agent_prompt_does_not_claim_a_fixed_workdir():
    task = get_task("terminal-bench")
    assert "/app" not in task.solver.system_prompt
    assert json.dumps(task.solver.describe())


def test_oracle_needs_a_sandbox():
    with pytest.raises(Exception, match="needs a sandbox"):
        asyncio.run(
            tb.HarborOracleSolver()(Sample(id="x", input="", target=""), None, None)  # type: ignore[arg-type]
        )
