"""Rerank a search's records before the record cut, and make every later
step follow the new order.

The search's points (or hits) are grouped by record in their fused order;
the first ``pool`` records go to the strategy with a title and a short text.
The strategy's first ``top`` known records lead, the rest keep the fused
order behind them, and the fused first ``keep_top`` records stay within
``top``. The points are then reordered record by record and given the
search's own scores again, in the new order: the record cut, the context
ranking, citation numbering and the fetch candidates all rank by score, so
they follow without knowing a reranker ran.

Only records already in the given points are ranked and nothing is added.
A record is offered to the strategy only when the caller can name it for
this user (``RecordTexts.titles``), so the strategy never reads a record the
user may not. Titles and summaries are read first, under their own bound, so
the strategy's timeout measures the strategy alone. Any failure, and either
timeout, keep the points exactly as they were.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType
from app.modules.retrieval.record_rerank.base import RecordCandidate, RerankRequest
from app.modules.retrieval.record_rerank.settings import (
    RecordRerankSettings,
    record_rerank_enabled,
    record_rerank_settings,
)
from app.utils.env_utils import env_bool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Hashable, Sequence

    from app.modules.retrieval.record_rerank.base import RecordReranker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecordRerank:
    """A search's request to rerank its records for ``question``."""

    question: str
    settings: RecordRerankSettings
    anchor: str = ""
    """The user's question behind an agent's own search: the strategy reads
    it beside the search query, and its dense records join the pool."""


RECORD_RERANK_TOOL_SEARCHES_ENV_VAR = "RECORD_RERANK_TOOL_SEARCHES"


def record_rerank_for(question: str, anchor: str = "") -> RecordRerank | None:
    """The rerank a search for ``question`` should run, or None when the
    switch is off. With an ``anchor`` that differs from the question, the
    strategy is given both."""
    if not question or not question.strip() or not record_rerank_enabled():
        return None
    anchor = (anchor or "").strip()
    if anchor and anchor != question.strip():
        return RecordRerank(
            question=f"{anchor}\n(Search query: {question.strip()})",
            settings=record_rerank_settings(),
            anchor=anchor,
        )
    return RecordRerank(question=question, settings=record_rerank_settings())


def search_record_rerank(question: str) -> dict[str, Any]:
    """``search_with_filters`` keyword arguments for this search's rerank."""
    rerank = record_rerank_for(question)
    return {"record_rerank": rerank} if rerank is not None else {}


def tool_search_record_rerank(query: str, user_question: str | None) -> dict[str, Any]:
    """``search_with_filters`` keyword arguments for an agent's own search:
    nothing unless RECORD_RERANK_TOOL_SEARCHES is "true", then the rerank of
    ``query`` anchored on the user's question."""
    if not env_bool(RECORD_RERANK_TOOL_SEARCHES_ENV_VAR, False):
        return {}
    rerank = record_rerank_for(query, anchor=user_question or "")
    return {"record_rerank": rerank} if rerank is not None else {}


@dataclass(frozen=True)
class ItemView:
    """How to read a vector point or a formatted hit."""

    record_of: Callable[[Any], str | None]
    content_of: Callable[[Any], str]
    metadata_of: Callable[[Any], dict[str, Any]]
    score_of: Callable[[Any], float]
    with_score: Callable[[Any, float], Any]


def _point_metadata(point: Any) -> dict[str, Any]:  # noqa: ANN401 - SearchResult or a look-alike
    return (getattr(point, "payload", None) or {}).get("metadata") or {}


def _hit_metadata(hit: dict[str, Any]) -> dict[str, Any]:
    return hit.get("metadata") or {}


def _text(value: Any) -> str:  # noqa: ANN401 - payload content
    return value if isinstance(value, str) else ""


POINTS = ItemView(
    record_of=lambda point: _point_metadata(point).get("virtualRecordId"),
    content_of=lambda point: _text((getattr(point, "payload", None) or {}).get("page_content")),
    metadata_of=_point_metadata,
    score_of=lambda point: float(getattr(point, "score", 0.0) or 0.0),
    with_score=lambda point, score: replace(point, score=score),
)

