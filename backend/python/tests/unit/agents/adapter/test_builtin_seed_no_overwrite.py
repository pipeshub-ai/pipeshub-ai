"""Builtin seeding keeps working when creates are insert-if-absent.

Regression guards: they pass before and after the change. They pin that two
concurrent syncs both settle on one doc, and that a custom skill squatting on a
pack name is left alone instead of being overwritten or retried forever.
"""

from __future__ import annotations

import asyncio

from app.agent_loop_lib.modules.providers.skills.base import SkillSource
from app.agents.agent_loop.skills.builtin_seeder import (
    SEED_IDENTITY,
    BuiltinSkillSeeder,
)
from tests.support.skill_stores import skill_store
from tests.unit.agents.adapter.test_builtin_skill_seeder import _PACK_A_V1, _write_pack
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

_CUSTOM_MD = """---
name: pack-a
description: Use pack-a for my own thing
---

Custom body that must survive.
"""


def _seeder(tmp_path) -> BuiltinSkillSeeder:
    root = str(tmp_path / "packs")
    _write_pack(root, "pack-a", _PACK_A_V1)
    return BuiltinSkillSeeder(root)


class _BothFindTheKeyFree(FakeGraphProvider):
    """Holds each insert until two have arrived: both syncs have read the name as free before either inserts."""

    def __init__(self) -> None:
        super().__init__()
        self.arrived = 0
        self._both = asyncio.Event()

    async def create_node_if_absent(self, *args: object, **kwargs: object) -> bool:
        self.arrived += 1
        if self.arrived == 2:
            self._both.set()
        await asyncio.wait_for(self._both.wait(), timeout=5)
        return await super().create_node_if_absent(*args, **kwargs)


async def test_concurrent_syncs_settle_on_one_builtin_doc(tmp_path) -> None:
    graph = _BothFindTheKeyFree()
    seeder = _seeder(tmp_path)

    settled = await asyncio.gather(
        seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)),
        seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)),
    )

    assert graph.arrived == 2  # the loser reached the insert and settled through its conflict
    assert settled == [True, True]
    docs = graph._col("agentSkills")
    assert list(docs) == ["org-1_pack-a"]
    assert docs["org-1_pack-a"]["source"] == SkillSource.BUILTIN.value
    assert docs["org-1_pack-a"]["createdBy"] == SEED_IDENTITY


async def test_a_custom_skill_on_a_pack_name_is_not_overwritten(tmp_path) -> None:
    graph = FakeGraphProvider()
    await skill_store(graph, "org-1", "user-a").create_skill("pack-a", _CUSTOM_MD)

    # Settled (True): the org is not left to retry the seed on every request.
    assert await _seeder(tmp_path).sync(skill_store(graph, "org-1", SEED_IDENTITY)) is True

    doc = graph._col("agentSkills")["org-1_pack-a"]
    assert doc["createdBy"] == "user-a"
    assert "Custom body that must survive." in doc["content"]


_PACK_WITH_RELATION = _PACK_A_V1.replace("    pack_version: 1.0.0\n", "    pack_version: 1.0.0\n    related: [pack-z]\n")


async def test_a_builtin_left_without_its_edges_gets_them_at_the_next_sync(tmp_path) -> None:
    from tests.unit.agents.adapter.test_skills_no_overwrite import _UndoFails

    root = str(tmp_path / "packs")
    _write_pack(root, "pack-a", _PACK_WITH_RELATION)
    seeder = BuiltinSkillSeeder(root)
    graph = _UndoFails(raises=True)  # the relation edges fail once, and so does the undo

    assert await seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)) is False
    assert "org-1_pack-a" in graph._col("agentSkills")
    assert not graph._edges.get("agentSkillRelation")

    assert await seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)) is True
    assert [(e["to_id"], e["type"]) for e in graph._edges["agentSkillRelation"]] == [("org-1_pack-z", "related")]
