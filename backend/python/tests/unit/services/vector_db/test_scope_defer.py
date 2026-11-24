"""A deferring scope filter (`ScopeFilter.defer`) decides only the points a trusted stamp of a current connector covers; every other point is left
to the request's own arms. The plain filter is unchanged."""

from __future__ import annotations

import pytest

from app.services.vector_db import scope_expr as sx
from app.services.vector_db.pgvector import scope_filter as sql
from tests.integration.vector_db.scope_world import (
    ADMIT_CASES,
    EVERYTHING,
    TRUST_CASES,
    WORLD,
    f,
)

NO_TRUSTED_STAMP = {"unstamped", "legacy", "unknown_stamp", "old_version", "old_generation", "new_generation", "no_connector", "unavailable_stamp"}


def undecided(flt) -> set[str]:
    """The points no trusted stamp of a current connector covers: no usable stamp, or a connector outside ``current``."""
    return NO_TRUSTED_STAMP | {v for v, (conn, *_rest) in WORLD.items() if not set(conn) <= flt.current}


def payload(vrid: str) -> dict:
    conn, groups, roots, stamp = WORLD[vrid]
    return {"connectorIds": conn, "recordGroupIds": groups, "rootRecordGroupIds": roots, **(stamp or {})}


def returned(flt) -> set[str]:
    node = sx.admits(flt)
    return {v for v in WORLD if sx.evaluate(node, payload(v))}


@pytest.mark.parametrize(("kw", "admitted"), [p.values for p in ADMIT_CASES])
def test_a_covered_point_is_decided_by_its_stamp_and_every_other_point_is_left_over(kw: dict, admitted: set[str]) -> None:
    flt = f(**kw, defer=True)
    got, left = returned(flt), undecided(flt)
    assert got - left == admitted - left          # both ways: nothing the stamps hide comes back, nothing they admit is lost
    assert left <= got                                   # and what they cannot decide is the request's, not hidden
    assert returned(f(**kw)) == admitted        # the plain filter still admits exactly its groups


@pytest.mark.parametrize(("kw", "_"), [p.values for p in TRUST_CASES])
def test_a_filter_that_trusts_a_stamp_by_version_and_generation_only(kw: dict, _: set[str]) -> None:
    flt = f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING, defer=True, **kw)
    got = returned(flt)
    assert undecided(flt) <= got or flt.trusts_nothing
    if flt.trusts_nothing:
        assert got == set(WORLD) and sx.admits(flt) == sx.TRUE          # nothing trusted: the request's own arms alone
        assert sql.predicate(flt, 10) == ("", [])
    else:
        assert not flt.matches_nothing


def test_the_plain_filter_that_trusts_nothing_admits_nothing() -> None:
    flt = f(generation=-1)
    assert sx.admits(flt) == sx.FALSE and flt.matches_nothing and sql.predicate(flt, 10) == (" AND false", [])


def test_the_two_sql_forms_share_their_parameters_and_the_plain_one_is_unchanged() -> None:
    plain, deferring = sql.predicate(f(readable_groups=frozenset({"g1"})), 10), sql.predicate(f(readable_groups=frozenset({"g1"}), defer=True), 10)
    assert plain[1] == deferring[1] and plain[0].startswith(" AND EXISTS (SELECT 1 FROM retrieval.point_groups sg WHERE ") and plain[0].endswith("OFFSET 0)")
    assert deferring[0].startswith(" AND COALESCE((SELECT coalesce(") and deferring[0].endswith(", true)") and "EXISTS" not in deferring[0]
