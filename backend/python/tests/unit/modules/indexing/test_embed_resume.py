"""The marker of an interrupted embedding run: its fields, who may trust it, and where it travels."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.modules.indexing.embed_resume import (
    DELIVERY_ID_KEY,
    EMBED_DELIVERY_ID,
    EMBED_FIELDS,
    EMBED_REV,
    EMBED_VRID,
    RESUME_DELIVERY_ID_KEY,
    EmbedAttempt,
    begin_attempt,
    cleared_fields,
    digest_of,
    end_attempt,
    marker_of,
    may_resume,
    stamp_delivery_id,
)
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.transformers.pipeline import IndexingPipeline
from app.schema.arango.documents import record_schema
from app.schema.node_validator import NodeSchemaValidator, SchemaValidationError
from app.services.graph_db.common.utils import api_record
from app.services.vector_db.const.const import (
    CONTENT_REV_FIELD,
    EMBED_KEY_FIELD,
    PAYLOAD_KEYWORD_INDEXES,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

APP = Path(__file__).resolve().parents[4] / "app"


@pytest.fixture(autouse=True)
def _no_attempt() -> Iterator[None]:
    end_attempt()
    yield
    end_attempt()


def _marker() -> dict:
    return {
        "embedVrid": "v", "embedRev": "r", "embedKey": "k", "embedDigest": "d", "embedTotal": 3,
        "embedDeliveryId": "e1", "embedStartedMs": 5, "embedSeconds": 0, "embedUncounted": 0, "embedDone": 0,
    }


class TestWhoMayTrustAMarker:
    def _may(self, **over: object) -> bool:
        args = {"delivery_id": "e1", "rev": "r", "node_vrid": "v", "force_reindex": False, **over}
        marker = over.pop("marker", _marker()) if "marker" in over else _marker()
        return may_resume(marker, **{k: v for k, v in args.items() if k != "marker"})

    def test_the_event_that_wrote_it_may(self) -> None:
        assert self._may()

    @pytest.mark.parametrize(
        "over",
        [
            {"delivery_id": "e2"}, {"delivery_id": None}, {"delivery_id": ""}, {"rev": "other"},
            {"node_vrid": "w"}, {"node_vrid": None}, {"force_reindex": True},
        ],
    )
    def test_nothing_else_does(self, over: dict) -> None:
        assert not self._may(**over)

    @pytest.mark.parametrize("missing", ["embedDigest", "embedVrid", "embedDeliveryId", "embedRev"])
    def test_a_marker_missing_a_field_it_depends_on_is_not_trusted(self, missing: str) -> None:
        marker = _marker() | {missing: None}
        assert not may_resume(marker, delivery_id="e1", rev="r", node_vrid="v", force_reindex=False)

    def test_an_empty_marker_never_resumes(self) -> None:
        assert not may_resume({}, delivery_id="e1", rev="r", node_vrid="v", force_reindex=False)


def test_the_digest_is_over_the_sorted_set_of_ids() -> None:
    assert digest_of(["b", "a", "c"]) == digest_of(["c", "b", "a", "a"])
    assert digest_of(["a", "b"]) != digest_of(["a", "c"])


def test_a_marker_is_read_as_empty_when_the_node_names_no_vrid() -> None:
    assert marker_of({"embedRev": "r"}) == {}
    assert marker_of(None) == {}
    assert marker_of({**_marker(), "other": 1}) == _marker()


class TestDeliveryIdentity:
    def test_a_delivery_is_named_by_the_consumers_stable_id(self) -> None:
        payload: dict = {}
        stamp_delivery_id(payload, "1700-0")
        assert payload[DELIVERY_ID_KEY] == "1700-0"

    def test_a_requeue_of_the_same_event_is_stamped_with_the_same_id(self) -> None:
        payload: dict = {DELIVERY_ID_KEY: "stale", "_retry_tracking_id": "1700-0"}
        stamp_delivery_id(payload, "1700-0")
        assert payload[DELIVERY_ID_KEY] == "1700-0"

    def test_recovery_continues_the_run_it_names_and_overrides_the_new_identity(self) -> None:
        payload = {RESUME_DELIVERY_ID_KEY: "1700-0"}
        stamp_delivery_id(payload, "recovery:rec:3")
        assert payload[DELIVERY_ID_KEY] == "1700-0"

    def test_a_new_event_never_matches_an_old_marker(self) -> None:
        old = _marker()
        payload: dict = {}
        stamp_delivery_id(payload, "1800-0")
        assert not may_resume(old, delivery_id=payload[DELIVERY_ID_KEY], rev="r", node_vrid="v", force_reindex=False)

    @pytest.mark.parametrize(
        "path",
        [
            "services/messaging/redis_streams/indexing_consumer.py",
            "services/messaging/kafka/consumer/indexing_consumer.py",
            "services/messaging/postgres/consumer.py",
        ],
    )
    def test_every_consumer_stamps_the_payload_before_the_handler(self, path: str) -> None:
        # Source scan: the wiring lives in a 400-line wrapper per consumer; QA runs each live.
        assert len(re.findall(r"stamp_delivery_id\(", (APP / path).read_text())) == 1

    def test_stale_recovery_hands_over_the_interrupted_runs_identity(self) -> None:
        source = (APP / "indexing_main.py").read_text()
        assert re.search(r"payload\[RESUME_DELIVERY_ID_KEY\]\s*=\s*record\[EMBED_DELIVERY_ID\]", source)


class TestTheFieldsAreDeclaredWhereverTheyAreWritten:
    def test_the_arango_record_schema_declares_every_field_flat_and_nullable(self) -> None:
        props = record_schema["rule"]["properties"]
        for name in EMBED_FIELDS:
            assert "null" in props[name]["type"], name
            assert props[name]["type"][0] in ("string", "number")
        assert record_schema["rule"]["additionalProperties"] is False

    @pytest.mark.parametrize("name", EMBED_FIELDS)
    def test_the_graph_validator_of_every_provider_accepts_each_field_alone(self, name: str) -> None:
        # A partial write of one field must pass on Neo4j and Postgres (which validate in the app) as on Arango.
        NodeSchemaValidator().validate_node_update("records", {name: _marker()[name]})

    def test_the_clear_write_is_accepted(self) -> None:
        NodeSchemaValidator().validate_node_update("records", cleared_fields())

    def test_a_nested_marker_is_refused(self) -> None:
        with pytest.raises(SchemaValidationError):
            NodeSchemaValidator().validate_node_update("records", {"embedMarker": {"done": 1}})

    def test_the_record_endpoint_does_not_return_the_marker(self) -> None:
        shown = api_record({"recordName": "n", **_marker()})
        assert shown == {"recordName": "n"}

    def test_points_are_indexed_on_the_revision_and_the_setup(self) -> None:
        names = [n for n, _ in PAYLOAD_KEYWORD_INDEXES]
        assert CONTENT_REV_FIELD in names and EMBED_KEY_FIELD in names


@pytest.mark.asyncio
class TestFirstReconciliationPassPurge:
    async def _context(self, resuming: bool | None) -> AsyncMock:
        container = (await JSONParser().parse(b'[{"a": 1}, {"a": 2}]', "d.json")).block_container
        record = MagicMock(block_containers=container, virtual_record_id="v", org_id="o")
        ctx = MagicMock(record=record, event_type=EventTypes.REINDEX_RECORD.value, prev_virtual_record_id="v")
        sink = MagicMock()
        sink.blob_storage.get_reconciliation_metadata = AsyncMock(return_value=None)
        sink.vector_store.purge_record_vectors = AsyncMock()
        if resuming is not None:
            begin_attempt(EmbedAttempt(delivery_id="e1", rev="r", vrid="v", resume=resuming))
        built = await IndexingPipeline.build_reconciliation_context(ctx, MagicMock(), sink)
        assert built is not None and built.blocks_to_index_ids is None  # a full index either way
        return sink.vector_store.purge_record_vectors

    async def test_a_fresh_pass_purges_as_before(self) -> None:
        purge = await self._context(None)
        purge.assert_awaited_once()

    async def test_a_new_event_that_finds_a_marker_purges(self) -> None:
        purge = await self._context(False)
        purge.assert_awaited_once()

    async def test_a_resumed_pass_keeps_the_points_the_interrupted_run_wrote(self) -> None:
        # Kills: the purge that deleted the progress of every retry of a first-time reconciled update.
        purge = await self._context(True)
        purge.assert_not_awaited()


@pytest.mark.asyncio
class TestTheMarkerEndsWithTheIndexedWrite:
    async def _row(self, attempt: bool) -> dict:
        from tests.unit.modules.transformers.test_sink_orchestrator import (
            _make_ctx,
            _make_orchestrator,
        )

        orch = _make_orchestrator()
        if attempt:
            begin_attempt(EmbedAttempt(delivery_id="e1", rev="r", vrid="v"))
        with patch("app.modules.transformers.sink_orchestrator.notify_record_indexed", AsyncMock()):
            await orch._update_indexing_status(_make_ctx())
        (row,) = orch.graph_provider.batch_update_nodes.await_args.args[0]
        return row

    async def test_completed_clears_every_field_in_the_same_write(self) -> None:
        # Kills: a stale marker after COMPLETED.
        row = await self._row(attempt=True)
        assert all(row[name] is None for name in EMBED_FIELDS)

    async def test_a_run_that_is_not_an_event_attempt_adds_nothing_to_the_write(self) -> None:
        row = await self._row(attempt=False)
        assert not set(EMBED_FIELDS) & set(row)


def test_rev_and_vrid_field_names_are_the_ones_the_schema_has() -> None:
    assert EMBED_REV == "embedRev" and EMBED_VRID == "embedVrid" and EMBED_DELIVERY_ID == "embedDeliveryId"
