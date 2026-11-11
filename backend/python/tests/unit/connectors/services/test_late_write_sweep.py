"""The late-write sweep removes what indexing writes for a connector after its delete, until it stops."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.services.late_write_sweep import (
    GIVE_UP_MS,
    GRACE_MS,
    PENDING_DIRECTORY,
    QUIET_MS,
    RETRY_CAP_MS,
    RETRY_MS,
    STALE_MS,
    LateWriteSweeper,
    LateWriteSweepIntentError,
    list_pending_late_write_sweeps,
    record_pending_late_write_sweep,
    retry_delay_ms,
)
from app.core.org_context import current_org_id

NOW = 30 * 24 * 60 * 60 * 1000
ORG, CONNECTOR = "org1", "conn1"
KEY = f"{PENDING_DIRECTORY}{CONNECTOR}"


class Config:
    """The KV store as the connectors service sees it: full keys listed, a missing key's delete refused."""

    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.read_error: Exception | None = None

    async def get_config(self, key, default=None, use_cache=True, *, raise_on_error=False, **_kw) -> object:
        if self.read_error is not None:
            # As ConfigurationService: a store failure reads as the default unless asked to raise.
            if raise_on_error:
                raise self.read_error
            return default
        return self.kv.get(key, default)

    async def set_config(self, key, value, **_kw) -> bool:
        self.kv[key] = value
        return True

    async def delete_config(self, key) -> bool:
        return self.kv.pop(key, None) is not None

    async def list_keys_in_directory(self, directory) -> list[str]:
        return [k for k in self.kv if k.startswith(directory)]


class Graph:
    """``passes`` are what each sweep pass returns, (removed, left), or an exception to raise."""

    def __init__(self, app: dict | None = None, passes: list | None = None, groups: list | Exception = ()) -> None:
        self.app = app
        self.passes = list(passes or [])
        self.groups = groups
        self.swept_in: list[str | None] = []
        self.swept_groups: list[list[str]] = []
        self.read_in: list[str | None] = []

    async def get_document(self, key, collection, **_kw) -> object:
        self.read_in.append(current_org_id())
        if isinstance(self.app, Exception):
            raise self.app
        return self.app

    async def get_nodes_by_filters(self, collection, filters, return_fields=None, transaction=None, **_kw) -> list:
        assert collection == "recordGroups" and filters == {"connectorId": CONNECTOR}
        if isinstance(self.groups, Exception):
            raise self.groups
        return [{"_key": g} for g in self.groups]

    async def sweep_deleted_connector_writes(self, connector_id: str, record_group_ids=()) -> tuple[int, int]:
        assert connector_id == CONNECTOR
        self.swept_in.append(current_org_id())
        self.swept_groups.append(list(record_group_ids))
        result = self.passes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def sweeper(graph: Graph, config: Config, *, now: int = NOW, leader: bool = True) -> LateWriteSweeper:
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=leader))
    return LateWriteSweeper(logger=MagicMock(), graph_provider=graph, config_service=config, lock=lock, now_ms=lambda: now)


async def recorded(*, age_ms: int, **fields: object) -> Config:
    config = Config()
    await record_pending_late_write_sweep(config, Graph(), org_id=ORG, connector_id=CONNECTOR, now_ms=NOW - age_ms)
    config.kv[KEY] = {**config.kv[KEY], **fields}
    return config


def intent(config: Config) -> dict | None:
    return config.kv.get(KEY)  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_within_the_grace_the_delete_is_left_to_sweep_for_itself() -> None:
    config, graph = await recorded(age_ms=GRACE_MS - 1), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {}
    assert graph.swept_in == []


@pytest.mark.asyncio
async def test_rows_written_after_the_delete_are_removed_and_the_watch_goes_on() -> None:
    config, graph = await recorded(age_ms=GRACE_MS + 1), Graph(passes=[(3, 0)])
    s = sweeper(graph, config)

    assert await s.tick() == {"swept": 1}

    assert graph.swept_in == [ORG]  # the pass runs in the intent's org
    kept = intent(config)
    assert kept["lastFoundAt"] == NOW and kept["attempts"] == 1 and kept["nextAttemptAt"] == NOW + RETRY_MS
    s.logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_every_pass_sweeps_the_groups_read_before_the_delete() -> None:
    """Links written late carry a record group and no connector; the groups are gone after the delete."""
    config = Config()
    await record_pending_late_write_sweep(
        config, Graph(groups=["rg1", "", "rg2", "rg1"]), org_id=ORG, connector_id=CONNECTOR,
        now_ms=NOW - GRACE_MS - 1,
    )
    graph = Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"swept": 1}
    assert graph.swept_groups == [["rg1", "rg2"]]


