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
  server, with the arguments it was asked with (or the person's edit of them, once it passes the
  tool's schema and rules; a refused edit runs nothing and leaves the card open), and its result
  becomes the result of the call the model made. A Deny may carry a note the model is told to
  follow. The model carries on from there in the same reply; it never re-creates the call.
  "Don't ask again" also saves the answer as the person's rule for an MCP tool or a personal
  toolset; an org toolset's rules change only in its rules editor.
- A card is answered in the PipesHub web app, or in Slack when the bot's "Approve actions from
  Slack" setting is on (client `slack-approvals`). Nobody can answer one anywhere else (Slack
  without that setting, the API, the MCP gateway, a service account): there, Ask is Block. Only a
  pre-approved tool runs there.

Enforced at the tool executor's one checkpoint (PreToolUse). `MCP_TOOL_APPROVALS=false` turns it
off for MCP tools, `TOOLSET_TOOL_APPROVALS=false` for toolset tools. Approvals are bound to the
signed-in user and the conversation they were asked in, and are answered only from a run someone
is watching. Whether a run is watched rests on the `client-name` header, which Node sets to `api`
for OAuth and personal access tokens, so only a web sign-in can claim the web app's name. Node sets
`slack-approvals` itself, only for the Slack bot's own token when that setting is on.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal, Optional

import regex
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as SchemaValidationError
from jsonschema.validators import extend, validator_for
from pydantic import Field, ValidationError, model_validator

from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.events.base import AgentEvent, EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.decisions import PendingApproval
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL
from app.agents.actions.util.read_only_sql import read_only_statement
from app.agents.agent_loop import parked_turn
from app.agents.constants.mcp_server_constants import (
    get_conversation_allow_path,
    get_mcp_tool_approval_claim_path,
    get_mcp_tool_approval_path,
    get_mcp_tool_approval_user_index_dir,
    get_mcp_user_tool_rules_path,
)
from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
from app.agents.mcp.models import MCPCamelModel
from app.agents.mcp.tool_kind import ToolKind  # noqa: TC001
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
SLACK_APPROVALS_CLIENT_NAME = "slack-approvals"  # the Node API sets it only for a bot whose admin turned approvals on
# How the app's answer to a card starts; the rest of the text is only for reading.
TOOL_APPROVAL_RESUME_PREFIX = "Tool approval:"
# How much of an approved call's result the model is given.
_MAX_RESULT_CHARS = 20_000
# How much of it the call's row keeps.
_MAX_PREVIEW_CHARS = 2_000
# Arguments larger than this are shown on the card as a preview only.
_MAX_CARD_ARGUMENT_CHARS = 8_000
# A tool's input schema larger than this isn't sent on the card, so the card can't be edited.
_MAX_CARD_SCHEMA_CHARS = 16_000
# How large an answer's edited arguments may be, as JSON.
_MAX_EDITED_ARGUMENT_CHARS = 64_000
EDITS_ENV = "TOOL_APPROVAL_EDITS"
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
# The answer couldn't be applied (a refused edit, or a store failure while checking it): this turn
# only tells the person so, and runs nothing.
_ANSWER_NOT_APPLIED = "_tool_approval_answer_not_applied"


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
    # Saved with the call so an edited answer can be checked against the tool's own schema and kind.
    tool_path: str = ""
    kind: ToolKind = "write"
    input_schema: dict[str, Any] | None = None
    # Whether the call was asked in a project chat, where an unset Block asks instead.
    enabled_on_project: bool = False
    # Who asked (`context.client_name`): the list of waiting calls only shows the app's own.
    client: str = ""
    created_at: float
    expires_at: float


class ToolApprovalAnswer(MCPCamelModel):
    """What the app sends when a person answers an approval card."""

    approval_id: str = Field(min_length=1, max_length=64)
    decision: Decision
    # "Don't ask again for this tool": the answer becomes the person's rule for it.
    remember: bool = False
    # "Allow for this conversation": no more cards for this tool in this conversation.
    remember_for_conversation: bool = False
    # The arguments the card showed (`args_hash`), and the person's edit of them, to run instead.
    args_hash: str = Field(default="", max_length=64)
    arguments: dict[str, Any] | None = None
    # Why the person declined, for the model to follow.
    feedback: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _fits_the_decision(self) -> "ToolApprovalAnswer":
        if self.arguments is not None:
            if self.decision != "allow" or self.remember:
                raise ValueError("Edited arguments go only with allow, and not with remember")
            text = json.dumps(self.arguments, separators=(",", ":"), default=str, ensure_ascii=False)
            if len(text) > _MAX_EDITED_ARGUMENT_CHARS:
                raise ValueError("The edited arguments are too large")
        if self.remember_for_conversation and (self.decision != "allow" or self.remember or self.arguments is not None):
            raise ValueError("Allowing for the conversation goes only with a plain allow")
        if self.feedback is not None and self.decision != "deny":
            raise ValueError("Feedback goes only with deny")
        return self


