"""Advisory persona locks with private on-disk state."""

from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from mqelite.storage.layout import FILE_MODE, PersonaLayout, ensure_private_directory


@contextmanager
def exclusive_lock(layout: PersonaLayout, lock_path: Path) -> Iterator[None]:
    layout.assert_safe(lock_path)
    ensure_private_directory(layout.data.data_root, lock_path.parent)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, FILE_MODE)
    os.chmod(lock_path, FILE_MODE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
