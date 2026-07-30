"""
record_escalation — pure kernel for full-record escalation.

Public exports used by the retrieval tool and agent-loop prompts/candidate CTAs.
"""

from app.modules.agents.record_escalation.coverage import analyze_coverage
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
from app.modules.agents.record_escalation.renderer import (
    render_candidate_table,
    render_coverage_note,
)

__all__ = [
    "FetchCandidate",
    "FetchPlan",
    "FetchVerdict",
    "analyze_coverage",
    "build_candidates",
    "needs_whole_document",
    "policy_text",
    "render_candidate_table",
    "render_coverage_note",
]