class Outcome(MCPCamelModel):
    """What came of an answered card: the call's row, and the result the model is given. Kept with
    the claim, so answering the same card again shows the same thing and runs nothing."""

    answer: Literal["allowed", "declined", "expired"]
    status: Literal["completed", "failed", "blocked"]
    result: str
    preview: str = ""
    summary: Optional[str] = None
    ran: bool = False
    # The person's edit of the arguments, when that is what ran.
    edited_arguments: dict[str, Any] | None = None
    # The note the person declined with, for the card's receipt.
    note: str | None = None


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


def kind_of_call(kind: ToolKind, tool_path: str, arguments: Any) -> ToolKind:  # noqa: ANN401
    """What this one call does to data: the tool's kind, unless this call can't be undone."""
    check = _CALL_CHECKS.get(tool_path)
    if check is None or kind == "destructive":
        return kind
    field, keeps_its_kind = check
    value = arguments.get(field) if isinstance(arguments, dict) else None
    return kind if keeps_its_kind(value) else "destructive"


def call_kind(target: "ApprovalTarget", tool_path: str, arguments: Any) -> ToolKind:  # noqa: ANN401
    return kind_of_call(target.kind, tool_path, arguments)


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
    enabled_on_project: bool = False,
) -> Verdict:
    """The rule for one call. `approved_call`: this is the call a person just approved (it runs
    unless something now blocks it). `enabled_on_project`: a project chat, where only the tools
    the project enables are loaded; there a tool nobody set a rule for that would start at Block
    asks instead, since enabling it on the project is a request to be able to use it. A Block
    someone set still blocks, and an unwatched run still can't ask."""
    effective = own or starting_rule(kind=kind, source=source)
    if own is None and effective == "block" and enabled_on_project:
        effective = "ask"
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
        context.client_name in (WEB_CLIENT_NAME, SLACK_APPROVALS_CLIENT_NAME) and context.chat_streaming
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


async def read_pending(config_service: "ConfigurationService", approval_id: str) -> PendingCall | None:
    """The saved call, without claiming it. A store failure raises."""
    raw = await _read(config_service, get_mcp_tool_approval_path(approval_id))
    try:
        return PendingCall.model_validate(raw) if isinstance(raw, dict) else None
    except ValidationError:
        return None


async def claim_pending(
    config_service: "ConfigurationService", approval_id: str, *, org_id: str, user_id: str, conversation_id: Optional[str],
) -> Claim:
    """Claims the saved call for this answer. Claiming makes it used: two answers to the same card
    settle it once. A store failure raises, and nothing is claimed."""
    pending = await read_pending(config_service, approval_id)
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
    await _drop_index_entry(config_service, pending)
    return Claim("claimed", pending)


def _index_path(pending: PendingCall) -> str:
    return f"{get_mcp_tool_approval_user_index_dir(pending.org_id, pending.user_id)}/{pending.approval_id}"


async def _drop_index_entry(config_service: "ConfigurationService", pending: PendingCall) -> None:
    try:
        await config_service.delete_config(_index_path(pending))
    except Exception as e:
        logger.warning(f"Couldn't drop the index entry of tool approval {pending.approval_id}: {e}")


async def list_pending_for(config_service: "ConfigurationService", *, org_id: str, user_id: str) -> list[dict[str, Any]]:
    """The calls waiting on this person, newest first: expired, claimed and unreadable entries are left out."""
    index_dir = get_mcp_tool_approval_user_index_dir(org_id, user_id)
    now = time.time()
    entries: list[dict[str, Any]] = []
    for key in await config_service.list_keys_in_directory(f"{index_dir}/"):
        approval_id = key.rsplit("/", 1)[-1]
        entry = await _read(config_service, f"{index_dir}/{approval_id}")
        if not isinstance(entry, dict) or not isinstance(entry.get("card"), dict) or entry.get("expiresAt", 0) <= now * 1000:
            continue
        if await _read(config_service, get_mcp_tool_approval_claim_path(approval_id)) is not None:
            continue
        entries.append(entry)
    return sorted(entries, key=lambda e: e.get("createdAt", 0), reverse=True)


