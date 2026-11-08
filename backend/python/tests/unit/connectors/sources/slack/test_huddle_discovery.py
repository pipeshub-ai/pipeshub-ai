from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.connectors.sources.slack.common.huddle import list_huddle_canvases


def source(*responses) -> tuple:
    ds = SimpleNamespace(files_list=AsyncMock(side_effect=responses))
    connector = SimpleNamespace(
        rate_limiter=SimpleNamespace(acquire=AsyncMock()),
        _fresh_datasource=AsyncMock(return_value=ds), display_name="Slack",
    )
    return connector, ds


def page(n, pages, files) -> SimpleNamespace:
    return SimpleNamespace(success=True, data={"paging": {"page": n, "pages": pages}, "files": files})


@pytest.mark.asyncio
async def test_lists_all_pages_and_only_huddle_canvases() -> None:
    connector, ds = source(
        page(1, 2, [{"id": "F1", "is_huddle_canvas": True}, {"id": "F2"}]),
        page(2, 2, [{"id": "F3", "is_huddle_canvas": True}]),
    )
    files = await list_huddle_canvases(connector, "C1")
    assert [f["id"] for f in files] == ["F1", "F3"]
    assert [c.kwargs["page"] for c in ds.files_list.await_args_list] == [1, 2]
    assert all(c.kwargs["channel"] == "C1" for c in ds.files_list.await_args_list)


@pytest.mark.asyncio
async def test_failed_later_page_never_returns_a_partial_listing() -> None:
    connector, _ = source(
        page(1, 2, [{"id": "F1", "is_huddle_canvas": True}]),
        SimpleNamespace(success=False, error="ratelimited", retry_after="5"),
    )
    with pytest.raises(HTTPException) as exc:
        await list_huddle_canvases(connector, "C1")
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_missing_pagination_is_not_an_empty_listing() -> None:
    connector, _ = source(SimpleNamespace(success=True, data={"files": []}))
    with pytest.raises(ValueError):
        await list_huddle_canvases(connector, "C1")
