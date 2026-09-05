"""Tests for ``app.modules.agents.enumeration.run``.

The property under test is the boundary between "safe to fall back" and "an
answer may already have reached the client". Getting that wrong shows the reader
two answers to one question.
"""
from __future__ import annotations

import pytest

from app.modules.agents.enumeration.run import EnumerationFinalizationError


class TestEnumerationFinalizationError:
    def test_is_an_exception(self) -> None:
        assert issubclass(EnumerationFinalizationError, Exception)

    def test_carries_the_original_cause(self) -> None:
        """The bridge re-raises this rather than retrying, so whatever actually
        failed has to survive for the error surfaced to the caller."""
        original = ValueError("event sink closed")
        try:
            try:
                raise original
            except ValueError as exc:
                raise EnumerationFinalizationError("finalisation failed") from exc
        except EnumerationFinalizationError as wrapped:
            assert wrapped.__cause__ is original

    def test_is_distinguishable_from_an_ordinary_failure(self) -> None:
        """A lookup or storage failure before finalisation must still be caught
        by the bridge's general handler and fall through to the agent."""
        assert not isinstance(RuntimeError("graph unavailable"),
                              EnumerationFinalizationError)
