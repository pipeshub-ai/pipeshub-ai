"""IPlatformBroker port — host side of the sandbox bridge.

The broker is the single security boundary between sandboxed workflow code
and the rest of the platform.  Every dispatch() call goes through:
  1. RunGrant enforcement (computed host-side from TaskDefinition and the
     pinned WorkflowVersion, never read from sandbox input)
  2. Max-calls, dry-run simulation, and taint checks
  3. Capability router → the matching ICapabilityHandler

The sandbox holds no credentials and presents no token: it reaches the host
only through this broker, and the host identifies the run by the process it
owns. A run-scoped bearer token would add a step both sides of which are the
same process, so there is none.

Replacing the old string `tool_path` discriminator with a typed `Capability`
enum means the scope enforcer can authorize each capability class without
parsing strings, and new capabilities (MCP, knowledge) are new handlers,
not new protocol.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, Field


class Capability(str, Enum):
    TOOL = "tool"
    AGENT_RUN = "agent.run"
    AGENT_CREATE = "agent.create"
    KNOWLEDGE_SEARCH = "knowledge.search"
    STATE_GET = "state.get"
    STATE_SET = "state.set"
    CONVERSATION_EMIT = "conversation.emit"


class RunGrant(BaseModel):
    """Authority granted to one workflow run — computed host-side from
    `TaskDefinition`, never read from the sandbox.

    Every set here is deny-by-default: empty means "granted nothing", never
    "granted everything". A run whose pins could not be computed therefore
    fails its first call with a message naming the fix, instead of executing
    against the whole registry.

    tool_names: frozenset of Tool.name values the run may call.
    agent_ids: frozenset of agent UUIDs the run may invoke via agent.run.
    collection_ids: frozenset of knowledge-base collection IDs. Unlike the
        others this one narrows rather than denies: empty leaves the handler's
        own org scoping in place, and non-empty is intersected with whatever
        the call requested (see `PlatformBroker._scope_call`).
    can_create_agents: True iff the run may call agent.create.
    max_calls: hard ceiling on total broker dispatches for this run (guards
        against runaway loops in generated code).
    """
    tool_names: frozenset[str] = Field(default_factory=frozenset)
    agent_ids: frozenset[str] = Field(default_factory=frozenset)
    collection_ids: frozenset[str] = Field(default_factory=frozenset)
    can_create_agents: bool = False
    max_calls: int = 200


class BrokerCall(BaseModel):
    """One capability invocation, capability-discriminated.

    `target` meaning is scoped by capability:
      TOOL            → Tool.name, e.g. "jira__create_issue"
      AGENT_RUN       → agent UUID
      AGENT_CREATE    → desired agent name (display, not persisted id)
      KNOWLEDGE_SEARCH → free-text query (collection_ids in arguments)
      STATE_GET/SET   → state key
      CONVERSATION_EMIT → emit kind ("text", "code", "error")
    """
    capability: Capability
    target: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    run_id: str
    step_key: str


class BrokerResult(BaseModel):
    success: bool
    data: Any = None
    error: str | None = None


class RunPrincipal(BaseModel):
    org_id: str
    user_id: str
    run_id: str
    workflow_id: str = ""
    """The task/workflow this run belongs to. Scopes `ctx.state`, which is
    durable ACROSS runs of the same workflow and so cannot be keyed by run."""
    is_dry_run: bool = False
    """Enforced at the broker, not in the SDK: only the host knows which tools
    are write-tagged, and a dry run must not mutate anything external even when
    the generated code calls `ctx.tool()` without declaring a side effect."""
    is_service_account: bool = False
    conversation_id: str | None = None
    """The chat conversation this workflow was created from, if any. Backs
    `ctx.emit()`: without it the emit handler has no destination and silently
    drops every message the workflow streams."""
    grant: RunGrant = Field(default_factory=RunGrant)


class ToolClassification(NamedTuple):
    """What a tool does, as far as the broker's dry-run and taint rules care.

    Lives on the port rather than in the broker implementation because
    `ICapabilityHandler.classify` returns it, so anything typed against the
    port needs it too.
    """

    is_write: bool
    is_external_read: bool
    is_destructive: bool


class ICapabilityHandler(Protocol):
    """One implementation per Capability, registered on the broker at startup."""

    capability: Capability

    capabilities: "tuple[Capability, ...]"
    """Optional. A handler serving several capabilities (state get/set) sets
    this instead of relying on `capability` alone, so the broker can register
    it the same way as every other handler rather than the handler having to
    reach back into the broker."""

    async def handle(self, call: BrokerCall, principal: RunPrincipal) -> BrokerResult:
        """Execute the capability call.  Must not raise — return a
        BrokerResult(success=False, error=...) instead."""
        ...

    def classify(self, tool_name: str) -> ToolClassification:
        """Optional. What `tool_name` does, for the dry-run write skip and the
        taint rules. Only the handler that owns the tool registry can answer
        this; the broker treats an unclassifiable tool as every category, so a
        handler that does not implement it costs safety, not correctness."""
        ...


class IPlatformBroker(Protocol):
    """Every dispatch() runs the FULL PRE_TOOL_USE → execute → POST_TOOL_USE
    chain, closing the code-mode permission-bypass hole."""

    def register(self, handler: ICapabilityHandler) -> None:
        """Register a capability handler under every capability it declares.
        Must be called before any dispatch."""
        ...

    def register_for(self, capability: Capability, handler: ICapabilityHandler) -> None:
        """Register `handler` under one explicit capability, for the cases
        `register` cannot infer."""
        ...

    async def dispatch(self, call: BrokerCall, principal: RunPrincipal) -> BrokerResult:
        """Verify, authorize, and route one capability call."""
        ...


# ---------------------------------------------------------------------------
# Tool-name normalization
#
# Three naming conventions exist in the codebase and they disagree:
#   Tool.path:     "/tools/jira/create_issue"       (connector routes)
#   Tool.name:     "jira__create_issue"             (ToolRegistry, LLM schemas)
#   graph fullName: "jira.create_issue"             (ArangoDB/Neo4j nodes)
#
# The SDK speaks Tool.name because that is what the LLM already sees in tool
# schemas — so generated code naturally uses that form.  `normalize_tool_name`
# converts any of the three forms to Tool.name at the broker boundary.
# An unresolvable name returns None so the broker can emit a loud error.
# ---------------------------------------------------------------------------

_PATH_RE = re.compile(r"^/(?:tools/)?([^/]+)/(.+)$")
_BARE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def normalize_tool_name(raw: str) -> str | None:
    """Convert any of the three naming conventions to Tool.name.

    Returns None if the input cannot be recognised as a tool reference.

    Examples:
      "/tools/jira/create_issue" → "jira__create_issue"
      "jira.create_issue"        → "jira__create_issue"
      "jira__create_issue"       → "jira__create_issue"
    """
    raw = raw.strip()
    if not raw:
        return None

    # Already Tool.name form: lowercase, double-underscore separator.
    if "__" in raw and "/" not in raw and "." not in raw:
        return raw

    # URL path form: /tools/jira/create_issue or /jira/create_issue
    m = _PATH_RE.match(raw)
    if m:
        app, name = m.group(1), m.group(2).replace("/", "__")
        return f"{app}__{name}"

    # Dot-separated fullName form: jira.create_issue
    if "." in raw and "/" not in raw:
        parts = raw.split(".", 1)
        return f"{parts[0]}__{parts[1].replace('.', '__')}"

    # Bare identifier: builtin tools registered without an app prefix
    # ("search", "web_search"). Returning None here would make every builtin
    # unreachable from generated code.
    if _BARE_NAME_RE.match(raw):
        return raw

    return None
