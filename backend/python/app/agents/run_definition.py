"""What an agent grants a run: the projection Node pins as the RunSpec upper bound.

Node authorizes an agent run with the caller's own credential and keeps this
projection on the run; the claim resolves the pinned ``RunSpec`` from it
(REPORT 6.1.1). It carries names and ids only, never credentials or prompts.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    model_serializer,
)

from app.agents.agent_environment import stored_agent_environment
from app.agents.agent_gates import stored_agent_gates
from app.agents.role_models import stored_role_models
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.skills import SkillBundleInvalid, SkillRef

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from app.harness.assembly.descriptor_pins import ToolDescriptorSource

# The system assistant definition (ASSIST1): the built-in assistant's Agent mode, under the name legacy gives
# Universal Agent Mode. Node's `ASSISTANT_AGENT_DEFINITION_KEY`.
ASSISTANT_AGENT_KEY = "agentIdPlaceholder"

# Node's bound (`MAX_CHILDREN_PER_RUN`): its agent-definition schema refuses more.
MAX_CHILDREN_PER_RUN = RELEASE_DEFAULTS.children.max_per_run


class _Wire(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid", frozen=True)


class RunDefinitionTool(_Wire):
    name: str
    full_name: str = Field(alias="fullName")
    # The tool's descriptor digest when admission could bind one (CPS PN-2); Node pins it as digestVersion 2.
    descriptor_digest: str | None = Field(default=None, alias="descriptorDigest")

    @model_serializer(mode="wrap")
    def _wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in ("descriptorDigest", "descriptor_digest"):
            if key in data and data[key] is None:
                del data[key]
        return data


class RunDefinitionToolset(_Wire):
    name: str
    instance_id: str | None = Field(default=None, alias="instanceId")
    tools: tuple[RunDefinitionTool, ...] = ()


UnlistedMcpReason = Literal["sign_in", "unreachable", "not_approved", "no_tools", "flagged"]
# The reason code the description scanner holds a tool back with (MX-04), as the MCP loader records it.
FLAGGED_REASON = "mcp_tool_description_flagged"


class RunDefinitionMcpUnavailable(_Wire):
    """Why the assistant's server got no tool (MCPNOTE-1): Node names it to the person, with what to do."""

    reason: UnlistedMcpReason
    # The person can sign in to it again from the chat: an OAuth server, whose sign-in is always the caller's own.
    sign_in_here: bool = Field(default=False, alias="signInHere")


class RunDefinitionMcpServer(_Wire):
    instance_id: str = Field(alias="instanceId")
    name: str
    type_id: str | None = Field(default=None, alias="typeId")
    tools: tuple[RunDefinitionTool, ...] = ()
    unavailable: RunDefinitionMcpUnavailable | None = None
    # D-MCP-5: tools of the server the description scanner held back from the run, by name; Node tells the person.
    held: tuple[str, ...] = ()
    # D-LAZY-2: granted whole, its tools named and pinned at the run's first use of it; asked for by Node only for a
    # worker that lists at first use (``mcpFirstUse``).
    at_first_use: bool = Field(default=False, alias="atFirstUse")

    @model_serializer(mode="wrap")
    def _wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("unavailable") is None:
            data.pop("unavailable", None)
        if not data.get("held"):
            data.pop("held", None)
        for key in ("atFirstUse", "at_first_use"):
            if data.get(key) is False:
                data.pop(key, None)
        return data


class RunDefinitionSourceLimit(_Wire):
    record_groups: tuple[str, ...] = Field(default=(), alias="recordGroups")
    records: tuple[str, ...] = ()


class RunDefinitionKnowledge(_Wire):
    connector_id: str = Field(alias="connectorId")
    is_kb: bool = Field(alias="isKb")
    # The source's limit to some of its record groups and records (main #4255); absent for a whole source. Node pins
    # it (SYNC4 S12): empty lists limit the source to nothing.
    limit: RunDefinitionSourceLimit | None = None

    @model_serializer(mode="wrap")
    def _wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if data.get("limit") is None:
            data.pop("limit", None)
        return data