@pytest.mark.asyncio
async def test_a_retried_delete_keeps_the_groups_the_first_attempt_read() -> None:
    """The first attempt may have removed some groups before it failed; their links still need sweeping."""
    config = Config()
    await record_pending_late_write_sweep(config, Graph(groups=["rg1", "rg2"]), org_id=ORG, connector_id=CONNECTOR)
    await record_pending_late_write_sweep(config, Graph(groups=["rg2"]), org_id=ORG, connector_id=CONNECTOR)
    assert intent(config)["recordGroupIds"] == ["rg1", "rg2"]


@pytest.mark.asyncio
async def test_an_earlier_intent_that_cannot_be_read_refuses_the_delete() -> None:
    """Rewritten from an unknown earlier value, the intent would drop the groups the first attempt saved."""
    config = Config()
    await record_pending_late_write_sweep(config, Graph(groups=["rg1"]), org_id=ORG, connector_id=CONNECTOR)
    config.read_error = ConnectionError("store down")
    with pytest.raises(LateWriteSweepIntentError):
        await record_pending_late_write_sweep(config, Graph(), org_id=ORG, connector_id=CONNECTOR)
    assert intent(config)["recordGroupIds"] == ["rg1"]


@pytest.mark.asyncio
async def test_groups_that_cannot_be_read_refuse_the_delete() -> None:
    config = Config()
    with pytest.raises(LateWriteSweepIntentError):
        await record_pending_late_write_sweep(config, Graph(groups=RuntimeError("timeout")), org_id=ORG, connector_id=CONNECTOR)
    assert intent(config) is None


@pytest.mark.asyncio
async def test_a_pass_that_removes_only_links_restarts_the_quiet_clock() -> None:
    config = await recorded(age_ms=QUIET_MS * 2, recordGroupIds=["rg1"])
    assert await sweeper(Graph(passes=[(1, 0)]), config).tick() == {"swept": 1}
    assert intent(config)["lastFoundAt"] == NOW


@pytest.mark.asyncio
async def test_a_zero_count_alone_does_not_end_the_watch() -> None:
    """A writer from before the delete may still commit: the intent stays until QUIET_MS without writes."""
    config, graph = await recorded(age_ms=QUIET_MS - 1), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"swept": 1}
    assert intent(config) is not None


@pytest.mark.asyncio
async def test_the_watch_ends_once_nothing_has_been_written_for_the_quiet_period() -> None:
    config, graph = await recorded(age_ms=QUIET_MS), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"cleared": 1}
    assert await list_pending_late_write_sweeps(config) == []


@pytest.mark.asyncio
async def test_the_quiet_period_counts_from_the_last_rows_found() -> None:
    config = await recorded(age_ms=QUIET_MS + 10, lastFoundAt=NOW - QUIET_MS + 1)
    assert await sweeper(Graph(passes=[(0, 0)]), config).tick() == {"swept": 1}
    assert intent(config) is not None


@pytest.mark.asyncio
async def test_rows_left_after_the_pass_keep_the_watch_whatever_its_age() -> None:
    config = await recorded(age_ms=QUIET_MS * 2)
    assert await sweeper(Graph(passes=[(5, 2)]), config).tick() == {"swept": 1}
    assert intent(config)["lastFoundAt"] == NOW


@pytest.mark.asyncio
async def test_the_watch_survives_a_restart() -> None:
    """The intent is all the state there is: a new process picks the watch up where it was."""
    config = await recorded(age_ms=GRACE_MS + 1)
    assert await sweeper(Graph(passes=[(4, 0)]), config).tick() == {"swept": 1}

    later = NOW + QUIET_MS
    assert await sweeper(Graph(passes=[(0, 0)]), config, now=later).tick() == {"cleared": 1}
    assert intent(config) is None


