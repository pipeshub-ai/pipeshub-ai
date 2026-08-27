import os

# Legacy constant — still works; resolves to "records" unless VECTOR_DB_TENANT is set.
# Prefer importing CollectionResolver from collections.py for new code.
from app.services.vector_db.collections import default_resolver

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
CONNECTOR_IDS_FIELD = "connectorIds"
RECORD_GROUP_IDS_FIELD = "recordGroupIds"

# Keyword/TAG payload indexes created on every collection (new and existing).
PAYLOAD_KEYWORD_INDEXES = (
    (VIRTUAL_RECORD_ID_FIELD, {"type": "keyword"}),
    (ORG_ID_FIELD, {"type": "keyword"}),
    (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
    (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
)

VECTOR_DB_SERVICE_NAME = os.getenv("VECTOR_DB_TYPE", "qdrant")

# This now goes through CollectionResolver to support future tenant/type overrides
VECTOR_DB_COLLECTION_NAME = default_resolver.default()
