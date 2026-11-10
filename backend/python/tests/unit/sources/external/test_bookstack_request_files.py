"""BookStack datasource requests with file parameters.

The generated write methods used to hand HTTPRequest a ``{"image": bytes}`` dict, which it
ignored until HTTPRequest gained a typed ``files`` field; a dict there fails validation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.bookstack.bookstack import BookStackDataSource


def _datasource() -> tuple[BookStackDataSource, MagicMock]:
    http = MagicMock()
    http.headers = {"Authorization": "Token a:b", "Content-Type": "application/json"}
    response = MagicMock()
    response.status = 200
    response.bytes = MagicMock(return_value=b'{"id": 1}')
    response.json = MagicMock(return_value={"id": 1})
    http.execute = AsyncMock(return_value=response)
    ds = BookStackDataSource.__new__(BookStackDataSource)
    ds.http = http
    ds.base_url = "https://wiki.example"
    return ds, http


@pytest.mark.asyncio
async def test_without_a_file_the_request_is_unchanged() -> None:
    ds, http = _datasource()
    result = await ds.create_book(name="Handbook", tags=[{"name": "team", "value": "eng"}])

    assert result.success
    request = http.execute.await_args.args[0]
    assert request.files is None
    assert request.body == {"name": "Handbook", "tags": [{"name": "team", "value": "eng"}]}


@pytest.mark.asyncio
async def test_a_file_becomes_a_multipart_part() -> None:
    ds, http = _datasource()
    result = await ds.create_attachment(name="Spec", uploaded_to=7, file=b"PDF")

    assert result.success
    request = http.execute.await_args.args[0]
    assert request.files == [("file", ("file", b"PDF", "application/octet-stream"))]
    assert request.body["name"] == "Spec"


@pytest.mark.asyncio
async def test_an_update_with_a_file_is_a_post_with_method_put() -> None:
    """PHP parses a multipart body only on POST; BookStack reads _method as the real verb."""
    ds, http = _datasource()
    result = await ds.update_attachment(attachment_id=3, name="Spec v2", file=b"PDF")

    assert result.success
    request = http.execute.await_args.args[0]
    assert request.method == "POST"
    assert request.body == {"name": "Spec v2", "_method": "PUT"}
    assert request.files == [("file", ("file", b"PDF", "application/octet-stream"))]


@pytest.mark.asyncio
async def test_an_update_without_a_file_stays_a_put() -> None:
    ds, http = _datasource()
    result = await ds.update_attachment(attachment_id=3, name="Spec v2")

    assert result.success
    request = http.execute.await_args.args[0]
    assert request.method == "PUT"
    assert request.body == {"name": "Spec v2"}
    assert request.files is None