@pytest.mark.asyncio
async def test_a_pass_not_due_yet_waits() -> None:
    config, graph = await recorded(age_ms=GRACE_MS + 1, nextAttemptAt=NOW + 1), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {}
    assert graph.swept_in == []


@pytest.mark.asyncio
async def test_a_failed_pass_is_retried_with_backoff() -> None:
    config = await recorded(age_ms=GRACE_MS + 1, attempts=2)
    assert await sweeper(Graph(passes=[RuntimeError("store down")]), config).tick() == {"retrying": 1}
    kept = intent(config)
    assert kept["attempts"] == 3 and kept["nextAttemptAt"] == NOW + retry_delay_ms(2)


@pytest.mark.asyncio
async def test_a_failed_pass_is_not_a_quiet_one() -> None:
    """A write conflict raises on Arango, so the pass that fails is the sign a writer is still there."""
    config = await recorded(age_ms=QUIET_MS - 1)
    assert await sweeper(Graph(passes=[RuntimeError("write-write conflict")]), config).tick() == {"retrying": 1}
    assert intent(config)["lastFoundAt"] == NOW

    due = intent(config)["nextAttemptAt"]
    assert await sweeper(Graph(passes=[(0, 0)]), config, now=due).tick() == {"swept": 1}
    kept = intent(config)
    assert kept is not None and kept["lastFoundAt"] == NOW


@pytest.mark.asyncio
async def test_the_app_is_read_in_the_intents_org() -> None:
    """The loop runs with no ambient org; a store that scopes every read by org refuses one outside it."""
    config, graph = await recorded(age_ms=GRACE_MS + 1), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"swept": 1}
    assert graph.read_in == [ORG]


@pytest.mark.asyncio
async def test_passes_that_fail_for_a_week_are_given_up_on() -> None:
    """A pass that always raises restarts the quiet clock each time, so only the age of the delete can end it."""
    config = await recorded(age_ms=GIVE_UP_MS, lastFoundAt=NOW - 1)
    s = sweeper(Graph(passes=[RuntimeError("not implemented on this store")]), config)
    assert await s.tick() == {"given_up": 1}
    assert intent(config) is None
    s.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_a_failed_app_read_never_passes_for_a_gone_app() -> None:
    config, graph = await recorded(age_ms=GRACE_MS + 1), Graph(app=RuntimeError("timeout"), passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"retrying": 1}
    assert graph.swept_in == []


@pytest.mark.asyncio
async def test_a_connector_that_still_exists_is_never_swept() -> None:
    config, graph = await recorded(age_ms=STALE_MS - 1), Graph(app={"_key": CONNECTOR}, passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {}
    assert graph.swept_in == [] and intent(config) is not None


@pytest.mark.asyncio
async def test_the_intent_of_a_delete_that_was_undone_is_dropped_once_stale() -> None:
    config, graph = await recorded(age_ms=STALE_MS), Graph(app={"_key": CONNECTOR}, passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {"dropped": 1}
    assert graph.swept_in == [] and intent(config) is None


@pytest.mark.asyncio
async def test_a_connector_still_being_deleted_keeps_its_intent() -> None:
    """A KB stuck DELETING is finished by the storage release; its writes are swept after."""
    config = await recorded(age_ms=STALE_MS * 3)
    graph = Graph(app={"_key": CONNECTOR, "status": "DELETING"}, passes=[(0, 0)])
    assert await sweeper(graph, config).tick() == {}
    assert graph.swept_in == [] and intent(config) is not None


@pytest.mark.asyncio
async def test_writes_that_never_stop_are_given_up_on_after_a_week() -> None:
    config = await recorded(age_ms=GIVE_UP_MS)
    s = sweeper(Graph(passes=[(2, 1)]), config)
    assert await s.tick() == {"given_up": 1}
    assert intent(config) is None
    s.logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_another_replica_holding_the_lease_sweeps_instead() -> None:
    config, graph = await recorded(age_ms=QUIET_MS), Graph(passes=[(0, 0)])
    assert await sweeper(graph, config, leader=False).tick() == {"not_leader": 1}
    assert graph.swept_in == []


def test_passes_back_off_to_an_hour() -> None:
    assert [retry_delay_ms(n) for n in range(6)] == [RETRY_MS * 2 ** n for n in range(4)] + [RETRY_CAP_MS] * 2