async def pass_over_waiting_calls(context: "AgentContext") -> None:
    """A new message in a conversation moves past the call waiting in it (the chat shows it as not
    run): its claim says so, so a later answer from anywhere runs nothing, and it leaves the list."""
    if not context.conversation_id:
        return
    index_dir = get_mcp_tool_approval_user_index_dir(context.org_id, context.user_id)
    try:
        for key in await context.config_service.list_keys_in_directory(f"{index_dir}/"):
            approval_id = key.rsplit("/", 1)[-1]
            entry = await _read(context.config_service, f"{index_dir}/{approval_id}")
            if not isinstance(entry, dict) or entry.get("conversationId") != context.conversation_id:
                continue
            outcome = Outcome(
                answer="expired", status="blocked", preview="A newer message was sent before this was answered.",
                result="The user sent a new message instead of answering this approval request, so it didn't run. "
                "If it's still needed, call the tool again to ask again.",
            )
            # If absent: an answer that claimed it first keeps its own outcome.
            await context.config_service.create_config_if_absent(
                get_mcp_tool_approval_claim_path(approval_id),
                {"claimedAt": time.time(), "outcome": outcome.model_dump(by_alias=True)}, ttl_seconds=_RECORD_TTL_SECONDS,
            )
            await context.config_service.delete_config(f"{index_dir}/{approval_id}")
    except Exception as e:
        logger.warning(f"Couldn't pass over the waiting tool approvals of conversation {context.conversation_id}: {e}")


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


def args_hash(arguments: dict[str, Any]) -> str:
    """Names one set of arguments, so an answer can say which ones it saw."""
    # default=str: the saved call must never fail to hash, whatever a tool was given.
    text = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


class EditError(MCPCamelModel):
    # A JSON pointer to the argument; "" for the call as a whole.
    path: str
    message: str


_KIND_RANK: dict[str, int] = {"read": 0, "write": 1, "destructive": 2}

# Every regex check of one edit (`pattern` on values, `patternProperties` and the regex part of
# `additionalProperties` on keys) shares this budget: a server-supplied regex can backtrack for
# ages, and the check runs on the event loop. An edit it can't check in time is refused: the tool
# doesn't check schema patterns itself.
_PATTERN_BUDGET_SECONDS = 0.2


def _path_to(root: Any, target: Any) -> tuple[Any, ...] | None:  # noqa: ANN401
    """Where `target` (by identity) sits in `root`, for a log line that names a field, not a value.
    Walked with a stack, so a deeply nested edit can't overflow it."""
    stack: list[tuple[Any, tuple[Any, ...]]] = [(root, ())]
    while stack:
        node, path = stack.pop()
        if node is target:
            return path
        children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        stack.extend((child, (*path, key)) for key, child in reversed(list(children)))
    return None


@dataclass
class _PatternSkips:
    """The regex checks of one edit that ran out of time: how many, and where the first was."""

    count: int = 0
    first: Any = None


def _bounded_validator(base: Any, deadline: float, skips: _PatternSkips) -> Any:  # noqa: ANN401
    """The schema's validator with its regex keywords run under one shared time budget."""

    def search(patrn: str, text: str, where: Any) -> bool | None:  # noqa: ANN401
        """Whether `patrn` matches `text`; None when it can't be told in time."""
        # Once the budget is spent, every later check is skipped with no work, however many there are.
        if not skips.count:
            left = deadline - time.monotonic()
            if left > 0:
                try:
                    return regex.search(patrn, text, timeout=left) is not None
                except TimeoutError:
                    pass
            skips.first = where
        skips.count += 1
        return None

    def pattern(validator: Any, patrn: str, instance: Any, schema: Any):  # noqa: ANN202, ANN401
        if validator.is_type(instance, "string") and search(patrn, instance, instance) is False:
            yield SchemaValidationError(f"{instance!r} does not match {patrn!r}")

    def pattern_properties(validator: Any, patterns: dict[str, Any], instance: Any, schema: Any):  # noqa: ANN202, ANN401
        if not validator.is_type(instance, "object"):
            return
        for patrn, subschema in patterns.items():
            for key, value in instance.items():
                if search(patrn, key, instance):
                    yield from validator.descend(value, subschema, path=key, schema_path=patrn)

    def additional_properties(validator: Any, ap: Any, instance: Any, schema: Any):  # noqa: ANN202, ANN401
        if not validator.is_type(instance, "object"):
            return
        properties = schema.get("properties", {})
        joined = "|".join(schema.get("patternProperties", {}))
        # A key whose match can't be told in time counts as matched; the skip refuses the edit.
        extras = sorted(
            (k for k in instance if k not in properties and not (joined and search(joined, k, instance) is not False)),
            key=str,
        )
        if validator.is_type(ap, "object"):
            for extra in extras:
                yield from validator.descend(instance[extra], ap, path=extra)
        elif not ap and extras:
            yield SchemaValidationError(f"Additional properties are not allowed ({', '.join(map(repr, extras))} unexpected)")

    return extend(base, {"pattern": pattern, "patternProperties": pattern_properties, "additionalProperties": additional_properties})


