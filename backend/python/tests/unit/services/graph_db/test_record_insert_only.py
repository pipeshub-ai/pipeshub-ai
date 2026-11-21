"""Insert-only ``createdAtTimestamp`` and ``incarnationId``: the parts that need no database.

The stores are checked on real servers in ``tests/integration/graph_db/test_record_insert_only_real_backends.py`` and
``tests/integration/graph_db/postgres/test_record_insert_only.py``.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import FileRecord, Record, RecordType
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.common.utils import (
    RECORD_UPDATE_DROPPED_FIELDS,
    positive_min,
    record_created_at_insert_only,
    record_incarnation_write,
    record_insert_doc,
    record_update_doc,
    restore_items,
)


@pytest.fixture
def incarnation_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")


# ---- the rule on a document ------------------------------------------------------------------------------------------

def test_an_update_drops_the_insert_only_fields_and_the_trash_state() -> None:
    doc = {"_key": "k", "recordName": "n", "createdAtTimestamp": 5, "incarnationId": "x", "isDeleted": False, "deleteBatchId": None}
    assert record_update_doc(doc) == {"_key": "k", "recordName": "n"}
    assert {"createdAtTimestamp", "incarnationId", "isDeleted", "deleteBatchId"} <= set(RECORD_UPDATE_DROPPED_FIELDS)


def test_the_kill_switch_restores_the_merge_of_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    doc = {"createdAtTimestamp": 5, "recordName": "n"}
    assert record_update_doc(doc) == doc


def test_an_insert_mints_an_id_only_with_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "false")
    assert "incarnationId" not in record_insert_doc({"a": 1})
    assert "incarnationId" not in record_insert_doc({"a": 1, "incarnationId": "held"}), "an older schema refuses the key"
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    minted = record_insert_doc({"a": 1})["incarnationId"]
    assert minted and minted != record_insert_doc({"a": 1})["incarnationId"]
    assert record_insert_doc({"a": 1, "incarnationId": "held"})["incarnationId"] == "held"
    assert record_insert_doc({"a": 1, "incarnationId": None})["incarnationId"]


@pytest.mark.parametrize(("stored", "incoming", "want"), [
    (500, 300, 300), (300, 500, 300), (None, 300, 300), (0, 300, 300), (-5, 300, 300), (500, 0, 500), (500, None, 500),
    (500, -1, 500), (None, None, None), (True, 300, 300),
])
def test_positive_min_ignores_values_that_are_not_real_dates(stored, incoming, want) -> None:
    assert positive_min(stored, incoming) == want


def test_the_flags_default_to_incarnation_off_and_insert_only_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first release writes no id unless the operator turns it on; the date rule is on. Nothing is set in the environment."""
    monkeypatch.delenv("RECORD_INCARNATION_WRITE", raising=False)
    monkeypatch.delenv("RECORD_CREATED_AT_INSERT_ONLY", raising=False)
    assert record_incarnation_write() is False and record_created_at_insert_only() is True
    assert "incarnationId" not in record_insert_doc({"a": 1})
    items, _ = restore_items([{"id": "a"}], None)
    assert "incarnationId" not in items[0]["set"]


def test_with_the_kill_switch_off_nothing_is_minted_even_with_the_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "true")
    assert "incarnationId" not in record_insert_doc({"a": 1})


@pytest.mark.parametrize(("insert_only", "flag"), [("true", "true"), ("true", "false"), ("false", "true"), ("false", "false")])
def test_the_neo4j_one_pass_split_equals_the_two_document_helpers(monkeypatch: pytest.MonkeyPatch, insert_only: str, flag: str) -> None:
    """``rest`` is what an update may merge and ``ins`` what an insert adds: together they are the document an insert writes."""
    from app.services.graph_db.neo4j.neo4j_provider import _record_insert_split

    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", insert_only)
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", flag)
    doc = {"id": "k", "recordName": "n", "createdAtTimestamp": 5, "incarnationId": "held", "isDeleted": False, "deleteBatchId": None}
    rest, ins = _record_insert_split(doc)
    full = record_insert_doc(doc)
    assert rest == record_update_doc(full)
    assert {**rest, **ins} == full
    _, ins2 = _record_insert_split({k: v for k, v in doc.items() if k != "incarnationId"})
    assert ("incarnationId" in ins2) == (insert_only == "true" and flag == "true")


