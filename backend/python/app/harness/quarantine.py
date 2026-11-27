"""The quarantined summarizer of a PipesHub run (G-58): what ``knowledgegraph__summarize`` hands its content to.

Bound into the run's tool state once the run's model exists, on both paths: the native loop's and an engine's
(whose calls come through the Tool Gateway to the same tool). Its model is the run's own, metered on the run's
ledger account (P6.5) as the gateway meters an engine's calls, so every summary call is reserved, charged and
written to the cost ledger like any other call of the run. The org's classification is asked before anything is
sent, at the conversation's level joined with the records being summarized.

The tool is offered only where it is for: a run with a summarizer whose knowledge holds a source outsiders write
(``outsider_sources_in``). A run with no transport has no summarizer; a knowledge base of the organization's own
documents needs none, and its tool surface stays the one the legacy loop offers (DUR1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.harness.knowledge.provenance import source_label
from harness_core.context.quarantine import QuarantinedSummarizer
from harness_core.models.transport_model import TransportModel

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, MutableMapping, Sequence

    from app.harness.assembly.model import ResolvedModel
    from app.harness.classification import ModelGuard

SUMMARIZER_STATE_KEY: Final = "harness_quarantined_summarizer"


def quarantined_summarizer(
    model: ResolvedModel, *, model_id: str, guard: ModelGuard | None = None
) -> QuarantinedSummarizer | None:
    if model.transport is None:
        return None
    provider = model.provider

    def refusal(records: Sequence[str]) -> str | None:
        return guard.refusal(model_id, provider, reading=records) if guard is not None else None

    admits: Callable[[Sequence[str]], str | None] | None = refusal if guard is not None else None
    return QuarantinedSummarizer(TransportModel(model.transport, purpose="summary"), admits=admits)


def bind_summarizer(
    state: MutableMapping[str, object], model: ResolvedModel, *, model_id: str, guard: ModelGuard | None
) -> bool:
    """Put the run's summarizer in its tool state; ``False`` (and nothing bound) for a model with no transport."""
    summarizer = quarantined_summarizer(model, model_id=model_id, guard=guard)
    if summarizer is None:
        _ = state.pop(SUMMARIZER_STATE_KEY, None)
        return False
    state[SUMMARIZER_STATE_KEY] = summarizer
    return True


def summarizer_in(state: Mapping[str, object] | None) -> QuarantinedSummarizer | None:
    found = state.get(SUMMARIZER_STATE_KEY) if state is not None else None
    return found if isinstance(found, QuarantinedSummarizer) else None


def outsider_sources_in(state: Mapping[str, object] | None) -> tuple[str, ...]:
    """How a person names the outsider-written sources the run's knowledge holds, by connector type: the agent's
    attached knowledge, and on the chat route the connectors the person may read within the turn's filter."""
    if state is None or not state.get("has_knowledge"):
        return ()
    scope = {str(a) for a in _items(state.get("apps"))}
    types: list[object] = [item.get("type") for item in _dicts(state.get("agent_knowledge"))]
    types.extend(
        item.get("type") for item in _dicts(state.get("available_connectors")) if not scope or item.get("id") in scope
    )
    found: dict[str, None] = {}
    for kind in types:
        if isinstance(kind, str) and kind:
            label = source_label({"connector": kind.upper()})
            if label is not None:
                found[label] = None
    return tuple(found)


def offers_summarize(state: Mapping[str, object] | None) -> bool:
    return summarizer_in(state) is not None and bool(outsider_sources_in(state))


def _items(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple, set, frozenset)) else []  # pyright: ignore[reportUnknownArgumentType]


def _dicts(value: object) -> list[Mapping[str, object]]:
    return [item for item in _items(value) if isinstance(item, dict)]  # pyright: ignore[reportUnknownVariableType]


__all__ = [
    "SUMMARIZER_STATE_KEY",
    "bind_summarizer",
    "offers_summarize",
    "outsider_sources_in",
    "quarantined_summarizer",
    "summarizer_in",
]
