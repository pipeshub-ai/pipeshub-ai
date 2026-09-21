"""Article-level retrieval metrics.

Gold links are the articles FRAMES annotators used, not every page that could
answer, so every recall here is a lower bound — the same bound for every system.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence

from benchmarks.frames.models import StreamTrace


def recall(found: Collection[str], gold: Sequence[str]) -> float | None:
    if not gold:
        return None
    return sum(1 for url in gold if url in found) / len(gold)


def recall_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float | None:
    return recall(set(ranked[:k]), gold)


def reciprocal_rank(ranked: Sequence[str], gold: Sequence[str]) -> float | None:
    if not gold:
        return None
    wanted = set(gold)
    return next((1.0 / rank for rank, url in enumerate(ranked, 1) if url in wanted), 0.0)


def ndcg_at_k(ranked: Sequence[str], gold: Sequence[str], k: int) -> float | None:
    if not gold:
        return None
    wanted = set(gold)
    dcg = sum(1.0 / math.log2(rank + 1) for rank, url in enumerate(ranked[:k], 1) if url in wanted)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(wanted), k) + 1))
    return dcg / ideal if ideal else 0.0


def tool_counts(trace: StreamTrace | None) -> tuple[int, int, int, int]:
    """(tool calls, searches, fetches, failed calls) over the whole trace."""
    if trace is None:
        return 0, 0, 0, 0
    calls = trace.tool_calls
    searches = sum(1 for c in calls if "search" in c.name)
    fetches = sum(1 for c in calls if "fetch" in c.name)
    failed = sum(1 for c in calls if (c.status or "").lower() in {"error", "failed", "failure"})
    return len(calls), searches, fetches, failed
