"""The weight-precision pin on the PostgreSQL key-value store: the claim, the uncached re-read and the release.

Two ``ConfigurationService`` objects over two ``PostgresKeyValueStore`` objects on one database are two processes.
Every collection name is unique to the test, so cases do not see each other's pins.
The processes run as the indexing service (``indexing_runtime``), the role that writes pins in production.
"""

import asyncio
import json
import logging
from unittest.mock import patch

from app.config.configuration_service import ConfigurationService
from app.config.providers import postgres_store
from app.config.providers.postgres_store import PostgresKeyValueStore
from app.services.vector_db.collection_epoch import EPOCH_DIRECTORY, CollectionEpochs
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore

log = logging.getLogger("pin-postgres")


def _process(runtime) -> tuple[ConfigurationService, CollectionPinStore]:
    store = PostgresKeyValueStore(lambda v: json.dumps(v).encode(), lambda b: json.loads(b), runtime=runtime)
    with patch.object(postgres_store, "listen_dsn", return_value=None):  # no listener: a missed NOTIFY is what is tested
        config = ConfigurationService(log, store)
    return config, CollectionPinStore(config, log)


async def test_a_key_is_created_once_and_never_replaced_by_the_second_creator(indexing_runtime, uid) -> None:
    config_a, _ = _process(indexing_runtime)
    config_b, _ = _process(indexing_runtime)
    key = f"/services/vectordb/embedding_pin/it-{uid}/claim"
    try:
        assert await config_a.create_config_if_absent(key, {"precision": INT8}) is True
        assert await config_b.create_config_if_absent(key, {"precision": FP32}) is False
        assert (await config_b.get_config(key, use_cache=False))["precision"] == INT8
    finally:
        await config_a.delete_config(key)


async def test_two_processes_claiming_different_precisions_leave_one_pin(indexing_runtime, uid) -> None:
    (_, pins_a), (_, pins_b) = _process(indexing_runtime), _process(indexing_runtime)
    collection = f"it-{uid}-race"
    try:
        one, two = await asyncio.gather(pins_a._claim_unfenced(collection, INT8, "o1"), pins_b._claim_unfenced(collection, FP32, "o1"))
        assert one.precision == two.precision  # the loser is told what the winner pinned
        assert (await pins_a.get(collection, "o1", fresh=True)).precision == one.precision
    finally:
        await pins_a.release_collection(collection)


async def test_a_release_is_seen_by_an_uncached_read_of_another_process(indexing_runtime, uid) -> None:
    (_, pins_a), (_, pins_b) = _process(indexing_runtime), _process(indexing_runtime)
    collection = f"it-{uid}-release"
    await pins_a._claim_unfenced(collection, INT8, "o1")
    assert (await pins_b.get(collection, "o1")).precision == INT8
    await pins_a.release_collection(collection)
    assert await pins_b.get(collection, "o1", fresh=True) is None
    assert (await pins_b._claim_unfenced(collection, FP32, "o1")).precision == FP32  # a new index may take the other precision
    await pins_b.release_collection(collection)


async def test_the_connectors_service_can_begin_a_reset(runtime, uid) -> None:
    """The search-index cleanup runs in the connectors service (as its login, ``runtime``) and moves the epoch before it resets anything."""
    config, _ = _process(runtime)
    collection = f"it-{uid}-reset"
    epochs = CollectionEpochs(config, log)
    try:
        ticket = await epochs.begin_reset(collection)
        assert ticket.collection == collection
        assert await epochs.current(collection) is not None
    finally:
        for key in await config.list_keys_in_directory(f"{EPOCH_DIRECTORY}/{collection}/") or []:
            await config.delete_config(key)
        await config.delete_config(f"{EPOCH_DIRECTORY}/{collection}/current")
