"""SS, BB and SM contract suites on the in-memory source, plain and sealed object stores, and the fake broker."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from harness_core.skills import (
    InMemorySkillContentSource,
    StoredSkillBundleBuilder,
    TarSkillMaterializer,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.skills import (
    SkillBundleBuilderContract,
    SkillContentSourceContract,
    SkillMaterializerContract,
)
from harness_core.testing.sandbox import FakeContractEnv, FakeSandboxBroker
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore

if TYPE_CHECKING:
    from collections.abc import Mapping

_START = datetime(2026, 10, 3, tzinfo=UTC)
PNG = b"\x89PNG\r\n\x1a\n\x00\xff\xfe"


def in_memory_content(name: str, marker: str) -> dict[str, bytes]:
    return {
        "SKILL.md": f"---\nname: {name}\ndescription: {marker}\n---\n# {marker}\n".encode(),
        "scripts/run.py": f"print({marker!r})\n".encode(),
        "assets/images/logo.png": PNG + marker.encode(),
        "assets/fonts/x.woff2": b"wOF2\x00\x01" + marker.encode(),
    }


class InMemoryHarness:
    def __init__(self, *, sealed: bool = False) -> None:
        self.source = InMemorySkillContentSource()
        self.tenant, self.other_tenant = "tenant-a", "tenant-b"
        self.scope = ObjectScope(self.tenant, "session-1")
        self.other_scope = ObjectScope(self.other_tenant, "session-1")
        self.plain_objects = InMemoryBinaryObjectStore()
        self.objects = (
            SealedObjectStore(self.plain_objects, InMemoryKeyProvider()) if sealed else self.plain_objects
        )
        self.workspaces = InMemoryWorkspaceStore(clock=ManualClock(_START))
        self.builder = StoredSkillBundleBuilder(source=self.source, objects=self.objects, workspaces=self.workspaces)

    def content(self, name: str, marker: str) -> dict[str, bytes]:
        return in_memory_content(name, marker)

    async def publish(self, tenant: str, name: str, files: Mapping[str, bytes]) -> str:
        return self.source.publish(tenant, name, files)

    async def delete(self, tenant: str, name: str) -> None:
        self.source.delete(tenant, name)


class TestInMemorySkillContentSource(SkillContentSourceContract):
    @pytest.fixture
    def harness(self) -> InMemoryHarness:  # type: ignore[override]
        return InMemoryHarness()


class TestStoredSkillBundleBuilder(SkillBundleBuilderContract):
    @pytest.fixture(params=[False, True], ids=["plain", "sealed"])
    def harness(self, request: pytest.FixtureRequest) -> InMemoryHarness:  # type: ignore[override]
        return InMemoryHarness(sealed=request.param)


class FakeMaterializerHarness:
    def __init__(self) -> None:
        clock = ManualClock(_START)
        self.scope = ObjectScope("tenant-a", "session-1")
        self.objects = InMemoryBinaryObjectStore()
        self.workspaces = workspaces = InMemoryWorkspaceStore(clock=clock)
        self.bundles = StoredSkillBundleBuilder(
            source=InMemorySkillContentSource(), objects=self.objects, workspaces=workspaces
        )
        self.materializer = TarSkillMaterializer(objects=self.objects, workspaces=workspaces)
        self.broker = FakeSandboxBroker(clock=clock)
        self.env = FakeContractEnv(clock)

    def corrupt_objects(self) -> None:
        self.objects.tamper()


class TestTarSkillMaterializerOnTheFakeBroker(SkillMaterializerContract):
    @pytest.fixture
    def harness(self) -> FakeMaterializerHarness:  # type: ignore[override]
        return FakeMaterializerHarness()
