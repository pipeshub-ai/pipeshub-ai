"""`FilterAdapterRegistry`: declarative connector_type -> adapter mapping,
mirroring the existing `ConnectorFactory`/`ClientFactoryRegistry` pattern
elsewhere in the codebase.

Adapters register themselves once at import time (see
`filtered_search/adapters/__init__.py`); nothing else in the filter-search
subsystem branches on connector type — every lookup goes through here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agents.actions.filtered_search.adapter import NativeFilterAdapter


def _normalize(connector_type: str) -> str:
    """Uppercase + collapse spaces/underscores so `Connectors.JIRA_DATA_CENTER`
    ("JIRA DATA CENTER") and a registration key of "JIRA_DATA_CENTER" resolve
    to the same adapter regardless of which separator style the caller uses."""
    return connector_type.upper().replace(" ", "_")


class FilterAdapterRegistry:
    _adapters: dict[str, type[NativeFilterAdapter]] = {}

    @classmethod
    def register(cls, connector_type: str, adapter_cls: type[NativeFilterAdapter]) -> None:
        cls._adapters[_normalize(connector_type)] = adapter_cls

    @classmethod
    def get(cls, connector_type: str) -> type[NativeFilterAdapter] | None:
        return cls._adapters.get(_normalize(connector_type))

    @classmethod
    def is_registered(cls, connector_type: str) -> bool:
        return _normalize(connector_type) in cls._adapters

    @classmethod
    def all_connector_types(cls) -> list[str]:
        return list(cls._adapters.keys())

    @classmethod
    def reset(cls) -> None:
        """Test-only: clear registrations."""
        cls._adapters.clear()


__all__ = ["FilterAdapterRegistry"]
