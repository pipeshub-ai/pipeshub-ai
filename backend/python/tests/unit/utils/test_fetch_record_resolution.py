"""Resolving record ids: concurrency, shared collaborators, and why an id
produced nothing.

The security rule these encode: *not available* deliberately covers both "you
may not read it" and "it does not exist". Distinguishing them would make the
tool an existence oracle for records the caller cannot see.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.fetch_full_record import (
    NOT_INDEXED_YET,
    STORAGE_ERROR,
    UNAVAILABLE,
    _fetch_multiple_records_impl,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

MODULE = "app.utils.fetch_full_record"


def _graph_provider(
    *,
    access: bool | set[str] = True,
    indexing_status: str | None = None,
) -> MagicMock:
    """``access``: every id, none, or these ids."""
    provider = MagicMock()
    provider.config_service = MagicMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"})
    provider.check_access = AsyncMock(
        side_effect=lambda _user_key, _org_id, node_ids=(), **_: AccessCheck(node_ids=frozenset(
            i for i in node_ids if access is True or (access and i in access)
        )),
    )
    provider.get_document = AsyncMock(return_value={
        "virtualRecordId": "vr-new",
        "indexingStatus": indexing_status or ProgressStatus.COMPLETED.value,
    })
    return provider


def _cached_record(record_id: str) -> dict:
    return {"id": record_id, "record_name": f"Record {record_id}", "record_type": "FILE", "block_containers": {"blocks": []}}


async def _fetch(record_ids: list[str], **kwargs) -> object:
    return await _fetch_multiple_records_impl(
        record_ids,
        kwargs.pop("virtual_record_id_to_result", {}),
        graph_provider=kwargs.pop("graph_provider", _graph_provider()),
        org_id=kwargs.pop("org_id", "org-1"),
        user_id=kwargs.pop("user_id", "user-1"),
        **kwargs,
    )


class TestCachedRecords:
    async def test_a_map_entry_is_checked_in_the_one_batch_not_per_id(self) -> None:
        provider = _graph_provider()
        result = await _fetch(
            ["rec-1"],
            virtual_record_id_to_result={"vr-1": _cached_record("rec-1")},
            graph_provider=provider,
        )

        assert result["ok"] is True
        assert result["records"][0]["virtual_record_id"] == "vr-1"
        provider.check_access.assert_awaited_once()

    async def test_a_map_entry_the_user_may_not_read_is_not_served(self) -> None:
        """The map is also filled by attachment and FK enrichment, which do not
        verify: being in it proves nothing."""
        result = await _fetch(
            ["rec-1"],
            virtual_record_id_to_result={"vr-1": _cached_record("rec-1")},
            graph_provider=_graph_provider(access=False),
        )

        assert result["ok"] is False
        assert result["unavailable_reasons"]["rec-1"] == UNAVAILABLE

    async def test_a_failed_batch_check_serves_nothing(self) -> None:
        provider = _graph_provider()
        provider.check_access = AsyncMock(side_effect=RuntimeError("graph down"))
        result = await _fetch(
            ["rec-1"],
            virtual_record_id_to_result={"vr-1": _cached_record("rec-1")},
            graph_provider=provider,
        )

        assert result["ok"] is False
        assert result["unavailable_reasons"]["rec-1"] == STORAGE_ERROR

    async def test_order_follows_the_requested_ids(self) -> None:
        """Concurrent resolution must not reorder the model's records."""
        cache = {f"vr-{i}": _cached_record(f"rec-{i}") for i in range(5)}
        result = await _fetch([f"rec-{i}" for i in range(5)], virtual_record_id_to_result=cache)

        assert [r["id"] for r in result["records"]] == [f"rec-{i}" for i in range(5)]


class TestConcurrency:
    async def test_ids_resolve_concurrently(self) -> None:
        """Ten ids used to be thirty sequential round trips."""
        in_flight = 0
        peak = 0

        async def slow_lookup(*_args, **_kwargs) -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            # not found: keeps the test to the lookup step

        provider = _graph_provider()
        provider.get_document = AsyncMock(side_effect=slow_lookup)

        await _fetch([f"rec-{i}" for i in range(10)], graph_provider=provider)

        assert peak > 1, "resolution ran sequentially"

    async def test_concurrency_is_bounded(self) -> None:
        """Unbounded fan-out would hammer the graph and blob stores."""
        in_flight = 0
        peak = 0

        async def slow_lookup(*_args, **_kwargs) -> None:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            # not found: keeps the test to the lookup step

        provider = _graph_provider()
        provider.get_document = AsyncMock(side_effect=slow_lookup)

        await _fetch([f"rec-{i}" for i in range(40)], graph_provider=provider)

        assert peak <= 5


