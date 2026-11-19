"""The query service ensures the payload indexes of every managed collection when it starts."""

from unittest.mock import AsyncMock, MagicMock

from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from tests.support.vector_db import make_manifest_store


def _registry(names: tuple[str, ...], create_index: AsyncMock) -> CollectionRegistry:
    vdb = MagicMock()
    vdb.create_index = create_index
    return CollectionRegistry(
        vector_db_service=vdb, strategy=SingleCollectionStrategy(),
        collection_config_factory=lambda size: CollectionConfig(embedding_size=size),
        manifest_store=make_manifest_store(names), logger=MagicMock(),
    )


async def test_every_managed_collection_gets_every_payload_index() -> None:
    create_index = AsyncMock()
    done = await _registry(("a", "b"), create_index).reconcile_payload_indexes()
    assert sorted(done) == ["a", "b"]
    wanted = {(n, f) for n in ("a", "b") for f, _ in PAYLOAD_KEYWORD_INDEXES}
    assert {(c.kwargs["collection_name"], c.kwargs["field_name"]) for c in create_index.await_args_list} == wanted


async def test_one_collection_that_fails_does_not_stop_the_others() -> None:
    async def create_index(collection_name, field_name, field_schema) -> None:
        if collection_name == "a":
            raise RuntimeError("boom")

    done = await _registry(("a", "b"), AsyncMock(side_effect=create_index)).reconcile_payload_indexes()
    assert "b" in done


async def test_an_unreadable_manifest_is_logged_not_raised() -> None:
    registry = _registry(("a",), AsyncMock())
    registry.list_managed_collections = AsyncMock(side_effect=RuntimeError("kv down"))
    assert await registry.reconcile_payload_indexes() == []
