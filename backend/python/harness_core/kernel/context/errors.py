"""Errors that stop a model call because its context cannot be made valid.

The PRE_MODEL pipeline records and swallows ordinary middleware errors (a
broken shaper must not end the run), so these are the one family it lets
through: sending a request that is over the model's window, or that has lost
the user's question, gives a wrong answer or a provider error instead.
"""

from __future__ import annotations


class ContextError(Exception):
    """The context for this model call cannot be shaped into a valid request."""


class ContextBudgetExceeded(ContextError):
    """Every shaper ran and the context still does not fit the model's window."""

    def __init__(self, token_count: int, budget: int) -> None:
        self.token_count = token_count
        self.budget = budget
        super().__init__(
            f"Context ({token_count} tokens) exceeds budget ({budget} tokens) "
            "after all compaction attempts"
        )
