"""A Zoom full sync, run as run_sync_task runs it, unlinks only what is gone from Zoom.

The runner unlinks every stored node a full sync neither wrote nor noted kept, unless
the connector's full_sync_coverage() says the run read less than everything. These
tests store what an earlier sync wrote, run a full sync against a fake Zoom, and
count what the runner would then unlink:

- wrongly unlinked: stored nodes still in Zoom that lose their links or grants;
- really gone: stored meetings deleted in Zoom that are (correctly) unlinked.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.sources.zoom.connector import ZoomConnector
from app.services.graph_db.common.full_sync_unlink import (
    split_unseen_by_window,
    unseen_node_keys,
)
from app.sources.client.zoom.zoom import ZoomResponse

TODAY = date(2026, 11, 24)
CONNECTOR_ID = "zoom-conn-1"
RECORDS = CollectionNames.RECORDS.value
RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value


def _start(days_ago: int) -> str:
    return f"{(TODAY - timedelta(days=days_ago)).isoformat()}T10:00:00Z"


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _failed(status: int) -> ZoomResponse:
    return ZoomResponse(success=False, status_code=status, message=f"Failed with status {status}")


@dataclass
class Meeting:
    uuid: str
    number: int
    host: str
    days_ago: int
    alt_hosts: list[str] = field(default_factory=list)
    invitees: list[str] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)

    @property
    def start_time(self) -> str:
        return _start(self.days_ago)

    def grants(self, host_email: str) -> set[str]:
        return {host_email, *self.alt_hosts, *self.invitees, *self.participants}


class FakeZoom:
    """The Zoom API the connector reads, with failures injected per call."""

    def __init__(self, users: list[tuple[str, str]], meetings: list[Meeting]) -> None:
        self.users_list = users
        self.meetings = {m.uuid: m for m in meetings}
        self.failing_user_pages: set[int] = set()
        self.failing_report_ranges: set[tuple[str, str, str]] = set()
        self.failing_details: set[str] = set()
        self.failing_participants: set[str] = set()
        self.created_at: dict[str, str] = {}

    async def user(self, user_id: str) -> ZoomResponse:
        return ZoomResponse(success=True, data={"id": "admin", "email": "admin@acme.example"})

    async def users(self, status: str, page_size: int, next_page_token: Optional[str] = None) -> ZoomResponse:
        page = int(next_page_token or 0)
        if page in self.failing_user_pages:
            return _failed(429)
        user_id, email = self.users_list[page]
        has_more = page + 1 < len(self.users_list)
        return ZoomResponse(success=True, data={
            "users": [{"id": user_id, "email": email, "first_name": user_id,
                       "created_at": self.created_at.get(user_id)}],
            "next_page_token": str(page + 1) if has_more else "",
        })

    async def report_meetings(
        self, userId: str, from_: str, to: str, page_size: int, next_page_token: Optional[str] = None
    ) -> ZoomResponse:
        if (userId, from_, to) in self.failing_report_ranges:
            return _failed(429)
        listed = [
            {"uuid": m.uuid, "id": m.number, "topic": m.uuid, "host_id": m.host,
             "start_time": m.start_time, "end_time": m.start_time}
            for m in self.meetings.values()
            if m.host == userId and from_ <= m.start_time[:10] <= to
        ]
        return ZoomResponse(success=True, data={"meetings": listed, "next_page_token": ""})

    def _by_number(self, number: str) -> Meeting:
        return next(m for m in self.meetings.values() if str(m.number) == number)

    async def meeting(self, meeting_id: str) -> ZoomResponse:
        if meeting_id in self.failing_details:
            return _failed(429)
        m = self._by_number(meeting_id)
        return ZoomResponse(success=True, data={"id": m.number, "settings": {
            "alternative_hosts": ";".join(m.alt_hosts),
            "meeting_invitees": [{"email": e} for e in m.invitees],
        }})

    async def recording_get(self, meeting_id: str) -> ZoomResponse:
        return _failed(404)

    async def report_meeting_participants(
        self, meetingId: str, page_size: int, next_page_token: Optional[str] = None
    ) -> ZoomResponse:
        m = next(m for m in self.meetings.values() if ZoomConnector._encode_uuid(m.uuid) == meetingId)
        if m.uuid in self.failing_participants:
            return _failed(429)
        return ZoomResponse(success=True, data={
            "participants": [{"user_email": e} for e in m.participants], "next_page_token": "",
        })


@dataclass
class StoredNode:
    id: str
    collection: str
    created_ms: Optional[int] = None
    record_group_id: Optional[str] = None
    grants: set[str] = field(default_factory=set)
    links: bool = True


class FakeStore:
    """What earlier syncs stored, written to as the processor writes, noting what a full sync sees."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.nodes: dict[str, StoredNode] = {}  # by external id
        self.seen: Optional[FullSyncSeen] = None
        self.failing_record_writes_for: set[str] = set()
        self.failing_group_reads_for: set[str] = set()
        self.group_reads: list[tuple[str, Optional[str]]] = []

    def store_group(self, user_id: str, email: str) -> None:
        self.nodes[user_id] = StoredNode(id=f"rg-{user_id}", collection=RECORD_GROUPS, grants={email})

    def store_meeting(self, meeting: Meeting, host_email: str) -> None:
        self.nodes[meeting.uuid] = StoredNode(
            id=f"rec-{meeting.uuid}", collection=RECORDS, created_ms=_ms(meeting.start_time),
            record_group_id=f"rg-{meeting.host}", grants=meeting.grants(host_email),
        )

    def _write(
        self, external_id: str, collection: str, grants: set[str], *, adds: bool = False, **fields: Optional[int | str]
    ) -> None:
        node = self.nodes.get(external_id)
        if node is None:
            node = self.nodes[external_id] = StoredNode(id=f"new-{external_id}", collection=collection, **fields)
        # As the processor does, a record that adds_permissions never replaces the stored grants.
        node.grants = node.grants | grants if adds else grants
        node.links = True
        if self.seen is not None:
            self.seen.add(collection, node.id)

    async def on_new_app_users(self, users: list[Any]) -> None:
        return None

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, perms in groups:
            self._write(group.external_group_id, RECORD_GROUPS, {p.email for p in perms})

    async def on_new_records(self, records: list[tuple[Any, list[Any]]]) -> None:
        if any(r.external_record_group_id in self.failing_record_writes_for for r, _ in records):
            raise RuntimeError("store write failed")
        for record, perms in records:
            self._write(
                record.external_record_id, RECORDS, {p.email for p in perms}, adds=record.adds_permissions,
                created_ms=record.source_created_at, record_group_id=record.record_group_id,
            )

    def full_sync_running(self, connector_id: str) -> bool:
        return self.seen is not None

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: Optional[str] = None
    ) -> list[MagicMock]:
        if external_group_id in self.failing_group_reads_for:
            raise RuntimeError("store read failed")
        self.group_reads.append((external_group_id, after_key))
        in_group = sorted(
            (n for n in self.nodes.values()
             if n.collection == RECORDS and n.record_group_id == f"rg-{external_group_id}"),
            key=lambda n: n.id,
        )
        page = [n for n in in_group if after_key is None or n.id > after_key][:limit]
        return [MagicMock(id=n.id, source_created_at=n.created_ms) for n in page]

    def note_kept_records(self, connector_id: str, record_ids: list[str]) -> None:
        for record_id in record_ids:
            if self.seen is not None:
                self.seen.add(RECORDS, record_id)

    async def note_kept_record_group(self, connector_id: str, external_group_id: str) -> None:
        node = self.nodes.get(external_group_id)
        if node is not None and self.seen is not None:
            self.seen.add(RECORD_GROUPS, node.id)

    def begin_full_sync_run(self, connector_id: str) -> FullSyncSeen:
        self.seen = FullSyncSeen()
        return self.seen

    def end_full_sync_run(self, connector_id: str, run: FullSyncSeen) -> None:
        self.seen = None

    async def unlink(
        self, connector_id: str, seen: dict[str, set[str]], *,
        window_start_ms: Optional[int] = None, windowed_group_types: frozenset[str] = frozenset(), **_: object,
    ) -> tuple[int, bool]:
        """What unlink_nodes_missed_by_full_sync does: unseen nodes lose every edge, and
        those outside the window keep their structure but lose their grants."""
        by_id = {node.id: node for node in self.nodes.values()}
        collected = {
            "record_keys": [n.id for n in by_id.values() if n.collection == RECORDS],
            "record_group_keys": [n.id for n in by_id.values() if n.collection == RECORD_GROUPS],
        }
        unseen = unseen_node_keys(collected, seen)
        record_fields = {
            key: {"sourceCreatedAtTimestamp": by_id[key].created_ms, "recordGroupId": by_id[key].record_group_id}
            for key in unseen[RECORDS]
        }
        group_fields = {key: {"sourceCreatedAtTimestamp": None, "groupType": "USER_GROUP"} for key in unseen[RECORD_GROUPS]}
        unlink, outside_window = split_unseen_by_window(
            unseen, record_fields, group_fields, window_start_ms, windowed_group_types
        )
        for node_id in unlink:
            node = by_id[node_id.split("/", 1)[1]]
            node.links, node.grants = False, set()
        for node_id in outside_window:
            by_id[node_id.split("/", 1)[1]].grants = set()
        return len(unlink) + len(outside_window), True


