"""Launch the prepared local Cursor smoke test without writing credentials to disk."""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list-models", action="store_true", help="Authenticate and list models")
    action.add_argument("--run", action="store_true", help="Run one paid/subscription task")
    parser.add_argument("--model", help="Exact Cursor model ID; required with --run")
    args = parser.parse_args()
    if args.run and not args.model:
        parser.error("--run requires --model; use --list-models first")

    root = Path(__file__).resolve().parents[1]
    config_path = root / "configs/harbor/fix-git-cursor-local.yaml"
    config = yaml.safe_load(config_path.read_text())
    if args.run:
        for required in (config["task"]["path"], config["harbor"]["executable"]):
            if not (root / required).exists():
                parser.error(f"Prepared local asset missing: {required}")
    agent = shutil.which("cursor-agent")
    if args.list_models and not agent:
        parser.error("cursor-agent is not installed on the host")

    env = os.environ.copy()
    if not env.get("CURSOR_API_KEY"):
        if not sys.stdin.isatty():
            parser.error("Run in your local interactive terminal to enter the Cursor key")
        # Refuse getpass's echoing fallback if no secure terminal is available.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass("Cursor API Key (hidden, not saved): ").strip()
            except getpass.GetPassWarning:
                parser.error("No terminal available for hidden credential input")
        if not key:
            parser.error("Empty Cursor API Key")
        env["CURSOR_API_KEY"] = key

    if args.list_models:
        return subprocess.run(
            [agent, "--list-models"], cwd=root, env=env, timeout=90, check=False
        ).returncode

    config["agent"]["model"] = args.model
    config["harbor"]["executable"] = str(root / config["harbor"]["executable"])
    config_dir = root / "runs/cursor-smoke-20261106/configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", prefix="cursor-", dir=config_dir, delete=False
    ) as stream:
        yaml.safe_dump(config, stream, sort_keys=False)
        run_config = stream.name
    print("Running one local Docker task; 900s wall-clock limit; internal retries disabled.")
    print("Model:", args.model)
    print("Config (no credentials):", run_config)
    return subprocess.run(
        [str(root / ".venv/bin/harness"), "harbor", "run", "-c", run_config],
        cwd=root,
        env=env,
        check=False,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