def test_two_rows_for_one_id_merge_by_one_rule() -> None:
    from app.services.graph_db.common.utils import merge_same_id_record_docs

    assert merge_same_id_record_docs({"a": 1, "b": 1, "createdAtTimestamp": 200}, {"b": 2, "createdAtTimestamp": 100}) == {"a": 1, "b": 2, "createdAtTimestamp": 100}
    assert merge_same_id_record_docs({"createdAtTimestamp": 0}, {"createdAtTimestamp": 100})["createdAtTimestamp"] == 100
    assert merge_same_id_record_docs({"x": 1}, {"createdAtTimestamp": 7}) == {"x": 1, "createdAtTimestamp": 7}


def test_repeated_records_collapse_to_the_last_row_with_the_earliest_date() -> None:
    from app.services.graph_db.common.utils import collapse_repeated_records

    a, other, b = _file("k", created_at=200), _file("o", created_at=7), _file("k", created_at=100)
    a.record_name, b.record_name = "first", "second"
    b._promotes_stub = False
    a._promotes_stub = True
    out = collapse_repeated_records([a, other, b])
    assert [r.id for r in out] == ["k", "o"]
    assert out[0].record_name == "second" and out[0].created_at == 100 and out[0]._promotes_stub is True
    unique = [other]
    assert collapse_repeated_records(unique) is unique


def test_collapsing_a_large_batch_with_one_repeated_id_reads_each_id_once_per_row() -> None:
    """The cost is linear in the batch: every record's ``id`` is read a bounded number of times (a scan per distinct id would read it
    tens of thousands of times per row at this size)."""
    from app.services.graph_db.common.utils import collapse_repeated_records

    reads = 0

    class Row:
        def __init__(self, key: str, created_at: int) -> None:
            self._key, self.created_at, self._promotes_stub, self._known_new = key, created_at, False, True

        @property
        def id(self) -> str:
            nonlocal reads
            reads += 1
            return self._key

        def model_copy(self) -> "Row":
            return Row(self._key, self.created_at)

    n = 30_000
    rows = [Row(f"r{i}", i + 1) for i in range(n)] + [Row("r0", 500)]
    out = collapse_repeated_records(rows)
    assert len(out) == n and out[0].created_at == 1
    assert reads <= 4 * len(rows), reads


# ---- restore -------------------------------------------------------------------------------------------------------

def test_every_restore_item_gets_its_own_new_id_with_the_flag(incarnation_on: None) -> None:
    items, _ = restore_items([{"id": "a"}, {"id": "b", "set": {"incarnationId": "caller", "isDeleted": True}}], None)
    ids = [i["set"]["incarnationId"] for i in items]
    assert len(set(ids)) == 2 and "caller" not in ids, "the caller's value is replaced, never written back"


def test_a_restore_writes_no_id_with_the_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_INCARNATION_WRITE", "false")
    items, _ = restore_items([{"id": "a"}], None)
    assert "incarnationId" not in items[0]["set"]


# ---- the model -------------------------------------------------------------------------------------------------------

def _file(key: str = "k", *, created_at: int = 100, placeholder: bool = False) -> FileRecord:
    return FileRecord(
        id=key, org_id="o", record_name="a.txt", record_type=RecordType.FILE, external_record_id=f"e-{key}", version=0,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.KNOWLEDGE_BASE, connector_id="c", created_at=created_at,
        is_file=True, is_placeholder=placeholder,
    )


def test_a_record_never_writes_an_incarnation_id_whether_or_not_it_holds_one() -> None:
    """The store mints it; a copy carried from a stored record could start a new vertex under a deleted one's id."""
    assert "incarnationId" not in _file().to_arango_base_record()
    record = _file()
    record.incarnation_id = "held"
    assert "incarnationId" not in record.to_arango_base_record()


def test_with_the_kill_switch_off_an_update_still_never_writes_the_incarnation_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    assert record_update_doc({"a": 1, "createdAtTimestamp": 5, "incarnationId": "x"}) == {"a": 1, "createdAtTimestamp": 5}


def test_a_legacy_record_loads_with_no_id_whatever_the_loader() -> None:
    doc = _file().to_arango_base_record()
    assert Record.from_arango_base_record({**doc, "_key": "k"}).incarnation_id is None
    assert Record.from_arango_base_record({**doc, "_key": "k", "incarnationId": "held"}).incarnation_id == "held"


# ---- the processor ---------------------------------------------------------------------------------------------------

