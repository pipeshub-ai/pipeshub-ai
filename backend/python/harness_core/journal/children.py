"""Child runs (P6.1, P6.3): what a parent asks for, and the rules Node decides it by.

Node is the authority at admission. The same ⊆ rule lives here for the
in-process reference journal (``journal.memory``), which plays Node in kernel
tests; both run the one table ``contracts/harness/vectors/child-grants.json``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import JsonValue

from harness_core.access.types import AccessRouteRef, spec_route_id
from harness_core.contracts import canonical_digest, canonical_json
from harness_core.journal.types import JournalError
from harness_core.workspace.forks import principal_workspace_id

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from harness_core.contracts.generated.enums import CompletedOutcome
    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.journal.child_model import ChildSpecModel

CHILD_MAX_DEPTH = 3
# A profile read only to narrow and re-pin it: its access route is the child's spec's own.
_NO_ACCESS = AccessRouteRef("child", "pin")

SandboxProfile = Literal["agent-code", "none"]


class ChildRefusal(StrEnum):
    """Why Node refused a child; each is one cause (C-6)."""

    NOT_GRANTED = "children_not_granted"
    LIMIT_REACHED = "child_limit_reached"
    GRANT_TOO_LARGE = "child_grant_too_large"
    DEPTH_EXCEEDED = "child_depth_exceeded"
    BUDGET_EXCEEDS_REMAINING = "child_budget_exceeds_remaining"
    # The child's model access or engine was refused by the host's policy; ``ChildRefused.code`` names the host's cause.
    ACCESS_REFUSED = "child_access_refused"


class ChildRefused(JournalError):
    """Node refused the child; nothing was written. The parent's call fails, the run goes on."""

    def __init__(self, reason: ChildRefusal, detail: str = "", *, code: str | None = None) -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason
        self.detail = detail
        # The host's own refusal code, when ``reason`` is its group (``ACCESS_REFUSED``).
        self.code = code


class JoinReady(JournalError):
    """A ``join`` suspension refused: every child of the run already ended.
    Nothing was written; the run folds the results and goes on."""


@dataclass(frozen=True)
class ChildGrantRequest:
    """A child's grant; ``None`` inherits the parent's value, a value must fit inside it."""

    tools: tuple[str, ...] | None = None
    sandbox_profile: SandboxProfile | None = None
    egress: str | None = None
    mounts: tuple[str, ...] | None = None
    knowledge_apps: tuple[str, ...] | None = None
    knowledge_kb: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    access: tuple[str, ...] | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    max_children: int | None = None

    def wire(self) -> dict[str, JsonValue]:
        """Node's request body ``grants``: only the dimensions named."""
        out: dict[str, JsonValue] = {}
        lists: dict[str, tuple[str, ...] | None] = {
            "tools": self.tools,
            "mounts": self.mounts,
            "skills": self.skills,
            "access": self.access,
        }
        for key, value in lists.items():
            if value is not None:
                out[key] = list(value)
        knowledge: dict[str, JsonValue] = {}
        if self.knowledge_apps is not None:
            knowledge["apps"] = list(self.knowledge_apps)
        if self.knowledge_kb is not None:
            knowledge["kb"] = list(self.knowledge_kb)
        if knowledge:
            out["knowledge"] = knowledge
        scalars: dict[str, JsonValue] = {
            "sandboxProfile": self.sandbox_profile,
            "egress": self.egress,
            "maxTurns": self.max_turns,
            "maxTokens": self.max_tokens,
            "maxChildren": self.max_children,
        }
        out.update({k: v for k, v in scalars.items() if v is not None})
        return out

    @classmethod
    def from_wire(cls, wire: dict[str, JsonValue]) -> ChildGrantRequest:
        def names(key: str, source: dict[str, JsonValue] = wire) -> tuple[str, ...] | None:
            value = source.get(key)
            return tuple(str(v) for v in value) if isinstance(value, list) else None

        knowledge = wire.get("knowledge")
        k = knowledge if isinstance(knowledge, dict) else {}
        sandbox = wire.get("sandboxProfile")
        egress = wire.get("egress")
        turns = wire.get("maxTurns")
        tokens = wire.get("maxTokens")
        children = wire.get("maxChildren")
        return cls(
            tools=names("tools"),
            sandbox_profile=sandbox if sandbox in ("agent-code", "none") else None,  # pyright: ignore[reportArgumentType]
            egress=egress if isinstance(egress, str) else None,
            mounts=names("mounts"),
            knowledge_apps=names("apps", k),
            knowledge_kb=names("kb", k),
            skills=names("skills"),
            access=names("access"),
            max_turns=turns if isinstance(turns, int) else None,
            max_tokens=tokens if isinstance(tokens, int) else None,
            max_children=children if isinstance(children, int) else None,
        )


