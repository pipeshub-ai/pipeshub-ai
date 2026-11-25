"""DK-2: SM-1..SM-6 on the Docker broker (runc lane; gVisor where ``runsc`` exists), plus what a process sees."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.types import LeaseRef
from harness_core.skills import (
    InMemorySkillContentSource,
    StoredSkillBundleBuilder,
    TarSkillMaterializer,
    skills_mount,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.skills import (
    ALPHA,
    SKILLS,
    SkillMaterializerContract,
)
from harness_core.testing.sandbox.contract import run, with_mounts
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryWorkspaceStore,
)

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker

    from .conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


class DockerMaterializerHarness:
    def __init__(self, broker: DockerSandboxBroker, env: DockerContractEnv) -> None:
        self.scope = ObjectScope("tenant-a", "session-1")
        self.objects = InMemoryBinaryObjectStore()
        self.workspaces = workspaces = InMemoryWorkspaceStore(clock=ManualClock(datetime(2026, 10, 3, tzinfo=UTC)))
        self.bundles = StoredSkillBundleBuilder(
            source=InMemorySkillContentSource(), objects=self.objects, workspaces=workspaces
        )
        self.materializer = TarSkillMaterializer(objects=self.objects, workspaces=workspaces)
        self.broker = broker
        self.env = env

    def corrupt_objects(self) -> None:
        self.objects.tamper()


class TestTarSkillMaterializerOnDocker(SkillMaterializerContract):
    @pytest.fixture
    def harness(  # type: ignore[override]
        self, docker_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv
    ) -> DockerMaterializerHarness:
        return DockerMaterializerHarness(docker_broker, sandbox_env)

    async def test_dk2_hashes_match_and_writes_hit_a_read_only_file_system(
        self, harness: DockerMaterializerHarness
    ) -> None:
        bundle = await harness.bundles.store(harness.scope, "alpha", ALPHA)
        sandbox = await harness.broker.acquire(
            with_mounts(harness.env.spec(), skills_mount(SKILLS)), lease=LeaseRef("w", 1), idempotency_key="k-dk2"
        )
        try:
            await harness.materializer.materialize(harness.scope, sandbox, [bundle], skills_dir=SKILLS)
            out, _, code = await run(
                await sandbox.exec(["sha256sum", f"{SKILLS}/alpha/assets/logo.png"], env={})
            )
            assert code == 0
            assert out.split()[0].decode() == hashlib.sha256(ALPHA["assets/logo.png"]).hexdigest()
            _, err, code = await run(
                await sandbox.exec(["/bin/sh", "-c", f"echo x > {SKILLS}/alpha/SKILL.md"], env={})
            )
            assert code != 0
            assert b"Read-only file system" in err
            out, _, _ = await run(await sandbox.exec(["stat", "-c", "%a %u", f"{SKILLS}/alpha/SKILL.md"], env={}))
            assert out.split() == [b"444", b"0"]
        finally:
            await harness.broker.release(sandbox)
