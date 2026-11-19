"""Which points of a virtual record one indexing write owns.

By default a write owns every point of its ``virtualRecordId``: it deletes and resumes by that id alone and its point ids
carry no salt. A package that stores a record's points in several groups installs a resolver with
:func:`set_scope_resolver`, which hands each write a scope of its own. Nothing registers at import time.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class WriteScope(Protocol):
    """What a write needs to know about the points it owns."""

    content_vrid: str  # whose stored blocks the write indexes; point ids derive from it
    read_vrid: str  # the id of the payload, access and membership
    point_salt: str  # added to the point ids of the write; empty keeps every existing id
    is_scoped: bool  # the write owns a part of its record's points, so a whole-record delete must be refused
    writes_summary: bool  # the write stores the record's summary point
    reconciles: bool  # the write may delete by block id

    def must(self) -> dict[str, Any]:
        """The filter (beyond organisation and block ids) that selects the points this write owns."""

    def payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """``metadata`` as stored on the points of this write."""


@dataclass(frozen=True)
class WholeRecordScope:
    content_vrid: str
    read_vrid: str
    point_salt: str = ""
    is_scoped: bool = False
    writes_summary: bool = True
    reconciles: bool = True

    @classmethod
    def of(cls, vrid: str) -> WholeRecordScope:
        return cls(vrid, vrid)

    def must(self) -> dict[str, Any]:
        return {"virtualRecordId": self.read_vrid}

    def payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {**metadata, "virtualRecordId": self.read_vrid}


ScopeResolver = Callable[[object, Mapping[str, Any] | None, bool], Awaitable[WriteScope]]
"""``(record, ctx, write)``: ``write`` is True when the scope is for a write (an index, a delete or a purge of points) and False for a read."""


async def _whole_record(record: object, ctx: Mapping[str, Any] | None, write: bool) -> WriteScope:  # noqa: FBT001
    vrid = getattr(record, "virtual_record_id", None)
    if not vrid and isinstance(record, Mapping):
        vrid = record.get("virtualRecordId")
    return WholeRecordScope.of(vrid)


_resolver: list[ScopeResolver] = [_whole_record]


def set_scope_resolver(resolver: ScopeResolver) -> None:
    """Install the resolver. Installing the one already installed is a no-op."""
    _resolver[0] = resolver


def reset_scope_resolver() -> None:
    _resolver[0] = _whole_record


async def scope_for(record: object, ctx: Mapping[str, Any] | None = None, *, write: bool = False) -> WriteScope:
    """The scope of ``record``'s points. ``ctx`` is whatever the caller knows about the run (its settings); ``write`` says
    whether the scope is for a write (an index, a delete or a purge) or a read, which a resolver may treat differently."""
    return await _resolver[0](record, ctx, write)
