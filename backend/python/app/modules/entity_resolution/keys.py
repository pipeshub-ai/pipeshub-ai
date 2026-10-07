"""Deterministic graph keys for canonical taxonomy nodes.

Two records that resolve the same new name at the same time both derive the
same key, so their concurrent inserts converge on one node instead of
racing to create two. The key is scoped by org and collection, which keeps
subcategory levels apart and never links two orgs.
"""

from __future__ import annotations

import uuid

TAXONOMY_KEY_NAMESPACE = uuid.UUID("2f1b7f4e-6f0a-4c5b-9a7d-3e8c2d1f0a9b")


def taxonomy_node_key(org_id: str, collection: str, normalized_name: str) -> str:
    """UUID5 key for the canonical node of ``normalized_name`` in ``collection``."""
    if not org_id or not collection or not normalized_name:
        raise ValueError("org_id, collection and normalized_name are all required")
    return str(
        uuid.uuid5(TAXONOMY_KEY_NAMESPACE, f"{org_id}:{collection}:{normalized_name}")
    )


__all__ = ["TAXONOMY_KEY_NAMESPACE", "taxonomy_node_key"]
