"""Reads that return points: scroll (a keyset on ``point_sk`` with an opaque, org-bound offset), retrieve by id, and the count.

Every read is pinned to the org, the collection and the org's active space, and skips tombstoned points.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import TYPE_CHECKING, Any

from app.services.permissions.scope_fields import (
    ACL_SCOPE_FIELD,
    PATH_IDS_FIELD,
    SCOPE_GENERATION_FIELD,
    SCOPE_KEYS,
    SCOPE_STAMP_FIELD,
    SCOPE_VERSION_FIELD,
)
from app.services.vector_db.models import VectorPoint
from app.services.vector_db.pgvector.errors import OrgMismatch
from app.services.vector_db.pgvector.sql_filters import PROMOTED, Compiled

if TYPE_CHECKING:
    import uuid

    import asyncpg

    from app.core.db import Tx

_ALIVE = "NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)"
# paths that come from columns, so a scan asking for nothing else reads no payload
_COLUMN_PATHS = {**PROMOTED, "connectorIds": None, "recordGroupIds": None, "rootRecordGroupIds": None, **dict.fromkeys(SCOPE_KEYS)}
_SCOPE_COLUMNS = "g.path_ids, g.acl_scope, g.scope_stamp, g.scope_version, g.scope_generation"


def encode_offset(org: str, point_sk: int) -> str:
    """``base64(sha256(org)[:8] || point_sk)``: a cursor from one org's scan is refused for another org."""
    return base64.urlsafe_b64encode(hashlib.sha256(org.encode()).digest()[:8] + point_sk.to_bytes(8, "big")).decode()


