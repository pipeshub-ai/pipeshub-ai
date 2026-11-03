"""Per-connector record groups come from the turn's containers, not a second permission query."""

from unittest.mock import AsyncMock

import pytest

from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.utils.pattern_match import (
    _accessible_groups_for_connector,
    _fetch_container_group_rows,
)

ROWS = [
    {"id": "rg1", "groupName": "Eng", "connectorId": "c1", "orgId": "o1"},
    {"id": "rg2", "groupName": "Other", "connectorId": "c2", "orgId": "o1"},
    {"_key": "rg3", "groupName": "Ops", "connectorId": "c1", "orgId": "o1"},
    {"id": "rg5", "groupName": "Foreign", "connectorId": "c1", "orgId": "o2"},
]

CONTAINERS = AccessibleContainers(
    record_group_ids_trusted=frozenset({"rg1", "rg2", "rg5"}),
    record_group_ids_verify=frozenset({"rg3", "rg4"}),
)


def test_filters_container_groups_to_the_connector_and_org():
    assert _accessible_groups_for_connector(CONTAINERS, ROWS, org_id="o1", connector_id="c1") == [
        {"id": "rg1", "group_name": "Eng"},
        {"id": "rg3", "group_name": "Ops"},
    ]
    assert _accessible_groups_for_connector(CONTAINERS, ROWS, org_id="o1", connector_id="c2") == [
        {"id": "rg2", "group_name": "Other"},
    ]


@pytest.mark.parametrize("bad_row", [
    {"id": "rg4", "groupName": "", "connectorId": "c1", "orgId": "o1"},
    {"id": "rg4", "groupName": None, "connectorId": "c1", "orgId": "o1"},
    {"groupName": "NoId", "connectorId": "c1", "orgId": "o1"},
])
def test_in_connector_group_without_id_or_name_means_do_not_scope(bad_row):
    assert _accessible_groups_for_connector(
        CONTAINERS, [*ROWS, bad_row], org_id="o1", connector_id="c1",
    ) == []


def test_unusable_containers_mean_do_not_scope():
    containers = AccessibleContainers(fallback_reason="membership_not_backfilled:c1")
    assert _accessible_groups_for_connector(containers, ROWS, org_id="o1", connector_id="c1") == []


def test_root_scoped_connector_is_not_scoped():
    containers = AccessibleContainers(
        record_group_ids_trusted=frozenset({"rg1"}), root_group_ids=frozenset({"rg1"}),
    )
    assert _accessible_groups_for_connector(containers, ROWS[:1], org_id="o1", connector_id="c1") == []


def test_row_outside_the_containers_is_never_scoped_to():
    containers = AccessibleContainers(record_group_ids_trusted=frozenset({"rg1"}))
    assert _accessible_groups_for_connector(containers, ROWS, org_id="o1", connector_id="c1") == [
        {"id": "rg1", "group_name": "Eng"},
    ]


@pytest.mark.asyncio
async def test_group_rows_fetched_by_every_reached_group_id():
    provider = AsyncMock()
    provider.get_nodes_by_field_in = AsyncMock(return_value=ROWS)
    assert await _fetch_container_group_rows(provider, CONTAINERS) == ROWS
    provider.get_nodes_by_field_in.assert_awaited_once_with(
        "recordGroups", "id", ["rg1", "rg2", "rg3", "rg4", "rg5"],
        ["id", "groupName", "connectorId", "orgId"],
    )


@pytest.mark.asyncio
async def test_no_reached_groups_skips_the_lookup():
    provider = AsyncMock()
    assert await _fetch_container_group_rows(provider, AccessibleContainers()) == []
    provider.get_nodes_by_field_in.assert_not_awaited()