class FakeTxStore:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Optional[MagicMock]:
        node = self.store.nodes.get(external_id)
        return MagicMock(id=node.id) if node and node.collection == RECORDS else None

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[MagicMock]:
        node = self.store.nodes.get(external_id)
        return MagicMock(id=node.id) if node and node.collection == RECORD_GROUPS else None


class FakeDataStoreProvider:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    def transaction(self) -> Any:  # noqa: ANN401
        tx = FakeTxStore(self.store)

        class _Ctx:
            async def __aenter__(self) -> FakeTxStore:
                return tx

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


@dataclass
class World:
    zoom: FakeZoom
    store: FakeStore
    emails: dict[str, str]
    # Meetings whose record build raises inside the connector.
    failing_meeting_builds: set[str] = field(default_factory=set)
    # The stored state before the sync: external id -> (had links, grants).
    before: dict[str, tuple[bool, set[str]]] = field(default_factory=dict)

    def snapshot(self) -> None:
        self.before = {k: (n.links, set(n.grants)) for k, n in self.store.nodes.items()}

    def still_in_zoom(self, external_id: str) -> bool:
        return external_id in self.zoom.meetings or external_id in self.emails

    def wrongly_unlinked(self) -> int:
        """Stored nodes still in Zoom that lost their links or all their grants."""
        lost = 0
        for external_id, (linked, grants) in self.before.items():
            node = self.store.nodes[external_id]
            if self.still_in_zoom(external_id) and linked and grants and (not node.links or not node.grants):
                lost += 1
        return lost

    def wrongly_revoked_grants(self) -> int:
        """Grants of stored meetings still valid in Zoom that the run removed."""
        lost = 0
        for external_id, (_, grants) in self.before.items():
            meeting = self.zoom.meetings.get(external_id)
            if meeting is not None:
                valid = grants & meeting.grants(self.emails[meeting.host])
                lost += len(valid - self.store.nodes[external_id].grants)
        return lost

    def really_gone_unlinked(self) -> int:
        return sum(
            1 for external_id, (linked, _) in self.before.items()
            if linked and not self.still_in_zoom(external_id) and not self.store.nodes[external_id].links
        )