# Deeper than this, checking an edit would overflow the validator's recursion.
_MAX_SCHEMA_DEPTH = 200


def schema_is_editable(schema: dict[str, Any]) -> bool:
    """Whether an edit can be checked against this schema in bounded time and depth. unevaluatedProperties
    matches keys against patternProperties with no time limit, so a schema with both is not, and
    neither is one nested too deep. Walked with a stack, each object once."""
    found: set[str] = set()
    seen: set[int] = set()
    stack: list[tuple[Any, int]] = [(schema, 0)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, (dict, list)) or id(node) in seen:
            continue
        if depth > _MAX_SCHEMA_DEPTH:
            return False
        seen.add(id(node))
        if isinstance(node, dict):
            found.update(k for k in ("unevaluatedProperties", "patternProperties") if k in node)
            stack.extend((v, depth + 1) for v in node.values())
        else:
            stack.extend((v, depth + 1) for v in node)
    return len(found) < 2


def check_edit(
    pending: PendingCall, edited: dict[str, Any], *, shown_hash: str, own_rule: Rule | None, enabled_on_project: bool = False,
) -> list[EditError]:
    """Why an edited set of arguments can't be run, one stage at a time (the first that fails
    answers): the card is current, the tool can be edited, the schema holds, the call is no riskier
    than the one asked for, and no rule now blocks it."""
    if shown_hash != args_hash(pending.arguments):
        return [EditError(path="", message="This request changed. Reload.")]
    # A call saved before edits existed has no tool path, and its kind is a default, not the tool's.
    if not edits_enabled() or pending.input_schema is None or not pending.tool_path or not schema_is_editable(pending.input_schema):
        return [EditError(path="", message="This action can't be edited.")]
    schema = pending.input_schema
    skips = _PatternSkips()
    try:
        validator_class = validator_for(schema, default=Draft202012Validator)
        validator_class.check_schema(schema)
        validator = _bounded_validator(validator_class, time.monotonic() + _PATTERN_BUDGET_SECONDS, skips)(schema)
        errors = [EditError(path=_pointer(e.absolute_path), message=e.message) for e in validator.iter_errors(edited)]
    except Exception as e:  # a server's schema can be malformed: bad types, a $ref that can't be resolved
        logger.warning(f"The schema of {pending.tool_name} can't be used to check an edit: {e}")
        return [EditError(path="", message="This action can't be edited.")]
    if skips.count:
        # Names the field, never its value.
        where = _pointer(_path_to(edited, skips.first) or ())
        logger.warning("%d pattern checks of %s were skipped, from %r: they ran out of time", skips.count, pending.tool_path, where)
        errors.append(EditError(path=where, message="This value couldn't be checked in time. Shorten it or allow the original."))
    if errors:
        return errors
    kind = kind_of_call(pending.kind, pending.tool_path, edited)
    if _KIND_RANK[kind] > _KIND_RANK[pending.kind]:
        return [EditError(path="", message="This change makes the action riskier, so it can't be made here. Use Suggest a change.")]
    verdict = decide(own=own_rule, kind=kind, source=pending.source, watched=True, approved_call=False, enabled_on_project=enabled_on_project)
    if verdict.rule == "block":
        return [EditError(path="", message="A rule blocks this action with these details.")]
    return []


def _pointer(path: Any) -> str:  # noqa: ANN401
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in path)


async def own_rule_for(config_service: "ConfigurationService", pending: PendingCall) -> Rule | None:
    """The rule the person (or, for an org toolset, its admin) set for this call's tool, if any."""
    if pending.source == "mcp":
        rules = await load_own_rules(config_service, user_id=pending.user_id, instance_id=pending.instance_id)
    elif pending.instance_id and (pending.personal or organization_toolset_tool_rules()):
        rules = await load_instance_rules(config_service, instance_id=pending.instance_id)
    else:
        return None
    return rules.tools.get(pending.tool_name)


