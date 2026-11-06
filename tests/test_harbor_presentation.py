import json

import pytest

from harness.integrations.harbor import importer
from harness.integrations.harbor.presentation import (
    export_report,
    progress_printer,
    progress_snapshot,
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def test_cursor_progress_partial_stream_and_finished_trial(tmp_path):
    trial = tmp_path / "t"
    write(trial / "config.json", {})
    stream = trial / "agent/cursor-cli.txt"
    write(
        stream,
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"shellToolCall": {"args": {"secret": "not displayed"}}},
        },
    )
    with stream.open("a") as f:
        f.write('\n{"type":')
    snapshot = progress_snapshot(tmp_path)
    assert snapshot["stages"] == {"Agent 作答": 1}
    assert "shellToolCall" in snapshot["trials"][0]["activity"]
    assert "not displayed" not in str(snapshot)
    write(trial / "result.json", {"exception_info": {"exception_type": "Timeout"}})
    assert progress_snapshot(tmp_path)["trials"][0]["activity"] == "异常结束"


def test_progress_heartbeat_and_cleanup(tmp_path):
    write(tmp_path / "run.json", {"job_dir": "local/job"})
    messages = []
    tick = progress_printer(tmp_path, messages.append)
    tick({"elapsed_s": 0})
    tick({"elapsed_s": 1})
    tick({"elapsed_s": 10})
    tick({"elapsed_s": 11, "phase": "清理容器"})
    assert len(messages) == 3
    assert "10s" in messages[1]
    assert "清理容器" in messages[2]


def report_fixture(tmp_path):
    write(
        tmp_path / "harbor_run.json",
        {
            "job": {"status": "interrupted"},
            "importer": {"version": importer.IMPORTER_VERSION},
            "raw": {"redaction": {"version": importer.REDACTION_VERSION}},
        },
    )
    trial = {
        "trial_name": "t",
        "outcome": "env_error",
        "usage": None,
        "trajectory": "agent/trajectory.json",
        "files": ["trial.log"],
    }
    (tmp_path / "trials.jsonl").write_text(json.dumps(trial) + "\n")
    write(
        tmp_path / "raw/t/agent/trajectory.json",
        {
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "message": '<script>alert("x")</script>',
                    "tool_calls": [
                        {"function_name": "shell", "arguments": {"command": "git status"}}
                    ],
                    "observation": {"results": [{"content": "clean"}]},
                }
            ]
        },
    )
    (tmp_path / "raw/t/trial.log").write_text("token-private-12345678")
    return tmp_path


def test_report_is_offline_escaped_redacted_and_keeps_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "token-private-12345678")
    root = report_fixture(tmp_path)
    # Originals must never be used by the exporter.
    write(root / "local/secret.json", {"secret": "LOCAL-MUST-NOT-APPEAR"})
    page = export_report(root).read_text()
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "token-private-12345678" not in page
    assert "LOCAL-MUST-NOT-APPEAR" not in page
    assert "git status" in page and "clean" in page
    assert "null" in page and "env_error" in page
    assert "default-src 'none'" in page


def test_report_missing_trajectory_preserves_logs(tmp_path):
    report_fixture(tmp_path)
    (tmp_path / "raw/t/agent/trajectory.json").unlink()
    assert "轨迹缺失" in export_report(tmp_path).read_text()


def test_report_rejects_external_symlink(tmp_path):
    report_fixture(tmp_path)
    log = tmp_path / "raw/t/trial.log"
    log.unlink()
    log.symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError):
        export_report(tmp_path)
