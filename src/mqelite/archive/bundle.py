"""Deterministic L2-only tar.gz bundles with external integrity manifests."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePosixPath
from typing import Any, Iterable

from mqelite.domain.l2 import L2Document, parse_legacy_l2
from mqelite.errors import ConflictError, StorageError, ValidationError
from mqelite.storage.atomic import atomic_write_bytes
from mqelite.storage.layout import PersonaLayout
from mqelite.storage.locking import exclusive_lock

ARCHIVE_SCHEMA_VERSION = "mqelite.archive-manifest.v1"
MAX_MEMBERS = 40
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_JSON_DEPTH = 64


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _json_depth(value: object, depth: int = 0) -> int:
    if depth > MAX_JSON_DEPTH:
        raise ValidationError("archive JSON nesting exceeds the safety limit")
    if isinstance(value, dict):
        return max((_json_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_json_depth(item, depth + 1) for item in value), default=depth)
    return depth


@dataclass(frozen=True, slots=True)
class ArchiveDay:
    memory_date: date
    payload: bytes
    source_kind: str
    empty_day: bool = False

    def __post_init__(self) -> None:
        if self.source_kind not in {"native", "legacy_mqe"}:
            raise ValidationError("archive day source_kind is unsupported")
        if not isinstance(self.payload, bytes):
            raise ValidationError("archive day payload must be bytes")


@dataclass(frozen=True, slots=True)
class ArchiveBundle:
    tar_bytes: bytes
    internal_manifest: bytes
    external_manifest: bytes


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o600
    return info


def build_archive(
    *,
    persona_id: str,
    year: int,
    month: int,
    generation: int,
    created_at: datetime,
    days: Iterable[ArchiveDay],
    tool_version: str,
    supersedes_generation: int | None = None,
) -> ArchiveBundle:
    if generation < 1:
        raise ValidationError("archive generation must be positive")
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValidationError("archive created_at must include a UTC offset")
    ordered = sorted(days, key=lambda item: item.memory_date)
    if not ordered or len(ordered) > MAX_MEMBERS - 1:
        raise ValidationError("archive day count is outside the supported range")
    seen: set[date] = set()
    members: list[dict[str, object]] = []
    source_kinds: set[str] = set()
    empty_days: list[str] = []
    for day in ordered:
        if day.memory_date in seen:
            raise ValidationError("archive contains duplicate memory dates")
        seen.add(day.memory_date)
        if day.memory_date.year != year or day.memory_date.month != month:
            raise ValidationError("archive member date is outside its calendar month")
        if len(day.payload) > MAX_MEMBER_BYTES:
            raise ValidationError("archive member exceeds the size limit")
        if day.source_kind == "native":
            try:
                decoded = json.loads(day.payload)
            except json.JSONDecodeError as exc:
                raise ValidationError("native archive member is not valid JSON") from exc
            document = L2Document.from_dict(decoded, expected_agent_id=persona_id)
            if document.date != day.memory_date or document.canonical_bytes() != day.payload:
                raise ValidationError("native archive member is not canonical")
        else:
            legacy = parse_legacy_l2(
                day.payload,
                mapped_agent_id=persona_id,
                member_name=f"{day.memory_date.isoformat()}_l2.json",
            )
            if legacy.date != day.memory_date:
                raise ValidationError("legacy archive member date mismatch")
        name = f"days/{day.memory_date.isoformat()}_l2.json"
        members.append({"name": name, "size": len(day.payload), "sha256": _digest(day.payload)})
        source_kinds.add(day.source_kind)
        if day.empty_day:
            empty_days.append(day.memory_date.isoformat())

    source_type = "mixed" if len(source_kinds) > 1 else next(iter(source_kinds))
    manifest: dict[str, object] = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "persona_id": persona_id,
        "calendar_month": f"{year:04d}-{month:02d}",
        "generation": generation,
        "created_at": created_at.isoformat(),
        "source_type": source_type,
        "day_range": [ordered[0].memory_date.isoformat(), ordered[-1].memory_date.isoformat()],
        "empty_days": empty_days,
        "members": members,
        "supersedes_generation": supersedes_generation,
        "tool_version": tool_version,
    }
    internal = _canonical_json(manifest)
    raw_tar = io.BytesIO()
    with tarfile.open(fileobj=raw_tar, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(_tar_info("manifest.json", len(internal)), io.BytesIO(internal))
        for day in ordered:
            name = f"days/{day.memory_date.isoformat()}_l2.json"
            archive.addfile(_tar_info(name, len(day.payload)), io.BytesIO(day.payload))
    compressed = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=compressed, mtime=0, compresslevel=9) as handle:
        handle.write(raw_tar.getvalue())
    tar_bytes = compressed.getvalue()
    external_value = dict(manifest)
    external_value["tar_sha256"] = _digest(tar_bytes)
    return ArchiveBundle(tar_bytes, internal, _canonical_json(external_value))


def verify_archive(tar_bytes: bytes, external_manifest: bytes, *, expected_persona_id: str | None = None) -> dict[str, Any]:
    if not tar_bytes or len(tar_bytes) > MAX_TOTAL_BYTES:
        raise ValidationError("archive compressed size is outside the safety limit")
    try:
        external = json.loads(external_manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("external archive manifest is invalid") from exc
    _json_depth(external)
    if not isinstance(external, dict) or external.get("tar_sha256") != _digest(tar_bytes):
        raise ValidationError("archive tar digest mismatch")
    if expected_persona_id is not None and external.get("persona_id") != expected_persona_id:
        raise ValidationError("archive persona mismatch")
    expected_members = external.get("members")
    if not isinstance(expected_members, list):
        raise ValidationError("archive member manifest is invalid")
    expected_by_name = {
        item.get("name"): item
        for item in expected_members
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    if len(expected_by_name) != len(expected_members):
        raise ValidationError("archive member manifest contains duplicates or invalid entries")
    seen: set[str] = set()
    total = 0
    internal_bytes: bytes | None = None
    try:
        with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as archive:
            entries = archive.getmembers()
            if len(entries) > MAX_MEMBERS:
                raise ValidationError("archive member count exceeds the safety limit")
            for entry in entries:
                path = PurePosixPath(entry.name)
                if entry.name in seen or path.is_absolute() or ".." in path.parts or not entry.isfile():
                    raise ValidationError("archive contains an unsafe member")
                seen.add(entry.name)
                if entry.size < 0 or entry.size > MAX_MEMBER_BYTES:
                    raise ValidationError("archive member exceeds the size limit")
                total += entry.size
                if total > MAX_TOTAL_BYTES or total > len(tar_bytes) * MAX_COMPRESSION_RATIO:
                    raise ValidationError("archive expansion exceeds the safety limit")
                extracted = archive.extractfile(entry)
                if extracted is None:
                    raise ValidationError("archive member cannot be read")
                payload = extracted.read(entry.size + 1)
                if len(payload) != entry.size:
                    raise ValidationError("archive member size mismatch")
                if entry.name == "manifest.json":
                    internal_bytes = payload
                else:
                    expected = expected_by_name.get(entry.name)
                    if expected is None or expected.get("size") != len(payload) or expected.get("sha256") != _digest(payload):
                        raise ValidationError("archive member integrity mismatch")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValidationError("archive container is invalid") from exc
    if internal_bytes is None or seen != {"manifest.json", *expected_by_name}:
        raise ValidationError("archive members do not match the manifest")
    try:
        internal = json.loads(internal_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("internal archive manifest is invalid") from exc
    without_tar = dict(external)
    without_tar.pop("tar_sha256", None)
    if internal != without_tar or internal_bytes != _canonical_json(internal):
        raise ValidationError("internal and external archive manifests disagree")
    return external


def commit_archive(layout: PersonaLayout, bundle: ArchiveBundle) -> tuple[str, str]:
    manifest = verify_archive(bundle.tar_bytes, bundle.external_manifest, expected_persona_id=layout.persona_id)
    month = manifest["calendar_month"]
    year, numeric_month = (int(part) for part in month.split("-"))
    generation = int(manifest["generation"])
    tar_path = layout.archive_tar(year, numeric_month, generation)
    manifest_path = layout.archive_manifest(year, numeric_month, generation)
    pointer_path = layout.state_root / "archive-active.json"
    layout.initialize()
    for path in (tar_path, manifest_path, pointer_path):
        layout.assert_safe(path)
    with exclusive_lock(layout, layout.locks_root / "archive.lock"):
        active: dict[str, dict[str, object]] = {}
        if pointer_path.exists():
            layout.assert_safe(pointer_path)
            try:
                pointer = json.loads(pointer_path.read_bytes())
            except json.JSONDecodeError as exc:
                raise ValidationError("active archive pointer is invalid") from exc
            if (
                not isinstance(pointer, dict)
                or pointer.get("schema_version") != "mqelite.archive-active.v1"
                or pointer.get("persona_id") != layout.persona_id
                or not isinstance(pointer.get("months"), dict)
            ):
                raise ValidationError("active archive pointer has an invalid shape")
            active = pointer["months"]
        current = active.get(month)
        current_generation = current.get("generation") if isinstance(current, dict) else None
        if current_generation == generation:
            if (
                tar_path.is_file()
                and manifest_path.is_file()
                and tar_path.read_bytes() == bundle.tar_bytes
                and manifest_path.read_bytes() == bundle.external_manifest
            ):
                return tar_path.name, manifest_path.name
            raise StorageError("active archive generation bytes do not match")
        if manifest.get("supersedes_generation") != current_generation:
            raise ConflictError("archive supersedes generation does not match the active pointer")
        if tar_path.exists() or manifest_path.exists():
            if (
                not tar_path.is_file()
                or not manifest_path.is_file()
                or tar_path.read_bytes() != bundle.tar_bytes
                or manifest_path.read_bytes() != bundle.external_manifest
            ):
                raise StorageError("archive generation already exists with different bytes")
        else:
            atomic_write_bytes(layout, tar_path, bundle.tar_bytes)
            atomic_write_bytes(layout, manifest_path, bundle.external_manifest)
        active[month] = {
            "generation": generation,
            "manifest_sha256": _digest(bundle.external_manifest),
        }
        pointer = {
            "schema_version": "mqelite.archive-active.v1",
            "persona_id": layout.persona_id,
            "months": active,
        }
        atomic_write_bytes(layout, pointer_path, _canonical_json(pointer))
    return tar_path.name, manifest_path.name
