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
    assert out == {"accessible": ["a"], "accessibleUrls": []}


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


def _rows(*pairs: tuple[str, str]) -> list[dict]:
    return [{"id": i, "webUrl": u} for i, u in pairs]


@pytest.mark.asyncio
async def test_urls_map_to_records_and_are_readable_only_when_every_record_is() -> None:
    graph = AsyncMock()
    graph.get_records_by_weburls = AsyncMock(
        return_value=_rows(("r1", "u1"), ("r2", "u2"), ("r3", "u2"))
    )
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value={"r1", "r2"})) as check:
        out = await check_viewer_record_access(
            _request({"recordIds": ["a"], "webUrls": ["u1", "u2", "gone", "u1"]}), graph, VIEWER
        )
    graph.get_records_by_weburls.assert_awaited_once_with(["u1", "u2", "gone"], "org-1", per_address=50)
    check.assert_awaited_once_with(graph, {"a", "r1", "r2", "r3"}, "viewer", "org-1")
    # u2 has an unreadable record at its address, "gone" resolves to nothing.
    assert out == {"accessible": [], "accessibleUrls": ["u1"]}


@pytest.mark.asyncio
async def test_no_urls_means_no_url_lookup() -> None:
    graph = AsyncMock()
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value={"a"})):
        out = await check_viewer_record_access(_request({"recordIds": ["a"]}), graph, VIEWER)
    graph.get_records_by_weburls.assert_not_awaited()
    assert out == {"accessible": ["a"], "accessibleUrls": []}


@pytest.mark.asyncio
async def test_address_with_a_full_page_of_records_is_not_readable() -> None:
    graph = AsyncMock()
    graph.get_records_by_weburls = AsyncMock(
        return_value=_rows(*[(f"r{i}", "full") for i in range(50)], ("s1", "few"))
    )
    everything = {f"r{i}" for i in range(50)} | {"s1"}
    with patch("app.api.routes.chatbot.accessible_node_ids", AsyncMock(return_value=everything)):
        out = await check_viewer_record_access(_request({"webUrls": ["full", "few"]}), graph, VIEWER)
    assert out["accessibleUrls"] == ["few"]


@pytest.mark.asyncio
async def test_failed_url_lookup_is_a_503() -> None:
    graph = AsyncMock()
    graph.get_records_by_weburls = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(HTTPException) as err:
        await check_viewer_record_access(_request({"webUrls": ["u"]}), graph, VIEWER)
    assert err.value.status_code == 503