def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "o"
    proc.messaging_producer = AsyncMock()
    return proc


def _tx(existing: Record | None) -> AsyncMock:
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing)
    tx.get_record_by_key = AsyncMock(return_value=None)
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx.batch_upsert_records = AsyncMock()
    tx.get_org_apps = AsyncMock(return_value=[])
    return tx


async def _resync(existing: Record | None, incoming: FileRecord) -> tuple[Record | None, AsyncMock]:
    tx = _tx(existing)
    with patch.object(DataSourceEntitiesProcessor, "_handle_record_group", AsyncMock(return_value=None)):
        processed, _ = await _processor()._process_record(incoming, [], tx)
    return processed, tx


async def test_a_re_sync_that_builds_created_at_as_now_keeps_the_stored_date_and_id() -> None:
    stored = _file("stored", created_at=100)
    stored.incarnation_id = "inc-1"
    incoming = _file("fresh", created_at=999_999)
    incoming.external_record_id = stored.external_record_id
    processed, tx = await _resync(stored, incoming)
    assert processed.id == "stored"
    assert processed.created_at == 100
    assert processed.incarnation_id == "inc-1"
    assert tx.batch_upsert_records.await_args.args[0][0].created_at == 100


async def test_with_the_kill_switch_off_the_processor_keeps_the_incoming_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECORD_CREATED_AT_INSERT_ONLY", "false")
    stored = _file("stored", created_at=100)
    incoming = _file("fresh", created_at=900)
    incoming.external_record_id = stored.external_record_id
    processed, _ = await _resync(stored, incoming)
    assert processed.created_at == 900 and processed._promotes_stub is False


async def test_a_rename_under_a_new_external_id_keeps_the_stored_date_and_id_even_when_the_revision_changes() -> None:
    """The record is found by its id, not its external id, and is not indexed under the same revision: the store keeps its date and id,
    so the record handed on does too."""
    stored = _file("same-key", created_at=100)
    stored.incarnation_id = "inc-1"
    stored.external_revision_id = "r1"
    incoming = _file("same-key", created_at=999_999)
    incoming.external_record_id = "renamed-ext"
    incoming.external_revision_id = "r2"
    tx = _tx(None)
    tx.get_record_by_key = AsyncMock(return_value=stored.to_arango_base_record() | {"_key": "same-key", "incarnationId": "inc-1"})
    with patch.object(DataSourceEntitiesProcessor, "_handle_record_group", AsyncMock(return_value=None)):
        processed, _ = await _processor()._process_record(incoming, [], tx)
    assert processed.created_at == 100 and processed.incarnation_id == "inc-1"


async def test_a_new_record_is_marked_known_new_for_the_insert_first_order() -> None:
    processed, tx = await _resync(None, _file("fresh"))
    assert processed._known_new is True
    assert tx.batch_upsert_records.await_args.args[0][0]._known_new is True


async def test_a_real_record_replacing_a_stub_takes_the_earlier_date_and_the_stub_id() -> None:
    stub = _file("stub", created_at=500, placeholder=True)
    stub.incarnation_id = "stub-id"
    incoming = _file("fresh", created_at=300)
    incoming.external_record_id = stub.external_record_id
    processed, _ = await _resync(stub, incoming)
    assert processed.created_at == 300 and processed._promotes_stub is True
    assert processed.incarnation_id == "stub-id"


@pytest.mark.parametrize(("stub_date", "real_date", "want"), [(300, 500, 300), (500, 0, 500), (0, 500, 500)])
async def test_a_promotion_takes_the_earlier_positive_date(stub_date, real_date, want) -> None:
    stub = _file("stub", created_at=stub_date, placeholder=True)
    incoming = _file("fresh", created_at=real_date)
    incoming.external_record_id = stub.external_record_id
    processed, _ = await _resync(stub, incoming)
    assert processed.created_at == want


async def test_a_stub_refreshed_by_a_stub_keeps_its_stored_date_and_is_no_promotion() -> None:
    stub = _file("stub", created_at=500, placeholder=True)
    incoming = _file("fresh", created_at=900, placeholder=True)
    incoming.external_record_id = stub.external_record_id
    processed, _ = await _resync(stub, incoming)
    assert processed.created_at == 500 and processed._promotes_stub is False


