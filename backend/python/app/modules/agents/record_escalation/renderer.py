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


def _coverage_pct(blocks_held: int, blocks_total: int) -> int | None:
    """Percentage of the record held, or None when the total is unknown.

    `analyze_coverage` reports 0 for a record whose block structure it could
    not read. Rendering that as a percentage produced "you have 2 of 0 blocks
    (100%)" — an unknown total presented to the model as full coverage, which
    is the one reading that guarantees it will not fetch.
    """
    if blocks_total <= 0:
        return None
    return round(100 * blocks_held / blocks_total)


def _min_coverage_pct(plan: FetchPlan) -> int:
    """Lowest coverage across candidates, counting an unknown total as 0 so it
    lands on the incomplete-coverage path rather than the optional one."""
    pcts = [_coverage_pct(c.blocks_held, c.blocks_total) for c in plan.candidates]
    return min(0 if pct is None else pct for pct in pcts)


def render_coverage_note(plan: FetchPlan, *, needs_whole_document: bool = False) -> str:
    """
    One-line coverage-gap flag meant to sit at the TOP of a retrieval
    result, before the record blocks — `render_candidate_table` (the full
    table, with the call-to-action and every record row) lives at the
    bottom, potentially thousands of tokens away on a long result. By the
    time the model reaches that footer it may already be composing an
    answer from the blocks alone. This note exists purely to put the
    coverage gap in front of the model early; it never repeats the record
    rows or the call-to-action, both of which stay in the footer table.

    Returns "" when the plan has no candidates, or when coverage is high
    enough (>= `_LOW_COVERAGE_THRESHOLD`) and the request was not flagged
    as needing whole-document content — the same conditions under which
    `render_candidate_table` keeps its own framing neutral/optional.
    """
    if not plan.has_candidates:
        return ""
    min_pct = _min_coverage_pct(plan)
    if not needs_whole_document and min_pct >= _LOW_COVERAGE_THRESHOLD:
        return ""
    return (
        f"Note: your coverage of these records is incomplete (as low as "
        f"{min_pct}%) — see the candidate list at the end before answering "
        f"anything that needs more than the specific passages shown above.\n\n"
    )


def render_candidate_table(
    plan: FetchPlan,
    tool_ref: str = "knowledgegraph__fetch_record",
    *,
    needs_whole_document: bool = False,
) -> str:
    """
    Render the inline candidate list appended to the retrieval tool result.

    Identity, topics, held/total counts, and coverage percentage sit side by
    side so the model can tell whether reading further would add anything.

    For the two paths where fetching is the default — whole-document
    requests and low-coverage records — the call-to-action leads BEFORE the
    record rows rather than trailing after them: a footer at the bottom
    competes with the blocks the model just read for attention, and by the
    time it gets there it may already be composing an answer from what it
    has. The high-coverage path keeps the instruction after the rows, since
    there fetching is genuinely optional and the rows are what the model
    needs to notice first.

    `needs_whole_document` also drops the relevance re-check the other two
    paths ask for: when the intent system already decided this request
    needs whole-document content, asking "is it relevant" again only gives
    the model another point at which to opt out.

    Returns "" when the plan has no candidates.
    """
    if not plan.has_candidates:
        return ""

    min_pct = _min_coverage_pct(plan)

    row_lines: list[str] = []
    for i, c in enumerate(plan.candidates, 1):
        topics_str = ", ".join(c.topics) if c.topics else "—"
        pct = _coverage_pct(c.blocks_held, c.blocks_total)
        held_str = (
            f"you have {c.blocks_held} of {c.blocks_total} blocks ({pct}%)"
            if pct is not None
            else f"you have {c.blocks_held} block(s); document length unknown"
        )
        name_part = c.record_name or "(unnamed)"
        row_lines.append(
            f"{i}. Record ID: {c.record_id} | {name_part} | {held_str} | Topics: {topics_str}"
        )
    rows = "\n".join(row_lines)

    if needs_whole_document:
        cta = (
            f"Call `{tool_ref}` ONCE with the record_ids below BEFORE answering "
            f"— this question needs whole-document content and your coverage "
            f"is incomplete (as low as {min_pct}%). Answering from the held "
            f"blocks alone will miss content the question requires:"
        )
        return f"\n\n{cta}\n{rows}"

    if min_pct < _LOW_COVERAGE_THRESHOLD:
        header = f"Coverage is incomplete (as low as {min_pct}%) for these records:"
        footer = (
            f"\nDrop any record above that isn't relevant to the question, "
            f"then call `{tool_ref}` ONCE with every record_id that is left "
            f"and answer from what it returns — unless the answer needs only "
            f"a single specific fact already visible in the blocks above."
        )
        return "\n\n" + header + "\n" + rows + "\n" + footer

    header = "Records you can read in full if this request needs more than the blocks above:"
    footer = (
        f"\nFor each record above, decide: (1) is it relevant to the question? "
        f"If not, skip it. (2) Is a specific fact you need already visible in "
        f"the blocks, or does answering require knowing what the document "
        f"says as a whole? If full content is needed, call `{tool_ref}` "
        f"ONCE with only the relevant record_ids, then answer."
    )
    return "\n\n" + header + "\n" + rows + "\n" + footer
