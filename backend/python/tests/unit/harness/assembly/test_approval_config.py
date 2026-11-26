"""AP-7, AP-8: the durable run's approval policy comes from configuration (X-6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.harness.approvals import approval_policy
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.unpinned import ConfiguredApprovals
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import ToolEffect, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.store_outage import outage_configuration_service

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic import JsonValue

    from harness_core.effects.types import EffectKind

LOG = HarnessLog.named("tests.approvals")
CALL = ToolCall(id="c", name="t", arguments={})


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


def _effect(kind: EffectKind) -> ToolEffect:
    return ToolEffect(tool_id_for("t"), kind, False, SinkDescriptor(egress="external", args_leave_boundary=True))


def _decide(document: JsonValue, kind: EffectKind = "write", agent: str | None = "a1") -> str:
    return approval_policy(document, org_id="org-1", agent_key=agent, log=LOG)(CALL, _effect(kind))


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        (None, "allow"),
        ({}, "allow"),
        ({"default": {"writes": "ask"}}, "ask"),
        ({"agents": {"a1": {"writes": "ask"}}}, "ask"),
        ({"agents": {"a2": {"writes": "ask"}}}, "allow"),
        ({"default": {"writes": "ask"}, "agents": {"a1": {"writes": "allow"}}}, "ask"),
        ({"default": {"writes": "ask"}, "agents": {"a1": {"writes": "deny"}}}, "deny"),
    ],
)
def test_ap7_layers_from_the_document_narrow(document: JsonValue, expected: str) -> None:
    assert _decide(document) == expected


@pytest.mark.parametrize(
    "document",
    ["ask", {"default": {"writes": "sometimes"}}, {"agents": ["a1"]}, {"reviewerLists": {"org-1": ["u-1"]}}],
)
def test_ap5_an_unreadable_layer_fails_closed(
    document: JsonValue, caplog: pytest.LogCaptureFixture
) -> None:
    policy = approval_policy(document, org_id="org-1", agent_key="a1", log=LOG)
    assert policy(CALL, _effect("write")) == "ask"
    assert policy(CALL, _effect("read")) == "allow"
    assert sum("approvals.config_invalid" in r.getMessage() for r in caplog.records) == 1


async def test_ap7_a_configuration_read_failure_fails_closed() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=ConnectionError("etcd down"))
    policy = await ConfiguredApprovals(config).policy(org_id="org-1", agent_key=None, log=LOG)
    assert policy(CALL, _effect("execute")) == "ask"


async def test_fm_f2_a_store_outage_through_the_real_configuration_service_fails_closed() -> None:
    policy = await ConfiguredApprovals(outage_configuration_service()).policy(org_id="org-1", agent_key=None, log=LOG)
    assert policy(CALL, _effect("write")) == "ask"


async def _runtime_with(document: Any) -> Any:  # noqa: ANN401
    services = w.Services()
    if document is not None:
        services.config.put("/services/agentHarness/approvals", document)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    return await assembler.build(w.claim(w.pinned_spec(tool_ids=[])))  # type: ignore[arg-type]


async def test_ap8_the_assembled_run_uses_the_configured_policy_not_allow_all() -> None:
    runtime = await _runtime_with({"default": {"writes": "ask"}})
    assert runtime.approval(CALL, _effect("write")) == "ask"
    assert runtime.approval(CALL, _effect("read")) == "allow"
    unconfigured = await _runtime_with(None)
    assert unconfigured.approval(CALL, _effect("write")) == "allow"