class RunDefinitionModel(_Wire):
    model_key: str = Field(alias="modelKey")
    model_name: str = Field(alias="modelName")
    provider: str
    is_reasoning: bool = Field(alias="isReasoning")


class RunDefinitionRoleModel(_Wire):
    model_key: str = Field(alias="modelKey")
    model_name: str = Field(alias="modelName")
    provider: str


class RunDefinitionSkill(_Wire):
    name: str
    version: str | None = None
    # Content digest of the granted version, pinned by Node as `x.pipeshub.skills[].bundleDigest` (X-8).
    bundle_digest: str | None = Field(default=None, alias="bundleDigest")


class AgentRunDefinition(_Wire):
    agent_key: str = Field(alias="agentKey")
    definition_version: int = Field(alias="definitionVersion", ge=1)
    models: tuple[RunDefinitionModel, ...] = ()
    default_reasoning_effort: str | None = Field(default=None, alias="defaultReasoningEffort")
    toolsets: tuple[RunDefinitionToolset, ...] = ()
    mcp_servers: tuple[RunDefinitionMcpServer, ...] = Field(default=(), alias="mcpServers")
    knowledge: tuple[RunDefinitionKnowledge, ...] = ()
    skills: tuple[RunDefinitionSkill, ...] = ()
    web_search: str | None = Field(default=None, alias="webSearch")
    # Child runs this agent may delegate to per run (P6.1); Node pins it as `budgets.maxChildren`.
    # Unset (absent from the wire): the chat mode's default applies (K-L: deep delegates).
    max_children: int | None = Field(default=None, alias="maxChildren", ge=0, le=MAX_CHILDREN_PER_RUN)
    # The agent's engine profile narrowing (E4a), passed as stored: Node checks it against the org
    # ceiling at admission and refuses it there, so a bad one never runs wider. Unset: the loop.
    engine_profile: dict[str, JsonValue] | None = Field(default=None, alias="engineProfile")
    # The org cloud account (`cloud/<id>`) the agent's editor pinned for its engine runs (AG1); Node
    # uses it when the run names no account of its own. Unset: admission chooses (A1).
    model_account_id: str | None = Field(default=None, alias="modelAccountId")
    # The agent's owner (its creator) by user id: what they put on the agent is shared with whoever runs it (CPS,
    # CPD-7's ESR-10). Unset when the creator cannot be resolved; nothing is shared through an unknown owner.
    owner: str | None = None
    # The agent's model per sub-agent role (MA1): Node asks for a child's model from it and its pin allows or
    # refuses it. Absent from the wire when the agent sets none, so such a definition reads as it always did.
    role_models: dict[str, RunDefinitionRoleModel] = Field(default_factory=dict, alias="roleModels")
    # The agent's own run gates (WP-31 D: plan approval, checks), held to every run of it: Node merges them with a
    # turn's own at admission and a gate only narrows. Unset (absent from the wire): the agent sets none.
    gates: dict[str, JsonValue] | None = None
    # The agent's environment for its engine runs (C20.18, WP-100): Node pins it as ``x.pipeshub.environment`` on each
    # engine run of the agent. Unset (absent from the wire): the agent sets none.
    environment: dict[str, JsonValue] | None = None

    @model_serializer(mode="wrap")
    def _wire(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        for key in ("maxChildren", "max_children", "engineProfile", "engine_profile", "modelAccountId", "model_account_id", "owner", "gates", "environment"):
            if key in data and data[key] is None:
                del data[key]
        for key in ("roleModels", "role_models"):
            if key in data and not data[key]:
                del data[key]
        return data


def _max_children(value: object) -> int | None:
    """The agent's delegation bound, clamped to the cap; unset when the agent sets
    none, so the mode decides; anything but a whole number grants none."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, MAX_CHILDREN_PER_RUN))


def _engine_profile(value: object) -> dict[str, JsonValue] | None:
    """The agent's engine narrowing as stored. A stored value that is not an object becomes one Node
    refuses as invalid, so the agent is refused rather than quietly run on the loop."""
    if value is None:
        return None
    if isinstance(value, dict):
        return cast("dict[str, JsonValue]", value)
    return {"invalid": True}


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _tools(raw: object) -> tuple[RunDefinitionTool, ...]:
    tools: list[RunDefinitionTool] = []
    for tool in raw if isinstance(raw, list) else []:
        if not isinstance(tool, dict):
            continue
        name, full_name = _text(tool.get("name")), _text(tool.get("fullName"))
        if name and full_name:
            tools.append(RunDefinitionTool(name=name, fullName=full_name))
    return tuple(tools)


def _unavailable(raw: object) -> RunDefinitionMcpUnavailable | None:
    return RunDefinitionMcpUnavailable.model_validate(raw) if isinstance(raw, dict) else None


def build_run_definition(
    agent: dict[str, Any],
    *,
    agent_key: str,
    actions_enabled: bool,
    mcp_enabled: bool,
    web_search_provider: str | None,
    definition_version: int | None = None,
) -> AgentRunDefinition:
    """Project an enriched ``get_agent`` document. The platform's Actions and MCP
    switches apply here, as they do in ``chat_stream``: a disabled platform
    feature grants nothing, whatever the agent lists.

    ``definition_version`` is the agent's published version (OWD-14). Only the
    system assistant, which has no versions, goes without one: it is stamped
    with its document's revision time."""
    toolsets = [
        RunDefinitionToolset(
            name=name,
            instanceId=_text(toolset.get("instanceId")),
            tools=_tools(toolset.get("tools")),
        )
        for toolset in (agent.get("toolsets") or []) if actions_enabled and isinstance(toolset, dict)
        if (name := _text(toolset.get("name")))
    ]
    mcp_servers = [
        RunDefinitionMcpServer(
            instanceId=instance_id,
            name=_text(server.get("name")) or instance_id,
            typeId=_text(server.get("typeId")),
            tools=_tools(server.get("tools")),
            unavailable=_unavailable(server.get("unavailable")),
            held=tuple(str(name) for name in server.get("held") or ()),
            atFirstUse=server.get("atFirstUse") is True,
        )
        for server in (agent.get("mcpServers") or []) if mcp_enabled and isinstance(server, dict)
        if (instance_id := _text(server.get("instanceId")))
    ]
    knowledge = _knowledge(agent.get("knowledge") or [])
    models = [
        RunDefinitionModel(
            modelKey=model_key,
            modelName=_text(model.get("modelName")) or model_key,
            provider=_text(model.get("provider")) or "unknown",
            isReasoning=bool(model.get("isReasoning", False)),
        )
        for model in (agent.get("models") or []) if isinstance(model, dict)
        if (model_key := _text(model.get("modelKey")))
    ]
    skills = [
        RunDefinitionSkill(name=name, version=_text(skill.get("version")))
        for skill in (agent.get("skills") or []) if isinstance(skill, dict)
        if (name := _text(skill.get("name")))
    ]
    version = definition_version or agent.get("updatedAtTimestamp") or agent.get("createdAtTimestamp")
    return AgentRunDefinition(
        agentKey=agent_key,
        definitionVersion=version if isinstance(version, int) and version > 0 else 1,
        models=tuple(models),
        defaultReasoningEffort=_text(agent.get("defaultReasoningEffort")),
        toolsets=tuple(toolsets),
        mcpServers=tuple(mcp_servers),
        knowledge=tuple(knowledge),
        skills=tuple(skills),
        webSearch=web_search_provider,
        maxChildren=_max_children(agent.get("maxChildren")),
        engineProfile=_engine_profile(agent.get("engineProfile")),
        modelAccountId=_text(agent.get("modelAccountId")),
        roleModels={
            role: RunDefinitionRoleModel(**entry) for role, entry in stored_role_models(agent.get("roleModels")).items()
        },
        gates=stored_agent_gates(agent.get("gates")),
        environment=stored_agent_environment(agent.get("environment")),
    )


def _knowledge(entries: list[object]) -> list[RunDefinitionKnowledge]:
    """Each knowledge source, with its limit read as main reads it (``knowledge_scope._narrowed_to``)."""
    from app.modules.agents.knowledge_scope import _narrowed_to  # pyright: ignore[reportPrivateUsage]

    items = [item for item in entries if isinstance(item, dict)]
    source_ids = {cid for item in items if (cid := _text(item.get("connectorId")))}
    out: list[RunDefinitionKnowledge] = []
    for item in items:
        connector_id = _text(item.get("connectorId"))
        if not connector_id:
            continue
        narrowed = _narrowed_to(item, source_ids)
        limit = (
            RunDefinitionSourceLimit(recordGroups=tuple(narrowed["recordGroups"]), records=tuple(narrowed["records"]))
            if narrowed is not None
            else None
        )
        out.append(
            RunDefinitionKnowledge(
                connectorId=connector_id,
                isKb=(str(item.get("type") or "").strip().upper() == "KB"),
                limit=limit,
            )
        )
    return out


class SkillDigestSource(Protocol):
    async def content_digest(self, tenant_id: str, skill: SkillRef) -> str | None: ...


async def pin_skill_content(
    definition: AgentRunDefinition, source: SkillDigestSource, tenant_id: str
) -> AgentRunDefinition:
    """Each granted skill with the digest of its content now, so a run sees this content however the
    skill changes later. A skill with nothing to pin keeps no digest and Node pins it by name and version."""

    async def pinned(skill: RunDefinitionSkill) -> RunDefinitionSkill:
        try:
            ref = SkillRef(skill.name, skill.version)
        except SkillBundleInvalid:
            return skill
        return skill.model_copy(update={"bundle_digest": await source.content_digest(tenant_id, ref)})

    skills = await asyncio.gather(*(pinned(skill) for skill in definition.skills))
    return definition.model_copy(update={"skills": tuple(skills)})


async def with_offered_skills(
    definition: AgentRunDefinition,
    graph: object,
    tenant_id: str,
    viewers: tuple[str | None, ...],
    *,
    enabled: bool,
) -> AgentRunDefinition:
    """The definition granting the skills the run is offered (``skills.offer``, FIX-SKILLS): the agent's own,
    then the builtins and, for an agent that assigns none, the person's own skills. Skills off for the deployment
    or the org: none, as the loop then has none."""
    if not enabled:
        return definition.model_copy(update={"skills": ()})
    from app.agents.agent_loop.skills.offer import offered_skills

    assigned = frozenset(skill.name for skill in definition.skills)
    offer = await offered_skills(graph, tenant_id, viewers, assigned)  # type: ignore[arg-type]
    added = tuple(RunDefinitionSkill(name=m.name) for m in offer if m.name not in assigned)
    return definition.model_copy(update={"skills": (*definition.skills, *added)})


async def pin_tool_descriptors(
    definition: AgentRunDefinition, source: ToolDescriptorSource, *, mcp_approvals: bool
) -> AgentRunDefinition:
    """Each granted tool with the digest of the descriptor it is granted under: a toolset tool's as the release
    declares it; an MCP tool's as its manifest names it (an admin's approval, or live discovery for the assistant
    while the hub is off), only when ``mcp_approvals``. A tool with nothing to bind keeps no digest, and Node pins it
    as before."""
    toolsets = tuple(
        toolset.model_copy(update={"tools": tuple(
            tool.model_copy(update={"descriptor_digest": source.toolset_tool(toolset.name, tool.name)})
            for tool in toolset.tools
        )})
        for toolset in definition.toolsets
    )
    servers: list[RunDefinitionMcpServer] = []
    for server in definition.mcp_servers:
        approved = await source.approved_mcp_tools(server.instance_id) if mcp_approvals else None
        servers.append(server.model_copy(update={"tools": tuple(
            tool.model_copy(update={"descriptor_digest": approved.tools.get(tool.name) if approved is not None else None})
            for tool in server.tools
        )}))
    return definition.model_copy(update={"toolsets": toolsets, "mcp_servers": tuple(servers)})


def held_mcp_tools(failures: Iterable[Mapping[str, Any]]) -> dict[str, list[str]]:
    """The tools of each server the description scanner held back at admission (D-MCP-5), by instance id."""
    held: dict[str, list[str]] = {}
    for failure in failures:
        instance = failure.get("instanceId")
        if failure.get("reason") != FLAGGED_REASON or not isinstance(instance, str):
            continue
        names = [str(name) for name in failure.get("tools") or () if isinstance(name, str)]
        held[instance] = sorted({*held.get(instance, []), *names})
    return held


def hold_flagged_tools(definition: AgentRunDefinition, failures: Iterable[Mapping[str, Any]]) -> AgentRunDefinition:
    """A saved agent's MCP tools less those the description scanner held back at admission, each server naming them
    (``held``), so the run is never pinned to a tool its claim will not register."""
    held = held_mcp_tools(failures)
    if not held:
        return definition
    servers = []
    for server in definition.mcp_servers:
        names = held.get(server.instance_id, [])
        servers.append(server.model_copy(update={
            "tools": tuple(t for t in server.tools if t.name not in names),
            "held": tuple(names),
        }) if names else server)
    return definition.model_copy(update={"mcp_servers": tuple(servers)})


def unlisted_mcp_reason(failures: Collection[str], *, hub: bool) -> UnlistedMcpReason:
    """Why a server's tools could not be named, from what its listing at admission recorded (``failures``: the
    reason codes of ``mcp_tool_load_failures``). A refused sign-in comes first: nothing else can be checked, or
    approved, until the person signs in again. A server that recorded nothing was dropped by the hub's approvals
    (under the hub) or by a discovery that ran out of time (without it)."""
    from app.agents.agent_loop.mcp_tool_loader import NO_TOOLS_OFFERED
    from app.agents.mcp.failure import SIGN_IN_FAILURES
    from harness_core.mcp_hub.errors import ToolChanged, ToolUnapproved

    seen = frozenset(failures)
    if seen & {reason.value for reason in SIGN_IN_FAILURES}:
        return "sign_in"
    if seen & {ToolUnapproved.reason_code, ToolChanged.reason_code}:
        return "not_approved"
    if FLAGGED_REASON in seen:
        return "flagged"
    if NO_TOOLS_OFFERED in seen:
        return "no_tools"
    if seen:
        return "unreachable"
    return "not_approved" if hub else "unreachable"


async def assistant_mcp_servers(
    servers: list[dict[str, Any]],
    manifests: ToolDescriptorSource,
    *,
    failures: Iterable[Mapping[str, Any]] = (),
    hub: bool = False,
) -> list[dict[str, Any]]:
    """The assistant's authenticated MCP servers with the tools a pin can name. The assistant attaches servers with
    no tool list, so ``manifests`` names them: the MCP hub's approved manifest under the hub, else the tools live
    discovery found (``LiveMcpDescriptors``). A server neither names gets no tool, and the run none of it; it carries
    why (``unlisted_mcp_reason`` over its listing's ``failures``), so the person is told what to do (MCPNOTE-1)."""
    from app.agents.mcp.service import mcp_tool_full_name

    recorded: dict[str, list[str]] = {}
    failures = list(failures)
    for failure in failures:
        if isinstance(instance := failure.get("instanceId"), str) and isinstance(code := failure.get("reason"), str):
            recorded.setdefault(instance, []).append(code)
    held = held_mcp_tools(failures)
    named: list[dict[str, Any]] = []
    for server in servers:
        instance_id = _text(server.get("instanceId"))
        approved = await manifests.approved_mcp_tools(instance_id) if instance_id else None
        names = sorted(approved.tools) if approved is not None else []
        tools = [{"name": name, "fullName": mcp_tool_full_name(server, name)} for name in names]
        entry = {**server, "tools": tools}
        if held.get(instance_id or ""):
            entry["held"] = held[instance_id or ""]
        if not tools:
            reason = unlisted_mcp_reason(recorded.get(instance_id or "", ()), hub=hub)
            entry["unavailable"] = {
                "reason": reason,
                "signInHere": reason == "sign_in" and server.get("authMode") == "oauth",
            }
        named.append(entry)
    return named


__all__ = [
    "ASSISTANT_AGENT_KEY",
    "AgentRunDefinition",
    "SkillDigestSource",
    "assistant_mcp_servers",
    "build_run_definition",
    "held_mcp_tools",
    "hold_flagged_tools",
    "unlisted_mcp_reason",
    "pin_skill_content",
    "pin_tool_descriptors",
    "with_offered_skills",
]