async def test_a_demoted_real_record_is_frozen_and_a_second_promotion_keeps_the_date() -> None:
    real = _file("r", created_at=300)
    demote = _file("fresh", created_at=700, placeholder=True)
    demote.external_record_id = real.external_record_id
    demoted, _ = await _resync(real, demote)
    assert demoted.created_at == 300 and demoted._promotes_stub is False
    again = _file("fresh2", created_at=1000)
    again.external_record_id = real.external_record_id
    stored_stub = _file("r", created_at=300, placeholder=True)
    promoted, _ = await _resync(stored_stub, again)
    assert promoted.created_at == 300


# ---- the ArangoDB bulk call  ----------------------------------------------------------------------

class _Resp:
    def __init__(self, status: int, body: object) -> None:
        self.status, self._body = status, body

    async def json(self) -> object:
        return self._body

    async def text(self) -> str:
        return json.dumps(self._body)

    async def __aenter__(self) -> _Resp:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


def _ok(n: int = 1) -> _Resp:
    return _Resp(201, [{"_key": f"k{i}"} for i in range(n)])


def _err(num: int, message: str = "e") -> dict:
    return {"error": True, "errorNum": num, "errorMessage": message}


@pytest.fixture
def client() -> ArangoHTTPClient:
    return ArangoHTTPClient(base_url="http://localhost:8529", username="root", password="x", database="db",
                            logger=MagicMock(spec=logging.Logger))


async def _run(client: ArangoHTTPClient, rows: list[dict], *answers: _Resp, txn_id: str | None = None) -> tuple[dict, list]:
    session = MagicMock()
    session.request.side_effect = list(answers)
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session), \
            patch("app.services.graph_db.arango.arango_http_client.asyncio.sleep", new=AsyncMock()):
        try:
            result = await client.batch_upsert_documents("records", rows, txn_id=txn_id)
        finally:
            calls = [(c.args[0], c.kwargs["json"]) for c in session.request.call_args_list]
    return result, calls


def _row(key: str, *, new: bool) -> dict:
    return {"key": key, "insert": {"_key": key, "createdAtTimestamp": 5, "incarnationId": "i"}, "patch": {"recordName": key}, "new": new}


async def test_the_patch_drops_nulls_like_the_older_write(client) -> None:
    """The patch sends ``keepNull=false``, so a None in an update removes the key as ``overwriteMode=update`` did."""
    session = MagicMock()
    session.request.side_effect = [_ok()]
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        await client.batch_upsert_documents("records", [_row("a", new=False)])
    assert session.request.call_args.kwargs["params"]["keepNull"] == "false"


async def test_an_insert_after_a_patch_miss_gets_a_new_incarnation_id(client, incarnation_on) -> None:
    """The key was deleted since the record was read, so the insert is a new life of it and must not carry the earlier id."""
    row = {"key": "a", "insert": {"_key": "a", "createdAtTimestamp": 5, "incarnationId": "old"}, "new": False}
    _, calls = await _run_drop(client, [row], _Resp(202, [_err(1202)]), _ok())
    assert [m for m, _ in calls] == ["PATCH", "POST"]
    assert calls[1][1][0]["incarnationId"] not in ("old", None)
    assert calls[1][1][0]["createdAtTimestamp"] == 5


async def test_an_insert_that_was_planned_keeps_the_id_it_was_given(client, incarnation_on) -> None:
    row = {"key": "a", "insert": {"_key": "a", "incarnationId": "minted"}, "new": True}
    _, calls = await _run_drop(client, [row], _ok())
    assert calls[0][1][0]["incarnationId"] == "minted"


async def _run_drop(client: ArangoHTTPClient, rows: list[dict], *answers: _Resp) -> tuple[dict, list]:
    session = MagicMock()
    session.request.side_effect = list(answers)
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        result = await client.batch_upsert_documents("records", rows, drop=RECORD_UPDATE_DROPPED_FIELDS)
    return result, [(c.args[0], c.kwargs["json"]) for c in session.request.call_args_list]


async def test_a_key_given_twice_is_written_once_with_the_fields_of_both_rows(client) -> None:
    rows = [
        {"key": "a", "insert": {"_key": "a", "x": 1, "y": 1}, "new": True},
        {"key": "a", "insert": {"_key": "a", "y": 2, "z": 3}, "new": True},
    ]
    _, calls = await _run(client, rows, _ok())
    assert [m for m, _ in calls] == ["POST"]
    assert calls[0][1] == [{"_key": "a", "x": 1, "y": 2, "z": 3}]


