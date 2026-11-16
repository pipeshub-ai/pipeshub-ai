"""The store the paid-result integration tests share; settings are in ``tests/support/paid_results_it.py``."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.modules.transformers.blob_storage import BlobStorage, close_shared_session
from app.services.paid_cache import store as store_module
from app.services.paid_cache.store import NodePaidResultStore
from tests.support.paid_results_it import config_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def store() -> AsyncIterator[NodePaidResultStore]:
    store_module.PAID_RESULT_ROUTES.reset()
    log = logging.getLogger("paid-results-it")
    yield NodePaidResultStore(BlobStorage(log, config_service()), log)
    await close_shared_session()


@pytest.fixture(autouse=True)
def _repurge_chain_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests drive the delayed re-purge, which is published only when the operator turns it on."""
    monkeypatch.setenv("PAID_RESULT_REPURGE_CHAIN", "true")