def edits_enabled() -> bool:
    return env_bool(EDITS_ENV, True)


def _input_schema(tool: "Tool") -> dict[str, Any] | None:
    schema = getattr(tool, "raw_input_schema", None)
    if schema is None:
        schema = _quietly(lambda: tool.to_schema().input_schema)
    return schema if isinstance(schema, dict) else None


def approval_gate(context: "AgentContext") -> Any:  # noqa: ANN401
    """PreToolUse middleware; registered last, so nothing after it can still deny a call it
    leaves pending."""

    async def _gate(ctx: "ToolCallContext", next_fn: Any) -> None:  # noqa: ANN401
        # Not even an Allow-rule tool: the model would act on a call the person hasn't settled.
        if context.tool_state.get(_ANSWER_NOT_APPLIED) and TAG_LIFECYCLE_TERMINAL not in ctx.tags:
            ctx.deny("Nothing runs until the user fixes the approval card.")
            await next_fn()
            return
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
            enabled_on_project=context.project_tools_selected,
        )
        if verdict.rule == "block":
            if verdict.reason == "unattended":
                # Names why no card was shown: the client the run came from can't answer one.
                logger.info(
                    "Tool approval: %s on %s not run: nobody can approve it from client %r (streaming=%s, service account=%s)",
                    target.tool_name, target.owner, context.client_name, bool(context.chat_streaming), bool(context.is_service_account),
                )
            ctx.deny(_blocked_message(verdict.reason, target))
        elif verdict.rule == "ask" and not await _allowed_for_conversation(context, target):
            await _ask(context, ctx, target)
        await next_fn()

    return _gate


async def _allowed_for_conversation(context: "AgentContext", target: ApprovalTarget) -> bool:
    """Whether this person already allowed this tool, as it is now, for this conversation."""
    schema = _input_schema(target.tool)
    if schema is None or not context.conversation_id:
        return False
    path = get_conversation_allow_path(context.conversation_id, target.source, target.instance_id, target.tool_name)
    try:
        raw = await _read(context.config_service, path)
    except Exception as e:
        # Asking again is the safe answer to a store that can't be read.
        logger.warning(f"Couldn't read the conversation allow for {target.tool_name}: {e}")
        return False
    return (
        isinstance(raw, dict) and raw.get("userId") == context.user_id and raw.get("schemaHash") == args_hash(schema)
        and raw.get("kind") in _KIND_RANK and _KIND_RANK[target.kind] <= _KIND_RANK[raw["kind"]]
    )