class TestSharedCollaborators:
    async def test_one_blob_client_and_one_endpoint_read_per_call(self) -> None:
        """Both used to be rebuilt inside the loop, once per resolved record."""
        provider = _graph_provider()
        blob_instance = MagicMock()
        blob_instance.config_service = MagicMock()
        blob_instance.config_service.get_config = AsyncMock(return_value={})

        async def fake_get_record(vrid, mapping, *_args, **_kwargs) -> None:
            mapping[vrid] = _cached_record("downloaded")

        with patch(f"{MODULE}.BlobStorage", return_value=blob_instance) as blob_cls, \
             patch(f"{MODULE}.get_record", side_effect=fake_get_record):
            await _fetch([f"rec-{i}" for i in range(6)], graph_provider=provider)

        assert blob_cls.call_count == 1
        assert blob_instance.config_service.get_config.await_count == 1


class TestUnavailableReasons:
    async def test_no_access_and_not_found_share_one_reason(self) -> None:
        """Distinguishing them tells a caller that a record they may not read
        exists — an existence oracle."""
        denied = _graph_provider(access=False)
        missing = _graph_provider()
        missing.get_document = AsyncMock(return_value=None)

        denied_result = await _fetch(["secret"], graph_provider=denied)
        missing_result = await _fetch(["ghost"], graph_provider=missing)

        assert denied_result["unavailable_reasons"]["secret"] == UNAVAILABLE
        assert missing_result["unavailable_reasons"]["ghost"] == UNAVAILABLE

    async def test_a_record_still_indexing_says_so(self) -> None:
        """Actionable, and leaks nothing: the caller already passed the access
        check to get here."""
        provider = _graph_provider(indexing_status=ProgressStatus.IN_PROGRESS.value)
        result = await _fetch(["fresh"], graph_provider=provider)

        assert result["unavailable_reasons"]["fresh"] == NOT_INDEXED_YET

    async def test_a_storage_failure_is_not_reported_as_absence(self) -> None:
        """It used to be swallowed by `except Exception: pass`, so an outage
        looked exactly like a record that does not exist."""
        provider = _graph_provider()
        provider.get_document = AsyncMock(side_effect=RuntimeError("graph down"))

        result = await _fetch(["rec-1"], graph_provider=provider)

        assert result["unavailable_reasons"]["rec-1"] == STORAGE_ERROR

    async def test_reasons_survive_when_nothing_resolved(self) -> None:
        """A bare 'none were found' leaves the model unable to tell a typo
        from a record that is still indexing."""
        provider = _graph_provider(indexing_status=ProgressStatus.IN_PROGRESS.value)
        result = await _fetch(["a", "b"], graph_provider=provider)

        assert result["ok"] is False
        assert sorted(result["not_available_ids"]) == ["a", "b"]
        assert result["unavailable_reasons"]["a"] == NOT_INDEXED_YET

    async def test_no_user_means_no_resolution(self) -> None:
        """An id outside the ACL-filtered map is unverified; without a user to
        check it against it is never served."""
        provider = _graph_provider()
        result = await _fetch(["rec-1"], graph_provider=provider, user_id=None)

        assert result["ok"] is False
        assert result["unavailable_reasons"]["rec-1"] == UNAVAILABLE

    async def test_empty_record_ids_says_so(self) -> None:
        result = await _fetch([])
        assert result["ok"] is False
        assert "No record IDs were provided" in result["error"]

    async def test_a_partial_batch_still_returns_what_resolved(self) -> None:
        provider = _graph_provider(access={"rec-1"})
        result = await _fetch(
            ["rec-1", "denied"],
            virtual_record_id_to_result={"vr-1": _cached_record("rec-1")},
            graph_provider=provider,
        )

        assert result["ok"] is True
        assert [r["id"] for r in result["records"]] == ["rec-1"]
        assert result["not_available_ids"] == ["denied"]


