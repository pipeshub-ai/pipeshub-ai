from typing import Optional

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
VECTOR_DB_SERVICE_NAME = "qdrant"
VECTOR_DB_COLLECTION_NAME = "records"


def normalize_identity(value: Optional[str]) -> str:
    """Normalize provider/model identifiers for identity comparisons.

    Strips an optional ``models/`` prefix (used by Google/Vertex), lower-cases
    and trims whitespace so minor casing / prefix differences don't produce
    spurious "model change" errors when the user is actually saving the same
    configuration.

    Returns ``""`` for falsy input so callers can cheaply distinguish
    "unknown" from "different".
    """
    if not value:
        return ""
    return value.removeprefix("models/").strip().lower()
