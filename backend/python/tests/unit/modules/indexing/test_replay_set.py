"""The replay set: the last deliveries that completed a record (fakeredis, with Lua)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import fakeredis.aioredis
import pytest

from app.modules.indexing import replay_set
from app.modules.indexing.replay_set import REPLAY_SET_SIZE, covers, flags_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def redis() -> AsyncIterator[Any]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


async def _record(redis, record_id: str, n: int, *, flags: str = "") -> None:
    await replay_set.record_delivery(
        redis, record_id, event_id=f"evt-{n}", delivery_id=f"t-0-{n}", flags=flags, completed_at_ms=1000 + n
    )


async def _entry(redis, record_id: str, payload: dict) -> replay_set.StoredDelivery | None:
    """What the set holds for the payload's event id, as the handler reads it."""
    return (await replay_set.read_entries(redis, record_id, payload)).get(str(payload.get("_event_id")))


class TestFlags:
    def test_a_plain_event_carries_no_flags(self) -> None:
        assert flags_of({"recordId": "r"}) == ""

    def test_force_and_stages_are_flags(self) -> None:
        assert flags_of({"forceReindex": True}) == "F"
        assert flags_of({"stages": ["classify"]}).startswith("S=")
        both = flags_of({"forceReindex": True, "stages": ["entities", "classify"]})
        assert both.startswith("F,S=")
        # The stage set, not its order.
        assert flags_of({"stages": ["classify", "entities"]}) == flags_of({"stages": ["entities", "classify"]})
        assert flags_of({"stages": ["classify"]}) != flags_of({"stages": ["entities"]})

    @pytest.mark.parametrize(
        ("stored", "replay", "answered"),
        [
            ("", "", True),
            ("F", "", True),
            ("F", "F", True),
            ("", "F", False),
            ("S=aaaaaaaa", "S=aaaaaaaa", True),
            ("S=aaaaaaaa", "S=bbbbbbbb", False),
            ("", "S=aaaaaaaa", False),
            ("F,S=aaaaaaaa", "F,S=aaaaaaaa", True),
            ("F", "F,S=aaaaaaaa", False),
        ],
    )
    def test_the_stored_flags_must_cover_the_replays(self, stored: str, replay: str, answered: bool) -> None:
        assert covers(stored, replay) is answered


