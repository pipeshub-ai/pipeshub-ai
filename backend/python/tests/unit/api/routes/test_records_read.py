"""The budgeted record-content route: who may read, and what reaches the reader.

The reading itself is covered in `tests/unit/utils/test_record_reader.py`.
These pin the gate in front of it -- the same access check as the connector
service's content route -- and that each mode reads what it says it reads.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import records as route
from app.models.blocks import BlockType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

USER = {"userId": "u-1", "orgId": "o-1"}


def _record(record_id: str, *texts: str, created: int = 0) -> dict:
    return {
        "id": record_id,
        "virtual_record_id": f"v-{record_id}",
        "context_metadata": f"Record ID: {record_id}",
        "source_created_at": created,
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value, "parent_index": None, "data": t}
                for i, t in enumerate(texts)
            ],
            "block_groups": [],
        },
    }


def _request(*, access: bool = True) -> tuple[SimpleNamespace, MagicMock, MagicMock]:
    graph = MagicMock()
    graph.get_user_by_user_id = AsyncMock(return_value={"id": "k-1"})
    graph.check_access = AsyncMock(
        side_effect=lambda _user, _org, node_ids, **_kw: SimpleNamespace(
            node_ids=set(node_ids) if access else set()
        )
    )
    graph.get_document = AsyncMock(return_value={"connectorId": "c-1"})
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value={"searchResults": []})
    container = SimpleNamespace(
        logger=lambda: MagicMock(),
        config_service=lambda: MagicMock(),
        graph_provider=AsyncMock(return_value=graph),
        blob_store=AsyncMock(return_value=MagicMock()),
        retrieval_service=AsyncMock(return_value=retrieval),
    )
    request = SimpleNamespace(app=SimpleNamespace(container=container), state=SimpleNamespace(user=USER))
    return request, graph, retrieval


async def _read(request: SimpleNamespace, record_id: str = "r-1", **params: object) -> dict:
    defaults = {
        "mode": "record", "query": None, "start_block": None, "start_record": 0, "max_chars": 40_000, "extract": False,
    }
    return await route.read_record_content(record_id, request, **{**defaults, **params})


class _no_scope:
    def __init__(self, *_args: object, **_kw: object) -> None:
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> None:
        return None


@pytest.fixture(autouse=True)
def _demo_data_visible() -> Iterator[None]:
    with patch.object(route, "is_hidden_demo_record", AsyncMock(return_value=False)):
        yield


class TestAccess:
    async def test_no_access_is_a_403_and_nothing_is_read(self) -> None:
        request, _, _ = _request(access=False)
        fetch = AsyncMock()
        with patch.object(route, "_fetch_multiple_records_impl", fetch), pytest.raises(HTTPException) as err:
            await _read(request)
        assert err.value.status_code == 403
        fetch.assert_not_awaited()

    async def test_one_access_check_is_made_and_the_fetch_reuses_it(self) -> None:
        request, graph, _ = _request()
        fetch = AsyncMock(return_value={"ok": False})
        with patch.object(route, "_fetch_multiple_records_impl", fetch):
            await _read(request)
        graph.check_access.assert_awaited_once()
        assert fetch.await_args.kwargs["accessible"] == {"r-1"}

    async def test_no_access_to_the_thread_record_is_a_403_too(self) -> None:
        request, _, _ = _request(access=False)
        thread = AsyncMock()
        with patch.object(route, "_fetch_thread_records_impl", thread), pytest.raises(HTTPException) as err:
            await _read(request, mode="thread")
        assert err.value.status_code == 403
        thread.assert_not_awaited()

    async def test_switched_off_demo_data_is_a_404(self) -> None:
        request, _, _ = _request()
        with patch.object(route, "is_hidden_demo_record", AsyncMock(return_value=True)), \
                pytest.raises(HTTPException) as err:
            await _read(request)
        assert err.value.status_code == 404


class TestRecordMode:
    async def test_the_read_is_bounded_and_says_how_to_continue(self) -> None:
        request, _, _ = _request()
        record = _record("r-1", *[f"block {i} " + "x" * 400 for i in range(200)])
        fetch = AsyncMock(return_value={"ok": True, "records": [record]})

        with patch.object(route, "_fetch_multiple_records_impl", fetch):
            body = await _read(request, max_chars=5_000)

        assert fetch.await_args.kwargs["user_id"] == "u-1"
        assert body["chars"] <= 5_000
        assert body["complete"] is False
        assert body["nextStartBlock"] > 0

    async def test_a_query_ranks_the_blocks_of_this_record_only(self) -> None:
        request, _, retrieval = _request()
        record = _record("r-1", *[f"block {i} " + "x" * 400 for i in range(200)])
        retrieval.search_with_filters.return_value = {
            "searchResults": [{"metadata": {"virtualRecordId": "v-r-1", "blockIndex": 150}}],
        }

        with patch.object(route, "_fetch_multiple_records_impl",
                          AsyncMock(return_value={"ok": True, "records": [record]})):
            body = await _read(request, query="what changed in block 150", max_chars=5_000)

        assert body["order"] == "relevance"
        assert "block 150 " in body["content"]
        assert retrieval.search_with_filters.await_args.kwargs["virtual_record_ids_from_tool"] == ["v-r-1"]

    async def test_extract_hands_the_record_and_query_to_the_reader(self) -> None:
        request, _, _ = _request()
        record = _record("r-1", "The ASF had 65 sponsors.")
        extract = AsyncMock(return_value={"content": "65 sponsors", "order": "extract"})

        with patch.object(route, "_fetch_multiple_records_impl",
                          AsyncMock(return_value={"ok": True, "records": [record]})), \
                patch.object(route, "extract_record", extract), \
                patch.object(route, "call_scope", _no_scope):
            body = await _read(request, query=" sponsors ", extract=True, max_chars=5_000)

        assert body == {"content": "65 sponsors", "order": "extract"}
        assert extract.await_args.args[0] is record
        assert extract.await_args.kwargs["query"] == "sponsors"
        assert extract.await_args.kwargs["fallback_chars"] == 5_000
        assert extract.await_args.kwargs["user_id"] == "u-1"

    async def test_the_reader_runs_on_the_users_identity(self) -> None:
        request, _, _ = _request()
        record = _record("r-1", "The ASF had 65 sponsors.")
        scopes: list[tuple] = []

        class _Scope:
            def __init__(self, principal: object, workload: object, **_kw: object) -> None:
                scopes.append((principal, workload))

            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *_exc: object) -> None:
                return None

        with patch.object(route, "_fetch_multiple_records_impl",
                          AsyncMock(return_value={"ok": True, "records": [record]})), \
                patch.object(route, "extract_record", AsyncMock(return_value={"order": "extract"})), \
                patch.object(route, "principal_from_request_user", lambda user: ("principal", user["userId"])), \
                patch.object(route, "call_scope", _Scope):
            await _read(request, query="sponsors", extract=True)

        assert scopes == [(("principal", "u-1"), route.Workload.SEARCH)]

    async def test_a_refusal_inside_the_extract_keeps_its_status(self) -> None:
        request, _, _ = _request()
        record = _record("r-1", "text")
        with patch.object(route, "_fetch_multiple_records_impl",
                          AsyncMock(return_value={"ok": True, "records": [record]})), \
                patch.object(route, "extract_record",
                             AsyncMock(side_effect=HTTPException(status_code=403, detail="no identity"))), \
                patch.object(route, "call_scope", _no_scope), \
                pytest.raises(HTTPException) as err:
            await _read(request, query="sponsors", extract=True)
        assert err.value.status_code == 403

    @pytest.mark.parametrize("params", [
        {"query": None},
        {"query": "  "},
        {"query": "sponsors", "start_block": 3},
        {"query": "sponsors", "mode": "thread"},
    ])
    async def test_extract_without_one_record_and_a_query_is_a_422(self, params: dict) -> None:
        request, graph, _ = _request()
        with pytest.raises(HTTPException) as err:
            await _read(request, extract=True, **params)
        assert err.value.status_code == 422
        graph.check_access.assert_not_awaited()

    async def test_a_record_without_indexed_content_is_described(self) -> None:
        request, _, _ = _request()
        fetch = AsyncMock(return_value={
            "ok": False,
            "not_indexed_records": {"r-1": {"status": "QUEUED", "context": "Record ID: r-1"}},
        })

        with patch.object(route, "_fetch_multiple_records_impl", fetch):
            body = await _read(request)

        assert body["content"].startswith("Record ID: r-1")
        assert body["complete"] is True


class TestThreadMode:
    async def test_every_reply_the_caller_can_see_comes_back_in_order(self) -> None:
        request, _, _ = _request()
        records = [
            _record("t-0", "parent: deploy failing", created=1),
            _record("t-1", "reply: cache", "reply: fixed", created=2),
        ]
        thread = AsyncMock(return_value={"ok": True, "records": records, "skipped_record_ids": ["t-9"]})

        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value={"record_group_id": "g"})), \
                patch.object(route, "_fetch_thread_records_impl", thread):
            body = await _read(request, mode="thread")

        # The listing is the permission-checked one, run as the caller.
        assert thread.await_args.kwargs["user_id"] == "u-1"
        assert thread.await_args.kwargs["record_id"] == "r-1"
        assert body["mode"] == "thread"
        assert body["content"].index("deploy failing") < body["content"].index("reply: fixed")
        assert body["complete"] is True
        assert body["unreadableRecordIds"] == ["t-9"]

    async def test_the_thread_cursor_reaches_the_reader(self) -> None:
        """`start_block` applies to the record at `start_record`."""
        request, _, _ = _request()
        records = [
            _record("t-0", "parent", created=1),
            _record("t-1", *[f"reply {i}" for i in range(6)], created=2),
        ]
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value={"record_group_id": "g"})), \
                patch.object(route, "_fetch_thread_records_impl",
                             AsyncMock(return_value={"ok": True, "records": records})):
            body = await _read(request, mode="thread", start_record=1, start_block=4)

        assert body["startRecord"] == 1 and body["startBlock"] == 4
        assert "reply 4" in body["content"] and "reply 3" not in body["content"]
        assert "parent" not in body["content"]

    async def test_a_thread_lookup_that_fails_is_a_500_not_a_claim_about_the_record(self) -> None:
        request, _, _ = _request()
        resolve = AsyncMock(side_effect=RuntimeError("graph down"))
        with patch.object(route, "_resolve_thread_record_group", resolve), \
                pytest.raises(HTTPException) as err:
            await _read(request, mode="thread")
        assert err.value.status_code == 500
        assert resolve.await_args.kwargs["raise_on_error"] is True

    async def test_a_record_not_from_slack_is_a_422_that_says_why(self) -> None:
        request, _, _ = _request()
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value=None)), \
                patch.object(route, "_resolve_slack_channel_record", AsyncMock(return_value=None)), \
                pytest.raises(HTTPException) as err:
            await _read(request, mode="thread")
        assert err.value.status_code == 422
        assert "not part of a Slack thread" in err.value.detail
        assert "Slack records only" in err.value.detail

    async def test_a_failed_channel_lookup_is_a_500(self) -> None:
        request, _, _ = _request()
        channel = AsyncMock(side_effect=RuntimeError("graph down"))
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value=None)), \
                patch.object(route, "_resolve_slack_channel_record", channel), \
                pytest.raises(HTTPException) as err:
            await _read(request, mode="thread")
        assert err.value.status_code == 500
        assert channel.await_args.kwargs["raise_on_error"] is True

    async def test_a_channel_record_reads_with_its_threads_and_says_so_first(self) -> None:
        """A run of channel messages reads as itself, then its threads' replies,
        with the note leading the first page and inside the budget."""
        request, _, _ = _request()
        records = [
            _record("burst", "parent: deploy failing\n\nReply Count: 2", created=1),
            _record("t-1", "reply: cache", "reply: fixed", created=2),
        ]
        note = "This record holds 3 Slack messages; 1 of them started a thread."
        thread = AsyncMock(return_value={
            "ok": True, "records": records, "skipped_record_ids": [], "note": note,
            "threads": [{"thread_record_group_id": "g-1", "record_count": 1}], "threads_not_read": 0,
        })
        channel = {"connector_id": "c", "org_id": "o-1", "channel_id": "C1", "is_message": True, "timestamps": []}
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value=None)), \
                patch.object(route, "_resolve_slack_channel_record", AsyncMock(return_value=channel)), \
                patch.object(route, "_fetch_thread_records_impl", thread):
            body = await _read(request, record_id="burst", mode="thread", max_chars=2_000)

        assert body["mode"] == "thread"
        assert body["content"].startswith(f"[{note}]")
        assert body["content"].index("deploy failing") < body["content"].index("reply: fixed")
        assert body["chars"] <= 2_000 and body["maxChars"] == 2_000
        assert body["threads"] == [{"threadRecordGroupId": "g-1", "recordCount": 1}]
        assert body["threadsNotRead"] == 0

    async def test_the_note_is_not_repeated_on_later_pages(self) -> None:
        request, _, _ = _request()
        records = [_record("burst", "parent", created=1), _record("t-1", "reply", created=2)]
        thread = AsyncMock(return_value={"ok": True, "records": records, "note": "Note.", "threads": []})
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value=None)), \
                patch.object(route, "_resolve_slack_channel_record", AsyncMock(return_value={"org_id": "o-1"})), \
                patch.object(route, "_fetch_thread_records_impl", thread):
            body = await _read(request, mode="thread", start_record=1)
        assert "Note." not in body["content"]
        assert "reply" in body["content"]

    async def test_a_message_without_replies_reads_as_itself(self) -> None:
        request, _, _ = _request()
        records = [_record("msg", "just one message", created=1)]
        thread = AsyncMock(return_value={
            "ok": True, "records": records, "note": "No replies: returned on its own.", "threads": [],
        })
        with patch.object(route, "_resolve_thread_record_group", AsyncMock(return_value=None)), \
                patch.object(route, "_resolve_slack_channel_record", AsyncMock(return_value={"org_id": "o-1"})), \
                patch.object(route, "_fetch_thread_records_impl", thread):
            body = await _read(request, record_id="msg", mode="thread")
        assert "just one message" in body["content"]
        assert body["content"].startswith("[No replies")
        assert body["threadRecordCount"] == 1 and body["complete"] is True
        assert body["threads"] == [] and body["threadsNotRead"] == 0


class TestOverHttp:
    """Parameter names and bounds as the API gateway sends them."""

    @staticmethod
    def _client(user: dict, container: SimpleNamespace) -> "TestClient":  # noqa: F821
        from fastapi import FastAPI, Request, Response
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.container = container

        @app.middleware("http")
        async def _user(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
            request.state.user = user
            return await call_next(request)

        app.include_router(route.router, prefix="/api/v1")
        return TestClient(app)

    def test_start_block_and_max_chars_reach_the_reader(self) -> None:
        request, _, _ = _request()
        record = _record("r-1", *[f"block {i} " + "x" * 400 for i in range(50)])
        client = self._client(USER, request.app.container)
        with patch.object(route, "_fetch_multiple_records_impl",
                          AsyncMock(return_value={"ok": True, "records": [record]})):
            body = client.get("/api/v1/records/r-1/read?start_block=10&max_chars=3000").json()

        assert body["startBlock"] == 10
        assert body["maxChars"] == 3_000
        assert body["chars"] <= 3_000
        assert "block 10 " in body["content"] and "block 9 " not in body["content"]

    def test_a_budget_outside_the_bounds_is_refused(self) -> None:
        request, _, _ = _request()
        client = self._client(USER, request.app.container)
        assert client.get("/api/v1/records/r-1/read?max_chars=100").status_code == 422
        assert client.get("/api/v1/records/r-1/read?max_chars=1000000").status_code == 422
        assert client.get("/api/v1/records/r-1/read?mode=channel").status_code == 422

    def test_a_token_without_connector_read_is_refused(self) -> None:
        request, _, _ = _request()
        user = {**USER, "isOAuth": True, "oauthScopes": ["semantic:write"]}
        client = self._client(user, request.app.container)
        assert client.get("/api/v1/records/r-1/read").status_code == 403
