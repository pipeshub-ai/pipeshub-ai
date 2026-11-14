"""A batch that reports a fallback is never written to an index that is not fp32, whatever precision it names."""

import logging

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.services.messaging.precision_flip import PrecisionFlip
from app.services.vector_db.collection_pin import INT8, CollectionPinStore
from app.services.vector_db.precision_guard import PrecisionGuard
from tests.unit.services.vector_db.test_collection_pin import _FakeDb

log = logging.getLogger("fallback-flag")


@pytest.mark.asyncio
async def test_a_fallback_flag_over_an_int8_pin_is_refused_even_when_the_header_says_int8() -> None:
    pins = CollectionPinStore(ConfigurationService(log, InMemoryKeyValueStore(log)), log)
    guard = PrecisionGuard(pins, _FakeDb(), log)
    await pins.claim("c", INT8, "o1")
    with pytest.raises(PrecisionFlip, match="fell back"):
        await guard.verify_batch("c", "o1", INT8, fallback=True, intent=INT8)
