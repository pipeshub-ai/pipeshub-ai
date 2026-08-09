"""Replay utilities for code workflow determinism checking."""
from __future__ import annotations


class ReplayDivergence(Exception):
    """The workflow function's call sequence diverged from the journal on resume.

    Canonical home for this exception -- `sdk/context.py`'s `Ctx` re-exports
    it rather than defining its own copy, since callers raising/catching it
    shouldn't have to care whether they got it from the SDK or the runtime."""