def decode_offset(org: str, offset: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(offset.encode())
    except ValueError as e:
        raise ValueError(f"malformed scroll offset {offset!r}") from e
    if len(raw) != 16:
        raise ValueError(f"malformed scroll offset {offset!r}")
    if raw[:8] != hashlib.sha256(org.encode()).digest()[:8]:
        raise OrgMismatch("the scroll offset belongs to another organisation")
    point_sk = int.from_bytes(raw[8:], "big")
    if point_sk >= 2**63:                # a forged cursor: point_sk is a bigint
        raise ValueError(f"malformed scroll offset {offset!r}")
    return point_sk


def _set_path(target: dict[str, Any], path: str, value: Any) -> None:  # noqa: ANN401
    *head, last = path.split(".")
    for part in head:
        target = target.setdefault(part, {})
    target[last] = value


def _get_path(source: dict[str, Any], path: str) -> tuple[bool, Any]:
    cur: Any = source
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


_COLUMN_KEYS = {"metadata.virtualRecordId": "vrid", "metadata.blockId": "block_id", "metadata.entityType": "ent_type",
                "metadata.entityId": "ent_id", "metadata.kind": "ent_kind", "metadata.level": "ent_level"}


def to_point(row: asyncpg.Record, with_payload: list[str] | None, org: str) -> VectorPoint:
    """The payload as the other backends return it: stored payload, ``page_content`` and the membership arrays as siblings."""
    if row["payload"] is not None:
        # points written before the scope fields moved to the group may still carry them in the payload: the group's own values (or none) win
        stored = {k: v for k, v in json.loads(row["payload"]).items() if k not in SCOPE_KEYS}
    else:      # only column paths were asked for: rebuild them from the columns
        stored = {"metadata": {"orgId": org}}
        for path, col in _COLUMN_KEYS.items():
            if row[col] is not None:
                stored["metadata"][path.split(".", 1)[1]] = row[col]
    full = {**stored, "page_content": row["page_content"] or "",
            "connectorIds": list(row["connector_ids"] or []), "recordGroupIds": list(row["record_group_ids"] or []),
            "rootRecordGroupIds": list(row["root_record_group_ids"] or [])}
    if row.get("scope_version") is not None:      # a group that was never stamped carries no scope keys, like a point of another store
        full.update({PATH_IDS_FIELD: list(row["path_ids"] or []), ACL_SCOPE_FIELD: list(row["acl_scope"] or []),
                     SCOPE_STAMP_FIELD: row["scope_stamp"], SCOPE_VERSION_FIELD: row["scope_version"],
                     SCOPE_GENERATION_FIELD: row["scope_generation"]})
    if with_payload is None:
        return VectorPoint(id=str(row["point_id"]), payload=full)
    out: dict[str, Any] = {}
    for path in with_payload:
        found, value = _get_path(full, path)
        if found:
            _set_path(out, path, value)
    return VectorPoint(id=str(row["point_id"]), payload=out)


def _needs(with_payload: list[str] | None) -> tuple[bool, bool]:
    """(read the stored payload, read page_content): neither when every requested path is a column."""
    json_ = with_payload is None or any(p not in _COLUMN_PATHS and p != "page_content" for p in with_payload)
    text = with_payload is None or "page_content" in with_payload
    return json_, text


async def scroll(tx: Tx, org: str, collection_id: int, space_id: int, c: Compiled, limit: int, offset: str | None,
                 with_payload: list[str] | None) -> tuple[list[VectorPoint], str | None]:
    """One page of points ordered by ``point_sk``; ``c`` is compiled with ``first_param=5``. Fetches one extra row to know
    whether another page exists."""
    after = decode_offset(org, offset) if offset else 0
    need_json, need_text = _needs(with_payload)
    join_text = need_json or need_text or c.needs_text
    rows = await tx.fetch(
        f"""/* pipeshub:pgvector.scroll */
        SELECT p.point_sk, p.point_id, p.vrid, p.block_id, p.ent_type, p.ent_id, p.ent_kind, p.ent_level,
               {'pt.payload' if need_json else 'NULL::jsonb'} AS payload, {'pt.page_content' if need_text else 'NULL::text'} AS page_content,
               coalesce(g.connector_ids, '{{}}') AS connector_ids, coalesce(g.record_group_ids, '{{}}') AS record_group_ids,
               coalesce(g.root_record_group_ids, '{{}}') AS root_record_group_ids, {_SCOPE_COLUMNS}
        FROM retrieval.points p
        {'JOIN retrieval.point_text pt ON pt.org_id = $1 AND pt.point_sk = p.point_sk' if join_text else ''}
        LEFT JOIN retrieval.point_groups g ON g.org_id = $1 AND g.collection_id = p.collection_id AND g.group_key = p.group_key
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_sk > $4 AND {_ALIVE} AND ({c.where})
        ORDER BY p.point_sk LIMIT ${len(c.params) + 5}""",
        org, collection_id, space_id, after, *c.params, limit + 1,
    )
    page = rows[:limit]
    return [to_point(r, with_payload, org) for r in page], (encode_offset(org, page[-1]["point_sk"]) if len(rows) > limit else None)


async def retrieve(tx: Tx, org: str, collection_id: int, space_id: int, ids: list[uuid.UUID]) -> list[VectorPoint]:
    rows = await tx.fetch(
        """/* pipeshub:pgvector.retrieve */
        SELECT p.point_id, p.vrid, p.block_id, p.ent_type, p.ent_id, p.ent_kind, p.ent_level, pt.payload, pt.page_content, coalesce(g.connector_ids, '{}') AS connector_ids,
               coalesce(g.record_group_ids, '{}') AS record_group_ids, coalesce(g.root_record_group_ids, '{}') AS root_record_group_ids,
               g.path_ids, g.acl_scope, g.scope_stamp, g.scope_version, g.scope_generation
        FROM retrieval.points p
        JOIN retrieval.point_text pt ON pt.org_id = $1 AND pt.point_sk = p.point_sk
        LEFT JOIN retrieval.point_groups g ON g.org_id = $1 AND g.collection_id = p.collection_id AND g.group_key = p.group_key
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3 AND p.point_id = ANY($4::uuid[])
          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)""",
        org, collection_id, space_id, ids,
    )
    return [to_point(r, None, org) for r in rows]


async def count(tx: Tx, org: str, collection_id: int, space_id: int) -> int:
    """Exact count of the org's live points of one collection and space (tombstoned points, kept in HNSW safe mode, are not live)."""
    return await tx.fetchval(
        """/* pipeshub:pgvector.count */
        SELECT count(*) FROM retrieval.points p
        WHERE p.org_id = $1 AND p.collection_id = $2 AND p.space_id = $3
          AND NOT EXISTS (SELECT 1 FROM retrieval.point_tombstones t WHERE t.org_id = $1 AND t.point_sk = p.point_sk)""",
        org, collection_id, space_id,
    )
