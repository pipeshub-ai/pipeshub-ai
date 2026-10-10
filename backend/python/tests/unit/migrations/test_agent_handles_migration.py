import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations import agent_handles_migration as migration
from app.migrations.agent_handles_migration import (
    AgentHandlesMigrationService,
    run_agent_handles_migration,
)
from tests.support.agent_routes import AGENTS, InMemoryGraph

FLAG = "/migrations/agent_handles_v1"


def _config(flag: dict | None = None) -> MagicMock:
    config = MagicMock()
    config.get_config = AsyncMock(return_value=flag)
    config.set_config = AsyncMock()
    return config


def _graph(*names: str, owner: str = "alice", **fields) -> InMemoryGraph:
    graph = InMemoryGraph()
    for i, name in enumerate(names):
        graph.add_agent(f"a{i}", owner, name=name, createdAtTimestamp=1000 + i, **fields)
    return graph


def _handles(graph: InMemoryGraph) -> dict[str, str | None]:
    return {k: a.get("handle") for k, a in graph.nodes[AGENTS].items()}


def _run(graph: InMemoryGraph, config: MagicMock | None = None) -> tuple[AgentHandlesMigrationService, MagicMock]:
    config = config or _config()
    return AgentHandlesMigrationService(graph, config, logging.getLogger("test.migration")), config  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_backfills_handles_and_org_then_sets_the_flag() -> None:
    graph = _graph("Sales Bot", "sales bot", "Assistant")
    service, config = _run(graph)

    result = await service.migrate()

    assert result == {"success": True, "agents_updated": 3, "agents_failed": 0}
    assert _handles(graph) == {"a0": "sales-bot", "a1": "sales-bot-2", "a2": "assistant-agent"}
    assert {a["orgId"] for a in graph.nodes[AGENTS].values()} == {"org-1"}
    key, value = config.set_config.await_args.args
    assert key == FLAG and value["done"] is True and value["agents_updated"] == 3


@pytest.mark.asyncio
async def test_second_run_is_skipped_by_the_flag() -> None:
    graph = _graph("Sales Bot")
    service, _ = _run(graph, _config({"done": True}))

    result = await service.migrate()

    assert result["skipped"] is True
    assert graph.calls_to("update_node") == [] and graph.calls_to("list_agents_missing_handle") == []


@pytest.mark.asyncio
async def test_rerun_without_the_flag_writes_nothing() -> None:
    graph = _graph("Sales Bot", "sales bot")
    await _run(graph)[0].migrate()
    graph.calls.clear()

    result = await _run(graph)[0].migrate()

    assert result["agents_updated"] == 0 and graph.calls_to("update_node") == []
    assert _handles(graph) == {"a0": "sales-bot", "a1": "sales-bot-2"}


@pytest.mark.asyncio
async def test_resumes_after_a_partial_batch_without_touching_finished_agents() -> None:
    graph = _graph("One", "Two", "Three")
    real = graph.update_node

    async def flaky(key, collection, updates, transaction=None) -> bool:
        if key == "a2":
            raise RuntimeError("connection reset")
        return await real(key, collection, updates, transaction)

    graph.update_node = flaky  # type: ignore[method-assign]
    service, config = _run(graph)

    first = await service.migrate()

    assert first["success"] is False and first["agents_updated"] == 2 and first["agents_failed"] == 1
    assert _handles(graph) == {"a0": "one", "a1": "two", "a2": None}
    config.set_config.assert_not_awaited()

    graph.update_node = real  # type: ignore[method-assign]
    graph.calls.clear()
    second = await _run(graph, config)[0].migrate()

    assert second["success"] is True and second["agents_updated"] == 1
    assert [a[0] for a, _ in graph.calls_to("update_node")] == ["a2"]
    assert _handles(graph) == {"a0": "one", "a1": "two", "a2": "three"}
    config.set_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_processes_in_batches_of_the_configured_size(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migration, "BATCH_SIZE", 2)
    graph = _graph(*[f"Agent {i}" for i in range(5)])

    result = await _run(graph)[0].migrate()

    assert result["agents_updated"] == 5
    sizes = [a[0] for a, _ in graph.calls_to("list_agents_missing_handle")]
    assert sizes == [2, 2, 2, 2]  # 2 + 2 + 1, then the empty batch that ends the pass


@pytest.mark.asyncio
async def test_default_batch_is_500() -> None:
    assert migration.BATCH_SIZE == 500


@pytest.mark.asyncio
async def test_avoids_handles_already_taken_in_the_org() -> None:
    graph = _graph("Sales Bot")
    graph.add_agent("existing", "bob", name="Old", orgId="org-1", handle="sales-bot")

    await _run(graph)[0].migrate()

    assert _handles(graph)["a0"] == "sales-bot-2"


@pytest.mark.asyncio
async def test_same_name_in_two_orgs_is_not_a_collision() -> None:
    graph = _graph("Sales Bot")
    graph.add_agent("other", "mallory", name="Sales Bot", createdAtTimestamp=1)

    await _run(graph)[0].migrate()

    assert _handles(graph) == {"a0": "sales-bot", "other": "sales-bot"}
    assert graph.nodes[AGENTS]["other"]["orgId"] == "org-2"


@pytest.mark.asyncio
async def test_agent_without_a_derivable_org_is_left_alone() -> None:
    graph = _graph("Sales Bot")
    graph.add_node(AGENTS, {"_key": "orphan", "name": "Orphan", "createdBy": "k-ghost", "models": []})

    result = await _run(graph)[0].migrate()

    assert result["success"] is True
    assert "handle" not in graph.nodes[AGENTS]["orphan"] and "orgId" not in graph.nodes[AGENTS]["orphan"]


@pytest.mark.asyncio
async def test_keeps_an_org_the_agent_already_has() -> None:
    graph = _graph("Shared", orgId="org-2")

    await _run(graph)[0].migrate()

    assert graph.nodes[AGENTS]["a0"]["orgId"] == "org-2"


@pytest.mark.asyncio
async def test_listing_failure_leaves_the_flag_unset() -> None:
    graph = _graph("Sales Bot")
    graph.fail("list_agents_missing_handle", RuntimeError("db down"))
    service, config = _run(graph)

    result = await service.migrate()

    assert result["success"] is False and "db down" in result["error"]
    config.set_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_lost_race_during_backfill_moves_to_the_next_suffix() -> None:
    graph = _graph("Sales Bot")
    real = graph.update_node
    raced = False

    async def racing(key, collection, updates, transaction=None) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            graph.add_agent("racer", "bob", name="R", orgId="org-1", handle=updates["handle"])
        return await real(key, collection, updates, transaction)

    graph.update_node = racing  # type: ignore[method-assign]

    await _run(graph)[0].migrate()

    assert _handles(graph)["a0"] == "sales-bot-2"


@pytest.mark.asyncio
async def test_runner_wires_the_service() -> None:
    graph = _graph("Sales Bot")

    result = await run_agent_handles_migration(graph, _config(), logging.getLogger("t"))  # type: ignore[arg-type]

    assert result["success"] is True