def _world(meetings: list[Meeting], *, gone: list[Meeting] = ()) -> World:
    emails = {"ann": "ann@acme.example", "bob": "bob@acme.example"}
    zoom = FakeZoom(list(emails.items()), meetings)
    store = FakeStore()
    for user_id, email in emails.items():
        store.store_group(user_id, email)
    for meeting in [*meetings, *gone]:
        store.store_meeting(meeting, emails[meeting.host])
    world = World(zoom, store, emails)
    world.snapshot()
    return world


def _connector(world: World) -> ZoomConnector:
    connector = ZoomConnector(
        logger=logging.getLogger("test.zoom.full_sync"),
        data_entities_processor=world.store,  # type: ignore[arg-type]
        data_store_provider=FakeDataStoreProvider(world.store),  # type: ignore[arg-type]
        config_service=AsyncMock(),
        connector_id=CONNECTOR_ID,
        scope="team",
        created_by="admin",
    )
    connector.data_source = world.zoom  # type: ignore[assignment]
    connector.external_client = MagicMock()
    connector._get_fresh_datasource = AsyncMock(return_value=world.zoom)  # type: ignore[method-assign]
    connector.register_authenticated_source_user = AsyncMock()  # type: ignore[method-assign]
    connector._load_filters = AsyncMock()  # type: ignore[method-assign]
    connector._today = MagicMock(return_value=TODAY)  # type: ignore[method-assign]
    # A full sync starts from reset sync points.
    connector._get_user_meeting_sync_point = AsyncMock(return_value=None)  # type: ignore[method-assign]
    connector._update_user_meeting_sync_point = AsyncMock()  # type: ignore[method-assign]
    build_meeting_record = connector._build_meeting_record

    def _build(**kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs["meeting_uuid"] in world.failing_meeting_builds:
            raise ValueError("record build failed")
        return build_meeting_record(**kwargs)

    connector._build_meeting_record = _build  # type: ignore[method-assign]
    return connector


async def _full_sync(world: World) -> ZoomConnector:
    connector = _connector(world)
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={"id": CONNECTOR_ID, "status": AppStatus.FULL_SYNCING.value})
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=world.store.unlink)
    await sync_runner.run_sync_task(
        connector, CONNECTOR_ID, graph, logging.getLogger("test.zoom.full_sync"),
        start_status=AppStatus.FULL_SYNCING.value,
    )
    return connector


