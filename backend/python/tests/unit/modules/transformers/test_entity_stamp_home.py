"""Where a collection's stamp lives: as an org-less point written under the explicit system scope, or, for a provider that
says so, in its own catalog (a point needs an org and the stamp belongs to none)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.core import org_context
from app.modules.transformers import entity_vectorstore as module
from app.modules.transformers.entity_vectorstore import EntityVectorStore
from app.services.vector_db.models import VectorDBCapabilities, VectorPoint
from tests.support.embedding_config import config_service
from tests.support.entity_vector_db import (
    FakeEmbeddingModel,
    FakeEntityVectorDB,
    embedding_models,
    recreate_lock_redis,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _env() -> Iterator[None]:
    with embedding_models({}), patch.object(module, "get_default_embedding_model", return_value=FakeEmbeddingModel(1.0, 4)), recreate_lock_redis():
        yield


def _store(db) -> EntityVectorStore:
    return EntityVectorStore(logger=logging.getLogger("stamp-test"), config_service=config_service(), vector_db_service=db)


class _PointStampDB(FakeEntityVectorDB):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[str] = []

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        try:
            self.contexts.append(org_context.current_or_system().kind)
        except org_context.MissingOrgContext:
            self.contexts.append("none")
        return await super().upsert_points(collection_name, points)


async def test_the_stamp_point_is_written_only_under_the_system_scope() -> None:
    db = _PointStampDB()
    store = _store(db)
    await store.embedding_fingerprint(recreate=True)
    stamp = await store.collection_stamp()
    assert stamp and db.stamp == stamp
    assert db.contexts == ["system"]                 # the one org-less write, and it says so
    assert await store.collection_stamp() == stamp   # read back, not rewritten
    assert db.contexts == ["system"]


class _CatalogStampDB(FakeEntityVectorDB):
    def __init__(self) -> None:
        super().__init__()
        self.catalog: dict[str, str] = {}

    def get_capabilities(self) -> VectorDBCapabilities:
        return VectorDBCapabilities(stamps_in_catalog=True)

    async def get_collection_stamp(self, name: str) -> str | None:
        return self.catalog.get(name)

    async def set_collection_stamp(self, name: str, stamp: str) -> str:
        return self.catalog.setdefault(name, stamp)


async def test_a_provider_that_keeps_the_stamp_itself_gets_no_point() -> None:
    db = _CatalogStampDB()
    store = _store(db)
    await store.embedding_fingerprint(recreate=True)
    stamp = await store.collection_stamp()
    assert stamp and list(db.catalog.values()) == [stamp]
    assert db.stamp is None and not db.points        # nothing was written as a point
    assert await store.collection_stamp() == stamp
