"""A shared chat is rendered per viewer: Node asks which of the records it shows
the viewer (named in the service token) may read."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.chatbot import check_viewer_record_access
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError

VIEWER = {"orgId": "org-1", "userId": "viewer"}


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


@pytest.mark.asyncio
async def test_returns_only_records_the_token_viewer_may_read() -> None:
    graph = AsyncMock()
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value={"a"})) as check:
        out = await check_viewer_record_access(_request({"recordIds": ["a", "b", "a"]}), graph, VIEWER)
    check.assert_awaited_once_with(graph, {"a", "b"}, "viewer", "org-1")
    assert out == {"accessible": ["a"]}


@pytest.mark.asyncio
async def test_check_that_cannot_answer_is_a_503_not_an_empty_or_full_answer() -> None:
    with patch(
        "app.api.routes.chatbot.accessible_node_ids",
        AsyncMock(side_effect=PermissionVerificationUnavailableError("down")),
    ):
        with pytest.raises(HTTPException) as err:
            await check_viewer_record_access(_request({"recordIds": ["a"]}), AsyncMock(), VIEWER)
    assert err.value.status_code == 503


@pytest.mark.asyncio
async def test_token_without_viewer_is_rejected() -> None:
    with pytest.raises(HTTPException) as err:
        await check_viewer_record_access(_request({"recordIds": ["a"]}), AsyncMock(), {"orgId": "org-1"})
    assert err.value.status_code == 400
