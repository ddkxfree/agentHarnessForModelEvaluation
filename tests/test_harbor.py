"""Harbor backend: version pin, config, argv, import and lifecycle, without Harbor or network."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from harness.cli import app
from harness.integrations.harbor import PINNED_VERSION
from harness.integrations.harbor import config as hc
from harness.integrations.harbor import importer as hi
from harness.integrations.harbor import mapping as hm
from harness.integrations.harbor import runtime as hr


def fake_harbor(tmp_path: Path, version: str = PINNED_VERSION, body: str = "") -> Path:
    exe = tmp_path / "venv" / "bin" / "harbor"
    exe.parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = /usr/bin\nversion_info = 3.12.3\n")
    exe.write_text(
        f'#!/bin/sh\nif [ "$1" = "--version" ]; then echo {version}; exit 0; fi\n{body}\n'
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
        ({"limits": {"token_limit": 0}}, "cannot be enforced"),
        ({"limits": {"cost_limit_usd": 0}}, "cannot be enforced"),
        ({"limits": {"max_steps": 0}}, "cannot be enforced"),
        ({"limits": {"cost_limit_usd": -1.0}}, "cannot be enforced"),
        ({"environment": {"delete": False}}, "delete=false is not supported"),
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
        environment={"extra_docker_compose": ["configs/net.yaml"]},
    )
    cfg, root = hc.load_config(path)
    assert root == tmp_path.resolve()
    argv = hc.build_argv(cfg, root, "/h", "/task", tmp_path / "jobs", "j1")
    assert argv == [
        "/h", "run", "--path", "/task", "--agent", "terminus-2", "--model", "m/x",
        "--agent-kwarg", "a=1", "--agent-kwarg", "b=2",
        "--n-attempts", "3", "--n-concurrent", "2", "--max-retries", "1",
        "--timeout-multiplier", "1.5", "--env", "docker", "--delete",
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


# --- G1: offline import -------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures" / "harbor" / "0.25.0"


def job_copy(tmp_path: Path, name: str) -> Path:
    import shutil

    dest = tmp_path / "jobs" / name
    shutil.copytree(FIXTURES / name, dest)
    return dest


def trials_of(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "trials.jsonl").read_text().splitlines()]


def edit_json(path: Path, fn) -> None:
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def test_import_real_scored_failure(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    res = hi.import_job(job, tmp_path / "out")
    rec = res["record"]
    assert res["action"] == "created" and rec["backend"] == "harbor"
    assert rec["harbor"] == {"version": "0.25.0", "lock_schema": 3}
    assert rec["job"]["status"] == "completed" and rec["job"]["n_missing"] == 0
    (t,) = trials_of(res["run_dir"])
    assert t["outcome"] == "wrong" and t["rewards"] == {"reward": 0.0}
    assert t["exception"] is None and t["repeat"] == 0
    assert t["usage"]["status"] == "not_applicable" and t["usage"]["input_tokens"] is None
    assert t["timing_s"]["verifier"] > 100
    assert hi.verify_snapshot(res["run_dir"]) == []


def test_import_real_environment_error_keeps_the_exception(tmp_path):
    res = hi.import_job(job_copy(tmp_path, "oracle-env-error"), tmp_path / "out")
    (t,) = trials_of(res["run_dir"])
    assert t["status"] == "error" and t["outcome"] == "error" and t["rewards"] is None
    assert t["exception"]["type"] == "RuntimeError"
    assert t["exception"]["category"] == "unknown"  # not guessed as an API error
    assert t["exception"]["phase"] == "environment_setup"
    assert res["record"]["job"]["outcomes"] == {"error": 1}


def test_reimport_is_idempotent_and_changes_need_replace(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    first = hi.import_job(job, tmp_path / "out")
    again = hi.import_job(job, tmp_path / "out")
    assert again["action"] == "unchanged" and again["run_dir"] == first["run_dir"]
    assert len(trials_of(first["run_dir"])) == 1
    (job / "job.log").write_text("more\n")
    with pytest.raises(hi.ImportConflict):
        hi.import_job(job, tmp_path / "out")
    assert hi.import_job(job, tmp_path / "out", replace=True)["action"] == "replaced"
    assert (first["run_dir"] / "raw" / "job.log").read_text() == "more\n"


def test_import_leaves_the_source_untouched(tmp_path):
    job = job_copy(tmp_path, "oracle-env-error")
    before = {p: p.read_bytes() for p in job.rglob("*") if p.is_file()}
    hi.import_job(job, tmp_path / "out")
    assert {p: p.read_bytes() for p in job.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (lambda lock: lock["harbor"].update(version="0.26.0"), "only 0.25.0"),
        (lambda lock: lock.update(schema_version=4), "lock schema 4"),
        (lambda lock: lock.pop("trials"), "no trial list"),
    ],
)
def test_other_versions_and_schemas_are_refused(tmp_path, edit, message):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    edit_json(job / "lock.json", edit)
    with pytest.raises(hi.IncompatibleJob, match=message):
        hi.import_job(job, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_not_a_job_directory(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(hi.IncompatibleJob, match="not a Harbor job"):
        hi.import_job(tmp_path / "empty", tmp_path / "out")


def test_multiple_and_partial_rewards_are_not_aggregated(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    result = job / "fix-git__Bu66kU2" / "result.json"
    edit_json(result, lambda r: r["verifier_result"].update(rewards={"reward": 1.0, "style": 0.5}))
    (t,) = trials_of(hi.import_job(job, tmp_path / "a")["run_dir"])
    assert t["outcome"] == "unscored" and "not aggregated" in t["score_note"]
    assert t["rewards"] == {"reward": 1.0, "style": 0.5}
    edit_json(result, lambda r: r["verifier_result"].update(rewards={"reward": 0.5}))
    (t,) = trials_of(hi.import_job(job, tmp_path / "b")["run_dir"])
    assert t["outcome"] == "unscored" and "partial" in t["score_note"]
    edit_json(result, lambda r: r["verifier_result"].update(rewards={"reward": 1}))
    (t,) = trials_of(hi.import_job(job, tmp_path / "c")["run_dir"])
    assert t["outcome"] == "correct"


def test_model_agent_without_usage_is_unknown_not_zero(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    result = job / "fix-git__Bu66kU2" / "result.json"

    def as_model_agent(r):
        r["agent_info"] = {"name": "terminus-2", "version": "2", "model_info": {"name": "m"}}

    edit_json(result, as_model_agent)
    rec = hi.import_job(job, tmp_path / "a")
    (t,) = trials_of(rec["run_dir"])
    assert t["usage"]["status"] == "unknown" and t["usage"]["input_tokens"] is None
    assert rec["record"]["job"]["usage_reported"] == "0/1"

    def reported(r):
        r["agent_result"].update(n_input_tokens=120, n_output_tokens=30)

    edit_json(result, reported)
    (t,) = trials_of(hi.import_job(job, tmp_path / "b")["run_dir"])
    assert t["usage"]["status"] == "reported" and t["usage"]["input_tokens"] == 120
    assert t["usage"]["cost_usd"] is None


def test_running_job_and_missing_trials_are_visible(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    (job / "result.json").unlink()
    trial = job / "fix-git__Bu66kU2"
    (trial / "result.json").unlink()
    edit_json(job / "lock.json", lambda lock: lock["trials"].append(lock["trials"][0]))
    rec = hi.import_job(job, tmp_path / "out")["record"]
    assert rec["job"]["status"] == "incomplete"
    assert rec["job"]["n_total_trials"] == 2 and rec["job"]["n_missing"] == 1
    assert rec["job"]["outcomes"] == {"missing": 1, "running": 1}
    (missing,) = [
        t for t in trials_of(tmp_path / "out" / rec["run_id"]) if t["status"] == "missing"
    ]
    assert missing["task_name"] == "fix-git" and missing["task_path"]


def test_missing_planned_trials_count_against_the_score_and_are_flagged(tmp_path):
    from harness.results import open_run
    from harness.results.analysis import diff_views

    job = job_copy(tmp_path, "oracle-verifier-offline")
    (job / "result.json").unlink()
    edit_json(job / "fix-git__Bu66kU2" / "result.json",
              lambda r: r["verifier_result"].update(rewards={"reward": 1.0}))  # fmt: skip
    edit_json(job / "lock.json", lambda lock: lock["trials"].append(lock["trials"][0]))
    run = hi.import_job(job, tmp_path / "out")["run_dir"]
    view = open_run(run)
    acc = view.accuracy()
    assert acc["n"] == 2 and acc["n_correct"] == 1 and acc["accuracy"] == 0.5
    assert acc["accuracy_of_scored"] == 1.0
    assert view.outcomes() == {"correct": 1, "missing": 1}
    assert view.completeness() == {"planned": 2, "missing": 1, "unfinished": 0,
                                   "complete": False}  # fmt: skip
    assert len({s.key for s in view.samples}) == 2
    r = CliRunner()
    out = r.invoke(app, ["show", str(run)])
    assert out.exit_code == 0 and "WARNING incomplete run: 1 of 2" in " ".join(out.output.split())
    r.invoke(app, ["report", str(run)])
    assert "incomplete run: 1 of 2" in (run / "report.md").read_text()
    full = hi.import_job(job_copy(tmp_path / "f", "oracle-verifier-offline"), tmp_path / "o2")
    d = diff_views(open_run(full["run_dir"]), view)
    assert any(n.startswith("b: incomplete run") for n in d["notes"])
    out = r.invoke(app, ["diff", str(full["run_dir"]), str(run)])
    assert "incomplete run" in " ".join(out.output.split())


def test_unknown_exception_type_stays_unknown():
    r = {"exception_info": {"exception_type": "BrandNewError"}, "agent_info": {"name": "x"}}
    assert hm.map_trial(r, None)["exception"]["category"] == "unknown"
    r["exception_info"]["exception_type"] = "AgentTimeoutError"
    assert hm.map_trial(r, None)["exception"]["category"] == "agent_timeout"


def test_repeats_are_numbered_per_task_and_agent():
    def t(name, started, task="a"):
        return {"trial_name": name, "task_name": task, "task_checksum": "c",
                "agent": {"name": "oracle", "model": None}, "started_at": started}  # fmt: skip

    trials = [t("x2", "2"), t("x1", "1"), t("y1", "1", task="b")]
    hm.number_repeats(trials)
    assert [tr["repeat"] for tr in trials] == [1, 0, 0]


def test_symlinks_and_special_files_are_not_followed(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    secret = tmp_path / "outside.txt"
    secret.write_text("host secret")
    (job / "fix-git__Bu66kU2" / "agent" / "leak.txt").symlink_to(secret)
    (job / "linkdir").symlink_to(tmp_path)
    os.mkfifo(job / "pipe")
    res = hi.import_job(job, tmp_path / "out")
    skipped = {e["path"]: e["skipped"] for e in res["record"]["raw"]["skipped"]}
    assert skipped == {
        "fix-git__Bu66kU2/agent/leak.txt": "symlink",
        "linkdir": "symlink",
        "pipe": "special file",
    }
    raw = res["run_dir"] / "raw"
    assert not (raw / "fix-git__Bu66kU2" / "agent" / "leak.txt").exists()
    assert "host secret" not in "".join(p.read_text() for p in raw.rglob("*") if p.is_file())


def test_trial_name_in_records_cannot_escape(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    edit_json(
        job / "fix-git__Bu66kU2" / "result.json",
        lambda r: r.update(trial_name="../../etc", trial_uri="file:///etc/passwd"),
    )
    res = hi.import_job(job, tmp_path / "out")
    (t,) = trials_of(res["run_dir"])
    assert t["outcome"] == "invalid" and res["record"]["job"]["problems"]
    assert {p.name for p in (tmp_path / "out").iterdir()} == {res["run_dir"].name}


def test_credentials_are_redacted_in_the_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_PROVIDER_API_KEY", "plain-value-1234567")
    job = job_copy(tmp_path, "oracle-verifier-offline")
    trial = job / "fix-git__Bu66kU2"

    def leak(c):
        c["agent"]["kwargs"] = {"api_key": "sk-abcdefghijklmnopqrstuvwx"}
        c["agent"]["model_api_key_name"] = "MY_PROVIDER_API_KEY"

    edit_json(trial / "result.json", lambda r: leak(r["config"]))
    (trial / "agent" / "oracle.txt").write_text(
        "using plain-value-1234567\nAuthorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
        "OPENAI_API_KEY=sk-proj-abcdefghijklmnop1234\n"
    )
    res = hi.import_job(job, tmp_path / "out")
    raw = res["run_dir"] / "raw"
    stored = "".join(p.read_text() for p in raw.rglob("*") if p.is_file())
    stored += (res["run_dir"] / "trials.jsonl").read_text()
    secrets = ["plain-value-1234567", "sk-abcdefghijklmnopqrstuvwx", "abcdefghijklmnopqrstuvwxyz"]
    for secret in [*secrets, "sk-proj-abcdefghijklmnop1234"]:
        assert secret not in stored
    assert '"model_api_key_name": "MY_PROVIDER_API_KEY"' in stored  # a name, not a value
    assert res["record"]["raw"]["redactions"] >= 4
    assert hi.verify_snapshot(res["run_dir"]) == []


def test_a_job_that_changes_while_read_is_not_snapshotted(tmp_path, monkeypatch):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    real_scan = hi.scan
    calls = []

    def scan_then_write(d):
        out = real_scan(d)
        calls.append(1)
        if len(calls) == 1:
            (job / "job.log").write_text("still running\n" * 10)
        return out

    monkeypatch.setattr(hi, "scan", scan_then_write)
    with pytest.raises(hi.SourceChanged, match="job.log"):
        hi.import_job(job, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_snapshot_tampering_is_detected(tmp_path):
    res = hi.import_job(job_copy(tmp_path, "oracle-verifier-offline"), tmp_path / "out")
    raw = res["run_dir"] / "raw"
    (raw / "job.log").write_text("edited")
    (raw / "fix-git__Bu66kU2" / "verifier" / "reward.txt").unlink()
    assert sorted(hi.verify_snapshot(res["run_dir"])) == [
        "missing fix-git__Bu66kU2/verifier/reward.txt",
        "modified job.log",
    ]


def test_import_command_reports_and_refuses(tmp_path):
    job = job_copy(tmp_path, "oracle-env-error")
    out = CliRunner().invoke(app, ["harbor", "import", str(job), "-o", str(tmp_path / "o")])
    assert out.exit_code == 0, out.output
    assert "created" in out.output and "outcomes {'error': 1}" in out.output
    out = CliRunner().invoke(app, ["harbor", "import", str(tmp_path), "-o", str(tmp_path / "o")])
    assert out.exit_code == 1 and "not a Harbor job" in " ".join(out.output.split())


# --- G2: one read layer for both backends -------------------------------------------------


def native_run(tmp_path: Path) -> Path:
    import asyncio

    from conftest import make_task, question_number

    from harness.core.model import MockModel
    from harness.core.runner import run_eval
    from harness.core.types import GenerateConfig

    return asyncio.run(
        run_eval(
            MockModel(lambda m: f"The answer is {2 * question_number(m)}", name="mock-model"),
            [(make_task(), GenerateConfig())],
            output_dir=tmp_path / "runs",
        )
    )


def test_native_runs_read_unchanged_through_the_view(tmp_path):
    from harness.results import backend_of, open_run

    run = native_run(tmp_path)
    view = open_run(run)
    assert backend_of(run) == "native" and view.backend == "native"
    assert view.accuracy()["n_correct"] == 6 and view.outcomes() == {"correct": 6}
    assert view.usage()["coverage"] == "6/6"
    native_show = CliRunner().invoke(app, ["show", str(run)])
    assert native_show.exit_code == 0 and "toy" in native_show.output


def test_harbor_view_keeps_unknowns(tmp_path):
    from harness.results import open_run

    job = job_copy(tmp_path, "oracle-env-error")
    view = open_run(hi.import_job(job, tmp_path / "out")["run_dir"])
    (s,) = view.samples
    assert view.backend == "harbor" and s.outcome == "error" and s.correct is None
    assert s.error_type == "RuntimeError" and s.error_category == "unknown"
    assert s.input_tokens is None and s.usage_status == "not_applicable"
    assert view.accuracy() == {
        "n": 1, "n_scored": 0, "n_correct": 0, "accuracy": 0.0, "accuracy_of_scored": None
    }  # fmt: skip


def test_usage_totals_are_none_unless_every_model_sample_reported(tmp_path):
    from harness.results import open_run

    job = job_copy(tmp_path, "oracle-verifier-offline")

    def model_agent(r):
        r["agent_info"] = {"name": "terminus-2", "version": "2", "model_info": {"name": "m"}}

    edit_json(job / "fix-git__Bu66kU2" / "result.json", model_agent)
    usage = open_run(hi.import_job(job, tmp_path / "out")["run_dir"]).usage()
    assert usage == {"samples_with_model": 1, "reported": 0, "coverage": "0/1",
                     "input_tokens": None, "output_tokens": None, "cost_usd": None}  # fmt: skip


def test_show_report_and_diff_read_harbor_runs(tmp_path):
    a = hi.import_job(job_copy(tmp_path, "oracle-verifier-offline"), tmp_path / "o")["run_dir"]
    b = hi.import_job(job_copy(tmp_path, "oracle-env-error"), tmp_path / "o")["run_dir"]
    r = CliRunner()
    out = r.invoke(app, ["show", str(a), "--outcome", "wrong"])
    assert out.exit_code == 0 and "fix-git__Bu66kU2" in out.output
    out = r.invoke(app, ["show", str(b), "--sample", "terminal-bench/fix-git"])
    assert out.exit_code == 0 and "RuntimeError" in out.output
    out = r.invoke(app, ["report", str(b)])
    assert out.exit_code == 0
    md = (b / "report.md").read_text()
    assert "| error | 1 |" in md and "RuntimeError in phase environment_setup" in md
    out = r.invoke(app, ["diff", str(a), str(b)])
    assert out.exit_code == 0 and "wrong -> error" in out.output


def test_mixed_backend_diff_is_a_system_comparison(tmp_path):
    from harness.results import open_run
    from harness.results.analysis import diff_views

    native = open_run(native_run(tmp_path))
    harbor = open_run(
        hi.import_job(job_copy(tmp_path, "oracle-verifier-offline"), tmp_path / "o")["run_dir"]
    )
    d = diff_views(native, harbor)
    assert any("system difference, not an engine difference" in n for n in d["notes"])
    assert d["config_changes"] == [] and d["paired"]["n"] == 0
    assert len(d["unpaired"]) == 7
    out = CliRunner().invoke(app, ["diff", str(native.path), str(harbor.path)])
    assert out.exit_code == 0 and "native" in out.output and "harbor" in out.output


def test_same_name_different_task_content_is_not_paired():
    from harness.results import RunView, SampleView
    from harness.results.analysis import diff_views

    def view(fp, outcome):
        s = SampleView("t/x", "t", 0, "completed", outcome, outcome == "correct", None,
                       usage_status="not_applicable",
                       task_identity={"harness_fingerprint": fp})  # fmt: skip
        return RunView("harbor", "r", Path("."), "completed", {}, [s])

    d = diff_views(view("aaa", "correct"), view("bbb", "wrong"))
    assert d["task_identity"] == {"t/x": "different"} and d["flips"] == {}
    assert d["paired"]["n"] == 0
    d = diff_views(view("aaa", "correct"), view("aaa", "wrong"))
    assert d["flips"] == {"t/x": ("correct", "wrong")} and d["paired"]["b_worse"] == 1


def test_untrusted_text_is_shown_without_control_sequences(tmp_path):
    job = job_copy(tmp_path, "oracle-env-error")
    edit_json(
        job / "fix-git__nCvpciu" / "result.json",
        lambda r: r["exception_info"].update(exception_message="\x1b]0;pwned\x07\x1b[31mred"),
    )
    run = hi.import_job(job, tmp_path / "o")["run_dir"]
    out = CliRunner().invoke(app, ["show", str(run), "--sample", "terminal-bench/fix-git"])
    assert "\x1b" not in out.output and "\x07" not in out.output
    CliRunner().invoke(app, ["report", str(run)])
    assert "\x1b" not in (run / "report.md").read_text()


# --- G3/G4: supervised process, status, cancel, resume ------------------------------------


@pytest.fixture
def harbor_env(tmp_path, monkeypatch):
    """A config with a local task, a fake harbor and docker; returns a setter for the mode."""
    import harbor_fakes

    from harness.core.fingerprint import fingerprint_dir
    from harness.integrations.harbor import process as hp

    task = tmp_path / "proj" / "tasks" / "t1"
    task.mkdir(parents=True)
    (task / "task.toml").write_text("[agent]\ntimeout_sec = 10\n")
    exe, bin_dir = harbor_fakes.install(tmp_path, "ok")
    monkeypatch.setattr(hp, "DOCKER", str(bin_dir / "docker"))
    monkeypatch.setattr(hp, "GRACE_S", 2.0)
    monkeypatch.setattr(hp, "SETTLE_S", 0.0)
    monkeypatch.setenv("HARNESS_HARBOR_BIN", str(exe))
    (tmp_path / "proj" / "pyproject.toml").write_text("")
    cfg = tmp_path / "proj" / "h.yaml"
    raw = {
        "schema_version": 1,
        "task": {"path": "tasks/t1", "fingerprint": fingerprint_dir(task)},
        "agent": {"name": "terminus-2", "model": "m/x", "env": ["SOME_KEY"]},
        "limits": {"wall_clock_s": 30},
        "output_dir": "out",
    }
    cfg.write_text(yaml.safe_dump(raw))
    monkeypatch.setenv("SOME_KEY", "the-secret-value-123")

    def setup(mode="ok", **limits):
        (bin_dir / "mode").write_text(mode)
        if limits:
            cfg.write_text(yaml.safe_dump({**raw, "limits": {**raw["limits"], **limits}}))
        return cfg

    setup.bin = bin_dir
    setup.out = tmp_path / "proj" / "out"
    return setup


def start(cfg_path: Path, **kw):
    from harness.integrations.harbor import process as hp

    cfg, root = hc.load_config(cfg_path)
    run_dir = hp.prepare(cfg, root, cfg_path)
    receipt = json.loads((run_dir / hp.RECEIPT).read_text())
    env = {**hr.base_env(), **hp.agent_env(cfg)}
    kw.setdefault("poll_s", 0.05)
    set_limits = cfg.limits.model_fields_set
    if "stop_grace_s" in set_limits:
        kw.setdefault("grace_s", cfg.limits.stop_grace_s)
    if "cleanup_s" in set_limits:
        kw.setdefault("cleanup_s", cfg.limits.cleanup_s)
    return run_dir, hp.supervise(run_dir, receipt["argv"], env,
                                 wall_clock_s=cfg.limits.wall_clock_s, **kw)  # fmt: skip


def test_run_records_receipt_state_and_trials_without_secrets(harbor_env):
    from harness.results import open_run

    run_dir, state = start(harbor_env())
    assert state["status"] == "completed" and state["exit_code"] == 0 and state["imported"]
    receipt = json.loads((run_dir / "run.json").read_text())
    assert receipt["argv"][1:3] == ["run", "--path"] and "--print-config" not in receipt["argv"]
    assert receipt["agent_env_names"] == ["SOME_KEY"]
    assert receipt["resolved_job_config"]["agent"]["kwargs"]["api_key"] == "[REDACTED]"
    assert_no_secret_outside_local(run_dir)
    assert (harbor_env.bin / "secret_seen").read_text() == "the-secret-value-123"
    calls = [json.loads(x) for x in (harbor_env.bin / "calls.jsonl").read_text().splitlines()]
    assert all("the-secret-value-123" not in json.dumps(c["argv"]) for c in calls)
    view = open_run(run_dir)
    assert view.backend == "harbor" and view.outcomes() == {"wrong": 1}
    assert view.status == "completed"
    record = json.loads((run_dir / "harbor_run.json").read_text())
    assert record["state"]["exit_code"] == 0 and record["source"]["job_dir"].startswith("local/")
    assert importer_verify(run_dir) == []


def importer_verify(run_dir):
    return hi.verify_snapshot(run_dir)


def assert_no_secret_outside_local(run_dir, secret="the-secret-value-123"):
    """Shareable output never holds the key; the unredacted originals stay in local/ (0700)."""
    assert stat.S_IMODE((run_dir / "local").stat().st_mode) == 0o700
    shared = [p for p in run_dir.rglob("*") if p.is_file() and "local" not in p.parts]
    assert shared
    for p in shared:
        assert secret.encode() not in p.read_bytes(), p
        assert b"sk-aaaaaaaaaaaaaaaaaaaaaaaa" not in p.read_bytes(), p


def test_secrets_in_output_and_native_files_are_redacted(harbor_env):
    run_dir, state = start(harbor_env("ok"))
    assert_no_secret_outside_local(run_dir)
    log = (run_dir / "harbor.log").read_text()
    assert "using key [REDACTED]" in log and "Bearer [REDACTED]" in log
    assert state["log"]["redacted"] >= 3
    assert state["log"]["sha256"] != state["log"]["stored_sha256"]
    raw_log = (run_dir / "local" / "harbor.raw.log").read_text()
    assert "the-secret-value-123" in raw_log
    assert stat.S_IMODE((run_dir / "local" / "harbor.raw.log").stat().st_mode) == 0o600
    index = {e["path"]: e for e in json.loads((run_dir / "raw_index.json").read_text())}
    for name in ("fix-git__Bu66kU2/agent/session.log", "fix-git__Bu66kU2/exception.txt"):
        e = index[name]
        assert e["redacted"] == 1 and e["stored_sha256"] != e["sha256"], name
        native = run_dir / "local" / "job" / run_dir.name / name
        assert hashlib.sha256(native.read_bytes()).hexdigest() == e["sha256"]
        assert "[REDACTED]" in (run_dir / "raw" / name).read_text()
    assert importer_verify(run_dir) == []


def test_a_failed_start_is_redacted_too(harbor_env):
    run_dir, state = start(harbor_env("crash_before_job"))
    assert state["status"] == "failed" and state["log"]["redacted"] >= 2
    assert_no_secret_outside_local(run_dir)


def test_exit_code_zero_is_not_success_per_trial(harbor_env):
    """A zero exit code with a failed trial is a completed job with a wrong trial."""
    _, state = start(harbor_env("ok"))
    assert state["status"] == "completed"
    from harness.results import open_run

    assert open_run(_).accuracy()["n_correct"] == 0


def test_failed_start_and_partial_exit_keep_what_exists(harbor_env):
    run_dir, state = start(harbor_env("crash_before_job"))
    assert state["status"] == "failed" and state["exit_code"] == 2
    assert state["imported"] is False and "no job" in state["import_error"]
    assert "boom" in (run_dir / "harbor.log").read_text()
    run_dir, state = start(harbor_env("partial_exit1"))
    assert state["status"] == "failed" and state["exit_code"] == 1 and state["imported"]
    record = json.loads((run_dir / "harbor_run.json").read_text())
    assert record["job"]["status"] == "incomplete" and record["job"]["outcomes"] == {"wrong": 1}


def test_unsupported_settings_are_refused_before_start(harbor_env):
    cfg_path = harbor_env()
    raw = yaml.safe_load(cfg_path.read_text())
    raw["limits"]["token_limit"] = 1000
    cfg_path.write_text(yaml.safe_dump(raw))
    out = CliRunner().invoke(app, ["harbor", "run", "-c", str(cfg_path)])
    assert out.exit_code == 1 and "not started" in out.output
    assert not harbor_env.out.exists()


def test_missing_agent_key_is_refused_before_start(harbor_env, monkeypatch):
    monkeypatch.delenv("SOME_KEY")
    out = CliRunner().invoke(app, ["harbor", "run", "-c", str(harbor_env())])
    assert out.exit_code == 1 and "SOME_KEY" in out.output
    assert (
        not (harbor_env.bin / "calls.jsonl").exists()
        or "run" not in (harbor_env.bin / "calls.jsonl").read_text()
    )


def test_wall_clock_stops_the_group_and_removes_only_this_jobs_containers(harbor_env):
    run_dir, state = start(harbor_env("hang", wall_clock_s=1))
    assert state["status"] == "timed_out" and state["stop_reason"] == "timed_out"
    assert (harbor_env.bin / "got_sigterm").exists()
    cleanup = state["runs"][-1]["cleanup"]
    assert [c["project"] for c in cleanup["containers"]] == ["fix-git__bu66ku2__env"]
    assert cleanup["leftover"] == []
    assert (harbor_env.bin / "containers").read_text() == "c-unrelated other-job__xyz__env\n"
    assert (harbor_env.bin / "networks").read_text() == "n-unrelated other-job__xyz__env_default\n"
    from harness.results import open_run

    view = open_run(run_dir)
    assert view.status == "timed_out" and view.outcomes() == {"running": 1}


def test_a_process_that_ignores_sigterm_is_killed(harbor_env):
    _, state = start(harbor_env("hang_ignore_term", wall_clock_s=1))
    assert state["status"] == "timed_out" and state["exit_code"] == -9
    assert state["runs"][-1]["stop"] == "killed after grace period"


def test_cancel_from_another_process_reaches_the_supervisor(harbor_env):
    import threading

    from harness.integrations.harbor import process as hp

    cfg_path = harbor_env("hang")
    # signal handlers can only be installed from the main thread, so the canceller (another
    # harness process in real use) runs in a thread and the supervisor here
    run_dir_holder = {}

    def canceller():
        for _ in range(200):
            dirs = list(harbor_env.out.glob("*/state.json"))
            if dirs and json.loads(dirs[0].read_text()).get("status") == "running":
                run_dir_holder["dir"] = dirs[0].parent
                break
            time.sleep(0.05)
        run_dir_holder["cancel"] = hp.cancel(run_dir_holder["dir"], wait_s=20)

    t = threading.Thread(target=canceller)
    t.start()
    run_dir, state = start(cfg_path)
    t.join(30)
    assert state["status"] == "cancelled" and state["stop_reason"] == "cancelled"
    assert run_dir_holder["cancel"]["cancel"] == "stopped by supervisor"
    assert hp.status(run_dir)["status"] == "cancelled"
    assert (harbor_env.bin / "containers").read_text() == "c-unrelated other-job__xyz__env\n"


def test_resume_is_exclusive_and_does_not_double_count(harbor_env):
    from harness.integrations.harbor import process as hp
    from harness.results import open_run

    run_dir, state = start(harbor_env("hang", wall_clock_s=1))
    assert state["status"] == "timed_out"
    with hp.run_lock(run_dir):
        out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
        assert out.exit_code == 1
        assert "supervised by another harness process" in " ".join(out.output.split())
    assert [r["kind"] for r in hp.read_state(run_dir)["runs"]] == ["run"]
    out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
    assert out.exit_code == 0, out.output
    state = hp.read_state(run_dir)
    assert [r["kind"] for r in state["runs"]] == ["run", "resume"]
    assert state["runs"][1]["argv"][1:4] == ["job", "resume", "-p"]
    view = open_run(run_dir)
    assert len(view.samples) == 1 and view.status == "completed"
    out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
    assert out.exit_code == 1 and "completed" in out.output


def _spawn_orphan():
    import subprocess
    import sys

    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )


_UNSET = object()


def _fake_orphaned_run(harbor_env, proc, start=_UNSET, **ident_changes):
    """A run whose supervisor died while Harbor (``proc``) runs on."""
    from harness.integrations.harbor import process as hp

    cfg, root = hc.load_config(harbor_env())
    run_dir = hp.prepare(cfg, root, harbor_env())
    ident = hp.process_identity(proc.pid)
    ident["pgid"] = proc.pid
    if start is not _UNSET:
        ident["start"] = start
    ident.update(ident_changes)
    (run_dir / hp.PROCESS).write_text(json.dumps(ident))
    hp._update_state(run_dir, status="running")
    return run_dir


@pytest.fixture(params=["proc", "ps"])
def identity_method(request, monkeypatch):
    from harness.integrations.harbor import process as hp

    if request.param == "proc" and not Path("/proc/self/stat").is_file():
        pytest.skip("no /proc on this host")
    monkeypatch.setattr(hp, "IDENTITY_METHOD", request.param)
    return request.param


def test_identity_is_pid_start_and_boot(identity_method):
    from harness.integrations.harbor import process as hp

    proc = _spawn_orphan()
    try:
        ident = hp.process_identity(proc.pid)
        assert ident["method"] == identity_method and ident["start"]
        assert hp.process_state(ident) == "alive"
        assert hp.process_state({**ident, "start": "Thu Jan  1 00:00:00 1970"}) == "gone"
        assert hp.process_state({**ident, "boot": "another-boot"}) == "gone"
        assert hp.process_state({**ident, "host": "another-host"}) == "unknown"
        assert hp.process_state({**ident, "start": None}) == "unknown"
        assert hp.process_state(None) == "none"
    finally:
        proc.kill()
        proc.wait()
    assert hp.process_state(ident) == "gone"


def test_status_and_cancel_of_an_orphaned_harbor_process(harbor_env, identity_method):
    from harness.integrations.harbor import process as hp

    proc = _spawn_orphan()
    try:
        run_dir = _fake_orphaned_run(harbor_env, proc)
        st = hp.status(run_dir)
        assert st["status"] == "orphaned" and st["harbor_process"] == "alive"
        out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
        assert out.exit_code == 1 and proc.poll() is None  # never a second Harbor on the job
        res = hp.cancel(run_dir, wait_s=1, grace_s=2)
        assert res["status"] == "cancelled" and res["cancel"] in (
            "terminated",
            "killed after grace period",
        )
        assert proc.wait(timeout=10) is not None
        assert hp.status(run_dir)["status"] == "cancelled"
    finally:
        proc.kill()


def test_a_reused_pid_is_never_signalled(harbor_env, identity_method):
    from harness.integrations.harbor import process as hp

    proc = _spawn_orphan()
    try:
        run_dir = _fake_orphaned_run(harbor_env, proc, start="Thu Jan  1 00:00:00 1970")
        st = hp.status(run_dir)
        assert st["status"] == "interrupted" and st["harbor_process"] == "gone"
        res = hp.cancel(run_dir, wait_s=1)
        assert res["cancel"] == "harbor process already gone" and res["status"] == "cancelled"
        assert proc.poll() is None  # still running: not ours to stop
    finally:
        proc.kill()


def test_an_unverifiable_process_blocks_cancel_and_resume(harbor_env, identity_method):
    from harness.integrations.harbor import process as hp

    proc = _spawn_orphan()
    try:
        run_dir = _fake_orphaned_run(harbor_env, proc, start=None, note="ps failed")
        st = hp.status(run_dir)
        assert st["status"] == "unknown" and "refused" in st["note"]
        out = CliRunner().invoke(app, ["harbor", "cancel", str(run_dir), "--wait", "0"])
        assert out.exit_code == 1 and "refused" in out.output
        assert proc.poll() is None and hp.read_state(run_dir)["status"] == "running"
        assert hp.status(run_dir)["cancel_pending"]
        out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
        assert out.exit_code == 1 and "unknown" in out.output
        calls = (harbor_env.bin / "calls.jsonl").read_text()
        assert '"resume"' not in calls
    finally:
        proc.kill()


def test_a_dead_supervisor_without_harbor_is_resumed_once(harbor_env):
    from harness.integrations.harbor import process as hp

    proc = _spawn_orphan()
    run_dir = _fake_orphaned_run(harbor_env, proc)
    shutil.copytree(harbor_env.bin / "fixture", run_dir / "local" / "job" / run_dir.name)
    proc.kill()
    proc.wait()
    assert hp.status(run_dir)["status"] == "interrupted"
    out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
    assert out.exit_code == 0, out.output
    assert [r["kind"] for r in hp.read_state(run_dir)["runs"]] == ["resume"]
    out = CliRunner().invoke(app, ["harbor", "resume", str(run_dir)])
    assert out.exit_code == 1 and "completed" in out.output


# --- cancel before and around the start (H2) ----------------------------------------------


def _prepared(harbor_env, mode="hang"):
    from harness.integrations.harbor import process as hp

    cfg_path = harbor_env(mode)
    cfg, root = hc.load_config(cfg_path)
    run_dir = hp.prepare(cfg, root, cfg_path)
    receipt = json.loads((run_dir / hp.RECEIPT).read_text())
    return run_dir, receipt, {**hr.base_env(), **hp.agent_env(cfg)}


def _harbor_runs(harbor_env):
    calls = [json.loads(c) for c in (harbor_env.bin / "calls.jsonl").read_text().splitlines()]
    return [c for c in calls if c["argv"][0] == "run" and "--print-config" not in c["argv"]
            or c["argv"][:2] == ["job", "resume"]]  # fmt: skip


def test_cancel_after_prepare_prevents_the_start(harbor_env):
    from harness.integrations.harbor import process as hp

    run_dir, receipt, env = _prepared(harbor_env)
    res = hp.cancel(run_dir, wait_s=0)
    assert res["status"] == "cancelled" and res["cancel"] == "harbor was never started"
    state = hp.supervise(run_dir, receipt["argv"], env, wall_clock_s=30, poll_s=0.05)
    assert state["status"] == "cancelled" and state["note"] == "cancelled before Harbor started"
    assert _harbor_runs(harbor_env) == []
    assert not (run_dir / hp.PROCESS).exists()


def test_cancel_while_another_process_holds_the_lock(harbor_env):
    from harness.integrations.harbor import process as hp

    run_dir, receipt, env = _prepared(harbor_env)
    with hp.run_lock(run_dir):  # another harness process holds the run
        hp.request_cancel(run_dir)
        with pytest.raises(hp.RunLocked):
            hp.supervise(run_dir, receipt["argv"], env, wall_clock_s=30)
    state = hp.supervise(run_dir, receipt["argv"], env, wall_clock_s=30, poll_s=0.05)
    assert state["status"] == "cancelled" and _harbor_runs(harbor_env) == []


def test_cancel_during_prepare_keeps_the_run_cancelled(harbor_env, monkeypatch):
    from harness.integrations.harbor import process as hp

    real_run = hp.subprocess.run

    def run_and_cancel(argv, **kw):
        if "--print-config" in argv:
            hp.request_cancel(Path(kw["cwd"]))
        return real_run(argv, **kw)

    monkeypatch.setattr(hp.subprocess, "run", run_and_cancel)
    cfg_path = harbor_env("hang")
    cfg, root = hc.load_config(cfg_path)
    run_dir = hp.prepare(cfg, root, cfg_path)
    assert hp.read_state(run_dir)["status"] == "cancelled"
    monkeypatch.setattr(hp.subprocess, "run", real_run)
    receipt = json.loads((run_dir / hp.RECEIPT).read_text())
    env = {**hr.base_env(), **hp.agent_env(cfg)}
    state = hp.supervise(run_dir, receipt["argv"], env, wall_clock_s=30)
    assert state["status"] == "cancelled" and _harbor_runs(harbor_env) == []


def test_resume_supersedes_an_older_cancel_but_not_a_newer_one(harbor_env):
    from harness.integrations.harbor import process as hp

    run_dir, state = start(harbor_env("hang", wall_clock_s=1))
    assert state["status"] == "timed_out"
    old = hp.request_cancel(run_dir)
    argv, _ = hp.resume_argv(run_dir)
    receipt = json.loads((run_dir / hp.RECEIPT).read_text())
    env = {**hr.base_env(), "SOME_KEY": "the-secret-value-123"}
    # a cancel issued after the resume was asked for wins
    requested = old - 1
    state = hp.supervise(run_dir, argv, env, wall_clock_s=30, kind="resume",
                         requested_ns=requested, poll_s=0.05)  # fmt: skip
    assert state["status"] == "cancelled" and [r["kind"] for r in state["runs"]] == ["run"]
    # an explicit resume after that cancel supersedes it and runs once
    requested = time.time_ns()
    state = hp.supervise(run_dir, argv, env, wall_clock_s=30, kind="resume",
                         requested_ns=requested, poll_s=0.05)  # fmt: skip
    assert state["status"] == "completed" and state["superseded_cancel_ns"] == old
    assert [r["kind"] for r in state["runs"]] == ["run", "resume"]
    assert not (run_dir / hp.CANCEL).exists()
    assert receipt["job_dir"].startswith("local/")


# --- cleanup budget (H5) ------------------------------------------------------------------


def test_a_hanging_docker_daemon_cannot_stall_cleanup(harbor_env):
    (harbor_env.bin / "docker_mode").write_text("hang")
    t0 = time.monotonic()
    run_dir, state = start(harbor_env("hang", wall_clock_s=1, cleanup_s=1.5, stop_grace_s=1))
    assert time.monotonic() - t0 < 20
    run = state["runs"][-1]
    cleanup = run["cleanup"]
    assert cleanup["leftover"] is None and cleanup["confirmed"] is False
    assert cleanup["errors"] and cleanup["budget_s"] == 1.5 and cleanup["elapsed_s"] < 5
    assert set(run["timing_s"]) == {"harbor", "stop", "cleanup"}
    assert state["status"] == "timed_out"


def test_failed_removals_are_reported_not_hidden(harbor_env):
    (harbor_env.bin / "docker_mode").write_text("rm_fails")
    _, state = start(harbor_env("hang", wall_clock_s=1, stop_grace_s=1))
    cleanup = state["runs"][-1]["cleanup"]
    assert cleanup["containers"] == [] and cleanup["confirmed"] is False
    assert cleanup["leftover"] == ["c-fix-git__bu66ku2"]
    assert any("permission denied" in e for e in cleanup["errors"])
    assert any("permission denied" in e for e in cleanup["late"]["errors"])


def test_containers_created_late_are_removed(harbor_env, monkeypatch):
    from harness.integrations.harbor import process as hp

    real = hp.cleanup_owned
    calls = []

    def late_container(job_dir, deadline):
        calls.append(1)
        if len(calls) == 2:  # appears while the first removal ran
            with (harbor_env.bin / "containers").open("a") as f:
                f.write("c-late fix-git__bu66ku2__verifier__1\n")
        return real(job_dir, deadline)

    monkeypatch.setattr(hp, "cleanup_owned", late_container)
    _, state = start(harbor_env("hang", wall_clock_s=1, stop_grace_s=1))
    cleanup = state["runs"][-1]["cleanup"]
    assert [c["id"] for c in cleanup["late"]["containers"]] == ["c-late"]
    assert cleanup["leftover"] == [] and cleanup["confirmed"] is True


def test_status_command_shows_state(harbor_env):
    run_dir, _ = start(harbor_env())
    out = CliRunner().invoke(app, ["harbor", "status", str(run_dir)])
    assert out.exit_code == 0 and '"status": "completed"' in out.output


def test_a_config_harbor_rejects_never_starts_a_job(harbor_env):
    from harness.integrations.harbor import process as hp

    out = CliRunner().invoke(app, ["harbor", "run", "-c", str(harbor_env("bad_config"))])
    assert out.exit_code == 1 and "print-config failed" in " ".join(out.output.split())
    (run_dir,) = harbor_env.out.iterdir()
    assert hp.read_state(run_dir)["status"] == "start_failed"
    assert not (run_dir / "local" / "job").exists()
    calls = (harbor_env.bin / "calls.jsonl").read_text().splitlines()
    assert all("--print-config" in json.loads(c)["argv"] or json.loads(c)["argv"] == ["--version"]
               for c in calls)  # fmt: skip


# --- review 44: H1a process-group probe, H3a outdated archives, H4a non-text redaction ----


def _sigterm_unreaped():
    """An orphan-like child that got SIGTERM and is now a zombie nobody has reaped yet."""
    import signal

    proc = _spawn_orphan()
    os.kill(proc.pid, signal.SIGTERM)
    for _ in range(100):
        if Path(f"/proc/{proc.pid}/stat").is_file():
            if Path(f"/proc/{proc.pid}/stat").read_text().split(")")[1].split()[0] == "Z":
                break
        time.sleep(0.05)
    return proc


def test_group_state_uses_the_member_list_not_only_the_signal_probe(monkeypatch):
    from harness.integrations.harbor import process as hp

    live = _spawn_orphan()
    zombie = _sigterm_unreaped()
    try:
        assert hp.group_state(live.pid) == "alive"
        assert hp.group_state(zombie.pid) == "gone"  # Linux accepts signals for zombies
        real = os.killpg

        def macos_probe(pgid, sig):
            real(pgid, sig)  # ProcessLookupError when the group is really gone
            if sig == 0:
                raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "killpg", macos_probe)
        assert hp.group_state(live.pid) == "alive"
        assert hp.group_state(zombie.pid) == "gone"
        monkeypatch.setattr(hp, "group_members", lambda pgid: None)
        assert hp.group_state(live.pid) == "unknown"  # EPERM and no member list: not "gone"
    finally:
        for p in (live, zombie):
            p.kill()
            p.wait()
    monkeypatch.undo()
    assert hp.group_state(live.pid) == "gone"


def test_orphan_cancel_survives_eperm_from_an_unreaped_group(harbor_env, identity_method,
                                                             monkeypatch):  # fmt: skip
    """macOS: after SIGTERM, probing a group whose leader is an unreaped zombie gives EPERM."""
    from harness.integrations.harbor import process as hp

    real = os.killpg

    def macos_probe(pgid, sig):
        real(pgid, sig)
        if sig == 0:
            raise PermissionError(1, "Operation not permitted")

    proc = _spawn_orphan()
    try:
        run_dir = _fake_orphaned_run(harbor_env, proc)
        monkeypatch.setattr(os, "killpg", macos_probe)
        res = hp.cancel(run_dir, wait_s=0, grace_s=5)
        assert res["status"] == "cancelled" and res["cancel"] == "terminated"
        assert "cleanup" in hp.read_state(run_dir)["cancel"]
        assert proc.wait(timeout=10) is not None
    finally:
        proc.kill()


def test_an_unsignallable_group_is_not_reported_cancelled(harbor_env, monkeypatch):
    from harness.integrations.harbor import process as hp

    def denied(pgid, sig):
        raise PermissionError(1, "Operation not permitted")

    proc = _spawn_orphan()
    try:
        run_dir = _fake_orphaned_run(harbor_env, proc)
        monkeypatch.setattr(hp, "KILL_WAIT_S", 0.3)
        monkeypatch.setattr(os, "killpg", denied)
        res = hp.cancel(run_dir, wait_s=0, grace_s=0.3)
        assert res["cancel"].startswith("failed: Harbor's process group is alive")
        state = hp.read_state(run_dir)
        assert state["status"] == "running" and state["cancel"]["group"] == "alive"
        assert "permission denied" in state["cancel"]["how"]
        assert proc.poll() is None and (run_dir / hp.CANCEL).exists()
        assert not (harbor_env.bin / "docker_calls.jsonl").exists()  # containers left to Harbor
        monkeypatch.undo()
        assert hp.status(run_dir)["status"] == "orphaned"
    finally:
        proc.kill()


def _old_archive(tmp_path):
    """An import made by importer version 1: the planned trial without a directory was left
    out, so 1 of 1 looked correct."""
    job = job_copy(tmp_path, "oracle-verifier-offline")
    (job / "result.json").unlink()
    edit_json(job / "fix-git__Bu66kU2" / "result.json",
              lambda r: r["verifier_result"].update(rewards={"reward": 1.0}))  # fmt: skip
    edit_json(job / "lock.json", lambda lock: lock["trials"].append(lock["trials"][0]))
    run = hi.import_job(job, tmp_path / "out")["run_dir"]
    kept = [t for t in trials_of(run) if t["outcome"] != "missing"]
    (run / "trials.jsonl").write_text("".join(json.dumps(t) + "\n" for t in kept))

    def as_v1(r):
        r["importer"]["version"] = 1
        del r["raw"]["redaction"]  # version 1 did not record how raw/ was redacted
        r["job"].update(n_total_trials=1, n_missing=0, outcomes={"correct": 1})

    edit_json(run / "harbor_run.json", as_v1)
    return job, run


def test_an_outdated_archive_is_flagged_and_not_reported_unchanged(tmp_path):
    from harness.results import open_run

    job, run = _old_archive(tmp_path)
    view = open_run(run)
    assert view.accuracy()["accuracy"] == 1.0
    assert any("importer version 1" in w for w in view.warnings())
    out = CliRunner().invoke(app, ["show", str(run)])
    assert "WARNING records built by importer version 1" in " ".join(out.output.split())
    with pytest.raises(hi.ImportConflict, match="importer version 1"):
        hi.import_job(job, tmp_path / "out")
    res = hi.import_job(job, tmp_path / "out", replace=True)
    assert res["action"] == "replaced"
    view = open_run(run)
    assert view.accuracy()["n"] == 2 and view.accuracy()["accuracy"] == 0.5
    assert not any("importer version" in w for w in view.warnings())
    assert hi.import_job(job, tmp_path / "out")["action"] == "unchanged"


def test_an_outdated_archive_is_rebuilt_from_its_snapshot(tmp_path):
    from harness.results import open_run

    job, run = _old_archive(tmp_path)
    shutil.rmtree(job)  # the Harbor job directory is gone; the snapshot is all there is
    index_before = (run / "raw_index.json").read_text()
    out = CliRunner().invoke(app, ["harbor", "rebuild", str(run)])
    assert out.exit_code == 0, out.output
    assert "importer 1 -> 2" in out.output and "1/2 trials" in out.output
    view = open_run(run)
    assert view.accuracy()["accuracy"] == 0.5 and view.outcomes() == {"correct": 1, "missing": 1}
    assert [w for w in view.warnings() if "importer" in w] == []
    record = json.loads((run / "harbor_run.json").read_text())
    assert record["rebuilt"]["previous"] == {"importer": record["rebuilt"]["previous"]["importer"],
                                             "redaction": 1}  # fmt: skip
    assert record["rebuilt"]["previous"]["importer"]["version"] == 1
    # nothing to redact again in this snapshot: index and files unchanged
    assert (run / "raw_index.json").read_text() == index_before and hi.verify_snapshot(run) == []


def test_a_tampered_snapshot_is_not_rebuilt(tmp_path):
    _, run = _old_archive(tmp_path)
    (run / "raw" / "lock.json").write_text("{}")
    out = CliRunner().invoke(app, ["harbor", "rebuild", str(run)])
    assert out.exit_code == 1 and "modified lock.json" in out.output


MARKER = "synthetic-marker-7f3a91"


@pytest.mark.parametrize(
    "data",
    [
        b"\xff diagnostic " + MARKER.encode() + b"\n",
        MARKER.encode() + b" \xfe\xff trailing",
        b"\x00\x01binary" + MARKER.encode() + b"\x00",
        b"\x00" * 10 + MARKER.encode("utf-16-le") + b"\x00",
        b"\xffsk-" + b"b" * 24 + b"\xff" + b" API_TOKEN=" + b"z" * 12,
    ],
    ids=["invalid-utf8-middle", "invalid-utf8-start", "nul", "utf16", "patterns"],
)
def test_non_text_bytes_are_redacted_too(data):
    stored, n = hi.redact_bytes("agent/blob.bin", data, [MARKER])
    assert n >= 1
    assert MARKER.encode() not in stored and MARKER.encode("utf-16-le") not in stored
    assert b"sk-bbbb" not in stored and b"zzzzzzzz" not in stored


def test_redacted_bytes_keep_everything_else():
    data = b"\x00\xff head " + MARKER.encode() + b" tail \xfe"
    stored, n = hi.redact_bytes("x.bin", data, [MARKER])
    assert n == 1 and stored == b"\x00\xff head [REDACTED] tail \xfe"
    assert hi.redact_bytes("x.bin", b"\x00\xff nothing here", [MARKER]) == (
        b"\x00\xff nothing here",
        0,
    )


def test_a_non_utf8_log_is_published_redacted(tmp_path):
    from harness.integrations.harbor import process as hp

    run_dir = tmp_path / "run"
    (run_dir / "local").mkdir(parents=True)
    (run_dir / hp.RAW_LOG).write_bytes(b"\xff diagnostic " + MARKER.encode() + b"\n")
    info = hp._publish_log(run_dir, [MARKER])
    assert MARKER.encode() not in (run_dir / "harbor.log").read_bytes()
    assert info["redacted"] == 1 and info["sha256"] != info["stored_sha256"]


def test_binary_native_files_are_redacted_in_the_shared_snapshot(tmp_path):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    blob = job / "fix-git__Bu66kU2" / "agent" / "dump.bin"
    blob.write_bytes(b"\x00\xff" + MARKER.encode() + b"\x00")
    run = hi.import_job(job, tmp_path / "out", secrets=[MARKER])["run_dir"]
    stored = run / "raw" / "fix-git__Bu66kU2" / "agent" / "dump.bin"
    assert MARKER.encode() not in stored.read_bytes()
    entry = next(e for e in json.loads((run / "raw_index.json").read_text())
                 if e["path"].endswith("dump.bin"))  # fmt: skip
    assert (
        entry["redacted"] == 1 and entry["sha256"] == hashlib.sha256(blob.read_bytes()).hexdigest()
    )
    assert hi.verify_snapshot(run) == []


# --- review 46: H4b an outdated snapshot is redacted again, not just relabelled -----------

LEAK = "synthetic-leak-5b2e8c1d"


def _old_archive_with_leak(tmp_path, monkeypatch):
    """What importer version 1 produced: a binary file with a credential stored unredacted."""
    job = job_copy(tmp_path, "oracle-verifier-offline")
    blob = job / "fix-git__Bu66kU2" / "agent" / "dump.bin"
    blob.write_bytes(b"\x00\xff core " + LEAK.encode() + b" \xfe\x00")
    original_sha = hashlib.sha256(blob.read_bytes()).hexdigest()
    with monkeypatch.context() as m:
        m.setattr(hi, "_redact_raw_bytes", lambda data, secrets: (data, 0))  # version 1 rules
        run = hi.import_job(job, tmp_path / "out", secrets=[LEAK])["run_dir"]

    def as_v1(r):
        r["importer"]["version"] = 1
        del r["raw"]["redaction"]

    edit_json(run / "harbor_run.json", as_v1)
    shutil.rmtree(job)  # only the archive is left
    assert LEAK.encode() in (run / "raw" / "fix-git__Bu66kU2" / "agent" / "dump.bin").read_bytes()
    return run, original_sha


def _shared_bytes(run):
    return b"".join(
        p.read_bytes() for p in run.rglob("*") if p.is_file() and "local" not in p.parts
    )


def test_an_unredacted_old_snapshot_is_flagged_as_not_shareable(tmp_path, monkeypatch):
    from harness.results import open_run

    run, _ = _old_archive_with_leak(tmp_path, monkeypatch)
    warnings = open_run(run).warnings()
    assert any("do not share" in w and "redaction version 1" in w for w in warnings)
    out = CliRunner().invoke(app, ["show", str(run)])
    assert "do not share it" in " ".join(out.output.split())


def test_rebuild_redacts_an_old_snapshot_again(tmp_path, monkeypatch):
    from harness.results import open_run

    run, original_sha = _old_archive_with_leak(tmp_path, monkeypatch)
    monkeypatch.setenv("SYNTH_LEAK", LEAK)
    out = CliRunner().invoke(app, ["harbor", "rebuild", str(run), "--secret-env", "SYNTH_LEAK"])
    assert out.exit_code == 0, out.output
    assert "redaction 1 -> 2" in out.output and "redacted again: 1 value(s)" in out.output
    assert LEAK.encode() not in _shared_bytes(run)
    entry = next(e for e in json.loads((run / "raw_index.json").read_text())
                 if e["path"].endswith("dump.bin"))  # fmt: skip
    stored = (run / "raw" / "fix-git__Bu66kU2" / "agent" / "dump.bin").read_bytes()
    assert entry["sha256"] == original_sha and entry["redacted"] == 1
    assert entry["stored_sha256"] == hashlib.sha256(stored).hexdigest()
    assert stored == b"\x00\xff core [REDACTED] \xfe\x00"
    assert hi.verify_snapshot(run) == []
    record = json.loads((run / "harbor_run.json").read_text())
    assert record["raw"]["redaction"]["migrated_from"] == 1
    assert (
        record["raw"]["index_sha256"]
        == hashlib.sha256((run / "raw_index.json").read_bytes()).hexdigest()
    )
    view = open_run(run)
    assert view.accuracy()["n"] == 1 and view.outcomes() == {"wrong": 1}
    warnings = view.warnings()
    assert not any("do not share" in w for w in warnings)
    assert any("review raw/ before sharing" in w for w in warnings)  # stays: migration is partial
    assert not list(run.glob(".raw-*"))


def test_a_secret_unknown_at_migration_stays_flagged(tmp_path, monkeypatch):
    from harness.results import open_run

    run, _ = _old_archive_with_leak(tmp_path, monkeypatch)
    res = hi.rebuild(run)  # the value is not known now: it cannot be found
    assert res["record"]["raw"]["redaction"]["redacted_at_migration"] == 0
    assert any("review raw/ before sharing" in w for w in open_run(run).warnings())


def test_an_old_snapshot_is_not_reimported_as_unchanged(tmp_path, monkeypatch):
    job = job_copy(tmp_path, "oracle-verifier-offline")
    (job / "fix-git__Bu66kU2" / "agent" / "dump.bin").write_bytes(b"\x00" + LEAK.encode())
    with monkeypatch.context() as m:
        m.setattr(hi, "_redact_raw_bytes", lambda data, secrets: (data, 0))
        run = hi.import_job(job, tmp_path / "out", secrets=[LEAK])["run_dir"]
    edit_json(run / "harbor_run.json", lambda r: r["raw"].update(redaction={"version": 1}))
    with pytest.raises(hi.ImportConflict, match="redaction version 1"):
        hi.import_job(job, tmp_path / "out", secrets=[LEAK])
    res = hi.import_job(job, tmp_path / "out", secrets=[LEAK], replace=True)
    assert res["action"] == "replaced" and LEAK.encode() not in _shared_bytes(run)


def test_rebuild_of_a_harness_run_uses_the_private_originals(harbor_env):
    from harness.integrations.harbor import process as hp
    from harness.results import open_run

    run_dir, _ = start(harbor_env("ok"))
    blob = run_dir / "raw" / "fix-git__Bu66kU2" / "agent" / "session.log"
    blob.write_text("export SOME_KEY=the-secret-value-123\n")  # as an old snapshot had it

    def as_v1(r):
        r["importer"]["version"] = 1
        del r["raw"]["redaction"]

    edit_json(run_dir / "harbor_run.json", as_v1)
    res = hi.rebuild(run_dir)
    record = res["record"]
    assert record["rebuilt"]["from"] == "original job"
    assert record["raw"]["redaction"] == {"version": 2, "from": "original files"}
    assert record["state"]["status"] == "completed" and record["run"]["argv"]
    assert record["source"]["job_dir"].startswith("local/")
    assert_no_secret_outside_local(run_dir)
    assert hi.verify_snapshot(run_dir) == [] and open_run(run_dir).warnings() == []
    assert hp.read_state(run_dir)["status"] == "completed"
