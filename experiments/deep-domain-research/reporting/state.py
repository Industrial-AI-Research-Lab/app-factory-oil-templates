import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .schema import ReportError


def save_manifest(manifest, path=None):
    destination = Path(path or manifest["manifest_path"])
    temporary = destination.with_name(f".{destination.name}.{uuid4()}.tmp")
    try:
        temporary.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def export_lock(folder):
    lock = Path(folder) / ".export.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ReportError(
            "EXPORT_BUSY", "validate", "Another export operation holds the lock"
        ) from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)
