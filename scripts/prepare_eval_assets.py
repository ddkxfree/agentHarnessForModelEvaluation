"""Bounded, resumable asset preparation. No model calls and no credential reads."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORE = ROOT / "runs/offline-assets-20261105"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--images", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=STORE)
    args = parser.parse_args()
    store = args.output_dir.resolve()
    store.mkdir(parents=True, exist_ok=True)
    (store / "logs").mkdir(exist_ok=True)
    from harness.core.fingerprint import fingerprint_dir
    from harness.tasks.swebench import DATASET, _rows
    from harness.tasks.terminal_bench import COMMIT, PINS, ensure_task

    manifest = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_calls": False,
        "tb_commit": COMMIT,
        "swe_dataset": DATASET.model_dump(),
        "tasks": [],
        "images": [],
    }
    path = store / "manifest.json"

    def save():
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    for name, pin in PINS.items():
        try:
            task = ensure_task(name)
            target = store / "terminal-bench" / name
            import shutil

            if not target.exists():
                shutil.copytree(task, target)
            if fingerprint_dir(target) != pin.fingerprint:
                raise ValueError(f"cached task fingerprint mismatch: {target}")
            manifest["tasks"].append(
                {
                    "name": name,
                    "path": str(target.relative_to(ROOT)),
                    "fingerprint": pin.fingerprint,
                    "status": "cached",
                }
            )
        except Exception as e:
            manifest["tasks"].append(
                {"name": name, "status": "failed", "error": type(e).__name__ + ": " + str(e)[:300]}
            )
        save()
    try:
        rows = _rows()
        chosen = {key: rows[key] for key in ["pytest-dev__pytest-5227", "django__django-11099"]}
        (store / "swe-instances.json").write_text(json.dumps(chosen, ensure_ascii=False, indent=2))
        manifest["swe_rows"] = "cached"
        save()
    except Exception as e:
        manifest["swe_rows"] = str(e)
        save()
    if not args.images:
        return
    images = [(name, pin.image, "linux/amd64") for name, pin in PINS.items()] + [
        (
            name,
            "swebench/sweb.eval.x86_64." + name.replace("__", "_1776_") + ":latest",
            "linux/amd64",
        )
        for name in ["pytest-dev__pytest-5227", "django__django-11099"]
    ]
    deadline = time.monotonic() + 25 * 60
    for name, image, platform in images:
        item = {"name": name, "reference": image, "platform": platform}
        manifest["images"].append(item)
        try:
            cached = subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, text=True, timeout=20
            )
            if cached.returncode:
                if time.monotonic() >= deadline:
                    item["status"] = "deferred_wait_budget"
                    save()
                    continue
                with (store / "logs" / f"{name}.pull.log").open("a") as log:
                    result = subprocess.run(
                        ["docker", "pull", "--platform", platform, image],
                        stdout=log,
                        stderr=log,
                        timeout=min(600, max(1, int(deadline - time.monotonic()))),
                    )
                if result.returncode:
                    item["status"] = "pull_failed"
                    save()
                    print(name, item["status"], flush=True)
                    continue
                cached = subprocess.run(
                    ["docker", "image", "inspect", image],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
            info = json.loads(cached.stdout)[0]
            item.update(
                status="cached",
                image_id=info["Id"],
                repo_digests=info.get("RepoDigests", []),
                architecture=info.get("Architecture"),
                size_bytes=info.get("Size"),
            )
        except subprocess.TimeoutExpired:
            item["status"] = "timeout"
        except Exception as e:
            item.update(status="failed", error=type(e).__name__ + ": " + str(e)[:300])
        save()
        print(name, item["status"], flush=True)
    manifest["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    save()


if __name__ == "__main__":
    main()
