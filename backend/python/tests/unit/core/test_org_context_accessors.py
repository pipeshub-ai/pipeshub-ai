"""The two sanctioned accessors besides ``current()``: the bound org where an unbound one is the caller's refusal,
and the org a stored row names, for a tenancy check against the bound org (WP-118)."""

import pytest

from app.core.org_context import OrgContext, bind, bound_org_id, org_of, system


def _user(org: str) -> OrgContext:
    return OrgContext(org, "u1", "user", "jwt", "-")


def test_bound_org_id_is_the_bound_org_and_none_where_none_is_bound_or_the_scope_is_cross_org() -> None:
    assert bound_org_id() is None
    with bind(_user("o1")):
        assert bound_org_id() == "o1"
    with system("test"):
        assert bound_org_id() is None


@pytest.mark.parametrize(
    ("row", "org"),
    [
        ({"orgId": "o1"}, "o1"),
        ({"org_id": "o2"}, "o2"),
        ({"orgId": None, "org_id": "o3"}, "o3"),
        ({"orgId": "o1", "org_id": "other"}, "o1"),
        ({}, None),
        (None, None),
        ({"orgId": ""}, ""),
    ],
)
def test_org_of_reads_the_camel_then_the_snake_field(row: dict[str, object] | None, org: str | None) -> None:
    assert org_of(row) == org


def test_org_of_never_binds() -> None:
    assert org_of({"orgId": "o1"}) == "o1"
    assert bound_org_id() is None