class TestToolSurface:
    """`_FetchFullRecordTool` guards the arguments a model actually sends."""

    @staticmethod
    def _tool():
        from app.agents.agent_loop.context import AgentContext
        from app.agents.agent_loop.hooks.citations import (
            CitationCollector,
            _FetchFullRecordTool,
        )

        context = AgentContext(org_id="org-1", user_id="user-1", user_email="u@example.com")
        return _FetchFullRecordTool(CitationCollector(context), context)

    async def test_continuing_several_records_at_once_is_refused(self) -> None:
        """One offset applied to every id re-reads whichever stopped later and
        skips the start of the others."""
        output = await self._tool().execute(record_ids=["a", "b"], start_block=40)

        assert output.success is False
        assert "one record at a time" in output.error

    @pytest.mark.parametrize("raw", ["not-a-number", [], {}])
    async def test_a_non_numeric_offset_is_a_tool_error_not_a_traceback(self, raw) -> None:
        output = await self._tool().execute(record_ids=["a"], start_block=raw)

        assert output.success is False
        assert "whole number" in output.error

    @pytest.mark.parametrize("raw", [-5, "-5"])
    async def test_a_negative_offset_means_the_beginning(self, raw) -> None:
        """Clamped rather than rejected: the intent is unambiguous."""
        with patch(
            "app.agents.actions.knowledge_graph.ops.fetch.execute_fetch_record",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ) as execute:
            await self._tool().execute(record_ids=["a"], start_block=raw)

        assert execute.await_args.kwargs["start_block"] == 0

    async def test_start_block_zero_with_several_ids_is_fine(self) -> None:
        """Only *continuing* is one at a time; a plain multi-record read is
        the normal case."""
        with patch(
            "app.agents.actions.knowledge_graph.ops.fetch.execute_fetch_record",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ) as execute:
            await self._tool().execute(record_ids=["a", "b", "c"])

        assert execute.await_args.kwargs["record_ids"] == ["a", "b", "c"]


# --- Parse on demand ------------------------------------------------------------

import logging  # noqa: E402
import time  # noqa: E402

from app.modules.pipeline.parse_now import ParseNow  # noqa: E402
from app.utils.fetch_full_record import PREPARING  # noqa: E402

_ORG = "org-1"


def _row(status: str | None = "NOT_STARTED", **extra) -> dict:
    return {
        "indexingStatus": status, "contentRev": "rev-1", "connectorId": "kb-1", "connectorName": "KB",
        "recordName": "Plan", "orgId": "graph-row-org", **extra,
    }


def _stored(row: dict) -> dict:
    return {**row, "virtualRecordId": "vr-1", "parseNowStatus": "STORED", "parseNowRev": "rev-1"}


class _Docs:
    """``get_document`` for the records collection: per id, the rows it returns in turn
    (the last one repeats). Counts reads per id."""

    def __init__(self, rows: dict[str, list[dict | None]]) -> None:
        self.rows = rows
        self.reads: dict[str, int] = {}

    async def __call__(self, document_key: str, collection: str = "", **_kw) -> object:
        n = self.reads.get(document_key, 0)
        self.reads[document_key] = n + 1
        seq = self.rows[document_key]
        return seq[min(n, len(seq) - 1)]

    @property
    def polls(self) -> int:
        return sum(self.reads.values()) - len(self.reads)


def _provider(docs: _Docs, **kwargs) -> MagicMock:
    provider = _graph_provider(**kwargs)

    async def get(*args, **kw) -> object:
        return await docs(*args, **kw)

    provider.get_document = AsyncMock(side_effect=get)
    return provider


def _asker(*, sent: bool | Exception = True, wait: float = 0.3) -> tuple[ParseNow, AsyncMock]:
    producer = MagicMock()
    if isinstance(sent, Exception):
        producer.send_message = AsyncMock(side_effect=sent)
    else:
        producer.send_message = AsyncMock(return_value=sent)
    config = MagicMock()
    config.get_config = AsyncMock(return_value={})
    asker = ParseNow(config, logging.getLogger("test_parse_now"), producer)  # no redis: dedup off, always send
    patch(f"{MODULE}.parse_now_wait_seconds", AsyncMock(return_value=wait)).start()
    return asker, producer.send_message


