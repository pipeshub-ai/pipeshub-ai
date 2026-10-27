"""Which graph errors the push retries, and the bound on the one retry loop."""

from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.localKB.push import retry as R
from app.connectors.sources.localKB.push import service as SV
from app.connectors.sources.localKB.push.contract import PushRefusal

from .test_service import validated


class FakeArangoError(Exception):
    def __init__(self, error_num: int | None, status: int = 409) -> None:
        super().__init__(f"arango {error_num}")
        self.error_num = error_num
        self.status = status


class TransientError(Exception):
    """Named like Neo4j's, which has no code when raised by hand."""


class CodedNeo4jError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Wrapped(Exception):
    pass


@pytest.mark.parametrize("error", [
    FakeArangoError(1654, 410), FakeArangoError(1655, 404), FakeArangoError(1200), FakeArangoError(29), FakeArangoError(18),
    FakeArangoError(None, 410), FakeArangoError(999, 410),
    Exception("Batch insert failed with 1 error(s): Item 0: [1200] timeout waiting to lock key"),
    TransientError("deadlock"), CodedNeo4jError("Neo.TransientError.Transaction.DeadlockDetected"),
])
def test_lost_transactions_are_recognised(error: Exception) -> None:
    assert R.is_attempt_lost(error)


@pytest.mark.parametrize("error", [
    FakeArangoError(1210), FakeArangoError(1501, 400), FakeArangoError(None, 500), CodedNeo4jError("Neo.ClientError.Schema.ConstraintValidationFailed"),
    RuntimeError("x"), Exception("Item 0: [1210] unique constraint violated"), TimeoutError(),
    # a bracketed number that is not an ArangoDB error code:
    ValueError("blocks[0].citation_metadata.page_number [18] invalid"), RuntimeError("budget [29] exceeded"),
    CodedNeo4jError("Neo.ClientError.Schema.ConstraintValidationFailed: value 'Q3 [1200] report' already exists"),
    Exception("Item 0: [1200] timeout waiting to lock key"),  # right number, not the provider's batch wording
    Exception("Batch insert failed with 1 error(s): Item 0: [1210] unique constraint violated"), ValueError("x"),
])
def test_other_errors_are_not_retried(error: Exception) -> None:
    assert not R.is_attempt_lost(error)


def test_a_wrapped_error_is_found_through_its_cause() -> None:
    try:
        try:
            raise FakeArangoError(1654, 410)
        except FakeArangoError as inner:
            raise Wrapped("could not look up record") from inner
    except Wrapped as e:
        assert R.is_attempt_lost(e)
    cycle = Wrapped("a")
    cycle.__cause__ = cycle
    assert not R.is_attempt_lost(cycle)


def test_the_module_imports_no_storage_client() -> None:
    import pathlib

    lines = pathlib.Path(R.__file__).read_text().splitlines()
    assert [line for line in lines if line.startswith(("import ", "from "))] == ["import re"]


def make_service(claim_error: Exception | None, *, clock: list[float] | None = None) -> tuple[SV.KbPushService, MagicMock]:
    graph = MagicMock()
    existing = {"externalRecordId": "doc-old", "externalRevisionId": "pb1:" + "c" * 64 + ":" + "d" * 16, "version": 1,
                "orgId": "o", "connectorId": "kb", "recordName": "n", "indexingStatus": "COMPLETED", "id": "rid"}
    graph.get_document = AsyncMock(return_value=existing)
    graph.get_record_parent_info = AsyncMock(return_value=None)
    graph.find_file_by_name_in_parent = AsyncMock(return_value=None)
    storage = MagicMock()
    storage.create = AsyncMock(return_value="doc-new")
    processor = MagicMock()
    processor.on_pushed_record = AsyncMock(side_effect=claim_error)
    svc = SV.KbPushService(
        graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(return_value=processor), schema_registry=MagicMock(),
        storage=storage, worker=MagicMock(), spool=MagicMock(), publish_event=AsyncMock(), sleep=AsyncMock(),
    )
    svc._claim_and_write = AsyncMock(side_effect=claim_error)
    svc._storage = storage
    return svc, processor


