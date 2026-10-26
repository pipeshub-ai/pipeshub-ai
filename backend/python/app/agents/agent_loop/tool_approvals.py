"""Per-tool approvals for MCP and toolset tool calls: which calls run, which wait for a person,
which never run.

Each tool has a rule — Allow ("Pre-approved"), Ask ("Allow on approval") or Block ("Deny"). For an
MCP tool the rule is the person's own, set for their MCP servers, and it applies in every chat they
have, agent chats included. For a toolset tool (Jira, Slack, ...) it is the toolset instance's, set
by an admin for everyone in the organization, or by its owner for a personal instance, one a user
added for themselves. Without one, a tool starts from what it does to data
(`mcp/tool_kind.py`). An MCP tool: a read-only one at Allow, one that changes data at Ask, one that
deletes data at Block. A toolset tool: reads and changes at Allow, one that deletes data at Ask.

- **Block**: the call doesn't run, and the model is told why.
- **Ask**: the call is saved and the turn stops on it, with an approval card on the tool's row.
  The person's answer comes back as a request that resumes that same call (`Tool approval: …`
  with `toolApproval`, like the ask-user card's `User selections:`): the saved call runs, on the
  server, with the arguments it was asked with, and its result becomes the result of the call the
  model made. The model carries on from there in the same reply; it never re-creates the call.
  "Don't ask again" also saves the answer as the person's rule for an MCP tool or a personal
  toolset; an org toolset's rules change only in its rules editor.
- Nobody can answer a card outside the PipesHub web app (Slack, the API, the MCP gateway, a
  service account): there, Ask is Block. Only a pre-approved tool runs there.

Enforced at the tool executor's one checkpoint (PreToolUse). `MCP_TOOL_APPROVALS=false` turns it
off for MCP tools, `TOOLSET_TOOL_APPROVALS=false` for toolset tools. Approvals are bound to the
signed-in user and the conversation they were asked in, and are answered only from a run someone
is watching. Whether a run is watched rests on the `client-name` header, which Node sets to `api`
for OAuth and personal access tokens, so only a web sign-in can claim the web app's name.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import Field, ValidationError

from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.events.base import AgentEvent, EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.decisions import PendingApproval
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agents.actions.util.read_only_sql import read_only_statement
from app.agents.agent_loop import parked_turn
from app.agents.constants.mcp_server_constants import (
    get_mcp_tool_approval_claim_path,
    get_mcp_tool_approval_path,
    get_mcp_user_tool_rules_path,
)
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.agents.mcp.models import MCPCamelModel
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.agent import Agent
    from app.agent_loop_lib.context.manager import ContextManager
    from app.agent_loop_lib.core.scope import RunScope
    from app.agent_loop_lib.hooks.middleware.context import (
        AgentLifecycleContext,
        ToolCallContext,
    )
    from app.agent_loop_lib.tools.base import Tool
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
    from app.agents.mcp.tool_kind import ToolKind
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

ENABLED_ENV = "MCP_TOOL_APPROVALS"
TOOLSET_ENABLED_ENV = "TOOLSET_TOOL_APPROVALS"
APPROVAL_TTL_SECONDS = 900
# A saved call and its claim are kept longer than they can be answered, so a late or repeated
# answer still finds the call it was about and is told what happened to it.
_RECORD_TTL_SECONDS = 86400
# The web app's `client-name`: the one client that can show an approval card.
WEB_CLIENT_NAME = "pipeshub-ai"
# How the app's answer to a card starts; the rest of the text is only for reading.
TOOL_APPROVAL_RESUME_PREFIX = "Tool approval:"
# How much of an approved call's result the model is given.
_MAX_RESULT_CHARS = 20_000
# How much of it the call's row keeps.
_MAX_PREVIEW_CHARS = 2_000
# Arguments larger than this are shown on the card as a preview only.
_MAX_CARD_ARGUMENT_CHARS = 8_000
# The stopped turn is kept (`parked_turn`) until the card can no longer be answered.
TURN_TTL_SECONDS = APPROVAL_TTL_SECONDS + 600
# How the result of a call that stopped on a card starts (its row, and its result in the history).
WAITING_PREFIX = "Waiting for your approval to run"

Rule = Literal["allow", "ask", "block"]
Decision = Literal["allow", "deny"]
# Whose tool: an MCP server's, or a toolset's.
Source = Literal["mcp", "toolset"]

# `context.tool_state` keys.
TOOLS_BY_PATH = "_mcp_tools_by_path"
# The toolset tools the gate checks, by path (`ApprovalTarget`s, filled by `tool_loader`).
TOOLSET_TOOLS_BY_PATH = "_toolset_tools_by_path"
_RULES_CACHE = "_tool_approval_rules"
_PENDING_IN_RUN = "_tool_approval_pending_id"
_STOPPED_CALL = "_tool_approval_stopped_call"
# The answer of a turn that ended waiting for approval (read by `AnswerFinalizer`).
PENDING_MESSAGE = "_tool_approval_pending_message"
_APPROVED_CALL = "_tool_approval_running"
# This request answers a card and continues the turn it stopped (read by the stream bridge).
RESUMING = "_tool_approval_resuming"
_RESUME_HISTORY = "_tool_approval_resume_history"


class ToolRules(MCPCamelModel):
    tools: dict[str, Rule] = Field(default_factory=dict)


class PendingCall(MCPCamelModel):
    approval_id: str
    org_id: str
    user_id: str
    conversation_id: str
    # Calls saved before toolsets were checked are all MCP calls.
    source: Source = "mcp"
    # A personal toolset instance's rules are its owner's, so an answer may set them.
    personal: bool = False
    instance_id: str
    server_name: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    # The model's id for the call: the answer becomes that call's result, on that call's row.
    tool_call_id: str = ""
    created_at: float
    expires_at: float


class ToolApprovalAnswer(MCPCamelModel):
    """What the app sends when a person answers an approval card."""

    approval_id: str = Field(min_length=1, max_length=64)
    decision: Decision
    # "Don't ask again for this tool": the answer becomes the person's rule for it.
    remember: bool = False


class Outcome(MCPCamelModel):
    """What came of an answered card: the call's row, and the result the model is given. Kept with
    the claim, so answering the same card again shows the same thing and runs nothing."""

    answer: Literal["allowed", "declined", "expired"]
    status: Literal["completed", "failed", "blocked"]
    result: str
    preview: str = ""
    summary: Optional[str] = None
    ran: bool = False


def enabled(source: Source = "mcp") -> bool:
    return env_bool(TOOLSET_ENABLED_ENV if source == "toolset" else ENABLED_ENV, True)


def organization_toolset_tool_rules() -> bool:
    """Whether admins set toolset tools' rules for the organization (an edition extension point)."""
    # Imported late: edition_config imports the routes that import this module.
    from app import edition_config

    return bool(getattr(edition_config, "organization_toolset_tool_rules", False))


