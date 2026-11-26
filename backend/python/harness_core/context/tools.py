"""The tools band: sorted once, appended after (REPORT 6.3 band 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Loop slot holding the band's order (tool ids), journaled when it changes.
TOOL_ORDER_SLOT = "context.toolOrder"


def tool_band_order(previous: Sequence[str], granted: Iterable[str]) -> tuple[str, ...]:
    """``previous`` kept in place (minus tools no longer granted), newly granted tools
    appended sorted by id. A first compile (``previous`` empty) is fully sorted."""
    current = set(granted)
    kept = [tool_id for tool_id in dict.fromkeys(previous) if tool_id in current]
    known = set(kept)
    return (*kept, *sorted(current - known))


__all__ = ["TOOL_ORDER_SLOT", "tool_band_order"]
