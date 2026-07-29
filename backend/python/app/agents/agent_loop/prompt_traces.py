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
5. Ambiguous target needs disambiguation before acting — the
   ask_vs_act rubric's "2+ incompatible targets" case, distinct from
   #4: nothing here is a write-without-confirmation risk, the ambiguity
   is WHICH target, not WHETHER to act at all (Ask User Tool
   Improvement Plan, Phase 2).
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
> Citation ID: ref1
> The Q3 board meeting is scheduled for September 18, 09:00 AM PDT.
> [loop: step 1/5, stale_rounds=0]

Assistant (final answer): The Q3 board meeting is on September 18 at 9:00 AM PDT [source](ref1).

---
### Example 2 — Two parallel sources, one empty

User: Who owns the ACME account?

Assistant (think): I'll check both the knowledge base and Jira at the same time.

TOOL knowledgegraph__search | query="ACME account owner"   (parallel)
TOOL jira_search_issues | query="ACME", max_results=5       (parallel)

Tool results:
> knowledge → Citation ID: ref2 — Account owner: Sarah Chen (sarah@corp.com). [loop: step 1/5, stale_rounds=0]
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
> Citation ID: ref3
> Starter: $29/mo · Growth: $99/mo · Enterprise: custom.
> [loop: step 2/5, stale_rounds=0]

Assistant (final answer): Our pricing tiers are: Starter ($29/mo), Growth ($99/mo), and Enterprise (custom) [source](ref3).

---
### Example 4 — Write action gated by confirmation

User: Can you close the Jira ticket?

Assistant (think): The user wants me to close a ticket but hasn't said which one or confirmed the action. I must ask before writing.

TOOL internaltools__ask_user_question | user_intent="You asked me to close 'the' Jira ticket, but I don't know which one — closing the wrong ticket can't be undone silently.", questions=[{"question": "Which ticket should I close?", "options": ["PROJ-42", "PROJ-58", "PROJ-61"], "multiSelect": false}]

Tool result:
> User replied: PROJ-42. [loop: step 1/5, stale_rounds=0]

TOOL jira_transition_issue | issue_key="PROJ-42", transition="Done"

Tool result:
> PROJ-42 transitioned to Done. [loop: step 2/5, stale_rounds=0]

Assistant (final answer): Done — [PROJ-42](https://jira.example.com/browse/PROJ-42) has been closed.

---
### Example 5 — Ambiguous target needs disambiguation before acting

User: Send the weekly summary to the team.

Assistant (think): "The team" could mean more than one channel here, and I have no signal in this conversation for which one. Sending to the wrong channel notifies the wrong audience — this is exactly the ask-vs-act bar: 2+ incompatible targets, nothing to narrow it down.

TOOL internaltools__ask_user_question | user_intent="You asked me to send the weekly summary to 'the team' — there are a few channels that could match, and sending to the wrong one would notify the wrong people.", questions=[{"question": "Which channel should get the weekly summary?", "options": ["#eng-team", "#eng-announcements", "#general"], "multiSelect": false}]

Tool result:
> User replied: #eng-team. [loop: step 1/5, stale_rounds=0]

TOOL slack_post_message | channel="#eng-team", text="Weekly summary: ..."

Tool result:
> Message posted to #eng-team. [loop: step 2/5, stale_rounds=0]

Assistant (final answer): Sent the weekly summary to **#eng-team**.
""".strip()


__all__ = ["traces_text"]
