"""PlatformBroker — concrete implementation of IPlatformBroker.

The broker is the security boundary between sandboxed workflow code and the
rest of the platform.  Every dispatch() call goes through, in order:

  1. Max-calls counter (guards against runaway loops in generated code).
  2. RunGrant enforcement (computed from TaskDefinition and the pinned
     WorkflowVersion, never from sandbox input).
  3. Dry-run simulation of capabilities that always mutate.
  4. Taint check (blocks destructive tools once untrusted content was read).
  5. Capability router → ICapabilityHandler.handle().
  6. Taint state update from the result.

ToolCapabilityHandler (also here) resolves Tool.name through the runtime
ToolRegistry, applies the grant's tool_names allowlist, and executes the
tool with host-side credentials.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.services.workflows.interface.broker import (
    BrokerResult,
    Capability,
    ICapabilityHandler,
    ToolClassification,
    normalize_tool_name,
)
from app.services.workflows.security.taint import TaintState

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.services.workflows.interface.agent_provisioning import IAgentProvisioning
    from app.services.workflows.interface.agent_runner import IWorkflowAgentRunner
    from app.services.workflows.interface.broker import BrokerCall, RunPrincipal
    from app.services.workflows.interface.conversation_writer import IConversationWriter
    from app.services.workflows.interface.state_store import IWorkflowStateStore

__all__ = [
    "PlatformBroker",
    "ToolCapabilityHandler",
    "StateCapabilityHandler",
    "build_platform_broker",
]

logger = logging.getLogger(__name__)

# `category` tag values that mean "this call changes something outside PipesHub".
_MUTATING_TAG_VALUES = frozenset({"write", "destructive"})

# ...those that destroy or overwrite, which taint blocks (see security/taint.py).
_DESTRUCTIVE_TAG_VALUES = frozenset({"destructive"})

# ...and those that pull untrusted external content into the run.
_EXTERNAL_READ_TAG_VALUES = frozenset({"read", "search"})


_UNCLASSIFIED = ToolClassification(is_write=True, is_external_read=True, is_destructive=True)

# Capabilities that always mutate, so a dry run never actually performs them.
# TOOL is absent because `ToolCapabilityHandler` decides it per call from the
# registry's tags -- a read-only tool should still execute so the dry run
# exercises a realistic shape.
#
# AGENT_RUN is here because a sub-agent runs with its own write tools and
# nothing threads `is_dry_run` into the child, so simulating the parent while
# executing the child would break the guarantee the feature is sold on.
# CONVERSATION_EMIT is here because it posts to the user's live conversation.
_DRY_RUN_SIMULATED_CAPABILITIES = frozenset({
    Capability.AGENT_CREATE,
    Capability.AGENT_RUN,
    Capability.CONVERSATION_EMIT,
    Capability.STATE_SET,
})


class PlatformBroker:
    """Concrete IPlatformBroker.  Instantiated once per CodeWorkflowRunner run."""

    def __init__(self) -> None:
        self._handlers: dict[Capability, ICapabilityHandler] = {}
        self._call_counts: dict[str, int] = {}  # run_id → call count
        self._taint_states: dict[str, TaintState] = {}  # run_id → taint state
        self._created_agents: dict[str, set[str]] = {}  # run_id → agent ids minted this run

    # -- Registration -------------------------------------------------------

    def register(self, handler: ICapabilityHandler) -> None:
        """Register a capability handler under every capability it declares.
        Call before any dispatch().

        A handler serving more than one capability declares them in
        `capabilities`; without that the caller has to know which handlers are
        special and register those a different way, which is how the state
        handler ended up registering itself against a concrete broker.
        """
        for capability in getattr(handler, "capabilities", None) or (handler.capability,):
            self.register_for(capability, handler)

    def register_for(self, capability: Capability, handler: ICapabilityHandler) -> None:
        """Register `handler` under an explicit capability, for handlers that
        serve more than one (e.g. state get/set)."""
        self._handlers[capability] = handler
        logger.debug("Broker: registered handler for capability=%s", capability)

    # -- Dispatch -----------------------------------------------------------

    async def dispatch(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        """Full security pipeline → capability handler → result."""
        run_id = principal.run_id

        # 1. Max-calls enforcement.
        count = self._call_counts.get(run_id, 0) + 1
        if count > principal.grant.max_calls:
            logger.warning(
                "Broker: max_calls exceeded run=%s org=%s cap=%s target=%s limit=%d",
                run_id, principal.org_id, call.capability, call.target,
                principal.grant.max_calls,
            )
            return BrokerResult(
                success=False,
                error=f"Run {run_id} exceeded max_calls={principal.grant.max_calls}",
            )
        self._call_counts[run_id] = count

        # 2. Grant check (capability-level).
        deny_reason = self._check_grant(call, principal)
        if deny_reason:
            # org/user on every denial: without them a denial cannot be
            # attributed to a tenant, which is what makes a run of them
            # readable as an attack rather than one workflow misbehaving.
            logger.warning(
                "Broker: grant denied run=%s org=%s user=%s cap=%s target=%s reason=%s",
                run_id, principal.org_id, principal.user_id,
                call.capability, call.target, deny_reason,
            )
            return BrokerResult(success=False, error=deny_reason)

        # 2b. Narrow arguments the grant covers but the call left open.
        call = self._scope_call(call, principal)

        # 3. Dry run: capabilities that always mutate are simulated here.
        # TOOL is decided by ToolCapabilityHandler, which can read tool tags.
        if principal.is_dry_run and call.capability in _DRY_RUN_SIMULATED_CAPABILITIES:
            logger.info(
                "Dry run %s: simulating %s on %s", run_id, call.capability.value, call.target,
            )
            data: dict[str, Any] = {
                "dry_run": True,
                "skipped": f"{call.capability.value}:{call.target}",
            }
            if call.capability == Capability.AGENT_CREATE:
                # `Ctx.create_agent` reads `agent_id` off this result to build
                # the handle, and the `.run()` that follows has to clear the
                # grant check above -- otherwise a dry run of a
                # create-then-run workflow dies on a denial instead of
                # exercising the shape.
                data["agent_id"] = call.target
                self._created_agents.setdefault(run_id, set()).add(call.target)
            return BrokerResult(success=True, data=data)

        # 4. Taint check (only for TOOL capability).
        tool_name = normalize_tool_name(call.target) if call.capability == Capability.TOOL else None
        if tool_name is not None:
            taint = self._taint_states.setdefault(run_id, TaintState())
            block = taint.check_tool_call(
                tool_name, is_destructive=self._classify(tool_name).is_destructive,
            )
            if block:
                logger.warning(
                    "Broker: taint blocked run=%s org=%s user=%s tool=%s sources=%s",
                    run_id, principal.org_id, principal.user_id,
                    tool_name, block.get("taint_sources"),
                )
                return BrokerResult(success=False, error=block.get("fix_hint", "Taint check failed"))

        # 5. Find handler.
        handler = self._handlers.get(call.capability)
        if handler is None:
            logger.error(
                "Broker: no handler for cap=%s (run=%s org=%s) -- a wiring bug, "
                "not a workflow error", call.capability, run_id, principal.org_id,
            )
            return BrokerResult(
                success=False,
                error=f"No handler registered for capability={call.capability}",
            )

        # 6. Execute.
        try:
            result = await handler.handle(call, principal)
        except Exception as exc:
            logger.exception("Broker: handler raised for cap=%s target=%s", call.capability, call.target)
            return BrokerResult(success=False, error=str(exc))

        # 7. An agent minted by this run is runnable by it.
        # Its id cannot appear in `grant.agent_ids`, which is pinned from
        # source at commit time, so without this `ctx.create_agent(...)`
        # followed by `.run()` would always deny. `can_create_agents` is the
        # authority being exercised here; this only records the consequence.
        if call.capability == Capability.AGENT_CREATE and result.success:
            created_id = (result.data or {}).get("agent_id") if isinstance(result.data, dict) else None
            if created_id:
                self._created_agents.setdefault(run_id, set()).add(str(created_id))

        # 8. Update taint state after tool result.
        if tool_name is not None and result.success:
            taint = self._taint_states.setdefault(run_id, TaintState())
            taint.after_tool_result(
                tool_name, is_taint_source=self._classify(tool_name).is_external_read,
            )

        return result

    def _classify(self, tool_name: str) -> ToolClassification:
        """Read/write classification from the run's tool registry tags.

        The registry lives on the TOOL handler, so the broker asks it rather
        than holding a second reference. An unclassifiable tool is treated as
        every category at once -- the conservative reading, since the failure
        mode of guessing wrong the other way is an unapproved destructive call
        driven by injected content.
        """
        handler = self._handlers.get(Capability.TOOL)
        classify = getattr(handler, "classify", None)
        if classify is None:
            return _UNCLASSIFIED
        return classify(tool_name)

    # -- Grant checks -------------------------------------------------------

    def _check_grant(self, call: "BrokerCall", principal: "RunPrincipal") -> str | None:
        """Deny unless the grant names the target.

        An empty set is "granted nothing", not "granted everything". Reading it
        the other way made the absence of a restriction mean unrestricted
        access, so a workflow whose version predates tool pinning -- or whose
        grant computation silently produced nothing -- could reach every tool
        the run's registry resolved.
        """
        grant = principal.grant

        if call.capability == Capability.TOOL:
            normalized = normalize_tool_name(call.target)
            if call.target not in grant.tool_names and normalized not in grant.tool_names:
                return (
                    f"Tool '{call.target}' not in run grant.tool_names "
                    f"({', '.join(sorted(grant.tool_names)) or 'empty'}). "
                    "Re-commit the workflow so its tool pins are regenerated, or "
                    "add the tool to the workflow's declared tools."
                )

        elif call.capability == Capability.AGENT_RUN:
            minted = self._created_agents.get(principal.run_id, ())
            if call.target not in grant.agent_ids and call.target not in minted:
                return (
                    f"Agent '{call.target}' not in run grant.agent_ids "
                    f"({', '.join(sorted(grant.agent_ids)) or 'empty'})"
                )

        elif call.capability == Capability.AGENT_CREATE:
            if not grant.can_create_agents:
                return "Run grant does not allow agent creation"

        elif call.capability == Capability.KNOWLEDGE_SEARCH:
            # Only checked here; the narrowing that makes an *absent*
            # `collections` argument safe happens in `_scope_call`, because a
            # check alone cannot stop `collections=[]` reaching a handler that
            # reads it as "every collection in the org".
            requested = set(call.arguments.get("collections") or [])
            if grant.collection_ids and requested and not requested.issubset(grant.collection_ids):
                denied = requested - grant.collection_ids
                return f"Knowledge search denied for collections: {denied}"

        return None

    @staticmethod
    def _scope_call(call: "BrokerCall", principal: "RunPrincipal") -> "BrokerCall":
        """Narrow a call's arguments to what the grant actually covers.

        `ctx.search("q")` sends no `collections`, and the knowledge handler
        treats an empty list as "search everything in the org" -- so a workflow
        granted one collection could read them all just by omitting the
        argument. Substituting the grant makes the omission mean "everything I
        was granted" instead.
        """
        grant = principal.grant
        if call.capability != Capability.KNOWLEDGE_SEARCH or not grant.collection_ids:
            return call

        requested = set(call.arguments.get("collections") or [])
        effective = (requested & grant.collection_ids) if requested else set(grant.collection_ids)
        return call.model_copy(
            update={"arguments": {**call.arguments, "collections": sorted(effective)}},
        )


class ToolCapabilityHandler:
    """ICapabilityHandler for Capability.TOOL.

    Normalizes the tool name, checks the grant, resolves through the
    ToolRegistry, and executes with host-side credentials.
    """

    capability = Capability.TOOL

    def __init__(self, tool_registry: "ToolRegistry") -> None:
        self._registry = tool_registry

    def classify(self, tool_name: str) -> "ToolClassification":
        """Classify a tool from its registry tags, for the dry-run skip and the
        taint check. An untaggable tool is conservatively every category."""
        if self._registry is None:
            return _UNCLASSIFIED
        try:
            tags = self._registry.tags_for_name(tool_name)
        except Exception:
            return _UNCLASSIFIED
        categories = {t.value for t in tags if t.key == "category"}
        if not categories:
            return _UNCLASSIFIED
        return ToolClassification(
            is_write=bool(categories & _MUTATING_TAG_VALUES),
            is_external_read=bool(categories & _EXTERNAL_READ_TAG_VALUES),
            is_destructive=bool(categories & _DESTRUCTIVE_TAG_VALUES),
        )

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        if self._registry is None:
            return BrokerResult(
                success=False,
                error="No ToolRegistry available for this run (fallback broker). "
                      "This is a configuration error — ensure the per-run broker is supplied.",
            )

        # Normalize to Tool.name form.
        tool_name = normalize_tool_name(call.target)
        if tool_name is None:
            return BrokerResult(
                success=False,
                error=f"Unresolvable tool reference: {call.target!r}. "
                      "Use Tool.name form (e.g. 'jira__create_issue').",
            )

        # Resolve through the registry.
        if not self._registry.has(tool_name):
            available = sorted(self._registry.names())[:10]
            return BrokerResult(
                success=False,
                error=f"Tool '{tool_name}' not found in ToolRegistry. "
                      f"Available (first 10): {available}",
            )
        tool = self._registry.resolve_by_name(tool_name)

        if principal.is_dry_run and self.classify(tool_name).is_write:
            logger.info(
                "Dry run %s: skipping write tool %s", principal.run_id, tool_name,
            )
            return BrokerResult(
                success=True,
                data={
                    "dry_run": True,
                    "skipped": tool_name,
                    "note": "Write tool not executed because this is a dry run.",
                },
            )

        # Execute the tool. `Tool.__call__` validates/defaults `kwargs`
        # against `parameters` before delegating to `execute`.
        try:
            output = await tool(**call.arguments)
        except Exception as exc:
            logger.warning("ToolCapabilityHandler: tool %s raised: %s", tool_name, exc)
            return BrokerResult(success=False, error=str(exc))
        if not output.success:
            return BrokerResult(success=False, error=output.error or f"Tool {tool_name} failed")
        return BrokerResult(success=True, data=output.data)


class KnowledgeCapabilityHandler:
    """ICapabilityHandler for Capability.KNOWLEDGE_SEARCH.

    Uses the org's ToolRegistry-resident search tool.  The 'search' tool is
    always loaded by PipesHubToolLoader when the agent has knowledge sources.
    """

    capability = Capability.KNOWLEDGE_SEARCH

    def __init__(self, tool_registry: "ToolRegistry") -> None:
        self._registry = tool_registry

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        if self._registry is None:
            logger.warning(
                "KnowledgeCapabilityHandler: no ToolRegistry for run=%s org=%s",
                principal.run_id, principal.org_id,
            )
            return BrokerResult(
                success=False,
                error="No ToolRegistry available for knowledge search (fallback broker).",
            )
        search_tool = None
        for candidate in ("search", "knowledge_search"):
            if self._registry.has(candidate):
                search_tool = self._registry.resolve_by_name(candidate)
                break
        if search_tool is None:
            logger.warning(
                "KnowledgeCapabilityHandler: no search tool loaded for run=%s org=%s",
                principal.run_id, principal.org_id,
            )
            return BrokerResult(
                success=False,
                error="No knowledge search tool loaded.  Ensure collection_ids are set on the workflow.",
            )
        try:
            output = await search_tool(
                query=call.target,
                collections=call.arguments.get("collections", []),
                limit=call.arguments.get("limit", 10),
            )
        except Exception as exc:
            # Returned to the workflow as a failed result, so without this the
            # only trace of a broken search backend is inside the run's own
            # output.
            logger.warning(
                "KnowledgeCapabilityHandler: search raised for run=%s org=%s: %s",
                principal.run_id, principal.org_id, exc,
            )
            return BrokerResult(success=False, error=str(exc))
        if not output.success:
            return BrokerResult(success=False, error=output.error or "Knowledge search failed")
        return BrokerResult(success=True, data=output.data)


class ConversationEmitHandler:
    """ICapabilityHandler for Capability.CONVERSATION_EMIT.

    Posts a message back to the originating conversation via
    `IConversationWriter`. A workflow that was not created from chat has no
    conversation to emit into, so the emit is a logged no-op there -- reported
    as such in the result rather than as a bare success, since "delivered" and
    "there was nowhere to deliver it" are different outcomes to the workflow.
    """

    capability = Capability.CONVERSATION_EMIT

    def __init__(self, conversation_writer: "IConversationWriter | None" = None) -> None:
        self._writer = conversation_writer

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        message = call.arguments.get("message", "")
        kind = call.target  # "text", "code", "error", "card"
        if self._writer is None or not principal.conversation_id:
            logger.info(
                "ctx.emit not delivered (writer=%s, conversation_id=%s): run=%s kind=%s message=%s",
                self._writer is not None, principal.conversation_id,
                principal.run_id, kind, message[:120],
            )
            return BrokerResult(success=True, data={"delivered": False})
        try:
            await self._writer.write(
                run_id=principal.run_id,
                org_id=principal.org_id,
                user_id=principal.user_id,
                content=message,
                kind=kind,
                conversation_id=principal.conversation_id,
            )
            return BrokerResult(success=True, data={"delivered": True})
        except Exception as exc:
            logger.warning("ctx.emit failed: %s", exc)
            return BrokerResult(success=False, error=str(exc))


class StateCapabilityHandler:
    """ICapabilityHandler for Capability.STATE_GET and STATE_SET.

    One handler serves both, declared in `capabilities` so `broker.register`
    wires it exactly like every other handler.
    """

    capability = Capability.STATE_GET
    capabilities = (Capability.STATE_GET, Capability.STATE_SET)

    def __init__(self, state_store: "IWorkflowStateStore | None") -> None:
        self._store = state_store

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        if self._store is None:
            return BrokerResult(
                success=False,
                error="ctx.state is unavailable: no workflow state store is configured.",
            )
        if not principal.workflow_id:
            return BrokerResult(
                success=False,
                error="ctx.state is unavailable: this run has no workflow id to scope state to.",
            )
        try:
            if call.capability == Capability.STATE_GET:
                value = await self._store.get(
                    org_id=principal.org_id, workflow_id=principal.workflow_id, key=call.target,
                )
                return BrokerResult(success=True, data=value)
            await self._store.set(
                org_id=principal.org_id,
                workflow_id=principal.workflow_id,
                key=call.target,
                value=call.arguments.get("value"),
            )
            return BrokerResult(success=True)
        except Exception as exc:
            logger.warning("StateCapabilityHandler: %s on key %s failed: %s",
                           call.capability, call.target, exc)
            return BrokerResult(success=False, error=str(exc))


class AgentRunHandler:
    """ICapabilityHandler for Capability.AGENT_RUN.

    Runs an existing Agent Builder agent by id with a natural-language goal.

    Two independent checks, because neither covers the other: `_check_grant`
    requires the id to be in `grant.agent_ids` (pinned from the `ctx.agent()`
    literals in the version's source), and resolution here happens within
    `principal.org_id`. Org scoping alone would still let a workflow drive any
    agent its own tenant owns.
    """

    capability = Capability.AGENT_RUN

    def __init__(self, agent_runner: "IWorkflowAgentRunner | None" = None) -> None:
        self._runner = agent_runner

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        agent_id = call.target
        goal = call.arguments.get("goal", "")
        if self._runner is None:
            # Codegen no longer advertises ctx.agent()/ctx.create_agent() in the
            # SDK reference it hands the model (see codegen/agent.py), precisely
            # because this path always fails until an IWorkflowAgentRunner is
            # wired in container_wiring.py. A warning here still catches
            # hand-written or pre-existing workflow code that calls it anyway.
            logger.warning(
                "AgentRunHandler: ctx.agent(%r).run() called for run %s but no "
                "IWorkflowAgentRunner is configured on this instance -- the agent "
                "node is not implemented on this deployment.",
                agent_id, principal.run_id,
            )
            return BrokerResult(
                success=False,
                error=(
                    f"ctx.agent('{agent_id}').run() is unavailable: no agent runner is "
                    "configured on this instance."
                ),
            )
        if not goal:
            return BrokerResult(success=False, error="ctx.agent(...).run() requires a goal.")
        try:
            output = await self._runner.run(
                agent_id=agent_id,
                org_id=principal.org_id,
                user_id=principal.user_id,
                goal=goal,
                arguments=call.arguments,
            )
        except Exception as exc:
            logger.exception("AgentRunHandler: agent %s failed for run %s", agent_id, principal.run_id)
            return BrokerResult(success=False, error=str(exc))
        return BrokerResult(success=True, data=output)


class AgentCreateHandler:
    """ICapabilityHandler for Capability.AGENT_CREATE.

    Creates a new Agent Builder agent using `AgentProvisioningService`.
    The handler extracts the agent spec from the broker call arguments.
    """

    capability = Capability.AGENT_CREATE

    def __init__(self, provisioning_service: "IAgentProvisioning | None") -> None:
        self._provisioning = provisioning_service

    async def handle(self, call: "BrokerCall", principal: "RunPrincipal") -> BrokerResult:
        if self._provisioning is None:
            return BrokerResult(
                success=False,
                error="AgentProvisioningService is not wired — ctx.create_agent() unavailable.",
            )
        from app.services.agents.provisioning import AgentSpec
        args = call.arguments
        try:
            spec = AgentSpec(
                name=call.target,  # target = desired agent name
                description=args.get("description", ""),
                instructions=args.get("instructions", ""),
                system_prompt=args.get("system_prompt", ""),
                org_id=principal.org_id,
                created_by_user_id=principal.user_id,
                tool_names=args.get("tools") or [],
                collection_ids=args.get("knowledge") or [],
                skill_names=args.get("skills") or [],
                models=args.get("models") or [],
                tags=["workflow-created"],
            )
            agent_key = await self._provisioning.create(spec)
            return BrokerResult(success=True, data={"agent_id": agent_key, "name": spec.name})
        except Exception as exc:
            logger.exception("AgentCreateHandler: failed to create agent %s", call.target)
            return BrokerResult(success=False, error=str(exc))


def build_platform_broker(
    *,
    tool_registry: "ToolRegistry | None",
    conversation_writer: "IConversationWriter | None" = None,
    provisioning_service: "IAgentProvisioning | None" = None,
    state_store: "IWorkflowStateStore | None" = None,
    agent_runner: "IWorkflowAgentRunner | None" = None,
) -> PlatformBroker:
    """Convenience factory: create a PlatformBroker and register the standard
    set of capability handlers for a code workflow run.

    Every capability gets a handler even when its dependency is missing, so an
    unwired dependency surfaces as an explanatory ``BrokerResult(success=False)``
    in the workflow's own error rather than "No handler registered for
    capability=...".
    """
    broker = PlatformBroker()
    broker.register(ToolCapabilityHandler(tool_registry))  # type: ignore[arg-type]
    broker.register(KnowledgeCapabilityHandler(tool_registry))  # type: ignore[arg-type]
    broker.register(ConversationEmitHandler(conversation_writer))
    broker.register(AgentRunHandler(agent_runner))
    broker.register(AgentCreateHandler(provisioning_service))
    broker.register(StateCapabilityHandler(state_store))
    return broker
