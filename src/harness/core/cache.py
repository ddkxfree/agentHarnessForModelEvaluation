from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from harness.core.types import GenerateConfig, Message, ModelOutput


def cache_key(
    model_spec: dict[str, Any],
    messages: list[Message],
    config: GenerateConfig,
    tools: list[dict[str, Any]] | None = None,
    namespace: str | None = None,
) -> str:
    payload: dict[str, Any] = {
        "model": model_spec,
        "messages": messages,
        "config": config.cache_fields(),
    }
    if tools:
        payload["tools"] = tools
    # Left out when unset so keys written before namespaces existed stay valid.
    if namespace is not None:
        payload["namespace"] = namespace
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


class DiskCache:
    """Content-addressed cache of successful model outputs.

    Re-running an interrupted or identical evaluation only pays for requests that
    have not completed before.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> ModelOutput | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return ModelOutput.model_validate_json(path.read_text())
        except ValueError:
            return None

    def put(self, key: str, output: ModelOutput) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(output.model_dump_json())
        os.replace(tmp, path)