@dataclass(frozen=True)
class ApprovalTarget:
    """A tool the gate checks: where its rule is kept, what it does to data, and the tool itself."""

    source: Source
    instance_id: str
    # The rule's key: the server's own name for an MCP tool, the toolset's for a toolset tool.
    tool_name: str
    # The MCP server's or the toolset instance's name, as people know it.
    owner: str
    kind: ToolKind
    tool: Tool
    title: Optional[str] = None
    # A personal toolset instance: its rules are the person's own, as an MCP server's are.
    personal: bool = False
    # Whose starting rules apply where nobody set one, when not its source's own. An API action's
    # operation is any REST call an admin picked from a spec, so it starts like an MCP tool.
    defaults: Source | None = None

    @property
    def own_rules(self) -> bool:
        return self.source == "mcp" or self.personal


def _mcp_target(adapter: "MCPToolAdapter") -> ApprovalTarget:
    title = (adapter.tool_info.annotations or {}).get("title")
    return ApprovalTarget(
        source="mcp", instance_id=adapter.instance_id, tool_name=adapter.raw_name,
        owner=adapter.server.display_name, kind=adapter.kind, tool=adapter,
        title=title if isinstance(title, str) else None,
    )


def sql_keeps_its_kind(sql: str) -> bool:
    """Whether a statement is one plain read, by the same test the SQL tools use to run it read-only
    (`read_only_statement`). Anything else, such as INSERT, UPDATE, DELETE, DROP, more than one
    statement, a call to a function that acts on the server, or text that can't be read this way,
    needs a person's approval."""
    return isinstance(sql, str) and read_only_statement(sql)[0] is not None


# Docs batch requests that only add or restyle content. Anything else, a delete, a named-range
# replace (which drops every range after the first), accepting or rejecting a suggestion, or a
# request type added to the API later, counts as a call that can't be undone.
_DOCS_ADDING_REQUESTS = frozenset({
    "insertText", "insertInlineImage", "insertTable", "insertTableRow", "insertTableColumn",
    "insertPageBreak", "insertSectionBreak", "insertPerson", "addDocumentTab",
    "createParagraphBullets", "createNamedRange", "createHeader", "createFooter", "createFootnote",
    "updateTextStyle", "updateParagraphStyle", "updateTableColumnProperties", "updateTableCellStyle",
    "updateTableRowStyle", "updateDocumentStyle", "updateSectionStyle", "updateDocumentTabProperties",
    "pinTableHeaderRows", "mergeTableCells", "unmergeTableCells",
})


def _docs_request_keeps_its_kind(request: Any) -> bool:  # noqa: ANN401
    if not isinstance(request, dict) or len(request) != 1:
        return False
    (name, body), = request.items()
    if name == "replaceAllText":
        # The same change replace_text makes; an empty replacement or a regex match can wipe text.
        if not isinstance(body, dict):
            return False
        contains = body.get("containsText")
        replacement = body.get("replaceText")
        return (
            isinstance(replacement, str) and replacement != ""
            and isinstance(contains, dict) and contains.get("searchByRegex") is not True
        )
    return name in _DOCS_ADDING_REQUESTS


