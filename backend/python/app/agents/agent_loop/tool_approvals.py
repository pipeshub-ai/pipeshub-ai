"""Per-tool approvals for MCP tool calls: which calls run, which wait for a person, which never run.

Each tool has a rule — Allow ("Pre-approved"), Ask ("Allow on approval") or Block ("Deny"). The
rule comes from the agent (set by whoever can edit it) or, in assistant chats, from the person
themselves; without one, it starts from what the tool does to data (`mcp/tool_kind.py`): a
read-only tool at Allow, one that changes data at Ask, one that deletes data at Block. An admin's
company rule for the tool is a floor: Ask or Block. The strictest rule wins.

- **Block**: the call doesn't run, and the model is told why.
- **Ask**: the call is saved for 15 minutes and the turn ends with an approval card on the tool's
  row. The person's choice arrives with the next message (`toolApproval`), and the saved call runs
  then, on the server, with the arguments it was asked with — never re-created by the model.
  "Allow for this chat" and "Always allow" also stop later calls of the tool from asking, unless
  the company rule is "Always ask".
- Nobody can answer a card outside the PipesHub web app (Slack, the API, the MCP gateway, a
  service account): there, Ask is Block, unless the admin allowed the tool unattended.

Enforced at the tool executor's one checkpoint (PreToolUse), so other kinds of tools can join later.
`MCP_TOOL_APPROVALS=false` turns it off. Approvals are bound to the signed-in user and the
conversation they were asked in, and are answered only from a run someone is watching. Whether a
run is watched rests on the `client-name` header, which Node sets to `api` for OAuth and personal
access tokens, so only a web sign-in can claim the web app's name.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional

from pydantic import Field, ValidationError

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.events.base import AgentEvent, EventType, ToolCallStatus
from app.agent_loop_lib.hooks.middleware.decisions import PendingApproval
from app.agent_loop_lib.tools.executor import ToolExecutor
from app.agents.constants.mcp_server_constants import (
    get_mcp_agent_tool_rules_path,
    get_mcp_tool_approval_claim_path,
    get_mcp_tool_approval_path,
    get_mcp_tool_chat_grants_path,
    get_mcp_tool_policy_path,
    get_mcp_user_tool_rules_path,
)
from app.agents.mcp.models import MCPCamelModel
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext, TurnContext
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
    from app.agents.mcp.tool_kind import ToolKind
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

ENABLED_ENV = "MCP_TOOL_APPROVALS"
APPROVAL_TTL_SECONDS = 900
CHAT_GRANT_TTL_SECONDS = 30 * 86400
# The web app's `client-name`: the one client that can show an approval card.
WEB_CLIENT_NAME = "pipeshub-ai"
# How much of an approved call's result the model is given back on the next turn.
_MAX_RESULT_CHARS = 20_000
# Arguments larger than this are shown on the card as a preview only.
_MAX_CARD_ARGUMENT_CHARS = 8_000

Rule = Literal["allow", "ask", "block"]
CompanyRule = Literal["ask", "block"]
Decision = Literal["allow_once", "allow_chat", "always", "deny"]
_STRICTNESS: dict[str, int] = {"allow": 0, "ask": 1, "block": 2}

# `context.tool_state` keys.
TOOLS_BY_PATH = "_mcp_tools_by_path"
_RULES_CACHE = "_tool_approval_rules"
_PENDING_IN_RUN = "_tool_approval_pending_id"
# The answer of a turn that ended waiting for approval (read by `AnswerFinalizer`).
PENDING_MESSAGE = "_tool_approval_pending_message"
_APPROVED_CALL = "_tool_approval_running"
# Set once the approved call has run, until its result is back: a later failure must not say "nothing ran".
_APPROVED_RAN = "_tool_approval_ran"


class CompanyToolRule(MCPCamelModel):
    rule: Optional[CompanyRule] = None
    # "Allowed when nobody is watching": in a run nobody can answer a card, Ask becomes Allow.
    unattended: bool = False


class CompanyPolicy(MCPCamelModel):
    tools: dict[str, CompanyToolRule] = Field(default_factory=dict)


class ToolRules(MCPCamelModel):
    tools: dict[str, Rule] = Field(default_factory=dict)


class PendingCall(MCPCamelModel):
    approval_id: str
    org_id: str
    user_id: str
    conversation_id: str
    instance_id: str
    server_name: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    created_at: float
    expires_at: float


class ToolApprovalAnswer(MCPCamelModel):
    """What the next message carries when a person answers an approval card."""

    approval_id: str = Field(min_length=1, max_length=64)
    decision: Decision


def enabled() -> bool:
    return env_bool(ENABLED_ENV, True)


@dataclass(frozen=True)
class Verdict:
    rule: Rule
    # Why, for a Block: what the model (and so the person) is told.
    reason: str = ""


_STARTING_RULES: dict[str, Rule] = {"read": "allow", "write": "ask", "destructive": "block"}


def starting_rule(*, kind: ToolKind) -> Rule:
    """A tool's rule where nobody set one."""
    return _STARTING_RULES[kind]


