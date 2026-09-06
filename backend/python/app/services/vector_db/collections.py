"""Collection naming primitives.

``CollectionType`` names the logical datasets a deployment can hold, and
``sanitize_collection_name`` normalises any candidate name to a form every
provider accepts.

*Which* collection a given record, query, or delete resolves to is decided by
``CollectionStrategy`` (see ``strategy.py``) and executed by
``CollectionRegistry`` — this module deliberately holds no resolution policy
of its own, so there is exactly one place that answers "where does this go?".
"""

import hashlib
import re
from enum import Enum

# Intersection of Qdrant / OpenSearch / Redis / pgvector naming rules.
_MAX_COLLECTION_NAME_LENGTH = 200
_DISALLOWED_LEADING_CHARS = ("_", "-", "+")
_INVALID_CHARS_RE = re.compile(r"[^a-z0-9_]")


class CollectionType(Enum):
    """Logical collection types.

    RECORDS   – indexed document chunks (current, only type in use)
    ENTITIES  – future: entity vectors from knowledge graph extraction
    """
    RECORDS = "records"
    ENTITIES = "entities"


def sanitize_collection_name(name: str) -> str:
    """Normalize a candidate collection name to a form valid across every provider.

    Rules (intersection of Qdrant, OpenSearch, Redis, pgvector):
    - Lowercase only
    - Only ``[a-z0-9_]`` (everything else becomes ``_``)
    - Cannot start with ``_``, ``-``, or ``+``
    - Max length 200; over-length names are truncated with a deterministic
      hash suffix so two different long names never collide after truncation

    Idempotent: ``sanitize(sanitize(x)) == sanitize(x)``. The dedup path
    compares a candidate name against an already-resolved one, so a second
    pass that changed the answer would let a record be skipped as a duplicate
    of something living in a collection it was never written to.
    """
    candidate = _INVALID_CHARS_RE.sub("_", name.lower())
    while candidate and candidate[0] in _DISALLOWED_LEADING_CHARS:
        candidate = candidate[1:]
    if not candidate:
        # Every character was illegal or stripped. A bare constant would map
        # every such name onto one physical collection; the digest keeps
        # distinct inputs distinct.
        return f"collection_{_digest(name)}"
    if len(candidate) > _MAX_COLLECTION_NAME_LENGTH:
        digest = _digest(name)
        truncate_to = _MAX_COLLECTION_NAME_LENGTH - len(digest) - 1
        candidate = f"{candidate[:truncate_to]}_{digest}"
    return candidate


def _digest(name: str) -> str:
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
