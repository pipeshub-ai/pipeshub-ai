"""Fuse each search deeper, then cut its hits by record (``FUSION_RECORD_CUT``).

Every vector backend fuses dense and sparse by point and sizes each leg from
the requested limit, so the cut at ``limit`` falls on points: a record that one
leg alone found (a strong keyword match the dense leg never saw) lands just
past it, while a long record holds many of the slots. With the switch on, a
search asks the backend for ``depth`` times the limit (never past the
over-fetch cap) and keeps ``limit`` hits spread across records:
the best half with at most ``per_record`` points from any one record, then the
best point of each record not yet represented, then rank order.

A high-accuracy feature: ``FUSION_RECORD_CUT`` decides when set, otherwise
``HIGH_ACCURACY_MODE`` does. Off, a search is asked for ``limit`` and cut by
point as before. The tuning knobs are read once at import, like the other
search knobs; the switch is read per search.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.utils.env_config import env_float, env_int
from app.utils.env_utils import accuracy_feature_enabled

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Sequence

FUSION_RECORD_CUT_ENV_VAR = "FUSION_RECORD_CUT"
FUSION_DEPTH_ENV_VAR = "SEARCH_FUSION_DEPTH"
NEW_RECORD_SHARE_ENV_VAR = "SEARCH_NEW_RECORD_SHARE"
PER_RECORD_FLOOR_ENV_VAR = "SEARCH_PER_RECORD_FLOOR"
PER_RECORD_DIVISOR_ENV_VAR = "SEARCH_PER_RECORD_DIVISOR"

# Past ten the over-fetch cap binds for any limit above 30 anyway, and a
# smaller limit gains nothing from fusing hundreds of points.
MAX_FUSION_DEPTH = 10
# Bounds the per-record knobs: past the over-fetch cap a larger value changes nothing.
MAX_PER_RECORD = 300

_logger = logging.getLogger(__name__)


def cut_by_record(
    results: Sequence[Any],
    limit: int,
    *,
    per_record: int,
    reserve: int,
    record_of: Callable[[Any], Hashable | None],
) -> list:
    """Keep ``limit`` of ``results`` (best first), spread across records.

    The best ``limit - reserve`` results come first, at most ``per_record``
    from any one record. The reserve then goes to the best result of each
    record not yet represented, and anything left is filled in rank order, so
    a search confined to a few records still returns ``limit``. The kept
    results stay in their original order.

    A result whose record is unknown counts as a record of its own. A
    ``limit`` below 1 keeps everything, as ``merge_collection_results`` does.
    """
    if limit < 1 or len(results) <= limit:
        return list(results)
    per_record = max(per_record, 1)
    reserve = min(max(reserve, 0), limit)
    keys: list[Hashable] = [
        key if key is not None else ("unkeyed", index)
        for index, key in enumerate(record_of(result) for result in results)
    ]
    chosen: set[int] = set()
    per_key: dict[Hashable, int] = {}

    def _take(index: int) -> None:
        chosen.add(index)
        per_key[keys[index]] = per_key.get(keys[index], 0) + 1

    head = limit - reserve
    for index, key in enumerate(keys):
        if len(chosen) >= head:
            break
        if per_key.get(key, 0) < per_record:
            _take(index)
    for index, key in enumerate(keys):
        if len(chosen) >= limit:
            break
        if key not in per_key:
            _take(index)
    for index in range(len(results)):
        if len(chosen) >= limit:
            break
        if index not in chosen:
            _take(index)
    return [results[index] for index in sorted(chosen)]


@dataclass(frozen=True)
class RecordCut:
    """How deep a search fuses and how its hits are cut back by record."""

    depth: int = 3
    new_record_share: float = 0.5
    per_record_floor: int = 4
    per_record_divisor: int = 6

    def fused_limit(self, limit: int, cap: int) -> int:
        """What to ask the backend for: ``depth`` times ``limit``, within ``cap``.

        The cap bounds the extra depth, never the caller's own ``limit``.
        """
        return max(limit, min(limit * self.depth, cap))

    def per_record(self, limit: int) -> int:
        """Points one record may hold in the head of a cut to ``limit``."""
        return max(self.per_record_floor, limit // self.per_record_divisor)

    def cut(
        self, results: Sequence[Any], limit: int, record_of: Callable[[Any], Hashable | None]
    ) -> list:
        return cut_by_record(
            results,
            limit,
            per_record=self.per_record(limit),
            reserve=int(limit * self.new_record_share),
            record_of=record_of,
        )


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    return max(low, min(high, env_int(name, default)))


def record_cut_from_env() -> RecordCut:
    """The tuning knobs, clamped; a malformed value keeps the default."""
    defaults = RecordCut()
    return RecordCut(
        depth=_bounded_int(FUSION_DEPTH_ENV_VAR, defaults.depth, 1, MAX_FUSION_DEPTH),
        new_record_share=env_float(
            NEW_RECORD_SHARE_ENV_VAR, defaults.new_record_share, low=0.0, high=1.0
        ),
        per_record_floor=_bounded_int(
            PER_RECORD_FLOOR_ENV_VAR, defaults.per_record_floor, 1, MAX_PER_RECORD
        ),
        per_record_divisor=_bounded_int(
            PER_RECORD_DIVISOR_ENV_VAR, defaults.per_record_divisor, 1, MAX_PER_RECORD
        ),
    )


_SETTINGS = record_cut_from_env()


@dataclass
class _LoggedState:
    enabled: bool | None = None


_logged = _LoggedState()


def active_record_cut() -> RecordCut | None:
    """The cut a search should apply, or None to cut by point as before.

    Logs the state on the first search and whenever it changes, so an operator
    can tell from the log which cut a deployment runs.
    """
    enabled = accuracy_feature_enabled(FUSION_RECORD_CUT_ENV_VAR)
    if enabled is not _logged.enabled:
        _logged.enabled = enabled
        _logger.info(
            "Search record cut %s (%s)",
            "on" if enabled else "off",
            _SETTINGS if enabled else "hits cut by point",
        )
    return _SETTINGS if enabled else None


def point_record_id(point: object) -> str | None:
    """The record a vector point belongs to, or None when its payload lacks one."""
    payload = getattr(point, "payload", None) or {}
    return (payload.get("metadata") or {}).get("virtualRecordId")


def hit_record_id(hit: dict[str, Any]) -> str | None:
    """The record a formatted search hit belongs to."""
    return (hit.get("metadata") or {}).get("virtualRecordId")
