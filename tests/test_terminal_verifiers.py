"""Runs the built-in terminal tasks' verifier scripts locally, against good and bad solutions."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

DATA = Path(__file__).parents[1] / "src/harness/tasks/terminal_data"

CORRECT_STATS = """
def mean(values):
    return sum(values) / len(values)


def median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2
"""


def run_verifier(task: str, app: Path) -> subprocess.CompletedProcess[str]:
    script = (DATA / task / "tests/test.sh").read_text()
    assert "cd /app\n" in script
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", script.replace("cd /app\n", f"cd {app}\n")],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def write_stats(tmp_path: Path, source: str) -> Path:
    (tmp_path / "stats.py").write_text(source)
    return tmp_path


def test_fix_mean_accepts_the_reference_solution(tmp_path):
    proc = run_verifier("fix-mean", write_stats(tmp_path, CORRECT_STATS))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_fix_mean_rejects_the_original_bug(tmp_path):
    original = (DATA / "fix-mean/environment/stats.py").read_text()
    proc = run_verifier("fix-mean", write_stats(tmp_path, original))
    assert proc.returncode != 0


@pytest.mark.parametrize(
    "value",
    ['float("nan")', 'float("inf")', 'float("-inf")', '"2"', "True", "None", "[2]"],
)
def test_fix_mean_rejects_invalid_return_values(tmp_path, value):
    source = f"def mean(values):\n    return {value}\n\n\ndef median(values):\n    return {value}\n"
    proc = run_verifier("fix-mean", write_stats(tmp_path, source))
    assert proc.returncode != 0, proc.stdout
    assert "FAIL" in proc.stdout
