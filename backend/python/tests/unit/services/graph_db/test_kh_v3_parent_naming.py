"""The parent a knowledge-hub row names, chosen once for
both backends from the hierarchy parents their page queries return. What the
queries return is tested on real graphs in
``tests/integration/graph_permissions/test_provider_v3_listing_parity.py``."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

APP = "app-1"
ACCESS = {"grantee_ids": ["u"], "gated_app_ids": [APP], "by_connector": {APP: []}}


def _option(node_id: str, node_type: str = "record", *, internal: bool = False) -> dict:
    return {"id": node_id, "name": f"name of {node_id}", "nodeType": node_type, "isInternal": internal}


def _row(*options: dict) -> dict:
    return {"id": "r", "parentId": "default", "parentName": "default", "parentType": "record",
            "parentIsInternal": False, "parentOptions": [*options, _option(APP, "app")]}


def _provider(admitted: set[str]) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    provider.check_access = AsyncMock(side_effect=lambda _u, _o, node_ids=(), **_: AccessCheck(
        node_ids=frozenset(set(node_ids) & admitted)))
    return provider


async def _name(provider, rows, *, listed_under=None) -> list[dict]:
    await provider._kh_v3_name_parents(
        rows, org_id="org", apps={APP}, access=ACCESS, listed_under=listed_under, transaction=None,
    )
    return rows


async def test_a_parent_the_user_cannot_access_is_never_named() -> None:
    provider = _provider({"g-open"})
    row, = await _name(provider, [_row(_option("a-closed"), _option("g-open", "recordGroup"))])
    assert (row["parentId"], row["parentType"], row["parentName"]) == ("g-open", "recordGroup", "name of g-open")
    assert "parentOptions" not in row
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"a-closed", "g-open"}


async def test_with_no_parent_accessible_the_row_names_its_app() -> None:
    row, = await _name(_provider(set()), [_row(_option("a-closed"))])
    assert (row["parentId"], row["parentType"]) == (APP, "app")


async def test_browsing_names_the_node_browsed_whatever_else_holds_the_row() -> None:
    """A row is listed under each parent the user can reach, so in
    browse it names the one it is listed under, internal or not, unasked."""
    provider = _provider({"a-open"})
    row, = await _name(provider, [_row(_option("a-open"), _option("s-inbox", "recordGroup", internal=True))],
                       listed_under="s-inbox")
    assert row["parentId"] == "s-inbox" and row["parentIsInternal"] is True
    provider.check_access.assert_not_awaited()


async def test_an_internal_parent_is_named_when_no_real_one_is_reachable() -> None:
    """The drive location over Shared with Me, and no App fallback
    while a reachable parent exists."""
    inbox = _option("s-inbox", "recordGroup", internal=True)
    row, = await _name(_provider({"s-inbox"}), [_row(inbox)])
    assert (row["parentId"], row["parentIsInternal"]) == ("s-inbox", True)
    row, = await _name(_provider({"s-inbox", "z-folder"}), [_row(inbox, _option("z-folder"))])
    assert row["parentId"] == "z-folder"
    row, = await _name(_provider({"s-inbox"}), [_row(inbox, _option("z-folder"))])
    assert row["parentId"] == "s-inbox"


async def test_a_record_or_group_parent_comes_before_the_app_and_ties_go_by_id() -> None:
    row, = await _name(_provider({"b", "c"}), [_row(_option("c"), _option("b"))])
    assert row["parentId"] == "b"


async def test_the_app_is_never_asked_about() -> None:
    provider = _provider(set())
    await _name(provider, [_row()])
    provider.check_access.assert_not_awaited()


async def test_a_check_that_cannot_run_fails_the_page() -> None:
    """Naming a parent unchecked would disclose it; dropping it silently would
    hide an outage."""
    provider = _provider(set())
    provider.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
    with pytest.raises(PermissionVerificationUnavailableError):
        await _name(provider, [_row(_option("a"))])


async def test_rows_without_options_are_left_alone() -> None:
    rows = [{"id": "r", "parentId": "p"}]
    assert await _name(_provider(set()), rows) == [{"id": "r", "parentId": "p"}]


async def test_rows_merged_across_connectors_are_named_in_one_check() -> None:
    """The global search asks each connector's page without names and names the
    merged page once; every App the user is gated into is named unasked."""
    provider = _provider({"g-open"})
    other = {"id": "r2", "parentOptions": [_option("a-closed"), _option("app-2", "app")]}
    rows = [_row(_option("g-open", "recordGroup")), other]
    await provider.name_knowledge_hub_parents(
        rows, "org", {**ACCESS, "gated_app_ids": [APP, "app-2"]},
    )
    assert [row["parentId"] for row in rows] == ["g-open", "app-2"]
    provider.check_access.assert_awaited_once()
    assert set(provider.check_access.await_args.kwargs["node_ids"]) == {"g-open", "a-closed"}


def _own(group_id: str) -> dict:
    return {**_option(group_id, "recordGroup"), "ownGroup": True}


async def test_a_chain_top_names_its_own_group_when_no_parent_is_open() -> None:
    """A node below a gap names its own record group, not the App."""
    row, = await _name(_provider({"g-own"}), [_row(_option("p-closed"), _own("g-own"))])
    assert (row["parentId"], row["parentType"]) == ("g-own", "recordGroup")


async def test_an_open_parent_comes_before_the_own_group() -> None:
    row, = await _name(_provider({"z-parent", "a-own"}), [_row(_option("z-parent"), _own("a-own"))])
    assert row["parentId"] == "z-parent"


async def test_a_closed_own_group_leaves_the_app() -> None:
    row, = await _name(_provider(set()), [_row(_option("p-closed"), _own("g-own"))])
    assert row["parentId"] == APP


async def test_browsing_its_own_group_names_that_group_unasked() -> None:
    provider = _provider(set())
    row, = await _name(provider, [_row(_option("p-closed"), _own("g-own"))], listed_under="g-own")
    assert row["parentId"] == "g-own"
    provider.check_access.assert_not_awaited()


async def test_a_group_that_is_parent_and_own_group_ranks_as_the_parent() -> None:
    """Listed twice by the query, it keeps its place among the parents."""
    row, = await _name(_provider({"b-group", "c-parent"}),
                       [_row(_own("b-group"), _option("b-group", "recordGroup"), _option("c-parent"))])
    assert row["parentId"] == "b-group"