HITS = ItemView(
    record_of=lambda hit: _hit_metadata(hit).get("virtualRecordId"),
    content_of=lambda hit: _text(hit.get("content")),
    metadata_of=_hit_metadata,
    score_of=lambda hit: float(hit.get("score") or 0.0),
    with_score=lambda hit, score: {**hit, "score": score},
)


def is_summary(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("isRecordSummary")) or metadata.get("blockType") == BlockType.RECORD_SUMMARY.value


@dataclass
class PoolRecord:
    record_id: str
    passage: str = ""
    """The record's best-ranked passage that is not its summary."""
    summary: str = ""
    """The record's summary, when the search matched it."""


def _by_score(items: Sequence[Any], view: ItemView) -> list[Any]:
    return sorted(items, key=lambda item: -view.score_of(item))


def record_keys(items: Sequence[Any], view: ItemView) -> list[Hashable]:
    """Records by their best item, best first. An item without a record
    counts as a record of its own, as the record cut counts it."""
    keys: list[Hashable] = []
    for index, item in enumerate(_by_score(items, view)):
        key = view.record_of(item)
        keys.append(key if key else ("unkeyed", index))
    return list(dict.fromkeys(keys))


def record_pool(items: Sequence[Any], view: ItemView, size: int) -> list[PoolRecord]:
    """The first ``size`` records, with the passage and summary the search
    matched for each."""
    pool: dict[str, PoolRecord] = {}
    for item in _by_score(items, view):
        record_id = view.record_of(item)
        if not record_id:
            continue
        record = pool.get(record_id)
        if record is None:
            if len(pool) >= size:
                continue
            record = pool[record_id] = PoolRecord(record_id=record_id)
        content = view.content_of(item)
        if is_summary(view.metadata_of(item)):
            record.summary = record.summary or content
        elif not record.passage and content.strip():
            record.passage = content
    return list(pool.values())


def display_title(name: str | None) -> str:
    """A record name as the reranker reads it: word separators as spaces."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", name or "")).strip()


def candidate_text(record: PoolRecord, summary: str, chars: int) -> str:
    """The text shown beside a record's title: its summary, else its best
    passage, on one line and capped."""
    return " ".join((summary or record.passage).split())[:chars]


def placed_records(ranked: Sequence[str], *, candidates: Sequence[str], top: int) -> list[str]:
    """The strategy's first ``top`` candidates, ignoring ids it invented or repeated."""
    offered = set(candidates)
    return list(dict.fromkeys(record_id for record_id in ranked if record_id in offered))[:top]


def final_record_order(
    current: Sequence[Hashable],
    ranked: Sequence[str],
    *,
    candidates: Sequence[str],
    top: int,
    keep_top: int,
) -> list[Hashable]:
    """``current`` with the strategy's first ``top`` candidates in front.

    Ids the strategy invented or repeated are ignored; whatever it left out
    keeps its current place behind them. Each of the current first
    ``keep_top`` records the strategy did not place takes one of the last
    slots of ``top``, in current order, and the strategy's last picks move
    down behind them.
    """
    head = placed_records(ranked, candidates=candidates, top=top)
    placed = set(head)
    protected = [key for key in current[: min(keep_top, top)] if key not in placed]
    kept = head[: top - len(protected)]
    return list(dict.fromkeys([*kept, *protected, *head[len(kept):], *current]))


def _descending(scores: list[float]) -> list[float]:
    """``scores`` sorted best first, ties broken downwards so that every
    later sort by score keeps the given order."""
    ordered = sorted(scores, reverse=True)
    for index in range(1, len(ordered)):
        if ordered[index] >= ordered[index - 1]:
            ordered[index] = math.nextafter(ordered[index - 1], -math.inf)
    return ordered


