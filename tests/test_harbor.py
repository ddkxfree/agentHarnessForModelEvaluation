"""Harbor backend: version pin, config, argv, import and lifecycle, without Harbor or network."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from harness.cli import app
from harness.integrations.harbor import PINNED_VERSION
from harness.integrations.harbor import config as hc
from harness.integrations.harbor import runtime as hr


def fake_harbor(tmp_path: Path, version: str = PINNED_VERSION, body: str = "") -> Path:
    exe = tmp_path / "venv" / "bin" / "harbor"
    exe.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.3\n")
    exe.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "--version" ]; then echo {version}; exit 0; fi\n'
        f"{body}\n"
    )
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


# --- G0: pinned version -------------------------------------------------------------------


def test_probe_accepts_only_the_pinned_version(tmp_path):
    rt = hr.probe(fake_harbor(tmp_path))
    assert rt.version == PINNED_VERSION
    assert rt.python == "3.12.3"
    with pytest.raises(hr.HarborVersionMismatch, match="only 0.25.0"):
        hr.probe(fake_harbor(tmp_path / "other", version="0.26.0"))


def test_probe_never_falls_back_to_path(tmp_path, monkeypatch):
    on_path = fake_harbor(tmp_path / "onpath")
    monkeypatch.setenv("PATH", f"{on_path.parent}:{os.environ['PATH']}")
    monkeypatch.setenv("HARNESS_HARBOR_HOME", str(tmp_path / "nothing"))
    monkeypatch.delenv("HARNESS_HARBOR_BIN", raising=False)
    with pytest.raises(hr.HarborNotFound, match="install Harbor 0.25.0"):
        hr.probe()
    monkeypatch.setenv("HARNESS_HARBOR_BIN", str(on_path))
    assert hr.probe().executable == str(on_path)


def test_probe_reports_a_broken_executable(tmp_path):
    exe = tmp_path / "harbor"
    exe.write_text("#!/bin/sh\necho boom >&2\nexit 3\n")
    exe.chmod(0o755)
    with pytest.raises(hr.HarborError, match="failed \\(3\\)"):
        hr.probe(exe)


def test_base_env_carries_no_credentials(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    env = hr.base_env()
    assert "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert env["NO_COLOR"] == "1"


# --- G0: config ---------------------------------------------------------------------------


def write_cfg(tmp_path: Path, **over) -> Path:
    (tmp_path / "pyproject.toml").write_text("")
    raw = {"schema_version": 1, "task": {"terminal_bench": "fix-git"}, "agent": {"name": "oracle"}}
    raw.update(over)
    path = tmp_path / "configs" / "h.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw))
    return path


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"limits": {"token_limit": 1000}}, "cannot be enforced"),
        ({"limits": {"cost_limit_usd": 1.0}}, "cannot be enforced"),
        ({"limits": {"max_steps": 30}}, "cannot be enforced"),
        ({"agent": {"name": "x", "kwargs": {"api_key": "v"}}}, "credential"),
        ({"agent": {"name": "x", "env": ["lower"]}}, "invalid environment"),
        ({"agent": {"name": "Bad Name"}}, "invalid agent name"),
        ({"task": {"terminal_bench": "fix-git", "path": "x"}}, "exactly one"),
        ({"task": {"path": "x"}}, "fingerprint"),
        ({"harbor": {"version": "0.26.0"}}, "0.25.0"),
        ({"extra_args": ["--foo"]}, "Extra inputs"),
        ({"schema_version": 2}, "schema_version"),
    ],
)
def test_config_refuses_what_harbor_cannot_honour(tmp_path, over, message):
    with pytest.raises(ValidationError, match=message):
        hc.load_config(write_cfg(tmp_path, **over))


def test_argv_maps_every_field_and_paths_are_root_relative(tmp_path):
    path = write_cfg(
        tmp_path,
        agent={"name": "terminus-2", "model": "m/x", "kwargs": {"b": "2", "a": "1"}},
        attempts=3,
        concurrency=2,
        max_retries=1,
        limits={"timeout_multiplier": 1.5},
        environment={"extra_docker_compose": ["configs/net.yaml"], "delete": False},
    )
    cfg, root = hc.load_config(path)
    assert root == tmp_path.resolve()
    argv = hc.build_argv(cfg, root, "/h", "/task", tmp_path / "jobs", "j1")
    assert argv == [
        "/h", "run", "--path", "/task", "--agent", "terminus-2", "--model", "m/x",
        "--agent-kwarg", "a=1", "--agent-kwarg", "b=2",
        "--n-attempts", "3", "--n-concurrent", "2", "--max-retries", "1",
        "--timeout-multiplier", "1.5", "--env", "docker", "--no-delete",
        "--extra-docker-compose", str(root / "configs" / "net.yaml"),
        "--jobs-dir", str(tmp_path / "jobs"), "--job-name", "j1", "--yes", "--quiet",
    ]  # fmt: skip


def test_local_task_must_match_its_fingerprint(tmp_path):
    task = tmp_path / "mytask"
    task.mkdir()
    (task / "task.toml").write_text("[agent]\ntimeout_sec = 10\n")
    path = write_cfg(tmp_path, task={"path": "mytask", "fingerprint": "0" * 16})
    cfg, root = hc.load_config(path)
    with pytest.raises(ValueError, match="the config pins"):
        hc.resolve_task(cfg, root)
    from harness.core.fingerprint import fingerprint_dir

    cfg.task.fingerprint = fingerprint_dir(task)
    assert hc.resolve_task(cfg, root)["path"] == str(task)


def test_capabilities_say_how_each_limit_is_applied(tmp_path):
    cfg, _ = hc.load_config(write_cfg(tmp_path))
    rows = {r["limit"]: r["how"] for r in hc.capabilities(cfg)}
    assert "enforced by harness" in rows["wall_clock_s"]
    assert "not supported" in rows["token / cost / steps"]


def test_doctor_fails_without_harbor_and_runs_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HARBOR_HOME", str(tmp_path / "none"))
    monkeypatch.delenv("HARNESS_HARBOR_BIN", raising=False)
    monkeypatch.setattr(hr, "check_docker", lambda **_: {"daemon": {"ok": True, "detail": "x"}})
    result = CliRunner().invoke(app, ["harbor", "doctor"])
    assert result.exit_code == 1
    assert "nothing was run" in result.output


def test_doctor_reports_missing_agent_env_without_values(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_HARBOR_BIN", str(fake_harbor(tmp_path)))
    monkeypatch.setattr(hr, "check_docker", lambda **_: {"daemon": {"ok": True, "detail": "x"}})
    monkeypatch.setattr(hc, "resolve_task", lambda cfg, root: {"source": "s", "fingerprint": "f"})
    monkeypatch.delenv("SOME_KEY", raising=False)
    cfg = write_cfg(tmp_path, agent={"name": "terminus-2", "env": ["SOME_KEY"]})
    result = CliRunner().invoke(app, ["harbor", "doctor", "-c", str(cfg)])
    assert result.exit_code == 1 and "SOME_KEY" in result.output
    monkeypatch.setenv("SOME_KEY", "very-secret-value")
    result = CliRunner().invoke(app, ["harbor", "doctor", "-c", str(cfg)])
    assert result.exit_code == 0, result.output
    assert "very-secret-value" not in result.output
