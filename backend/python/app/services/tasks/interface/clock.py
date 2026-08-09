"""`IClock` -- the one seam that lets tests control "now" without patching
`datetime.now` globally, and lets production code stay trivially UTC-correct.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone


class IClock(ABC):
    @abstractmethod
    def now(self) -> datetime:
        """Current time, timezone-aware, UTC."""
        ...


class SystemClock(IClock):
    """Production implementation -- wall-clock UTC time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(IClock):
    """Test implementation -- returns a fixed (or externally advanced)
    instant. Never used in production code paths."""

    def __init__(self, initial: datetime) -> None:
        self._current = initial

    def now(self) -> datetime:
        return self._current

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._current = self._current + timedelta(seconds=seconds)

    def set(self, value: datetime) -> None:
        self._current = value
