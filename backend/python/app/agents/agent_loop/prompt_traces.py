"""Tier-gated worked traces for small and mid-tier models.

These literal message sequences show the exact call format the agent
should follow for four common patterns.  They are injected for SMALL
and MID tiers only: frontier models already understand the format from
the function schemas; adding examples inflates their already-expensive
prompt for no benefit.

Patterns covered:
1. Single knowledge lookup — basic search → cite result.
2. Two parallel sources, one empty — parallelize, fill in from
   the one that returned data, report the other as unavailable.
3. Empty result then reformulation — recognise miss, widen query,
   try again before giving up.
4. Write request gated by ask_user_question — always confirm before
   any mutating action the user's message did not explicitly request.
"""
from __future__ import annotations


def traces_text() -> str:
    """Return the full worked-traces section string.

    Call only when the model tier warrants it (``model_profile.inject_traces()``).
    """
    return _WORKED_TRACES


_WORKED_TRACES = """
## Worked Examples

These examples show the exact pattern to follow for common request types.
Tool calls are shown as: TOOL tool_name | param=value

---
### Example 1 — Single knowledge lookup

User: When is the Q3 board meeting?

Assistant (think): I'll search the knowledge base for the meeting.

TOOL knowledgegraph__search | query="Q3 board meeting date"

Tool result:
> [12|ref1] The Q3 board meeting is scheduled for September 18, 09:00 AM PDT.
> [loop: step 1/5, stale_rounds=0]

Assistant (final answer): The Q3 board meeting is on September 18 at 9:00 AM PDT [source](ref1).

---
### Example 2 — Two parallel sources, one empty

User: Who owns the ACME account?

Assistant (think): I'll check both the knowledge base and Jira at the same time.

TOOL knowledgegraph__search | query="ACME account owner"   (parallel)
TOOL jira_search_issues | query="ACME", max_results=5       (parallel)

Tool results:
> knowledge → [4|ref2] Account owner: Sarah Chen (sarah@corp.com). [loop: step 1/5, stale_rounds=0]
> jira      → (no results). [loop: step 1/5, stale_rounds=0]

Assistant (final answer): The ACME account is owned by **Sarah Chen (sarah@corp.com)** [source](ref2).
(Jira returned no matching issues.)

---
### Example 3 — Empty result then query reformulation

User: What are our pricing tiers?

Assistant (think): I'll search for pricing information.

TOOL knowledgegraph__search | query="pricing tiers"

Tool result:
> (no results). [loop: step 1/5, stale_rounds=0]

Assistant (think): No results — the knowledge base may use different terms. I'll broaden the query.

TOOL knowledgegraph__search | query="plans subscription cost"

Tool result:
> [7|ref3] Starter: $29/mo · Growth: $99/mo · Enterprise: custom.
> [loop: step 2/5, stale_rounds=0]

Assistant (final answer): Our pricing tiers are: Starter ($29/mo), Growth ($99/mo), and Enterprise (custom) [source](ref3).

---
### Example 4 — Write action gated by confirmation

User: Can you close the Jira ticket?

Assistant (think): The user wants me to close a ticket but hasn't confirmed which one or whether to proceed. I must ask before writing.

TOOL internaltools__ask_user_question | question="Which Jira ticket should I close, and shall I proceed?", options=["Yes, close it","No, cancel"]

Tool result:
> User replied: Yes, close PROJ-42. [loop: step 1/5, stale_rounds=0]

TOOL jira_transition_issue | issue_key="PROJ-42", transition="Done"

Tool result:
> PROJ-42 transitioned to Done. [loop: step 2/5, stale_rounds=0]

Assistant (final answer): Done — [PROJ-42](https://jira.example.com/browse/PROJ-42) has been closed.
""".strip()


__all__ = ["traces_text"]
