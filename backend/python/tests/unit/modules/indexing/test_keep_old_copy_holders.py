"""The holder predicate of a record's pending virtual record, and the switches of the keep-old-copy swap."""
import pytest

from app.modules.indexing import keep_old_copy
from app.plugins.telemetry.modules import indexing_metrics
from app.services.graph_db.common.record_visibility import (
    aql_holds_vrid,
    cypher_holds_vrid,
    holds_vrid,
    sql_holds_vrid,
)


def test_a_record_holds_the_vrid_it_names_whatever_its_status() -> None:
    assert holds_vrid({"virtualRecordId": "x", "indexingStatus": "FAILED"}, "x")


@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED", "NOT_STARTED"])
def test_a_running_or_waiting_record_holds_its_pending_vrid(status) -> None:
    assert holds_vrid({"virtualRecordId": "x", "pendingVirtualRecordId": "y", "indexingStatus": status}, "y")


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "EMPTY", "FILE_TYPE_NOT_SUPPORTED", "AUTO_INDEX_OFF"])
def test_any_other_status_releases_the_pending_vrid(status) -> None:
    assert not holds_vrid({"virtualRecordId": "x", "pendingVirtualRecordId": "y", "indexingStatus": status}, "y")


def test_the_three_store_fragments_carry_the_same_guard_and_statuses() -> None:
    aql, cy, sql = aql_holds_vrid("r", "vid"), cypher_holds_vrid("r", "vid"), sql_holds_vrid("n", "w.v")
    for fragment in (aql, cy, sql):
        assert "pendingVirtualRecordId" in fragment
        for status in ("IN_PROGRESS", "QUEUED", "NOT_STARTED"):
            assert status in fragment
    assert "pendingVirtualRecordId != null" in aql  # the sparse index serves correlated forms only with the guard
    assert "doc ? 'pendingVirtualRecordId'" in sql  # the partial index predicate is repeated


def test_the_flag_is_off_by_default(monkeypatch) -> None:
    monkeypatch.delenv(keep_old_copy.ENV_ENABLED, raising=False)
    assert not keep_old_copy.enabled()
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    assert keep_old_copy.enabled()


@pytest.mark.asyncio
async def test_the_swap_is_refused_without_a_confirmed_fleet(monkeypatch) -> None:
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    assert not await keep_old_copy.swap_allowed(None)


def test_the_grace_follows_the_cache_ttl(monkeypatch) -> None:
    monkeypatch.delenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", raising=False)
    monkeypatch.delenv("REDIS_MODE", raising=False)
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    assert keep_old_copy.grace_seconds() == 330.0
    monkeypatch.setenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", "100")
    assert keep_old_copy.grace_seconds() == 130.0


def test_the_postgres_statements_carry_the_shared_fragment_verbatim() -> None:
    """The provider's statements are literals (a test forbids building them), so the fragment is repeated there: it must not drift."""
    from pathlib import Path

    text = Path("app/services/graph_db/postgres/records.py").read_text(encoding="utf-8")
    assert text.count(sql_holds_vrid("n", "$2")) == 1
    assert text.count(sql_holds_vrid("n", "w.v")) == 2


@pytest.mark.asyncio
async def test_the_postgres_broker_needs_the_operators_statement_and_no_older_claimant(monkeypatch) -> None:
    """Each refusal says why (the counter label), so a test that only saw a refusal could not tell the operator's statement from a failed read."""
    from unittest.mock import AsyncMock, patch

    from app.services.messaging.config import MessageBrokerType

    reasons: list[str] = []
    monkeypatch.setattr(indexing_metrics.SWAP_REFUSALS, "inc", lambda reason: reasons.append(reason))
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    monkeypatch.setattr("app.services.messaging.config.get_message_broker_type", lambda: MessageBrokerType.POSTGRES)
    monkeypatch.setattr("app.services.messaging.membership.groups_to_check", lambda: {"records_consumer_group": ["record-events"]})
    monkeypatch.delenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED, raising=False)
    assert not await keep_old_copy.swap_allowed(None)
    assert reasons == ["pg_broker_unconfirmed"]  # no statement: refused, before any claim is read
    monkeypatch.setenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED, "on")
    with patch("app.services.messaging.postgres.fleet_claims.untagged_claimants", AsyncMock(return_value=["old-pod"])) as claimants:
        assert not await keep_old_copy.swap_allowed(None)
    claimants.assert_awaited_once()
    assert claimants.await_args.args[1] == ["records_consumer_group"]  # only the indexing groups are asked about
    assert reasons[-1] == "older_consumer"
    with patch("app.services.messaging.postgres.fleet_claims.untagged_claimants", AsyncMock(side_effect=RuntimeError("down"))):
        assert not await keep_old_copy.swap_allowed(None)
    assert reasons[-1] == "pg_broker_unreadable"
    with patch("app.services.messaging.postgres.fleet_claims.untagged_claimants", AsyncMock(return_value=[])):
        assert await keep_old_copy.swap_allowed(None)
    monkeypatch.setattr("app.services.messaging.membership.groups_to_check", lambda: {})
    assert not await keep_old_copy.swap_allowed(None)
    assert reasons[-1] == "indexing_groups_unknown"