def docs_requests_keep_their_kind(requests: Any) -> bool:  # noqa: ANN401
    """Whether a Google Docs batch update only adds or restyles content. Any other request, or
    requests that aren't a list of request objects, count as a call that can't be undone."""
    return isinstance(requests, list) and bool(requests) and all(map(_docs_request_keeps_its_kind, requests))


# Tools whose one call can do more than the tool's kind says: the argument that decides, and the
# check that it doesn't. A call that fails the check counts as destructive.
_CALL_CHECKS: dict[str, tuple[str, Callable[[Any], bool]]] = {
    "/tools/mariadb/execute_query": ("query", sql_keeps_its_kind),
    "/tools/redshift/execute_query": ("query", sql_keeps_its_kind),
    "/tools/docs/batch_update_document": ("requests", docs_requests_keep_their_kind),
}


def call_kind(target: "ApprovalTarget", tool_path: str, arguments: Any) -> ToolKind:  # noqa: ANN401
    """What this one call does to data: the tool's kind, unless this call can't be undone."""
    check = _CALL_CHECKS.get(tool_path)
    if check is None or target.kind == "destructive":
        return target.kind
    field, keeps_its_kind = check
    value = arguments.get(field) if isinstance(arguments, dict) else None
    return target.kind if keeps_its_kind(value) else "destructive"


@dataclass(frozen=True)
class Verdict:
    rule: Rule
    # Why, for a Block: what the model (and so the person) is told.
    reason: str = ""


# An MCP tool comes from a third-party server; a toolset tool is one of PipesHub's own integrations,
# set up for the organization, so its writes run and only a deleting call waits for a person.
_STARTING_RULES: dict[Source, dict[str, Rule]] = {
    "mcp": {"read": "allow", "write": "ask", "destructive": "block"},
    "toolset": {"read": "allow", "write": "allow", "destructive": "ask"},
}


def starting_rule(*, kind: ToolKind, source: Source) -> Rule:
    """A tool's rule where nobody set one."""
    return _STARTING_RULES[source][kind]


def decide(
    *, own: Optional[Rule], kind: ToolKind, source: Source, watched: bool, approved_call: bool = False,
) -> Verdict:
    """The rule for one call. `approved_call`: this is the call a person just approved (it runs
    unless something now blocks it)."""
    effective = own or starting_rule(kind=kind, source=source)
    if effective == "block":
        return Verdict("block", "own" if own else "default")
    if effective == "allow" or approved_call:
        return Verdict("allow")
    if not watched:
        return Verdict("block", "unattended")
    return Verdict("ask")


def watched(context: "AgentContext") -> bool:
    """Whether someone can answer an approval card in this run."""
    return bool(
        context.client_name == WEB_CLIENT_NAME and context.chat_streaming
        and context.conversation_id and not context.is_service_account
    )


# ---------------------------------------------------------------------------
# Stores
# ---------------------------------------------------------------------------

async def _read(config_service: "ConfigurationService", key: str) -> Any:  # noqa: ANN401
    # A store that can't be read must not look like "no rules": that would drop every Block.
    return await config_service.get_config(key, default=None, use_cache=False, raise_on_error=True, keep_in_cache=False)


async def _write(config_service: "ConfigurationService", key: str, value: dict[str, Any], *, ttl_seconds: Optional[int] = None) -> bool:
    return bool(await config_service.set_config(key, value, ttl_seconds=ttl_seconds, keep_in_cache=False))


def _model(model: type, raw: Any) -> Any:  # noqa: ANN401
    try:
        return model.model_validate(raw) if isinstance(raw, dict) else model()
    except ValidationError:
        logger.warning(f"Ignoring a malformed {model.__name__} record")
        return model()


async def load_own_rules(config_service: "ConfigurationService", *, user_id: str, instance_id: str) -> ToolRules:
    return _model(ToolRules, await _read(config_service, get_mcp_user_tool_rules_path(user_id, instance_id)))


async def save_own_rules(config_service: "ConfigurationService", rules: ToolRules, *, user_id: str, instance_id: str) -> bool:
    return await _write(config_service, get_mcp_user_tool_rules_path(user_id, instance_id), rules.model_dump(by_alias=True))


async def load_instance_rules(config_service: "ConfigurationService", *, instance_id: str) -> ToolRules:
    return _model(ToolRules, await _read(config_service, get_toolset_tool_rules_path(instance_id)))


async def save_instance_rules(config_service: "ConfigurationService", rules: ToolRules, *, instance_id: str) -> bool:
    return await _write(config_service, get_toolset_tool_rules_path(instance_id), rules.model_dump(by_alias=True))


@dataclass
class Claim:
    """`claimed`: this answer is the one that settles the call. `used`: an earlier answer did
    (`outcome` is what came of it, once it is known). `expired`: too late to run it. `unknown`:
    no such call for this person in this conversation."""

    status: Literal["claimed", "used", "expired", "unknown"]
    pending: Optional[PendingCall] = None
    outcome: Optional[Outcome] = None


