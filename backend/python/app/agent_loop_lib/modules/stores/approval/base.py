from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from enum import Enum

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"


class ApprovalPolicy(str, Enum):
    AUTO_APPROVE  = "auto_approve"
    AUTO_DENY     = "auto_deny"
    ASK_ONCE      = "ask_once"
    ASK_EACH_TIME = "ask_each_time"


# Default policy per risk level
DEFAULT_APPROVAL_POLICIES: dict[RiskLevel, ApprovalPolicy] = {
    RiskLevel.LOW:      ApprovalPolicy.AUTO_APPROVE,
    RiskLevel.MEDIUM:   ApprovalPolicy.ASK_ONCE,
    RiskLevel.HIGH:     ApprovalPolicy.ASK_EACH_TIME,
    RiskLevel.CRITICAL: ApprovalPolicy.AUTO_DENY,
}


class ApprovalDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    session_id: str | None = None
    risk_level: RiskLevel
    policy: ApprovalPolicy
    approved: bool
    reason: str | None = None


class ApprovalStore(ABC):
    """Stores per-tool policies and session-scoped approval decisions."""

    @abstractmethod
    async def get_policy(self, tool_name: str) -> ApprovalPolicy | None:
        """Return the explicitly set policy for a tool, or None to use default."""
        ...

    @abstractmethod
    async def set_policy(self, tool_name: str, policy: ApprovalPolicy) -> None:
        """Override the policy for a specific tool."""
        ...

    @abstractmethod
    async def record_decision(self, decision: ApprovalDecision) -> None:
        """Persist an approval decision for possible cache lookup."""
        ...

    @abstractmethod
    async def get_session_decision(
        self, tool_name: str, session_id: str
    ) -> ApprovalDecision | None:
        """Return the last recorded decision for this tool in this session, or None."""
        ...
