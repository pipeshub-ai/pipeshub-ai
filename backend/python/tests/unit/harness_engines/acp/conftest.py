from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.testing.engines.contract import new_engine_sandbox
from harness_core.testing.ids import SeededIds
from harness_engines.acp.adapter import AcpAdapter, acp_adapter
from harness_engines.acp.launch import UNSUPPORTED
from harness_engines.acp.testing.fake_agent import (
    FakeAcpAgent,
    install,
    write_behaviour,
)
from harness_engines.acp.testing.profile import fake_profile

if TYPE_CHECKING:
    from harness_core.engines.policy import NativeTool
    from harness_core.engines.types import EngineVersion, JsonValue
    from harness_core.testing.sandbox.egress import FakeEgress
    from harness_core.testing.sandbox.fake import FakeSandboxHandle
    from harness_engines.acp.profiles import AcpAgentProfile


@dataclass
class AcpHarness:
    """Runs the ACP driver against the scripted fake ACP agent in a fake sandbox."""

    profile: AcpAgentProfile = field(default_factory=fake_profile)
    ids: SeededIds = field(default_factory=SeededIds)
    behaviour: dict[str, JsonValue] | None = None
    fakes: dict[str, FakeAcpAgent] = field(default_factory=dict[str, FakeAcpAgent])
    sandboxes: list[FakeSandboxHandle] = field(default_factory=list["FakeSandboxHandle"])
    session_options: dict[str, float] = field(default_factory=dict[str, float])

    def __post_init__(self) -> None:
        self.adapter: AcpAdapter = acp_adapter(self.profile, **self.session_options)

    @property
    def version(self) -> EngineVersion:
        return self.profile.engine

    def native_names(self, tool: NativeTool) -> frozenset[str]:
        # A tool ACP cannot carry (sub-agents) is never shown, whatever the profile names for it.
        return frozenset() if tool in UNSUPPORTED else frozenset(self.profile.native_tools.get(tool, ()))

    @property
    def plan_names(self) -> frozenset[str]:
        return frozenset({self.profile.plan_tool}) if self.profile.plan_tool else frozenset()

    def exposed_name(self, granted: str) -> str:
        return self.profile.exposed_tool_name(granted)

    async def new_sandbox(self, egress: FakeEgress) -> FakeSandboxHandle:
        sandbox = await new_engine_sandbox(egress, ids=self.ids)
        self.fakes[sandbox.id] = install(sandbox)
        if self.behaviour is not None:
            write_behaviour(sandbox, self.behaviour)
        self.sandboxes.append(sandbox)
        return sandbox

    def secrets(self) -> frozenset[str]:
        return frozenset()


@pytest.fixture
def acp_harness() -> AcpHarness:
    return AcpHarness()
