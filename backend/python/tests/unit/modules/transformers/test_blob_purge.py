"""E7 (PA-7) on main's purge route: `BlobStorage.purge_document` erases a document for good, idempotently."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import BlobStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _Response:
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self._body = body

    async def json(self) -> dict[str, Any]:
        return self._body

    async def text(self) -> str:
        return str(self._body)

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str]]] = []

    def delete(self, url: str, *, headers: dict[str, str]) -> _Response:
        self.calls.append((url, headers))
        return self.response


def _blob(session: _Session) -> tuple[BlobStorage, Any]:
    blob = BlobStorage(logger=MagicMock(), config_service=MagicMock(), graph_provider=MagicMock())
    blob._get_auth_and_config = AsyncMock(  # type: ignore[method-assign]
        return_value=({"Authorization": "Bearer storage-token"}, "http://node:3000", "s3")
    )

    @asynccontextmanager
    async def borrowed() -> AsyncIterator[_Session]:
        yield session

    return blob, borrowed


@pytest.mark.parametrize(("body", "purged"), [({"purged": 1}, 1), ({"purged": 0}, 0)])
async def test_purge_reports_how_many_this_call_removed(body: dict[str, Any], purged: int) -> None:
    session = _Session(_Response(200, body))
    blob, borrowed = _blob(session)
    with patch.object(bs_mod, "_borrowed_session", borrowed):
        assert await blob.purge_document("org-1", "doc-9") == purged
    url, headers = session.calls[0]
    assert url == "http://node:3000/api/v1/document/internal/doc-9/purge"
    assert headers == {"Authorization": "Bearer storage-token"}
    blob._get_auth_and_config.assert_awaited_once_with("org-1")


async def test_any_other_answer_raises_so_the_caller_keeps_its_records() -> None:
    session = _Session(_Response(503, {}))
    blob, borrowed = _blob(session)
    with patch.object(bs_mod, "_borrowed_session", borrowed), pytest.raises(aiohttp.ClientError):
        await blob.purge_document("org-1", "doc-9")