async def claim_pending(
    config_service: "ConfigurationService", approval_id: str, *, org_id: str, user_id: str, conversation_id: Optional[str],
) -> Claim:
    """Claims the saved call for this answer. Claiming makes it used: two answers to the same card
    settle it once. A store failure raises, and nothing is claimed."""
    raw = await _read(config_service, get_mcp_tool_approval_path(approval_id))
    try:
        pending = PendingCall.model_validate(raw) if isinstance(raw, dict) else None
    except ValidationError:
        pending = None
    if pending is None:
        return Claim("unknown")
    if (pending.org_id, pending.user_id, pending.conversation_id) != (org_id, user_id, conversation_id or ""):
        logger.warning(f"Tool approval {approval_id} was answered by another user or from another conversation; ignored")
        return Claim("unknown")
    claim_path = get_mcp_tool_approval_claim_path(approval_id)
    earlier = await _read(config_service, claim_path)
    if isinstance(earlier, dict):
        return Claim("used", pending, _outcome_of(earlier))
    if pending.expires_at <= time.time():
        return Claim("expired", pending)
    if not await config_service.create_config_if_absent(claim_path, {"claimedAt": time.time()}, ttl_seconds=_RECORD_TTL_SECONDS):
        return Claim("used", pending, _outcome_of(await _read(config_service, claim_path)))
    return Claim("claimed", pending)


def _outcome_of(raw: Any) -> Optional[Outcome]:  # noqa: ANN401
    try:
        return Outcome.model_validate(raw["outcome"]) if isinstance(raw, dict) and isinstance(raw.get("outcome"), dict) else None
    except ValidationError:
        return None


