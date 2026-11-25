"""An agent's definition as one environment hands it to another, and its re-binding in the importing one (C4.08).

The portable form (``contracts/harness/ext/pipeshub/api/portable-agent.schema.json``) is built field by field from
what it names, so a field an agent gains later is never carried until it is named here. Every reference is a name:
a model by provider and model name, an action by its instance's name, an MCP server by its instance's name, a
knowledge source by its connector's name and type, a skill by name. The agent's own gates (plan approval, result
checks) and its environment (setup, variables, cache folders) travel as they are. Ids, credentials (the model account), sharing,
the owner team and the service-account switch stay behind.

An import binds a name only when exactly one of the importer's own objects carries it (for a model the entry
marked default settles a tie); anything else is left out of the new agent and reported, never guessed. A knowledge
source limited to some folders or files is never re-bound to the whole source: that would widen what it searches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

from app.agents.agent_environment import (
    ENVIRONMENT_FIELD,
    AgentEnvironmentRefused,
    parse_agent_environment,
)
from app.agents.agent_gates import GATES_FIELD, AgentGatesRefused, parse_agent_gates
from app.agents.role_models import stored_role_models
from app.harness.access.configured import machine_name, models_of
from app.harness.contracts.generated.pipeshub.api.portable_agent import PortableAgent
from app.modules.agents.knowledge_scope import (
    UnreadableSourceLimitError,
    read_source_limit,
)
from app.services.graph_db.common.utils import decode_agent_json_fields

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

ExcludedKind = Literal["modelAccount", "ownerTeam", "serviceAccount", "sharing"]
RebindReason = Literal["not_found", "ambiguous", "limited", "not_reasoning", "unverified", "no_tools"]

_AMBIGUOUS = "Several here match; choose one."


def _members(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _items(value: object) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", v) for v in cast("list[object]", value) if isinstance(v, dict)] if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _same(a: object, b: object) -> bool:
    return _text(a).casefold() == _text(b).casefold() and bool(_text(a))


def _web_search(value: object) -> str | None:
    provider = (_text(_members(value).get("provider")) or _text(value)).lower()
    return provider or None


def _tool(tool: Mapping[str, Any], owner: str) -> dict[str, str]:
    name = _text(tool.get("name"))
    description: object = tool.get("description")
    return {
        "name": name,
        "fullName": _text(tool.get("fullName")) or f"{owner}.{name}",
        "description": description if isinstance(description, str) else "",
    }


def _tools(raw: object, owner: str) -> list[dict[str, str]]:
    return [_tool(tool, owner) for tool in _items(raw) if _text(tool.get("name"))]


def _model_of(raw: object, llm_entries: list[dict[str, Any]]) -> dict[str, str] | None:
    """A stored model (``{modelKey}_{modelName}``, a bare key, or the builder's object) by provider and name;
    None when this deployment no longer configures its key."""
    members = _members(raw)
    if members:
        entry_key, name = _text(members.get("modelKey")), _text(members.get("modelName"))
    elif isinstance(raw, str):
        entry_key, _, name = raw.partition("_")
    else:
        return None
    entry = next((e for e in llm_entries if e.get("modelKey") == entry_key), None)
    if entry is None:
        return None
    served = models_of(entry)
    model_name = name or (served[0] if served else "")
    if not model_name:
        return None
    return {"provider": machine_name(str(entry.get("provider") or "")), "modelName": model_name}


def _knowledge_of(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    name = _text(entry.get("name")) or _text(entry.get("displayName"))
    kind = _text(entry.get("type")).upper()
    # The graph names a source whose connector is gone by its id, typed UNKNOWN: nothing to carry.
    if not name or not kind or kind == "UNKNOWN" or name == entry.get("connectorId"):
        return None
    try:
        limit = read_source_limit(entry.get("filters"))
        items = [i for key in ("recordGroups", "records") for i in limit.get(key, []) if i != entry.get("connectorId")]
    except UnreadableSourceLimitError:
        # An unreadable limit searches nothing when read: carried as limited, never as the whole source.
        return {"name": name, "type": kind, "scope": "limited", "limitedItems": 0}
    return {"name": name, "type": kind, "scope": "limited" if items else "whole", "limitedItems": len(items)}


def _gates_of(raw: object) -> dict[str, Any] | None:
    """The agent's own gates (WP-51) as they travel (WP-56); None: it sets none. Gates the agent could not run
    with are refused (a ValueError naming them) rather than exported as none, which would loosen the agent."""
    try:
        gates = parse_agent_gates(raw)
    except AgentGatesRefused as refused:
        raise ValueError(str(refused)) from None
    return gates or None


def _environment_of(raw: object) -> dict[str, Any] | None:
    """The agent's environment (C20.18) as it travels (WP100-FU-9); None: it sets none. One the worker would refuse
    is refused (a ValueError naming it) rather than exported, as gates are."""
    try:
        environment = parse_agent_environment(raw)
    except AgentEnvironmentRefused as refused:
        message = str(refused)
        raise ValueError(message if message.startswith(ENVIRONMENT_FIELD) else f"{ENVIRONMENT_FIELD}: {message}") from None
    return environment or None


def portable_agent(definition: Mapping[str, Any], llm_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """``definition`` (a version's, or the draft as ``get_agent`` reads it) in its portable form.

    Raises:
        ValueError: the stored gates or environment are not ones an agent can run with.
    """
    stored = decode_agent_json_fields(dict(definition)) or {}
    engine_profile = stored.get("engineProfile")
    role_models = {
        role: {"provider": machine_name(model["provider"]), "modelName": model["modelName"]}
        for role, model in stored_role_models(stored.get("roleModels")).items()
    }
    instructions = _text(stored.get("instructions"))
    agent: dict[str, Any] = {
        "name": _text(stored.get("name")),
        "description": _text(stored.get("description")),
        "startMessage": _text(stored.get("startMessage")),
        "systemPrompt": _text(stored.get("systemPrompt")),
        "instructions": instructions or None,
        "tags": [t for t in (_text(t) for t in cast("list[object]", stored.get("tags") or [])) if t],
        "sendUserContext": stored.get("sendUserContext") is not False,
        "webSearch": _web_search(stored.get("webSearch")),
        "defaultReasoningEffort": _text(stored.get("defaultReasoningEffort")) or None,
        "engineProfile": engine_profile if isinstance(engine_profile, dict) else None,
        "models": [m for m in (_model_of(raw, llm_entries) for raw in cast("list[object]", stored.get("models") or [])) if m],
        "roleModels": role_models,
        "toolsets": [
            {
                "name": _text(ts.get("name")),
                "displayName": _text(ts.get("displayName")) or _text(ts.get("name")),
                "type": _text(ts.get("type")) or "app",
                "instanceName": (
                    _text(ts.get("instanceName")) or _text(ts.get("displayName")) or _text(ts.get("name"))
                    if _text(ts.get("instanceId")) else None
                ),
                "tools": _tools(ts.get("tools"), _text(ts.get("name"))),
            }
            for ts in _items(stored.get("toolsets"))
            if _text(ts.get("name"))
        ],
        "mcpServers": [
            {
                "name": _text(server.get("name")),
                "displayName": _text(server.get("displayName")) or _text(server.get("name")),
                "typeId": _text(server.get("typeId")) or None,
                "allTools": server.get("allTools") is True,
                "tools": _tools(server.get("tools"), _text(server.get("name"))),
            }
            for server in _items(stored.get("mcpServers"))
            if _text(server.get("name"))
        ],
        "knowledge": [k for k in (_knowledge_of(entry) for entry in _items(stored.get("knowledge"))) if k],
        "skills": list(dict.fromkeys(
            name for name in (
                _text(_members(s).get("name")) or _text(s)
                for s in cast("list[object]", stored.get("skills") or [])
            ) if name
        )),
        "environment": _environment_of(stored.get(ENVIRONMENT_FIELD)),
        "gates": _gates_of(stored.get(GATES_FIELD)),
    }
    PortableAgent.model_validate(agent)
    return agent


def excluded_of(live: Mapping[str, Any], definition: Mapping[str, Any]) -> list[ExcludedKind]:
    """What the agent has that its portable form leaves out by design, sorted: the importer sets these again."""
    kinds: list[ExcludedKind] = []
    if _text(definition.get("modelAccountId")):
        kinds.append("modelAccount")
    if _text(live.get("ownerTeam")):
        kinds.append("ownerTeam")
    if live.get("isServiceAccount") is True:
        kinds.append("serviceAccount")
    if live.get("shareWithOrg") is True:
        kinds.append("sharing")
    return kinds


@dataclass(frozen=True)
class ImportCatalog:
    """What the importer can bind to in their own organization: the deployment's configured models, and the
    actions, MCP servers and knowledge sources they can see."""

    llm_entries: list[dict[str, Any]]
    toolset_instances: list[dict[str, Any]]
    mcp_instances: list[dict[str, Any]]
    connectors: list[dict[str, Any]]


@dataclass
class ImportPlan:
    """The create body for the new agent, what it bound, and what the importer must bind again."""

    body: dict[str, Any]
    bound: list[dict[str, str]] = field(default_factory=lambda: [])
    rebind: list[dict[str, str]] = field(default_factory=lambda: [])

    def report(self, kind: str, name: str, reason: RebindReason, detail: str) -> None:
        self.rebind.append({"kind": kind, "name": name, "reason": reason, "detail": detail})


def _one(candidates: Iterable[dict[str, Any]]) -> tuple[dict[str, Any] | None, RebindReason | None]:
    found = list(candidates)
    if len(found) == 1:
        return found[0], None
    return None, "ambiguous" if found else "not_found"


def _bind_model(model: Mapping[str, Any], entries: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, RebindReason | None]:
    serving = [
        e for e in entries
        if machine_name(str(e.get("provider") or "")) == machine_name(str(model["provider"]))
        and model["modelName"] in models_of(e)
    ]
    defaults = [e for e in serving if e.get("isDefault") is True]
    return _one(defaults if len(serving) > 1 and len(defaults) == 1 else serving)


def _model_label(model: Mapping[str, Any]) -> str:
    return f"{model['provider']}/{model['modelName']}"


def plan_import(agent: Mapping[str, Any], catalog: ImportCatalog, *, sole_instance: bool = False) -> ImportPlan:
    """The new agent ``agent`` (a portable form, checked against its schema first) makes in the importer's org.

    ``sole_instance`` (an agent drafted in chat, ADR-AGB-01): an action named without an instance binds to the
    importer's one instance of its type; with several it is listed to choose again. Without it such an action is
    kept unbound, as an export of a platform action is.

    Raises:
        pydantic.ValidationError (a ValueError): ``agent`` is not a portable agent; nothing is planned.
    """
    PortableAgent.model_validate(agent)
    plan = ImportPlan(body={
        "name": agent["name"],
        "description": agent["description"],
        "startMessage": agent["startMessage"],
        "systemPrompt": agent["systemPrompt"],
        "instructions": agent["instructions"] or "",
        "tags": list(agent["tags"]),
        "sendUserContext": agent["sendUserContext"],
        "webSearch": agent["webSearch"],
        "defaultReasoningEffort": agent["defaultReasoningEffort"],
        "shareWithOrg": False,
        "isServiceAccount": False,
        "skills": list(agent["skills"]),
    })
    if agent["engineProfile"] is not None:
        plan.body["engineProfile"] = agent["engineProfile"]
    if agent["gates"] is not None:
        plan.body[GATES_FIELD] = agent["gates"]
    # Held to the rules the agents API and the worker hold it to, before anything is planned: the schema alone lets
    # a secret-looking variable through.
    if (environment := _environment_of(agent.get(ENVIRONMENT_FIELD))) is not None:
        plan.body[ENVIRONMENT_FIELD] = environment
    _plan_models(plan, agent, catalog.llm_entries)
    _plan_toolsets(plan, agent, catalog.toolset_instances, sole_instance=sole_instance)
    _plan_mcp_servers(plan, agent, catalog.mcp_instances)
    _plan_knowledge(plan, agent, catalog.connectors)
    return plan


def _plan_models(plan: ImportPlan, agent: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
    bound: list[tuple[str, dict[str, Any]]] = []
    for model in agent["models"]:
        entry, reason = _bind_model(model, entries)
        if entry is None:
            plan.report("model", _model_label(model), reason or "not_found",
                        _AMBIGUOUS if reason == "ambiguous" else "No configured model here serves it.")
            continue
        bound.append((_model_label(model), {
            "modelKey": entry["modelKey"], "modelName": model["modelName"], "isReasoning": entry.get("isReasoning") is True,
        }))
    if bound and not any(m["isReasoning"] for _, m in bound):
        # The agents API refuses a model list without a reasoning model; no models is the org's default instead.
        for label, _ in bound:
            plan.report("model", label, "not_reasoning",
                        "None of its models is a reasoning model here, so it uses the organization's default model.")
        bound = []
    plan.body["models"] = [m for _, m in bound]
    plan.bound.extend({"kind": "model", "name": label} for label, _ in bound)
    roles: dict[str, dict[str, str]] = {}
    for role, model in agent["roleModels"].items():
        entry, reason = _bind_model(model, entries)
        if entry is None:
            plan.report("roleModel", f"{role}: {_model_label(model)}", reason or "not_found",
                        _AMBIGUOUS if reason == "ambiguous" else "No configured model here serves it.")
            continue
        roles[role] = {"modelKey": entry["modelKey"], "modelName": model["modelName"], "provider": str(entry.get("provider") or "")}
    if roles:
        plan.body["roleModels"] = roles


def _plan_toolsets(
    plan: ImportPlan, agent: Mapping[str, Any], instances: list[dict[str, Any]], *, sole_instance: bool = False,
) -> None:
    toolsets: list[dict[str, Any]] = []
    for ts in agent["toolsets"]:
        base = {"name": ts["name"], "displayName": ts["displayName"], "type": ts["type"], "tools": ts["tools"]}
        if ts["instanceName"] is None:
            of_type = [i for i in instances if _text(i.get("toolsetType")).lower() == ts["name"].lower()]
            if not sole_instance or not of_type:
                toolsets.append(base)
                continue
            if len(of_type) > 1:
                plan.report("toolset", ts["name"], "ambiguous", _AMBIGUOUS)
                continue
            instance = of_type[0]
            toolsets.append({**base, "instanceId": instance["_id"], "instanceName": _text(instance.get("instanceName"))})
            plan.bound.append({"kind": "toolset", "name": ts["name"]})
            continue
        instance, reason = _one(
            i for i in instances
            if _text(i.get("toolsetType")).lower() == ts["name"].lower() and _same(i.get("instanceName"), ts["instanceName"])
        )
        if instance is None:
            plan.report("toolset", ts["instanceName"], reason or "not_found",
                        _AMBIGUOUS if reason == "ambiguous" else f"No action here is called '{ts['instanceName']}'.")
            continue
        toolsets.append({**base, "instanceId": instance["_id"], "instanceName": _text(instance.get("instanceName"))})
        plan.bound.append({"kind": "toolset", "name": ts["instanceName"]})
    plan.body["toolsets"] = toolsets


def _plan_mcp_servers(plan: ImportPlan, agent: Mapping[str, Any], instances: list[dict[str, Any]]) -> None:
    servers: list[dict[str, Any]] = []
    for server in agent["mcpServers"]:
        instance, reason = _one(
            i for i in instances
            if _same(i.get("name"), server["name"])
            and (server["typeId"] is None or not i.get("typeId") or i.get("typeId") == server["typeId"])
        )
        if instance is None:
            plan.report("mcpServer", server["name"], reason or "not_found",
                        _AMBIGUOUS if reason == "ambiguous" else f"No MCP server here is called '{server['name']}'.")
            continue
        servers.append({
            "instanceId": instance["_id"], "name": server["name"], "displayName": server["displayName"],
            "typeId": server["typeId"], "allTools": server["allTools"], "tools": server["tools"],
        })
        plan.bound.append({"kind": "mcpServer", "name": server["name"]})
    plan.body["mcpServers"] = servers


def _plan_knowledge(plan: ImportPlan, agent: Mapping[str, Any], connectors: list[dict[str, Any]]) -> None:
    knowledge: list[dict[str, Any]] = []
    for source in agent["knowledge"]:
        if source["scope"] == "limited":
            count = source["limitedItems"]
            plan.report("knowledge", source["name"], "limited",
                        f"It searched {count} chosen folders or files; choose them again." if count
                        else "It searched only some of its folders or files; choose them again.")
            continue
        connector, reason = _one(
            c for c in connectors
            if _same(c.get("name"), source["name"]) and _text(c.get("type")).upper() == source["type"].upper()
        )
        connector_id = _text((connector or {}).get("_key")) or _text((connector or {}).get("id"))
        if connector is None or not connector_id:
            plan.report("knowledge", source["name"], reason or "not_found",
                        _AMBIGUOUS if reason == "ambiguous" else f"No source here is called '{source['name']}'.")
            continue
        knowledge.append({"connectorId": connector_id, "filters": {}})
        plan.bound.append({"kind": "knowledge", "name": source["name"]})
    plan.body["knowledge"] = knowledge


def _known_tool(wanted: Mapping[str, Any], owner: str, known: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The real tool a drafted one names, by its name or full name, case ignored."""
    asked = {_text(wanted.get("name")).casefold(), _text(wanted.get("fullName")).casefold()} - {""}
    for tool in known:
        real = _text(tool.get("name"))
        names = {real.casefold(), _text(tool.get("fullName")).casefold(), f"{owner}.{real}".casefold()} - {""}
        if asked & names:
            return tool
    return None


def _checked_tools(
    plan: ImportPlan, owner: str, tools: list[dict[str, Any]], known: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for tool in tools:
        label = f"{owner}.{_text(tool.get('name'))}"
        if known is None:
            plan.report("tool", label, "unverified", "Its tools could not be listed here, so it was left out.")
            continue
        real = _known_tool(tool, owner, known)
        if real is None:
            plan.report("tool", label, "not_found", f"'{owner}' has no tool called '{_text(tool.get('name'))}'.")
            continue
        if not any(k["name"] == real["name"] for k in kept):
            kept.append({
                "name": _text(real.get("name")),
                "fullName": _text(real.get("fullName")) or f"{owner}.{_text(real.get('name'))}",
                "description": real.get("description") if isinstance(real.get("description"), str) else "",
            })
    return kept


def check_drafted_tools(
    plan: ImportPlan,
    *,
    toolset_tools: Mapping[str, Sequence[Mapping[str, Any]] | None],
    mcp_tools: Mapping[str, Sequence[Mapping[str, Any]] | None],
) -> None:
    """Holds a chat-drafted agent's tools (ADR-AGB-01, UI-ADOPT2) to the real tool lists: ``toolset_tools`` by
    toolset type, ``mcp_tools`` by MCP instance id, each ``None`` when the list could not be read. A tool the list
    has binds under its real name; one it lacks is reported ``not_found``, and every tool of an unreadable list
    ``unverified``: a model wrote the names, so none is created unchecked. An action or server left with none of the
    tools it named is not attached (``no_tools``). An action that names no tool, or a server with all its tools,
    has nothing to check."""
    toolsets: list[dict[str, Any]] = []
    for ts in plan.body.get("toolsets") or []:
        if not ts.get("tools"):
            toolsets.append(ts)
            continue
        owner = _text(ts.get("name"))
        kept = _checked_tools(plan, owner, ts["tools"], toolset_tools.get(owner.lower()))
        if not kept:
            bound_as = _text(ts.get("instanceName")) or owner
            plan.bound[:] = [b for b in plan.bound if b != {"kind": "toolset", "name": bound_as} and b != {"kind": "toolset", "name": owner}]
            plan.report("toolset", owner, "no_tools", "None of the tools it named is offered here, so it was left out.")
            continue
        toolsets.append({**ts, "tools": kept})
    plan.body["toolsets"] = toolsets
    servers: list[dict[str, Any]] = []
    for server in plan.body.get("mcpServers") or []:
        if server.get("allTools") is True:
            servers.append(server)
            continue
        owner = _text(server.get("name"))
        kept = _checked_tools(plan, owner, list(server.get("tools") or []), mcp_tools.get(_text(server.get("instanceId"))))
        if not kept:
            plan.bound[:] = [b for b in plan.bound if b != {"kind": "mcpServer", "name": owner}]
            plan.report("mcpServer", owner, "no_tools", "None of the tools it named is offered here, so it was left out.")
            continue
        servers.append({**server, "tools": kept})
    plan.body["mcpServers"] = servers


def unlinked_skills(requested: list[str], linked: Iterable[str]) -> list[dict[str, str]]:
    """The skills the new agent asked for that no skill here carries, as re-binding entries."""
    have = {name.casefold() for name in linked}
    return [
        {"kind": "skill", "name": name, "reason": "not_found", "detail": f"No skill here is called '{name}'."}
        for name in requested if name.casefold() not in have
    ]
