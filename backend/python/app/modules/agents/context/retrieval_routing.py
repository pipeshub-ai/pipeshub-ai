"""Duplicate-connector disambiguation — the one catalog-specific routing
fact that doesn't belong in a tool schema (a schema can't see how many
connectors of the same type this request has).

Everything this module used to also state — "search all sources in
parallel when signals are ambiguous", "naming a source narrows where you
look, never means skip searching" — is cut, not moved verbatim: the first
is already `retrieval.search_internal_knowledge`'s own schema text ("Pass a
single connector_ids value per call and run one call per source in
parallel"), and the second is now the single, generalized precedence rule
in `PipesHubPromptBuilder`'s `finding_information` section, which covers
every tool surface, not just indexed connectors.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.agents.context.source_catalog import SourceCatalog


def build_routing_guidance(catalog: "SourceCatalog") -> str:
    """Return the duplicate-connector warning, or "" when there are none.

    Called from `SourceCatalog.render()` so the note sits directly under
    the source table it disambiguates — no separate section, no separate
    heading to keep in sync with removal of the sections above.
    """
    dups = catalog.duplicate_apps()
    if not dups:
        return ""

    dup_str = ", ".join(f"**{d}**" for d in sorted(dups))
    return (
        f"⚠️ Multiple connectors of the same type exist ({dup_str}). "
        "Use the source labels and any project/space signals from the query "
        "to identify the correct connector before routing."
    )
