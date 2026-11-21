"""Counts taken before and after an upgrade: a count that fell, or vanished, lost data."""

from __future__ import annotations


def diff(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """One message per key that dropped or is missing in `after`. Growth is fine."""
    out = []
    for key, was in sorted(before.items()):
        if key not in after:
            out.append(f"{key}: was {was}, now missing")
        elif after[key] < was:
            out.append(f"{key}: was {was}, now {after[key]}")
    return out
