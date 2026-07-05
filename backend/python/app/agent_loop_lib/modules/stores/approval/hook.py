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

        # ASK_EACH_TIME or ASK_ONCE with no cached decision → ask HIL
        if self._hil_store is not None:
            from app.agent_loop_lib.modules.stores.hil.base import (
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
            await self._hil_store.submit(req)
            # For now, submit and return a pending-approval decision
            # (Agent.resume() will pick up the HIL response later)
            decision = await self._record(
                call.name, session_id, risk, policy, False,
                reason=f"hil_request_id={req.request_id}",
            )
            return decision

        # No HIL store → auto-approve as fallback
        return await self._record(call.name, session_id, risk, policy, True)

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
