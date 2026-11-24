"""A compiled ``regex`` pattern that records the ``timeout`` of every match call, so a test can check the time budget a
search was given instead of timing the search on the wall clock."""

from __future__ import annotations

from typing import Any


class RecordingPattern:
    """Delegates to *pattern*; ``timeouts`` holds the ``timeout`` argument of each call (``None`` when absent)."""

    def __init__(self, pattern: Any) -> None:  # noqa: ANN401 - a regex.Pattern
        self._pattern = pattern
        self.timeouts: list[float | None] = []

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._pattern, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            self.timeouts.append(kwargs.get("timeout"))
            return attr(*args, **kwargs)

        return call
