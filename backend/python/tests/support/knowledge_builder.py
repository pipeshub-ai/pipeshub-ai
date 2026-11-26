"""Backs one caller's ``KnowledgeContextBuilder`` with a fixture's blocks.

Prefetch and the search tool both build their context through
``app.modules.retrieval.context.builder``, whose storage seams are
``get_flattened_results`` and the graph enrichments. A driven run backs
each caller with its own fixture, so the seams are not replaced outright:
the caller's builder names its fixture in a context variable for the length
of its ``build``, and the seams read it there. Concurrent builds (parallel
tool calls) each see their own; anything else reaches the real function.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from app.modules.retrieval.context import builder as _builder

_BUILDER = "app.modules.retrieval.context.builder"
_FLATTEN: ContextVar[Any] = ContextVar("fixture_flatten", default=None)

_real_flatten = _builder.get_flattened_results
_real_enrichments = {
    name: getattr(_builder, name)
    for name in (
        "enrich_records_with_graph_context",
        "enrich_virtual_record_id_to_result_with_fk_children",
        "enrich_records_with_address_links",
    )
}


async def _flatten(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
    fake = _FLATTEN.get()
    return await (fake or _real_flatten)(*args, **kwargs)


def _skip_under_fixture(name: str) -> Any:  # noqa: ANN401
    real = _real_enrichments[name]

    async def enrich(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        if _FLATTEN.get() is None:
            await real(*args, **kwargs)

    return enrich


def builder_patches(target: str, flatten: Any) -> list[tuple[str, object]]:  # noqa: ANN401
    """Patches that make the ``KnowledgeContextBuilder`` imported at ``target`` read ``flatten``'s blocks."""

    class FixtureBuilder(_builder.KnowledgeContextBuilder):
        async def build(self, *args: Any, **kwargs: Any) -> _builder.KnowledgeContext:  # noqa: ANN401
            token = _FLATTEN.set(flatten)
            try:
                return await super().build(*args, **kwargs)
            finally:
                _FLATTEN.reset(token)

    return [
        (target, FixtureBuilder),
        (f"{_BUILDER}.get_flattened_results", _flatten),
        *((f"{_BUILDER}.{name}", _skip_under_fixture(name)) for name in _real_enrichments),
    ]


__all__ = ["builder_patches"]
