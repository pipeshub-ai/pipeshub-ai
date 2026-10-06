from __future__ import annotations

import pytest

from app.modules.authz.role_mapper import (
    CANONICAL_ROLES,
    ROLE_TABLE,
    at_least,
    from_canonical,
    rank,
    to_canonical,
)


def test_unknown_role_maps_to_none() -> None:
    assert to_canonical("kb", "SUPERUSER") == "none"
    assert to_canonical("kb", None) == "none"
    assert to_canonical("kb", "owner") == "none"
    assert to_canonical("document", "OWNER") == "none"


def test_at_least_follows_ladder_order() -> None:
    for i, lower in enumerate(CANONICAL_ROLES):
        for j, higher in enumerate(CANONICAL_ROLES):
            assert at_least(higher, lower) == (j >= i)
    assert rank("bogus") == 0


def test_organizer_is_manager_and_fileorganizer_is_editor() -> None:
    assert to_canonical("kb", "ORGANIZER") == "manager"
    assert to_canonical("kb", "FILEORGANIZER") == "editor"
    assert from_canonical("kb", "editor") == "WRITER"


@pytest.mark.parametrize("rtype", sorted(ROLE_TABLE))
def test_every_stored_value_round_trips_to_equivalent_role(rtype: str) -> None:
    for stored, canonical in ROLE_TABLE[rtype]:
        back = from_canonical(rtype, canonical)
        assert back is not None
        assert to_canonical(rtype, back) == canonical
        assert to_canonical(rtype, stored) == canonical


def test_from_canonical_returns_none_when_unrepresentable() -> None:
    assert from_canonical("project", "manager") is None
    assert from_canonical("chat", "commenter") is None
    assert from_canonical("nope", "owner") is None
