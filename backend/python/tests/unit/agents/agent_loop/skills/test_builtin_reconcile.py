"""Builtin skill seeding moved off the request path (P0.17): the agent request
only schedules a background reconcile, at most once per org per pack version,
and reconcile itself is idempotent."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agents.agent_loop.skills.manager_factory as mf
from app.agent_loop_lib.modules.providers.skills.base import SkillFilter, SkillSource
from app.agents.agent_loop.skills.builtin_seeder import (
    SEED_IDENTITY,
    BuiltinSkillSeeder,
)
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from tests.unit.agents.adapter.test_builtin_skill_seeder import (
    _PACK_A_V1,
    _PACK_A_V2,
    _PACK_B_V1,
    _write_pack,
)
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_state() -> Iterator[None]:
    mf.reset_builtin_reconcile_state()
    yield
    mf.reset_builtin_reconcile_state()


@pytest.fixture
def packs_root_v1(tmp_path: Path) -> str:
    root = str(tmp_path / "v1")
    _write_pack(root, "pack-a", _PACK_A_V1)
    _write_pack(root, "pack-b", _PACK_B_V1)
    return root


@pytest.fixture
def packs_root_v2(tmp_path: Path) -> str:
    root = str(tmp_path / "v2")
    _write_pack(root, "pack-a", _PACK_A_V2)
    _write_pack(root, "pack-b", _PACK_B_V1)
    return root


def _counting_seeder(root: str) -> BuiltinSkillSeeder:
    seeder = BuiltinSkillSeeder(root)
    seeder.sync = AsyncMock(wraps=seeder.sync)  # type: ignore[method-assign]
    return seeder


async def _drain() -> None:
    await asyncio.gather(*mf._reconcile_tasks.values())


async def _builtin_names(graph: FakeGraphProvider, org: str) -> set[str]:
    store = GraphSkillStore(graph, org, SEED_IDENTITY)
    return {m.name for m in await store.list_skills(SkillFilter(source=SkillSource.BUILTIN))}


def _context(graph: object, org: str = "org-1") -> SimpleNamespace:
    return SimpleNamespace(graph_provider=graph, org_id=org, user_id="u", retrieval_service=MagicMock())


async def test_request_path_never_awaits_the_seeder(packs_root_v1: str) -> None:
    seeder = _counting_seeder(packs_root_v1)
    manager = MagicMock()
    with patch.object(mf, "get_builtin_seeder", return_value=seeder), \
         patch.object(mf, "_build_manager", new=AsyncMock(return_value=manager)), \
         patch.object(mf, "schedule_builtin_skill_reconcile") as schedule:
        result = await mf.build_runtime_skill_manager(_context(FakeGraphProvider()), MagicMock())

    assert result is manager
    seeder.sync.assert_not_awaited()
    manager.refresh.assert_not_called()
    schedule.assert_called_once()


async def test_schedules_once_per_org_and_version(packs_root_v1: str) -> None:
    graph = FakeGraphProvider()
    seeder = _counting_seeder(packs_root_v1)
    with patch.object(mf, "get_builtin_seeder", return_value=seeder), \
         patch.object(mf, "_build_manager", new=AsyncMock(return_value=MagicMock())):
        for _ in range(3):
            await mf.build_runtime_skill_manager(_context(graph), MagicMock())
        await _drain()
        for _ in range(3):
            await mf.build_runtime_skill_manager(_context(graph), MagicMock())
        await _drain()

    assert seeder.sync.await_count == 1
    assert await _builtin_names(graph, "org-1") == {"pack-a", "pack-b"}


async def test_reconcile_is_idempotent_and_rechecks_on_new_pack_version(
    packs_root_v1: str, packs_root_v2: str,
) -> None:
    graph = FakeGraphProvider()
    v1 = _counting_seeder(packs_root_v1)
    with patch.object(mf, "get_builtin_seeder", return_value=v1):
        await mf.reconcile_builtin_skills(graph, "org-1")
        await mf.reconcile_builtin_skills(graph, "org-1")
    assert v1.sync.await_count == 1

    v2 = _counting_seeder(packs_root_v2)
    with patch.object(mf, "get_builtin_seeder", return_value=v2):
        await mf.reconcile_builtin_skills(graph, "org-1")
        await mf.reconcile_builtin_skills(graph, "org-1")
    assert v2.sync.await_count == 1
    store = GraphSkillStore(graph, "org-1", SEED_IDENTITY)
    assert (await store.get_skill("pack-a")).metadata.pack_version == "2.0.0"


async def test_failed_reconcile_is_retried(packs_root_v1: str) -> None:
    seeder = _counting_seeder(packs_root_v1)
    seeder.sync.side_effect = [RuntimeError("graph down"), None]
    with patch.object(mf, "get_builtin_seeder", return_value=seeder):
        await mf.reconcile_builtin_skills(FakeGraphProvider(), "org-1")
        await mf.reconcile_builtin_skills(FakeGraphProvider(), "org-1")
    assert seeder.sync.await_count == 2


async def test_startup_reconciles_every_org(packs_root_v1: str) -> None:
    graph = FakeGraphProvider()
    with patch.object(mf, "get_builtin_seeder", return_value=BuiltinSkillSeeder(packs_root_v1)):
        await mf.reconcile_builtin_skills_for_orgs(graph, [{"_key": "org-1"}, {"id": "org-2"}, {}])

    assert await _builtin_names(graph, "org-1") == {"pack-a", "pack-b"}
    assert await _builtin_names(graph, "org-2") == {"pack-a", "pack-b"}


async def test_list_route_sync_skips_once_reconciled(packs_root_v1: str) -> None:
    graph = FakeGraphProvider()
    seeder = _counting_seeder(packs_root_v1)
    manager = MagicMock()
    manager.refresh = AsyncMock()
    manager.catalog_snapshot.return_value = []
    with patch.object(mf, "get_builtin_seeder", return_value=seeder):
        await mf.reconcile_builtin_skills(graph, "org-1")
        await mf.sync_builtin_skills(graph, "org-1", manager)

    assert seeder.sync.await_count == 1
    manager.catalog_snapshot.assert_not_called()
