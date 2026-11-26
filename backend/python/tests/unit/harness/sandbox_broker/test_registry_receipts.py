"""``RegistryExecutionReceipts`` against the receipt contract (P5.11 RC-6), over the registry's fakes."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.receipts import RegistryExecutionReceipts
from app.models.entities import ArtifactVisibility
from app.services.artifact_registry.models import Actor
from app.services.artifact_registry.registry import ArtifactRegistryService
from harness_core.effects.receipts import ExecutionReceipt
from harness_core.testing.contracts.receipts import AT, KEY, ExecutionReceiptsContract
from tests.unit.services.artifact_registry.fakes import FakeBlobStore, FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Callable

ORG = "org-1"
USER = "user-1"
CONVERSATION = "ses-1"


@pytest.fixture
def graph() -> FakeGraphProvider:
    graph = FakeGraphProvider()
    graph.add_user(USER, key="ukey-1")
    return graph


@pytest.fixture
def registry(graph: FakeGraphProvider, monkeypatch: pytest.MonkeyPatch) -> ArtifactRegistryService:
    blob = FakeBlobStore()

    async def fetch(*, org_id: str, storage_document_id: str, version: int | None = None, **_: object) -> bytes:
        return await blob.get_buffer(org_id, storage_document_id, version)

    monkeypatch.setattr("app.agents.actions.util.blob_staging.fetch_blob_bytes", fetch)
    return ArtifactRegistryService(graph, blob)


class TestRegistryExecutionReceipts(ExecutionReceiptsContract):
    @pytest.fixture
    def receipts(self, registry: ArtifactRegistryService) -> RegistryExecutionReceipts:
        return RegistryExecutionReceipts(registry, Actor(org_id=ORG, user_id=USER), CONVERSATION)

    @pytest.fixture
    def stored_names(self, graph: FakeGraphProvider) -> Callable[[], list[str]]:
        def names() -> list[str]:
            return [
                str(doc.get(field))
                for docs in graph.nodes.values()
                for doc in docs.values()
                for field in ("name", "logicalName", "recordName")
                if doc.get(field)
            ]

        return names


async def test_a_receipt_is_a_staging_document_of_the_run_conversation(registry: ArtifactRegistryService) -> None:
    receipts = RegistryExecutionReceipts(registry, Actor(org_id=ORG, user_id=USER), CONVERSATION)
    await receipts.put(KEY, ExecutionReceipt("started", AT))
    listed = await registry.list_for_conversation(actor=Actor(org_id=ORG, user_id=USER), conversation_id=CONVERSATION)
    assert all(item.visibility == ArtifactVisibility.STAGING for item in listed if item.name.startswith("receipt-"))
    other = RegistryExecutionReceipts(registry, Actor(org_id=ORG, user_id=USER), "ses-2")
    assert await other.get(KEY) is None


class _Config:
    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    async def get_config(self, key: str, *_: object, **__: object) -> object:
        if key == "/services/secretKeys":
            return {"scopedJwtSecret": "test-secret"}
        return {"cm": {"endpoint": self._endpoint}}


async def test_qafix3_receipts_move_forward_over_local_storage_which_serves_a_json_file_as_itself(
    graph: FakeGraphProvider,
) -> None:
    """QAFIX3 bug 5. Local storage (the Compose default) answers the download route with the stored file, typed by
    its extension and sent as an attachment, as ``serveFileFromLocalStorage`` does. A receipt is a ``.json`` file,
    so its reader once took it for the cloud ``{signedUrl}`` envelope and failed (``BlobStagingError``): only
    ``started`` was ever stored, and a re-driven attempt answered ``run_code_interrupted`` for a program that had
    finished. Here the reader is the real ``fetch_blob_bytes`` over HTTP."""
    from aiohttp import web

    blob = FakeBlobStore(signs_urls=False)

    async def download(request: web.Request) -> web.Response:
        version = request.query.get("version")
        body = await blob.get_buffer(ORG, request.match_info["document_id"], int(version) if version else None)
        return web.Response(
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Disposition": "attachment; filename*=UTF-8''receipt.json",
            },
        )

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/{document_id}/download", download)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        blob.config_service = _Config(f"http://127.0.0.1:{port}")  # type: ignore[assignment]
        receipts = RegistryExecutionReceipts(
            ArtifactRegistryService(graph, blob), Actor(org_id=ORG, user_id=USER), CONVERSATION
        )
        await receipts.put(KEY, ExecutionReceipt("started", AT))
        await receipts.put(KEY, ExecutionReceipt("executed", AT, {"exit_code": 3}))
        await receipts.put(KEY, ExecutionReceipt("done", AT, {"exit_code": 3}))
        found = await receipts.get(KEY)
    finally:
        await runner.cleanup()
    assert found == ExecutionReceipt("done", AT, {"exit_code": 3})
