import os

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
VECTOR_DB_SERVICE_NAME = os.getenv("VECTOR_DB_TYPE", "qdrant")
VECTOR_DB_COLLECTION_NAME = "records"
