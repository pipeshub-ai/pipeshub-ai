from __future__ import annotations

from app.agent_loop_lib.modules.stores.approval.base import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalStore,
)


class InMemoryApprovalStore(ApprovalStore):
    def __init__(self) -> None:
        self._policies: dict[str, ApprovalPolicy] = {}
        self._decisions: list[ApprovalDecision] = []

    async def get_policy(self, tool_name: str) -> ApprovalPolicy | None:
        return self._policies.get(tool_name)

    async def set_policy(self, tool_name: str, policy: ApprovalPolicy) -> None:
        self._policies[tool_name] = policy

    async def record_decision(self, decision: ApprovalDecision) -> None:
        self._decisions.append(decision)

    async def get_session_decision(
        self, tool_name: str, session_id: str
    ) -> ApprovalDecision | None:
        # Return the most recent matching decision
        for d in reversed(self._decisions):
            if d.tool_name == tool_name and d.session_id == session_id:
                return d
        return None
