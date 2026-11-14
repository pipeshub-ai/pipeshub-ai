"""The vector-store cleanup while indexing waits for the embedding precision, from the first hand-back to the new pin.

The real handler, verdict, filter, guard and pin store run; the vector service and the collection registry are
in-memory stand-ins that drop the points when the collections are rebuilt.
"""

import logging
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.services.vector_store_rebuild import PHASE_READY
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.messaging.precision_check import (
    VERDICT_KEY,
    PrecisionFilter,
    ServerState,
    set_active_verdict,
)
from app.services.messaging.precision_runtime import build_precision_verdict
from app.services.vector_db.collection_pin import FP32, INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("cleanup-e2e")
ORG = "o1"


@pytest.fixture(autouse=True)
def _no_active_verdict():  # noqa: ANN202
    yield
    set_active_verdict(None)


def _message(event: str):  # noqa: ANN202
    return SimpleNamespace(eventType=event, payload={})


@pytest.mark.asyncio
async def test_the_cleanup_releases_every_pin_and_refreshes_the_verdict_before_it_reports_ready(monkeypatch) -> None:
    config = ConfigurationService(log, InMemoryKeyValueStore(log))
    pins = CollectionPinStore(config, log)
    db = _FakeDb()
    db.add(INT8, n=3, org=ORG)
    registry = MagicMock()
    registry.list_managed_collections = AsyncMock(return_value=[SimpleNamespace(name="records", embedding_model="m")])
    graph = MagicMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": ORG}])
    verdict = build_precision_verdict(config, registry, db, graph, log)
    verdict._fetch_states = AsyncMock(return_value={"m": ServerState(FP32, FP32, False)})  # the server now serves fp32
    set_active_verdict(verdict)
    await pins.claim("records", INT8, ORG)
    await pins.claim("gone", FP32, "o9")  # an orphan left by a collection that no longer exists

    await verdict.refresh()
    assert verdict.mismatch
    hand_back = PrecisionFilter(verdict, log, rng=random.Random(1))
    assert sorted(hand_back.hand_back([_message("newRecord"), _message("deleteVectorCollection"), _message("deleteRecord")])) == [0]

    assert (await config.get_config(VERDICT_KEY, use_cache=False))["mismatch"] is True  # what the cleanup job reads

    phases: list[tuple[str, bool, bool]] = []

    async def mark(_config, phase, **_k):  # noqa: ANN202
        published = (await config.get_config(VERDICT_KEY, use_cache=False))["mismatch"]
        phases.append((phase, verdict.mismatch, published))

    monkeypatch.setattr(record_module, "mark_cleanup_phase", mark)

    async def rebuild(_size):  # noqa: ANN202
        db.points.clear()
        return ["records"]

    vector_store = SimpleNamespace(get_embedding_model_instance=AsyncMock(), embedding_size=4)
    handler = object.__new__(RecordEventHandler)
    handler.config_service = config
    handler.logger = log
    handler.event_processor = SimpleNamespace(
        sink_orchestrator=SimpleNamespace(vector_store=vector_store),
        processor=SimpleNamespace(indexing_pipeline=SimpleNamespace(collection_registry=SimpleNamespace(recreate_records_collections=rebuild))),
    )
    events = [e async for e in handler._delete_vector_collection()]
    assert events

    assert phases == [(PHASE_READY, False, False)]  # the verdict, and the key the job reads, agreed before READY
    assert await pins.keys() == []  # the index's pin and the orphan are both gone
    assert hand_back.hand_back([_message("newRecord")]) == {}  # handed-back events are let through again

    # the first write at the new precision claims the new pin, and nothing of the old precision is anywhere
    guard = PrecisionGuard(pins, db, log)
    await guard.verify_batch("records", ORG, FP32, fallback=False, intent=INT8)
    db.add(FP32, org=ORG)
    assert (await pins.get("records", ORG, fresh=True)).precision == FP32
    assert {p["embedPrecision"] for p in db.points} == {FP32}
