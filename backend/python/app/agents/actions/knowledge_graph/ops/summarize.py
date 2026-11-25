"""knowledgegraph__summarize: a knowledge search the agent never reads, summarized in quarantine (G-58).

The search is the agent's own (``KnowledgeService`` as the run's principal and pinned scope, so the same ACL), and
it fills the run's citation state as a search does: the records it read are journaled with the call, so the turn's
label names them, a source outsiders write (Slack, email, tickets, the web) makes the outcome ``external``, and the
flow rule and the classification levels apply to what follows exactly as after a search. Only what the search
rendered differs: it goes to the run's quarantined summarizer (``app.harness.quarantine``), a model with no tools,
and the agent is shown that model's structured summary, quoted as untrusted data.

A search that rendered no record content (no results, or an error) is answered as the search answered it.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Final, cast

from harness_core.budget.errors import BudgetRefused, BudgetUnavailable
from harness_core.context.quarantine import (
    QuarantineFailed,
    QuarantineRefused,
    render_summary,
)

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from app.agent_loop_lib.core.messages import Part

SUMMARIZE_TOOL_NAME: Final = "knowledgegraph__summarize"
logger = logging.getLogger(__name__)


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


def _no_content(output: str) -> bool:
    """A search answer that holds no record content: its JSON form with no results."""
    try:
        parsed: object = json.loads(output)
    except ValueError:
        return False
    if not isinstance(parsed, dict):
        return False
    answer = cast("dict[str, object]", parsed)
    return answer.get("results") == [] and not answer.get("result_count")


def _text_of(output: str | list[Part]) -> str:
    if isinstance(output, str):
        return output
    return "\n\n".join(text for part in output if isinstance(text := getattr(part, "text", None), str))


def _read_records(state: MutableMapping[str, Any]) -> list[str]:
    """Every record the run's citation state holds: at least those this search rendered."""
    records: object = state.get("virtual_record_id_to_result")
    if not isinstance(records, dict):
        return []
    found: list[str] = []
    for vrid, record in cast("dict[str, object]", records).items():
        entry = cast("dict[str, object]", record) if isinstance(record, dict) else {}
        found.append(str(entry.get("id") or entry.get("_key") or vrid))
    return found


async def execute_summarize(
    state: MutableMapping[str, Any],
    *,
    query: str,
    focus: str | None = None,
    source_ids: list[str] | None = None,
    modified_after: str | None = None,
    modified_before: str | None = None,
) -> str:
    from app.harness.knowledge.knowledge_service import (
        KnowledgeCall,
        KnowledgeService,
        SearchRequest,
        backends_of,
    )
    from app.harness.quarantine import summarizer_in
    from app.modules.retrieval.context.targeted import SHOWN_BLOCKS_STATE_KEY

    summarizer = summarizer_in(state)
    if summarizer is None:
        return _error("Summarizing in isolation is not available in this chat. Use knowledgegraph__search.")
    if not (query or "").strip():
        return _error("Name what to search for in query.")
    shown: object = state.get(SHOWN_BLOCKS_STATE_KEY)
    shown_before = set(cast("set[object]", shown)) if isinstance(shown, set) else None
    output = await KnowledgeService(backends_of(state)).search(
        KnowledgeCall.from_state(state),  # pyright: ignore[reportArgumentType]
        SearchRequest(
            query=query,
            source_ids=source_ids,
            modified_after=modified_after,
            modified_before=modified_before,
            response_format="detailed",
        ),
    )
    # The agent was shown none of these passages: a later search of its own shows them again.
    if shown_before is None:
        state.pop(SHOWN_BLOCKS_STATE_KEY, None)
    else:
        state[SHOWN_BLOCKS_STATE_KEY] = shown_before
    text = _text_of(output)
    if not text.strip() or _no_content(text):
        return text
    try:
        summary = await summarizer.summarize([text], request=focus or query, sources=_read_records(state))
    except QuarantineRefused as refused:
        return _error(f"{refused} Nothing was summarized.")
    except (BudgetRefused, BudgetUnavailable):
        return _error("The run's budget does not cover summarizing these results. Nothing was summarized.")
    except QuarantineFailed as failed:
        logger.warning("knowledgegraph__summarize: no usable summary (%s)", failed)
        return _error("The summary could not be made, so nothing from the results is shown. Try a narrower query.")
    return render_summary(summary, read=f'the knowledge search for "{query}"')


__all__ = ["SUMMARIZE_TOOL_NAME", "execute_summarize"]
