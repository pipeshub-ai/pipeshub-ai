"""Skill bundles: a skill's folder (``SKILL.md`` plus resources) as immutable, content-addressed bytes.

The core knows no skill store: a skill is a name, an optional version and a
mapping of relative paths to bytes. Binary resources are bytes like any other.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, TypedDict

from harness_core.contracts.canonical_json import canonical_digest
from harness_core.workspace.types import normalize_workspace_file_path

if TYPE_CHECKING:
    from collections.abc import Mapping

SKILL_ENTRYPOINT = "SKILL.md"
BUNDLE_FORMAT = "skill-bundle/1"
_DIGEST_PREFIX = "sha256:"
_DIGEST_HEX = 64


class BundleFileEntry(TypedDict):
    sha256: str
    size: int


class SkillBundleError(Exception):
    """Base of the typed skill-bundle refusals; ``reason`` is a stable machine-readable code."""

    reason: ClassVar[str] = "skill_bundle_error"


class SkillBundleInvalid(SkillBundleError, ValueError):
    reason: ClassVar[str] = "skill_bundle_invalid"


class SkillBundleUnavailable(SkillBundleError, LookupError):
    """No content for the skill, version or pin in this scope."""

    reason: ClassVar[str] = "skill_not_found"


class SkillBundleIntegrityError(SkillBundleError):
    """Stored bytes no longer match the bundle digest; they are never served."""

    reason: ClassVar[str] = "skill_bundle_corrupt"


@dataclass(frozen=True, slots=True)
class SkillRef:
    """A skill by name; ``version=None`` is its current content."""

    name: str
    version: str | None = None

    def __post_init__(self) -> None:
        validate_skill_name(self.name)


@dataclass(frozen=True, slots=True)
class SkillPin:
    """What a run pinned: a content digest, or (runs admitted earlier) ``legacy_skill_pin_digest``."""

    name: str
    bundle_digest: str

    def __post_init__(self) -> None:
        validate_skill_name(self.name)
        if not is_digest(self.bundle_digest):
            raise SkillBundleInvalid("A skill pin needs a sha256 digest")


@dataclass(frozen=True, slots=True)
class SkillBundleRef:
    """An immutable bundle in a workspace store; metadata only, never file bytes."""

    name: str
    digest: str
    workspace_id: str
    revision: int
    file_count: int
    size_bytes: int

    def __post_init__(self) -> None:
        validate_skill_name(self.name)
        if not is_digest(self.digest) or not self.workspace_id:
            raise SkillBundleInvalid("Skill bundle reference is incomplete")
        if self.revision <= 0 or self.file_count <= 0 or self.size_bytes < 0:
            raise SkillBundleInvalid("Skill bundle reference has invalid counters")


@dataclass(frozen=True, slots=True)
class SkillBundleLimits:
    max_files: int = 500
    max_total_bytes: int = 50 * 1024 * 1024
    max_file_bytes: int = 25 * 1024 * 1024

    def __post_init__(self) -> None:
        if min(self.max_files, self.max_total_bytes, self.max_file_bytes) <= 0:
            raise ValueError("Skill bundle quotas must be positive")


def is_digest(value: str) -> bool:
    hex_part = value[len(_DIGEST_PREFIX) :]
    return (
        value.startswith(_DIGEST_PREFIX)
        and len(hex_part) == _DIGEST_HEX
        and all(c in "0123456789abcdef" for c in hex_part)
    )


def validate_skill_name(name: str) -> None:
    """A skill is one folder in a skills directory: a single canonical path segment."""
    try:
        canonical = normalize_workspace_file_path(name)
    except ValueError as exc:
        raise SkillBundleInvalid(f"Skill name is not a valid folder name: {exc}") from exc
    if canonical != name or "/" in name or name.startswith("."):
        raise SkillBundleInvalid("Skill name must be one canonical path segment")


def validate_skill_files(
    files: Mapping[str, bytes], limits: SkillBundleLimits | None = None
) -> dict[str, bytes]:
    """Canonical paths → bytes, or ``SkillBundleInvalid``: ``SKILL.md`` at the root in UTF-8,
    no traversal, no two paths that differ only by case (they collide on case-insensitive
    file systems), and within the quotas."""
    limits = limits or SkillBundleLimits()
    if not files or len(files) > limits.max_files:
        raise SkillBundleInvalid("Skill bundle has an invalid file count")
    canonical: dict[str, bytes] = {}
    aliases: set[str] = set()
    total = 0
    for supplied_path, content in files.items():
        try:
            path = normalize_workspace_file_path(supplied_path)
        except ValueError as exc:
            raise SkillBundleInvalid(f"Skill bundle path is invalid: {exc}") from exc
        alias = path.casefold()
        if alias in aliases:
            raise SkillBundleInvalid("Skill bundle contains two paths that differ only by case")
        aliases.add(alias)
        if not isinstance(content, bytes):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise SkillBundleInvalid("Skill bundle file content must be bytes")
        if len(content) > limits.max_file_bytes:
            raise SkillBundleInvalid("Skill bundle file exceeds its quota")
        total += len(content)
        if total > limits.max_total_bytes:
            raise SkillBundleInvalid("Skill bundle exceeds its total byte quota")
        canonical[path] = bytes(content)
    if SKILL_ENTRYPOINT not in canonical:
        raise SkillBundleInvalid("Skill bundle must contain SKILL.md at its root")
    try:
        _ = canonical[SKILL_ENTRYPOINT].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillBundleInvalid("SKILL.md must be valid UTF-8") from exc
    return canonical


def skill_bundle_manifest(files: Mapping[str, bytes]) -> dict[str, BundleFileEntry]:
    """Canonical path → plaintext SHA-256 and size: what the bundle digest covers.

    Plaintext SHA-256 and size only: MIME types are guessed differently per
    platform, and sealed stores hash per session.
    """
    return {
        normalize_workspace_file_path(path): {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
        for path, content in files.items()
    }


def skill_bundle_manifest_digest(manifest: Mapping[str, BundleFileEntry]) -> str:
    """Files form a JSON object so RFC 8785 orders paths the same in both languages."""
    return canonical_digest({"format": BUNDLE_FORMAT, "files": dict(manifest)})


def skill_bundle_digest(files: Mapping[str, bytes]) -> str:
    """The content digest a run pins. Node computes the same (``domain/skill-bundle.ts``)."""
    return skill_bundle_manifest_digest(skill_bundle_manifest(files))


def legacy_skill_pin_digest(name: str, version: str | None) -> str:
    """The pin Node wrote before content digests (``canonicalDigest({name, version})``); still resolvable."""
    return canonical_digest({"name": name, "version": version})


__all__ = [
    "BUNDLE_FORMAT",
    "BundleFileEntry",
    "SKILL_ENTRYPOINT",
    "SkillBundleError",
    "SkillBundleIntegrityError",
    "SkillBundleInvalid",
    "SkillBundleLimits",
    "SkillBundleRef",
    "SkillBundleUnavailable",
    "SkillPin",
    "SkillRef",
    "is_digest",
    "legacy_skill_pin_digest",
    "skill_bundle_digest",
    "skill_bundle_manifest",
    "skill_bundle_manifest_digest",
    "validate_skill_files",
    "validate_skill_name",
]
