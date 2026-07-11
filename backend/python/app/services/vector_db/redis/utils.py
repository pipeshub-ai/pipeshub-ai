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

import struct
from typing import Any, Dict, List, Optional, Tuple, Union

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
# IMPORTANT: space is NOT included — spaces are word separators, not operators.
# Escaping them fuses multi-word queries into a single token that never matches.
_TEXT_QUERY_ESCAPE_CHARS = set(',.<>{}[]"\'`@!:;#$%^&*()+=-~|\\')


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


# struct format codes per Redis VECTOR data type.
#   FLOAT32 -> 'f' (4 bytes/dim), FLOAT16 -> 'e' (IEEE-754 half, 2 bytes/dim)
_DTYPE_STRUCT_CODE = {
    "FLOAT32": "f",
    "FLOAT16": "e",
}


def vector_to_bytes(vector: List[float], dtype: str = "FLOAT32") -> bytes:
    """Encode a float list as raw little-endian bytes for a Redis VECTOR field.

    The byte width MUST match the index field's ``TYPE`` — RediSearch rejects a
    query blob whose size does not equal ``dim * sizeof(dtype)``.  Use the same
    ``dtype`` that the target index was created with.
    """
    code = _DTYPE_STRUCT_CODE.get(dtype.upper())
    if code is None:
        raise ValueError(
            f"Unsupported Redis vector dtype {dtype!r}; "
            f"expected one of {sorted(_DTYPE_STRUCT_CODE)}"
        )
    return struct.pack(f"<{len(vector)}{code}", *vector)


def vector_point_to_hash_fields(point: VectorPoint, dtype: str = "FLOAT16") -> Dict[str, Any]:
    """Convert a VectorPoint to a flat dict of HSET fields.

    Structure stored under key ``{collection}:{point_id}``::

        {
          "page_content": "...",
          "dense_embedding": b"...",  # binary FLOAT16/FLOAT32 blob
          "metadata_orgId": "...",    # flattened metadata fields (TAG indexed)
          "metadata_virtualRecordId": "...",
          ...                         # any additional metadata_* fields
        }

    Metadata is stored only in flattened ``metadata_*`` keys (needed for
    Redis FT TAG indexing).  On read, ``reconstruct_metadata`` rebuilds the
    nested ``metadata`` dict from those keys, avoiding data duplication.

    The ``dense_embedding`` is stored as a binary blob (not a JSON float array)
    so Redis can map it directly into the HNSW index without text parsing.
    The byte width is determined by ``dtype`` (FLOAT16 = 2 bytes/dim,
    FLOAT32 = 4 bytes/dim) and must match the ``TYPE`` used in ``FT.CREATE``.
    """
    fields: Dict[str, Any] = {
        "page_content": point.payload.get("page_content", ""),
    }
    if point.dense_vector is not None:
        fields["dense_embedding"] = vector_to_bytes(point.dense_vector, dtype)

    metadata = point.payload.get("metadata", {})
    for k, v in metadata.items():
        fields[f"metadata_{k}"] = _coerce_hash_value(v)

    return fields


def _coerce_hash_value(v: Any) -> Union[str, int, float, bytes]:
    """Coerce a metadata value to a type Redis HSET accepts.

    Redis commands only accept bytes, str, int, or float.  Booleans, None,
    lists, dicts, and other types are serialized to their string representation.
    """
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()  # "true" / "false"
    if isinstance(v, (int, float, str, bytes)):
        return v
    # Lists, dicts, and other complex types → JSON-like string
    import json
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return str(v)