@pytest.mark.asyncio
async def test_the_swap_asks_for_the_strict_verdict_that_counts_idle_consumers(monkeypatch) -> None:
    """A lenient verdict (idle consumers not counted) must not stand in for the strict one."""
    from unittest.mock import AsyncMock, MagicMock

    from app.services.messaging.membership import MembershipVerdict

    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    monkeypatch.setattr("app.services.messaging.config.get_message_broker_type", lambda: __import__("app.services.messaging.config", fromlist=["x"]).MessageBrokerType.REDIS)
    reader = MagicMock()
    reader.verdict = AsyncMock(return_value=MembershipVerdict(True))  # what a lenient check would say
    reader.strict_verdict = AsyncMock(return_value=MembershipVerdict(False, ("idle-old-pod",), "untagged"))
    monkeypatch.setattr("app.services.messaging.membership.reader_for", lambda _c: reader)
    assert not await keep_old_copy.swap_allowed(MagicMock())
    reader.strict_verdict.assert_awaited_once()
    reader.verdict.assert_not_called()


@pytest.mark.asyncio
async def test_the_membership_publisher_measures_idle_consumers_too() -> None:
    """The publisher of ``indexing_main`` passes a second check that counts every consumer as live, and its entry carries the result."""
    import inspect

    from app import indexing_main
    from app.services.messaging import membership

    source = inspect.getsource(indexing_main.run_membership_publisher)
    assert "check_any=check_any" in source and "idle_live_ms=None" in source
    sig = inspect.signature(membership.MembershipPublisher.__init__)
    assert "check_any" in sig.parameters


@pytest.mark.asyncio
async def test_a_refusal_is_logged_once_not_per_run(monkeypatch, caplog) -> None:
    import logging

    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    keep_old_copy._refusals_logged.clear()
    with caplog.at_level(logging.WARNING, logger="keep_old_copy"):
        for _ in range(5):
            await keep_old_copy.swap_allowed(None)
    assert sum("Keep-old-copy swap refused" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_a_record_that_is_not_indexed_is_left_to_a_later_pass_by_the_named_entity_completion() -> None:
    """A record replacing its copy (or that failed doing so) is tried again later: its revision is the attempt's, not the served copy's."""
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.indexing.named_entity_completion import _complete_one

    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"virtualRecordId": "x", "indexingStatus": "IN_PROGRESS", "orgId": "o"})
    assert await _complete_one(graph, MagicMock(), MagicMock(), "r1", "o", 0, 0) == "not_indexed"


def test_idle_consumers_count_as_live_for_the_strict_fleet_verdict() -> None:
    from app.services.messaging.membership import evaluate_strict

    fresh = {"checkedAt": 1000, "untagged": [], "groupsChecked": ["g"], "groupsExpected": ["g"]}
    # an entry that did not measure idle members proves nothing
    assert evaluate_strict([fresh], 1000).reason == "stale"
    assert evaluate_strict([{**fresh, "untaggedAny": []}], 1000).ok
    verdict = evaluate_strict([{**fresh, "untaggedAny": ["old-pod"]}], 1000)
    assert not verdict.ok and verdict.untagged == ("old-pod",) and verdict.reason == "untagged"


@pytest.mark.asyncio
async def test_a_redis_consumer_idle_for_hours_is_live_when_idle_ones_count() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.messaging.membership import redis_untagged

    redis = MagicMock()
    redis.xinfo_consumers = AsyncMock(return_value=[{"name": "old-pod", "pending": 0, "idle": 3 * 3600 * 1000}])
    assert await redis_untagged(redis, {"g": ["s"]}) == []
    assert await redis_untagged(redis, {"g": ["s"]}, idle_live_ms=None) == ["old-pod"]


@pytest.mark.asyncio
async def test_a_record_failing_for_good_releases_its_pending_copy_through_the_handler() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    handler = RecordEventHandler.__new__(RecordEventHandler)
    handler.logger = MagicMock()
    swap = MagicMock()
    swap.release_failed = AsyncMock()
    handler.event_processor = MagicMock()
    handler.event_processor.processor.sink_orchestrator.swap = swap
    await handler._release_pending_copy("r1", "o1")
    swap.release_failed.assert_awaited_once_with("r1", "o1", None)
    swap.release_failed = AsyncMock(side_effect=RuntimeError("down"))
    await handler._release_pending_copy("r1", "o1")  # logged, never raised: the orphan sweep reclaims the copy
    handler.logger.warning.assert_called()


@pytest.mark.asyncio
async def test_the_publisher_writes_what_the_idle_inclusive_check_found() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.services.messaging.membership import MembershipPublisher

    put = AsyncMock()
    publisher = MembershipPublisher(
        MagicMock(), AsyncMock(return_value=[]), put, check_any=AsyncMock(return_value=["idle-old-pod"]),
        groups=lambda: {"g": ["t"]}, expected=lambda: {"g": ["t"]},
    )
    await publisher.publish_once()
    entry = put.await_args.args[1]
    assert entry["untagged"] == [] and entry["untaggedAny"] == ["idle-old-pod"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED", "FAILED", "NOT_STARTED"])
async def test_the_reextract_script_leaves_a_record_that_is_not_indexed_alone(status: str) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.scripts.named_entity_reextract import Tally, _wants_reextract

    blob = MagicMock()
    blob.get_record_from_storage = AsyncMock()
    record = {"virtualRecordId": "x", "indexingStatus": status, "orgId": "o"}
    assert not await _wants_reextract(record, MagicMock(), blob, Tally(), everything=True)
    blob.get_record_from_storage.assert_not_called()
