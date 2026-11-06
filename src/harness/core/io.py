from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harness.core.observability import safe_data
from harness.core.types import SampleResult


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text())


def write_results(path: Path, results: list[SampleResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for r in results:
                f.write(json.dumps(safe_data(r.model_dump(mode="json")), ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_results(path: Path) -> list[SampleResult]:
    with open(path) as f:
        return [SampleResult.model_validate_json(line) for line in f if line.strip()]
