"""EC-6, EC-7, EC-11: registry refusals and their logs; in-memory port adapters pass their contracts."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest

from harness_core.engines.registry import AllowlistEngineRegistry
from harness_core.engines.types import EngineVersion
from harness_core.testing.contracts.interactions import (
    BridgeHarness,
    InteractionBridgeBasics,
)
from harness_core.testing.engines.contract import (
    EngineRegistryContract,
    RegistryHarness,
)
from harness_core.testing.engines.fake_engine import FAKE_VERSION, FakeEngineAdapter
from harness_core.testing.engines.gateway import FakeInteractionBridge

ORPHAN = EngineVersion("codex", "rust-v0.160.0", "sha256:" + "c" * 64)


@dataclass
class _Registry:
    registry: AllowlistEngineRegistry
    allowed: EngineVersion = FAKE_VERSION
    unregistered_kind: EngineVersion = ORPHAN


class TestAllowlistRegistryContract(EngineRegistryContract):
    @pytest.fixture
    def registry_harness(self) -> RegistryHarness:
        return _Registry(AllowlistEngineRegistry([FakeEngineAdapter()], [FAKE_VERSION, ORPHAN]))


def test_ec7_each_refusal_is_logged_once_with_a_reason(caplog: pytest.LogCaptureFixture) -> None:
    registry = AllowlistEngineRegistry([FakeEngineAdapter()], [FAKE_VERSION])
    caplog.set_level(logging.INFO)
    with pytest.raises(Exception, match="not allowed"):
        registry.adapter_for(EngineVersion("jcode", "9", FAKE_VERSION.binary_digest))
    refused = [r for r in caplog.records if getattr(r, "harness_event", None) == "engine.version.refused"]
    assert len(refused) == 1
    assert refused[0].levelno == logging.WARNING
    assert refused[0].harness == {  # type: ignore[attr-defined]
        "reasonCode": "version_not_allowed",
        "engineKind": "jcode",
        "engineVersion": "9",
    }


def test_two_adapters_for_one_kind_are_refused() -> None:
    with pytest.raises(ValueError, match="Two adapters"):
        AllowlistEngineRegistry([FakeEngineAdapter(), FakeEngineAdapter()], [])


def test_allowed_lists_the_pins() -> None:
    assert AllowlistEngineRegistry([], [FAKE_VERSION]).allowed() == frozenset({FAKE_VERSION})



@dataclass
class _Bridge:
    bridge: FakeInteractionBridge

    def decide(self, interaction_id: str, decision: str) -> None:
        self.bridge.resolve(interaction_id, decision)  # type: ignore[arg-type]


class TestFakeInteractionBridgeContract(InteractionBridgeBasics):
    @pytest.fixture
    def bridge_harness(self) -> BridgeHarness:
        return _Bridge(FakeInteractionBridge())
