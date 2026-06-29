"""Collection naming abstraction.

Provides a stable, future-ready naming convention for vector DB collections.
Current state: single collection type "records", no tenant separation.
Future state: per-connector/per-type collections and per-tenant namespacing
can be enabled by extending CollectionType and enabling the tenant prefix
without touching any call-site code.
"""

from enum import Enum
from typing import Optional


class CollectionType(Enum):
    """Logical collection types.

    RECORDS   – indexed document chunks (current, only type in use)
    ENTITIES  – future: entity vectors from knowledge graph extraction
    """
    RECORDS = "records"
    ENTITIES = "entities"


class CollectionResolver:
    """Resolves a (tenant, collection_type) pair to a physical collection name.

    Naming scheme: ``{tenant_prefix}{type}``

    - With no tenant (default): ``records`` (backward-compatible with all existing data)
    - With tenant ``acme``: ``acme_records``

    Examples::

        resolver = CollectionResolver()
        resolver.resolve()                    # "records"
        resolver.resolve(collection_type=CollectionType.ENTITIES)   # "entities"
        resolver.resolve(tenant_id="acme")    # "acme_records"
        resolver.resolve("acme", CollectionType.ENTITIES)            # "acme_entities"
    """

    def __init__(self, tenant_id: Optional[str] = None) -> None:
        self._tenant_id = tenant_id

    def resolve(
        self,
        tenant_id: Optional[str] = None,
        collection_type: CollectionType = CollectionType.RECORDS,
    ) -> str:
        """Return the physical collection / index name.

        Args:
            tenant_id: Override the instance-level tenant.  Pass ``None`` to
                use the instance default (or no tenant if none was set).
            collection_type: The logical collection type.

        Returns:
            Physical name string safe for use as a collection / index name.
        """
        effective_tenant = tenant_id if tenant_id is not None else self._tenant_id
        base = collection_type.value  # e.g. "records"
        if effective_tenant:
            # Sanitise: lowercase, replace disallowed chars with underscores
            safe_tenant = "".join(
                c if c.isalnum() else "_" for c in effective_tenant.lower()
            )
            return f"{safe_tenant}_{base}"
        return base

    def default(self) -> str:
        """Shortcut: resolve the default (RECORDS, no tenant-override)."""
        return self.resolve()


# Module-level singleton that resolves to "records" unless VECTOR_DB_TENANT env
# is set.  Call-sites import this and call `default_resolver.resolve(...)`.
import os as _os
default_resolver = CollectionResolver(tenant_id=_os.getenv("VECTOR_DB_TENANT"))
