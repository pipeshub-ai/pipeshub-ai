import os

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
CONNECTOR_IDS_FIELD = "connectorIds"
RECORD_GROUP_IDS_FIELD = "recordGroupIds"
# Root of a record's group chain, so search can scope by root instead of every
# descendant group. Only set by connectors whose grants sit at the root.
ROOT_RECORD_GROUP_IDS_FIELD = "rootRecordGroupIds"

# Keyword/TAG payload indexes created on every collection (new and existing).
PAYLOAD_KEYWORD_INDEXES = (
    (VIRTUAL_RECORD_ID_FIELD, {"type": "keyword"}),
    (ORG_ID_FIELD, {"type": "keyword"}),
    (CONNECTOR_IDS_FIELD, {"type": "keyword"}),
    (RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
    (ROOT_RECORD_GROUP_IDS_FIELD, {"type": "keyword"}),
)

VECTOR_DB_SERVICE_NAME = os.getenv("VECTOR_DB_TYPE", "qdrant")
