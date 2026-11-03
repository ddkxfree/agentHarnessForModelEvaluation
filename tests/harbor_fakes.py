"""Stand-ins for the Harbor 0.25.0 CLI and the docker CLI, for lifecycle tests."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "harbor" / "0.25.0"

FAKE_HARBOR = r"""
import json, os, shutil, signal, sys, time
from pathlib import Path

here = Path(__file__).parent
mode = (here / "mode").read_text().strip()
args = sys.argv[1:]
calls = here / "calls.jsonl"
with calls.open("a") as f:
    f.write(json.dumps({"argv": args, "env": sorted(os.environ)}) + "\n")

def opt(name):
    return args[args.index(name) + 1]

def add_container(trial):
    with (here / "containers").open("a") as f:
        f.write(f"c-{trial.lower()} {trial.lower()}__env\n")

if args == ["--version"]:
    print("0.25.0")
    sys.exit(0)
if args[0] == "run":
    if "--print-config" in args:
        if mode == "bad_config":
            print("unknown agent", file=sys.stderr)
            sys.exit(2)
        print(json.dumps({"job_name": opt("--job-name"),
                          "agent": {"kwargs": {"api_key": os.environ.get("SOME_KEY", "")}}}))
        sys.exit(0)
    job = Path(opt("--jobs-dir")) / opt("--job-name")
    if mode == "crash_before_job":
        print("boom")
        sys.exit(2)
    shutil.copytree(here / "fixture", job)
    (here / "secret_seen").write_text(os.environ.get("SOME_KEY", "<unset>"))
    if mode == "ok":
        sys.exit(0)
    (job / "result.json").unlink()
    if mode == "partial_exit1":
        sys.exit(1)
    add_container("fix-git__Bu66kU2")
    (job / "fix-git__Bu66kU2" / "result.json").unlink()
    if mode == "hang_ignore_term":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    else:
        def stop(*_):
            (here / "got_sigterm").write_text("1")
            sys.exit(130)
        signal.signal(signal.SIGTERM, stop)
    while True:
        time.sleep(0.1)
if args[:2] == ["job", "resume"]:
    job = Path(args[3])
    for p in (here / "fixture").rglob("*"):
        dest = job / p.relative_to(here / "fixture")
        if p.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(p, dest)
    sys.exit(0)
sys.exit(64)
"""

FAKE_DOCKER = r"""
import json, sys
from pathlib import Path

here = Path(__file__).parent
args = sys.argv[1:]
with (here / "docker_calls.jsonl").open("a") as f:
    f.write(json.dumps(args) + "\n")
store = here / ("networks" if args[0] == "network" else "containers")
lines = store.read_text().splitlines() if store.exists() else []
if args[:2] == ["ps", "-a"] or args[:2] == ["network", "ls"]:
    print("\n".join(lines))
elif args[:2] == ["rm", "-f"] or args[:2] == ["network", "rm"]:
    store.write_text("".join(l + "\n" for l in lines if l.split()[0] != args[2]))
else:
    sys.exit(1)
"""


def install(tmp_path: Path, mode: str = "ok") -> tuple[Path, Path]:
    """A fake harbor and docker in tmp_path/bin; returns (harbor, bin dir)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("harbor", FAKE_HARBOR), ("docker", FAKE_DOCKER)):
        exe = bin_dir / name
        exe.write_text(f"#!{sys.executable}\n{body}")
        exe.chmod(0o755)
    (bin_dir / "mode").write_text(mode)
    if not (bin_dir / "fixture").exists():
        shutil.copytree(FIXTURES / "oracle-verifier-offline", bin_dir / "fixture")
    (bin_dir / "containers").write_text("c-unrelated other-job__xyz__env\n")
    (bin_dir / "networks").write_text("n-unrelated other-job__xyz__env_default\n")
    return bin_dir / "harbor", bin_dir
