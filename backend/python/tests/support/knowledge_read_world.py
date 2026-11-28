"""QA-27's org, laid out once for both levels: every matrix cell as a Drive file and as a Slack thread reply.

Alice reads; bob owns everything that is not hers; carol is another publication's audience. A record's container is
its publication column's record group (a Drive folder group, a Slack thread), shared by the column's ownerships.
Every record carries a marker word of its own, so any answer tells which records reached it. The unit level seeds
this layout into ``InMemoryRecordAccess``; the integration level writes it through ``IGraphDBProvider``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import FileRecord, MessageRecord, RecordGroupType, RecordType
from tests.support.knowledge_read_matrix import CELLS, PUBLICATIONS, Cell, Publication

C = CollectionNames
Kind = Literal["drive", "slack"]
NOW: Final = 1_790_000_000_000
# Created and expired long before any clock this runs under: no wall-clock threshold decides it.
EXPIRED_AT: Final = 2_000
MODES: Final[dict[Publication, str]] = {
    "full": "full", "answer_only": "answer_only", "revoked": "full", "expired": "full", "other_audience": "full",
}


@dataclass(frozen=True)
class Seeded:
    cell: Cell
    kind: Kind
    id: str
    external_id: str
    url: str
    name: str
    marker: str
    vrid: str
    container: str | None
    ts: str

    @property
    def content(self) -> str:
        """The record's one block."""
        return f"{self.marker} ledger note."

    def names(self) -> tuple[str, ...]:
        """Everything that names this record in an answer."""
        return (self.id, self.external_id, self.url, self.name, self.marker, self.vrid, self.ts)


@dataclass
class World:
    org_id: str
    suffix: str
    users: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])  # name -> (key, userId)
    records: dict[tuple[Cell, Kind], Seeded] = field(default_factory=dict[tuple[Cell, Kind], Seeded])
    publications: dict[tuple[Publication, Kind], str] = field(default_factory=dict[tuple[Publication, Kind], str])

    @property
    def drive(self) -> str:
        return f"drive-qa27-{self.suffix}"

    @property
    def slack(self) -> str:
        return f"slack-qa27-{self.suffix}"

    @property
    def group(self) -> str:
        return f"grp-eng-{self.suffix}"

    @property
    def channel(self) -> str:
        return f"C{self.suffix.upper()}"

    def user_id(self, name: str) -> str:
        return self.users[name][1]

    def user_key(self, name: str) -> str:
        return self.users[name][0]

    def record(self, cell: Cell, kind: Kind) -> Seeded:
        return self.records[(cell, kind)]

    def publication(self, cell: Cell, kind: Kind) -> str | None:
        return self.publications.get((cell.publication, kind))

    def container(self, publication: Publication, kind: Kind) -> str:
        return f"{kind}-{publication.replace('_', '-')}-{self.suffix}"

    def thread(self, publication: Publication) -> str:
        return f"thread_{self.channel}_{self._thread_ts(publication)}"

    def _thread_ts(self, publication: Publication) -> str:
        return f"17900{PUBLICATIONS.index(publication)}0000.000100"

    def full_record(self, vrid: str) -> dict[str, Any] | None:
        """The record as blob storage returns it."""
        seeded = next((s for s in self.records.values() if s.vrid == vrid), None)
        if seeded is None or seeded.cell.ownership == "missing":
            return None
        return {
            "virtual_record_id": vrid, "id": seeded.id, "_key": seeded.id, "record_name": seeded.name,
            "context_metadata": f"Record ID: {seeded.id}\nRecord: {seeded.name}",
            "block_containers": {"blocks": [{"index": 0, "type": "text", "data": seeded.content}], "block_groups": []},
            "origin": "CONNECTOR",
            "connector_name": (Connectors.GOOGLE_DRIVE if seeded.kind == "drive" else Connectors.SLACK).value,
            "mime_type": "text/plain", "record_type": "FILE" if seeded.kind == "drive" else "MESSAGE", "version": 1,
        }


def named(org_id: str, suffix: str) -> World:
    world = World(org_id, suffix)
    for name in ("alice", "bob", "carol"):
        world.users[name] = (f"uk-{name}-{suffix}", f"{name}-{suffix}")
    for index, cell in enumerate(CELLS):
        for kind in ("drive", "slack"):
            # Every id, link and name has one shape: a lookup's searched trail follows the identifier's shape.
            rid = f"{kind}-r{index:02d}-{suffix}"
            marker = f"{'D' if kind == 'drive' else 'S'}{index:02d}Q{suffix.upper()}"
            ts = f"17900{index:02d}000.{index:06d}"
            world.records[(cell, kind)] = Seeded(
                cell=cell,
                kind=kind,
                id=rid,
                external_id=f"ext-{rid}" if kind == "drive" else ts,
                url=(
                    f"https://drive.example/{suffix}/r{index:02d}"
                    if kind == "drive"
                    else f"https://qa27.slack.com/archives/{world.channel}/p{ts.replace('.', '')}"
                ),
                name=f"{marker}.txt" if kind == "drive" else f"{marker} thread reply",
                marker=marker,
                vrid=f"vr-{rid}",
                container=(
                    world.container(cell.publication, kind)
                    if kind == "slack" or cell.publication != "none"
                    else None
                ),
                ts=ts,
            )
    for publication in PUBLICATIONS[1:]:
        for kind in ("drive", "slack"):
            world.publications[(publication, kind)] = f"pub-{kind}-{publication.replace('_', '-')}-{suffix}"
    return world


def typed_record(world: World, seeded: Seeded) -> FileRecord | MessageRecord:
    """The record as a connector writes it, in the trash for the ``trashed`` row."""
    fields: dict[str, Any] = {
        "id": seeded.id,
        "org_id": world.org_id,
        "record_name": seeded.name,
        "external_record_id": seeded.external_id,
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "weburl": seeded.url,
        "indexing_status": ProgressStatus.COMPLETED.value,
        "mime_type": "text/plain",
        "source_created_at": NOW,
        "source_updated_at": NOW,
    }
    if seeded.cell.ownership == "trashed":
        fields.update(
            is_deleted=True, deleted_at=NOW - 1000, deleted_by_user_id=world.user_key("alice"),
            delete_source=DeleteSource.USER, delete_batch_id=f"batch-{world.suffix}",
        )
    if seeded.kind == "drive":
        return FileRecord(
            **fields, record_type=RecordType.FILE, connector_name=Connectors.GOOGLE_DRIVE, connector_id=world.drive,
            is_file=True, extension="txt", size_in_bytes=len(seeded.content),
            **({"record_group_id": seeded.container} if seeded.container else {}),
        )
    return MessageRecord(
        **fields, record_type=RecordType.MESSAGE, connector_name=Connectors.SLACK, connector_id=world.slack,
        record_group_type=RecordGroupType.SLACK_THREAD, record_group_id=seeded.container,
        external_record_group_id=world.thread(seeded.cell.publication), thread_id=seeded.ts, is_reply=True,
    )


__all__ = ["EXPIRED_AT", "MODES", "NOW", "Kind", "Seeded", "World", "named", "typed_record"]
