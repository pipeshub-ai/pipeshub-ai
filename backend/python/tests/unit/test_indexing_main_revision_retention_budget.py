"""Revision retention must not hold the recovery pass hostage to a slow or hung storage service.

The real sweep and the real ``NodeArtifactStore``/``BlobStorage`` HTTP client run
against a local HTTP server standing in for the storage service; only the graph
is faked (pages of 200 mappings, each with a pointer, nothing trashed).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web

from app import indexing_main as m
from app.config.constants.service import config_node_constants
from app.modules.artifacts import store as store_module
from app.modules.artifacts.retention import RevisionRetentionSweep
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.transformers import blob_storage as blob_module
from app.modules.transformers.blob_storage import BlobStorage, close_shared_session

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.timeout(400)

# The recovery loop's interval: one pass of retention should fit well inside it.
RECOVERY_INTERVAL_S = 60.0


class _Graph:
    def __init__(self, page: int = 200) -> None:
        self.page = page

    async def get_revision_pointers(self, after_key: str | None, limit: int) -> list[dict[str, Any]]:
        start = 0 if after_key is None else int(after_key[1:]) + 1
        return [
            {"virtualRecordId": f"v{i:08d}", "orgId": "64d000000000000000000e01",
             "currentRev": "c" * 16, "previousRev": "b" * 16}
            for i in range(start, start + min(limit, self.page))
        ]

    async def get_virtual_record_ids_in_use(self, vrids: list[str], **_: Any) -> set[str]:  # noqa: ANN401
        return set()

    async def stage_states_unheld_revisions(self, vrids: list[str], **_: Any) -> list[tuple[str, str]]:  # noqa: ANN401
        return []


async def _storage(delay_s: float | None) -> tuple[web.AppRunner, str]:
    """A storage service whose listing answers after ``delay_s``; None never answers."""

    async def listing(request: web.Request) -> web.Response:
        if delay_s is None:
            await asyncio.sleep(3600)
        await asyncio.sleep(delay_s or 0)
        return web.json_response({"revisions": []})

    app = web.Application()
    app.router.add_get("/api/v1/document/internal/artifacts", listing)
    # A hung listing handler is still asleep at cleanup; do not wait out aiohttp's 60 s.
    runner = web.AppRunner(app, shutdown_timeout=0.5)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
    return runner, f"http://127.0.0.1:{port}"


def _blob(origin: str) -> BlobStorage:
    values = {
        config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": "budget-test-scoped-secret-0123456789"},
        config_node_constants.ENDPOINTS.value: {"cm": {"endpoint": origin}},
        config_node_constants.STORAGE.value: {"storageType": "local"},
    }
    service = MagicMock()
    service.get_config = AsyncMock(side_effect=lambda key, *a, **kw: values[key])
    return BlobStorage(logger=MagicMock(), config_service=service)


@pytest.fixture(autouse=True)
async def _clean() -> AsyncIterator[None]:
    m._retention_cursor = None
    store_module.ARTIFACT_ROUTES.reset()
    await close_shared_session()
    yield
    await close_shared_session()
    m._retention_cursor = None


async def test_one_recovery_tick_of_retention_fits_the_interval_when_storage_is_slow() -> None:
    """Storage answering each listing in 1 s (slow, not down): the tick is still bounded in time."""
    runner, origin = await _storage(1.0)
    container = MagicMock()
    container.blob_storage = MagicMock(return_value=_blob(origin))
    try:
        started = time.perf_counter()
        await m._sweep_retired_revisions(
            app_container=container, graph_provider=_Graph(), logger=MagicMock(), lease_held=lambda: True,
        )
        took = time.perf_counter() - started
    finally:
        await runner.cleanup()

    assert took < RECOVERY_INTERVAL_S, f"one retention tick took {took:.1f}s"


async def test_a_hung_storage_service_ends_the_page_early() -> None:
    """Listings that never answer: the page gives up after a few, not after every vrid's retries."""
    runner, origin = await _storage(None)
    sweep = RevisionRetentionSweep(_Graph(), NodeArtifactStore(_blob(origin), MagicMock()), MagicMock())
    try:
        # Read timeout scaled down from 120 s to 0.2 s; everything else as shipped.
        with patch.object(blob_module, "BLOB_HTTP_SOCK_READ_TIMEOUT_SECONDS", 0.2):
            started = time.perf_counter()
            _, report = await sweep.run_page(None)
            took = time.perf_counter() - started
    finally:
        await runner.cleanup()

    # 200 vrids x 3 attempts x read timeout, 8 at a time. At the shipped 120 s
    # timeout that is ~3 h for one page of a recovery pass.
    assert report.failed < 200, f"every vrid of the page was tried against a hung storage ({took:.1f}s)"
