"""The indexing-membership result: how it is found, published and read."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.messaging import membership as m

NOW = 1_700_000_000_000


def _entry(checked_at: int = NOW, untagged: list[str] | None = None) -> dict[str, Any]:
    return {"checkedAt": checked_at, "untagged": untagged or [], "groupsChecked": ["g"]}


class TestEvaluate:
    def test_no_entry_or_only_stale_entries_is_stale(self) -> None:
        assert m.evaluate([], NOW) == m.MembershipVerdict(False, (), "stale")
        assert m.evaluate([_entry(NOW - 91_000)], NOW).reason == "stale"
        assert m.evaluate([None, "x", {"checkedAt": "no"}], NOW).reason == "stale"

    def test_a_fresh_clean_entry_is_ok(self) -> None:
        assert m.evaluate([_entry(NOW - 89_000)], NOW).ok

    def test_the_union_over_fresh_entries_so_an_ex_leaders_clean_write_cannot_hide_a_finding(self) -> None:
        """T52′. Control: a single overwritten key would read the later, clean write."""
        entries = [_entry(NOW - 5_000, ["old-pod"]), _entry(NOW - 1_000)]
        verdict = m.evaluate(entries, NOW)
        assert verdict == m.MembershipVerdict(False, ("old-pod",), "untagged")
        last_write_wins = m.evaluate(entries[-1:], NOW)
        assert last_write_wins.ok  # the masked result a single key would give

    def test_a_stale_finding_does_not_count(self) -> None:
        assert m.evaluate([_entry(NOW - 200_000, ["gone"]), _entry(NOW)], NOW).ok


class TestChecks:
    @staticmethod
    def _described(version: int, *groups: tuple[int, str, list[str]]) -> Any:  # noqa: ANN401
        """A real aiokafka 0.14 ``DescribeGroupsResponse_v<N>`` (what ``describe_consumer_groups`` returns)."""
        from aiokafka.protocol import admin

        extra = (0,) if version == 3 else ()  # v3 adds authorized_operations to each group
        rows = [
            (error, name, "Stable", "consumer", "range", [(f"m-{i}", cid, "/127.0.0.1", b"", b"") for i, cid in enumerate(cids)], *extra)
            for error, name, cids in groups
        ]
        response = getattr(admin, f"DescribeGroupsResponse_v{version}")
        if version == 0:
            return response(groups=rows)
        return response(throttle_time_ms=0, groups=rows)

    @staticmethod
    def _admin(described: list[Any]) -> MagicMock:
        admin = MagicMock()
        admin.start, admin.close = AsyncMock(), AsyncMock()
        admin.describe_consumer_groups = AsyncMock(return_value=described)
        return admin

    @pytest.mark.asyncio
    @pytest.mark.parametrize("version", [0, 1, 2, 3])
    async def test_kafka_lists_live_members_without_the_tag(self, version: int) -> None:
        """QA-05cb-1. The input is aiokafka's own response type, not an object with ``members``."""
        admin = self._admin([
            self._described(version, (0, "g1", ["indexing/sp3-backlog", "indexing", "indexing/sp3"])),
            self._described(version, (0, "g2", ["older-pod-1"])),
        ])

        assert await m.kafka_untagged(lambda: admin, ["g1", "g2"]) == ["indexing", "older-pod-1"]
        admin.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kafka_group_error_fails_the_check_rather_than_reporting_clean(self) -> None:
        admin = self._admin([self._described(1, (15, "g1", []))])
        with pytest.raises(RuntimeError, match="g1"):
            await m.kafka_untagged(lambda: admin, ["g1"])
        admin.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_redis_counts_a_consumer_with_pending_entries_or_recent_activity_as_live(self) -> None:
        old = m.REDIS_IDLE_LIVE_MS + 1
        redis = MagicMock()
        redis.xinfo_consumers = AsyncMock(return_value=[
            {"name": "indexing/sp3-abc", "pending": 0, "idle": 0},
            {"name": "old-busy", "pending": 3, "idle": old},
            {"name": "old-parked", "pending": 0, "idle": 1_000},
            {"name": "old-stopped", "pending": 0, "idle": old},
        ])

        assert await m.redis_untagged(redis, {"g": ["s"]}) == ["old-busy", "old-parked"]

    @pytest.mark.asyncio
    async def test_a_stream_without_the_group_has_no_members(self) -> None:
        redis = MagicMock()
        redis.xinfo_consumers = AsyncMock(side_effect=RuntimeError("NOGROUP No such consumer group"))
        assert await m.redis_untagged(redis, {"g": ["s"]}) == []


