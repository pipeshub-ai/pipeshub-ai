"""Canonical role ladder and per-resource role mapping.

Mirrors backend/nodejs/apps/src/modules/authz/domain/{ladder,role-mapper}.ts; both are
checked against tests/fixtures/authz/permissions-matrix.json. Pure: no graph or I/O imports.
"""

from __future__ import annotations

from typing import Final

CANONICAL_ROLES: Final[tuple[str, ...]] = (
    "none",
    "viewer",
    "commenter",
    "editor",
    "manager",
    "owner",
)

# For from_canonical the first entry per canonical role wins, so the KB alias
# FILEORGANIZER sits after WRITER. Team roles are membership roles and have no
# resource mapping (S5).
ROLE_TABLE: Final[dict[str, tuple[tuple[str, str], ...]]] = {
    "project": (("owner", "owner"), ("editor", "editor"), ("viewer", "viewer")),
    "chat": (("owner", "owner"), ("write", "editor"), ("read", "viewer")),
    "kb": (
        ("OWNER", "owner"),
        ("ORGANIZER", "manager"),
        ("WRITER", "editor"),
        ("FILEORGANIZER", "editor"),
        ("COMMENTER", "commenter"),
        ("READER", "viewer"),
    ),
    "agent": (
        ("OWNER", "owner"),
        ("ORGANIZER", "manager"),
        ("WRITER", "editor"),
        ("READER", "viewer"),
    ),
    "team": (("OWNER", "owner"), ("WRITER", "editor"), ("READER", "viewer")),
}

_TO_CANONICAL: Final[dict[str, dict[str, str]]] = {
    rtype: dict(entries) for rtype, entries in ROLE_TABLE.items()
}
_FROM_CANONICAL: Final[dict[str, dict[str, str]]] = {
    rtype: {canonical: stored for stored, canonical in reversed(entries)}
    for rtype, entries in ROLE_TABLE.items()
}


def rank(role: str) -> int:
    """Position on the ladder; an unknown role ranks as none."""
    return CANONICAL_ROLES.index(role) if role in CANONICAL_ROLES else 0


def at_least(actual: str, required: str) -> bool:
    return rank(actual) >= rank(required)


def to_canonical(resource_type: str, stored: str | None) -> str:
    """Unknown resource types and unknown or missing stored values map to none (fail closed)."""
    if not isinstance(stored, str):
        return "none"
    return _TO_CANONICAL.get(resource_type, {}).get(stored, "none")


def from_canonical(resource_type: str, canonical: str) -> str | None:
    """Stored value for the canonical role, or None when the resource has no such role."""
    return _FROM_CANONICAL.get(resource_type, {}).get(canonical)