def reorder_items(items: Sequence[Any], order: Sequence[Hashable], view: ItemView) -> list[Any]:
    """``items`` grouped by record in ``order`` (each record's items keep
    their order) and rescored with the same scores, best first."""
    groups: dict[Hashable, list[Any]] = {}
    for index, item in enumerate(_by_score(items, view)):
        key = view.record_of(item) or ("unkeyed", index)
        groups.setdefault(key, []).append(item)
    position = {key: rank for rank, key in enumerate(order)}
    keys = sorted(groups, key=lambda key: position.get(key, len(position)))
    sequence = [item for key in keys for item in groups[key]]
    if all(a is b for a, b in zip(sequence, items)):
        return list(items)
    scores = _descending([view.score_of(item) for item in items])
    return [view.with_score(item, score) for item, score in zip(sequence, scores)]


@dataclass(frozen=True)
class RecordOrder:
    """What a rerank made of a search's records."""

    records: list[Hashable]
    """Every record, in the new order."""
    placed: frozenset[str]
    """The records the strategy put in its top."""


@dataclass(frozen=True)
class RecordTexts:
    """Where the stage reads what the points do not carry."""

    titles: Callable[[list[str]], Awaitable[dict[str, str]]]
    """Names by virtual record id of the records this user may read; a
    record it leaves out is not offered to the strategy."""
    summaries: Callable[[list[str]], Awaitable[dict[str, str]]]
    """Record summaries by virtual record id."""


async def _candidates(
    pool: list[PoolRecord], texts: RecordTexts, settings: RecordRerankSettings,
) -> list[RecordCandidate]:
    titles = await texts.titles([record.record_id for record in pool])
    readable = [record for record in pool if record.record_id in titles]
    missing = [record.record_id for record in readable if not record.summary]
    summaries = await texts.summaries(missing) if missing else {}
    return [
        RecordCandidate(
            record_id=record.record_id,
            title=display_title(titles[record.record_id]),
            text=candidate_text(
                record, record.summary or summaries.get(record.record_id, ""), settings.text_chars,
            ),
        )
        for record in readable
    ]


async def rerank_order(
    rerank: RecordRerank,
    items: Sequence[Any],
    view: ItemView,
    *,
    reranker: RecordReranker | None,
    texts: RecordTexts,
    org_id: str,
) -> RecordOrder | None:
    """Every record of ``items`` in the reranked order, or None to keep the
    search order: nothing to rerank, or the strategy failed or ran out of
    time (logged once, here)."""
    settings = rerank.settings
    pool = record_pool(items, view, settings.pool)
    if reranker is None or len(pool) < 2:
        return None
    started = time.monotonic()
    try:
        candidates = await asyncio.wait_for(_candidates(pool, texts, settings), timeout=settings.lookup_timeout_s)
    except TimeoutError:
        logger.warning(
            "record_rerank: reading titles and summaries took longer than %.1fs; keeping the search order",
            settings.lookup_timeout_s,
        )
        return None
    except Exception as exc:
        logger.warning("record_rerank: reading titles and summaries failed, keeping the search order: %r", exc)
        return None
    if len(candidates) < 2:
        return None
    looked_up = time.monotonic()
    request = RerankRequest(question=rerank.question, candidates=candidates, top=settings.top, org_id=org_id)
    try:
        ranked = await asyncio.wait_for(reranker.rank(request), timeout=settings.timeout_s)
    except TimeoutError:
        logger.warning(
            "record_rerank: %s took longer than %.1fs; keeping the search order", reranker.name, settings.timeout_s,
        )
        return None
    except Exception as exc:
        logger.warning("record_rerank: %s failed, keeping the search order: %r", reranker.name, exc)
        return None
    done = time.monotonic()
    current = record_keys(items, view)
    offered = [candidate.record_id for candidate in candidates]
    order = final_record_order(current, ranked, candidates=offered, top=settings.top, keep_top=settings.keep_top)
    placed = placed_records(ranked, candidates=offered, top=settings.top)
    logger.info(
        "record_rerank strategy=%s pool=%d placed=%d new_in_top=%d ms=%d lookup_ms=%d rank_ms=%d",
        reranker.name, len(candidates), len(placed),
        len(set(order[: settings.top]) - set(current[: settings.top])),
        int((done - started) * 1000), int((looked_up - started) * 1000), int((done - looked_up) * 1000),
    )
    return RecordOrder(records=order, placed=frozenset(placed))
