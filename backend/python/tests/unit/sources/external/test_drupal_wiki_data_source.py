"""Unit tests for the generated Drupal Wiki data source.

Requests go through a real ``httpx.AsyncClient`` with a mock transport, so the
assertions cover what actually goes on the wire: URL, query string, headers, JSON
body, multipart upload and streamed download.
"""

import importlib.util
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from app.sources.client.drupal_wiki.drupal_wiki import (
    DrupalWikiClient,
    DrupalWikiTokenConfig,
)
from app.sources.external.drupal_wiki.drupal_wiki import DrupalWikiDataSource

BASE_URL = "https://wiki.example.com"
BACKEND_PYTHON = Path(__file__).resolve().parents[4]
GENERATOR = BACKEND_PYTHON / "code-generator" / "drupal_wiki.py"
SPEC = BACKEND_PYTHON / "code-generator" / "drupal_wiki" / "drupal_wiki_openapi.json"

# The generator and the vendor spec are kept in a separate repository, so these two
# checks only run where that repository is also checked out. They still guard the
# generated data source wherever it is regenerated.
needs_generator = pytest.mark.skipif(
    not (GENERATOR.exists() and SPEC.exists()),
    reason="code-generator/drupal_wiki.py and its OpenAPI spec live in a separate repository",
)


Handler = Callable[[httpx.Request], httpx.Response]


def _data_source(handler: Handler) -> tuple[DrupalWikiDataSource, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = DrupalWikiClient.build_with_config(DrupalWikiTokenConfig(base_url=BASE_URL, token="secret"))
    http = client.get_client()
    http.client = httpx.AsyncClient(transport=httpx.MockTransport(record), headers=http.headers)
    return DrupalWikiDataSource(client), seen


def _json(payload: dict, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, json=payload)


@pytest.mark.asyncio
async def test_list_pages_sends_filters_and_auth_headers() -> None:
    page = {"content": [{"id": 7, "title": "Home", "homeSpace": 3, "lastModified": 1722935527}], "last": True}
    ds, seen = _data_source(_json(page))

    resp = await ds.list_pages(space_id=3, modified_after=1722935527, page=0, size=100)

    assert resp.status == 200
    assert resp.json() == page
    request = seen[0]
    assert request.method == "GET"
    assert request.url.path == "/api/rest/scope/api/page"
    assert dict(request.url.params) == {"space": "3", "modifiedAfter": "1722935527", "page": "0", "size": "100"}
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["X-API-Version"] == "1"


@pytest.mark.asyncio
async def test_unset_optional_query_params_are_omitted() -> None:
    ds, seen = _data_source(_json({"content": [], "last": True}))
    await ds.list_spaces()
    assert seen[0].url.query == b""


@pytest.mark.asyncio
async def test_boolean_query_is_lowercase() -> None:
    ds, seen = _data_source(_json({"content": [], "last": True}))
    await ds.list_users(only_active=False, size=1)
    assert dict(seen[0].url.params) == {"onlyActive": "false", "size": "1"}


@pytest.mark.asyncio
async def test_path_ids_are_formatted() -> None:
    ds, seen = _data_source(_json({"id": 42, "members": [{"id": 1, "uid": 5}], "memberCount": 1}))
    resp = await ds.get_group(42)
    assert seen[0].url.path == "/api/rest/scope/api/group/42"
    assert resp.json()["memberCount"] == 1


@pytest.mark.asyncio
async def test_create_page_body_keeps_page_body_and_wraps_space_id() -> None:
    ds, seen = _data_source(_json({"id": 99}, status=201))

    resp = await ds.create_page(
        title="Runbook",
        body="Restart the service",
        page_type="DOCUMENT",
        space_id=12,
        tags=["ops"],
        categories=[],
    )

    assert resp.status == 201
    request = seen[0]
    assert request.method == "POST"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "title": "Runbook",
        "body": "Restart the service",
        "type": "DOCUMENT",
        "space": {"id": 12},
        "tags": ["ops"],
        "categories": [],
    }


@pytest.mark.asyncio
async def test_update_page_only_sends_given_fields() -> None:
    ds, seen = _data_source(lambda request: httpx.Response(204))
    await ds.update_page(5, body="new text", tags=[])
    assert seen[0].method == "PATCH"
    assert json.loads(seen[0].content) == {"body": "new text", "tags": []}


@pytest.mark.asyncio
async def test_create_attachment_is_multipart() -> None:
    ds, seen = _data_source(_json({"id": 3}))

    await ds.create_attachment(page_id=733, file_name="notes.txt", content=b"hello")

    request = seen[0]
    assert dict(request.url.params) == {"pageId": "733", "fileName": "notes.txt"}
    assert request.headers["Content-Type"].startswith("multipart/form-data; boundary=")
    body = request.read()
    assert b'name="attachment"; filename="notes.txt"' in body
    assert b"hello" in body


@pytest.mark.asyncio
async def test_download_attachment_streams_bytes_with_any_accept() -> None:
    payload = b"x" * 10
    ds, seen = _data_source(lambda request: httpx.Response(200, content=payload))

    chunks = [chunk async for chunk in ds.download_attachment(8, chunk_size=4)]

    assert b"".join(chunks) == payload
    assert seen[0].url.path == "/api/rest/scope/api/attachment/8/download"
    assert seen[0].headers["Accept"] == "*/*"
    assert seen[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_download_attachment_raises_on_error_status() -> None:
    ds, _ = _data_source(lambda request: httpx.Response(404))
    with pytest.raises(httpx.HTTPStatusError):
        [chunk async for chunk in ds.download_attachment(8)]


@pytest.mark.asyncio
async def test_error_status_is_returned_not_raised() -> None:
    problem = {"status": 403, "title": "Forbidden", "errorCode": "ACCESS_DENIED"}
    ds, _ = _data_source(_json(problem, status=403))
    resp = await ds.get_space(1)
    assert resp.status == 403
    assert resp.json()["errorCode"] == "ACCESS_DENIED"


@needs_generator
def test_every_spec_operation_is_generated() -> None:
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    operation_count = sum(1 for item in spec["paths"].values() for verb in item if verb in {"get", "post", "put", "patch", "delete"})
    public_methods = [name for name in vars(DrupalWikiDataSource) if not name.startswith("_") and name != "get_data_source"]
    assert len(public_methods) == operation_count


@needs_generator
def test_generated_file_matches_generator() -> None:
    spec = importlib.util.spec_from_file_location("drupal_wiki_generator", GENERATOR)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)

    expected = generator.render(json.loads(generator.SPEC_FILE.read_text(encoding="utf-8")))
    actual = generator.OUTPUT_FILE.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == expected, "Run python code-generator/drupal_wiki.py to regenerate the data source"
