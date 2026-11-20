"""One access request per reader: the route Node calls for every read of a chat answers for every record
id the read needs (up to 10,000), resolving the reader and their grants once and checking the ids a
chunk at a time. It says which ids are deleted apart from the ones the reader may not open, and until
when its answer holds. A reader whose account was removed or switched off is refused, and a chunk that
cannot be checked fails the whole answer."""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.routes import chatbot
from app.api.routes.chatbot import RecordAccessRequest, check_viewer_record_access
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.interface.graph_db_provider import AccessCheck

VIEWER = {"orgId": "org-1", "userId": "viewer"}


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


class Graph:
    """A graph that knows who the reader is, what they may open and which records exist."""

    def __init__(self, user: dict[str, Any] | None, opens: set[str], records: dict[str, bool] | None = None,
                 groups: set[str] = frozenset(), apps: set[str] = frozenset()) -> None:
        self.user = user
        self.opens = opens
        self.records = records or {}
        self.groups, self.apps = set(groups), set(apps)
        self.user_reads = 0
        self.grant_reads = 0
        self.chunks: list[int] = []
        self.in_flight = 0
        self.most = 0
        self.fail_on_chunk: int | None = None

    async def get_user_by_user_id(self, *_a: Any, **_k: Any) -> dict[str, Any] | None:
        self.user_reads += 1
        return self.user

    async def get_knowledge_hub_access_v3(self, user_key: str, org_id: str, **_k: Any) -> dict[str, Any]:
        self.grant_reads += 1
        return {"grantee_ids": [user_key]}

    async def check_access(self, user_key: str, org_id: str, *, node_ids: list[str], access: dict[str, Any] | None = None,
                           **_k: Any) -> AccessCheck:
        assert access == {"grantee_ids": [user_key]}, "every chunk reuses the grants read once"
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        self.chunks.append(len(node_ids))
        index = len(self.chunks)
        try:
            await asyncio.sleep(0.01)
            if self.fail_on_chunk is not None and index == self.fail_on_chunk:
                raise PermissionVerificationUnavailableError("statement timed out")
            ok = frozenset(i for i in node_ids if i in self.opens)
            return AccessCheck(node_ids=ok, names={i: f"name {i}" for i in ok})
        finally:
            self.in_flight -= 1

    async def get_nodes_by_field_in(self, collection: str, field: str, values: list[str], **_k: Any) -> list[dict]:
        assert field == "id"
        if collection == "records":
            return [{"id": v, "isDeleted": self.records[v]} for v in values if v in self.records]
        pool = self.groups if collection == "recordGroups" else self.apps
        return [{"id": v} for v in values if v in pool]

    async def get_records_by_weburls(self, *_a: Any, **_k: Any) -> list[dict]:
        return []


LIVE = {"_key": "u-key", "userId": "viewer", "orgId": "org-1", "isActive": True}


@pytest.mark.asyncio
async def test_one_request_carries_up_to_ten_thousand_ids() -> None:
    assert len(RecordAccessRequest(recordIds=[f"r{i}" for i in range(10_000)]).recordIds) == 10_000
    with pytest.raises(ValueError):
        RecordAccessRequest(recordIds=[f"r{i}" for i in range(10_001)])


@pytest.mark.asyncio
async def test_the_reader_and_grants_are_read_once_and_the_ids_checked_four_chunks_at_a_time() -> None:
    ids = [f"r{i}" for i in range(4_100)]
    graph = Graph(LIVE, opens=set(ids[:10]))
    out = await check_viewer_record_access(_request({"recordIds": ids}), graph, VIEWER)
    assert graph.user_reads == 1
    assert graph.grant_reads == 1
    assert sorted(graph.chunks) == [100] + [500] * 8
    assert graph.most == 4
    assert out["accessible"] == ids[:10]


@pytest.mark.asyncio
async def test_a_chunk_that_cannot_be_checked_fails_the_whole_answer() -> None:
    graph = Graph(LIVE, opens={"r1"})
    graph.fail_on_chunk = 2
    with pytest.raises(HTTPException) as err:
        await check_viewer_record_access(_request({"recordIds": [f"r{i}" for i in range(1_200)]}), graph, VIEWER)
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_deleted_ids_are_said_apart_from_the_ones_the_reader_may_not_open() -> None:
    graph = Graph(
        LIVE,
        opens={"open"},
        records={"open": False, "forbidden": False, "deleted": True},
        groups={"folder"},
    )
    out = await check_viewer_record_access(
        _request({"recordIds": ["open", "forbidden", "deleted", "gone", "folder"]}), graph, VIEWER
    )
    assert out["accessible"] == ["open"]
    # A record marked deleted, and an id that names nothing at all; never a forbidden record or a folder.
    assert out["deleted"] == ["deleted", "gone"]


@pytest.mark.asyncio
async def test_a_lookup_of_deleted_records_that_fails_excuses_none() -> None:
    graph = Graph(LIVE, opens=set())
    graph.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    out = await check_viewer_record_access(_request({"recordIds": ["x"]}), graph, VIEWER)
    assert out["deleted"] == []


@pytest.mark.asyncio
async def test_the_answer_says_until_when_it_holds() -> None:
    before = int(time.time() * 1000)
    out = await check_viewer_record_access(_request({"recordIds": ["r1"]}), Graph(LIVE, opens={"r1"}), VIEWER)
    assert before + 25_000 <= out["validUntil"] <= int(time.time() * 1000) + chatbot.RECORD_ACCESS_VALID_MS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user",
    [
        {**LIVE, "isActive": False},
        {**LIVE, "isDisabled": True},
    ],
    ids=["removed", "switched off"],
)
async def test_a_reader_whose_account_is_not_active_is_refused(user: dict[str, Any]) -> None:
    graph = Graph(user, opens={"r1"})
    with pytest.raises(HTTPException) as err:
        await check_viewer_record_access(_request({"recordIds": ["r1"]}), graph, VIEWER)
    assert err.value.status_code == 403
    assert graph.chunks == []


@pytest.mark.asyncio
async def test_an_account_written_before_those_fields_is_read_as_live() -> None:
    user = {"_key": "u-key", "userId": "viewer", "orgId": "org-1"}
    out = await check_viewer_record_access(_request({"recordIds": ["r1"]}), Graph(user, opens={"r1"}), VIEWER)
    assert out["accessible"] == ["r1"]


@pytest.mark.asyncio
async def test_a_grants_read_that_fails_is_a_503() -> None:
    graph = Graph(LIVE, opens={"r1"})
    graph.get_knowledge_hub_access_v3 = AsyncMock(side_effect=RuntimeError("down"))  # type: ignore[method-assign]
    with pytest.raises(HTTPException) as err:
        await check_viewer_record_access(_request({"recordIds": ["r1"]}), graph, VIEWER)
    assert err.value.status_code == 503