@pytest.fixture(autouse=True)
def _no_owed_retry() -> None:
    sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(CONNECTOR_ID)


def _recent(uuid: str, number: int, host: str = "ann", days_ago: int = 10, **kw: list[str]) -> Meeting:
    return Meeting(uuid=uuid, number=number, host=host, days_ago=days_ago, participants=["guest@acme.example"], **kw)


@pytest.mark.asyncio
class TestZoomFullSyncUnlinksOnlyWhatIsGone:
    async def test_control_a_meeting_deleted_in_zoom_is_unlinked(self) -> None:
        world = _world([_recent("m-recent", 1)], gone=[_recent("m-gone", 2, days_ago=20)])

        connector = await _full_sync(world)

        assert world.wrongly_unlinked() == 0
        assert world.really_gone_unlinked() == 1
        assert connector.full_sync_coverage().complete is True

    async def test_a_meeting_older_than_the_report_window_keeps_its_links_and_grants(self) -> None:
        """Zoom's report API lists only the last six months; a meeting older than that is
        still in Zoom, and still readable by its host and participants."""
        old = _recent("m-old", 3, days_ago=200)
        world = _world([_recent("m-recent", 1), old], gone=[_recent("m-gone", 2, days_ago=20)])

        await _full_sync(world)

        assert world.wrongly_unlinked() == 0
        assert world.store.nodes["m-old"].grants == {"ann@acme.example", "guest@acme.example"}
        assert world.really_gone_unlinked() == 1

    async def test_a_user_page_that_fails_leaves_that_users_meetings_linked(self) -> None:
        world = _world(
            [_recent("m-ann", 1), _recent("m-bob-1", 4, host="bob"), _recent("m-bob-2", 5, host="bob", days_ago=40)],
            gone=[_recent("m-gone", 2, days_ago=20)],
        )
        world.zoom.failing_user_pages = {1}

        await _full_sync(world)
        assert world.wrongly_unlinked() == 0

        world.zoom.failing_user_pages = set()
        await _full_sync(world)
        assert world.really_gone_unlinked() == 1

    async def test_the_first_users_page_failing_unlinks_nothing(self) -> None:
        world = _world(
            [_recent("m-ann", 1), _recent("m-bob", 4, host="bob")],
            gone=[_recent("m-gone", 2, days_ago=20)],
        )
        world.zoom.failing_user_pages = {0}

        connector = await _full_sync(world)

        assert world.wrongly_unlinked() == 0
        assert world.really_gone_unlinked() == 0
        assert connector.full_sync_coverage().complete is False

    async def test_a_meeting_whose_processing_raises_keeps_its_links(self) -> None:
        world = _world([_recent("m-1", 1), _recent("m-2", 3)], gone=[_recent("m-gone", 2, days_ago=20)])
        world.failing_meeting_builds = {"m-1"}

        await _full_sync(world)
        assert world.wrongly_unlinked() == 0
        assert world.store.nodes["m-1"].grants == {"ann@acme.example", "guest@acme.example"}

        world.failing_meeting_builds = set()
        await _full_sync(world)
        assert world.really_gone_unlinked() == 1

    async def test_a_meeting_list_that_fails_leaves_its_meetings_linked(self) -> None:
        world = _world(
            [_recent("m-ann-new", 1, days_ago=5), _recent("m-ann-old", 6, days_ago=100), _recent("m-bob", 4, host="bob")],
            gone=[_recent("m-gone", 2, host="bob", days_ago=20)],
        )
        newest = (TODAY - timedelta(days=180 - 30 * 5)).isoformat(), TODAY.isoformat()
        world.zoom.failing_report_ranges = {("ann", *newest)}

        await _full_sync(world)
        assert world.wrongly_unlinked() == 0

        world.zoom.failing_report_ranges = set()
        await _full_sync(world)
        assert world.really_gone_unlinked() == 1

    async def test_a_user_whose_sync_fails_keeps_its_meetings_linked(self) -> None:
        world = _world(
            [_recent("m-ann", 1), _recent("m-bob-1", 4, host="bob"), _recent("m-bob-2", 5, host="bob", days_ago=40)],
            gone=[_recent("m-gone", 2, days_ago=20)],
        )
        world.store.failing_record_writes_for = {"bob"}

        await _full_sync(world)
        assert world.wrongly_unlinked() == 0

        world.store.failing_record_writes_for = set()
        await _full_sync(world)
        assert world.really_gone_unlinked() == 1

    async def test_a_participants_read_that_fails_keeps_the_stored_grants(self) -> None:
        world = _world([_recent("m-1", 1)], gone=[_recent("m-gone", 2, days_ago=20)])
        world.zoom.failing_participants = {"m-1"}

        await _full_sync(world)

        assert world.wrongly_revoked_grants() == 0
        assert world.really_gone_unlinked() == 1

    async def test_a_meeting_detail_read_that_fails_keeps_the_stored_grants(self) -> None:
        world = _world(
            [_recent("m-1", 1, alt_hosts=["bob@acme.example"], invitees=["ext@partner.example"])],
            gone=[_recent("m-gone", 2, days_ago=20)],
        )
        world.zoom.failing_details = {"1"}

        await _full_sync(world)

        assert world.wrongly_revoked_grants() == 0
        assert world.really_gone_unlinked() == 1

    async def test_a_grant_removed_in_zoom_is_revoked_when_the_acl_reads_in_full(self) -> None:
        world = _world([_recent("m-1", 1)], gone=[_recent("m-gone", 2, days_ago=20)])
        world.store.nodes["m-1"].grants.add("old@partner.example")

        await _full_sync(world)

        assert world.store.nodes["m-1"].grants == {"ann@acme.example", "guest@acme.example"}
        assert world.really_gone_unlinked() == 1

    async def test_a_meeting_deleted_in_zoom_inside_the_read_range_is_still_unlinked(self) -> None:
        world = _world(
            [_recent("m-recent", 1), _recent("m-old", 3, days_ago=200)],
            gone=[_recent("m-gone-179", 2, days_ago=179), _recent("m-gone-20", 4, days_ago=20)],
        )

        await _full_sync(world)

        assert world.wrongly_unlinked() == 0
        assert world.really_gone_unlinked() == 2
        assert world.store.nodes["m-old"].links is True

    async def test_a_meeting_on_the_window_boundary_day_is_kept_if_not_listed(self) -> None:
        """Zoom may read the report's from date in the account's timezone, so a meeting
        early on the first day read may not be listed although it is still in Zoom."""
        world = _world([_recent("m-recent", 1)], gone=[_recent("m-boundary", 2, days_ago=180)])
        read_from = TODAY - timedelta(days=180)
        world.store.nodes["m-boundary"].created_ms = _ms(f"{read_from.isoformat()}T03:00:00Z")

        await _full_sync(world)

        node = world.store.nodes["m-boundary"]
        assert node.links is True
        assert node.grants == {"ann@acme.example", "guest@acme.example"}

    async def test_a_meeting_a_day_past_the_boundary_and_gone_is_unlinked(self) -> None:
        world = _world([_recent("m-recent", 1)], gone=[_recent("m-gone", 2, days_ago=178)])

        await _full_sync(world)

        assert world.really_gone_unlinked() == 1

    async def test_a_stored_meeting_without_a_start_time_is_not_kept(self) -> None:
        world = _world([_recent("m-recent", 1)], gone=[_recent("m-gone", 2, days_ago=200)])
        world.store.nodes["m-gone"].created_ms = None

        await _full_sync(world)

        assert world.really_gone_unlinked() == 1

    async def test_a_failed_stored_meeting_read_marks_the_run_incomplete(self) -> None:
        world = _world(
            [_recent("m-ann", 1), _recent("m-bob", 4, host="bob")],
            gone=[_recent("m-gone", 2, days_ago=20)],
        )
        world.store.failing_group_reads_for = {"ann"}

        connector = await _full_sync(world)

        assert connector.full_sync_coverage().complete is False
        assert world.wrongly_unlinked() == 0
        assert world.really_gone_unlinked() == 0
        assert ("bob", None) in world.store.group_reads
        assert world.store.nodes["m-bob"].links is True

    async def test_a_user_created_recently_keeps_nothing_it_did_not_have(self) -> None:
        world = _world([_recent("m-recent", 1, days_ago=2)], gone=[_recent("m-gone", 2, days_ago=5)])
        world.zoom.created_at["ann"] = f"{(TODAY - timedelta(days=10)).isoformat()}T00:00:00Z"

        await _full_sync(world)

        assert world.really_gone_unlinked() == 1
