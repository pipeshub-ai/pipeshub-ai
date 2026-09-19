"""Which cleanup event a connector/KB deletion publishes, and what it carries.

The behaviour under test is the reason this exists at all: a connector with
200k records used to ship every virtual record id in one Kafka message, which
exceeded the 1 MiB default request cap, failed the publish, and left every
embedding behind with the graph rows already gone.
"""

import json

import pytest

from app.config.constants.arangodb import EventTypes
from app.connectors.services.vector_cleanup_events import (
    MAX_VIRTUAL_RECORD_IDS_PER_EVENT,
    build_connector_vector_cleanup_events,
)
from app.services.graph_db.vector_membership_queries import can_use_membership_cleanup

# aiokafka's default max_request_size, which nothing in the repo overrides.
KAFKA_DEFAULT_MAX_REQUEST_BYTES = 1048576


class TestBackfilledConnector:
    def test_publishes_one_connector_scoped_event_with_no_ids(self):
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            connector_name="GOOGLE_DRIVE",
            virtual_record_ids=["vr-1", "vr-2"],
        )

        assert len(events) == 1
        assert events[0]["eventType"] == EventTypes.DELETE_CONNECTOR_EMBEDDINGS.value
        # Present but unused: the points find themselves by connectorIds.
        assert "virtualRecordIds" not in events[0]["payload"]

    def test_payload_size_is_independent_of_record_count(self):
        """The whole point. One record or two hundred thousand, same message."""
        small = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            virtual_record_ids=["vr-1"],
        )
        huge = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            virtual_record_ids=[f"vr-{i}" for i in range(200_000)],
        )
        assert len(json.dumps(small)) == len(json.dumps(huge))

    def test_carries_the_connectors_record_groups(self):
        """They went with the connector, so a surviving shared point has to be
        told to stop pointing at them."""
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            record_group_ids=["rg-1", "rg-1", "  ", "rg-2"],
        )
        assert events[0]["payload"]["recordGroupIds"] == ["rg-1", "rg-2"]

    def test_a_connector_with_no_records_still_publishes(self):
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            virtual_record_ids=[],
        )
        assert len(events) == 1


class TestUnbackfilledConnectorFallback:
    def test_falls_back_to_shipping_ids(self):
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=False,
            virtual_record_ids=["vr-1", "vr-1", " vr-2 ", "", None],
        )
        assert len(events) == 1
        assert events[0]["eventType"] == EventTypes.BULK_DELETE_RECORDS.value
        assert events[0]["payload"]["virtualRecordIds"] == ["vr-1", "vr-2"]

    def test_nothing_to_delete_publishes_nothing(self):
        assert (
            build_connector_vector_cleanup_events(
                org_id="org-1",
                connector_id="conn-1",
                vector_membership_backfilled=False,
                virtual_record_ids=[],
            )
            == []
        )

    @pytest.mark.parametrize("count", [30_000, 120_000])
    def test_large_id_lists_are_chunked_under_the_kafka_cap(self, count):
        ids = [f"{i:036d}" for i in range(count)]
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=False,
            virtual_record_ids=ids,
        )

        assert len(events) > 1
        for event in events:
            assert len(json.dumps(event)) < KAFKA_DEFAULT_MAX_REQUEST_BYTES

        seen = [i for e in events for i in e["payload"]["virtualRecordIds"]]
        assert seen == ids

    def test_chunk_boundary(self):
        ids = [f"vr-{i}" for i in range(MAX_VIRTUAL_RECORD_IDS_PER_EVENT + 1)]
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=False,
            virtual_record_ids=ids,
        )
        assert [len(e["payload"]["virtualRecordIds"]) for e in events] == [
            MAX_VIRTUAL_RECORD_IDS_PER_EVENT,
            1,
        ]
        assert {e["payload"]["chunkCount"] for e in events} == {2}


class TestExhaustedBackfillGate:
    def test_exhausted_connector_falls_back_despite_the_completion_flag(self):
        """The backfill sets `backfilled` when it gives up as well as when it
        succeeds. Reading only that flag would send a connector whose points
        were never tagged down the membership path and orphan every one."""
        events = build_connector_vector_cleanup_events(
            org_id="org-1",
            connector_id="conn-1",
            vector_membership_backfilled=True,
            vector_membership_backfill_exhausted=True,
            virtual_record_ids=["vr-1"],
        )
        assert events[0]["eventType"] == EventTypes.BULK_DELETE_RECORDS.value

    @pytest.mark.parametrize(
        ("backfilled", "exhausted", "expected"),
        [
            (True, False, True),
            (True, True, False),
            (False, False, False),
            (False, True, False),
        ],
    )
    def test_gate_truth_table(self, backfilled, exhausted, expected):
        assert (
            can_use_membership_cleanup(
                {
                    "vectorMembershipBackfilled": backfilled,
                    "vectorMembershipBackfillExhausted": exhausted,
                }
            )
            is expected
        )
