"""Contract suites every skill adapter must pass (P7.1, ``impl/P7.1-skill-bundles.md``).

Subclass a contract in a test module and provide its ``harness`` fixture:

- :class:`SkillContentSourceContract` (SS-1..SS-5) on every ``SkillContentSource``;
- :class:`SkillBundleBuilderContract` (BB-1..BB-3, BB-5..BB-10) on every
  ``SkillBundleBuilder``, over the source and stores it is built with;
- :class:`SkillMaterializerContract` (SM-1..SM-6) on every ``SkillMaterializer``,
  against any ``SandboxBroker`` (the fake, a container runtime).

A source decides what a published skill's bytes look like (a graph store
re-renders ``SKILL.md``, for example), so the suites compare bundles with
what the source returns, never with what was published.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.sandbox.errors import SandboxError
from harness_core.sandbox.types import LeaseRef
from harness_core.skills.materializer import skills_mount
from harness_core.skills.types import (
    SkillBundleIntegrityError,
    SkillBundleInvalid,
    SkillBundleUnavailable,
    SkillPin,
    SkillRef,
    legacy_skill_pin_digest,
    skill_bundle_digest,
)
from harness_core.testing.sandbox.contract import WORK, run, with_mounts
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.types import WorkspaceFile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.ports import SandboxBroker, SandboxHandle
    from harness_core.skills.builder import StoredSkillBundleBuilder
    from harness_core.skills.ports import (
        SkillBundleBuilder,
        SkillContentSource,
        SkillMaterializer,
    )
    from harness_core.skills.types import SkillBundleRef
    from harness_core.testing.sandbox.contract import SandboxContractEnv
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectScope

SKILLS = "/skills"
_ZERO_DIGEST = "sha256:" + "0" * 64


class SkillSourceHarness(Protocol):
    @property
    def source(self) -> SkillContentSource: ...

    @property
    def tenant(self) -> str: ...

    @property
    def other_tenant(self) -> str: ...

    def content(self, name: str, marker: str) -> dict[str, bytes]:
        """A valid skill folder for this source, different for each ``marker``."""
        ...

    async def publish(self, tenant: str, name: str, files: Mapping[str, bytes]) -> str:
        """Create the skill or replace its content; the version it now has."""
        ...

    async def delete(self, tenant: str, name: str) -> None: ...


class SkillContentSourceContract:
    @pytest.fixture
    def harness(self) -> SkillSourceHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_ss1_current_files_hold_skill_md_and_every_resource(self, harness: SkillSourceHarness) -> None:
        published = harness.content("ss-one", "first")
        _ = await harness.publish(harness.tenant, "ss-one", published)
        files = await harness.source.files(harness.tenant, SkillRef("ss-one"))
        assert files is not None
        assert set(files) == set(published)
        assert all(isinstance(content, bytes) for content in files.values())

    async def test_ss2_an_archived_version_keeps_its_content(self, harness: SkillSourceHarness) -> None:
        first = await harness.publish(harness.tenant, "ss-two", harness.content("ss-two", "first"))
        before = await harness.source.files(harness.tenant, SkillRef("ss-two"))
        _ = await harness.publish(harness.tenant, "ss-two", harness.content("ss-two", "second"))
        assert await harness.source.files(harness.tenant, SkillRef("ss-two", first)) == before
        assert await harness.source.files(harness.tenant, SkillRef("ss-two")) != before

    async def test_ss3_versions_lists_current_and_archived(self, harness: SkillSourceHarness) -> None:
        first = await harness.publish(harness.tenant, "ss-three", harness.content("ss-three", "first"))
        second = await harness.publish(harness.tenant, "ss-three", harness.content("ss-three", "second"))
        assert {first, second} <= set(await harness.source.versions(harness.tenant, "ss-three"))

    async def test_ss4_unknown_skill_or_version_is_none(self, harness: SkillSourceHarness) -> None:
        assert await harness.source.files(harness.tenant, SkillRef("ss-missing")) is None
        assert list(await harness.source.versions(harness.tenant, "ss-missing")) == []
        _ = await harness.publish(harness.tenant, "ss-four", harness.content("ss-four", "first"))
        assert await harness.source.files(harness.tenant, SkillRef("ss-four", "no-such-version")) is None

    async def test_ss5_another_tenant_sees_nothing(self, harness: SkillSourceHarness) -> None:
        _ = await harness.publish(harness.tenant, "ss-five", harness.content("ss-five", "first"))
        assert await harness.source.files(harness.other_tenant, SkillRef("ss-five")) is None
        assert list(await harness.source.versions(harness.other_tenant, "ss-five")) == []

    async def test_a_deleted_skill_keeps_its_content_readable_by_version(self, harness: SkillSourceHarness) -> None:
        version = await harness.publish(harness.tenant, "ss-gone", harness.content("ss-gone", "first"))
        before = await harness.source.files(harness.tenant, SkillRef("ss-gone"))
        await harness.delete(harness.tenant, "ss-gone")
        assert await harness.source.files(harness.tenant, SkillRef("ss-gone")) is None
        assert await harness.source.files(harness.tenant, SkillRef("ss-gone", version)) == before


class SkillBuilderHarness(SkillSourceHarness, Protocol):
    @property
    def builder(self) -> SkillBundleBuilder: ...

    @property
    def objects(self) -> BinaryObjectStore: ...

    @property
    def workspaces(self) -> WorkspaceStore: ...

    @property
    def scope(self) -> ObjectScope: ...

    @property
    def other_scope(self) -> ObjectScope:
        """A scope of ``other_tenant``."""
        ...


async def _bundle_bytes(h: SkillBuilderHarness, scope: ObjectScope, bundle: SkillBundleRef) -> dict[str, bytes]:
    manifest = await h.workspaces.revision(scope, bundle.workspace_id, bundle.revision)
    return {path: await h.objects.get(scope, entry.content) for path, entry in manifest.files.items()}


class SkillBundleBuilderContract:
    @pytest.fixture
    def harness(self) -> SkillBuilderHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_bb1_a_bundle_holds_exactly_the_source_bytes(self, harness: SkillBuilderHarness) -> None:
        _ = await harness.publish(harness.tenant, "bb-one", harness.content("bb-one", "first"))
        source = await harness.source.files(harness.tenant, SkillRef("bb-one"))
        assert source is not None
        bundle = await harness.builder.build(harness.scope, SkillRef("bb-one"))
        assert bundle.name == "bb-one"
        assert bundle.digest == skill_bundle_digest(source)
        assert (bundle.file_count, bundle.size_bytes) == (len(source), sum(len(c) for c in source.values()))
        assert await _bundle_bytes(harness, harness.scope, bundle) == dict(source)

    async def test_bb2_building_twice_is_idempotent(self, harness: SkillBuilderHarness) -> None:
        _ = await harness.publish(harness.tenant, "bb-two", harness.content("bb-two", "first"))
        first = await harness.builder.build(harness.scope, SkillRef("bb-two"))
        second = await harness.builder.build(harness.scope, SkillRef("bb-two"))
        assert second == first
        assert (await harness.workspaces.head(harness.scope, first.workspace_id)).revision == first.revision == 1

    async def test_bb3_the_digest_follows_content_not_versions(self, harness: SkillBuilderHarness) -> None:
        # A source may stamp the version into SKILL.md (the graph store renders it), so equal
        # content is what the source returns, not what was published.
        content = harness.content("bb-three", "same")
        versions = [
            await harness.publish(harness.tenant, "bb-three", content),
            await harness.publish(harness.tenant, "bb-three", content),
            await harness.publish(harness.tenant, "bb-three", harness.content("bb-three", "other")),
        ]
        sources = [await harness.source.files(harness.tenant, SkillRef("bb-three", v)) for v in versions]
        bundles = [await harness.builder.build(harness.scope, SkillRef("bb-three", v)) for v in versions]
        for files, bundle in zip(sources, bundles, strict=True):
            assert files is not None
            assert bundle.digest == skill_bundle_digest(files)
        assert (bundles[0].digest == bundles[1].digest) == (sources[0] == sources[1])
        assert bundles[2].digest not in (bundles[0].digest, bundles[1].digest)

    async def test_bb5_an_unknown_skill_or_version_is_unavailable(self, harness: SkillBuilderHarness) -> None:
        with pytest.raises(SkillBundleUnavailable):
            _ = await harness.builder.build(harness.scope, SkillRef("bb-missing"))
        _ = await harness.publish(harness.tenant, "bb-five", harness.content("bb-five", "first"))
        with pytest.raises(SkillBundleUnavailable):
            _ = await harness.builder.build(harness.scope, SkillRef("bb-five", "no-such-version"))

    async def test_bb6_another_tenant_cannot_resolve_the_pin(self, harness: SkillBuilderHarness) -> None:
        _ = await harness.publish(harness.tenant, "bb-six", harness.content("bb-six", "first"))
        bundle = await harness.builder.build(harness.scope, SkillRef("bb-six"))
        with pytest.raises(SkillBundleUnavailable):
            _ = await harness.builder.resolve(harness.other_scope, SkillPin("bb-six", bundle.digest))

    @pytest.mark.parametrize("found_in", ["stored", "history"])
    async def test_bb7_a_content_pin_survives_edits_and_deletion(
        self, harness: SkillBuilderHarness, found_in: str
    ) -> None:
        name = f"bb-seven-{found_in}"
        _ = await harness.publish(harness.tenant, name, harness.content(name, "pinned"))
        pinned = await harness.source.files(harness.tenant, SkillRef(name))
        assert pinned is not None
        pin = SkillPin(name, skill_bundle_digest(pinned))
        if found_in == "stored":
            _ = await harness.builder.build(harness.scope, SkillRef(name))
        _ = await harness.publish(harness.tenant, name, harness.content(name, "edited"))
        await harness.delete(harness.tenant, name)
        bundle = await harness.builder.resolve(harness.scope, pin)
        assert bundle.digest == pin.bundle_digest
        assert await _bundle_bytes(harness, harness.scope, bundle) == dict(pinned)

    async def test_bb8_legacy_pins_resolve_to_the_version_they_named(self, harness: SkillBuilderHarness) -> None:
        old = await harness.publish(harness.tenant, "bb-eight", harness.content("bb-eight", "old"))
        old_files = await harness.source.files(harness.tenant, SkillRef("bb-eight"))
        _ = await harness.publish(harness.tenant, "bb-eight", harness.content("bb-eight", "new"))
        new_files = await harness.source.files(harness.tenant, SkillRef("bb-eight"))
        assert old_files is not None and new_files is not None
        by_version = await harness.builder.resolve(harness.scope, SkillPin("bb-eight", legacy_skill_pin_digest("bb-eight", old)))
        assert by_version.digest == skill_bundle_digest(old_files)
        latest = await harness.builder.resolve(harness.scope, SkillPin("bb-eight", legacy_skill_pin_digest("bb-eight", None)))
        assert latest.digest == skill_bundle_digest(new_files)
        with pytest.raises(SkillBundleUnavailable):
            _ = await harness.builder.resolve(harness.scope, SkillPin("bb-eight", _ZERO_DIGEST))

    async def test_bb9_a_bundle_that_no_longer_matches_is_refused(self, harness: SkillBuilderHarness) -> None:
        _ = await harness.publish(harness.tenant, "bb-nine", harness.content("bb-nine", "first"))
        bundle = await harness.builder.build(harness.scope, SkillRef("bb-nine"))
        intruder = await harness.objects.put(harness.scope, b"not part of the bundle")
        _ = await harness.workspaces.commit(
            harness.scope,
            bundle.workspace_id,
            base_revision=bundle.revision,
            changes=ChangeSet.of(WorkspaceFile("extra.txt", intruder)),
            reason="autosave",
        )
        with pytest.raises(SkillBundleIntegrityError):
            _ = await harness.builder.build(harness.scope, SkillRef("bb-nine"))

    async def test_bb10_concurrent_builds_make_one_bundle(self, harness: SkillBuilderHarness) -> None:
        _ = await harness.publish(harness.tenant, "bb-ten", harness.content("bb-ten", "first"))
        bundles = await asyncio.gather(*(harness.builder.build(harness.scope, SkillRef("bb-ten")) for _ in range(4)))
        assert len(set(bundles)) == 1
        assert (await harness.workspaces.head(harness.scope, bundles[0].workspace_id)).revision == 1


class SkillMaterializerHarness(Protocol):
    @property
    def materializer(self) -> SkillMaterializer: ...

    @property
    def bundles(self) -> StoredSkillBundleBuilder:
        """Stores bundles where the materializer reads them."""
        ...

    @property
    def scope(self) -> ObjectScope: ...

    @property
    def broker(self) -> SandboxBroker: ...

    @property
    def env(self) -> SandboxContractEnv: ...

    @property
    def objects(self) -> BinaryObjectStore: ...

    @property
    def workspaces(self) -> WorkspaceStore: ...

    def corrupt_objects(self) -> None:
        """Change the stored bytes of every object behind the store's back."""
        ...


