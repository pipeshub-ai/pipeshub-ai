"""Membership and VRID deletes across more than one collection.

Under `single` every one of these degenerates to today's behaviour — which is
half the point: the same code path serves both, so the multi-collection case is
not a separate branch that only runs in Enterprise and rots.

The other half is the failure these tests pin down. Before the locator, every
VRID-scoped write and delete resolved `RecordContext(org_id="")` to get "the"
collection. Under a strategy that names collections from record context that
resolves to a collection which does not exist, so the write lands nowhere and
the delete removes nothing — silently, with the vectors left searchable.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_locator import VirtualRecordCollectionLocator
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record,
    sync_vector_membership,
)
from tests.support.vector_db import make_manifest_store
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)

pytestmark = pytest.mark.asyncio


def _locator(managed=("google_drive_records", "slack_records")):
    return VirtualRecordCollectionLocator(
        strategy=PerConnectorTypeStrategy(),
        manifest_store=make_manifest_store(managed),
        logger=MagicMock(),
    )


def _record(key: str, connector_id: str, connector_name: str) -> dict:
    return {
        "_key": key,
        "orgId": "org-1",
        "connectorId": connector_id,
        "connectorName": connector_name,
        "recordGroupId": f"rg-{connector_id}",
    }


def _graph(records):
    """A graph provider serving these records for one VRID."""
    gp = AsyncMock()
    gp.get_records_by_virtual_record_id = AsyncMock(
        return_value=[r["_key"] for r in records]
    )
    by_key = {r["_key"]: r for r in records}
    gp.get_document = AsyncMock(side_effect=lambda key, _c: by_key.get(key))
    gp.get_edges_from_node = AsyncMock(return_value=[])
    gp.delete_nodes = AsyncMock()
    return gp


def _vdb():
    vdb = AsyncMock()
    vdb.filter_collection = AsyncMock(return_value={"vrid": "vr-1"})
    return vdb


class TestSyncFansOut:
    async def test_writes_membership_to_every_collection_the_vrid_occupies(self):
        """One VRID indexed from two connectors lives in two collections; a
        rewrite that touched only one would leave the other advertising
        membership the graph no longer agrees with."""
        gp = _graph(
            [
                _record("rec-a", "conn-drive", "GOOGLE_DRIVE"),
                _record("rec-b", "conn-slack", "SLACK"),
            ]
        )
        vdb = _vdb()

        await sync_vector_membership(vdb, _locator(), gp, "vr-1", MagicMock())

        targets = {c.args[0] for c in vdb.set_payload.await_args_list}
        assert targets == {"google_drive_records", "slack_records"}

    async def test_writes_the_same_membership_arrays_to_each(self):
        gp = _graph(
            [
                _record("rec-a", "conn-drive", "GOOGLE_DRIVE"),
                _record("rec-b", "conn-slack", "SLACK"),
            ]
        )
        vdb = _vdb()

        await sync_vector_membership(vdb, _locator(), gp, "vr-1", MagicMock())

        payloads = [c.args[1] for c in vdb.set_payload.await_args_list]
        assert all(
            set(p[CONNECTOR_IDS_FIELD]) == {"conn-drive", "conn-slack"} for p in payloads
        )
        assert all(RECORD_GROUP_IDS_FIELD in p for p in payloads)

    async def test_single_connector_writes_exactly_one_collection(self):
        gp = _graph([_record("rec-a", "conn-drive", "GOOGLE_DRIVE")])
        vdb = _vdb()

        await sync_vector_membership(vdb, _locator(), gp, "vr-1", MagicMock())

        vdb.set_payload.assert_awaited_once()
        assert vdb.set_payload.await_args.args[0] == "google_drive_records"

    async def test_no_extra_graph_round_trips_versus_the_arrays_alone(self):
        """Resolving collections must reuse the documents membership already
        fetched — that economy is the entire justification for the design."""
        records = [
            _record("rec-a", "conn-drive", "GOOGLE_DRIVE"),
            _record("rec-b", "conn-slack", "SLACK"),
        ]
        gp = _graph(records)

        await sync_vector_membership(_vdb(), _locator(), gp, "vr-1", MagicMock())

        assert gp.get_document.await_count == len(records)
        assert gp.get_records_by_virtual_record_id.await_count == 1

    async def test_empty_membership_writes_nothing_anywhere(self):
        """A graph that returns nothing is a lagging read, not a record that
        belongs to no connector; blanking membership has no repair path."""
        gp = _graph([])
        vdb = _vdb()

        await sync_vector_membership(vdb, _locator(), gp, "vr-1", MagicMock())

        vdb.set_payload.assert_not_awaited()


class TestRewriteOrDelete:
    async def test_rewrites_where_records_remain(self):
        """The last record in one collection went away, another still exists.

        Membership is rewritten only where a record remains. The collection
        that lost its last record is purged rather than left holding points
        nothing references — see TestStaleCollectionPurge for why.
        """
        gp = _graph([_record("rec-b", "conn-slack", "SLACK")])
        vdb = _vdb()

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(), gp, "vr-1", MagicMock()
        )

        assert outcome == "rewritten"
        assert vdb.set_payload.await_args.args[0] == "slack_records"

    async def test_orphaned_vrid_is_deleted_from_every_managed_collection(self):
        """No record references it anywhere, so there is nothing to resolve a
        collection from — and nothing that could still want the points."""
        gp = _graph([])
        vdb = _vdb()

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(), gp, "vr-1", MagicMock()
        )

        assert outcome == "deleted"
        targets = {c.kwargs["collection_name"] for c in vdb.delete_points.await_args_list}
        assert targets == {"google_drive_records", "slack_records"}

    async def test_orphan_delete_still_drops_the_mapping_row(self):
        gp = _graph([])

        await rewrite_or_delete_virtual_record(
            _vdb(), _locator(), gp, "vr-1", MagicMock()
        )

        gp.delete_nodes.assert_awaited_once()

    async def test_nothing_managed_deletes_nothing_and_still_reports_deleted(self):
        """A fresh deployment with no collections yet: the mapping cleanup must
        still run, or the VRID is swept forever."""
        gp = _graph([])
        vdb = _vdb()
        locator = _locator(managed=())

        outcome = await rewrite_or_delete_virtual_record(
            vdb, locator, gp, "vr-1", MagicMock()
        )

        assert outcome == "deleted"
        vdb.delete_points.assert_not_awaited()
        gp.delete_nodes.assert_awaited_once()

    async def test_missing_locator_is_a_skip_not_a_crash(self):
        outcome = await rewrite_or_delete_virtual_record(
            _vdb(), None, _graph([]), "vr-1", MagicMock()
        )
        assert outcome == "skipped"


class TestStaleCollectionPurge:
    """A VRID whose collection set shrank must not leave points behind.

    Only reachable because deduplication lets one VRID be indexed into several
    collections: the same file arriving through Drive and through Slack shares
    a content identity but gets vectors in each connector type's collection.
    Deleting the Drive record leaves a Slack record, so the *rewrite* branch
    runs — and without a purge the Drive collection keeps points for a record
    that no longer exists, still searchable and citing a deleted record.
    """

    async def test_points_are_removed_from_the_collection_that_lost_its_record(self):
        gp = _graph([_record("rec-b", "conn-slack", "SLACK")])
        vdb = _vdb()

        outcome = await rewrite_or_delete_virtual_record(
            vdb, _locator(), gp, "vr-1", MagicMock()
        )

        assert outcome == "rewritten"
        assert vdb.set_payload.await_args.args[0] == "slack_records"
        purged = {c.kwargs["collection_name"] for c in vdb.delete_points.await_args_list}
        assert purged == {"google_drive_records"}

    async def test_nothing_is_purged_while_every_collection_keeps_a_record(self):
        gp = _graph(
            [
                _record("rec-a", "conn-drive", "GOOGLE_DRIVE"),
                _record("rec-b", "conn-slack", "SLACK"),
            ]
        )
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(vdb, _locator(), gp, "vr-1", MagicMock())

        vdb.delete_points.assert_not_awaited()

    async def test_single_collection_never_purges(self):
        """Under `single` the managed set and the live set are the same one
        collection, so this path costs nothing and does nothing."""
        from app.services.vector_db.collection_locator import StaticCollectionLocator

        gp = _graph([_record("rec-a", "conn-1", "GOOGLE_DRIVE")])
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(
            vdb, StaticCollectionLocator(["records"]), gp, "vr-1", MagicMock()
        )

        vdb.delete_points.assert_not_awaited()

    async def test_a_partial_graph_read_does_not_delete_live_points(self):
        """Deleting is irreversible, so a non-empty stale set is confirmed
        against a second read. Here the re-read shows the Drive record after
        all — its points must survive."""
        reads = {"n": 0}
        drive = _record("rec-a", "conn-drive", "GOOGLE_DRIVE")
        slack = _record("rec-b", "conn-slack", "SLACK")
        by_key = {r["_key"]: r for r in (drive, slack)}

        gp = AsyncMock()

        async def _records(virtual_record_id=None, **kw):
            reads["n"] += 1
            # First pass sees only Slack; the confirming read sees both.
            return ["rec-b"] if reads["n"] <= 2 else ["rec-a", "rec-b"]

        gp.get_records_by_virtual_record_id = AsyncMock(side_effect=_records)
        gp.get_document = AsyncMock(side_effect=lambda key, _c: by_key.get(key))
        gp.get_edges_from_node = AsyncMock(return_value=[])
        gp.delete_nodes = AsyncMock()
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(vdb, _locator(), gp, "vr-1", MagicMock())

        vdb.delete_points.assert_not_awaited()

    async def test_an_unreadable_record_document_does_not_delete_live_points(self):
        """The shape the key-count check above misses.

        Both providers' `get_document` catch every exception and return None,
        so a dropped connection looks exactly like "this record does not
        exist". The record key is still reported, so the re-read does not come
        back shorter — the document simply never resolves, its collection
        drops out of the live set, and its points get deleted while the record
        is very much alive.
        """
        drive = _record("rec-a", "conn-drive", "GOOGLE_DRIVE")
        slack = _record("rec-b", "conn-slack", "SLACK")

        gp = AsyncMock()
        gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-a", "rec-b"])
        # rec-a is live, but the graph cannot be read for it right now.
        gp.get_document = AsyncMock(
            side_effect=lambda key, _c: slack if key == "rec-b" else None
        )
        gp.get_edges_from_node = AsyncMock(return_value=[])
        gp.delete_nodes = AsyncMock()
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(vdb, _locator(), gp, "vr-1", MagicMock())

        assert drive  # the record exists; only the read failed
        vdb.delete_points.assert_not_awaited()

    async def test_a_complete_read_still_purges(self):
        """The guard must not disable the sweep it protects: when every record
        document resolves, the departed collection is still cleared."""
        gp = _graph([_record("rec-b", "conn-slack", "SLACK")])
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(vdb, _locator(), gp, "vr-1", MagicMock())

        purged = {c.kwargs["collection_name"] for c in vdb.delete_points.await_args_list}
        assert purged == {"google_drive_records"}

    async def test_a_skipped_membership_write_skips_the_purge(self):
        """When the graph resolved no connectorIds the sync declines to write;
        acting on that same unusable answer to delete would be worse."""
        gp = _graph([{"_key": "rec-x", "orgId": "org-1"}])
        vdb = _vdb()

        await rewrite_or_delete_virtual_record(vdb, _locator(), gp, "vr-1", MagicMock())

        vdb.set_payload.assert_not_awaited()
        vdb.delete_points.assert_not_awaited()
