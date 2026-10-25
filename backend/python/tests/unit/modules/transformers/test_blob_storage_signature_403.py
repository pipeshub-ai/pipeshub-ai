"""A 403 from an expired or skewed signature, mapping unchanged, is retried.

Real BlobStorage reader against a real HTTP server (gateway signs a URL, the
object answers 403), then:
  * the error as the consumer classifies it (MessageErrorClassifier);
  * the vector-only re-index handler (the caller named by the build note).
Single and ranged downloads both raise the reader's own error; it must classify
TRANSIENT without leaning on an aiohttp error in its chain.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web

from app.modules.transformers import blob_storage as bs_mod
from app.modules.transformers.blob_storage import (
    BlobStorage,
    SignedUrlReadError,
    StorageDocumentNotFoundError,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.utils.user_errors import STORED_CONTENT_MISSING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

log = logging.getLogger("qa05ca")


class Graph:
    def __init__(self, doc: str, size: int | None) -> None:
        self.doc, self.size, self.reads = doc, size, 0

    async def get_document(self, key, collection, **_kw: object) -> dict:
        self.reads += 1
        return {"documentId": self.doc, "fileSizeBytes": self.size}

    async def get_nodes_by_filters(self, *_a: object, **_kw: object) -> list:
        return []


@pytest.fixture
async def server() -> AsyncIterator[dict]:
    state = {"status": 403, "hits": 0}

    async def download(request) -> web.Response:
        doc = request.match_info["doc"]
        return web.json_response({"signedUrl": f"{state['origin']}/obj/{doc}?X-Amz-Signature=abc"})

    async def obj(request) -> web.Response:
        state["hits"] += 1
        body = b"<Error><Code>RequestTimeTooSkewed</Code></Error>"
        return web.Response(status=state["status"], body=body)

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{doc}/download", download)
    app.router.add_route("*", "/obj/{doc}", obj)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    state["origin"] = f"http://127.0.0.1:{port}"
    yield state
    await bs_mod.close_shared_session()
    await runner.cleanup()


def _blob(state, graph) -> BlobStorage:
    blob = BlobStorage(logger=log, config_service=AsyncMock(), graph_provider=graph)
    blob._get_auth_and_config = AsyncMock(return_value=({}, state["origin"], "s3"))
    blob._cached_signed_url = AsyncMock(return_value=None)
    blob._store_signed_url = AsyncMock()
    return blob


@pytest.mark.parametrize("size", [10, None], ids=["single", "ranged"])
async def test_skewed_signature_403_is_not_terminal(server, size) -> None:
    graph = Graph("doc-1", size)
    blob = _blob(server, graph)
    with pytest.raises(Exception) as raised:
        await blob.get_record_from_storage("vr-1", "org-1")
    exc = raised.value
    assert not isinstance(exc, StorageDocumentNotFoundError)
    kind = MessageErrorClassifier.classify_by_exception(exc)
    assert kind == MessageErrorType.TRANSIENT, f"a skewed-signature 403 classified {kind}"


@pytest.mark.parametrize("size", [10, None], ids=["single", "ranged"])
async def test_vector_only_reindex_does_not_brand_content_missing_on_a_403(server, size) -> None:
    from app.config.constants.arangodb import EventTypes, MimeTypes
    from tests.unit.services.messaging.test_record_handler import (
        _collect_events,
        _make_handler,
        _vector_only_graph_doc,
    )

    handler = _make_handler()
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value=_vector_only_graph_doc())
    pipeline = handler.event_processor.processor.indexing_pipeline
    pipeline.delete_points_for_virtual_record = AsyncMock()
    status = AsyncMock(return_value=_vector_only_graph_doc())
    handler._RecordEventHandler__update_document_status = status
    sink = MagicMock()
    sink.blob_storage = _blob(server, Graph("doc-1", size))
    handler.event_processor.sink_orchestrator = sink

    payload = {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": "vr-1",
        "mimeType": MimeTypes.PLAIN_TEXT.value, "forceReindex": True, "vectorDbOnly": True,
    }
    with pytest.raises(SignedUrlReadError):
        await _collect_events(handler, EventTypes.REINDEX_RECORD.value, payload)
    reasons = [c.kwargs.get("reason") for c in status.await_args_list]
    assert STORED_CONTENT_MISSING not in reasons, f"record marked FAILED: {reasons}"


@pytest.mark.parametrize("status", [403, 404, None])
@pytest.mark.parametrize("ranged", [False, True])
def test_the_reader_error_classifies_transient_on_its_own(status: int | None, ranged: bool) -> None:
    """No aiohttp error in the chain, and whatever the status it carries."""
    exc = SignedUrlReadError("x", status, ranged=ranged)
    assert MessageErrorClassifier.classify_by_exception(exc) == MessageErrorType.TRANSIENT
