"""The matrix run's spec and the production engine assembler wired to the lane's services.

``PipesHubEngineAssembler`` (E1) builds the attempt exactly as a worker does: ``build_tool_environment`` from the
pinned spec, the run's ``ToolSource``s, ``engine_grant``, ``RunToolEndpoint`` over ``RegistryToolPort`` with
``ENGINE_APPROVALS`` and the profile's and org's layers. The only factory replaced is the loop's model (an engine
run's model is reached through E3, not through the run's ``ResolvedModel``; the environment only reads its flags).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.harness.assembly.model import ResolvedModel
from tests.unit.harness.engines.durable_stack import engine_spec
from tests.unit.harness.engines.matrix.services import (
    AGENT,
    JIRA_INSTANCE,
    MCP_INSTANCE,
)

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext
    from tests.unit.harness.engines.matrix.org import Org

JIRA_READ = f"jira@{JIRA_INSTANCE}/search_issues"
JIRA_WRITE = f"jira@{JIRA_INSTANCE}/create_issue"


def mcp_tool(name: str) -> str:
    return f"mcp@{MCP_INSTANCE}/{name}"


def matrix_spec(
    org: Org,
    *,
    kind: str,
    tools: tuple[str, ...] = (),
    mcp: bool = False,
    manifest_digest: str = "sha256:" + "0" * 64,
    pinned: dict[str, Any] | None = None,
    skills: tuple[tuple[str, str], ...] = (),
) -> dict[str, Any]:
    """A v3 engine spec as Node pins it for alice's run of the matrix agent: its knowledge is the seeded drive,
    ``tools`` its connector and MCP grants, ``skills`` its skills by name and bundle digest, ``pinned`` the model,
    route and access of another configured model (the live matrix's Azure entry) instead of the scripted one."""
    spec = engine_spec(kind=kind, tools=tools, skills=skills, **(pinned or {}))
    spec["principalRef"] = f"p_{org.user_id('alice')}"
    extension = spec["extensions"]["x.pipeshub"]
    extension["agent"] = {"agentKey": AGENT, "definitionVersion": 1, "mode": "quick"}
    extension["knowledge"] = {"apps": [org.connector_id], "kb": [], "strictScope": False}
    extension["mcp"] = [{"instanceId": MCP_INSTANCE, "manifestDigest": manifest_digest}] if mcp else []
    return spec


async def scripted_model(run: RunContext, services: object) -> ResolvedModel:
    from tests.unit.agents.adapter.conftest import FakeChatModel

    return ResolvedModel(
        raw_llm=FakeChatModel(), provider="openai", is_multimodal=False, context_length=128_000, is_reasoning=False,
        port=None,  # type: ignore[arg-type]
    )


__all__ = ["JIRA_READ", "JIRA_WRITE", "matrix_spec", "mcp_tool", "scripted_model"]
