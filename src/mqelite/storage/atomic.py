"""Crash-recoverable writes that never stage plaintext outside data_root."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Callable

from mqelite.errors import StorageError
from mqelite.storage.layout import FILE_MODE, PersonaLayout, ensure_private_directory


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(
    layout: PersonaLayout,
    target: Path,
    payload: bytes,
    *,
    validate: Callable[[bytes], None] | None = None,
) -> None:
    if not isinstance(payload, bytes):
        raise StorageError("atomic payload must be bytes")
    layout.assert_safe(target)
    ensure_private_directory(layout.data.data_root, target.parent)
    temporary = target.parent / f".{target.name}.tmp-{secrets.token_hex(8)}"
    layout.assert_safe(temporary)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, FILE_MODE)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if validate is not None:
            validate(temporary.read_bytes())
        layout.assert_safe(target)
        os.replace(temporary, target)
        os.chmod(target, FILE_MODE)
        fsync_directory(target.parent)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
