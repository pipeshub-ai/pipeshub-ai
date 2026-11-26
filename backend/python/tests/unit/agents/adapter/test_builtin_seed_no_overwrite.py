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


async def test_concurrent_syncs_settle_on_one_builtin_doc(tmp_path) -> None:
    graph = FakeGraphProvider()
    seeder = _seeder(tmp_path)

    settled = await asyncio.gather(
        seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)),
        seeder.sync(skill_store(graph, "org-1", SEED_IDENTITY)),
    )

    assert settled == [True, True]
    docs = graph._col("agentSkills")
    assert list(docs) == ["org-1_pack-a"]
    assert docs["org-1_pack-a"]["source"] == SkillSource.BUILTIN.value
    assert docs["org-1_pack-a"]["createdBy"] == SEED_IDENTITY


async def test_a_custom_skill_on_a_pack_name_is_not_overwritten(tmp_path) -> None:
    graph = FakeGraphProvider()
    await skill_store(graph, "org-1", "user-a").create_skill("pack-a", _CUSTOM_MD)

    await _seeder(tmp_path).sync(skill_store(graph, "org-1", SEED_IDENTITY))

    doc = graph._col("agentSkills")["org-1_pack-a"]
    assert doc["createdBy"] == "user-a"
    assert "Custom body that must survive." in doc["content"]
