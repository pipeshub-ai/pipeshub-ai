import os

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
    (CONTENT_REV_FIELD, {"type": "keyword"}),
    (EMBED_KEY_FIELD, {"type": "keyword"}),
    (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
    (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
    (ROOT_RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
)

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
