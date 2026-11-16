"""The parsing service's ``parse`` writer lists the paid calls of a parse in its revision's manifest.

With ``USE_PARSING_SERVICE`` on, this manifest is the only thing that protects the page OCR and table results a
parse stores from the sweep the ``index`` run starts (QA-S7a-7).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import parsing
from app.modules.pipeline.fingerprint import content_revision
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.run import clear_run
from app.services.parsing.registry import ParserRegistry
from tests.support.paid_cache import MemoryResultStore, record_in_flight
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

ORG, REC = "org-123", "rec-1"
BODY = b"%PDF-1.4 a scan"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    monkeypatch.setattr(parsing, "_config_service", lambda _request: MagicMock())

    @asynccontextmanager
    async def scope(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        with record_in_flight(ORG, REC):
            yield

    monkeypatch.setattr(parsing, "scope_from_claims", scope)
    yield memory
    clear_run()


def _post(parser_parse: Any) -> Any:  # noqa: ANN401
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = parser_parse
    registry.resolve = MagicMock(return_value=parser)
    client = TestClient(_build_app(registry), raise_server_exceptions=False)
    return client.post(
        "/api/v1/parse",
        files={"file": ("s.pdf", BODY, "application/pdf")},
        data={"record_name": "s.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default"},
    )


def test_a_parse_lists_the_paid_calls_it_made_in_the_manifest_of_its_revision(store: MemoryResultStore) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await access.paid_cache_for(MagicMock()).get_or_compute(KEY, AsyncMock(return_value="page text"))
        return _ok_result()

    response = _post(parse)

    assert response.status_code == 200
    manifest = json.loads(store.files[(ORG, REC, f"manifests/{content_revision(BODY)}/parse.json")][0])
    assert manifest["writer"] == "parse" and manifest["keys"] == [KEY.relative_path()]
    assert (ORG, REC, f"manifests/{content_revision(BODY)}/parse.open.json") not in store.files


def test_a_parse_that_fails_keeps_its_open_manifest(store: MemoryResultStore) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await access.paid_cache_for(MagicMock()).get_or_compute(KEY, AsyncMock(return_value="page text"))
        raise RuntimeError("renderer crashed")

    assert _post(parse).status_code >= 500
    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/parse.open.json") in store.files
    assert (ORG, REC, f"manifests/{rev}/parse.json") not in store.files
