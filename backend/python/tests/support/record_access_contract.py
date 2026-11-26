"""The record-access contract (H2 RB-8, made reusable in H5): what Neo4j, ArangoDB and the in-memory fake all hold.

Each adapter seeds its own world (through its own writes) and names which records the user must and must not read;
the contract then checks every method of ``RecordAccessReader`` against those decisions and against each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.graph_db.interface.record_access import RECORD_ACCESS_METHODS

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.services.graph_db.interface.record_access import RecordAccessReader


@dataclass(frozen=True)
class AccessWorld:
    access: RecordAccessReader
    org_id: str
    user_id: str
    #: label -> record id the user reads (every access path the adapter's world seeded)
    readable: Mapping[str, str]
    #: label -> record id the user may not read: no grant, another user's, an app the user lacks, absent, in the trash
    unreadable: Mapping[str, str]
    #: label -> record id whose decision this world leaves open; still held to "the batch decides as the single check"
    either: Mapping[str, str] | None = None
    #: the user's graph key (``User.id``), which main's one access check takes; the user id when they are one
    user_key: str | None = None


def assert_implements_every_access_method(adapter: type) -> None:
    """The adapter defines each method of the surface: itself, or (a provider) through the graph port's concrete
    method, as main's ``check_access`` is (a mock or an abstract stub does not count)."""
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    def defined(name: str) -> bool:
        if callable(vars(adapter).get(name)):
            return True
        return (
            issubclass(adapter, IGraphDBProvider)
            and callable(vars(IGraphDBProvider).get(name))
            and name not in IGraphDBProvider.__abstractmethods__
        )

    missing = [name for name in RECORD_ACCESS_METHODS if not defined(name)]
    assert missing == [], f"{adapter.__name__} does not implement {missing}"


async def assert_record_access_contract(world: AccessWorld) -> None:
    access, org, user = world.access, world.org_id, world.user_id
    ids = [*world.readable.values(), *world.unreadable.values(), *(world.either or {}).values()]

    single = {rid for rid in ids if await access.check_record_access_with_details(user, org, rid) is not None}
    assert set(world.readable.values()) <= single, {k: v for k, v in world.readable.items() if v not in single}
    assert not single & set(world.unreadable.values()), {k: v for k, v in world.unreadable.items() if v in single}

    # Main's one access check (#4255), which every harness raw read decides by (SYNC4 S7), answers as the single one.
    key = world.user_key or user
    assert (await access.check_access(key, org, node_ids=ids)).node_ids == single
    assert (await access.check_access(key, org, node_ids=[])).node_ids == frozenset()
    assert (await access.check_access(f"u-{uuid.uuid4().hex[:12]}", org, node_ids=ids)).node_ids == frozenset()

    searchable = await access.filter_accessible_record_ids(ids, user, org)
    assert searchable <= single, searchable - single
    assert await access.filter_accessible_record_ids([], user, org) == set()

    for rid in world.readable.values():
        details = await access.check_record_access_with_details(user, org, rid)
        assert details is not None
        assert details["record"]["id"] == rid
        assert [p["id"] for p in details["permissions"]] == [rid]


__all__ = ["AccessWorld", "assert_implements_every_access_method", "assert_record_access_contract"]
