"""Completeness pass (``ANSWER_COMPLETENESS_PASS``): the final draft goes back once to fill what it left out.

Off unless the switch is "true", whatever ``HIGH_ACCURACY_MODE`` says. Answers drop specifics the model was shown:
one step of a procedure, the identifier in a check, one of the artifacts a list names, the condition on a threshold.
A rule in the composition text asking to keep them did not help (it competes with the rules that keep answers
scoped); a separate check against the records, after the draft is written, does. The request asks for the complete
answer only, so nothing has to be stripped from the reply.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.agents.agent_loop.answer_rules import answer_scope_guard_enabled
from app.agents.agent_loop.coverage.opening import concedes_absence
from app.utils.citations import cited_record_ids
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Mapping

ANSWER_COMPLETENESS_PASS_ENV_VAR = "ANSWER_COMPLETENESS_PASS"

# A record link the model wrote out in full rather than as a `refN` marker.
_RECORD_LINK_RE = re.compile(r"\]\((?:https?://[^)\s]*)?/record/[0-9A-Za-z_-]+")

REQUEST = (
    "Before you finalize, check your answer for completeness against the records it relies on. Go through each "
    "part of the question and, for each of those records, the specifics it gives about that part: steps and their "
    "order, conditions and thresholds, figures, names and identifiers (fields, files, commands, endpoints, labels), "
    "owners, dates, and what a thing is for or what it caused. Add each one your answer does not state yet, in the "
    "place it belongs, in the record's own words and cited like the rest of the answer.\n"
    "Do not add items a record gives for another purpose or another part than the one asked about, values of other "
    "versions beyond those your answer already labels, sentences about what a record does not say, or background "
    "the question does not ask for. Keep every fact, citation and the format of your answer; change nothing else. "
    "If nothing is missing, give the answer unchanged.\n"
    "Reply with the complete final answer only, with nothing before or after it."
)


# ANSWER_SCOPE_GUARD: the pass merged a record read after the draft into the answer's requirements (another tier's
# ramp schedule, a secondary policy's approval gates), and the answer then read as contradicting itself.
_SCOPE_GUARD_CLAUSE = (
    " Take added specifics only from records about the item and scope the question asks about (the same customer, "
    "tier, product, version line or process). A record about another scope gives at most one attributed sentence: "
    "do not merge its steps, approvals, thresholds or schedules into the answer. Where a record gives a different "
    "value for an item the answer already states, keep the answer's value unless that record covers the same scope "
    "and says it corrects, updates or replaces the record the answer cites; then give its value and name the cited "
    "one as the earlier value. Otherwise name the other in one line as what it is (another name for it, drift in an "
    "implementation, an earlier value, or one that applies only in a narrower case), not as a second requirement."
)


def completeness_pass_enabled() -> bool:
    return env_bool(ANSWER_COMPLETENESS_PASS_ENV_VAR, False)


def should_check(draft: str, ref_to_url: Mapping[str, str] | None) -> bool:
    """A draft that relies on at least one record and does not say the answer was not found."""
    if not (draft or "").strip() or concedes_absence(draft):
        return False
    return bool(cited_record_ids(draft, ref_to_url)) or _RECORD_LINK_RE.search(draft) is not None


def render_request() -> str:
    return REQUEST + _SCOPE_GUARD_CLAUSE if answer_scope_guard_enabled() else REQUEST
