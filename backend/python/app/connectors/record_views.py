"""How a record is shown on the connector and knowledge-base surfaces.

The default shows every record as stored. A package that shows a record differently (a status read from several stored
documents, a content source of its own) installs a decorator with :func:`set_decorator`. Nothing registers at import time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class StoredDocument:
    """A document in blob storage that holds a record's content."""

    document_id: str
    mime_type: str


class RecordViewDecorator(Protocol):
    async def decorate_rows(self, rows: list, graph: object, *, surface: str) -> list: ...

    async def decorate_record(self, record: dict, graph: object, *, surface: str) -> dict: ...

    async def decorate_stats(self, stats: dict, graph: object, *, surface: str) -> dict: ...

    async def stream_source(self, record: object, graph: object) -> StoredDocument | None:
        """The stored document to stream for ``record`` instead of asking its connector, or None for the default source.
        May raise ``RecordOwnerUnknown`` (the route answers 404)."""

    async def reindex_connector_extra(
        self, connector: object, graph: object, *, org_id: str, connector_id: str, statuses: list[str], user_key: str | None
    ) -> int:
        """Re-index records of ``connector_id`` that its own listing does not reach. Returns how many were queued."""


class DefaultRecordViewDecorator:
    async def decorate_rows(self, rows: list, graph: object, *, surface: str) -> list:
        return rows

    async def decorate_record(self, record: dict, graph: object, *, surface: str) -> dict:
        return record

    async def decorate_stats(self, stats: dict, graph: object, *, surface: str) -> dict:
        return stats

    async def stream_source(self, record: object, graph: object) -> StoredDocument | None:
        return None

    async def reindex_connector_extra(
        self, connector: object, graph: object, *, org_id: str, connector_id: str, statuses: list[str], user_key: str | None
    ) -> int:
        return 0


_decorator: list[RecordViewDecorator] = [DefaultRecordViewDecorator()]


def set_decorator(decorator: RecordViewDecorator) -> None:
    """Install the decorator. Installing the one already installed is a no-op."""
    _decorator[0] = decorator


def reset_decorator() -> None:
    _decorator[0] = DefaultRecordViewDecorator()


def views() -> RecordViewDecorator:
    return _decorator[0]


__all__ = ["StoredDocument", "DefaultRecordViewDecorator", "RecordViewDecorator", "reset_decorator", "set_decorator", "views"]
