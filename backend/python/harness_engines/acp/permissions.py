"""An ACP agent's ``session/request_permission``, decided by the harness's approval policy, never by the agent.

The harness's rules (main #4313): what the engine does inside its own sandbox
(reads, searches, edits under the workspace, commands) runs without asking when the run's mode permits that
native tool, exactly as the native drivers configure their engines not to ask. A PipesHub tool runs: the tool
gateway is where its approval is asked and held (E2), so the agent's own ask before it would only ask twice. The
engine's task list (its plan tool) and pure thinking change nothing. Everything else (web fetches, mode switches,
writes outside the workspace, tools the mode does not permit, anything the driver cannot classify) is not the
agent's to allow: it becomes an ``ApprovalRequested`` that the controller refuses, as it refuses every engine's
own ask (the flow guard is unchanged).

An allowance is always ``allow_once``. ``allow_always`` would let the agent skip later asks this policy has not
seen, so it is never chosen; without an ``allow_once`` option the ask is refused.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.engines.native_policy import (
    NOT_PERMITTED,
    NativeCall,
    NativeCallPolicy,
)
from harness_core.engines.policy import NativeTool
from harness_engines.acp.profiles import WORKDIR

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.interactions.ports import InteractionDecision

Verdict = Literal["allow", "ask"]

# ACP ``ToolKind`` → the native tool it is, for the kinds that are one.
_KINDS: Mapping[str, NativeTool] = {
    "read": NativeTool.READ,
    "search": NativeTool.SEARCH,
    "edit": NativeTool.EDIT,
    "delete": NativeTool.EDIT,
    "move": NativeTool.EDIT,
    "execute": NativeTool.SHELL,
    "fetch": NativeTool.WEB,
}
_WRITES = frozenset({"edit", "delete", "move"})


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """What ``harness_engines.acp.launch.session_policy`` rendered for the run."""

    agent: str
    allowed: frozenset[NativeTool]
    native_names: Mapping[str, NativeTool]
    plan_tool: str | None
    gateway_prefix: str
    mcp_server: str
    tools_url: str
    tools_placeholder: str
    client_fs_read: bool
    client_fs_write: bool
    call_id_name_separator: str | None = None
    usage_path: tuple[str, ...] = ()
    call_usage_path: tuple[str, ...] = ()
    tool_name_path: tuple[str, ...] = ()
    error_reasons: Mapping[str, str] = field(default_factory=dict[str, str])
    mcp_meta: Mapping[str, JsonValue] = field(default_factory=dict[str, "JsonValue"])
    command_result: str = "text"
    output_may_be_lost: bool = False

    @staticmethod
    def from_json(data: bytes) -> SessionPolicy:
        raw = as_object(json.loads(data))
        names: dict[str, NativeTool] = {}
        for tool, listed in as_object(raw.get("nativeNames")).items():
            for name in as_list(listed):
                names[as_str(name)] = NativeTool(tool)
        plan = raw.get("planTool")
        return SessionPolicy(
            agent=as_str(raw.get("agent")),
            allowed=frozenset(NativeTool(as_str(t)) for t in as_list(raw.get("allowed"))),
            native_names=names,
            plan_tool=plan if isinstance(plan, str) and plan else None,
            gateway_prefix=as_str(raw.get("gatewayPrefix")),
            mcp_server=as_str(raw.get("mcpServer")),
            tools_url=as_str(raw.get("toolsUrl")),
            tools_placeholder=as_str(raw.get("toolsPlaceholder")),
            client_fs_read=raw.get("clientFsRead") is True,
            client_fs_write=raw.get("clientFsWrite") is True,
            call_id_name_separator=as_str(raw.get("callIdNameSeparator")) or None,
            usage_path=tuple(as_str(k) for k in as_list(raw.get("usagePath"))),
            call_usage_path=tuple(as_str(k) for k in as_list(raw.get("callUsagePath"))),
            tool_name_path=tuple(as_str(k) for k in as_list(raw.get("toolNamePath"))),
            error_reasons={as_str(k): as_str(v) for k, v in as_object(raw.get("errorReasons")).items()},
            mcp_meta=dict(as_object(raw.get("mcpMeta"))),
            command_result=as_str(raw.get("commandResult")) or "text",
            output_may_be_lost=raw.get("outputMayBeLost") is True,
        )

    def is_gateway_tool(self, name: str) -> bool:
        return bool(self.gateway_prefix) and name.startswith(self.gateway_prefix) and len(name) > len(self.gateway_prefix)


def _at(message: Mapping[str, JsonValue], path: tuple[str, ...]) -> JsonValue:
    node: JsonValue = dict(message)
    for key in path:
        node = as_object(node).get(key)
    return node


def named_at(call: Mapping[str, JsonValue], path: tuple[str, ...]) -> str:
    """The string at ``path`` in ``call``, or ``""``."""
    return as_str(_at(call, path)) if path else ""


def named_object(message: Mapping[str, JsonValue], path: tuple[str, ...]) -> Mapping[str, JsonValue]:
    """The object at ``path`` in ``message``, or an empty one."""
    return as_object(_at(message, path)) if path else {}


def tool_name(call: Mapping[str, JsonValue], separator: str | None = None, path: tuple[str, ...] = ()) -> str:
    """The tool's own name: ACP's ``name`` when the agent sends it; the value at ``path`` (where the agent names it
    in the call); with ``separator``, the call id's part before it (an agent whose title is only a display text);
    else its ``title``."""
    name = as_str(call.get("name")) or named_at(call, path)
    if name:
        return name
    call_id = as_str(call.get("toolCallId"))
    if separator is not None and separator in call_id:
        return call_id.split(separator, 1)[0]
    return as_str(call.get("title"))


def classify(policy: SessionPolicy, call: Mapping[str, JsonValue]) -> tuple[Verdict, str]:
    """The verdict on one ask, and the permission it is (for the ``ApprovalRequested`` when it is not allowed).

    A native call is judged by the run's native-call policy, the one the hook-running engines ask through the
    Tool Gateway (``harness_core.engines.native_policy``)."""
    name = tool_name(call, policy.call_id_name_separator, policy.tool_name_path)
    kind = as_str(call.get("kind")) or "other"
    if policy.is_gateway_tool(name):
        return "allow", "gateway_tool"
    if policy.plan_tool is not None and name == policy.plan_tool:
        return "allow", "plan"
    if kind == "think":
        return "allow", "think"
    tool = _KINDS.get(kind) or policy.native_names.get(name)
    if tool is None:
        return "ask", kind
    writes = kind in _WRITES or tool is NativeTool.EDIT
    paths = tuple(as_str(as_object(loc).get("path")) for loc in as_list(call.get("locations"))) if writes else ()
    verdict = NativeCallPolicy(policy.allowed, WORKDIR).verdict(NativeCall(tool, paths, name))
    if verdict.allowed:
        return "allow", tool.value
    return "ask", tool.value if verdict.code == NOT_PERMITTED else verdict.code


# A native tool's ACP kind, for an agent that reports every call as ``other`` (Goose): the mapper then shows a shell
# call as a command and an edit's locations as file changes, as for an agent that sends the kind.
_NATIVE_KINDS: Mapping[NativeTool, str] = {
    NativeTool.READ: "read",
    NativeTool.SEARCH: "search",
    NativeTool.EDIT: "edit",
    NativeTool.SHELL: "execute",
}


def native_kind(policy: SessionPolicy, name: str, kind: str) -> str:
    """``kind``, or the ACP kind of the native tool ``name`` is when the agent sent only ``other``."""
    if kind not in ("", "other"):
        return kind
    tool = policy.native_names.get(name)
    return _NATIVE_KINDS.get(tool, kind or "other") if tool is not None else kind or "other"


def option_for(options: JsonValue, decision: InteractionDecision | Literal["allow"]) -> JsonValue:
    """The ``RequestPermissionResponse``: ``allow_once`` for an allowance, else a rejection (``reject_once`` first),
    else ``cancelled``. Never ``allow_always``."""
    approved = decision in ("allow", "approved")
    wanted = ("allow_once",) if approved else ("reject_once", "reject_always")
    for kind in wanted:
        for option in as_list(options):
            entry = as_object(option)
            option_id = entry.get("optionId")
            if as_str(entry.get("kind")) == kind and isinstance(option_id, str) and option_id:
                return {"outcome": {"outcome": "selected", "optionId": option_id}}
    return {"outcome": {"outcome": "cancelled"}}


CANCELLED: JsonValue = {"outcome": {"outcome": "cancelled"}}

__all__ = [
    "CANCELLED",
    "SessionPolicy",
    "Verdict",
    "classify",
    "named_at",
    "named_object",
    "native_kind",
    "option_for",
    "tool_name",
]
