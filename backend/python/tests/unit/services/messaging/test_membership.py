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
    return {"checkedAt": checked_at, "untagged": untagged or [], "groupsChecked": ["g"], "groupsExpected": ["g"]}


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

    def test_a_clean_entry_proves_only_when_it_checked_every_group_it_was_configured_to_expect(self) -> None:
        """A publisher that checked only the groups registered so far (a stage consumer starts later) proves nothing about
        the rest; nor does one that does not say what it expected (an older publisher)."""
        partial = {"checkedAt": NOW, "untagged": [], "groupsChecked": ["records"], "groupsExpected": ["records", "pipeline_x"]}
        unstated = {"checkedAt": NOW, "untagged": [], "groupsChecked": ["records"]}
        empty = {"checkedAt": NOW, "untagged": [], "groupsChecked": [], "groupsExpected": []}
        full = {"checkedAt": NOW, "untagged": [], "groupsChecked": ["pipeline_x", "records"], "groupsExpected": ["records", "pipeline_x"]}

        for entry in (partial, unstated, empty):
            assert m.evaluate([entry], NOW) == m.MembershipVerdict(False, (), "stale"), entry
        assert m.evaluate([full], NOW).ok
        assert m.evaluate([{**unstated, "untagged": ["old-pod"]}, full], NOW).untagged == ("old-pod",)  # a finding counts all the same

    def test_a_group_one_fresh_entry_expects_must_be_checked_by_some_fresh_entry(self) -> None:
        """A leader handover: the ex-leader's entry, still fresh, proves the groups it expected; the new leader expects a
        stage group more and has not covered it. No fresh entry checked that group, so the result is not clean."""
        ex_leader = {"checkedAt": NOW - 60_000, "untagged": [], "groupsChecked": ["records"], "groupsExpected": ["records"]}
        new_leader = {
            "checkedAt": NOW - 1_000, "untagged": [], "groupsChecked": ["records"], "groupsExpected": ["pipeline_x", "records"],
        }
        covered = {**new_leader, "groupsChecked": ["pipeline_x", "records"]}

        assert m.evaluate([ex_leader, new_leader], NOW) == m.MembershipVerdict(False, (), "stale")
        assert m.evaluate([ex_leader, covered], NOW).ok


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
    async def test_kafka_describes_one_group_per_request(self) -> None:
        """aiokafka 0.14 reads a v3 DescribeGroups answer with the v2 layout (no authorized_operations), so every group after
        the first in one answer comes back shifted (error -32768, an empty name)."""
        admin = self._admin([])
        admin.describe_consumer_groups = AsyncMock(
            side_effect=lambda groups: [self._described(1, (0, groups[0], [f"old-{groups[0]}"]))] if len(groups) == 1 else
            [self._described(1, (0, groups[0], []), (-32768, "", []))],
        )

        assert await m.kafka_untagged(lambda: admin, ["g1", "g2"]) == ["old-g1", "old-g2"]
        assert [c.args[0] for c in admin.describe_consumer_groups.await_args_list] == [["g1"], ["g2"]]

    @pytest.mark.asyncio
    async def test_kafka_a_group_that_does_not_exist_yet_has_no_members(self) -> None:
        """A stage group whose consumers have not started anywhere: GROUP_ID_NOT_FOUND (69), not a failed check."""
        admin = self._admin([self._described(1, (69, "pipeline_x", []), (0, "g1", ["older-pod-1"]))])
        assert await m.kafka_untagged(lambda: admin, ["pipeline_x", "g1"]) == ["older-pod-1"]

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

    def test_a_postgres_indexing_consumer_registers_its_group_and_claims_under_the_tag(self) -> None:
        from app.services.messaging.config import RedisStreamsConfig
        from app.services.messaging.postgres.consumer import PgMessagingConsumer
        from app.services.messaging.storage_protocol import is_tagged

        m.reset_registered_groups()
        try:
            cfg = RedisStreamsConfig(topics=["record-events"], group_id="records_group", client_id="records")
            indexing = PgMessagingConsumer(logging.getLogger("t"), cfg, indexing=True, queue=MagicMock())
            simple = PgMessagingConsumer(
                logging.getLogger("t"),
                RedisStreamsConfig(topics=["entity-events"], group_id="entity_group", client_id="entity"),
                queue=MagicMock(),
            )

            assert m.registered_groups() == {"records_group": ["record-events"]}
            assert is_tagged(indexing.consumer_name) and not is_tagged(simple.consumer_name)
        finally:
            m.reset_registered_groups()


