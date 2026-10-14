"""Which part of a request a model call belongs to, for usage accounting.

A request's model calls come from more than its main agent: sub-agents,
one-turn helper agents (intent, goal), context compaction, plan and result
critics, planners. A transport that reports usage (see PipesHub's
`UsageReportingTransport`) labels each call with the source set here. The
label is a context variable, so a sub-agent or tool running in its own task
inherits its caller's label until it sets its own.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

MAIN_SOURCE = "main"

_source: ContextVar[str] = ContextVar("llm_usage_source", default=MAIN_SOURCE)


@contextmanager
def llm_usage_source(label: str) -> Iterator[None]:
    """Label the model calls made inside this block."""
    token = _source.set(label)
    try:
        yield
    finally:
        _source.reset(token)


def current_llm_usage_source() -> str:
    return _source.get()


__all__ = ["MAIN_SOURCE", "current_llm_usage_source", "llm_usage_source"]