async def test_a_key_given_twice_merges_the_derived_patch_of_a_row_without_one(client) -> None:
    rows = [
        {"key": "a", "insert": {"_key": "a", "a": 1}, "patch": {"a": 1}, "new": False},
        {"key": "a", "insert": {"_key": "a", "b": 2, "createdAtTimestamp": 9}, "new": False},
    ]
    session = MagicMock()
    session.request.side_effect = [_ok()]
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        await client.batch_upsert_documents("records", rows, drop=RECORD_UPDATE_DROPPED_FIELDS)
    assert session.request.call_args.kwargs["json"] == [{"_key": "a", "a": 1, "b": 2}]


async def test_a_key_given_twice_is_patched_first_unless_every_row_is_new(client) -> None:
    rows = [
        {"key": "a", "insert": {"_key": "a", "x": 1}, "new": True},
        {"key": "a", "insert": {"_key": "a", "y": 2}, "new": False},
    ]
    _, calls = await _run(client, rows, _ok())
    assert [m for m, _ in calls] == ["PATCH"]


async def test_a_known_new_record_is_one_insert_call(client) -> None:
    result, calls = await _run(client, [_row("a", new=True)], _ok())
    assert [m for m, _ in calls] == ["POST"] and result["created"] == 1
    assert calls[0][1] == [{"_key": "a", "createdAtTimestamp": 5, "incarnationId": "i"}]


async def test_a_known_existing_record_is_one_patch_call_without_the_insert_only_keys(client) -> None:
    result, calls = await _run(client, [_row("a", new=False)], _ok())
    assert [m for m, _ in calls] == ["PATCH"] and result["updated"] == 1
    assert calls[0][1] == [{"_key": "a", "recordName": "a"}]


async def test_a_patch_miss_is_inserted_with_the_whole_document(client) -> None:
    result, calls = await _run(client, [_row("a", new=False)], _Resp(202, [_err(1202)]), _ok())
    assert [m for m, _ in calls] == ["PATCH", "POST"]
    assert calls[1][1][0]["createdAtTimestamp"] == 5


async def test_an_insert_that_finds_the_key_stored_is_patched(client) -> None:
    result, calls = await _run(client, [_row("a", new=True)], _Resp(202, [_err(1210)]), _ok())
    assert [m for m, _ in calls] == ["POST", "PATCH"]
    assert "createdAtTimestamp" not in calls[1][1][0]


async def test_a_write_conflict_inside_a_transaction_raises_and_is_not_sent_through_the_patch(client) -> None:
    with pytest.raises(Exception, match="1200"):
        await _run(client, [_row("a", new=True)], _Resp(202, [_err(1200, "write-write conflict")]), txn_id="tx1")
    # nothing else was sent: a patch next would answer 1202 and the insert 1200 again


async def test_the_conflict_the_bulk_call_raises_is_one_the_push_retry_recognises(client) -> None:
    """The push's retry loop (and the other classifiers) read a batch's conflict from the message form ``Batch <word> failed with
    N error(s): Item i: [1200]``; a different form would turn a lost write into a 500 instead of a rerun."""
    from app.connectors.sources.localKB.push.retry import is_attempt_lost

    with pytest.raises(Exception, match="1200") as caught:
        await _run(client, [_row("a", new=True)], _Resp(202, [_err(1200, "timeout waiting to lock key")]), txn_id="tx1")
    assert is_attempt_lost(caught.value)


async def test_a_write_conflict_inside_a_transaction_raises_from_the_patch_too(client) -> None:
    with pytest.raises(Exception, match="1200"):
        await _run(client, [_row("a", new=False)], _Resp(202, [_err(1200)]), txn_id="tx1")


@pytest.mark.parametrize("num", [1200, 1210, 1202, 1620, 1, 1580])
async def test_any_other_per_item_error_raises(client, num) -> None:
    """The expected answer of each call is consumed; the rest raise so the owner of the transaction reruns."""
    expected_for_insert = {1210}
    expected_for_patch = {1202}
    row_new = _row("a", new=True)
    row_old = _row("b", new=False)
    for row, expected in ((row_new, expected_for_insert), (row_old, expected_for_patch)):
        if num in expected:
            continue
        with pytest.raises(Exception, match="failed"):
            await _run(client, [row], _Resp(202, [_err(num)]), _Resp(202, [_err(num)]), _Resp(202, [_err(num)]), txn_id="tx1")