def reconstruct_metadata(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the nested ``metadata`` dict from flattened ``metadata_*`` keys.

    Also handles legacy documents that still carry the nested ``metadata`` blob.

    Because Redis HASH fields are always strings, values that were originally
    int, float, or bool are recovered via ``_recover_typed_value``.
    """
    if "metadata" in doc and isinstance(doc["metadata"], dict):
        return doc["metadata"]
    return {
        k[len("metadata_"):]: _recover_typed_value(v)
        for k, v in doc.items()
        if k.startswith("metadata_")
    }


def _recover_typed_value(v: Any) -> Any:
    """Best-effort recovery of original Python types from Redis string values.

    Redis HASH fields are always returned as strings.  We wrote booleans as
    "true"/"false", ints as their decimal repr, and complex types as JSON.
    This function reverses that coercion so downstream code sees the original
    types (int comparisons, bool checks, etc.).
    """
    if not isinstance(v, str):
        return v
    # Booleans (written by _coerce_hash_value as "true"/"false")
    if v == "true":
        return True
    if v == "false":
        return False
    # Empty string → was None on write
    if v == "":
        return None
    # Integer
    try:
        return int(v)
    except ValueError:
        pass
    # Float (only if it contains a dot or 'e' to avoid int→float promotion)
    if "." in v or "e" in v.lower():
        try:
            return float(v)
        except ValueError:
            pass
    # JSON compound types (lists, dicts)
    if v.startswith(("{", "[")):
        import json
        try:
            return json.loads(v)
        except (json.JSONDecodeError, ValueError):
            pass
    return v


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


def _as_map(obj: Any) -> Dict[str, Any]:
    """Normalise a Redis map reply to a ``{str: value}`` dict.

    Handles both RESP2 (flat ``[k, v, k, v, ...]`` list) and RESP3 (native
    dict) representations, decoding all keys to ``str``.
    """
    if isinstance(obj, dict):
        return {_decode(k): v for k, v in obj.items()}
    return _parse_fields_list(obj)


def decode_hash_doc(raw: Any) -> Dict[str, Any]:
    """Decode an HGETALL reply into a ``{str: str}`` dict for payload reconstruction.

    - Bytes keys/values are decoded to str.
    - The ``dense_embedding`` field (binary blob) is skipped — it carries no
      useful information outside of the HNSW index and would corrupt string
      processing downstream.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        items_iter = raw.items()
    else:
        # RESP2 flat [k, v, k, v, ...] list
        items_list = list(raw)
        items_iter = (  # type: ignore[assignment]
            (items_list[i], items_list[i + 1])
            for i in range(0, len(items_list) - 1, 2)
        )

    result: Dict[str, Any] = {}
    for k, v in items_iter:
        key = _decode(k)
        if key == "dense_embedding":
            continue
        result[key] = _decode(v) if isinstance(v, bytes) else (v if v is not None else "")
    return result


def parse_ft_hybrid_reply(reply: Any) -> List[Tuple[str, float]]:
    """Parse an ``FT.HYBRID`` reply and return ``(full_key, score)`` pairs.

    Redis 8.4 ``FT.HYBRID`` returns a map (not the ``FT.SEARCH`` array shape).
    Under RESP2 (``decode_responses=False``) it arrives as a flat key/value list::

        [b"total_results", 2,
         b"results", [
             [b"__key", b"records:id1", b"__score", b"0.03"],
             [b"__key", b"records:id2", b"__score", b"0.03"],
         ],
         b"warnings", [],
         b"execution_time", b"3.2"]

    Each result entry is a flat field list from ``LOAD 2 @__key @__score``.
    The caller is responsible for fetching the full document payload via a
    pipelined ``HGETALL`` on each returned key.
    """
    pairs: List[Tuple[str, float]] = []
    if not reply:
        return pairs

    top = _as_map(reply)
    raw_results = top.get("results")
    if not isinstance(raw_results, (list, tuple)):
        return pairs

    for entry in raw_results:
        fields = _as_map(entry)

        score_raw = fields.get("__score") or fields.get("@__score") or "0"
        try:
            score = float(_decode(score_raw))
        except (TypeError, ValueError):
            score = 0.0

        key_str = _decode(fields.get("__key") or fields.get("@__key") or "")
        if key_str:
            pairs.append((key_str, score))

    return pairs


def parse_ft_search_reply(reply: Any) -> List[SearchResult]:
    """Parse a plain ``FT.SEARCH`` reply (ON HASH, no RETURN clause) into SearchResult objects.

    With ON HASH and no RETURN/NOCONTENT, Redis returns all hash fields::

        [total_count,
         b"prefix:id1", [b"page_content", b"...", b"metadata_orgId", b"..."],
         b"prefix:id2", [...], ...]

    No score is available; we assign 0.0.
    The ``dense_embedding`` binary blob is present in the field list but is
    decoded and discarded by ``decode_hash_doc``.
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

        doc = decode_hash_doc(_parse_fields_list(fields_list))
        payload: Dict[str, Any] = {
            "page_content": doc.get("page_content", ""),
            "metadata": reconstruct_metadata(doc),
        }

        key_str = _decode(raw_key)
        point_id = key_str.rsplit(":", 1)[-1] if ":" in key_str else key_str
        results.append(SearchResult(id=point_id, score=0.0, payload=payload))

    return results
