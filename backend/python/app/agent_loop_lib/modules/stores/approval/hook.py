from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.modules.stores.approval.base import (
    DEFAULT_APPROVAL_POLICIES,
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalStore,
    RiskLevel,
)


class ApprovalHook:
    """
    Pre-tool hook that enforces approval policies.

    Integrate in Agent.run() before tool execution:
        approved = await approval_hook.check(call, session_id)
    """

    def __init__(
        self,
        store: ApprovalStore,
        hil_store: Any | None = None,
        tool_registry: Any | None = None,
    ) -> None:
        self._store = store
        self._hil_store = hil_store
        self._tool_registry = tool_registry

    async def check(
        self,
        call: ToolCall,
        session_id: str | None = None,
    ) -> ApprovalDecision:
        """
        Evaluate whether a tool call is approved.

        Returns an ApprovalDecision. `.approved` tells the caller whether
        to proceed. Raises nothing — all outcomes are encoded in the decision.
        """
        from app.agent_loop_lib.modules.stores.approval.base import RiskLevel

        # 1. Determine risk level from tool registry
        risk = RiskLevel.LOW
        if self._tool_registry is not None:
            try:
                tool = self._tool_registry.resolve_by_name(call.name)
                risk = tool.risk_level
            except Exception:
                pass  # unknown tool → LOW risk

        # 2. Resolve effective policy (explicit override or default)
        explicit_policy = await self._store.get_policy(call.name)
        policy = explicit_policy or DEFAULT_APPROVAL_POLICIES[risk]

        # 3. Apply policy
        if policy == ApprovalPolicy.AUTO_APPROVE:
            return await self._record(call.name, session_id, risk, policy, True)

        if policy == ApprovalPolicy.AUTO_DENY:
            return await self._record(call.name, session_id, risk, policy, False)

        if policy == ApprovalPolicy.ASK_ONCE:
            # Check session cache first
            if session_id is not None:
                cached = await self._store.get_session_decision(call.name, session_id)
                if cached is not None:
                    return cached  # reuse existing decision

        # ASK_EACH_TIME or ASK_ONCE with no cached decision → ask HIL and
        # actually wait for the human's answer before deciding (task engine
        # plan Part D1/D2: the previous version submitted the HIL request
        # then immediately recorded+returned `approved=False` without ever
        # calling `wait_for_response` — a "submit-then-deny" bug that
        # silently denied every ask-required tool call, indistinguishable
        # from a real human rejection).
        if self._hil_store is not None:
            import asyncio

            from app.agent_loop_lib.modules.stores.hil.base import (
                DEFAULT_HIL_RESPONSE_TIMEOUT_SECONDS,
                HILRequest,
                HILRequestType,
            )
            req = HILRequest(
                request_type=HILRequestType.TOOL_APPROVAL,
                run_id=call.id,
                session_id=session_id,
                question=f"Approve tool call '{call.name}'?",
                context={"arguments": call.arguments, "risk_level": risk.value},
            )
            request_id = await self._hil_store.submit(req)
            try:
                hil_response = await self._hil_store.wait_for_response(
                    request_id, timeout=DEFAULT_HIL_RESPONSE_TIMEOUT_SECONDS
                )
            except (TimeoutError, asyncio.TimeoutError):
                # Task engine plan Part D2 ("TTL on pending questions"): fail
                # closed on a genuine timeout, same as an explicit human
                # denial would — distinct from the submit-then-deny bug this
                # replaced, since a real (bounded) wait for an answer DID
                # happen here.
                return await self._record(
                    call.name, session_id, risk, policy, False,
                    reason=f"hil_request_id={request_id} timed out waiting for approval",
                )
            return await self._record(
                call.name, session_id, risk, policy, hil_response.approved,
                reason=f"hil_request_id={request_id}",
            )

        # No HIL store configured — this policy genuinely cannot be
        # resolved. Silently falling back to either outcome would hide a
        # real misconfiguration (e.g. a CRITICAL-risk tool call proceeding
        # with no human ever asked); fail loudly instead so the caller
        # notices `ApprovalHook` was wired without a `hil_store`.
        raise RuntimeError(
            f"ApprovalHook: tool call {call.name!r} requires human approval "
            f"(policy={policy.value}, risk={risk.value}) but no hil_store is "
            "configured to ask one."
        )

    async def _record(
        self,
        tool_name: str,
        session_id: str | None,
        risk: "RiskLevel",
        policy: ApprovalPolicy,
        approved: bool,
        reason: str | None = None,
    ) -> ApprovalDecision:
        decision = ApprovalDecision(
            tool_name=tool_name,
            session_id=session_id,
            risk_level=risk,
            policy=policy,
            approved=approved,
            reason=reason,
        )
        await self._store.record_decision(decision)
        return decision