class TestTheSet:
    async def test_an_entry_is_found_by_its_event_id_only(self, redis) -> None:
        await _record(redis, "rec-1", 5, flags="F")

        found = await _entry(redis, "rec-1", {"_event_id": "evt-5"})
        assert found == replay_set.StoredDelivery(flags="F", delivery_id="t-0-5", completed_at_ms=1005)
        # The broker delivery id alone is not an identity.
        assert await _entry(redis, "rec-1", {"_delivery_id": "t-0-5"}) is None
        assert await _entry(redis, "rec-1", {"_event_id": "evt-6", "_delivery_id": "t-0-5"}) is None
        assert await _entry(redis, "rec-2", {"_event_id": "evt-5"}) is None
        assert await _entry(redis, "rec-1", {}) is None

    async def test_only_the_newest_thirty_two_deliveries_are_kept(self, redis) -> None:
        for n in range(REPLAY_SET_SIZE + 5):
            await _record(redis, "rec-1", n)

        assert await redis.zcard(replay_set.order_key("rec-1")) == REPLAY_SET_SIZE
        assert await redis.hlen(replay_set.hash_key("rec-1")) == REPLAY_SET_SIZE
        assert await _entry(redis, "rec-1", {"_event_id": "evt-0"}) is None
        newest = REPLAY_SET_SIZE + 4
        assert await _entry(redis, "rec-1", {"_event_id": f"evt-{newest}"}) is not None

    async def test_a_hash_left_without_its_order_key_does_not_outgrow_the_bound(self, redis) -> None:
        # An eviction policy can drop one key of the pair; the order key is the one trimming reads.
        for n in range(REPLAY_SET_SIZE):
            await _record(redis, "rec-1", n)
        await redis.delete(replay_set.order_key("rec-1"))

        await _record(redis, "rec-1", REPLAY_SET_SIZE)

        assert await redis.hlen(replay_set.hash_key("rec-1")) == 1
        assert await redis.zcard(replay_set.order_key("rec-1")) == 1

    async def test_the_same_event_completing_twice_is_one_entry(self, redis) -> None:
        await replay_set.record_delivery(redis, "rec-1", event_id="evt-a", delivery_id="d", flags="", completed_at_ms=1)
        await replay_set.record_delivery(redis, "rec-1", event_id="evt-a", delivery_id="d", flags="F", completed_at_ms=2)

        assert await redis.zcard(replay_set.order_key("rec-1")) == 1
        assert (await _entry(redis, "rec-1", {"_event_id": "evt-a"})).flags == "F"

    async def test_the_keys_expire_and_share_one_hash_slot(self, redis) -> None:
        await _record(redis, "rec-1", 1)

        for key in (replay_set.hash_key("rec-1"), replay_set.order_key("rec-1")):
            assert 0 < await redis.pttl(key) <= replay_set.REPLAY_SET_TTL_MS
        # A cluster runs the script on one node only when both keys carry the same tag.
        assert "{rec-1}" in replay_set.hash_key("rec-1") and "{rec-1}" in replay_set.order_key("rec-1")

    async def test_an_event_without_an_envelope_event_id_writes_nothing(self, redis) -> None:
        await replay_set.record_delivery(redis, "rec-1", event_id=None, delivery_id="t-0-1", flags="", completed_at_ms=1)

        assert await redis.dbsize() == 0

    async def test_a_value_that_is_not_an_entry_is_not_one(self, redis) -> None:
        await redis.hset(replay_set.hash_key("rec-1"), "evt-1", "garbage")

        assert await _entry(redis, "rec-1", {"_event_id": "evt-1"}) is None