async def _allow_for_conversation(context: "AgentContext", pending: PendingCall, target: ApprovalTarget) -> None:
    # An org toolset's rules are the admin's: the card doesn't offer this there, and a request that sends it saves nothing.
    if not target.own_rules:
        return
    schema = _input_schema(target.tool)
    if schema is None or not pending.conversation_id:
        return
    path = get_conversation_allow_path(pending.conversation_id, pending.source, pending.instance_id, pending.tool_name)
    try:
        await _write(
            context.config_service, path,
            {"userId": pending.user_id, "schemaHash": args_hash(schema), "kind": pending.kind}, ttl_seconds=_RECORD_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Couldn't save the conversation allow for {pending.tool_name}: {e}")


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
    schema = _input_schema(target.tool)
    if schema is not None and len(json.dumps(schema, default=str, ensure_ascii=False)) > _MAX_CARD_SCHEMA_CHARS:
        schema = None
    pending = PendingCall(
        approval_id=uuid.uuid4().hex, org_id=context.org_id, user_id=context.user_id,
        conversation_id=context.conversation_id or "", source=target.source, personal=target.personal,
        instance_id=target.instance_id,
        server_name=server, tool_name=tool_name, arguments=dict(ctx.tool_input), tool_call_id=getattr(call, "id", None) or "",
        tool_path=ctx.tool_path, kind=target.kind, input_schema=schema, enabled_on_project=context.project_tools_selected,
        client=context.client_name or "", created_at=now, expires_at=now + APPROVAL_TTL_SECONDS,
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
    details = _card_details(target, pending)
    try:
        await _write(
            context.config_service, _index_path(pending),
            {"approvalId": pending.approval_id, "conversationId": pending.conversation_id, "client": pending.client,
             "createdAt": int(now * 1000), "expiresAt": int(pending.expires_at * 1000), "card": details},
            # Listed only while it can be answered: no dead entries for the list to read.
            ttl_seconds=max(1, math.ceil(pending.expires_at - time.time())),
        )
    except Exception as e:
        logger.warning(f"Couldn't index tool approval {pending.approval_id} for listing: {e}")
    ctx.ask_later(PendingApproval(message=message, details=details))


def _card_details(target: ApprovalTarget, pending: PendingCall) -> dict[str, Any]:
    card_arguments = _card_arguments(pending.arguments)
    args_summary = _quietly(target.tool.summarize_args, pending.arguments)
    schema = pending.input_schema
    return {
        "approvalId": pending.approval_id, "instanceId": target.instance_id, "serverName": pending.server_name,
        "toolName": pending.tool_name, "toolTitle": target.title, "readOnly": pending.kind == "read", "kind": pending.kind,
        # "Don't ask again" sets the person's own rule; an org toolset's rules are the admin's.
        "canRemember": target.own_rules,
        "expiresAt": int(pending.expires_at * 1000), **card_arguments,
        "toolPath": pending.tool_path, "source": target.source, "argsHash": args_hash(pending.arguments),
        "displayName": target.tool.display_name,
        "editable": edits_enabled() and card_arguments["arguments"] is not None and schema is not None and schema_is_editable(schema),
        **({"inputSchema": schema} if schema is not None else {}),
        **({"argsSummary": args_summary} if isinstance(args_summary, str) else {}),
    }


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
        try:
            refused = await _refused_edit(context, answer)
        except Exception as e:
            logger.warning(f"Couldn't check the edited arguments of tool approval {answer.approval_id}: {e}")
            context.tool_state[_ANSWER_NOT_APPLIED] = True
            await _bind(history, _not_applied(), ctx.scope.goal)
            await next_fn()
            return
        if refused is not None:
            await _keep_card_open(context, ctx.scope, history, *refused, edited=answer.arguments)
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


async def _refused_edit(
    context: "AgentContext", answer: ToolApprovalAnswer,
) -> tuple[ApprovalTarget, PendingCall, list[EditError]] | None:
    """Why an answer's edited arguments can't run, checked before the call is claimed, so a refused
    edit leaves the card answerable. None when there is nothing to refuse: no edit, the arguments
    as asked, or a call that isn't waiting for this person any more (settling it says why). A store
    failure raises."""
    if answer.arguments is None:
        return None
    pending = await read_pending(context.config_service, answer.approval_id)
    if (
        pending is None or answer.arguments == pending.arguments
        or (pending.org_id, pending.user_id, pending.conversation_id) != (context.org_id, context.user_id, context.conversation_id or "")
        or pending.expires_at <= time.time()
        or await _read(context.config_service, get_mcp_tool_approval_claim_path(pending.approval_id)) is not None
    ):
        return None
    target = _target_for(context, pending)
    if target is None:
        return None
    errors = check_edit(
        pending, answer.arguments, shown_hash=answer.args_hash,
        own_rule=await own_rule_for(context.config_service, pending), enabled_on_project=pending.enabled_on_project,
    )
    return (target, pending, errors) if errors else None


async def _keep_card_open(
    context: "AgentContext", run: "RunScope", history: "ContextManager",
    target: ApprovalTarget, pending: PendingCall, errors: list[EditError], *, edited: dict[str, Any],
) -> None:
    """A refused edit: the card is shown again with what's wrong, nothing is claimed or run, and the
    turn still waits on the call. The run goes on only so the reply can point the person at the card;
    the gate runs no tool meanwhile."""
    call_id = pending.tool_call_id or f"approval_{pending.approval_id[:16]}"
    message = f"{WAITING_PREFIX} {pending.tool_name} on {pending.server_name}."
    details = {**_card_details(target, pending), "editErrors": [e.model_dump(by_alias=True) for e in errors]}
    # The card reopens on the person's edit; one too large to show reopens on the call as asked.
    if _card_arguments(edited)["arguments"] is not None:
        details["editedArguments"] = edited
    await _emit(run, EventType.TOOL_BLOCKED, {
        "tool": target.tool.name, "reason": message, "tool_call_id": call_id,
        "status": ToolCallStatus.AWAITING_APPROVAL, "approval": details,
    })
    context.tool_state[_ANSWER_NOT_APPLIED] = True
    context.tool_state[_PENDING_IN_RUN] = pending.approval_id
    context.tool_state[PENDING_MESSAGE] = message
    names = await parked_turn.restore(context, history, call_id=call_id)
    if names:
        _show_tools(run, names)
    run.goal.constraints.append(
        f"The user's changes to {pending.tool_name} on {pending.server_name} can't be used, so nothing ran and it still "
        "waits for their approval. Don't call any tool. In one short sentence, tell them to fix the highlighted fields "
        "on the approval card."
    )


def _show_tools(run: "RunScope", names: list[str]) -> None:
    """Makes the tools the stopped turn could see visible again."""
    registry = run.runtime.tool_registry
    visible = set(names) & set(registry.names()) if registry is not None else set()
    if visible:
        from app.agent_loop_lib.agent.tool_loop import initial_visible_tools

        run.visible_tools = (run.visible_tools or initial_visible_tools(run.spec, run.runtime)) | visible


@dataclass
class _Settled:
    # The stopped call's id ("" when it isn't known); its tool and the arguments it ran with, for a
    # history without it.
    call_id: str
    tool: str
    arguments: dict[str, Any]
    result: str
    is_error: bool
    # The person edited the arguments: the call in the history gets them too.
    edited: bool = False


def _not_applied() -> _Settled:
    # Nothing was claimed, so the card can be answered again.
    return _Settled("", "", {}, "The user's answer to the approval request couldn't be applied, so nothing ran. "
                    "Tell them to answer the card again.", True)


_MISSING = object()


def _changed_fields(asked: dict[str, Any], edited: dict[str, Any]) -> list[str]:
    return sorted(k for k in asked.keys() | edited.keys() if asked.get(k, _MISSING) != edited.get(k, _MISSING))


_MAX_NOTE_VALUE_CHARS = 200


def _note_value(value: Any) -> str:  # noqa: ANN401
    text = "(cleared)" if value is _MISSING else json.dumps(value, default=str, ensure_ascii=False)
    return text if len(text) <= _MAX_NOTE_VALUE_CHARS else text[:_MAX_NOTE_VALUE_CHARS] + "…"


def _changed_note(asked: dict[str, Any], edited: dict[str, Any]) -> str:
    # Said outright: told only which fields changed, a model that later saw the new values (a sent
    # email's address) took them for a mistake and ran the call again with its own.
    fields = _changed_fields(asked, edited)
    changes = "; ".join(f"`{k}` = {_note_value(edited.get(k, _MISSING))}" for k in fields)
    return (
        f"The user changed {', '.join(f'`{k}`' for k in fields)} before approving, and the call ran with their values: {changes}. "
        "Their edit is their latest instruction and overrides anything said earlier: don't run the call again to undo or "
        "repeat it, and don't call the difference a mistake."
    )


async def _settle(context: "AgentContext", run: "RunScope", answer: ToolApprovalAnswer) -> _Settled:
    try:
        claim = await claim_pending(
            context.config_service, answer.approval_id,
            org_id=context.org_id, user_id=context.user_id, conversation_id=context.conversation_id,
        )
    except Exception as e:
        logger.warning(f"Couldn't claim tool approval {answer.approval_id}: {e}")
        return _not_applied()
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
        await _show(run, call_id, tool_name, pending, target, outcome, replayed=True)
        return _settled(call_id, tool_name, pending, outcome)
    tool, server = pending.tool_name, pending.server_name
    # Checked before the claim (`_refused_edit`); the arguments as asked are a plain allow.
    edited = answer.arguments if answer.arguments not in (None, pending.arguments) else None
    outcome: Optional[Outcome] = None
    try:
        if claim.status == "expired":
            outcome = Outcome(
                answer="expired", status="blocked", preview="The approval request expired.",
                result=f"The approval request for {tool} on {server} expired before the user answered, so it didn't run. "
                "If it's still needed, call the tool again to ask again.",
            )
        elif answer.decision == "deny" and answer.feedback:
            outcome = Outcome(
                answer="declined", status="blocked", preview=f"Declined: {answer.feedback}", note=answer.feedback,
                result=f"The user declined this call and said: {answer.feedback}. Follow their instruction; "
                "don't repeat the same call.",
            )
            if answer.remember:
                await _remember(context, pending, "block")
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
            outcome = await _run(context, run, target, pending, call_id, edited)
            if answer.remember and outcome.status != "blocked":
                await _remember(context, pending, "allow")
            if answer.remember_for_conversation and outcome.status != "blocked":
                await _allow_for_conversation(context, pending, target)
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
    return _settled(call_id, tool_name, pending, outcome)


def _settled(call_id: str, tool_name: str, pending: PendingCall, outcome: Outcome) -> _Settled:
    edited = outcome.edited_arguments
    return _Settled(
        call_id, tool_name, edited if edited is not None else pending.arguments, outcome.result,
        outcome.status != "completed", edited=edited is not None,
    )


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


def _start_payload(
    call_id: str, tool_name: str, pending: PendingCall, target: ApprovalTarget | None, outcome_answer: str,
    edited: dict[str, Any] | None = None,
) -> dict[str, Any]:
    arguments = edited if edited is not None else pending.arguments
    payload: dict[str, Any] = {
        "tool": tool_name, "args": arguments, "tool_call_id": call_id, "approval_answer": outcome_answer,
    }
    if edited is not None:
        payload["edited"] = True
        # For the card's receipt: the saved `args` cut long values, so they can't be compared.
        payload["changed_fields"] = _changed_fields(pending.arguments, edited)
    if target is not None:
        payload["display_name"] = target.tool.display_name
        args_summary = _quietly(target.tool.summarize_args, arguments)
        if isinstance(args_summary, str):
            payload["args_summary"] = args_summary
    return payload


async def _show(
    run: Any, call_id: str, tool_name: str, pending: PendingCall, target: Optional[ApprovalTarget],  # noqa: ANN401
    outcome: Outcome, *, replayed: bool = False,
) -> None:
    """The stopped call's row, settled: the same id, so it replaces the card's row in the reply.
    `replayed`: the outcome of an earlier answer, shown again; this answer ran nothing."""
    start = _start_payload(call_id, tool_name, pending, target, outcome.answer, outcome.edited_arguments)
    if outcome.note:
        start["approval_note"] = outcome.note
    if outcome.ran:
        # Kept on the reply's part: regenerating that reply could run the action again.
        start["approved"] = True
    if replayed:
        start["approval_replayed"] = True
    await _emit(run, EventType.TOOL_CALL_START, start)
    await _emit(run, EventType.TOOL_CALL_END, {
        "tool": tool_name, "tool_call_id": call_id, "is_error": outcome.status == "failed",
        "content": outcome.preview, "result_summary": outcome.summary, **_end_status(outcome),
    })


def _end_status(outcome: Outcome) -> dict[str, Any]:
    if outcome.status == "blocked":
        return {"status": ToolCallStatus.BLOCKED, "reason": outcome.preview}
    return {"status": ToolCallStatus.ERROR if outcome.status == "failed" else ToolCallStatus.SUCCESS}


async def _run(
    context: "AgentContext", run: Any, target: ApprovalTarget, pending: PendingCall, call_id: str,  # noqa: ANN401
    edited: dict[str, Any] | None = None,
) -> Outcome:
    """The saved call, or the person's edit of it, through the executor (so every PreToolUse check
    runs again: a Block set since the card was shown still stops it), on the stopped call's row."""
    outcome = await _run_call(context, run, target, pending, call_id, edited)
    if edited is None:
        return outcome
    return outcome.model_copy(update={
        "edited_arguments": edited, "result": f"{_changed_note(pending.arguments, edited)}\n{outcome.result}",
    })


async def _run_call(
    context: "AgentContext", run: Any, target: ApprovalTarget, pending: PendingCall, call_id: str,  # noqa: ANN401
    edited: dict[str, Any] | None,
) -> Outcome:
    tool, server = pending.tool_name, pending.server_name
    name = target.tool.name
    denied: list[str] = []

    async def _on_denied(reason: str) -> None:
        denied.append(reason)

    arguments = edited if edited is not None else pending.arguments
    call = ToolCall(id=call_id, name=name, arguments=arguments)
    await _emit(run, EventType.TOOL_CALL_START, {**_start_payload(call_id, name, pending, target, "allowed", edited), "approved": True})
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
        summary = _quietly(target.tool.summarize_result, arguments, result)
        outcome = Outcome(
            answer="allowed", status="completed", ran=True, preview=text[:_MAX_PREVIEW_CHARS],
            summary=summary if isinstance(summary, str) else None, result=text,
        )
        end.update(content=outcome.preview, result_summary=outcome.summary, status=ToolCallStatus.SUCCESS)
        view = _quietly(target.tool.result_view, arguments, result)
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
        if settled.edited:
            _rewrite_call(messages, messages[index].tool_call_id, settled.arguments)
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


def _rewrite_call(messages: list[Any], call_id: str, arguments: dict[str, Any]) -> None:
    """The model's call, as it ran: with the person's edit of its arguments."""
    for i, message in enumerate(messages):
        if isinstance(message, AssistantMessage) and any(c.id == call_id for c in message.tool_calls or []):
            calls = [c.model_copy(update={"arguments": arguments}) if c.id == call_id else c for c in message.tool_calls]
            messages[i] = message.model_copy(update={"tool_calls": calls})


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
