"""The reader's own request came with a token limited to some sources: what lies outside them is unreadable for that
read, as for every other read by that token ("search, chat, agent chat and every record read by id")."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.chatbot import check_viewer_record_access
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.selection_scope import SelectionTooLargeError

VIEWER = {"orgId": "org-1", "userId": "viewer"}
LIMIT = {"apps": [], "recordGroups": [], "records": ["q"]}


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


def _graph() -> AsyncMock:
    graph = AsyncMock()
    graph.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "r", "isDeleted": False}, {"id": "q", "isDeleted": False}])
    graph.get_records_by_weburls = AsyncMock(return_value=[{"id": "r", "webUrl": "https://deck.example.test/r"}])
    return graph


@pytest.mark.asyncio
async def test_a_record_outside_the_tokens_limit_is_unreadable_and_so_is_its_address() -> None:
    readable = AsyncMock(return_value={"r": "Board deck", "q": "Handbook"})
    outside = AsyncMock(return_value={"r"})
    with patch("app.api.routes.chatbot.readable_for_reader", readable), patch("app.api.routes.chatbot.outside_connection", outside, create=True):
        out = await check_viewer_record_access(
            _request({"recordIds": ["r", "q"], "webUrls": ["https://deck.example.test/r"], "knowledgeLimit": LIMIT}), _graph(), VIEWER,
        )
    outside.assert_awaited_once()
    args = outside.await_args.args
    assert args[1:3] == ("viewer", "org-1") and sorted(args[3]) == ["q", "r"] and args[4] == LIMIT
    assert out["accessible"] == ["q"] and out["names"] == {"q": "Handbook"}
    assert out["accessibleUrls"] == []
    assert out["deleted"] == [], "a record outside the limit is not gone: it is not excused for its asker"


@pytest.mark.asyncio
async def test_a_read_with_no_limit_asks_nothing_more() -> None:
    outside = AsyncMock(return_value=set())
    with patch("app.api.routes.chatbot.readable_for_reader", AsyncMock(return_value={"r": "Board deck"})), \
            patch("app.api.routes.chatbot.outside_connection", outside, create=True):
        out = await check_viewer_record_access(_request({"recordIds": ["r"]}), _graph(), VIEWER)
    outside.assert_not_awaited()
    assert out["accessible"] == ["r"]


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [SelectionTooLargeError("too large"), PermissionVerificationUnavailableError("down")])
async def test_a_limit_that_cannot_be_resolved_is_a_503_never_a_full_answer(error: Exception) -> None:
    with patch("app.api.routes.chatbot.readable_for_reader", AsyncMock(return_value={"r": "Board deck"})), \
            patch("app.api.routes.chatbot.outside_connection", AsyncMock(side_effect=error)):
        with pytest.raises(HTTPException) as err:
            await check_viewer_record_access(_request({"recordIds": ["r"], "knowledgeLimit": LIMIT}), _graph(), VIEWER)
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_a_limit_that_names_nothing_reaches_nothing() -> None:
    outside = AsyncMock(return_value=set())
    with patch("app.api.routes.chatbot.readable_for_reader", AsyncMock(return_value={"r": "Board deck"})), \
            patch("app.api.routes.chatbot.outside_connection", outside, create=True):
        out = await check_viewer_record_access(
            _request({"recordIds": ["r"], "knowledgeLimit": {"apps": [], "recordGroups": [], "records": []}}), _graph(), VIEWER,
        )
    assert out["accessible"] == []