class TestSwitch:
    @pytest.mark.parametrize("value", ["off", "OFF", "0", "false"])
    def test_replay_set_off_disables_the_guard(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("REPLAY_SET", value)
        assert replay_set.enabled() is False

    def test_on_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLAY_SET", raising=False)
        assert replay_set.enabled() is True


class _Info:
    def __init__(self, policy: str | None, *, fail: bool = False) -> None:
        self._policy, self._fail = policy, fail

    async def info(self, section: str) -> dict:
        assert section == "memory"
        if self._fail:
            raise ConnectionError("down")
        return {"maxmemory_policy": self._policy} if self._policy is not None else {}


class TestEvictionPolicy:
    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        replay_set._eviction_warning = None
        yield
        replay_set._eviction_warning = None

    @pytest.mark.parametrize("policy", ["volatile-lru", "allkeys-lru", "volatile-ttl", "allkeys-random"])
    async def test_a_policy_that_evicts_is_reported(self, policy: str) -> None:
        warning = await replay_set.check_eviction_policy(_Info(policy))

        assert warning is not None and policy in warning
        assert replay_set.eviction_policy_warning() == warning

    async def test_noeviction_is_safe_and_clears_an_earlier_warning(self) -> None:
        await replay_set.check_eviction_policy(_Info("allkeys-lru"))
        assert await replay_set.check_eviction_policy(_Info("noeviction")) is None
        assert replay_set.eviction_policy_warning() is None

    async def test_a_redis_that_cannot_be_read_reports_nothing_new(self) -> None:
        assert await replay_set.check_eviction_policy(_Info(None, fail=True)) is None
        assert await replay_set.check_eviction_policy(_Info(None)) is None


class TestLogPosition:
    @pytest.mark.parametrize(
        ("delivery_id", "position"),
        [
            ("record-events-0-42", ("kafka:record-events-0", (42,))),
            ("t-3-7", ("kafka:t-3", (7,))),
            ("record-events:981", ("pg:record-events", (981,))),
            # Redis Streams: a bare entry id, with no stream; ids of two lane streams are not ordered.
            ("1731650000000-3", None),
            ("", None),
            (None, None),
            ("not-a-position", None),
        ],
    )
    def test_each_consumer_id_names_its_log_and_position(self, delivery_id, position) -> None:
        # The formats stamp_delivery_id receives: Kafka topic-partition-offset and the PostgreSQL queue
        # topic:row id name a position; the bare Redis Streams entry id does not.
        assert replay_set.log_position(delivery_id) == position


class TestTheRecordMustStillShowARememberedCompletion:
    """An entry answers a replay only while the record's last completion is a remembered one it does not postdate."""

    @staticmethod
    def _entries(*rows: tuple[str, str, int]) -> dict[str, replay_set.StoredDelivery]:
        return {
            event_id: replay_set.StoredDelivery(flags="", delivery_id=delivery_id, completed_at_ms=at)
            for event_id, delivery_id, at in rows
        }

    def test_the_delivery_that_made_the_record_answers_its_replay(self) -> None:
        entries = self._entries(("u1", "t-0-10", 1))

        assert replay_set.not_older_than_the_record("u1", entries, 1)
        assert replay_set.not_older_than_the_record("u1", entries, "1"), "a store may hand the timestamp back as text"

    def test_an_earlier_event_of_the_same_log_is_answered_by_a_later_completion(self) -> None:
        entries = self._entries(("u1", "t-0-10", 1), ("u2", "t-0-15", 2))

        assert replay_set.not_older_than_the_record("u1", entries, 2)
        assert replay_set.not_older_than_the_record("u2", entries, 2)

    def test_a_later_event_is_not_answered_by_an_older_one_that_ran_again(self) -> None:
        # U1's entry was lost, so a replay ran U1 again and it completed the record last.
        entries = self._entries(("u2", "t-0-15", 2), ("u1", "t-0-10", 3))

        assert not replay_set.not_older_than_the_record("u2", entries, 3)
        assert replay_set.not_older_than_the_record("u1", entries, 3)

    def test_a_completion_the_set_does_not_know_answers_nothing(self) -> None:
        # The last run's write failed, or its entry was evicted: nothing shows what the record holds.
        entries = self._entries(("u1", "t-0-10", 1), ("u2", "t-0-15", 2))

        assert not replay_set.not_older_than_the_record("u2", entries, 3)
        assert not replay_set.not_older_than_the_record("u2", entries, None)

    def test_positions_in_different_logs_are_not_compared(self) -> None:
        entries = self._entries(("u1", "t-0-10", 1), ("u2", "t-1-15", 2), ("u3", "q:5", 3))

        assert not replay_set.not_older_than_the_record("u1", entries, 2)
        assert not replay_set.not_older_than_the_record("u2", entries, 3)

    def test_an_event_with_no_position_is_only_answered_by_its_own_completion(self) -> None:
        entries = self._entries(("u1", "", 1), ("u2", "t-0-15", 2))

        assert not replay_set.not_older_than_the_record("u1", entries, 2)

    async def test_a_window_longer_than_the_set_runs_from_its_first_forgotten_event(self, redis) -> None:
        # The first five updates were trimmed. Replayed in order, the first of them runs again and
        # completes the record; from then on no remembered update is answered, since each is later.
        total = REPLAY_SET_SIZE + 5
        for n in range(total):
            await _record(redis, "rec-1", n)
        await replay_set.record_delivery(
            redis, "rec-1", event_id="evt-0", delivery_id="t-0-0", flags="", completed_at_ms=5000
        )
        entries = await replay_set.read_entries(redis, "rec-1", {"_event_id": "evt-0"})

        assert all(
            not replay_set.not_older_than_the_record(f"evt-{n}", entries, 5000) for n in range(5, total)
        )

    def test_redis_streams_ids_answer_only_their_own_completion(self) -> None:
        # Two lane streams: 1000-5 on one and 1000-0 on the other say nothing about which came first.
        entries = self._entries(("u1", "1000-0", 1), ("u2", "1000-5", 2))

        assert not replay_set.not_older_than_the_record("u1", entries, 2)
        assert replay_set.not_older_than_the_record("u2", entries, 2)