@dataclass(frozen=True)
class ChildExecutorRequest:
    """A child that runs on an engine (E1); ``profile`` narrows its parent's engine authority
    (an ``EngineProfileNarrowing``), ``None`` takes it whole."""

    kind: Literal["engine"] = "engine"
    profile: dict[str, JsonValue] | None = None

    def wire(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {"kind": self.kind}
        if self.profile is not None:
            out["profile"] = self.profile
        return out


@dataclass(frozen=True)
class ChildRequest:
    """One delegation from one tool invocation: the child's task and grant."""

    invocation_id: str
    goal: str
    grants: ChildGrantRequest = field(default_factory=ChildGrantRequest)
    task_id: str | None = None
    # The host's definition of what the child runs as (opaque to the core); the
    # admission authority pins it in the child's spec (PipesHub: ``x.pipeshub.subAgent``).
    definition: dict[str, JsonValue] | None = None
    # Absent: the child runs on the loop.
    executor: ChildExecutorRequest | None = None
    # The parent's context label when it asked (opaque to the core; PipesHub: a P2.5a turn label): the admission
    # authority pins it so the child's ledger starts from what its parent had read (UX2). Context, not identity:
    # a replay of the invocation is the same request whatever label it carries.
    inherited_label: dict[str, JsonValue] | None = None
    # The conversation grants the parent held when it asked (``{toolId, key}``, FLOWASK): pinned so the child, on a
    # stream that holds none of them, does not ask again where a person already allowed it. Context, like the label.
    inherited_grants: list[JsonValue] | None = None
    # The child's task in its parts, for the person's view of it (PipesHub: a ``JobTask``, BGUI-10); the child is
    # given ``goal``. Context, like the label: a replay of the invocation is the same request whatever it carries.
    task: JsonValue | None = None
    # The parent's first-use pins of the MCP servers it has listed (``McpPins``, ADR-OWD-07 D-LAZY-5): the child is
    # pinned to them, narrowed to what it holds, so it sees the tools its parent saw and does not list them again.
    # Context, like the label.
    mcp_pins: McpPins | None = None


EXECUTOR_DIMENSION = "executor"


def child_executor(
    parent: RunSpec, request: ChildExecutorRequest | None, authority: JsonValue | None = None
) -> dict[str, JsonValue] | GrantDecision:
    """The executor a child is pinned with (the ⊆ rule's ``executor`` dimension, E1).

    Absent: the loop. An engine narrows the parent's engine authority: an engine
    parent's own pinned profile, else ``authority`` (the profile the deployment
    resolved for the parent's org); with neither, or a narrowing
    that widens it, the child is refused ``CHILD_GRANT_TOO_LARGE`` naming ``executor``.
    """
    if request is None:
        return {"kind": "loop"}
    from harness_core.engines.errors import EngineRefusal
    from harness_core.engines.profiles import (
        constraint_from_wire,
        profile_digest,
        profile_from_wire,
        profile_json,
    )

    engine = getattr(parent.executor, "engine", None)
    base: object = getattr(engine, "profile", None) if engine is not None else authority
    if base is None:
        return _executor_refusal(["engine"])
    try:
        profile = profile_from_wire(base, access=_NO_ACCESS)
        if request.profile is not None:
            profile = profile.narrow(constraint_from_wire(request.profile))
    except EngineRefusal as refused:
        return _executor_refusal([str(getattr(refused, "field", refused.reason_code))])
    return {
        "kind": "engine",
        "engine": {
            "kind": profile.engine.kind,
            "version": profile.engine.version,
            "binaryDigest": profile.engine.binary_digest,
            "profileDigest": profile_digest(profile),
            "profile": profile_json(profile),
        },
    }


def _executor_refusal(items: list[str]) -> GrantDecision:
    return GrantDecision(
        None,
        {"code": "CHILD_GRANT_TOO_LARGE", "dimension": EXECUTOR_DIMENSION, "items": cast("JsonValue", items)},
    )


@dataclass(frozen=True)
class ChildAdmitted:
    child_run_id: str
    # False: this invocation's child was admitted before (a replay).
    created: bool


@dataclass(frozen=True)
class GrantDecision:
    grants: dict[str, JsonValue] | None
    refusal: dict[str, JsonValue] | None


def _pipeshub(spec: RunSpec) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", (spec.extensions or {}).get("x.pipeshub") or {})


WORKSPACE_MOUNT = "/workspace"


@dataclass(frozen=True)
class ChildWorkspace:
    """A child's workspace fork as its admission pinned it (P6.4)."""

    workspace_id: str
    fork_of: str
    revision: int

    def wire(self) -> dict[str, JsonValue]:
        return {
            "workspaceId": self.workspace_id,
            "forkOf": {"workspaceId": self.fork_of, "revision": self.revision},
        }


def child_workspace(spec: RunSpec) -> ChildWorkspace | None:
    """The fork a child's spec pins (PipesHub: ``x.pipeshub.workspace``); ``None`` for any other run."""
    pinned = _pipeshub(spec).get("workspace")
    if not isinstance(pinned, dict):
        return None
    origin = pinned.get("forkOf")
    workspace_id = pinned.get("workspaceId")
    if not isinstance(origin, dict) or not isinstance(workspace_id, str):
        return None
    fork_of, revision = origin.get("workspaceId"), origin.get("revision")
    if not isinstance(fork_of, str) or not isinstance(revision, int):
        return None
    return ChildWorkspace(workspace_id, fork_of, revision)


def run_workspace(spec: RunSpec, session_id: str) -> str | None:
    """The workspace a run writes: a child's pinned fork, else its principal's own in the session when
    ``/workspace`` is mounted (OWD-16), else none (a run that mounts no workspace forks nothing)."""
    pinned = child_workspace(spec)
    if pinned is not None:
        return pinned.workspace_id
    if WORKSPACE_MOUNT in _str_list(_pipeshub(spec).get("mounts")):
        audience = _pipeshub(spec).get("audience")
        collaborator = isinstance(audience, dict) and audience.get("sessionRole") == "collaborator"
        actor = spec.principal_ref.removeprefix("p_")
        return principal_workspace_id(session_id, actor, collaborator=collaborator)
    return None


def forks_for(grants: dict[str, JsonValue], parent: RunSpec) -> bool:
    """Whether admission forks a workspace for a child with these checked grants."""
    return bool(_pipeshub(parent)) and WORKSPACE_MOUNT in _str_list(grants.get("mounts"))


def _str_list(value: JsonValue) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _subset(
    held: Sequence[str], requested: Sequence[str] | None, also_held: Callable[[str], bool] = lambda _: False
) -> tuple[list[str], list[str]]:
    if requested is None:
        return sorted(set(held)), []
    own = set(held)
    asked = sorted(set(requested))
    return (
        [i for i in asked if i in own or also_held(i)],
        [i for i in asked if i not in own and not also_held(i)],
    )


_MCP_TOOL_ID = re.compile(r"^mcp@([^/]+)/(.+)$")
_WHOLE_TOOL = "*"


def whole_mcp_tool_id(instance_id: str) -> str:
    """ADR-OWD-07 D-LAZY-8 (R31-HOT): a child's grant of every tool of an MCP server its parent holds whole
    (``tools: at_first_use``), whose tools the parent's tool grants name none of; a named tool of it is
    ``mcp@<instance>/<tool>``. Node's ``wholeMcpToolId``."""
    return f"mcp@{instance_id}/{_WHOLE_TOOL}"


def _whole_mcp(ext: dict[str, Any]) -> dict[str, dict[str, Any]]:
    servers = ext.get("mcp")
    return {
        str(s["instanceId"]): s
        for s in (servers if isinstance(servers, list) else [])
        if isinstance(s, dict) and s.get("tools") == "at_first_use"
    }


# ADR-OWD-07 D-LAZY-5 (R31HOT2): the first-use pins a parent's worker carries into a spawn, per instance: each tool its
# first listing served, by the server's own name -> its descriptor digest. Node's ``McpFirstUsePins``.
McpPins = Mapping[str, Mapping[str, str]]


def _parent_pin(server: dict[str, Any], carried: McpPins | None) -> Mapping[str, str] | None:
    """The parent's pin of one server: its own spec's (inherited), else the one its worker pinned; Node's
    ``parentPinOf``."""
    own = server.get("pins")
    if isinstance(own, dict):
        return cast("dict[str, str]", own)
    return (carried or {}).get(str(server["instanceId"]))


def _narrowed_pin(pin: Mapping[str, str] | None, names: Sequence[str] | None) -> dict[str, str] | None:
    if pin is None:
        return None
    kept = {tool: pin[tool] for tool in sorted(pin) if names is None or tool in names}
    return kept or None


def _with_pin(entry: dict[str, Any], pin: dict[str, str] | None) -> dict[str, Any]:
    out = {k: v for k, v in entry.items() if k != "pins"}
    if pin is not None:
        out["pins"] = pin
    return out


def _child_mcp(
    servers: list[dict[str, Any]], granted: set[str], carried: McpPins | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The child's MCP pins and the tool grants they add, as Node's ``childMcp``: a server the child holds whole stays
    whole; named tools of a server the parent holds whole are pinned to exactly those; a server pinned by its tools
    follows the tools the child keeps. A server the parent has listed carries its first-use pin, narrowed to what the
    child holds (D-LAZY-5)."""
    named: dict[str, list[str]] = {}
    for tool_id in granted:
        match = _MCP_TOOL_ID.match(tool_id)
        if match is not None and match.group(2) != _WHOLE_TOOL:
            named.setdefault(match.group(1), []).append(match.group(2))
    mcp: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []
    for server in servers:
        instance = str(server["instanceId"])
        names = sorted(named.get(instance, []))
        pin = _parent_pin(server, carried)
        if server.get("tools") != "at_first_use":
            if names:
                mcp.append(_with_pin(server, _narrowed_pin(pin, names)))
            continue
        if whole_mcp_tool_id(instance) in granted:
            mcp.append(_with_pin(server, _narrowed_pin(pin, None)))
            continue
        if not names:
            continue
        mcp.append(
            _with_pin(
                {
                    "instanceId": instance,
                    "manifestDigest": canonical_digest(
                        {"instanceId": instance, "wholeOf": server["manifestDigest"], "tools": names}
                    ),
                },
                _narrowed_pin(pin, names),
            )
        )
        for name in names:
            tool_id = f"mcp@{instance}/{name}"
            tools.append(
                {"toolId": tool_id, "descriptorDigest": canonical_digest({"toolId": tool_id, "wholeOf": server["manifestDigest"]})}
            )
    return mcp, tools


def _none_or_same(held: str, requested: str | None) -> list[str]:
    return [] if requested in (None, "none", held) else [str(requested)]


def _at_most(held: int, requested: int | None) -> list[str]:
    return [] if requested is None or requested <= held else [str(requested)]


_Check = tuple[dict[str, JsonValue], list[str], str | None]


def _dimensions(
    parent: RunSpec, request: ChildGrantRequest, depth: int, pins: McpPins | None = None
) -> list[tuple[str, Callable[[], _Check]]]:
    ext = _pipeshub(parent)
    knowledge = ext.get("knowledge")
    k = knowledge if isinstance(knowledge, dict) else {}
    skills = ext.get("skills")
    skill_names = [
        str(s.get("name")) for s in (skills if isinstance(skills, list) else []) if isinstance(s, dict)
    ]

    whole = _whole_mcp(ext)

    def tools() -> _Check:
        def of_whole(tool_id: str) -> bool:
            # A server held whole that the parent has listed holds only what that listing served (D-LAZY-5).
            match = _MCP_TOOL_ID.match(tool_id)
            server = whole.get(match.group(1)) if match is not None else None
            if match is None or server is None:
                return False
            pin = _parent_pin(server, pins)
            return pin is None or match.group(2) in pin

        held = [t.tool_id for t in parent.grants.tools] + [whole_mcp_tool_id(i) for i in whole]
        granted, extra = _subset(held, request.tools, of_whole)
        return {"tools": list(granted)}, extra, None

    def sandbox() -> _Check:
        held = parent.grants.sandbox_profile
        extra = _none_or_same(held, request.sandbox_profile)
        return {"sandboxProfile": request.sandbox_profile or held}, extra, None

    def egress() -> _Check:
        held = parent.grants.egress
        extra = _none_or_same(held, request.egress)
        return {"egress": request.egress or held}, extra, None

    def mounts() -> _Check:
        granted, extra = _subset(_str_list(ext.get("mounts")), request.mounts)
        return {"mounts": list(granted)}, extra, None

    def knowledge_check() -> _Check:
        apps, apps_extra = _subset(_str_list(k.get("apps")), request.knowledge_apps)
        kb, kb_extra = _subset(_str_list(k.get("kb")), request.knowledge_kb)
        extra = [f"app:{i}" for i in apps_extra] + [f"kb:{i}" for i in kb_extra]
        return {"knowledge": {"apps": list(apps), "kb": list(kb)}}, extra, None

    def skills_check() -> _Check:
        granted, extra = _subset(skill_names, request.skills)
        return {"skills": list(granted)}, extra, None

    def access() -> _Check:
        granted, extra = _subset([spec_route_id(route) for route in parent.model.policy], request.access)
        return {"access": list(granted)}, extra, None

    def max_turns() -> _Check:
        held = parent.budgets.max_turns
        extra = _at_most(held, request.max_turns)
        return {"maxTurns": request.max_turns or held}, extra, None

    def max_tokens() -> _Check:
        held = parent.budgets.max_tokens
        extra = _at_most(held, request.max_tokens)
        return {"maxTokens": request.max_tokens or held}, extra, None

    def max_children() -> _Check:
        wanted = request.max_children or 0
        if wanted > 0 and depth >= CHILD_MAX_DEPTH:
            return {}, [str(wanted)], "CHILD_DEPTH_EXCEEDED"
        extra = _at_most(parent.budgets.max_children, wanted)
        return {"maxChildren": wanted}, extra, None

    return [
        ("tools", tools),
        ("sandboxProfile", sandbox),
        ("egress", egress),
        ("mounts", mounts),
        ("knowledge", knowledge_check),
        ("skills", skills_check),
        ("access", access),
        ("maxTurns", max_turns),
        ("maxTokens", max_tokens),
        ("maxChildren", max_children),
    ]


def child_grants(
    parent: RunSpec, request: ChildGrantRequest, depth: int, mcp_pins: McpPins | None = None
) -> GrantDecision:
    """grant(child) ⊆ grant(parent): the first widening dimension refuses, in registry order. ``mcp_pins``: the
    parent's first-use pins its worker carried (R31HOT2)."""
    grants: dict[str, JsonValue] = {}
    for dimension, check in _dimensions(parent, request, depth, mcp_pins):
        granted, extra, code = check()
        if extra:
            return GrantDecision(
                None,
                {
                    "code": code or "CHILD_GRANT_TOO_LARGE",
                    "dimension": dimension,
                    "items": list(extra),
                },
            )
        grants.update(granted)
    return GrantDecision(grants, None)


def child_spec(
    parent: RunSpec,
    grants: dict[str, JsonValue],
    definition: dict[str, JsonValue] | None = None,
    workspace: ChildWorkspace | None = None,
    executor: dict[str, JsonValue] | None = None,
    inherited_label: dict[str, JsonValue] | None = None,
    model: ChildSpecModel | None = None,
    inherited_grants: list[JsonValue] | None = None,
    mcp_pins: McpPins | None = None,
) -> dict[str, JsonValue]:
    """The child's spec as Node pins it: the parent's narrowed to ``grants``, as a
    child on ``executor`` (the loop by default); ``definition`` is pinned as the
    product's ``subAgent`` and ``workspace`` as its ``workspace`` (never the parent's own).
    ``model``: the model its role sets (MA1), its one route; absent, the parent's routes it was granted."""
    spec: dict[str, Any] = parent.model_dump(mode="json", by_alias=True, exclude_none=True)
    access = set(_str_list(grants["access"]))
    kept = [
        (route, raw)
        for route, raw in zip(parent.model.policy, spec["model"]["policy"], strict=True)
        if spec_route_id(route) in access
    ]
    spec["model"] = {**spec["model"], "policy": [raw for _, raw in kept]}
    # The configured model names the parent's first route; a child without it has none.
    first_kept = bool(kept) and kept[0][0] is parent.model.policy[0]
    if model is not None:
        # One route and no fallback: a role-assigned child never calls another model.
        spec["model"]["policy"] = [{"provider": model.provider, "model": model.model}]
    tools = set(_str_list(grants["tools"]))
    held: list[dict[str, Any]] = spec["grants"]["tools"]
    extensions: dict[str, dict[str, Any]] = spec.get("extensions") or {}
    ext = extensions.get("x.pipeshub")
    servers, whole_tools = _child_mcp(ext.get("mcp", []) if ext is not None else [], tools, mcp_pins)
    kept = [t for t in held if t["toolId"] in tools]
    spec.update(
        runKind="child",
        schemaVersion=3,
        executor=executor or {"kind": "loop"},
        grants={
            "tools": sorted(kept + whole_tools, key=lambda t: str(t["toolId"])) if whole_tools else kept,
            "sandboxProfile": grants["sandboxProfile"],
            "egress": grants["egress"],
        },
        budgets={
            **spec["budgets"],
            "maxTurns": grants["maxTurns"],
            "maxTokens": grants["maxTokens"],
            "maxChildren": grants["maxChildren"],
        },
    )
    if ext is not None:
        knowledge = cast("dict[str, Any]", grants["knowledge"])
        skills = set(_str_list(grants["skills"]))
        held_skills: list[dict[str, Any]] = ext.get("skills", [])
        ext.update(
            knowledge={**ext["knowledge"], "apps": knowledge["apps"], "kb": knowledge["kb"]},
            mcp=servers,
            skills=[skill for skill in held_skills if skill["name"] in skills],
            mounts=_str_list(grants["mounts"]),
        )
        _ = ext.pop("subAgent", None)
        _ = ext.pop("workspace", None)
        _ = ext.pop("inheritedLabel", None)
        _ = ext.pop("inheritedGrants", None)
        if model is not None:
            ext["model"] = {"modelKey": model.model_key, "modelName": model.model}
        elif not first_kept:
            _ = ext.pop("model", None)
        if definition is not None:
            ext["subAgent"] = definition
        if workspace is not None:
            ext["workspace"] = workspace.wire()
        if inherited_label is not None:
            ext["inheritedLabel"] = inherited_label
        if inherited_grants:
            ext["inheritedGrants"] = inherited_grants
    return cast("dict[str, JsonValue]", spec)


@dataclass(frozen=True)
class BudgetShare:
    max_turns: int
    max_tokens: int
    max_children: int = 0


def child_budget_share(
    *,
    parent_max_turns: int,
    parent_max_tokens: int,
    definition_max_turns: int,
    batch: int,
) -> BudgetShare:
    """A child's explicit share of its parent's budget, until P6.5's ledger replaces it.

    Turns: the sub-agent definition's own limit, never above the parent's (a
    child's turns are its own, as legacy's are). Tokens: the parent's split over
    the batch's children plus one share the parent keeps for its own synthesis.
    The child spawns nothing (its pool holds no delegating tool), so the tree's
    depth stays 1."""
    turns = max(1, min(definition_max_turns, parent_max_turns))
    tokens = max(1, parent_max_tokens // (max(batch, 1) + 1))
    return BudgetShare(max_turns=turns, max_tokens=tokens)


def child_run_id_for(org_id: str, parent_run_id: str, invocation_id: str) -> str:
    """Node's derivation: the run index is then the idempotency key of the invocation."""
    digest = hashlib.sha256(
        canonical_json(["child-run", org_id, parent_run_id, invocation_id])
    ).hexdigest()
    return f"{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"


SUBAGENT_NEEDS_INPUT = "This sub-agent could not fully complete its task — it needs: "


def child_result_document(
    *,
    outcome: CompletedOutcome,
    output: str,
    confidence: str | None = None,
    truncated_by: str | None = None,
) -> dict[str, JsonValue]:
    """The sub-agent result shape a parent's model reads (``spawn_agent.child_result_content``). ``truncated_by``:
    a limit cut the child's work short and its output is what it had gathered (BUDGETEND); the parent's model
    reads ``stopped_early`` and says so in its answer."""
    doc: dict[str, JsonValue] = {"output": output, "success": True}
    if confidence is not None:
        doc["confidence"] = confidence
    if outcome == "needs_input":
        doc["needs_input"] = SUBAGENT_NEEDS_INPUT + output
    if truncated_by is not None:
        doc["truncatedBy"] = truncated_by
        doc["stopped_early"] = STOPPED_EARLY_NOTES.get(truncated_by, STOPPED_EARLY_NOTES["budget"])
    return doc


# What a parent's model reads when its child stopped early (BUDGETEND). SPENDCONT: told only "say so", a parent
# answered "the file isn't ready yet" and dropped the child's 8.3 KB partial inventory.
_PARTIAL_WORK = (
    "its output is partial but real work: give it to the person in full, labelled partial, say what it did not get "
    "to, and that 'continue' picks up from the saved work. Do not discard it or start the task again in this message."
)
STOPPED_EARLY_NOTES: dict[str, str] = {
    "budget": f"This sub-task stopped early at its spending limit; {_PARTIAL_WORK}",
    "turns": f"This sub-task stopped early at its step limit; {_PARTIAL_WORK}",
    "time": f"This sub-task stopped early when its working time ran out; {_PARTIAL_WORK}",
    # WP-62: a stall's note read "spending limit" (the fallback), which the run was nowhere near.
    "stall": f"This sub-task stopped early because its recent steps were not making progress; {_PARTIAL_WORK}",
    # WP-77: the model provider failed for longer than the run waits.
    "provider": f"This sub-task stopped early because the AI model provider was unavailable; {_PARTIAL_WORK}",
    # WP-84: the engine's own error after it had gathered; its wrap-up answered from that.
    "engine_error": f"This sub-task stopped early because its coding engine ran into an error; {_PARTIAL_WORK}",
    # WP-103: PipesHub blocked one of its model calls as carrying what looked like a secret.
    "secret_blocked": (
        "This sub-task stopped early because PipesHub blocked one of its requests that looked like it contained a "
        f"secret; {_PARTIAL_WORK}"
    ),
}


__all__ = [
    "CHILD_MAX_DEPTH",
    "STOPPED_EARLY_NOTES",
    "SUBAGENT_NEEDS_INPUT",
    "BudgetShare",
    "ChildAdmitted",
    "ChildGrantRequest",
    "ChildRefusal",
    "ChildRefused",
    "ChildRequest",
    "ChildWorkspace",
    "GrantDecision",
    "JoinReady",
    "WORKSPACE_MOUNT",
    "child_budget_share",
    "child_grants",
    "child_result_document",
    "child_run_id_for",
    "child_spec",
    "child_workspace",
    "forks_for",
    "run_workspace",
]