ALPHA = {
    "SKILL.md": "---\nname: alpha\ndescription: Alpha.\n---\n# Alpha ✓\n".encode(),
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff" + bytes(range(256)),
    "assets/empty.bin": b"",
}
BETA = {
    "SKILL.md": b"---\nname: beta\ndescription: Beta.\n---\n# Beta\n",
    "scripts/run.py": b"print('beta')\n",
}
_LEASE = LeaseRef("worker-1", 1)


class SkillMaterializerContract:
    @pytest.fixture
    def harness(self) -> SkillMaterializerHarness:
        raise NotImplementedError("provide a harness fixture")

    async def _sandbox(self, h: SkillMaterializerHarness, key: str, *, mounted: bool = True) -> SandboxHandle:
        spec = with_mounts(h.env.spec(), skills_mount(SKILLS)) if mounted else h.env.spec()
        return await h.broker.acquire(spec, lease=_LEASE, idempotency_key=key)

    async def _read(self, h: SkillMaterializerHarness, sandbox: SandboxHandle, path: str) -> tuple[bytes, int]:
        out, _, code = await run(await sandbox.exec(h.env.argv_read_file(path), env={}))
        return out, code

    async def _two(self, h: SkillMaterializerHarness) -> list[SkillBundleRef]:
        return [await h.bundles.store(h.scope, "alpha", ALPHA), await h.bundles.store(h.scope, "beta", BETA)]

    async def test_sm1_bundles_appear_with_their_exact_bytes(self, harness: SkillMaterializerHarness) -> None:
        sandbox = await self._sandbox(harness, "k-sm1")
        try:
            await harness.materializer.materialize(harness.scope, sandbox, await self._two(harness), skills_dir=SKILLS)
            for name, files in (("alpha", ALPHA), ("beta", BETA)):
                for path, content in files.items():
                    assert await self._read(harness, sandbox, f"{SKILLS}/{name}/{path}") == (content, 0)
        finally:
            await harness.broker.release(sandbox)

    async def test_sm2_processes_cannot_change_the_skills(self, harness: SkillMaterializerHarness) -> None:
        sandbox = await self._sandbox(harness, "k-sm2")
        try:
            await harness.materializer.materialize(harness.scope, sandbox, await self._two(harness), skills_dir=SKILLS)
            for argv in (
                harness.env.argv_write_file(f"{SKILLS}/alpha/SKILL.md", "changed"),
                harness.env.argv_write_file(f"{SKILLS}/alpha/new.md", "new"),
                harness.env.argv_write_file(f"{SKILLS}/gamma.md", "new"),
                harness.env.argv_remove_file(f"{SKILLS}/beta/scripts/run.py"),
            ):
                _, _, code = await run(await sandbox.exec(argv, env={}))
                assert code != 0, argv
            assert await self._read(harness, sandbox, f"{SKILLS}/alpha/SKILL.md") == (ALPHA["SKILL.md"], 0)
            assert await self._read(harness, sandbox, f"{SKILLS}/beta/scripts/run.py") == (BETA["scripts/run.py"], 0)
        finally:
            await harness.broker.release(sandbox)

    async def test_sm3_a_skills_dir_that_is_not_a_read_only_mount_is_refused(
        self, harness: SkillMaterializerHarness
    ) -> None:
        sandbox = await self._sandbox(harness, "k-sm3", mounted=False)
        try:
            bundles = await self._two(harness)
            for skills_dir in (WORK, SKILLS):
                with pytest.raises(SandboxError):
                    await harness.materializer.materialize(harness.scope, sandbox, bundles, skills_dir=skills_dir)
            assert (await self._read(harness, sandbox, f"{WORK}/alpha/SKILL.md"))[1] != 0
        finally:
            await harness.broker.release(sandbox)

    async def test_sm4_bytes_that_no_longer_match_are_never_seeded(self, harness: SkillMaterializerHarness) -> None:
        sandbox = await self._sandbox(harness, "k-sm4")
        try:
            bundles = await self._two(harness)
            harness.corrupt_objects()
            with pytest.raises(SkillBundleIntegrityError):
                await harness.materializer.materialize(harness.scope, sandbox, bundles, skills_dir=SKILLS)
            assert (await self._read(harness, sandbox, f"{SKILLS}/alpha/SKILL.md"))[1] != 0
        finally:
            await harness.broker.release(sandbox)

    async def test_sm4_a_manifest_that_no_longer_matches_is_never_seeded(
        self, harness: SkillMaterializerHarness
    ) -> None:
        alpha = await harness.bundles.store(harness.scope, "alpha", ALPHA)
        swapped = await harness.objects.put(harness.scope, b"---\nname: alpha\n---\n# swapped\n")
        changed = await harness.workspaces.commit(
            harness.scope,
            alpha.workspace_id,
            base_revision=alpha.revision,
            changes=ChangeSet.of(WorkspaceFile("SKILL.md", swapped)),
            reason="autosave",
        )
        stale = dataclasses.replace(alpha, revision=changed.revision)
        sandbox = await self._sandbox(harness, "k-sm4b")
        try:
            with pytest.raises(SkillBundleIntegrityError):
                await harness.materializer.materialize(harness.scope, sandbox, [stale], skills_dir=SKILLS)
            assert (await self._read(harness, sandbox, f"{SKILLS}/alpha/SKILL.md"))[1] != 0
        finally:
            await harness.broker.release(sandbox)

    async def test_sm5_two_bundles_with_one_name_are_refused(self, harness: SkillMaterializerHarness) -> None:
        sandbox = await self._sandbox(harness, "k-sm5")
        try:
            alpha = await harness.bundles.store(harness.scope, "alpha", ALPHA)
            twin = await harness.bundles.store(harness.scope, "alpha", BETA)
            with pytest.raises(SkillBundleInvalid):
                await harness.materializer.materialize(harness.scope, sandbox, [alpha, twin], skills_dir=SKILLS)
        finally:
            await harness.broker.release(sandbox)

    async def test_sm6_materializing_again_is_harmless(self, harness: SkillMaterializerHarness) -> None:
        sandbox = await self._sandbox(harness, "k-sm6")
        try:
            bundles = await self._two(harness)
            for _ in range(2):
                await harness.materializer.materialize(harness.scope, sandbox, bundles, skills_dir=SKILLS)
            assert await self._read(harness, sandbox, f"{SKILLS}/alpha/assets/logo.png") == (ALPHA["assets/logo.png"], 0)
        finally:
            await harness.broker.release(sandbox)


__all__ = [
    "ALPHA",
    "BETA",
    "SKILLS",
    "SkillBuilderHarness",
    "SkillBundleBuilderContract",
    "SkillContentSourceContract",
    "SkillMaterializerContract",
    "SkillMaterializerHarness",
    "SkillSourceHarness",
]
