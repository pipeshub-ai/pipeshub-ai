from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.types import EngineVersion
from harness_core.testing.engines.contract import new_engine_sandbox
from harness_core.testing.ids import SeededIds
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.opencode.launch import NATIVE_TOOLS, exposed_tool_name
from harness_engines.opencode.testing.fake_server import (
    DIGEST,
    VERSION,
    FakeOpencode,
    install,
)

if TYPE_CHECKING:
    from harness_core.engines.policy import NativeTool
    from harness_core.testing.sandbox.egress import FakeEgress
    from harness_core.testing.sandbox.fake import FakeSandboxHandle

PINNED = EngineVersion("opencode", VERSION, DIGEST)


async def _no_wait(_seconds: float) -> None:
    return None


@dataclass
class OpencodeHarness:
    """Runs the opencode adapter against the recorded fake opencode in a fake sandbox."""

    ids: SeededIds = field(default_factory=SeededIds)
    passwords: list[str] = field(default_factory=list[str])
    fakes: dict[str, FakeOpencode] = field(default_factory=dict[str, FakeOpencode])
    binary_version: str = VERSION
    version: EngineVersion = PINNED
    # What the broker vouches for in the engine image (EH2): nothing, so the adapter hashes for itself.
    vouched: dict[str, str] = field(default_factory=dict[str, str])

    def __post_init__(self) -> None:
        self.adapter = OpencodeAdapter(password=self._password, sleep=_no_wait)

    def _password(self) -> str:
        secret = f"server-password-{self.ids.new_id('pw')}"
        self.passwords.append(secret)
        return secret

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        return frozenset(NATIVE_TOOLS[tool])

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({"todowrite"})

    def exposed_name(self, granted: str) -> str:
        return exposed_tool_name(granted)

    async def new_sandbox(self, egress: FakeEgress) -> FakeSandboxHandle:
        sandbox = await new_engine_sandbox(egress, ids=self.ids, verified_binaries=self.vouched)
        self.fakes[sandbox.id] = install(sandbox, version=self.binary_version, digest=self.version.binary_digest)
        return sandbox

    def secrets(self) -> frozenset[str]:
        return frozenset(self.passwords)


@pytest.fixture
def opencode_harness() -> OpencodeHarness:
    return OpencodeHarness()
