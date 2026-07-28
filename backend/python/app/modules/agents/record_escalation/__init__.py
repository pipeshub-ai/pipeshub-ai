"""
record_escalation — pure kernel for full-record escalation.

Public exports used by the retrieval tool and the gate hook.
"""

from app.modules.agents.record_escalation.coverage import analyze_coverage
from app.modules.agents.record_escalation.judge import IFetchJudge, LLMFetchJudge
from app.modules.agents.record_escalation.models import (
    FetchCandidate,
    FetchPlan,
    FetchVerdict,
)
from app.modules.agents.record_escalation.policy import (
    build_candidates,
    needs_whole_document,
    policy_text,
)
from app.modules.agents.record_escalation.renderer import render_candidate_table

__all__ = [
    "FetchCandidate",
    "FetchPlan",
    "FetchVerdict",
    "IFetchJudge",
    "LLMFetchJudge",
    "analyze_coverage",
    "build_candidates",
    "needs_whole_document",
    "policy_text",
    "render_candidate_table",
]