@pytest.fixture(autouse=True)
def _quick_polls(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(f"{MODULE}._POLL_STEPS", (0.01,))
    monkeypatch.setattr(f"{MODULE}._POLL_THEN_EVERY", 0.01)
    yield
    patch.stopall()  # the wait patched by `_asker`


@pytest.fixture
def stored_content() -> Iterator[object]:
    async def fake_get_record(vrid, mapping, *_a, **_kw) -> None:
        mapping[vrid] = _cached_record("rec-1")

    with patch(f"{MODULE}.get_record", side_effect=fake_get_record) as mock:
        yield mock


async def _parse_fetch(ids: list[str], docs: _Docs, asker: ParseNow | None, **kwargs) -> object:
    provider = kwargs.pop("provider", None) or _provider(docs)
    return await _fetch(ids, graph_provider=provider, parse_now=asker, **kwargs)


class TestParseNow:
    async def test_a_record_that_becomes_ready_is_read_and_asked_for_once(self, stored_content) -> None:  # R1, R10
        docs = _Docs({"rec-1": [_row("QUEUED"), _row("QUEUED"), _row("IN_PROGRESS"), _stored(_row("IN_PROGRESS"))]})
        asker, send = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker, user_id="user-9")

        assert result["ok"] is True and result["records"][0]["id"] == "rec-1"
        send.assert_awaited_once()
        topic, event = send.await_args.args
        assert topic == "record-events-priority" and event["eventType"] == "reindexRecord"
        assert event["payload"]["orgId"] == _ORG  # the caller's, not the graph row's
        assert event["payload"]["parseNow"] is True and event["payload"]["requestedByUserId"] == "user-9"
        assert event["payload"]["contentRev"] == "rev-1" and "forceReindex" not in event["payload"]

    async def test_ready_on_the_first_look_has_no_timeout_note(self, stored_content) -> None:  # R2
        docs = _Docs({"rec-1": [_row("QUEUED"), _stored(_row("QUEUED"))]})
        asker, send = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        assert result["ok"] is True and result["not_available_ids"] == []
        assert send.await_count == 1

    async def test_a_timeout_is_preparing_and_keeps_the_metadata(self) -> None:  # R3
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, _ = _asker(wait=0.05)

        result = await _parse_fetch(["rec-1"], docs, asker)

        assert result["ok"] is False and "being prepared" in result["error"]
        assert result["unavailable_reasons"]["rec-1"] == PREPARING
        entry = result["not_indexed_records"]["rec-1"]
        assert entry["preparing"] == "waiting" and "Name: Plan" in entry["context"]

    async def test_a_timeout_on_a_record_with_indexing_off_does_not_promise_it_stays_unindexed(self) -> None:  # R4
        from app.utils.not_indexed import not_indexed_note

        docs = _Docs({"rec-1": [_row("AUTO_INDEX_OFF")]})
        asker, _ = _asker(wait=0.05)

        result = await _parse_fetch(["rec-1"], docs, asker)

        entry = result["not_indexed_records"]["rec-1"]
        note = not_indexed_note(entry["status"], entry["preparing"])
        assert "stays unindexed" not in note and "being prepared" in note

    @pytest.mark.parametrize("status", [
        "PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY", "ENABLE_MULTIMODAL_MODELS", "SKIPPED", None, "WEIRD",
    ])
    async def test_final_statuses_are_never_asked_for(self, status) -> None:  # R5
        docs = _Docs({"rec-1": [_row(status)]})
        asker, send = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        send.assert_not_awaited()
        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET

    @pytest.mark.parametrize("status", ["NOT_STARTED", "QUEUED", "IN_PROGRESS", "AUTO_INDEX_OFF", "FAILED"])
    async def test_waiting_statuses_are_asked_for_once(self, status) -> None:  # R6
        docs = _Docs({"rec-1": [_row(status)]})
        asker, send = _asker(wait=0.03)

        await _parse_fetch(["rec-1"], docs, asker)

        assert send.await_count == 1

    async def test_without_parse_now_nothing_changes(self) -> None:  # R7
        docs = _Docs({"rec-1": [_row("QUEUED")]})

        result = await _parse_fetch(["rec-1"], docs, None)

        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET
        assert docs.polls == 0

    async def test_a_record_with_an_indexed_copy_is_read_normally(self, stored_content) -> None:  # R8
        docs = _Docs({"rec-1": [_row("QUEUED", hasIndexedCopy=True, virtualRecordId="vr-1")]})
        asker, send = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        assert result["ok"] is True
        send.assert_not_awaited()

    async def test_no_access_no_user_or_hidden_demo_never_ask(self) -> None:  # R9, L1
        asker, send = _asker(wait=0.03)
        docs = _Docs({"rec-1": [_row("QUEUED")]})

        denied = await _parse_fetch(["rec-1"], docs, asker, provider=_provider(docs, access=False))
        no_user = await _parse_fetch(["rec-1"], docs, asker, user_id=None)
        with patch(f"{MODULE}.excluded_demo_connector_ids", AsyncMock(return_value=frozenset({"kb-1"}))):
            hidden = await _parse_fetch(["rec-1"], docs, asker)

        send.assert_not_awaited()
        assert denied["unavailable_reasons"] == {"rec-1": UNAVAILABLE}
        assert no_user["unavailable_reasons"] == {"rec-1": UNAVAILABLE}
        assert hidden["unavailable_reasons"] == {"rec-1": UNAVAILABLE}

    async def test_a_trashed_record_is_never_asked_for(self) -> None:  # R9, L1
        asker, send = _asker(wait=0.03)

        await _parse_fetch(["rec-1"], _Docs({"rec-1": [_row("QUEUED", isDeleted=True)]}), asker)

        send.assert_not_awaited()

    async def test_denied_and_missing_look_the_same(self) -> None:  # L3
        asker, _ = _asker(wait=0.03)
        missing = await _parse_fetch(["x"], _Docs({"x": [None]}), asker)
        docs = _Docs({"x": [_row("QUEUED")]})
        denied = await _parse_fetch(["x"], docs, asker, provider=_provider(docs, access=False))

        assert missing == denied

    @pytest.mark.parametrize("app", [{"id": "kb-1", "isActive": False}, None])
    async def test_connector_off_or_removed_never_asks(self, app) -> None:  # L1
        asker, send = _asker(wait=0.03)
        docs = _Docs({"rec-1": [_row("QUEUED", origin="CONNECTOR")]})
        provider = _provider(docs)

        async def get(document_key, collection="", **kw) -> object:
            return app if collection == "apps" else await docs(document_key, collection, **kw)

        provider.get_document = AsyncMock(side_effect=get)

        result = await _parse_fetch(["rec-1"], docs, asker, provider=provider)

        send.assert_not_awaited()
        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET

    @pytest.mark.parametrize("sent", [False, RuntimeError("broker down")])
    async def test_a_publish_that_fails_is_todays_reply(self, sent) -> None:  # R12
        docs = _Docs({"a": [_row("QUEUED")], "b": [_row("QUEUED")]})
        asker, _ = _asker(sent=sent)

        result = await _parse_fetch(["a", "b"], docs, asker)

        assert result["unavailable_reasons"] == {"a": NOT_INDEXED_YET, "b": NOT_INDEXED_YET}
        assert "being prepared" not in result["error"]

    @pytest.mark.parametrize("marker", ["PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY", "NEEDS_MULTIMODAL"])
    async def test_a_parse_that_ends_for_good_is_reported_with_its_status(self, marker) -> None:  # R13, L15
        ended = _row("QUEUED", parseNowStatus=marker, parseNowRev="rev-1")
        docs = _Docs({"rec-1": [_row("QUEUED"), ended]})
        asker, _ = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET
        assert result["not_indexed_records"]["rec-1"]["status"] != "QUEUED"
        assert "preparing" not in result["not_indexed_records"]["rec-1"]

    async def test_an_ended_parse_at_this_revision_is_not_asked_for_again(self) -> None:
        docs = _Docs({"rec-1": [_row("FAILED", parseNowStatus="PASSWORD_PROTECTED", parseNowRev="rev-1")]})
        asker, send = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        send.assert_not_awaited()
        assert result["not_indexed_records"]["rec-1"]["status"] == "PASSWORD_PROTECTED"

    async def test_a_marker_of_an_older_revision_is_ignored(self) -> None:
        docs = _Docs({"rec-1": [_row("QUEUED", parseNowStatus="FAILED", parseNowRev="rev-0")]})
        asker, send = _asker(wait=0.03)

        await _parse_fetch(["rec-1"], docs, asker)

        assert send.await_count == 1

    @pytest.mark.parametrize("blob_read", ["raises", "none"])
    async def test_ready_but_unreadable_is_an_error_and_the_map_never_holds_none(self, blob_read) -> None:  # R14
        docs = _Docs({"rec-1": [_row("QUEUED"), _stored(_row("QUEUED"))]})
        asker, _ = _asker()
        shared: dict = {}
        maps_during_polling: list[dict] = []

        async def fake_get_record(vrid, mapping, *_a, **_kw) -> None:
            maps_during_polling.append(dict(mapping))
            if blob_read == "raises":
                raise OSError("blob down")

        with patch(f"{MODULE}.get_record", side_effect=fake_get_record):
            result = await _parse_fetch(["rec-1"], docs, asker, virtual_record_id_to_result=shared)

        assert result["unavailable_reasons"]["rec-1"] == (STORAGE_ERROR if blob_read == "raises" else UNAVAILABLE)
        assert result["ok"] is False
        assert maps_during_polling == [{}] and shared == {}

    async def test_a_record_stored_for_indexing_off_is_still_read_after_the_parse(self, stored_content) -> None:  # R15
        docs = _Docs({"rec-1": [_row("AUTO_INDEX_OFF"), _stored(_row("AUTO_INDEX_OFF"))]})
        asker, _ = _asker()

        result = await _parse_fetch(["rec-1"], docs, asker)

        assert result["ok"] is True

    async def test_many_ids_are_asked_first_and_wait_one_shared_deadline(self, monkeypatch) -> None:  # R16
        monkeypatch.setenv("PARSE_NOW_MAX_PER_CALL", "50")
        ids = [f"rec-{i}" for i in range(12)]
        docs = _Docs({i: [_row("QUEUED")] for i in ids})
        asker, send = _asker(wait=0.2)
        polls_at_each_send: list[int] = []

        async def watching(*_a, **_kw) -> bool:
            polls_at_each_send.append(docs.polls)
            return True

        send.side_effect = watching
        started = time.monotonic()

        result = await _parse_fetch(ids, docs, asker)

        elapsed = time.monotonic() - started
        assert send.await_count == 12 and set(polls_at_each_send) == {0}  # no look before every ask
        assert 0.15 < elapsed < 1.0  # one deadline, not twelve
        assert set(result["unavailable_reasons"].values()) == {PREPARING}

    async def test_a_mixed_batch_keeps_the_requested_order(self, stored_content) -> None:  # R17
        docs = _Docs({
            "indexed": [_row("COMPLETED", virtualRecordId="vr-1")],
            "ready": [_row("QUEUED"), _stored(_row("QUEUED"))],
            "slow": [_row("QUEUED")],
            "denied": [_row("QUEUED")],
        })
        asker, _ = _asker(wait=0.1)

        result = await _parse_fetch(["indexed", "ready", "slow", "denied"], docs, asker,
                                    provider=_provider(docs, access={"indexed", "ready", "slow"}))

        assert result["ok"] is True and result["record_count"] == 2
        assert result["not_available_ids"] == ["slow", "denied"]
        assert result["unavailable_reasons"] == {"slow": PREPARING, "denied": UNAVAILABLE}
        assert set(result["not_indexed_records"]) == {"slow"}

    async def test_the_same_id_twice_is_asked_once(self) -> None:  # R18
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, send = _asker(wait=0.03)

        await _parse_fetch(["rec-1", "rec-1"], docs, asker)

        assert send.await_count == 1

    async def test_a_repeat_while_the_event_is_in_flight_still_waits(self, stored_content) -> None:  # R18
        docs = _Docs({"rec-1": [_row("QUEUED"), _stored(_row("QUEUED"))]})
        asker, send = _asker()
        with patch.object(ParseNow, "_claim", AsyncMock(return_value=False)):
            result = await _parse_fetch(["rec-1"], docs, asker)

        send.assert_not_awaited()
        assert result["ok"] is True

    async def test_only_records_still_waiting_are_looked_at(self) -> None:  # R19
        docs = _Docs({
            "a": [_row("QUEUED"), _stored(_row("QUEUED"))],
            "b": [_row("QUEUED")],
        })
        asker, _ = _asker(wait=0.1)
        reads_when_content_read: list[int] = []

        async def fake_get_record(vrid, mapping, *_a, **_kw) -> None:
            reads_when_content_read.append(docs.reads["a"])
            mapping[vrid] = _cached_record("a")

        with patch(f"{MODULE}.get_record", side_effect=fake_get_record):
            await _parse_fetch(["a", "b"], docs, asker)

        assert reads_when_content_read == [2]  # no content read until a look found it
        assert docs.reads["a"] == 2  # not looked at again once ready
        assert docs.reads["b"] > 3

    async def test_a_stored_record_is_read_without_a_wait_or_an_event(self, stored_content) -> None:  # R20
        docs = _Docs({"rec-1": [_stored(_row("AUTO_INDEX_OFF"))]})

        result = await _parse_fetch(["rec-1"], docs, None)

        assert result["ok"] is True
        assert docs.polls == 0

    async def test_a_stored_record_of_an_older_revision_is_not_readable(self) -> None:
        docs = _Docs({"rec-1": [{**_stored(_row("QUEUED")), "contentRev": "rev-2"}]})

        result = await _parse_fetch(["rec-1"], docs, None)

        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET

    async def test_cancelling_mid_wait_stops_the_looks(self) -> None:  # R21
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, _ = _asker(wait=30)

        task = asyncio.create_task(_parse_fetch(["rec-1"], docs, asker))
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        reads = docs.reads["rec-1"]
        await asyncio.sleep(0.1)

        assert docs.reads["rec-1"] == reads

    async def test_parse_now_off_means_no_events(self) -> None:  # L13
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, send = _asker(wait=0)

        result = await _parse_fetch(["rec-1"], docs, asker)

        send.assert_not_awaited()
        assert result["unavailable_reasons"]["rec-1"] == NOT_INDEXED_YET
        assert docs.polls == 0

    async def test_a_call_asks_for_only_three_and_answers_the_rest_ask_again(self) -> None:  # L5
        ids = [f"rec-{i}" for i in range(500)]
        docs = _Docs({i: [_row("QUEUED")] for i in ids})
        asker, send = _asker(wait=0.05)

        result = await _parse_fetch(ids, docs, asker)

        assert send.await_count == 3
        entries = result["not_indexed_records"]
        assert sorted(e["preparing"] for e in entries.values()).count("waiting") == 3
        assert sorted(e["preparing"] for e in entries.values()).count("deferred") == 497
        assert set(result["unavailable_reasons"].values()) == {PREPARING}

    async def test_the_limit_per_call_can_be_set_by_one_variable(self, monkeypatch) -> None:  # L5
        monkeypatch.setenv("PARSE_NOW_MAX_PER_CALL", "5")
        docs = _Docs({f"r{i}": [_row("QUEUED")] for i in range(9)})
        asker, send = _asker(wait=0.03)

        await _parse_fetch([f"r{i}" for i in range(9)], docs, asker)

        assert send.await_count == 5

    async def test_the_connection_limit_filters_what_a_timeout_keeps(self) -> None:  # R11
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, _ = _asker(wait=0.03)
        limit = {"apps": ["kb-1"]}
        with patch(f"{MODULE}.keep_record_text_inside_connection", AsyncMock()) as keep:
            await _parse_fetch(["rec-1"], docs, asker, connection_sources=limit)

        call = keep.await_args_list[0]
        assert call.args[0][0]["id"] == "rec-1" and call.args[4] == limit

    async def test_a_wait_that_ends_in_time_serves_the_next_call_at_once(self, stored_content) -> None:  # L15
        docs = _Docs({"rec-1": [_row("QUEUED")]})
        asker, send = _asker(wait=0.05)
        first = await _parse_fetch(["rec-1"], docs, asker)
        docs.rows["rec-1"] = [_stored(_row("QUEUED"))]
        started = time.monotonic()
        second = await _parse_fetch(["rec-1"], docs, asker)

        assert first["unavailable_reasons"]["rec-1"] == PREPARING
        assert second["ok"] is True and time.monotonic() - started < 0.04
        assert send.await_count == 1
