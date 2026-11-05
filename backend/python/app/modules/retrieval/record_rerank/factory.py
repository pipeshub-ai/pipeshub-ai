"""Strategy registry: the only place a strategy name is looked up.

A strategy module registers its class with ``@register_record_reranker``;
the package imports the built-in ones. Adding a strategy is a new module,
nothing in the search path changes.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.modules.retrieval.record_rerank.base import RecordReranker, RerankerDeps

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, Callable[[RerankerDeps], RecordReranker]] = {}
_warned_unknown: set[str] = set()


def register_record_reranker(
    name: str,
) -> Callable[[type[RecordReranker]], type[RecordReranker]]:
    def register(cls: type[RecordReranker]) -> type[RecordReranker]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return register


def registered_strategies() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def create_record_reranker(name: str, deps: RerankerDeps) -> RecordReranker | None:
    """The strategy registered as ``name``, or None (warned once) when there
    is none, which leaves the fused order in place."""
    builder = _REGISTRY.get(name)
    if builder is None:
        if name not in _warned_unknown:
            _warned_unknown.add(name)
            logger.warning(
                "record_rerank: unknown strategy %r (registered: %s); keeping the search order",
                name, ", ".join(registered_strategies()),
            )
        return None
    return builder(deps)
