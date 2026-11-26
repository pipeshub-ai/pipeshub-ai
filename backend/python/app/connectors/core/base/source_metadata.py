"""Source metadata an edition reads onto the records a connector writes, kept in ``Record.edition_fields``.

Reading it is an extension point, ``SourceMetadataReader``, read through ``harness_edition().port``. The connectors
call it at the points where a source can say more about an item (a Drive file's metadata, a OneDrive or SharePoint
item, a Box entry, a Confluence page, an Outlook mail); what is read and under which keys is the edition's. This
build's default reads nothing: a record keeps the fields it holds, and a new record has none. A read never fails a
sync: a source that cannot say leaves the record as it was.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Final, Protocol

from app.models.entities import EditionFields, clean_edition_fields

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from logging import Logger

    from app.models.entities import Record


class DriveMetadata(Protocol):
    """One Drive sync's reads: the fields a file's metadata gives."""

    def fields_for(self, metadata: Mapping[str, Any], held: EditionFields) -> EditionFields: ...


class ItemMetadata(Protocol):
    """One OneDrive or SharePoint sync's reads of its files' fields."""

    def new_sync(self) -> None: ...

    async def fields_for(self, drive_id: str, item_id: str, held: EditionFields, *, changed: bool) -> EditionFields: ...


class ContentMetadata(Protocol):
    """One Confluence sync's reads of its pages' and blog posts' fields."""

    def new_sync(self) -> None: ...

    async def fields_for(
        self, datasource: Any, content_type: str, item_id: str, space_id: str | None, held: EditionFields  # noqa: ANN401
    ) -> EditionFields: ...


class SourceMetadataReader(ABC):
    """What a connector reads of an item beyond its own model. Each answer is the item's complete edition fields
    given those the record holds (``held``); a key whose value is ``None`` is one the item no longer has."""

    @abstractmethod
    async def drive_metadata(
        self, build_service: Callable[[Sequence[str]], Awaitable[Any]], logger: Logger
    ) -> DriveMetadata:
        """A Drive sync's reads, prepared once per sync through a service ``build_service`` makes with the scopes
        it is given. Never raises: a read that fails keeps every record's fields."""

    @abstractmethod
    def drive_source(self, source: Any, metadata: Callable[[], DriveMetadata]) -> Any:  # noqa: ANN401
        """``source`` (a Drive data source), asking Drive for what the sync's reads need where it needs it."""

    @abstractmethod
    def item_metadata(self, client: Callable[[], Any], logger: Logger) -> ItemMetadata:
        """A OneDrive or SharePoint sync's reads, through the connector's current Graph client (``client``)."""

    def box_fields(self) -> tuple[str, ...]:
        """The fields a Box listing and file read add for ``box_metadata``; none here."""
        return ()

    def box_metadata(self, entry: Mapping[str, object], held: EditionFields) -> EditionFields:
        """A Box file's fields, from an entry read with ``box_fields``; here the ones the record holds."""
        return held

    def confluence_metadata(self, logger: Logger) -> ContentMetadata:
        """A Confluence sync's reads; here none."""
        return _HeldContentMetadata()

    def mail_expand(self) -> tuple[str, ...]:
        """What an Outlook folder's delta read expands for ``mail_metadata``; none here, so nothing is read."""
        return ()

    def mail_metadata(self, message: object, held: EditionFields) -> EditionFields:
        """A mail's fields, from a message read with ``mail_expand``; here the ones the record holds."""
        return held


class _HeldDriveMetadata:
    def fields_for(self, metadata: Mapping[str, Any], held: EditionFields) -> EditionFields:
        return held


class _HeldItemMetadata:
    def new_sync(self) -> None:
        return None

    async def fields_for(self, drive_id: str, item_id: str, held: EditionFields, *, changed: bool) -> EditionFields:
        return held


class _HeldContentMetadata:
    def new_sync(self) -> None:
        return None

    async def fields_for(
        self, datasource: Any, content_type: str, item_id: str, space_id: str | None, held: EditionFields  # noqa: ANN401
    ) -> EditionFields:
        return held


HELD_DRIVE_METADATA: Final = _HeldDriveMetadata()


class KeepHeldMetadata(SourceMetadataReader):
    """Reads nothing: every record keeps the fields it holds, and nothing is asked of the source."""

    async def drive_metadata(
        self, build_service: Callable[[Sequence[str]], Awaitable[Any]], logger: Logger
    ) -> DriveMetadata:
        return HELD_DRIVE_METADATA

    def drive_source(self, source: Any, metadata: Callable[[], DriveMetadata]) -> Any:  # noqa: ANN401
        return source

    def item_metadata(self, client: Callable[[], Any], logger: Logger) -> ItemMetadata:
        return _HeldItemMetadata()


KEEP_HELD_METADATA: Final = KeepHeldMetadata()


def source_metadata() -> SourceMetadataReader:
    """This process's source metadata reader."""
    from app.edition_services import harness_edition

    return harness_edition().port(SourceMetadataReader, KEEP_HELD_METADATA)


def held_metadata(existing: Record | None) -> EditionFields:
    """The edition fields the stored record holds (none for a new record)."""
    return clean_edition_fields(getattr(existing, "edition_fields", None))


def metadata_changed(existing: Record | None, fields: Mapping[str, Any]) -> bool:
    """Whether ``fields`` differ from what the stored record holds: a change the record is written again for."""
    return existing is not None and held_metadata(existing) != clean_edition_fields(fields)


def apply_source_metadata(record: Record, existing: Record | None, fields: Mapping[str, Any]) -> bool:
    """Sets ``fields`` on ``record`` and, for a key the stored record holds that the source no longer gives, a
    ``None`` that clears it (the upserts merge, so an omitted key would keep the old value). Answers whether the
    fields changed."""
    record.edition_fields = {
        **dict.fromkeys(held_metadata(existing)),
        **clean_edition_fields(fields),
    } or None
    return metadata_changed(existing, fields)


__all__ = [
    "HELD_DRIVE_METADATA",
    "KEEP_HELD_METADATA",
    "ContentMetadata",
    "DriveMetadata",
    "ItemMetadata",
    "KeepHeldMetadata",
    "SourceMetadataReader",
    "apply_source_metadata",
    "held_metadata",
    "metadata_changed",
    "source_metadata",
]
