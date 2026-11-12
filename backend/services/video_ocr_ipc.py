"""Small, dependency-free file messages for the isolated video worker."""

import json
import os
from pathlib import Path


def write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as target:
            json.dump(value, target, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path, max_bytes=8 * 1024 * 1024):
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError("Video worker message exceeds the size limit")
    with path.open("r", encoding="utf-8") as source:
        return json.load(source)
