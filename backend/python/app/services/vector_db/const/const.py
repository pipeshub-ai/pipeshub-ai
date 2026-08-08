import os

# Legacy constant — still works; resolves to "records" unless VECTOR_DB_TENANT is set.
# Prefer importing CollectionResolver from collections.py for new code.
from app.services.vector_db.collections import CollectionType, default_resolver

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"

VECTOR_DB_SERVICE_NAME = os.getenv("VECTOR_DB_TYPE", "qdrant")

# This now goes through CollectionResolver to support future tenant/type overrides
VECTOR_DB_COLLECTION_NAME = default_resolver.default()
VECTOR_DB_ENTITIES_COLLECTION_NAME = default_resolver.resolve(
    collection_type=CollectionType.ENTITIES
)