async def push_changed(svc: SV.KbPushService) -> None:
    v = validated()
    v.version = 2
    v.digest = "e" * 64
    v.content_rev = "f" * 16
    await svc._write(kb_id="kb", user_id="u", org_id="o", v=v, folder_id=None)


async def test_a_loss_on_every_attempt_is_five_claims_then_409() -> None:
    svc, _ = make_service(FakeArangoError(1654, 410))
    with pytest.raises(PushRefusal) as e:
        await push_changed(svc)
    assert (e.value.status, e.value.reason) == (409, "PUSH_CONFLICT")
    assert svc._claim_and_write.await_count == SV.MAX_ATTEMPTS
    assert svc.storage.create.await_count == 1  # the document is reused


async def test_the_retry_loop_stops_when_its_time_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0, 1.0, 2.0, SV.RETRY_BUDGET_S + 1, SV.RETRY_BUDGET_S + 2])  # first loss, then one reading per retry
    monkeypatch.setattr(SV, "time", types.SimpleNamespace(monotonic=lambda: next(ticks)))
    svc, _ = make_service(FakeArangoError(1654, 410))
    with pytest.raises(PushRefusal) as e:
        await push_changed(svc)
    assert e.value.reason == "PUSH_CONFLICT" and svc._claim_and_write.await_count == 3


async def test_a_slow_first_attempt_does_not_spend_the_retry_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first attempt may take long under load (a trash racing it); only the retries are bounded."""
    clock = iter([100.0 + i * 0.01 for i in range(20)])  # the first loss happens at t=100, long after the push began
    monkeypatch.setattr(SV, "time", types.SimpleNamespace(monotonic=lambda: next(clock)))
    svc, _ = make_service(FakeArangoError(1654, 410))
    with pytest.raises(PushRefusal):
        await push_changed(svc)
    assert svc._claim_and_write.await_count == SV.MAX_ATTEMPTS


async def test_an_error_that_is_not_a_loss_is_raised_after_one_claim() -> None:
    svc, _ = make_service(FakeArangoError(1210))
    with pytest.raises(FakeArangoError):
        await push_changed(svc)
    assert svc._claim_and_write.await_count == 1


async def test_the_processors_write_is_not_wrapped_in_a_second_retry() -> None:
    import inspect

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

    assert not hasattr(DataSourceEntitiesProcessor.on_pushed_record, "__wrapped__"), "retry_on_deadlock nests a second loop"
    assert inspect.iscoroutinefunction(DataSourceEntitiesProcessor.on_pushed_record)


def test_the_batch_wording_is_matched_only_in_full() -> None:
    assert R.is_attempt_lost(Exception("Batch insert failed with 2 error(s): Item 0: [1210] dup; Item 1: [1200] timeout waiting to lock key"))
    assert not R.is_attempt_lost(Exception("see Batch insert failed with 1 error(s): Item 0: [1200] x"))
    assert not R.is_attempt_lost(Exception("Batch insert failed (status=500): [1200]"))


async def test_a_commit_whose_outcome_is_unknown_leaves_the_document_to_the_sweep() -> None:
    """the store may apply a commit after the client gave up, so the document is not deleted."""
    class CommitTimeout(Exception):
        commit_outcome_unknown = True

    svc, _ = make_service(CommitTimeout("commit response lost"))
    svc.graph.get_document = AsyncMock(return_value=None)  # a create: nothing stored yet as far as the re-read can tell
    svc._cleanup = AsyncMock()
    v = validated()
    with pytest.raises(CommitTimeout):
        await svc._write(kb_id="kb", user_id="u", org_id="o", v=v, folder_id=None)
    svc._cleanup.assert_not_awaited()


async def test_any_other_failure_still_cleans_the_new_document() -> None:
    svc, _ = make_service(RuntimeError("graph down"))
    svc.graph.get_document = AsyncMock(return_value=None)
    svc._cleanup = AsyncMock()
    with pytest.raises(RuntimeError):
        await svc._write(kb_id="kb", user_id="u", org_id="o", v=validated(), folder_id=None)
    svc._cleanup.assert_awaited_once()
