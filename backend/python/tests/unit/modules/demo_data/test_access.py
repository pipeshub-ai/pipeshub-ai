"""Who sees the Acme Corp demo data: each person's switch, and its default."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.demo_data import access
from app.modules.demo_data.access import (
    demo_data_status,
    excluded_demo_connector_ids,
    org_has_real_data,
    preference_key,
    write_preference,
)

DEMO = {"_key": "demo-1", "type": "Demo"}
JIRA = {"_key": "jira-1", "type": "JIRA"}
KB = {"_key": "kb-1", "type": "KB"}


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    access.clear_caches()


def _graph(apps: list[dict[str, Any]], indexed: set[str] = frozenset()) -> MagicMock:
    graph = MagicMock()
    graph.get_org_apps = AsyncMock(return_value=apps)
    graph.get_records_by_status = AsyncMock(
        side_effect=lambda org_id, connector_id, statuses, limit=None: ["r"] if connector_id in indexed else []
    )
    return graph


def _config(saved: dict[str, Any] | None = None) -> MagicMock:
    store = {} if saved is None else {preference_key("org", "u1"): saved}
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=lambda key, use_cache=True: store.get(key))
    config.set_config = AsyncMock(side_effect=lambda key, value: store.__setitem__(key, value) or True)
    config.delete_config = AsyncMock(side_effect=lambda key: store.pop(key, None) is not None)
    config.store = store
    return config


@pytest.mark.asyncio
async def test_without_a_demo_there_is_nothing_to_switch() -> None:
    status = await demo_data_status(_graph([JIRA]), _config(), "org", "u1")
    assert status.to_dict()["hasDemo"] is False
    assert await excluded_demo_connector_ids(_graph([JIRA]), _config(), "org", "u1") == frozenset()


@pytest.mark.asyncio
async def test_on_by_default_until_the_org_has_its_own_data() -> None:
    before = await demo_data_status(_graph([DEMO, JIRA]), _config(), "org", "u1")
    assert before.include is True and before.chosen is None

    access.clear_caches()
    graph = _graph([DEMO, JIRA], indexed={"jira-1"})
    after = await demo_data_status(graph, _config(), "org", "u1")
    assert after.include is False and after.real_data is True
    assert await excluded_demo_connector_ids(graph, _config(), "org", "u1") == frozenset({"demo-1"})


@pytest.mark.asyncio
async def test_a_persons_choice_wins_over_the_default() -> None:
    graph = _graph([DEMO, JIRA], indexed={"jira-1"})
    assert (await demo_data_status(graph, _config({"include": True}), "org", "u1")).include is True
    access.clear_caches()
    quiet = _graph([DEMO])
    assert await excluded_demo_connector_ids(quiet, _config({"include": False}), "org", "u1") == frozenset({"demo-1"})


@pytest.mark.asyncio
async def test_only_other_sources_count_as_real_data_and_connectors_are_checked_first() -> None:
    graph = _graph([KB, DEMO, JIRA], indexed={"demo-1"})
    assert await org_has_real_data(graph, "org") is False
    checked = [c.args[1] for c in graph.get_records_by_status.await_args_list]
    assert checked == ["jira-1", "kb-1"]

    access.clear_caches()
    assert await org_has_real_data(_graph([DEMO, KB], indexed={"kb-1"}), "org") is True


@pytest.mark.asyncio
async def test_the_answer_is_reused_for_a_while_then_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(access.time, "monotonic", lambda: now[0])
    graph = _graph([DEMO, JIRA], indexed={"jira-1"})
    assert await org_has_real_data(graph, "org") is True
    assert await org_has_real_data(graph, "org") is True
    assert graph.get_records_by_status.await_count == 1

    # An org that deleted its data gets the demo back by default, after a while.
    graph.get_records_by_status = AsyncMock(return_value=[])
    now[0] += access._REAL_DATA_TTL_S + 1
    assert await org_has_real_data(graph, "org") is False


@pytest.mark.asyncio
async def test_saving_and_clearing_the_choice() -> None:
    config = _config()
    await write_preference(config, "org", "u1", include=False)
    assert config.store[preference_key("org", "u1")] == {"include": False}

    await write_preference(config, "org", "u1", include=None)
    assert preference_key("org", "u1") not in config.store

    # Back to the default when nothing is saved is not an error.
    await write_preference(config, "org", "u1", include=None)
    config.delete_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failed_save_is_reported() -> None:
    config = _config()
    config.set_config = AsyncMock(return_value=False)
    with pytest.raises(RuntimeError):
        await write_preference(config, "org", "u1", include=True)



@pytest.mark.asyncio
async def test_requests_that_miss_together_share_one_look() -> None:
    import asyncio

    graph = _graph([DEMO, JIRA, KB])
    results = await asyncio.gather(*(org_has_real_data(graph, "org") for _ in range(5)))
    assert results == [False] * 5
    graph.get_org_apps.assert_awaited_once()
    assert graph.get_records_by_status.await_count == 2


@pytest.mark.asyncio
async def test_many_collections_are_probed_in_batches_and_stop_at_the_first_hit() -> None:
    apps = [{"_key": f"kb-{i}", "type": "KB"} for i in range(40)]
    graph = _graph(apps, indexed={"kb-3"})
    assert await org_has_real_data(graph, "org") is True
    assert graph.get_records_by_status.await_count == access._PROBE_BATCH



@pytest.mark.asyncio
async def test_a_failed_probe_is_not_taken_for_no_real_data() -> None:
    graph = _graph([DEMO, JIRA])
    graph.get_records_by_status = AsyncMock(side_effect=RuntimeError("graph unavailable"))
    with pytest.raises(RuntimeError):
        await org_has_real_data(graph, "org")

    # Nothing was cached, so the next look sees the real data.
    graph.get_records_by_status = AsyncMock(return_value=["r"])
    assert await org_has_real_data(graph, "org") is True


@pytest.mark.asyncio
async def test_one_probe_finding_a_record_wins_over_another_failing() -> None:
    graph = _graph([DEMO, JIRA, KB])
    graph.get_records_by_status = AsyncMock(side_effect=[RuntimeError("down"), ["r"]])
    assert await org_has_real_data(graph, "org") is True



@pytest.mark.asyncio
async def test_a_failed_app_listing_is_not_remembered_as_no_demo_or_no_real_data() -> None:
    # Both providers answer [] when the listing query fails.
    graph = _graph([])
    assert await access.demo_connector_ids(graph, "org") == ()
    assert await org_has_real_data(graph, "org") is False

    graph.get_org_apps = AsyncMock(return_value=[DEMO, JIRA])
    graph.get_records_by_status = AsyncMock(return_value=["r"])
    assert await access.demo_connector_ids(graph, "org") == ("demo-1",)
    assert await org_has_real_data(graph, "org") is True
