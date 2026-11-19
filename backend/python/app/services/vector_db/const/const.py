import os
from collections.abc import Iterable

from app.services.permissions.scope_fields import (
    ACL_SCOPE_FIELD,
    PATH_IDS_FIELD,
    SCOPE_GENERATION_FIELD,
    SCOPE_STAMP_FIELD,
    SCOPE_VERSION_FIELD,
)
from app.services.vector_db.collections import CollectionType, sanitize_collection_name

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
# Deleting some of a record's points by block id filters on it; Redis refuses a filter on a field its index lacks.
BLOCK_ID_FIELD = "metadata.blockId"
# Which revision and embedding setup wrote a point, so a resumed embedding run can remove the points
# of another (see app/modules/indexing/embed_resume.py).
CONTENT_REV_FIELD = "metadata.contentRev"
EMBED_KEY_FIELD = "metadata.embedKey"
CONNECTOR_IDS_FIELD = "connectorIds"
RECORD_GROUP_IDS_FIELD = "recordGroupIds"
# Root of a record's group chain, so search can scope by root instead of every
# descendant group. Only set by connectors whose grants sit at the root.
ROOT_RECORD_GROUP_IDS_FIELD = "rootRecordGroupIds"

# Keyword/TAG payload indexes created on every collection (new and existing).
PAYLOAD_KEYWORD_INDEXES = (
    (VIRTUAL_RECORD_ID_FIELD, {"type": "keyword"}),
    (ORG_ID_FIELD, {"type": "keyword"}),
    (BLOCK_ID_FIELD, {"type": "keyword"}),
    (CONTENT_REV_FIELD, {"type": "keyword"}),
    (EMBED_KEY_FIELD, {"type": "keyword"}),
    (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
    (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
    (ROOT_RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
)

# Payload fields a package adds to the index list at start-up. ``guarded`` fields are ones a resumed or scoped write filters
# on, so a collection that could not index them is reported by ``CollectionRegistry.fields_ready``; ``keyword_required`` fields
# must be mapped as keyword by a store that maps dynamically (OpenSearch). Empty by default, which is the list above.
_registered_indexes: dict[str, dict] = {}
_registered_guarded: set[str] = set()
_registered_keyword_required: set[str] = set()


def register_payload_keyword_indexes(
    fields: Iterable[tuple[str, dict]], *, guarded: bool = False, keyword_required: bool = False
) -> None:
    """Add payload indexes. Deduplicated by field, so registering the same fields again is a no-op."""
    for name, schema in fields:
        _registered_indexes.setdefault(name, schema)
        if guarded:
            _registered_guarded.add(name)
        if keyword_required:
            _registered_keyword_required.add(name)


def payload_keyword_indexes() -> tuple:
    """``PAYLOAD_KEYWORD_INDEXES`` plus whatever was registered."""
    known = {name for name, _ in PAYLOAD_KEYWORD_INDEXES}
    return PAYLOAD_KEYWORD_INDEXES + tuple((n, s) for n, s in _registered_indexes.items() if n not in known)


def guarded_payload_fields() -> frozenset[str]:
    return frozenset(_registered_guarded)


def keyword_required_fields() -> frozenset[str]:
    return frozenset(_registered_keyword_required)


def _reset_payload_registry() -> None:
    """Test-only: forget every registered field."""
    _registered_indexes.clear()
    _registered_guarded.clear()
    _registered_keyword_required.clear()


# Scope fields (permission model). Their indexes are created only while scope fields are written (see
# `app.services.permissions.mode.scope_fields_enabled`): nothing reads them yet.
SCOPE_KEYWORD_INDEXES = (
    (PATH_IDS_FIELD, {"type": "keyword"}),
    (ACL_SCOPE_FIELD, {"type": "keyword"}),
    (SCOPE_STAMP_FIELD, {"type": "keyword"}),
)

# The two integers a scope filter compares (Redis cannot filter a field that is not in its index; Qdrant and OpenSearch are faster with one).
SCOPE_INTEGER_INDEXES = (
    (SCOPE_VERSION_FIELD, {"type": "integer"}),
    (SCOPE_GENERATION_FIELD, {"type": "integer"}),
)

VECTOR_DB_SERVICE_NAME = os.getenv("VECTOR_DB_TYPE", "qdrant")

# Fixed default names for call sites that need one before a CollectionStrategy
# is resolved (container wiring for EntityVectorStore, mainly — it isn't
# strategy-scoped like the RECORDS path through CollectionRegistry).
VECTOR_DB_COLLECTION_NAME = sanitize_collection_name(CollectionType.RECORDS.value)
VECTOR_DB_ENTITIES_COLLECTION_NAME = sanitize_collection_name(CollectionType.ENTITIES.value)
