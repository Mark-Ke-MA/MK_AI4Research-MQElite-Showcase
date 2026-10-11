"""Explicit, private, symlink-resistant runtime path construction."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from mqelite.errors import StorageError, ValidationError

PERSONA_ID = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


def is_ignored_metadata_entry(path: Path) -> bool:
    """Return whether an entry is non-canonical hidden filesystem metadata.

    Canonical MQElite hot-tree names never begin with a dot. Finder, editors,
    and workspace tools commonly create dot-prefixed metadata beside managed
    files; those entries must not become memory input or block maintenance.
    """

    return path.name.startswith(".")


def is_canonical_hot_month_name(path: Path) -> bool:
    """Return whether an entry name is an exact, valid ``YYYY-MM`` month."""

    if re.fullmatch(r"\d{4}-\d{2}", path.name) is None:
        return False
    try:
        date.fromisoformat(f"{path.name}-01")
    except ValueError:
        return False
    return True


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_within(anchor: Path, candidate: Path) -> None:
    anchor_abs = _absolute_lexical(anchor)
    candidate_abs = _absolute_lexical(candidate)
    try:
        common = Path(os.path.commonpath((anchor_abs, candidate_abs)))
    except ValueError as exc:
        raise StorageError("path is outside the configured data root") from exc
    if common != anchor_abs:
        raise StorageError("path is outside the configured data root")


def reject_symlink_path(anchor: Path, candidate: Path) -> None:
    """Reject existing symlinks at the anchor or any managed descendant."""

    _require_within(anchor, candidate)
    anchor = _absolute_lexical(anchor)
    candidate = _absolute_lexical(candidate)
    current = anchor
    paths = [anchor]
    try:
        relative = candidate.relative_to(anchor)
    except ValueError as exc:
        raise StorageError("path is outside the configured data root") from exc
    for part in relative.parts:
        current = current / part
        paths.append(current)
    for path in paths:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise StorageError("symbolic links are forbidden in managed paths")


def ensure_private_directory(anchor: Path, path: Path) -> None:
    reject_symlink_path(anchor, path)
    path.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
    reject_symlink_path(anchor, path)
    if not path.is_dir():
        raise StorageError("managed directory path is not a directory")
    os.chmod(path, DIRECTORY_MODE)


@dataclass(frozen=True, slots=True)
class DataLayout:
    data_root: Path

    def __post_init__(self) -> None:
        if not self.data_root.is_absolute():
            raise ValidationError("data_root must be absolute")
        root = _absolute_lexical(self.data_root)
        object.__setattr__(self, "data_root", root)

    @property
    def personas_root(self) -> Path:
        return self.data_root / "personas"

    @property
    def tmp_root(self) -> Path:
        return self.data_root / "tmp"

    @property
    def audit_root(self) -> Path:
        return self.data_root / "audit"

    def initialize(self) -> None:
        if self.data_root.exists() and self.data_root.is_symlink():
            raise StorageError("data_root must not be a symbolic link")
        self.data_root.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        os.chmod(self.data_root, DIRECTORY_MODE)
        for path in (self.personas_root, self.tmp_root, self.audit_root):
            ensure_private_directory(self.data_root, path)

    def persona(self, persona_id: str) -> "PersonaLayout":
        if not isinstance(persona_id, str) or PERSONA_ID.fullmatch(persona_id) is None:
            raise ValidationError("persona_id is not a safe path component")
        return PersonaLayout(data=self, persona_id=persona_id)


@dataclass(frozen=True, slots=True)
class PersonaLayout:
    data: DataLayout
    persona_id: str

    @property
    def root(self) -> Path:
        return self.data.personas_root / self.persona_id

    @property
    def hot_root(self) -> Path:
        return self.root / "hot"

    @property
    def archive_root(self) -> Path:
        return self.root / "archive"

    @property
    def index_root(self) -> Path:
        return self.root / "index"

    @property
    def state_root(self) -> Path:
        return self.root / "state"

    @property
    def quarantine_root(self) -> Path:
        return self.root / "quarantine"

    @property
    def locks_root(self) -> Path:
        return self.root / "locks"

    @property
    def ingest_db(self) -> Path:
        return self.state_root / "ingest.sqlite3"

    @property
    def recall_db(self) -> Path:
        return self.index_root / "recall.sqlite3"

    @property
    def daily_lock(self) -> Path:
        return self.locks_root / "daily.lock"

    def hot_day(self, memory_date: date) -> Path:
        month = memory_date.strftime("%Y-%m")
        return self.hot_root / month / f"{memory_date.isoformat()}_l2.json"

    def archive_tar(self, year: int, month: int, generation: int) -> Path:
        if generation < 1:
            raise ValidationError("archive generation must be positive")
        return self.archive_root / f"{year:04d}" / f"{year:04d}-{month:02d}.g{generation:04d}.tar.gz"

    def archive_manifest(self, year: int, month: int, generation: int) -> Path:
        return self.archive_tar(year, month, generation).with_suffix("").with_suffix(".manifest.json")

    def initialize(self) -> None:
        self.data.initialize()
        for path in (
            self.root,
            self.hot_root,
            self.archive_root,
            self.index_root,
            self.state_root,
            self.quarantine_root,
            self.locks_root,
        ):
            ensure_private_directory(self.data.data_root, path)

    def assert_safe(self, path: Path) -> None:
        _require_within(self.root, path)
        reject_symlink_path(self.data.data_root, path)