class TestPublisher:
    @staticmethod
    def _publisher(check: Any, puts: list, **kw: Any) -> m.MembershipPublisher:  # noqa: ANN401
        async def put(key: str, entry: dict, ttl: int) -> None:
            puts.append((key, entry, ttl))

        return m.MembershipPublisher(
            logging.getLogger("t"), check, put, holder="h1", now_ms=lambda: NOW, groups=lambda: {"g": ["t"]}, **kw,
        )

    @pytest.mark.asyncio
    async def test_a_process_with_no_registered_group_publishes_nothing_rather_than_all_clear(self) -> None:
        """QA-05cb-7."""
        puts: list = []
        check = AsyncMock(return_value=[])
        publisher = m.MembershipPublisher(
            logging.getLogger("t"), check, self._put_into(puts), holder="h1", now_ms=lambda: NOW, groups=lambda: {},
        )

        assert await publisher.publish_once() is None
        assert puts == []
        check.assert_not_awaited()

    @staticmethod
    def _put_into(puts: list) -> Any:  # noqa: ANN401
        async def put(key: str, entry: dict, ttl: int) -> None:
            puts.append((key, entry, ttl))

        return put

    @pytest.mark.asyncio
    async def test_it_writes_the_holders_own_key_with_a_ttl_and_the_check_start_time(self) -> None:
        puts: list = []
        await self._publisher(AsyncMock(return_value=["b", "a", "a"]), puts).publish_once()

        assert puts == [(f"{m.MEMBERSHIP_DIRECTORY}h1", {"checkedAt": NOW, "untagged": ["a", "b"], "groupsChecked": ["g"]}, 300)]

    @pytest.mark.asyncio
    async def test_a_failed_check_or_a_non_leader_writes_nothing(self) -> None:
        puts: list = []
        assert await self._publisher(AsyncMock(side_effect=RuntimeError("down")), puts).publish_once() is None
        assert await self._publisher(AsyncMock(return_value=[]), puts, is_leader=AsyncMock(return_value=False)).publish_once() is None
        assert puts == []

    @pytest.mark.asyncio
    async def test_the_timer_keeps_writing_while_something_else_is_slow(self) -> None:
        """T55. The publisher has no dependency on a lap or a sweep: with a slow 'sweep' running beside it, the entry advances every period."""
        stamps: list[int] = []
        clock = {"now": NOW}

        async def put(key: str, entry: dict, ttl: int) -> None:
            stamps.append(entry["checkedAt"])

        publisher = m.MembershipPublisher(
            logging.getLogger("t"), AsyncMock(return_value=[]), put, now_ms=lambda: clock["now"], groups=lambda: {"g": ["t"]},
        )
        slow_sweep = asyncio.create_task(asyncio.sleep(0.3))
        task = asyncio.create_task(publisher.run(period_seconds=0.02))
        for _ in range(3):
            await asyncio.sleep(0.05)
            clock["now"] += 30_000
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await slow_sweep

        assert len(stamps) >= 3 and stamps == sorted(stamps) and len(set(stamps)) >= 3


class TestPublisherFailures:
    @pytest.mark.asyncio
    async def test_a_failed_store_is_logged_and_the_loop_goes_on(self, caplog: pytest.LogCaptureFixture) -> None:
        """An entry that cannot be stored leaves readers holding: the log says why, the loop keeps trying."""
        calls = 0

        async def put(key: str, entry: dict, ttl: int) -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("the membership entry was not stored")

        publisher = m.MembershipPublisher(
            logging.getLogger("t"), AsyncMock(return_value=[]), put, now_ms=lambda: NOW, groups=lambda: {"g": ["t"]},
        )
        with caplog.at_level(logging.WARNING, logger="t"):
            task = asyncio.create_task(publisher.run(period_seconds=0.01))
            await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert calls >= 2
        assert any("publish failed" in r.message and "was not stored" in r.message for r in caplog.records)


class TestReader:
    @pytest.mark.asyncio
    async def test_it_caches_the_verdict_not_the_entries(self) -> None:
        reads = AsyncMock(return_value=[_entry(NOW)])
        t = {"now": 0.0}
        reader = m.MembershipReader(reads, now_ms=lambda: NOW, monotonic=lambda: t["now"])

        assert (await reader.verdict()).ok and (await reader.verdict()).ok
        assert reads.await_count == 1
        t["now"] = 31.0
        assert (await reader.verdict()).ok
        assert reads.await_count == 2

    @pytest.mark.asyncio
    async def test_an_unreadable_directory_is_stale(self) -> None:
        reader = m.MembershipReader(AsyncMock(side_effect=RuntimeError("kv down")), now_ms=lambda: NOW)
        assert (await reader.verdict()).reason == "stale"


class TestPlaintextEntries:
    """T52. The entries must be readable by Node, which does not decrypt: the encrypted store keeps
    them plaintext (the round trip through the real store is in tests/unit/config/test_encrypted_store_behaviour.py)."""

    @pytest.mark.asyncio
    async def test_the_directory_is_stored_unencrypted_and_other_keys_are_not(self) -> None:
        from app.config.providers.encrypted_store import is_unencrypted_key

        assert is_unencrypted_key(f"{m.MEMBERSHIP_DIRECTORY}holder-1")
        assert not is_unencrypted_key("/services/platform/settings")
        assert not is_unencrypted_key("/services/platform/artifactRollout/membership")