def decide(
    *,
    own: Optional[Rule],
    kind: ToolKind,
    company: Optional[CompanyToolRule],
    watched: bool,
    granted_for_chat: bool = False,
    approved_call: bool = False,
) -> Verdict:
    """The rule for one call. `approved_call`: this is the call a person just approved (it runs
    unless something now blocks it). `granted_for_chat`: "Allow for this chat" was chosen for the
    tool in this conversation."""
    company_rule = company.rule if company is not None else None
    effective = max(own or starting_rule(kind=kind), company_rule or "allow", key=_STRICTNESS.__getitem__)
    if effective == "block":
        if company_rule == "block":
            return Verdict("block", "company")
        return Verdict("block", "own" if own else "default")
    if effective == "allow":
        return Verdict("allow")
    if approved_call:
        return Verdict("allow")
    if granted_for_chat and company_rule != "ask":
        return Verdict("allow")
    if not watched:
        return Verdict("allow") if company is not None and company.unattended else Verdict("block", "unattended")
    return Verdict("ask")


def watched(context: "AgentContext") -> bool:
    """Whether someone can answer an approval card in this run."""
    return bool(
        context.client_name == WEB_CLIENT_NAME and context.chat_streaming
        and context.conversation_id and not context.is_service_account
    )


def rules_owner(context: "AgentContext") -> tuple[Optional[str], str]:
    """Whose rules apply: (agent key, None-able) for an agent chat, else the person's own."""
    if context.agent_key and not context.is_assistant:
        return context.agent_key, context.user_id
    return None, context.user_id


def can_always_allow(context: "AgentContext") -> bool:
    """An agent's rule is set by whoever can edit the agent; a person's own rules by them."""
    agent_key, _ = rules_owner(context)
    return agent_key is None or bool(context.can_edit_agent)


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


async def load_company_policy(config_service: "ConfigurationService", instance_id: str) -> CompanyPolicy:
    return _model(CompanyPolicy, await _read(config_service, get_mcp_tool_policy_path(instance_id)))


async def save_company_policy(config_service: "ConfigurationService", instance_id: str, policy: CompanyPolicy) -> bool:
    return await _write(config_service, get_mcp_tool_policy_path(instance_id), policy.model_dump(by_alias=True, exclude_none=True))


def _own_rules_path(agent_key: Optional[str], user_id: str, instance_id: str) -> str:
    return get_mcp_agent_tool_rules_path(agent_key, instance_id) if agent_key else get_mcp_user_tool_rules_path(user_id, instance_id)


async def load_own_rules(config_service: "ConfigurationService", *, agent_key: Optional[str], user_id: str, instance_id: str) -> ToolRules:
    return _model(ToolRules, await _read(config_service, _own_rules_path(agent_key, user_id, instance_id)))


async def save_own_rules(
    config_service: "ConfigurationService", rules: ToolRules, *, agent_key: Optional[str], user_id: str, instance_id: str,
) -> bool:
    return await _write(config_service, _own_rules_path(agent_key, user_id, instance_id), rules.model_dump(by_alias=True))


async def load_chat_grants(config_service: "ConfigurationService", conversation_id: str, instance_id: str) -> set[str]:
    raw = await _read(config_service, get_mcp_tool_chat_grants_path(conversation_id, instance_id))
    tools = raw.get("tools") if isinstance(raw, dict) else None
    return {t for t in tools if isinstance(t, str)} if isinstance(tools, list) else set()


async def add_chat_grant(config_service: "ConfigurationService", conversation_id: str, instance_id: str, tool_name: str) -> bool:
    tools = await load_chat_grants(config_service, conversation_id, instance_id)
    tools.add(tool_name)
    return await _write(
        config_service, get_mcp_tool_chat_grants_path(conversation_id, instance_id), {"tools": sorted(tools)},
        ttl_seconds=CHAT_GRANT_TTL_SECONDS,
    )


