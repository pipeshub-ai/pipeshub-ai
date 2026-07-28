"""
Candidate-list renderer for record escalation.

Produces the text appended to the retrieval tool result whenever a fetch could
add something. The held/total counts (with percentage) are the load-bearing
part: without them the model can see WHICH blocks it got but has no way to tell
whether it holds 4 of 87 or 87 of 87, so it cannot judge whether reading
further would add anything. The percentage makes the coverage gap viscerally
obvious for smaller models that do not infer it from raw fractions.

No I/O, no LLM.
"""

from __future__ import annotations

from app.modules.agents.record_escalation.models import FetchPlan

_LOW_COVERAGE_THRESHOLD = 30  # percent — below this, use a stronger header


def render_candidate_table(
    plan: FetchPlan,
    tool_ref: str = "knowledgegraph__fetch_record",
    *,
    needs_whole_document: bool = False,
) -> str:
    """
    Render the inline candidate list appended to the retrieval tool result.

    Identity, topics, held/total counts, and coverage percentage sit side by
    side so the model can apply a two-step filter:
      (1) Is this record relevant to the question? If not, skip it.
      (2) Do the blocks held answer the question, or does the question need the
          full document? If full content is needed and coverage is low, fetch.

    `needs_whole_document` selects the header framing. It does not decide
    whether the list appears — that turns on whether any record is incomplete.

    Returns "" when the plan has no candidates.
    """
    if not plan.has_candidates:
        return ""

    # Compute per-candidate percentages for header decision and per-row display.
    pcts = [
        round(100 * c.blocks_held / c.blocks_total) if c.blocks_total else 100
        for c in plan.candidates
    ]
    min_pct = min(pcts)

    if needs_whole_document:
        header = "Records you can read in full — this request needs whole-document content:"
    elif min_pct < _LOW_COVERAGE_THRESHOLD:
        header = (
            "Your coverage of these records is partial — read in full before answering "
            "if the question needs more than the specific passages already visible:"
        )
    else:
        header = "Records you can read in full if this request needs more than the blocks above:"

    lines = ["\n\n" + header]
    for i, (c, pct) in enumerate(zip(plan.candidates, pcts), 1):
        topics_str = ", ".join(c.topics) if c.topics else "—"
        held_str = f"you have {c.blocks_held} of {c.blocks_total} blocks ({pct}%)"
        name_part = c.record_name or "(unnamed)"
        lines.append(
            f"{i}. Record ID: {c.record_id} | {name_part} | {held_str} | Topics: {topics_str}"
        )

    lines.append(
        f"\nFor each record above, decide: (1) is it relevant to the question? "
        f"If not, skip it. (2) Is a specific fact you need already visible in the "
        f"blocks, or does answering require knowing what the document says as a "
        f"whole — a summary, review, comparison, risk assessment, complete listing? "
        f"If full content is needed and coverage is low, call `{tool_ref}` ONCE "
        f"with only the relevant record_ids, then answer."
    )
    return "\n".join(lines)
