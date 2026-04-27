VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
VECTOR_DB_SERVICE_NAME = "qdrant"
VECTOR_DB_COLLECTION_NAME = "records"

# Re-exported from its canonical home so existing callers don't need updating.
from app.config.collection_spec import normalize_identity as normalize_identity  # noqa: E402, F401
