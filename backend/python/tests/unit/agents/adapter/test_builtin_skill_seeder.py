"""`BuiltinSkillSeeder` — per-org lazy idempotent sync of the in-repo
`builtin_packs/` into a `GraphSkillStore`: first sync creates every pack,
a second sync at an unchanged pack_version is a no-op, bumping the version
on an unmodified copy upgrades it (with a real revision snapshot), and
bumping it on an org-edited copy is skipped (fork-detection via
`updatedBy`)."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.agent_loop_lib.modules.providers.skills.base import SkillSource
from app.agents.agent_loop.skills.builtin_seeder import SEED_IDENTITY, BuiltinSkillSeeder
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

_PACK_A_V1 = """---
name: pack-a
description: Use pack-a when doing thing A
metadata:
  agent-loop:
    source: builtin
    pack_name: pack-a
    pack_version: 1.0.0
---

Do thing A, version 1.
"""

_PACK_B_V1 = """---
name: pack-b
description: Use pack-b when doing thing B
metadata:
  agent-loop:
    source: builtin
    pack_name: pack-b
    pack_version: 1.0.0
---

Do thing B, version 1.
"""

_PACK_A_V2 = _PACK_A_V1.replace("pack_version: 1.0.0", "pack_version: 2.0.0").replace(
    "Do thing A, version 1.", "Do thing A, version 2 (better)."
)


def _write_pack(root: str, name: str, content: str) -> None:
    pack_dir = os.path.join(root, name)
    os.makedirs(pack_dir, exist_ok=True)
    with open(os.path.join(pack_dir, "SKILL.md"), "w", encoding="utf-8") as f:
        f.write(content)


@pytest.fixture
def packs_root_v1(tmp_path) -> str:
    root = str(tmp_path / "builtin_packs_v1")
    _write_pack(root, "pack-a", _PACK_A_V1)
    _write_pack(root, "pack-b", _PACK_B_V1)
    return root


@pytest.fixture
def packs_root_v2(tmp_path) -> str:
    root = str(tmp_path / "builtin_packs_v2")
    _write_pack(root, "pack-a", _PACK_A_V2)
    _write_pack(root, "pack-b", _PACK_B_V1)
    return root


def _seed_store(graph: FakeGraphProvider, org_id: str = "org-1") -> GraphSkillStore:
    return GraphSkillStore(graph, org_id, SEED_IDENTITY)


class TestFirstSync:
    async def test_creates_every_pack_tagged_builtin(self, packs_root_v1: str) -> None:
        graph = FakeGraphProvider()
        seeder = BuiltinSkillSeeder(packs_root_v1)
        store = _seed_store(graph)

        await seeder.sync(store)

        names = {m.name for m in await store.list_skills()}
        assert names == {"pack-a", "pack-b"}
        for name in names:
            skill = await store.get_skill(name)
            assert skill.metadata.source == SkillSource.BUILTIN

    async def test_pack_versions_property_matches_disk(self, packs_root_v1: str) -> None:
        seeder = BuiltinSkillSeeder(packs_root_v1)
        assert seeder.pack_versions == {"pack-a": "1.0.0", "pack-b": "1.0.0"}


class TestNoOpResync:
    async def test_second_sync_at_same_version_does_not_rewrite(self, packs_root_v1: str) -> None:
        graph = FakeGraphProvider()
        seeder = BuiltinSkillSeeder(packs_root_v1)
        store = _seed_store(graph)
        await seeder.sync(store)

        before = await store.get_skill("pack-a")
        await seeder.sync(store)
        after = await store.get_skill("pack-a")

        assert before.metadata.version == after.metadata.version
        # No new revision snapshot was written on the no-op path.
        assert await store.list_versions("pack-a") == []


class TestAutoUpgrade:
    async def test_unmodified_copy_upgrades_to_new_pack_version(
        self, packs_root_v1: str, packs_root_v2: str,
    ) -> None:
        graph = FakeGraphProvider()
        store = _seed_store(graph)
        await BuiltinSkillSeeder(packs_root_v1).sync(store)

        await BuiltinSkillSeeder(packs_root_v2).sync(store)

        upgraded = await store.get_skill("pack-a")
        assert upgraded.metadata.pack_version == "2.0.0"
        assert "version 2" in upgraded.body
        # The upgrade path is a real content edit -> a revision snapshot
        # of the pre-upgrade content must exist.
        versions = await store.list_versions("pack-a")
        assert len(versions) == 1

        unchanged = await store.get_skill("pack-b")
        assert unchanged.metadata.pack_version == "1.0.0"


class TestForkSkip:
    async def test_org_edited_copy_is_not_overwritten_by_upgrade(
        self, packs_root_v1: str, packs_root_v2: str,
    ) -> None:
        graph = FakeGraphProvider()
        seed_store = _seed_store(graph)
        await BuiltinSkillSeeder(packs_root_v1).sync(seed_store)

        # A real user edits the org's copy of pack-a — updatedBy becomes
        # their id, not the seed identity.
        real_user_store = GraphSkillStore(graph, "org-1", "user-42")
        await real_user_store.patch_skill("pack-a", "version 1", "version 1 (customized)")

        await BuiltinSkillSeeder(packs_root_v2).sync(seed_store)

        skill = await real_user_store.get_skill("pack-a")
        # Still the org's edited copy, not silently replaced by the v2
        # upstream content — pack_version stays at the pre-upgrade value.
        assert skill.metadata.pack_version == "1.0.0"
        assert "customized" in skill.body

    async def test_seed_identity_edits_are_still_auto_upgraded(
        self, packs_root_v1: str, packs_root_v2: str,
    ) -> None:
        """A revision written by the seeder itself (e.g. a prior
        auto-upgrade) must not be mistaken for an org fork — only a
        DIFFERENT identity should block the next upgrade."""
        graph = FakeGraphProvider()
        seed_store = _seed_store(graph)
        await BuiltinSkillSeeder(packs_root_v1).sync(seed_store)
        await BuiltinSkillSeeder(packs_root_v2).sync(seed_store)  # seeder's own upgrade

        packs_root_v3 = os.path.dirname(packs_root_v2) + "_v3"
        os.makedirs(packs_root_v3, exist_ok=True)
        _write_pack(
            packs_root_v3, "pack-a",
            _PACK_A_V2.replace("pack_version: 2.0.0", "pack_version: 3.0.0"),
        )
        _write_pack(packs_root_v3, "pack-b", _PACK_B_V1)

        await BuiltinSkillSeeder(packs_root_v3).sync(seed_store)

        skill = await seed_store.get_skill("pack-a")
        assert skill.metadata.pack_version == "3.0.0"


class TestValidationOnLoad:
    def test_pack_with_invalid_frontmatter_is_silently_skipped_by_the_loader(self, tmp_path) -> None:
        """A malformed `name`/`description` never reaches `BuiltinSkillSeeder`
        at all — `load_skills_from_dir` already logs-and-skips it (same
        leniency it gives any third-party skill)."""
        root = str(tmp_path / "bad_packs")
        _write_pack(root, "good-pack", _PACK_A_V1.replace("pack-a", "good-pack"))
        _write_pack(root, "bad-pack", "---\nname: Bad_Name\ndescription: x\n---\n\nbody\n")
        seeder = BuiltinSkillSeeder(root)
        assert seeder.pack_versions == {"good-pack": "1.0.0"}

    def test_zero_packs_raises_at_construction(self, tmp_path) -> None:
        """Zero packs means the SKILL.md files didn't ship with the build
        (e.g. a `**/*.md` .dockerignore rule stripping them from the image) —
        must fail loudly instead of becoming a silent no-op seeder that
        leaves every org with an empty catalog."""
        root = str(tmp_path / "empty_packs")
        os.makedirs(root, exist_ok=True)
        with pytest.raises(RuntimeError, match="no skill packs found"):
            BuiltinSkillSeeder(root)

    def test_pack_with_empty_body_raises_at_construction(self, tmp_path) -> None:
        """Unlike a frontmatter error, an empty body passes the loader's
        own (frontmatter-only) parse and is only caught by the seeder's
        explicit `validate_skill()` call — which, for OUR in-repo content
        (unlike third-party skills), should fail loudly at process
        startup rather than silently ship a broken pack."""
        root = str(tmp_path / "bad_packs")
        _write_pack(root, "bad-pack", "---\nname: bad-pack\ndescription: x\n---\n\n")
        with pytest.raises(Exception):
            BuiltinSkillSeeder(root)
