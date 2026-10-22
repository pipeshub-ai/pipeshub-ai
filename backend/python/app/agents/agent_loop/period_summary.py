"""Steer a question about a span of time to ``knowledgegraph__search_timeline``.

"What came up between March and May", "how did the migration evolve", "what
was decided over 2026": these need material from every part of the span, and
a plain search returns the top matches overall, which bunch around one moment.
When the timeline tool is granted and the question reads like one of these,
a PRE_AGENT middleware adds a short turn-scoped prompt section pointing at the
tool. The model still chooses its tools; the section only says which one fits.

Detection is a few English patterns over the user's own words, with no model
call: a named span ("between March and May", "since Q2", "over the past few
months", "over 2026"), a question about change over time ("how did X evolve",
"what has been happening"), or a recap verb next to a time cue ("what problems
came up ... in September"). It misses questions with no time words at all;
the tool's own description covers those.

The section is turn-volatile (``extra_sections``), so the cached prompt prefix
is the same whether or not it fires. Enterprise-search chat also skips its
upfront search for such a question (``timeline_replaces_prefetch``), so the
model does not answer from that one bunched result.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.agents.actions.knowledge_graph.ops.timeline import (
    TIMELINE_TOOL_NAME,
    timeline_summaries_enabled,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import AgentLifecycleContext
    from app.agents.agent_loop.context import AgentContext

PERIOD_SUMMARY_SECTION = "period_summary"

PERIOD_SUMMARY_HINT = (
    "## Questions About a Span of Time\n"
    "This question asks what happened over a span of time. Gather it with "
    f"`{TIMELINE_TOOL_NAME}`: pass the span the question names as start/end (omit "
    "both when it names none), and keep to the channel, project or ticket it names. "
    "Then answer period by period in date order, citing each period's own blocks. "
    "Search further for any period the timeline leaves thin."
)

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_YEAR = r"(?:19|20)\d{2}"
# A point in time that is neither a month nor a season: "Q2", "2025", "last quarter".
_PLAIN_POINT = rf"(?:q[1-4](?:\s+{_YEAR})?|{_YEAR}|last (?:week|month|quarter|year))"
_SEASON = r"the (?:summer|spring|autumn|fall|winter)"
# "last March", "this March"; never "may", which is as often a verb.
_NEAR_MONTH = rf"(?:last|this)\s+(?!may\b){_MONTH}"
_POINT_PREFIX = r"(?:(?:early|mid|late|the end of|the start of)[- ]?\s*)?"
# One end of a span: "March", "mid-May 2026", "March 1", "2026-03-01", "Q2",
# "last March", "the summer". Only used after between/from/since, which make
# a month name a date.
_DAY_YEAR = rf"(?:\s+\d{{1,2}}(?:st|nd|rd|th)?\b)?(?:,?\s+{_YEAR})?"
_POINT = (
    rf"{_POINT_PREFIX}(?:\d{{4}}-\d{{2}}-\d{{2}}|{_NEAR_MONTH}{_DAY_YEAR}"
    rf"|{_MONTH}{_DAY_YEAR}|{_PLAIN_POINT}|{_SEASON})\b"
)
# A month name only where it is a date: after a preposition, with a year, or
# with an ordinal day. "so we may 1) fix" is not one.
_DATED_MONTH = (
    rf"(?:(?:in|on|during|since|until|by|from|before|after|through|over|throughout|early|mid|late)"
    rf"[- ]+(?:the\s+)?{_MONTH}|{_NEAR_MONTH}|{_MONTH}\s+(?:{_YEAR}|\d{{1,2}}(?:st|nd|rd|th)\b))"
)
_STRETCH = (
    r"the\s+(?:past|last)\s+(?:few\s+|several\s+|couple of\s+|\d+\s+|two\s+|three\s+|six\s+)?"
    r"(?:days|weeks|months|quarters|years)"
)

# Enough on their own: the question names a span or asks how something changed.
_SPAN_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    rf"\bbetween\s+{_POINT}\s+(?:and|to)\s+{_POINT}",
    rf"\bfrom\s+{_POINT}\s+(?:to|until|till|through)\s+{_POINT}",
    rf"\b(?:since|over|during|throughout)\s+(?:{_STRETCH}|(?:all of\s+)?{_YEAR}"
    r"|the (?:year|quarter|month|summer|spring|autumn|fall|winter))\b",
    r"\bacross\s+(?:our|the|all(?:\s+(?:of\s+)?(?:our|the))?)?\s*(?:recent|past|last)\b",
    r"\bover\s+time\b",
    r"\btime\s?line\s+(?:of|for)\b|\bchronolog\w*|\bhistory of\b|\bblow[- ]by[- ]blow\b",
    r"\bhow\s+(?:has|have|did|do)\b[^?.]{0,80}?\b(?:evolve[ds]?|progress(?:ed)?|develop(?:ed)?"
    r"|unfold(?:ed)?|play(?:ed)? out|change[ds]? over)\b",
    r"\bwhat\s+(?:has\s+)?(?:been happening|was going on|has been going on|went on)\b",
))

# A recap, or a question about what came up, counts only next to a time cue:
# "what problems came up" alone is as often about one document.
_RECAP_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\b(?:recap|summari[sz]e|summary of|catch me up|walk me through|run me through|overview of)\b",
    r"\bwhat\s+(?:happened|has happened)\b",
    r"\bwhat\s+(?:problems|issues|concerns|gaps|blockers|risks|decisions|changes|updates|complaints)\b"
    r"[^?.]{0,60}?\b(?:came up|come up|were raised|got raised|were made|happened|were there"
    r"|have there been|did we (?:hit|see|get))\b",
    r"\bwhat\s+(?:was|were|has been|have been)\s+(?:decided|agreed|discussed|committed)\b",
    r"\bwhat\s+did\s+we\s+(?:commit|agree|promise|decide|tell|send|quote)\b",
    r"\bwhere\s+(?:are|did|do|have)\s+we\s+(?:stand|end(?:ed)? up|got(?:ten)? to|with)\b",
    r"\b(?:went|gone|go) quiet\b",
))
_TIME_CUE = re.compile(
    # No seasons here: "the fall migration", "the spring launch". They count
    # after since/over/during/throughout (_SPAN_PATTERNS) and in a span.
    rf"\b(?:{_DATED_MONTH}|{_PLAIN_POINT}|since\s+(?:then|{_POINT})|recent(?:ly)?|lately|so far"
    r"|this (?:year|quarter|month)|past (?:few )?(?:weeks|months|year))\b",
    re.IGNORECASE,
)


def asks_for_period_summary(query: str | None) -> bool:
    """Whether ``query`` asks what happened over a span of time."""
    if not query or not query.strip():
        return False
    if any(pattern.search(query) for pattern in _SPAN_PATTERNS):
        return True
    return any(pattern.search(query) for pattern in _RECAP_PATTERNS) and bool(_TIME_CUE.search(query))


def timeline_replaces_prefetch(query: str | None) -> bool:
    """Whether enterprise-search chat skips its one upfront search for this
    question. That search is the top matches overall, bunched around one
    moment; handed to the model as the context to answer from, it would be the
    summary the timeline exists to replace. The model searches with the
    timeline tool instead, pointed there by the hint section."""
    return timeline_summaries_enabled() and asks_for_period_summary(query)


def _timeline_granted(scope: Any) -> bool:  # noqa: ANN401
    registry = scope.runtime.tool_registry
    if registry is None or not registry.has(TIMELINE_TOOL_NAME):
        return False
    names = scope.spec.tool_names
    return not names or TIMELINE_TOOL_NAME in names


def period_summary_hint(context: AgentContext) -> Any:  # noqa: ANN401
    """PRE_AGENT middleware: the hint section for the top-level run of a
    question about a span, when that run can call the timeline tool."""

    async def _middleware(ctx: AgentLifecycleContext, next_fn: Any) -> None:  # noqa: ANN401
        scope = ctx.scope
        if (
            scope is not None
            and not scope.identity.parent_run_id
            and asks_for_period_summary(context.query)
            and _timeline_granted(scope)
        ):
            scope.extra_prompt_sections[PERIOD_SUMMARY_SECTION] = PERIOD_SUMMARY_HINT
        await next_fn()

    return _middleware


__all__ = [
    "PERIOD_SUMMARY_HINT",
    "PERIOD_SUMMARY_SECTION",
    "asks_for_period_summary",
    "period_summary_hint",
    "timeline_replaces_prefetch",
]
