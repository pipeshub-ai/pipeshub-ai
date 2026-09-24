"""Resolving record ids: concurrency, shared collaborators, and why an id
produced nothing.

The security rule these encode: *not available* deliberately covers both "you
may not read it" and "it does not exist". Distinguishing them would make the
tool an existence oracle for records the caller cannot see.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.utils.fetch_full_record import (
    NOT_INDEXED_YET,
    STORAGE_ERROR,
    UNAVAILABLE,
    _fetch_multiple_records_impl,
)

MODULE = "app.utils.fetch_full_record"


def _graph_provider(*, access: bool = True, indexing_status: str | None = None) -> MagicMock:
    provider = MagicMock()
    provider.config_service = MagicMock()
    provider.check_record_access_with_details = AsyncMock(return_value=access)
    provider.get_document = AsyncMock(return_value={
        "virtualRecordId": "vr-new",
        "indexingStatus": indexing_status or ProgressStatus.COMPLETED.value,
    })
    return provider


def _cached_record(record_id: str) -> dict:
    return {"id": record_id, "record_type": "FILE", "block_containers": {"blocks": []}}


async def _fetch(record_ids: list[str], **kwargs):
    return await _fetch_multiple_records_impl(
        record_ids,
        kwargs.pop("virtual_record_id_to_result", {}),
        graph_provider=kwargs.pop("graph_provider", _graph_provider()),
        org_id=kwargs.pop("org_id", "org-1"),
        user_id=kwargs.pop("user_id", "user-1"),
        **kwargs,
    )


class TestCachedRecords:
    async def test_a_record_already_in_the_map_skips_the_access_check(self) -> None:
        """The map is per request and already ACL-filtered; re-checking every
        repeat fetch would be a round trip for nothing."""
        provider = _graph_provider()
        result = await _fetch(
            ["rec-1"],
            virtual_record_id_to_result={"vr-1": _cached_record("rec-1")},
            graph_provider=provider,
        )

        assert result["ok"] is True
        assert result["records"][0]["virtual_record_id"] == "vr-1"
        provider.check_record_access_with_details.assert_not_awaited()

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

        async def slow_check(*_args, **_kwargs) -> bool:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return False        # denied: keeps the test to the ACL step

        provider = _graph_provider()
        provider.check_record_access_with_details = AsyncMock(side_effect=slow_check)

        await _fetch([f"rec-{i}" for i in range(10)], graph_provider=provider)

        assert peak > 1, "resolution ran sequentially"

    async def test_concurrency_is_bounded(self) -> None:
        """Unbounded fan-out would hammer the graph and blob stores."""
        in_flight = 0
        peak = 0

        async def slow_check(*_args, **_kwargs) -> bool:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return False

        provider = _graph_provider()
        provider.check_record_access_with_details = AsyncMock(side_effect=slow_check)

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
        provider.check_record_access_with_details.assert_not_awaited()

    async def test_empty_record_ids_says_so(self) -> None:
        result = await _fetch([])
        assert result["ok"] is False
        assert "No record IDs were provided" in result["error"]

    async def test_a_partial_batch_still_returns_what_resolved(self) -> None:
        provider = _graph_provider(access=False)
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