class TestPublisher:
    @staticmethod
    def _publisher(check: Any, puts: list, **kw: Any) -> m.MembershipPublisher:  # noqa: ANN401
        async def put(key: str, entry: dict, ttl: int) -> None:
            puts.append((key, entry, ttl))

        return m.MembershipPublisher(
            logging.getLogger("t"), check, put, holder="h1", now_ms=lambda: NOW,
            groups=lambda: {"g": ["t"]}, expected=lambda: {"g": ["t"]}, **kw,
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

        assert puts == [(f"{m.MEMBERSHIP_DIRECTORY}h1", {"checkedAt": NOW, "untagged": ["a", "b"], "groupsChecked": ["g"], "groupsExpected": ["g"]}, 300)]

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
            logging.getLogger("t"), AsyncMock(return_value=[]), put, now_ms=lambda: clock["now"], groups=lambda: {"g": ["t"]}, expected=lambda: {"g": ["t"]},
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
            logging.getLogger("t"), AsyncMock(return_value=[]), put, now_ms=lambda: NOW, groups=lambda: {"g": ["t"]}, expected=lambda: {"g": ["t"]},
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


class TestExpectedGroups:
    """The groups a clean entry covers are the ones this process is configured to run, started or not: a stage consumer
    whose topics are missing starts later, and an older member of its group must still be found."""

    RECORDS = {"records_consumer_group": ["record-events"]}
    EXPECTED = {**RECORDS, "pipeline_classify_group": ["pipeline-classify"]}

    @pytest.fixture(autouse=True)
    def _clean(self) -> Any:  # noqa: ANN401
        m.reset_registered_groups()
        yield
        m.reset_registered_groups()

    @staticmethod
    def _broker_check(broker: str) -> Any:  # noqa: ANN401
        """The broker's own check, with a live member without the tag in the stage group only."""
        if broker == "kafka":
            def described(groups: list[str]) -> list[Any]:
                members = {"records_consumer_group": ["records/sp3-1"], "pipeline_classify_group": ["old-stage"]}
                return [TestChecks._described(1, *[(0, g, members[g]) for g in groups])]

            admin = MagicMock()
            admin.start, admin.close = AsyncMock(), AsyncMock()
            admin.describe_consumer_groups = AsyncMock(side_effect=described)
            return lambda groups: m.kafka_untagged(lambda: admin, list(groups))
        redis = MagicMock()
        consumers = {"record-events": [{"name": "records/sp3-1", "pending": 0, "idle": 0}],
                     "pipeline-classify": [{"name": "old-stage", "pending": 1, "idle": 0}]}
        redis.xinfo_consumers = AsyncMock(side_effect=lambda stream, group: consumers[stream])
        return lambda groups: m.redis_untagged(redis, groups)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("broker", ["kafka", "redis"])
    async def test_a_stage_group_that_registers_late_is_checked_and_its_older_member_found(self, broker: str) -> None:
        m.register_group("records_consumer_group", ["record-events"])     # the record consumer has started; the stage one has not
        m.expect_groups(self.EXPECTED)
        puts: list = []
        publisher = m.MembershipPublisher(
            logging.getLogger("t"), self._broker_check(broker), TestPublisher._put_into(puts), holder="h1", now_ms=lambda: NOW,
        )

        await publisher.publish_once()

        entry = puts[0][1]
        assert entry["groupsChecked"] == entry["groupsExpected"] == sorted(self.EXPECTED)
        assert m.evaluate([entry], NOW) == m.MembershipVerdict(False, ("old-stage",), "untagged")

    @pytest.mark.asyncio
    async def test_nothing_is_published_before_the_expected_groups_are_declared(self) -> None:
        m.register_group("records_consumer_group", ["record-events"])
        puts: list = []
        check = AsyncMock(return_value=[])
        publisher = m.MembershipPublisher(logging.getLogger("t"), check, TestPublisher._put_into(puts), holder="h1", now_ms=lambda: NOW)

        assert await publisher.publish_once() is None
        assert puts == []
        check.assert_not_awaited()