async def _keep_outcome(config_service: "ConfigurationService", approval_id: str, outcome: Outcome) -> None:
    try:
        await _write(
            config_service, get_mcp_tool_approval_claim_path(approval_id),
            {"claimedAt": time.time(), "outcome": outcome.model_dump(by_alias=True)}, ttl_seconds=_RECORD_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Couldn't keep the outcome of tool approval {approval_id}: {e}")


# ---------------------------------------------------------------------------
# The gate (PreToolUse)
# ---------------------------------------------------------------------------

def _target(context: "AgentContext", path: str) -> Optional[ApprovalTarget]:
    """The tool a call is for, when the gate checks that kind of tool."""
    adapter: Optional[MCPToolAdapter] = context.tool_state.get(TOOLS_BY_PATH, {}).get(path)
    if adapter is not None:
        return _mcp_target(adapter) if enabled("mcp") else None
    target: Optional[ApprovalTarget] = context.tool_state.get(TOOLSET_TOOLS_BY_PATH, {}).get(path)
    return target if target is not None and enabled("toolset") else None


def _identity(target: ApprovalTarget) -> tuple[str, str, str]:
    return target.source, target.instance_id, target.tool_name


@dataclass
class _InstanceRules:
    rules: ToolRules
    # The rules couldn't be read: every tool of the instance is refused, and nothing is saved.
    unavailable: bool = False


async def _rules_for(context: "AgentContext", source: Source, instance_id: str, *, personal: bool = False) -> _InstanceRules:
    """Read once per request and instance; an answer to a card updates it in place."""
    cache: dict[tuple[str, str], _InstanceRules] = context.tool_state.setdefault(_RULES_CACHE, {})
    key = (source, instance_id)
    if key not in cache:
        try:
            if source == "mcp":
                rules = await load_own_rules(context.config_service, user_id=context.user_id, instance_id=instance_id)
            elif instance_id and (personal or organization_toolset_tool_rules()):
                # A personal instance's rules are its owner's, in every edition.
                rules = await load_instance_rules(context.config_service, instance_id=instance_id)
            else:
                # Without organization rules, or for a toolset whose instance couldn't be told
                # apart, every tool starts from what it does to data.
                rules = ToolRules()
        except Exception as e:
            logger.warning(f"Couldn't read the tool rules of {source} instance {instance_id}: {e}")
            cache[key] = _InstanceRules(ToolRules(), unavailable=True)
        else:
            cache[key] = _InstanceRules(rules)
    return cache[key]


def _blocked_message(reason: str, target: ApprovalTarget) -> str:
    tool, owner = target.tool_name, target.owner
    # An MCP tool's and a personal toolset's rules are the person's own; an org toolset's are the
    # organization's, set by an admin.
    whose, who = ("the user's", "they") if target.own_rules else ("the organization's", "an admin")
    if reason == "unattended":
        can_pre_approve = target.source == "toolset" and not target.personal and organization_toolset_tool_rules()
        return (
            f"{tool} on {owner} needs someone to approve it in the PipesHub app, so it wasn't run here. "
            "Tell the user what you wanted to do"
            + (", and that an admin can pre-approve it." if can_pre_approve else ".")
        )
    if reason == "default":
        # Without organization rules nobody can change an org instance's rule (an API action's).
        can_change = target.own_rules or organization_toolset_tool_rules()
        return (
            f"{tool} on {owner} can delete data, so it's denied unless {whose} tool approval rules allow it. "
            + (f"It wasn't run. Tell the user {who} can change its rule to run it." if can_change
               else f"It wasn't run. Tell the user to do it in {owner} itself.")
        )
    return f"{tool} on {owner} is blocked in {whose} tool approval rules, so it wasn't run."


def _card_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(arguments, default=str, ensure_ascii=False)
    if len(text) <= _MAX_CARD_ARGUMENT_CHARS:
        return {"arguments": arguments}
    return {"arguments": None, "argumentsPreview": text[:_MAX_CARD_ARGUMENT_CHARS] + "…"}


def approval_gate(context: "AgentContext") -> Any:  # noqa: ANN401
    """PreToolUse middleware; registered last, so nothing after it can still deny a call it
    leaves pending."""

    async def _gate(ctx: "ToolCallContext", next_fn: Any) -> None:  # noqa: ANN401
        target = _target(context, ctx.tool_path)
        if target is None:
            await next_fn()
            return
        target = replace(target, kind=call_kind(target, ctx.tool_path, ctx.tool_input))
        rules = await _rules_for(context, target.source, target.instance_id, personal=target.personal)
        if rules.unavailable:
            ctx.deny(
                f"The approval rules for {target.tool_name} on {target.owner} couldn't be checked, "
                "so it wasn't run. Tell the user to try again in a moment."
            )
            await next_fn()
            return
        verdict = decide(
            own=rules.rules.tools.get(target.tool_name),
            kind=target.kind,
            source=target.defaults or target.source,
            watched=watched(context),
            approved_call=context.tool_state.get(_APPROVED_CALL) == _identity(target),
        )
        if verdict.rule == "block":
            ctx.deny(_blocked_message(verdict.reason, target))
        elif verdict.rule == "ask":
            await _ask(context, ctx, target)
        await next_fn()

    return _gate


async def _ask(context: "AgentContext", ctx: "ToolCallContext", target: ApprovalTarget) -> None:
    tool_name, server = target.tool_name, target.owner
    if context.tool_state.get(_PENDING_IN_RUN):
        # The model reads this when the answer to the other card resumes the turn: it must ask again
        # then, or the action is silently dropped.
        ctx.deny(
            "Not run yet: only one action can wait for the user's approval at a time, and another one is waiting. "
            f"After the user answers it, call {tool_name} again with the same arguments to ask for this one."
        )
        return
    now = time.time()
    call = getattr(ctx.scope, "call", None)
    pending = PendingCall(
        approval_id=uuid.uuid4().hex, org_id=context.org_id, user_id=context.user_id,
        conversation_id=context.conversation_id or "", source=target.source, personal=target.personal,
        instance_id=target.instance_id,
        server_name=server, tool_name=tool_name, arguments=dict(ctx.tool_input), tool_call_id=getattr(call, "id", None) or "",
        created_at=now, expires_at=now + APPROVAL_TTL_SECONDS,
    )
    # Taken before the write is awaited: calls in one wave run concurrently.
    context.tool_state[_PENDING_IN_RUN] = pending.approval_id
    context.tool_state[_STOPPED_CALL] = pending.tool_call_id
    try:
        saved = await _write(
            context.config_service, get_mcp_tool_approval_path(pending.approval_id),
            pending.model_dump(by_alias=True), ttl_seconds=_RECORD_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Couldn't save a tool approval for {tool_name}: {e}")
        saved = False
    if not saved:
        context.tool_state.pop(_PENDING_IN_RUN, None)
        ctx.deny(f"{tool_name} on {server} needs approval, and the approval couldn't be saved, so it wasn't run.")
        return
    message = f"{WAITING_PREFIX} {tool_name} on {server}."
    context.tool_state[PENDING_MESSAGE] = message
    ctx.ask_later(PendingApproval(
        message=message,
        details={
            "approvalId": pending.approval_id, "instanceId": target.instance_id, "serverName": server,
            "toolName": tool_name, "toolTitle": target.title, "readOnly": target.kind == "read", "kind": target.kind,
            # "Don't ask again" sets the person's own rule; an org toolset's rules are the admin's.
            "canRemember": target.own_rules,
            "expiresAt": int(pending.expires_at * 1000), **_card_arguments(pending.arguments),
        },
    ))


# ---------------------------------------------------------------------------
# The answer: the stopped turn carries on from the call
# ---------------------------------------------------------------------------

def is_resume_query(text: Optional[str]) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(TOOL_APPROVAL_RESUME_PREFIX)


def begin_resume(context: "AgentContext", query: Optional[str]) -> bool:
    """Whether this request answers an approval card, and so continues the turn that stopped on
    it rather than starting a new one: the app's answer text, the answer itself, and someone
    watching. Anything else is an ordinary message."""
    if context.tool_approval is None or not is_resume_query(query) or not watched(context):
        return False
    context.tool_state[RESUMING] = True
    return True


def hold_history(context: "AgentContext", agent: "Agent") -> None:
    """The conversation the answer is bound into, once the factory has loaded it."""
    if context.tool_state.get(RESUMING):
        context.tool_state[_RESUME_HISTORY] = agent.context


def resume_parked_call(context: "AgentContext") -> Any:  # noqa: ANN401
    """PRE_AGENT middleware of a request that answers an approval card: settles the stopped call
    (runs it when the person allowed it) and makes what happened that call's result, on the call's
    own row. PRE_AGENT, not the first turn: a loop may add its own message (a plan request) before
    that turn, and the result must follow the call, not that message."""

    async def _middleware(ctx: "AgentLifecycleContext", next_fn: Any) -> None:  # noqa: ANN401
        if ctx.scope is None or ctx.scope.identity.parent_run_id:
            await next_fn()
            return
        history: Optional[ContextManager] = context.tool_state.pop(_RESUME_HISTORY, None)
        answer, context.tool_approval = context.tool_approval, None
        if history is None or answer is None:
            await next_fn()
            return
        settled = await _settle(context, ctx.scope, answer)
        names = await parked_turn.restore(context, history, call_id=settled.call_id)
        if names:
            _show_tools(ctx.scope, names)
        await _bind(history, settled, ctx.scope.goal)
        await next_fn()

    return _middleware


async def keep_stopped_turn(context: "AgentContext", agent: "Agent") -> None:
    """After a run that stopped on a card: the turn as the model had it, for the answer (`parked_turn`)."""
    call_id = context.tool_state.get(_STOPPED_CALL)
    if context.tool_state.get(_PENDING_IN_RUN) and call_id:
        await parked_turn.keep(context, agent, call_id=call_id, ttl_seconds=TURN_TTL_SECONDS)


def _show_tools(run: "RunScope", names: list[str]) -> None:
    """Makes the tools the stopped turn could see visible again."""
    registry = run.runtime.tool_registry
    visible = set(names) & set(registry.names()) if registry is not None else set()
    if visible:
        from app.agent_loop_lib.agent.tool_loop import initial_visible_tools

        run.visible_tools = (run.visible_tools or initial_visible_tools(run.spec, run.runtime)) | visible


@dataclass
class _Settled:
    # The stopped call's id ("" when it isn't known); its tool and arguments, for a history without it.
    call_id: str
    tool: str
    arguments: dict[str, Any]
    result: str
    is_error: bool


async def _settle(context: "AgentContext", run: "RunScope", answer: ToolApprovalAnswer) -> _Settled:
    try:
        claim = await claim_pending(
            context.config_service, answer.approval_id,
            org_id=context.org_id, user_id=context.user_id, conversation_id=context.conversation_id,
        )
    except Exception as e:
        logger.warning(f"Couldn't claim tool approval {answer.approval_id}: {e}")
        # Nothing was claimed, so the card can be answered again.
        return _Settled("", "", {}, "The user's answer to the approval request couldn't be applied, so nothing ran. "
                        "Tell them to answer the card again.", True)
    pending = claim.pending
    if pending is None:
        return _Settled("", "", {}, "The approval request the user answered wasn't found, so nothing ran.", True)
    target = _target_for(context, pending)
    call_id = pending.tool_call_id or f"approval_{pending.approval_id[:16]}"
    tool_name = target.tool.name if target is not None else pending.tool_name
    if claim.status == "used":
        outcome = claim.outcome
        if outcome is None:
            # The first answer is still being applied; its own request finishes the row.
            return _Settled(call_id, tool_name, pending.arguments, (
                f"The user already answered the approval for {pending.tool_name} on {pending.server_name}; "
                "it may still be running. Don't run it again."
            ), True)
        await _show(run, call_id, tool_name, pending, target, outcome)
        return _Settled(call_id, tool_name, pending.arguments, outcome.result, outcome.status != "completed")
    tool, server = pending.tool_name, pending.server_name
    outcome: Optional[Outcome] = None
    try:
        if claim.status == "expired":
            outcome = Outcome(
                answer="expired", status="blocked", preview="The approval request expired.",
                result=f"The approval request for {tool} on {server} expired before the user answered, so it didn't run. "
                "If it's still needed, call the tool again to ask again.",
            )
        elif answer.decision == "deny":
            outcome = Outcome(
                answer="declined", status="blocked", preview="Declined by the user.",
                # Said plainly: models otherwise report it as "declined by the system".
                result=f"The user declined: they chose Deny on the approval card for {tool} on {server}, so it didn't run. "
                # "That call", not the tool: another call of it may still be waiting to be asked for.
                "Tell them you didn't do it because they declined, and don't make that call again unless they ask for it.",
            )
            if answer.remember:
                await _remember(context, pending, "block")
        elif target is None:
            outcome = Outcome(
                answer="allowed", status="blocked", preview="This tool is no longer available.",
                result=f"{tool} on {server} is no longer available here, so the approved action didn't run.",
            )
        else:
            outcome = await _run(context, run, target, pending, call_id)
            if answer.remember and outcome.status != "blocked":
                await _remember(context, pending, "allow")
    finally:
        if claim.status == "claimed":
            # Kept even when the run is stopped mid-call, so a later answer says what may have happened.
            await _keep_outcome(context.config_service, pending.approval_id, outcome or Outcome(
                answer="allowed", status="failed", ran=True, preview="Stopped before its result came back.",
                result=f"The user approved running {tool} on {server}, but the request was stopped while it ran, so it "
                "may or may not have run. Don't run it again; tell the user to check its effect.",
            ))
    if outcome.answer != "allowed" or target is None:
        await _show(run, call_id, tool_name, pending, target, outcome)
    return _Settled(call_id, tool_name, pending.arguments, outcome.result, outcome.status != "completed")


async def _remember(context: "AgentContext", pending: PendingCall, rule: Rule) -> None:
    """ "Don't ask again for this tool": the answer becomes the person's rule for it. A Deny set
    since the card was shown stands over an Allow. An org toolset's rules are the admin's: an answer
    never changes them, whatever the request says."""
    if pending.source != "mcp":
        await _remember_for_personal_toolset(context, pending, rule)
        return
    rules = await _rules_for(context, "mcp", pending.instance_id)
    if rules.unavailable or (rule == "allow" and rules.rules.tools.get(pending.tool_name) == "block"):
        return
    rules.rules.tools[pending.tool_name] = rule
    try:
        await save_own_rules(context.config_service, rules.rules, user_id=context.user_id, instance_id=pending.instance_id)
    except Exception as e:
        logger.warning(f"Couldn't save the rule for {pending.tool_name}: {e}")


async def _remember_for_personal_toolset(context: "AgentContext", pending: PendingCall, rule: Rule) -> None:
    """A personal toolset instance's rules are its owner's: the answer is saved there, once the
    instance is confirmed to be this person's own (the saved call alone doesn't prove it)."""
    if not pending.personal:
        return
    from app.agents.toolset_store import get_personal_instance

    try:
        own = await get_personal_instance(context.config_service, context.org_id, context.user_id, pending.instance_id)
    except Exception as e:
        logger.warning(f"Couldn't check the toolset instance of {pending.tool_name}: {e}")
        return
    if own is None:
        return
    rules = await _rules_for(context, "toolset", pending.instance_id, personal=True)
    if rules.unavailable or (rule == "allow" and rules.rules.tools.get(pending.tool_name) == "block"):
        return
    rules.rules.tools[pending.tool_name] = rule
    try:
        await save_instance_rules(context.config_service, rules.rules, instance_id=pending.instance_id)
    except Exception as e:
        logger.warning(f"Couldn't save the rule for {pending.tool_name}: {e}")


def _target_for(context: "AgentContext", pending: PendingCall) -> Optional[ApprovalTarget]:
    """The tool a saved call was for, as this request loaded it."""
    if pending.source == "mcp":
        targets = [_mcp_target(adapter) for adapter in context.tool_state.get(TOOLS_BY_PATH, {}).values()]
    else:
        targets = list(context.tool_state.get(TOOLSET_TOOLS_BY_PATH, {}).values())
    return next((t for t in targets if (t.instance_id, t.tool_name) == (pending.instance_id, pending.tool_name)), None)


def _start_payload(call_id: str, tool_name: str, pending: PendingCall, target: Optional[ApprovalTarget], outcome_answer: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "tool": tool_name, "args": pending.arguments, "tool_call_id": call_id, "approval_answer": outcome_answer,
    }
    if target is not None:
        payload["display_name"] = target.tool.display_name
        args_summary = _quietly(target.tool.summarize_args, pending.arguments)
        if isinstance(args_summary, str):
            payload["args_summary"] = args_summary
    return payload


async def _show(run: Any, call_id: str, tool_name: str, pending: PendingCall, target: Optional[ApprovalTarget], outcome: Outcome) -> None:  # noqa: ANN401
    """The stopped call's row, settled: the same id, so it replaces the card's row in the reply."""
    start = _start_payload(call_id, tool_name, pending, target, outcome.answer)
    if outcome.ran:
        # Kept on the reply's part: regenerating that reply could run the action again.
        start["approved"] = True
    await _emit(run, EventType.TOOL_CALL_START, start)
    await _emit(run, EventType.TOOL_CALL_END, {
        "tool": tool_name, "tool_call_id": call_id, "is_error": outcome.status == "failed",
        "content": outcome.preview, "result_summary": outcome.summary, **_end_status(outcome),
    })


def _end_status(outcome: Outcome) -> dict[str, Any]:
    if outcome.status == "blocked":
        return {"status": ToolCallStatus.BLOCKED, "reason": outcome.preview}
    return {"status": ToolCallStatus.ERROR if outcome.status == "failed" else ToolCallStatus.SUCCESS}


async def _run(context: "AgentContext", run: Any, target: ApprovalTarget, pending: PendingCall, call_id: str) -> Outcome:  # noqa: ANN401
    """The saved call, through the executor (so every PreToolUse check runs again: a Block set
    since the card was shown still stops it), on the stopped call's row."""
    tool, server = pending.tool_name, pending.server_name
    name = target.tool.name
    denied: list[str] = []

    async def _on_denied(reason: str) -> None:
        denied.append(reason)

    call = ToolCall(id=call_id, name=name, arguments=pending.arguments)
    await _emit(run, EventType.TOOL_CALL_START, {**_start_payload(call_id, name, pending, target, "allowed"), "approved": True})
    context.tool_state[_APPROVED_CALL] = _identity(target)
    try:
        result = await ToolExecutor(run.runtime.tool_registry, run.runtime.hooks).call_tool(
            call, session_id=run.session_id, on_denied=_on_denied,
        )
    except Exception as e:
        logger.warning(f"Running the approved call {tool} failed: {e}", exc_info=True)
        outcome = Outcome(
            answer="allowed", status="failed", ran=True, preview="Something went wrong while it ran.",
            result=f"The user approved running {tool} on {server}, but something went wrong while it ran, so it may "
            "or may not have run. Don't run it again; tell the user to check its effect.",
        )
        await _emit(run, EventType.TOOL_CALL_END, {
            "tool": name, "tool_call_id": call_id, "is_error": True, "content": outcome.preview,
            "result_summary": None, "status": ToolCallStatus.ERROR,
        })
        return outcome
    finally:
        context.tool_state.pop(_APPROVED_CALL, None)
    end: dict[str, Any] = {"tool": name, "tool_call_id": call_id, "is_error": result.is_error, "result_summary": None}
    text = _text(result.content)
    if denied:
        outcome = Outcome(
            answer="allowed", status="blocked", preview=denied[0],
            result=f"The user approved running {tool} on {server}, but it wasn't run: {denied[0]}",
        )
        end.update(content=denied[0], status=ToolCallStatus.BLOCKED, reason=denied[0])
    elif result.is_error:
        outcome = Outcome(
            answer="allowed", status="failed", ran=True, preview=text[:_MAX_PREVIEW_CHARS],
            result=f"The user approved running {tool} on {server}, but it failed: {text}",
        )
        end.update(content=outcome.preview, status=ToolCallStatus.ERROR)
    else:
        summary = _quietly(target.tool.summarize_result, pending.arguments, result)
        outcome = Outcome(
            answer="allowed", status="completed", ran=True, preview=text[:_MAX_PREVIEW_CHARS],
            summary=summary if isinstance(summary, str) else None, result=text,
        )
        end.update(content=outcome.preview, result_summary=outcome.summary, status=ToolCallStatus.SUCCESS)
        view = _quietly(target.tool.result_view, pending.arguments, result)
        if isinstance(view, dict):
            end["result_view"] = view
    await _emit(run, EventType.TOOL_CALL_END, end)
    return outcome


def _quietly(fn: Any, *args: Any) -> Any:  # noqa: ANN401
    try:
        return fn(*args)
    except Exception:
        return None


async def _bind(history: "ContextManager", settled: _Settled, goal: Any) -> None:  # noqa: ANN401
    """Makes the outcome the stopped call's result in the conversation the model reads. The turn's
    closing words ("Waiting for your approval…") go: the result follows the call. A call the history
    doesn't have (a sub-agent's, whose row is nested) is added after it, with its result. Call ids
    are unique in the history, so the call is looked for in all of it: adding one that is already
    there would repeat its id, which providers refuse."""
    messages = list(await history.messages())
    while messages and isinstance(messages[-1], AssistantMessage) and not messages[-1].tool_calls:
        messages.pop()
    if settled.call_id:
        candidates = range(len(messages) - 1, -1, -1)
    else:
        turn_start = max((i for i, m in enumerate(messages) if isinstance(m, UserMessage)), default=0)
        candidates = range(turn_start, len(messages))
    index = next((
        i for i in candidates
        if isinstance(messages[i], ToolMessage) and _is_stopped_call(messages[i], settled.call_id)
    ), None)
    if index is not None:
        messages[index] = ToolMessage(
            content=settled.result, tool_call_id=messages[index].tool_call_id, is_error=settled.is_error,
        )
    elif settled.call_id:
        messages.append(AssistantMessage(
            content=[], tool_calls=[ToolCall(id=settled.call_id, name=settled.tool, arguments=settled.arguments)],
        ))
        messages.append(ToolMessage(content=settled.result, tool_call_id=settled.call_id, is_error=settled.is_error))
    else:
        goal.constraints.append(settled.result)
    await history.clear()
    for message in messages:
        await history.add(message)


def _is_stopped_call(message: ToolMessage, call_id: str) -> bool:
    if call_id:
        return message.tool_call_id == call_id
    return isinstance(message.content, str) and message.content.startswith(WAITING_PREFIX)


async def _emit(run: Any, event_type: EventType, payload: dict[str, Any]) -> None:  # noqa: ANN401
    emitter = run.runtime.event_emitter
    if emitter is not None:
        await emitter.emit(AgentEvent(event_type=event_type, run_context=run.identity, payload=payload))


def _text(content: Any) -> str:  # noqa: ANN401
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(str(getattr(part, "text", "")) for part in content if getattr(part, "text", None))
    else:
        text = json.dumps(content, default=str, ensure_ascii=False)
    return text if len(text) <= _MAX_RESULT_CHARS else text[:_MAX_RESULT_CHARS] + "\n[result cut]"
