"""The Confluence module setup must not trip over another run's test pages (no live services).

Setup snapshots the shared IT space, syncs, then checks every snapshotted page in the graph.
On PR #4228's run another run's update test bumped its own page from version 1 to 2 between
our snapshot and our sync, and setup failed with "Expected external_revision_id='1', got '2'",
erroring every Confluence mutation test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.models.entities import RecordType
from connectors.confluence.confluence_v1_test_utils import (
    assert_api_content_matches_graph,
    fetch_space_content_snapshot,
)
from helper.assertions import ConnectorAssertions

pytestmark = pytest.mark.unit

SPACE_ID = "98139"
PERMANENT = {"id": "1001", "title": "Page-1-DO-NOT-DELETE", "version": 3}
OTHER_RUNS = {"id": "2002", "title": "Integration Test Page Beta 29ce5c17", "version": 1}


class _Resp:
    status = 200

    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


class _Space:
    """The shared space as Confluence's v1 API lists it at snapshot time."""

    async def get_spaces(self, **_: Any) -> _Resp:
        return _Resp({"results": [{"id": SPACE_ID}]})

    async def get_pages_v1(self, *, page_ids: list[str] | None = None, **_: Any) -> _Resp:
        pages = [p for p in (PERMANENT, OTHER_RUNS) if page_ids is None or p["id"] in page_ids]
        return _Resp({
            "results": [{"id": p["id"], "title": p["title"], "version": {"number": p["version"]}}
                        for p in pages],
            "_links": {},
        })

    async def get_blogposts_v1(self, **_: Any) -> _Resp:
        return _Resp({"results": [], "_links": {}})

    async def get_folders_v1(self, **_: Any) -> _Resp:
        return _Resp({"results": [], "_links": {}})


class _Graph:
    """What our sync stored: the other run edited its page after our snapshot."""

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Any:
        page = next(p for p in (PERMANENT, OTHER_RUNS) if p["id"] == external_id)
        version = page["version"] + (1 if page is OTHER_RUNS else 0)
        return SimpleNamespace(
            external_record_id=page["id"], record_type=RecordType.CONFLUENCE_PAGE.value,
            record_name=page["title"], mime_type="text/html", parent_external_record_id=None,
            external_record_group_id=SPACE_ID, external_revision_id=str(version),
            is_dependent_node=False,
        )


async def test_another_runs_page_edited_mid_setup_does_not_fail_it() -> None:
    space, graph = _Space(), _Graph()
    snapshot = await fetch_space_content_snapshot(space, "ITSPACE")  # type: ignore[arg-type]

    await assert_api_content_matches_graph(
        snapshot, space, graph, ConnectorAssertions(graph),  # type: ignore[arg-type]
        "conn-1", phase="SETUP after initial sync",
    )
    assert [p.id for p in snapshot.pages] == [PERMANENT["id"]]