async def claim_pending(
    config_service: "ConfigurationService", approval_id: str, *, org_id: str, user_id: str, conversation_id: Optional[str],
) -> Optional[PendingCall]:
    """The saved call, if it is this person's, in this conversation, unexpired and not used yet.
    Claiming makes it used: two answers to the same card run it once."""
    raw = await _read(config_service, get_mcp_tool_approval_path(approval_id))
    if not isinstance(raw, dict):
        return None
    try:
        pending = PendingCall.model_validate(raw)
    except ValidationError:
        return None
    if (pending.org_id, pending.user_id, pending.conversation_id) != (org_id, user_id, conversation_id or ""):
        logger.warning(f"Tool approval {approval_id} was answered by another user or from another conversation; ignored")
        return None
    if pending.expires_at <= time.time():
        return None
    try:
        claimed = await config_service.create_config_if_absent(
            get_mcp_tool_approval_claim_path(approval_id), {"claimedAt": time.time()}, ttl_seconds=APPROVAL_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Couldn't claim tool approval {approval_id}: {e}")
        return None
    return pending if claimed else None


# ---------------------------------------------------------------------------
# The gate (PreToolUse)
# ---------------------------------------------------------------------------

def mcp_tool_identity(path: str) -> Optional[tuple[str, str]]:
    """(instance id, the server's own tool name) of an MCP tool path `/mcp/{instance}/{tool}`.
    The tool name may itself contain `/`; the instance id never does."""
    prefix = "/mcp/"
    if not path.startswith(prefix):
        return None
    instance_id, _, tool_name = path[len(prefix):].partition("/")
    return (instance_id, tool_name) if instance_id and tool_name else None


@dataclass
class _InstanceRules:
    company: CompanyPolicy
    own: ToolRules
    chat_grants: set[str]
    # The rules couldn't be read: every tool of the instance is refused, and nothing is saved.
    unavailable: bool = False


async def _rules_for(context: "AgentContext", instance_id: str) -> _InstanceRules:
    """Read once per request and instance; an answer to a card updates it in place."""
    cache: dict[str, _InstanceRules] = context.tool_state.setdefault(_RULES_CACHE, {})
    if instance_id not in cache:
        agent_key, user_id = rules_owner(context)
        try:
            company = await load_company_policy(context.config_service, instance_id)
            own = await load_own_rules(context.config_service, agent_key=agent_key, user_id=user_id, instance_id=instance_id)
            grants = (
                await load_chat_grants(context.config_service, context.conversation_id, instance_id)
                if context.conversation_id else set()
            )
        except Exception as e:
            logger.warning(f"Couldn't read the tool rules of MCP instance {instance_id}: {e}")
            cache[instance_id] = _InstanceRules(CompanyPolicy(), ToolRules(), set(), unavailable=True)
        else:
            cache[instance_id] = _InstanceRules(company, own, grants)
    return cache[instance_id]


def _blocked_message(reason: str, tool: str, server: str, *, agent_chat: bool) -> str:
    if reason == "company":
        return f"{tool} on {server} is blocked by your organization's settings, so it wasn't run."
    if reason == "unattended":
        return (
            f"{tool} on {server} needs someone to approve it in the PipesHub app, so it wasn't run here. "
            "Tell the user what you wanted to do."
        )
    if reason == "default":
        whose = "this agent's" if agent_chat else "your"
        return (
            f"{tool} on {server} can delete data, so it's denied unless {whose} tool approval rules allow it. "
            "It wasn't run. Tell the user they can change its rule to run it."
        )
    return f"{tool} on {server} is blocked {'for this agent' if agent_chat else 'in your settings'}, so it wasn't run."


def _card_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(arguments, default=str, ensure_ascii=False)
    if len(text) <= _MAX_CARD_ARGUMENT_CHARS:
        return {"arguments": arguments}
    return {"arguments": None, "argumentsPreview": text[:_MAX_CARD_ARGUMENT_CHARS] + "…"}


def approval_gate(context: "AgentContext") -> Any:  # noqa: ANN401
    """PreToolUse middleware; registered last, so nothing after it can still deny a call it
    leaves pending."""

    async def _gate(ctx: "ToolCallContext", next_fn: Any) -> None:  # noqa: ANN401
        identity = mcp_tool_identity(ctx.tool_path)
        adapter: Optional[MCPToolAdapter] = context.tool_state.get(TOOLS_BY_PATH, {}).get(ctx.tool_path)
        if not enabled() or identity is None or adapter is None:
            await next_fn()
            return
        instance_id, tool_name = identity
        rules = await _rules_for(context, instance_id)
        if rules.unavailable:
            ctx.deny(
                f"The approval rules for {tool_name} on {adapter.server.display_name} couldn't be checked, "
                "so it wasn't run. Tell the user to try again in a moment."
            )
            await next_fn()
            return
        verdict = decide(
            own=rules.own.tools.get(tool_name),
            kind=adapter.kind,
            company=rules.company.tools.get(tool_name),
            watched=watched(context),
            granted_for_chat=tool_name in rules.chat_grants,
            approved_call=context.tool_state.get(_APPROVED_CALL) == identity,
        )
        server = adapter.server.display_name
        if verdict.rule == "block":
            agent_key, _ = rules_owner(context)
            ctx.deny(_blocked_message(verdict.reason, tool_name, server, agent_chat=agent_key is not None))
        elif verdict.rule == "ask":
            company = rules.company.tools.get(tool_name)
            await _ask(context, ctx, adapter, instance_id, tool_name, always_ask=company is not None and company.rule == "ask")
        await next_fn()

    return _gate


async def _ask(
    context: "AgentContext", ctx: "ToolCallContext", adapter: "MCPToolAdapter", instance_id: str, tool_name: str,
    *, always_ask: bool,
) -> None:
    server = adapter.server.display_name
    if context.tool_state.get(_PENDING_IN_RUN):
        ctx.deny(
            "Another action is already waiting for the user's approval, so this one wasn't run. "
            "Tell the user what you wanted to do; don't run other actions that need approval now."
        )
        return
    now = time.time()
    pending = PendingCall(
        approval_id=uuid.uuid4().hex, org_id=context.org_id, user_id=context.user_id,
        conversation_id=context.conversation_id or "", instance_id=instance_id, server_name=server,
        tool_name=tool_name, arguments=dict(ctx.tool_input), created_at=now, expires_at=now + APPROVAL_TTL_SECONDS,
    )
    # Taken before the write is awaited: calls in one wave run concurrently.
    context.tool_state[_PENDING_IN_RUN] = pending.approval_id
    try:
        saved = await _write(
            context.config_service, get_mcp_tool_approval_path(pending.approval_id),
            pending.model_dump(by_alias=True), ttl_seconds=APPROVAL_TTL_SECONDS,
        )
    except Exception as e:
        logger.warning(f"Couldn't save a tool approval for {tool_name}: {e}")
        saved = False
    if not saved:
        context.tool_state.pop(_PENDING_IN_RUN, None)
        ctx.deny(f"{tool_name} on {server} needs approval, and the approval couldn't be saved, so it wasn't run.")
        return
    message = f"Waiting for your approval to run {tool_name} on {server}."
    context.tool_state[PENDING_MESSAGE] = message
    title = (adapter.tool_info.annotations or {}).get("title")
    ctx.ask_later(PendingApproval(
        message=message,
        details={
            "approvalId": pending.approval_id, "instanceId": instance_id, "serverName": server,
            "toolName": tool_name, "toolTitle": title if isinstance(title, str) else None,
            "readOnly": adapter.kind == "read", "kind": adapter.kind, "canAlwaysAllow": can_always_allow(context) and not always_ask,
            # The company asks every time: "for this chat" and "always" wouldn't stop the next ask.
            "companyAlwaysAsk": always_ask,
            "expiresAt": int(pending.expires_at * 1000), **_card_arguments(pending.arguments),
        },
    ))


# ---------------------------------------------------------------------------
# The answer (the next turn)
# ---------------------------------------------------------------------------

def run_approved_call(context: "AgentContext") -> Any:  # noqa: ANN401
    """PRE_TURN middleware: on the first turn of a request that answers an approval card, record
    the person's choice and, when they approved, run the saved call before the model's first step.
    What happened is added to the goal, so the model carries on from it."""

    async def _middleware(ctx: "TurnContext", next_fn: Any) -> None:  # noqa: ANN401
        answer = context.tool_approval
        if answer is None or ctx.turn_index != 0 or ctx.scope is None or ctx.scope.run.identity.parent_run_id:
            await next_fn()
            return
        context.tool_approval = None
        try:
            note = await _answer(context, ctx, answer)
        except Exception as e:
            logger.warning(f"Answering tool approval {answer.approval_id} failed: {e}", exc_info=True)
            note = (
                "The user approved the action and it ran, but its result couldn't be read back. "
                "Don't run it again; tell the user to check its effect."
                if context.tool_state.pop(_APPROVED_RAN, False)
                else "Applying the user's approval failed, so nothing ran. If the action is still needed, call the tool again."
            )
        ctx.scope.run.goal.constraints.append(note)
        await next_fn()

    return _middleware


async def _answer(context: "AgentContext", ctx: "TurnContext", answer: ToolApprovalAnswer) -> str:
    if not watched(context):
        # Left unclaimed, so the card can still be answered in the app.
        return "Tool approvals can only be answered in the PipesHub app, so nothing ran."
    pending = await claim_pending(
        context.config_service, answer.approval_id,
        org_id=context.org_id, user_id=context.user_id, conversation_id=context.conversation_id,
    )
    if pending is None:
        return (
            "The approval the user answered has expired or was already used, so nothing ran. "
            "If the action is still needed, call the tool again to ask for approval."
        )
    tool, server = pending.tool_name, pending.server_name
    if answer.decision == "deny":
        return f"The user declined to run {tool} on {server}, so it didn't run. Don't run it again unless they ask for it."
    adapter = _adapter_for(context, pending.instance_id, tool)
    if adapter is None:
        return f"{tool} on {server} is no longer available here, so the approved action didn't run."
    result, denied = await _run(context, ctx, adapter, pending)
    if denied is not None:
        return f"The user approved running {tool} on {server}, but it wasn't run: {denied}"
    await _remember_choice(context, pending, answer.decision)
    if result.is_error:
        return f"The user approved running {tool} on {server}, but it failed: {_text(result.content)}"
    return (
        f"The user approved running {tool} on {server}, and it ran with the arguments you asked for. "
        f"Its result:\n{_text(result.content)}\nContinue from this result; don't run it again."
    )


async def _remember_choice(context: "AgentContext", pending: PendingCall, decision: Decision) -> None:
    if decision == "allow_once":
        return
    rules = await _rules_for(context, pending.instance_id)
    if rules.unavailable:
        return
    if decision == "always" and can_always_allow(context):
        if rules.own.tools.get(pending.tool_name) == "block":
            # Blocked since the card was shown; that choice stands.
            return
        agent_key, user_id = rules_owner(context)
        rules.own.tools[pending.tool_name] = "allow"
        await save_own_rules(context.config_service, rules.own, agent_key=agent_key, user_id=user_id, instance_id=pending.instance_id)
        return
    # "Always allow" from someone who can't change the agent's rules still covers this chat.
    rules.chat_grants.add(pending.tool_name)
    await add_chat_grant(context.config_service, pending.conversation_id, pending.instance_id, pending.tool_name)


def _adapter_for(context: "AgentContext", instance_id: str, tool_name: str) -> Optional["MCPToolAdapter"]:
    for adapter in context.tool_state.get(TOOLS_BY_PATH, {}).values():
        if adapter.instance_id == instance_id and adapter.raw_name == tool_name:
            return adapter
    return None


async def _run(context: "AgentContext", ctx: "TurnContext", adapter: "MCPToolAdapter", pending: PendingCall) -> tuple[Any, Optional[str]]:
    """The saved call, through the executor (so every PreToolUse check runs again: a Block set
    since the card was shown still stops it), shown on the activity like any other call. Returns
    the result and, when a check refused it, why."""
    run = ctx.scope.run
    denied: list[str] = []

    async def _on_denied(reason: str) -> None:
        denied.append(reason)

    call = ToolCall(id=f"approved_{pending.approval_id[:16]}", name=adapter.name, arguments=pending.arguments)
    # `approved` stays on the reply's part: regenerating that reply could run the action again.
    await _emit(run, EventType.TOOL_CALL_START, {
        "tool": call.name, "args": call.arguments, "tool_call_id": call.id, "display_name": adapter.display_name,
        "args_summary": adapter.summarize_args(call.arguments), "approved": True,
    })
    context.tool_state[_APPROVED_CALL] = (pending.instance_id, pending.tool_name)
    try:
        result = await ToolExecutor(run.runtime.tool_registry, run.runtime.hooks).call_tool(
            call, session_id=run.session_id, on_denied=_on_denied,
        )
    finally:
        context.tool_state.pop(_APPROVED_CALL, None)
    if not denied:
        context.tool_state[_APPROVED_RAN] = True
    await _emit(run, EventType.TOOL_CALL_END, {
        "tool": call.name, "is_error": result.is_error, "content": _text(result.content)[:2000],
        "tool_call_id": call.id, "result_summary": None,
        "status": ToolCallStatus.BLOCKED if denied else ToolCallStatus.ERROR if result.is_error else ToolCallStatus.SUCCESS,
        **({"reason": denied[0]} if denied else {}),
    })
    context.tool_state.pop(_APPROVED_RAN, None)
    return result, (denied[0] if denied else None)


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
