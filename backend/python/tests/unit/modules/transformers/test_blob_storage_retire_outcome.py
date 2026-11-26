"""A missing document and a missing retire route are told apart by the 404's body."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture(autouse=True)
def _route_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(bs_mod._RETIRE_ROUTE, "down_until", 0.0)


def _blob(monkeypatch: pytest.MonkeyPatch, status: int, body: Any) -> BlobStorage:  # noqa: ANN401
    b = BlobStorage(logger=MagicMock(spec=logging.Logger), config_service=MagicMock(), graph_provider=MagicMock())
    b._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "local"))
    response = MagicMock()
    response.status = status
    if isinstance(body, Exception):
        response.json = AsyncMock(side_effect=body)
    else:
        response.json = AsyncMock(return_value=body)

    @asynccontextmanager
    async def post(*a: Any, **k: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        yield response

    monkeypatch.setattr(bs_mod, "get_shared_session", lambda: MagicMock(post=post))
    return b


async def test_a_missing_document_is_gone_and_leaves_retirement_on(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _blob(monkeypatch, 404, {"error": {"code": "HTTP_NOT_FOUND", "message": "Document not found"}})
    assert await b._retire_document_outcome("org", "doc") == "gone"
    assert bs_mod._RETIRE_ROUTE["down_until"] == 0.0


@pytest.mark.parametrize("body", [{"message": "Cannot POST /x"}, ValueError("<html>Cannot POST</html>"), {"error": {"code": "OTHER"}}])
async def test_a_missing_route_turns_retirement_off_for_the_retry_interval(monkeypatch: pytest.MonkeyPatch, body: Any) -> None:  # noqa: ANN401
    b = _blob(monkeypatch, 404, body)
    assert await b._retire_document_outcome("org", "doc") == "unavailable"
    assert bs_mod._RETIRE_ROUTE["down_until"] > 0.0
