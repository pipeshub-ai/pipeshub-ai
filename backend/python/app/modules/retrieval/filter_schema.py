"""Which keys a client may send in a retrieval request's ``filters``.

An unknown key used to be lowercased into a metadata term, which forced the
slow record-id permission path and was then ignored. It is refused instead.

The Node gateway validates the same keys (``es_validators.ts``) and the
OpenAPI ``Filters`` schema documents them; a test in each language compares its
list with that schema, so the three cannot drift apart.
"""

from __future__ import annotations

from typing import Any

from app.services.graph_db.interface.graph_db_provider import (
    ALLOWED_FILTER_KEYS,
    SELECTION_APPS_FILTER_KEY,
)

# Id lists that scope the search to apps, record groups, folders or records.
SCOPE_FILTER_KEYS = ("apps", "kb", "recordGroups", "records", "recordsExact")
# Classification names (not ids) a record must carry. Internal only: they force
# the uncapped record-id permission path, so they stay out of the public API
# (and the gateway refuses them) until scoped search can serve them cheaply.
TAXONOMY_FILTER_KEYS = (
    "departments",
    "categories",
    "subcategories1",
    "subcategories2",
    "subcategories3",
    "topics",
    "languages",
)
ENTITY_FILTERS_KEY = "entityFilters"
# Set by this service for a project chat or a dated "outside" search, never by a client (the gateway refuses it).
STRICT_SCOPE_KEY = "strictScope"

# What the gateway accepts: the OpenAPI ``Filters`` schema.
PUBLIC_REQUEST_FILTER_KEYS = (*SCOPE_FILTER_KEYS, ENTITY_FILTERS_KEY)
# What this service accepts: the public keys plus the internal ones.
ALLOWED_REQUEST_FILTER_KEYS = (*PUBLIC_REQUEST_FILTER_KEYS, *TAXONOMY_FILTER_KEYS, STRICT_SCOPE_KEY)
# Bounds the server sets itself (a saved agent's, a project's, a connected app's). A client
# may send them, but the retrieval entry points drop them before use, so they are accepted
# here (as id lists) and never listed to a client as keys it may use.
SERVER_SET_REQUEST_FILTER_KEYS = (*ALLOWED_FILTER_KEYS, SELECTION_APPS_FILTER_KEY)


def validate_request_filters(filters: dict[str, Any] | None) -> dict[str, Any] | None:
    """``filters`` unchanged if every key is known and typed; ValueError otherwise."""
    if not filters:
        return filters
    allowed = ", ".join(ALLOWED_REQUEST_FILTER_KEYS)
    unknown = sorted(set(filters) - set(ALLOWED_REQUEST_FILTER_KEYS) - set(SERVER_SET_REQUEST_FILTER_KEYS))
    if unknown:
        raise ValueError(f"Unknown filter key(s): {', '.join(unknown)}. Allowed keys: {allowed}.")
    for key, value in filters.items():
        if value is None:
            continue
        if key == ENTITY_FILTERS_KEY:
            ok = isinstance(value, dict)
            expected = "an object"
        elif key == STRICT_SCOPE_KEY:
            ok = isinstance(value, bool)
            expected = "true or false"
        else:
            ok = isinstance(value, list) and all(isinstance(i, str) for i in value)
            expected = "a list of strings"
        if not ok:
            raise ValueError(f"Filter '{key}' must be {expected}. Allowed keys: {allowed}.")
    return filters
