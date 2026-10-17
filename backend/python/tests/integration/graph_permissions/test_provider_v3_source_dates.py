"""The knowledge hub shows when a connector item was written at the source, not
when it was synced, and sorts and filters on that date. A collection item shows
when it was uploaded."""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import (
    CONFLUENCE,
    GROUP_G,
    ORG,
    USER_U,
    USER_V,
    _principals,
    app,
    bt,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from .loaders import arango_shape, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

APP = "sd-app"
DAY = 86_400_000
SYNCED = 1_780_000_000_000
PAGE = {"record_type": "CONFLUENCE_PAGE", "connector_id": APP}


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    pages = [
        # Written at the source long before the sync, in this order: old, middle, new.
        rec("sd-old", "Old", sourceCreatedAtTimestamp=SYNCED - 300 * DAY,
            sourceLastModifiedTimestamp=SYNCED - 200 * DAY, updatedAtTimestamp=SYNCED + 3, **PAGE),
        rec("sd-mid", "Middle", sourceCreatedAtTimestamp=SYNCED - 100 * DAY,
            sourceLastModifiedTimestamp=SYNCED - 50 * DAY, updatedAtTimestamp=SYNCED + 2, **PAGE),
        rec("sd-new", "New", sourceCreatedAtTimestamp=SYNCED - 10 * DAY,
            sourceLastModifiedTimestamp=SYNCED - 5 * DAY, updatedAtTimestamp=SYNCED + 1, **PAGE),
        rec("sd-nosrc", "No source date", updatedAtTimestamp=SYNCED, **PAGE),
    ]
    for page in pages:
        page["props"]["createdAtTimestamp"] = SYNCED
    nodes = [
        *principal_nodes,
        app(APP, "Confluence", connector=CONFLUENCE, app_group="Atlassian"),
        rg("sd-space", "Space", group_type="CONFLUENCE_SPACES", connector=CONFLUENCE, connectorId=APP),
        *pages,
    ]
    edges = [
        *principal_edges,
        user_app(USER_U, APP), user_app(USER_V, APP),
        nr(APP, "sd-space"), ip("sd-space", APP), perm(GROUP_G, "sd-space", grant_type="GROUP"),
        *[e for p in pages for e in (nr("sd-space", p["id"]), ip(p["id"], "sd-space"), bt(p["id"], "sd-space"))],
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def dates_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _rows(provider: Any, **overrides: Any) -> list[dict]:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    kwargs = {
        "app_id": APP, "org_id": ORG,
        "grantee_ids": access["grantee_ids"], "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 100, "flatten": False, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True, "start_id": "sd-space", "start_type": "recordGroup",
    }
    kwargs.update(overrides)
    return (await provider.get_knowledge_hub_connector_page_v3(**kwargs))["rows"]


async def test_a_row_carries_the_source_dates(dates_graph, provider) -> None:
    rows = {row["id"]: row for row in await _rows(provider)}

    assert rows["sd-old"]["createdAt"] == SYNCED - 300 * DAY
    assert rows["sd-old"]["updatedAt"] == SYNCED - 200 * DAY
    assert rows["sd-nosrc"]["createdAt"] == SYNCED


async def test_a_date_sort_follows_the_source(dates_graph, provider) -> None:
    rows = await _rows(provider, sort_field="updatedAt", sort_dir="DESC")

    assert [row["id"] for row in rows] == ["sd-nosrc", "sd-new", "sd-mid", "sd-old"]


async def test_a_date_window_reads_the_source(dates_graph, provider) -> None:
    window = {"gte": SYNCED - 150 * DAY, "lte": SYNCED - 1}
    rows = await _rows(provider, filters={"created_at": window})

    assert {row["id"] for row in rows} == {"sd-mid", "sd-new"}


async def test_a_page_boundary_resumes_on_the_source_date(dates_graph, provider) -> None:
    first = await _rows(provider, sort_field="createdAt", sort_dir="ASC", limit=2)
    last = first[-1]
    rest = await _rows(provider, sort_field="createdAt", sort_dir="ASC", limit=2, include_total=False,
                       after={"id": last["id"], "sortKey": last["sortKey"], "nullRank": last["nullRank"]})

    assert [row["id"] for row in first + rest] == ["sd-old", "sd-mid", "sd-new", "sd-nosrc"]
