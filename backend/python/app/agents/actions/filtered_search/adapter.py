"""`NativeFilterAdapter`: the one abstraction a new connector implements to
gain native-query filter search.

This is the unit of extension the whole design turns on — adding connector
N+1 means writing one adapter class (plus one thin tool method, see
`filtered_search/tools.py`) and registering it (see `registry.py`).

Each adapter accepts the connector's OWN query language (JQL, CQL, Slack
search operators) rather than a normalized filter spec — see the design
doc's "Why the fix is a language change, not a new field" for why a
normalized vocabulary was abandoned. Three responsibilities per adapter:

1. `validate_query` — deterministically reject free-text/content predicates
   (this subsystem's entire premise is "native = filters, PipesHub = content";
   a text predicate that reaches the native API silently returns weak
   full-text results instead of routing through PipesHub retrieval).
2. `substitute_identity` — rewrite a self-reference token (`currentUser()`,
   `me`) to the ASKING user's real connector identity. Never a no-op that
   lets the native API resolve it against the connector's own
   service-account/token identity — see `agent_loop/hooks/
   filter_value_resolution.py` for why that is a correctness bug, not a
   convenience.
3. `execute` — run the (validated, identity-substituted) query and return
   the candidate universe.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.actions.filtered_search.models import (
        CustomFieldDef,
        FilterCapabilityDescriptor,
        FilteredSearchUniverse,
    )


@dataclass(frozen=True)
class Pagination:
    limit: int = 50
    cursor: str | None = None


class NativeFilterAdapter(ABC):
    """Validates, identity-substitutes, and executes one connector's native
    query language — filters only, never a content/semantic search term."""

    @classmethod
    @abstractmethod
    def capabilities(cls) -> FilterCapabilityDescriptor:
        """Static capability declaration — must not depend on instance state."""
        ...

    @classmethod
    @abstractmethod
    def validate_query(cls, query: str) -> str | None:
        """Return an actionable error message if *query* contains a
        free-text/content predicate this adapter must reject under the
        filter-only contract (e.g. JQL/CQL `~` used as a text-match
        operator, a bare Slack search term with no `operator:value`
        shape) — naming the offending predicate and telling the caller to
        move that term into `content_query` instead. Returns `None` when
        *query* is filter-only and safe to execute.

        Pure and synchronous so it is trivially unit-testable without a
        client or network access.
        """
        ...

    @classmethod
    @abstractmethod
    def has_self_reference(cls, query: str) -> bool:
        """Whether *query* contains this connector's self-reference token
        (`currentUser()` for Jira/Confluence, `me`/`my` for Slack) —
        lets callers skip the identity graph lookup entirely for queries
        that don't need it."""
        ...

    @classmethod
    @abstractmethod
    def substitute_identity(cls, query: str, source_user_id: str) -> str:
        """Rewrite this connector's self-reference token(s) in *query* to
        *source_user_id* — the ASKING session user's real identity on this
        connector, resolved via `connector_context.resolve_self_identity`,
        never the connector's own service-account/token identity.

        A no-op (returns *query* unchanged) when `has_self_reference` is
        False for this query.
        """
        ...

    @abstractmethod
    async def execute(
        self, query: str, client: Any, page: Pagination  # noqa: ANN401
    ) -> FilteredSearchUniverse:
        """Run *query* (already validated and identity-substituted) against
        the native API and return the candidate universe."""
        ...

    async def discover_custom_fields(self, client: Any) -> list[CustomFieldDef]:  # noqa: ANN401
        """Optional: connector-specific fields not obvious from the query
        language alone (Jira custom fields, Confluence labels catalog).
        Only called when `capabilities().supports_custom_fields` is True."""
        raise NotImplementedError(f"{type(self).__name__} does not support custom field discovery")

    @classmethod
    async def build_datasource(
        cls, config_service: Any, connector_id: str, logger: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Build an authenticated DataSource for *connector_id* using the
        connector's org-level credentials (``/services/connectors/{id}/config``).

        Each adapter implements this using its connector family's
        ``Client.build_from_services`` → ``DataSource`` wrapping pattern,
        keeping connector-specific imports out of ``connector_context.py``.
        """
        raise NotImplementedError(f"{cls.__name__} does not implement build_datasource")


__all__ = ["NativeFilterAdapter", "Pagination"]
