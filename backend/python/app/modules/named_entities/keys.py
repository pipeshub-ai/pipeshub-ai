"""Deterministic identity of a named entity: uuid5(org, kind, norm_key)."""

from __future__ import annotations

import re
import uuid

NAMED_ENTITY_NAMESPACE = uuid.UUID("c3a1e7b2-4d58-4f0a-9b6e-2a7d8c1f0e55")

# Names the recipe that builds norm keys. It stays out of the hash so that a new
# scheme forks only the keys whose recipe actually changed.
KEY_SCHEME = 1


# A key is built from a base kind, never an ontology class: a class is a label in
# the node's ``types``, so reclassifying an entity never moves its id. Built-in
# kinds never contain a dot; ``x.<org>.<name>`` is reserved for org-defined base
# kinds, so one can never collide with a built-in added later.
_BASE_KIND = re.compile(r"^(?:[a-z_]+|x\.[a-z0-9_-]+\.[a-z0-9_]+)$")


def named_entity_key(org_id: str, kind: str, norm_key: str) -> str:
    if not _BASE_KIND.match(kind):
        raise ValueError(f"not a base entity kind: {kind!r}")
    return str(uuid.uuid5(NAMED_ENTITY_NAMESPACE, f"{org_id}:{kind}:{norm_key}"))


def value_mention_key(record_id: str, kind: str, norm_key: str) -> str:
    """One row per value per record; values are never shared between records."""
    return str(uuid.uuid5(NAMED_ENTITY_NAMESPACE, f"value:{record_id}:{kind}:{norm_key}"))


def alias_partition(kind: str) -> str:
    """Neo4j TaxonomyAlias ``collection`` value, unique per kind."""
    return f"namedEntities:{kind}"
