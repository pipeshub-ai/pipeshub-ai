"""Which nodes lose their links after a full sync ran to the end.

Shared by the Neo4j and ArangoDB providers: each one reads the node ids and the
two fields the window needs, and this decides what to unlink.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

#: The edge collections a full sync rewrites, and so the ones it may remove from
#: a node it did not see. The same list the old pre-sync delete used.
FULL_SYNC_EDGE_COLLECTIONS: tuple[str, ...] = (
    CollectionNames.BELONGS_TO.value,
    CollectionNames.RECORD_RELATIONS.value,
    CollectionNames.PERMISSION.value,
    CollectionNames.INHERIT_PERMISSIONS.value,
    CollectionNames.USER_APP_RELATION.value,
    CollectionNames.ENTITY_RELATIONS.value,
    CollectionNames.ANYONE.value,
)

#: The connector's nodes that a full sync tracks. The connector's own app node is
#: left out: its incoming edges are the connector's sharing and its user list,
#: written at creation and by sharing as well as by syncs, and none of them lets
#: anyone read a record by itself.
TRACKED_COLLECTIONS: tuple[str, ...] = (
    CollectionNames.RECORDS.value,
    CollectionNames.RECORD_GROUPS.value,
    CollectionNames.GROUPS.value,
    CollectionNames.ROLES.value,
)

_COLLECTED_KEYS = {
    CollectionNames.RECORDS.value: "record_keys",
    CollectionNames.RECORD_GROUPS.value: "record_group_keys",
    CollectionNames.GROUPS.value: "group_keys",
    CollectionNames.ROLES.value: "role_keys",
}


def unseen_node_keys(
    collected: Mapping[str, Any], seen: Mapping[str, Collection[str]]
) -> dict[str, list[str]]:
    """The connector's node keys, per collection, that the sync did not write."""
    unseen: dict[str, list[str]] = {}
    for collection in TRACKED_COLLECTIONS:
        seen_ids = seen.get(collection) or ()
        unseen[collection] = [
            key for key in (collected.get(_COLLECTED_KEYS[collection]) or []) if key not in seen_ids
        ]
    return unseen


def split_unseen_by_window(
    unseen: Mapping[str, list[str]],
    record_fields: Mapping[str, Mapping[str, Any]],
    group_fields: Mapping[str, Mapping[str, Any]],
    window_start_ms: int | None,
    windowed_group_types: Collection[str],
) -> tuple[list[str], list[str]]:
    """Split the unseen nodes into those to unlink and those outside the window.

    Returns ``(unlink, outside_window)`` as ``collection/key`` node ids.

    Without a window every unseen node is unlinked: a sync that reads the whole
    source and did not see a node means the node is gone or filtered out.

    With one, an unseen record created before the window start was simply not
    read, and keeps its links; so does an unseen group of a windowed type (a
    Slack thread) created before it, and every unseen record in such a group,
    since the group's records are read only when the group is. A node with no
    creation time is treated as inside the window, which unlinks it as before.

    ``record_fields`` maps an unseen record key to its ``sourceCreatedAtTimestamp``
    and ``recordGroupId``; ``group_fields`` maps an unseen group key to its
    ``sourceCreatedAtTimestamp`` and ``groupType``.
    """
    records = CollectionNames.RECORDS.value
    groups = CollectionNames.RECORD_GROUPS.value

    def before_window(fields: Mapping[str, Any] | None) -> bool:
        created = (fields or {}).get("sourceCreatedAtTimestamp")
        return (
            window_start_ms is not None
            and isinstance(created, (int, float))
            and not isinstance(created, bool)
            and created < window_start_ms
        )

    kept_groups: set[str] = set()
    if window_start_ms is not None:
        kept_groups = {
            key
            for key in unseen.get(groups, [])
            if (group_fields.get(key) or {}).get("groupType") in windowed_group_types
            and before_window(group_fields.get(key))
        }

    unlink: list[str] = []
    outside_window: list[str] = []
    for collection in TRACKED_COLLECTIONS:
        for key in unseen.get(collection, []):
            if collection == groups:
                kept = key in kept_groups
            elif collection == records:
                fields = record_fields.get(key)
                kept = before_window(fields) or (fields or {}).get("recordGroupId") in kept_groups
            else:
                kept = False
            (outside_window if kept else unlink).append(f"{collection}/{key}")
    return unlink, outside_window
