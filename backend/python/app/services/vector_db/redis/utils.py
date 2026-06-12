"""Utility helpers for the Redis vector provider.

Redis FT query syntax notes
---------------------------
- TAG fields: ``@field:{value}``  (values with special chars must be escaped)
- TEXT fields: ``@field:word``
- NUMERIC fields: ``@field:[lo hi]``
- Logical AND: space-separated or ``( … ) ( … )``
- Logical OR: ``( … | … )``
- Parentheses group sub-queries.

Tag value escaping
------------------
Qdrant virtual-record IDs are UUIDs (safe).  org-IDs may contain characters
like ``-`` which are safe in Redis tag values.  We still escape any char that
Redis treats as special inside ``{}``.
"""

import json
import struct
from typing import Any, Dict, List, Optional

from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    SearchResult,
    VectorPoint,
)


# Characters that need escaping inside a Redis tag value
_TAG_ESCAPE_CHARS = set(r",.<>{}[]\"':;!@#$%^&*()\- +=/\\|~`")

# Characters that must be escaped in a RediSearch full-text (TEXT field) query.
# See https://redis.io/docs/latest/develop/interact/search-and-query/advanced-concepts/query_syntax/
_TEXT_QUERY_ESCAPE_CHARS = set(',.<>{}[]"\'`@!:;#$%^&*()+=-~|/ \\')


def escape_tag_value(value: str) -> str:
    """Escape a single tag value for use in a Redis FT query."""
    return "".join(f"\\{c}" if c in _TAG_ESCAPE_CHARS else c for c in str(value))


def escape_redisearch_text(query: str) -> str:
    """Escape a free-text query string for safe use in RediSearch FT queries.

    Characters that would otherwise be interpreted as operators (``@``, ``{}``,
    ``:``, ``-``, etc.) are backslash-escaped so they are treated as literals.
    """
    if not query:
        return ""
    return "".join(f"\\{c}" if c in _TEXT_QUERY_ESCAPE_CHARS else c for c in query)


def field_conditions_to_redis_query(conditions: List[FieldCondition]) -> str:
    """Convert a list of FieldCondition objects to a Redis FT query fragment."""
    parts: List[str] = []
    for cond in conditions:
        field = cond.key  # already has "metadata." prefix
        redis_field = "@" + field.replace(".", "_")  # dots not allowed in field names
        if cond.values is not None:
            escaped = "|".join(escape_tag_value(str(v)) for v in cond.values)
            parts.append(f"{redis_field}:{{{escaped}}}")
        elif cond.value is not None:
            escaped = escape_tag_value(str(cond.value))
            parts.append(f"{redis_field}:{{{escaped}}}")
    return " ".join(parts)


def filter_expression_to_redis_query(expr: FilterExpression) -> str:
    """Convert a FilterExpression to a Redis FT query string.

    - ``must`` conditions are ANDed together.
    - ``should`` conditions are ORed and wrapped (minimum-1 match).
    - ``must_not`` conditions are negated.

    Returns empty string (match-all) when the expression is empty.
    """
    parts: List[str] = []

    if expr.must:
        parts.append(field_conditions_to_redis_query(expr.must))

    if expr.should:
        or_clause = "|".join(
            f"({field_conditions_to_redis_query([c])})" for c in expr.should
        )
        parts.append(f"({or_clause})")

    if expr.must_not:
        for cond in expr.must_not:
            neg = field_conditions_to_redis_query([cond])
            if neg:
                parts.append(f"-({neg})")

    return " ".join(parts) if parts else "*"


def vector_to_bytes(vector: List[float]) -> bytes:
    """Encode a float list as raw FLOAT32 little-endian bytes for Redis VECTOR field."""
    return struct.pack(f"{len(vector)}f", *vector)


def vector_point_to_json_doc(point: VectorPoint) -> Dict[str, Any]:
    """Convert a VectorPoint to the JSON dict stored in Redis as a JSON document.

    Structure stored under key ``{collection}:{point_id}``::

        {
          "page_content": "...",
          "dense_embedding": [...],          # list[float]
          "metadata_orgId": "...",           # flattened metadata fields (TAG indexed)
          "metadata_virtualRecordId": "...",
          ...raw metadata fields also nested under "metadata"...
        }

    Flattened ``metadata_*`` fields allow Redis TAG/NUMERIC indexing without
    dots (Redis FT field names cannot contain dots).
    """
    doc: Dict[str, Any] = {
        "page_content": point.payload.get("page_content", ""),
    }
    if point.dense_vector is not None:
        doc["dense_embedding"] = point.dense_vector

    metadata = point.payload.get("metadata", {})
    # Keep the nested metadata blob for downstream retrieval
    doc["metadata"] = metadata

    # Flatten selected metadata fields for indexing
    for k, v in metadata.items():
        doc[f"metadata_{k}"] = v

    return doc


