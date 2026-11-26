"""R33JGAPS-FU-3: a service-account agent's durable run asks the person who started it (ADR-OWD-09 amendment R34).

Today's loop has nobody to show a service-account run's card to, so main's gate reads its Ask as Block
("unattended"). A durable run always has someone: the kernel's approval interaction reaches the run's actor (the
person in the chat, or a schedule's actor through the inbox), and the approvals queue routes it to whoever else may
decide. So the run asks; an admin's Block still refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app import edition_config
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.effects.types import refresh_policy
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver import AssembledRuntime

JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
JIRA_CREATE = f"jira@{w.JIRA}/create_issue"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


@pytest.fixture(autouse=True)
def _org_rules() -> Iterator[None]:
    with patch.object(edition_config, "organization_toolset_tool_rules", True):
        yield


async def _service_identity(*_: Any, **__: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"userId": w.ACTOR, "orgId": w.ORG, "userEmail": "user@corp.example", "fullName": "Uma"}


async def _build(services: w.Services) -> AssembledRuntime:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    with (
        patch("app.api.routes.agent._enrich_user_info_for_service_account_agent_chat", _service_identity),
        patch("app.api.routes.agent._resolve_service_account_caller_identity", _service_identity),
        patch("app.api.routes.agent._service_account_artifact_owner", _owner),
    ):
        runtime = await w.first_use(await assembler.build(w.claim(w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE]))))
    await refresh_policy(runtime.approval)
    return runtime


async def _owner(*_: Any, **__: Any) -> str:  # noqa: ANN401
    return w.ACTOR


def _decide(runtime: AssembledRuntime, name: str) -> str:
    effect = runtime.tools.describe(name)
    assert effect is not None
    return runtime.approval(ToolCall(id="c", name=name, arguments={}), effect)


def _service_agent(rule: str) -> w.Services:
    services = w.Services(w.agent_doc(isServiceAccount=True))
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": rule}})
    return services


async def test_r34_a_service_account_agents_ask_rule_asks_the_person_instead_of_refusing() -> None:
    runtime = await _build(_service_agent("ask"))
    assert runtime.approval is not None
    assert _decide(runtime, "jira__create_issue") == "ask", "a service-account run refused a write it should ask"
    assert _decide(runtime, "jira__search_issues") == "allow"


async def test_r34_a_service_account_agents_admin_block_still_refuses() -> None:
    runtime = await _build(_service_agent("block"))
    assert _decide(runtime, "jira__create_issue") == "deny"


async def test_r34_a_user_principal_agent_asks_the_same() -> None:
    services = w.Services()
    services.config.put(get_toolset_tool_rules_path(w.JIRA), {"tools": {"create_issue": "ask"}})
    runtime = await _build(services)
    assert _decide(runtime, "jira__create_issue") == "ask"
