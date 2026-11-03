"""The hop and seed rules of the PostgreSQL access check, as ``Neo4jProvider._kh_v3_granted_hop`` and its seed arm state them."""
from __future__ import annotations

import itertools

import pytest

from app.services.graph_db.postgres.access import (
    PRINCIPALS,
    _is_seed,
    _walk_app,
    _walk_seed,
)

RULES = ["OPEN", "STRICT", "RESTRICTED", "WEIRD", ""]


def _info(rule: str, *, deleted: bool = False) -> dict:
    return {"rule": rule, "deleted": deleted, "cid": "app", "model": None, "coll": "records"}


def _hop(rule: str, *, inherits: bool, granted: bool, opens: bool) -> bool:
    """The Neo4j hop: ``opens OR (inherits AND (rule IN (STRICT, OPEN) OR (rule = RESTRICTED AND granted))) OR (rule IN (STRICT, OPEN) AND granted)``."""
    open_rule = rule in ("STRICT", "OPEN")
    return opens or (inherits and (open_rule or (rule == "RESTRICTED" and granted))) or (open_rule and granted)


@pytest.mark.parametrize(("rule", "inherits", "granted", "opens"), list(itertools.product(RULES, [True, False], [True, False], [True, False])))
def test_the_walk_to_the_app_admits_a_hop_exactly_as_the_listing_rule_does(rule: str, inherits: bool, granted: bool, opens: bool) -> None:
    walk = _walk_app(opens=opens, granted={"c"} if granted else set())
    assert walk("c", inherits, _info(rule)) is _hop(rule, inherits=inherits, granted=granted, opens=opens)


@pytest.mark.parametrize("opens", [True, False])
def test_a_deleted_node_is_never_walked_through(opens: bool) -> None:
    assert _walk_app(opens=opens, granted={"c"})("c", True, _info("OPEN", deleted=True)) is False
    assert _walk_seed({"c"})("c", True, _info("OPEN", deleted=True)) is False


@pytest.mark.parametrize(("rule", "inherits", "granted"), list(itertools.product(RULES, [True, False], [True, False])))
def test_below_a_seed_a_hop_must_be_open_and_inherit_or_be_granted(rule: str, inherits: bool, granted: bool) -> None:
    assert _walk_seed({"c"} if granted else set())("c", inherits, _info(rule)) is (rule == "OPEN" and (inherits or granted))


@pytest.mark.parametrize(("doc", "granted", "seed"), [
    ({"connectorId": "app"}, True, True),
    ({"connectorId": "app", "accessRule": "OPEN"}, True, True),
    ({"connectorId": "app", "accessRule": ""}, True, False),            # an empty rule is not OPEN
    ({"connectorId": "app", "accessRule": "STRICT"}, True, False),
    ({"connectorId": "app", "isDeleted": True}, True, False),
    ({"connectorId": "other"}, True, False),
    ({"connectorId": "app"}, False, False),
])
def test_a_grant_is_a_seed_only_when_open_live_of_the_connector_and_granted(doc: dict, granted: bool, seed: bool) -> None:
    assert _is_seed(doc, "records", "app", {"n"} if granted else set(), "n") is seed


def test_an_app_or_a_user_is_never_a_seed() -> None:
    assert _is_seed({"connectorId": "app"}, "apps", "app", {"n"}, "n") is False


def test_the_grantees_of_a_permission_are_the_principal_collections_only() -> None:
    assert set(PRINCIPALS) == {"users", "groups", "roles", "teams", "organizations"}
