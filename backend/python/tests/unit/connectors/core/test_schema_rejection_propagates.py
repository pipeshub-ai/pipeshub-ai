"""A schema rejection is a permanent error: the processor's log-and-go-on handlers let it through.

It used to be swallowed there, and the transaction then committed without the rejected write.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jsonschema import Draft7Validator

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.schema import node_validator
from app.schema.node_validator import SchemaValidationError
from app.utils.logger import log_record_fields


def _rejection() -> SchemaValidationError:
    """A fresh error per use: ``schema_reported`` is set on the instance once it has been counted."""
    return SchemaValidationError("records", "at 'x': unknown key")


def _processor(tx_store: MagicMock) -> DataSourceEntitiesProcessor:
    store = MagicMock(spec=GraphDataStore)
    store.is_transient_error = MagicMock(return_value=False)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=tx_store)
    ctx.__aexit__ = AsyncMock(return_value=False)
    store.transaction = MagicMock(return_value=ctx)
    proc = DataSourceEntitiesProcessor(MagicMock(), store, AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def test_a_schema_rejection_must_propagate_and_is_logged_at_error_and_counted(caplog: pytest.LogCaptureFixture) -> None:
    proc = _processor(MagicMock(explicit=False))
    proc.logger = logging.getLogger("schema-rejection-test")
    with (
        caplog.at_level(logging.ERROR, logger="schema-rejection-test"),
        patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.record_schema_rejection"
        ) as counted,
    ):
        assert proc._must_propagate(_rejection(), MagicMock(explicit=False)) is True

    counted.assert_called_once_with("records")
    [record] = caplog.records
    assert record.levelno == logging.ERROR
    assert "records" in record.getMessage() and "unknown key" not in record.getMessage()
    # Raised by a provider, not by jsonschema: no rule to point at, and nothing breaks.
    assert (log_record_fields(record)["schema_path"], log_record_fields(record)["validator"]) == (None, None)


@pytest.mark.parametrize(("field", "value", "rule", "validator"), [
    ("name", "Ada Seeded-Lovelace", {"type": "integer"}, "type"),
    ("email", "ada.seeded@example.com", {"type": "string", "pattern": "^nobody$"}, "pattern"),
])
def test_a_quarantined_record_logs_where_and_which_rule_never_the_value(
    caplog: pytest.LogCaptureFixture, field: str, value: str, rule: dict, validator: str,
) -> None:
    error = next(Draft7Validator({"type": "object", "properties": {field: rule}}).iter_errors({field: value}))
    with pytest.raises(SchemaValidationError) as raised:
        node_validator._raise("users", error)
    assert value in raised.value.original_error.message  # control: the validator quotes the value
    proc = _processor(MagicMock(explicit=False))
    proc.logger = logging.getLogger("schema-rejection-test")
    record = MagicMock()
    record.id = "rec-1"
    with (
        caplog.at_level(logging.ERROR, logger="schema-rejection-test"),
        patch("app.connectors.core.base.data_processor.data_source_entities_processor.record_schema_rejection"),
    ):
        proc._quarantine_record(record, raised.value)

    report, quarantine = caplog.records
    fields = log_record_fields(report)
    assert (fields["schema_path"], fields["validator"]) == (f"$.{field}", validator)
    assert "users" in report.getMessage() and "rec-1" in quarantine.getMessage()
    for r in caplog.records:
        assert value not in r.getMessage() + str(log_record_fields(r)), r.getMessage()


def test_an_ordinary_error_outside_an_explicit_transaction_is_still_swallowed() -> None:
    proc = _processor(MagicMock(explicit=False))
    assert proc._must_propagate(RuntimeError("boom"), MagicMock(explicit=False)) is False


async def test_a_group_delete_rejected_by_the_schema_raises_instead_of_returning_false() -> None:
    tx = AsyncMock()
    tx.get_record_group_by_external_id = AsyncMock(side_effect=_rejection())
    proc = _processor(tx)

    with (
        patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.is_soft_delete_enabled",
            new=AsyncMock(return_value=False),
        ),
        pytest.raises(SchemaValidationError),
    ):
        await proc.on_record_group_deleted("ext-1", "conn-1")


# ---- on_new_records: one rejected record must not take its slice down ----------------------------------------


def _records(n: int) -> list[tuple[MagicMock, list]]:
    out = []
    for i in range(n):
        rec = MagicMock()
        rec.id = f"rec-{i}"
        rec.external_record_id = f"ext-{i}"
        out.append((rec, []))
    return out


def _slice_processor(
    bad: set[str], other_error: tuple[str, Exception] | None = None
) -> tuple[DataSourceEntitiesProcessor, list[dict]]:
    """A processor whose _process_record rejects the records in ``bad``; returns it with its call logs."""
    proc = _processor(MagicMock(explicit=True))
    proc.logger = MagicMock()
    proc._access_changed = AsyncMock()
    proc._snapshot_old_paths = AsyncMock(return_value={})
    proc._after_slice_committed = AsyncMock()
    ctxs: list[dict] = []
    real_ctx = proc.data_store_provider.transaction

    def transaction(**kwargs: object) -> MagicMock:
        entry = {"written": [], "committed": False}
        ctxs.append(entry)
        ctx = real_ctx(**kwargs)
        ctx.__aexit__ = AsyncMock(side_effect=lambda et, *_: entry.update(committed=et is None) or False)
        return ctx

    proc.data_store_provider.transaction = transaction

    async def process(record: MagicMock, *args: object, **kwargs: object) -> tuple[MagicMock, list]:
        ctxs[-1]["written"].append(record.id)
        if record.id in bad:
            raise SchemaValidationError("records", f"bad {record.id}")
        if other_error and record.id == other_error[0]:
            raise other_error[1]
        return record, []

    proc._process_record = process
    return proc, ctxs


def _published(proc) -> list[str]:
    return [r.id for call in proc._after_slice_committed.await_args_list for r in call.args[0]]


async def test_one_bad_record_in_a_slice_of_100_is_quarantined_and_the_other_99_commit() -> None:
    proc, ctxs = _slice_processor({"rec-37"})
    items = _records(100)

    with patch(
        "app.connectors.core.base.data_processor.data_source_entities_processor.record_schema_rejection"
    ) as counted:
        await proc.on_new_records(items)

    assert _published(proc) == [r.id for r, _ in items if r.id != "rec-37"]  # order kept, bad one not published
    committed = [w for c in ctxs if c["committed"] for w in c["written"]]
    assert sorted(committed) == sorted(r.id for r, _ in items if r.id != "rec-37")
    assert not any(c["committed"] for c in ctxs if "rec-37" in c["written"])
    counted.assert_called_once_with("records")
    errors = [c.args for c in proc.logger.error.call_args_list]
    assert len(errors) == 2  # the rejection report and the quarantine line, nothing per handler
    assert any("rec-37" in str(a) for args in errors for a in args)


async def test_a_slice_with_only_bad_records_commits_nothing_and_does_not_raise() -> None:
    proc, ctxs = _slice_processor({"rec-0", "rec-1", "rec-2"})

    await proc.on_new_records(_records(3))

    assert _published(proc) == []
    assert not any(c["committed"] for c in ctxs)


async def test_slices_are_redone_one_record_each_only_for_the_slice_that_failed() -> None:
    proc, ctxs = _slice_processor({"rec-5"})
    items = _records(250)  # slices 0-99, 100-199, 200-249

    await proc.on_new_records(items)

    # slice 1 (one tx of 100), slice 2 redone per record (1 failed whole + 100 singles), slice 3 whole
    assert [len(c["written"]) for c in ctxs[:2]] == [6, 1]  # the failed whole-slice tx stops at the bad record
    assert len(ctxs) == 1 + 100 + 1 + 1
    assert _published(proc) == [r.id for r, _ in items if r.id != "rec-5"]


async def test_a_non_schema_error_still_rolls_the_slice_back_and_propagates() -> None:
    proc, ctxs = _slice_processor(set(), other_error=("rec-3", RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        await proc.on_new_records(_records(10))

    assert len(ctxs) == 1 and not ctxs[0]["committed"]
    proc._after_slice_committed.assert_not_awaited()


async def test_a_later_records_error_in_the_fallback_does_not_strand_the_earlier_ones() -> None:
    # Each fallback record commits in its own transaction: its post-commit work must already have run
    # when a later record fails (a deadlock, say), or the retry finds it unchanged and never redoes it.
    proc, _ = _slice_processor({"rec-1"}, other_error=("rec-3", RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        await proc.on_new_records(_records(5))

    assert _published(proc) == ["rec-0", "rec-2"]


async def test_a_schema_error_raised_by_a_helper_outside_the_slice_loop_is_reported_once() -> None:
    proc, _ = _slice_processor(set())
    proc._after_slice_committed = AsyncMock(side_effect=SchemaValidationError("records", "late"))

    with (
        patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.record_schema_rejection"
        ) as counted,
        pytest.raises(SchemaValidationError),
    ):
        await proc.on_new_records(_records(2))

    counted.assert_called_once_with("records")


def test_a_rejection_passing_several_handlers_is_logged_and_counted_once() -> None:
    proc = _processor(MagicMock(explicit=False))
    proc.logger = MagicMock()
    error = SchemaValidationError("records", "x")
    with patch(
        "app.connectors.core.base.data_processor.data_source_entities_processor.record_schema_rejection"
    ) as counted:
        assert proc._must_propagate(error, None) is True
        assert proc._must_propagate(error, None) is True
        proc._log_failure("outer", error)

    counted.assert_called_once_with("records")
    proc.logger.error.assert_called_once()
    assert error.schema_reported is True


# ---- _reset_indexing_status_to_queued -----------------------------------------------------------------------


async def test_the_status_reset_writes_only_the_status() -> None:
    tx = AsyncMock()
    tx.get_record_by_key = AsyncMock(return_value={"_key": "r1", "indexingStatus": "NOT_STARTED", "legacyKey": 1})
    proc = _processor(tx)

    await proc._reset_indexing_status_to_queued("r1", tx)

    tx.batch_upsert_nodes.assert_not_awaited()
    tx.batch_update_nodes.assert_awaited_once_with([{"_key": "r1", "indexingStatus": "QUEUED"}], "records")


async def test_the_status_reset_lets_a_schema_rejection_through_but_swallows_other_errors() -> None:
    tx = AsyncMock()
    tx.get_record_by_key = AsyncMock(return_value={"indexingStatus": "NOT_STARTED"})
    tx.batch_update_nodes = AsyncMock(side_effect=_rejection())
    proc = _processor(tx)
    with pytest.raises(SchemaValidationError):
        await proc._reset_indexing_status_to_queued("r1", tx)

    tx.batch_update_nodes = AsyncMock(side_effect=RuntimeError("nope"))
    await proc._reset_indexing_status_to_queued("r1", tx)  # swallowed, as before
