import uuid
from typing import Optional

VIRTUAL_RECORD_ID_FIELD = "metadata.virtualRecordId"
ORG_ID_FIELD = "metadata.orgId"
VECTOR_DB_SERVICE_NAME = "qdrant"
VECTOR_DB_COLLECTION_NAME = "records"

# Payload marker used to identify the single "collection signature" sentinel
# point that records which embedding model (provider + model name + dimension)
# was used to build a given collection. User-content queries filter by orgId
# and virtualRecordId, which the sentinel does not carry, so it is never
# returned via normal filtered search.
COLLECTION_SIGNATURE_KIND = "collection_signature"
COLLECTION_SIGNATURE_KIND_FIELD = "_kind"
COLLECTION_SIGNATURE_VERSION = 1

# Namespace UUID used to derive a stable sentinel point id per collection.
# Deterministic so we can upsert/retrieve by id without a lookup table.
_SIGNATURE_NAMESPACE = uuid.UUID("6d1a7f2e-0c5e-4d2a-9c1e-3a0f1b7c5c4e")


def collection_signature_point_id(collection_name: str) -> str:
    """Deterministic sentinel point id for a collection's signature."""
    return str(uuid.uuid5(_SIGNATURE_NAMESPACE, f"collection-signature:{collection_name}"))


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
