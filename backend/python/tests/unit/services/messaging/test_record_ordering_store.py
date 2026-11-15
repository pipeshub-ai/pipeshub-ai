"""Tests for per-record ordering state and atomic failure flips."""

import logging

import pytest

from app.services.messaging.record_ordering_store import (
    RECORD_ORDER_BIT_FIELD,
    RecordOrderingStore,
)
from tests.support.fake_cluster_redis import FakeClusterRedis


@pytest.fixture
def store() -> RecordOrderingStore:
    return RecordOrderingStore(
        logging.getLogger("test_record_ordering_store"),
        redis_client=FakeClusterRedis(),
    )


class TestRecordOrderingStore:
    @pytest.mark.asyncio
    async def test_initializes_missing_records_false_and_preserves_existing_bit(
        self, store: RecordOrderingStore
    ) -> None:
        assert await store.get("record-1") is None
        assert await store.get_or_initialize("record-1") is False

        await store.flip_after_failure("record-1", False, "failure-1")

        assert await store.get_or_initialize("record-1") is True

    @pytest.mark.asyncio
    async def test_matching_event_bit_is_accepted_and_mismatch_returns_saved_bit(
        self, store: RecordOrderingStore
    ) -> None:
        await store.get_or_initialize("record-1", initial_bit=True)
        matching = {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: True}
        stale = {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: False}

        assert await store.defer_bit_if_mismatched(matching) is None
        assert await store.defer_bit_if_mismatched(stale) is True
        assert await store.get("record-1") is True

    @pytest.mark.asyncio
    async def test_legacy_event_is_seeded_with_saved_bit(
        self, store: RecordOrderingStore
    ) -> None:
        await store.get_or_initialize("record-1", initial_bit=True)
        payload = {"recordId": "record-1"}

        assert await store.defer_bit_if_mismatched(payload) is None
        assert payload[RECORD_ORDER_BIT_FIELD] is True

    @pytest.mark.asyncio
    async def test_failure_flips_saved_and_event_bits_once_per_token(
        self, store: RecordOrderingStore
    ) -> None:
        await store.get_or_initialize("record-1")
        payload = {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: False}

        assert await store.flip_event_after_failure(payload, "failure-1") is True
        assert payload[RECORD_ORDER_BIT_FIELD] is True
        assert await store.get("record-1") is True

        assert await store.flip_event_after_failure(payload, "failure-1") is True
        assert payload[RECORD_ORDER_BIT_FIELD] is True
        assert await store.get("record-1") is True

    @pytest.mark.asyncio
    async def test_stale_failure_does_not_flip_saved_bit(
        self, store: RecordOrderingStore
    ) -> None:
        await store.get_or_initialize("record-1", initial_bit=True)
        payload = {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: False}

        assert await store.flip_event_after_failure(payload, "failure-1") is True
        assert payload[RECORD_ORDER_BIT_FIELD] is True
        assert await store.get("record-1") is True

    @pytest.mark.asyncio
    async def test_recordless_payload_bypasses_ordering_state(
        self, store: RecordOrderingStore
    ) -> None:
        payload = {"orgId": "org-1"}

        assert await store.defer_bit_if_mismatched(payload) is None
        assert await store.flip_event_after_failure(payload, "failure-1") is None
        assert RECORD_ORDER_BIT_FIELD not in payload

    @pytest.mark.asyncio
    async def test_rejects_non_boolean_event_bit(
        self, store: RecordOrderingStore
    ) -> None:
        with pytest.raises(ValueError, match="must be a boolean"):
            await store.defer_bit_if_mismatched(
                {"recordId": "record-1", RECORD_ORDER_BIT_FIELD: 1}
            )