def _decode(val: Any) -> str:
    """Decode bytes to str; pass-through for already-decoded strings."""
    if isinstance(val, bytes):
        return val.decode(errors="replace")
    return str(val) if val is not None else ""


def _parse_fields_list(fields_list: Any) -> Dict[str, Any]:
    """Convert a flat alternating [name, value, ...] list into a dict.

    All field names are decoded to str so look-ups are consistent regardless
    of ``decode_responses`` setting.
    """
    fields: Dict[str, Any] = {}
    if not isinstance(fields_list, (list, tuple)):
        return fields
    items = list(fields_list)
    i = 0
    while i < len(items) - 1:
        fname = _decode(items[i])
        fval = items[i + 1]
        fields[fname] = fval
        i += 2
    return fields


def parse_ft_hybrid_reply(reply: Any, with_payload: bool = True) -> List[SearchResult]:
    """Parse the reply from ``FT.HYBRID … LOAD 1 $`` into SearchResult objects.

    Reply format (``decode_responses=False``)::

        [total_count,
         b"prefix:id1", [b"__score", b"0.0167", b"$", b"{...json...}"],
         b"prefix:id2", [...], ...]

    The RRF combined score is stored in the ``__score`` field.
    """
    results: List[SearchResult] = []
    if not reply or not isinstance(reply, (list, tuple)):
        return results

    items = list(reply[1:])  # skip total count at index 0

    i = 0
    while i < len(items) - 1:
        raw_key = items[i]
        fields_list = items[i + 1]
        i += 2

        if not isinstance(fields_list, (list, tuple)):
            continue

        fields = _parse_fields_list(fields_list)

        # RRF score field — Redis 8.4 FT.HYBRID returns it as "__score"
        score_raw = fields.get("__score") or fields.get("@__combined_score") or "0"
        try:
            score = float(_decode(score_raw))
        except (TypeError, ValueError):
            score = 0.0

        payload: Dict[str, Any] = {}
        if with_payload:
            json_blob = fields.get("$")
            if json_blob:
                try:
                    if isinstance(json_blob, (bytes, bytearray)):
                        json_blob = json_blob.decode()
                    doc = json.loads(json_blob)
                    payload = {
                        "page_content": doc.get("page_content", ""),
                        "metadata": doc.get("metadata", {}),
                    }
                except Exception:
                    pass

        key_str = _decode(raw_key)
        point_id = key_str.rsplit(":", 1)[-1] if ":" in key_str else key_str
        results.append(SearchResult(id=point_id, score=score, payload=payload))

    return results


def parse_ft_search_reply(reply: Any) -> List[SearchResult]:
    """Parse a plain ``FT.SEARCH … RETURN 1 $`` reply into SearchResult objects.

    Reply format (``decode_responses=False``)::

        [total_count,
         b"prefix:id1", [b"$", b"{...json...}"],
         b"prefix:id2", [...], ...]

    No score is available; we assign 0.0.
    """
    results: List[SearchResult] = []
    if not reply or not isinstance(reply, (list, tuple)):
        return results

    items = list(reply[1:])
    i = 0
    while i < len(items) - 1:
        raw_key = items[i]
        fields_list = items[i + 1]
        i += 2

        if not isinstance(fields_list, (list, tuple)):
            continue

        fields = _parse_fields_list(fields_list)
        payload: Dict[str, Any] = {}
        json_blob = fields.get("$")
        if json_blob:
            try:
                if isinstance(json_blob, (bytes, bytearray)):
                    json_blob = json_blob.decode()
                doc = json.loads(json_blob)
                payload = {
                    "page_content": doc.get("page_content", ""),
                    "metadata": doc.get("metadata", {}),
                }
            except Exception:
                pass

        key_str = _decode(raw_key)
        point_id = key_str.rsplit(":", 1)[-1] if ":" in key_str else key_str
        results.append(SearchResult(id=point_id, score=0.0, payload=payload))

    return results
