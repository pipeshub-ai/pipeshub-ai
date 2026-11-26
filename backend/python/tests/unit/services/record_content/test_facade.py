"""Unit tests for the agent facade (resolve_attachments).

Covers:
- Empty refs → empty bundle
- Missing runtime context → failure bundle
- Single record resolved → bundle with one item
- Access denied record → failure bundle entry, no bytes returned
- Deduplication: duplicate ref IDs resolved once
- Total size cap: second record pushes over budget → second in failures
- No-external-call-on-denied-record regression: resolver not called when
  context is malformed
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.record_content.models import (
    AttachmentFailure,
    RecordAccessDeniedError,
    RecordNotFoundError,
    ResolvedRecordContent,
)
from app.utils.error_class import mark_error
from tests.support.log_capture import assert_text_free
from tests.support.record_access import InMemoryRecordAccess


def _make_state(**kwargs):
    defaults = {
        "org_id": "org1",
        "user_id": "user1",
        "config_service": MagicMock(),
        "graph_provider": AsyncMock(),
        "conversation_id": "conv1",
        "blob_store": None,
    }
    defaults.update(kwargs)
    return defaults


def _resolved(record_id="rec1", size=10):
    return ResolvedRecordContent(
        record_id=record_id,
        filename=f"{record_id}.txt",
        mime_type="text/plain",
        size_bytes=size,
        content=b"x" * size,
        version=None,
        source="blob",
    )


@pytest.mark.asyncio
async def test_empty_refs_returns_empty_bundle():
    from app.agents.actions.util.attachments import resolve_attachments

    bundle = await resolve_attachments(_make_state(), None)
    assert not bundle.resolved
    assert not bundle.failures


@pytest.mark.asyncio
async def test_missing_context_returns_failure():
    from app.agents.actions.util.attachments import resolve_attachments

    bad_state = "not a dict"
    bundle = await resolve_attachments(bad_state, ["rec1"])
    assert not bundle.resolved
    assert bundle.failures
    assert bundle.failures[0].error_type == "RuntimeError"


@pytest.mark.asyncio
async def test_missing_org_id_returns_failure():
    from app.agents.actions.util.attachments import resolve_attachments

    state = _make_state(org_id=None)
    bundle = await resolve_attachments(state, ["rec1"])
    assert not bundle.resolved
    assert bundle.failures


@pytest.mark.asyncio
async def test_single_record_resolved():
    from app.agents.actions.util.attachments import resolve_attachments

    resolved = _resolved("rec1", size=100)

    with patch(
        "app.agents.actions.util.attachments.RecordContentResolver"
    ) as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(return_value=resolved)
        MockResolver.return_value = instance

        bundle = await resolve_attachments(_make_state(), ["rec1"])

    assert len(bundle.resolved) == 1
    assert bundle.resolved[0].record_id == "rec1"
    assert not bundle.failures
    assert bundle.total_bytes == 100


@pytest.mark.asyncio
async def test_access_denied_goes_to_failures():
    from app.agents.actions.util.attachments import resolve_attachments

    with patch(
        "app.agents.actions.util.attachments.RecordContentResolver"
    ) as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(
            side_effect=RecordAccessDeniedError("denied")
        )
        MockResolver.return_value = instance

        bundle = await resolve_attachments(_make_state(), ["rec1"])

    assert not bundle.resolved
    assert len(bundle.failures) == 1
    assert bundle.failures[0].error_type == "RecordAccessDeniedError"


@pytest.mark.asyncio
async def test_deduplication_calls_resolver_once():
    from app.agents.actions.util.attachments import resolve_attachments

    resolved = _resolved("rec1", size=10)

    with patch(
        "app.agents.actions.util.attachments.RecordContentResolver"
    ) as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(return_value=resolved)
        MockResolver.return_value = instance

        bundle = await resolve_attachments(_make_state(), ["rec1", "rec1", "rec1"])

    assert instance.resolve.call_count == 1
    assert len(bundle.resolved) == 1


@pytest.mark.asyncio
async def test_total_size_cap_puts_excess_in_failures():
    from app.agents.actions.util.attachments import resolve_attachments

    rec1 = _resolved("rec1", size=20)
    rec2 = _resolved("rec2", size=10)

    with patch(
        "app.agents.actions.util.attachments.RecordContentResolver"
    ) as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(side_effect=[rec1, rec2])
        MockResolver.return_value = instance

        # cap = 25 bytes; rec1 (20) fits; rec2 (10) would push to 30
        bundle = await resolve_attachments(
            _make_state(), ["rec1", "rec2"],
            max_total_bytes=25,
        )

    assert len(bundle.resolved) == 1
    assert bundle.resolved[0].record_id == "rec1"
    assert len(bundle.failures) == 1
    assert bundle.failures[0].ref == "rec2"
    assert bundle.total_bytes == 20


@pytest.mark.asyncio
async def test_too_many_refs_returns_failure():
    from app.agents.actions.util.attachments import (
        MAX_ATTACHMENTS_PER_CALL,
        resolve_attachments,
    )

    refs = [f"rec{i}" for i in range(MAX_ATTACHMENTS_PER_CALL + 1)]

    with patch(
        "app.agents.actions.util.attachments.RecordContentResolver"
    ) as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(return_value=_resolved())
        MockResolver.return_value = instance

        bundle = await resolve_attachments(_make_state(), refs)

    assert not bundle.resolved
    assert bundle.failures
    instance.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_records_are_resolved_as_the_run_user_and_artifacts_as_their_owner() -> None:
    """A service-account agent attaches knowledge with its creator's access and
    the chat's own files as the person chatting."""
    from app.agents.actions.util.attachments import resolve_attachments

    with patch("app.agents.actions.util.attachments.RecordContentResolver") as MockResolver:
        instance = AsyncMock()
        instance.resolve = AsyncMock(return_value=_resolved("rec1"))
        MockResolver.return_value = instance
        await resolve_attachments(_make_state(user_id="u-alice", artifact_owner_user_id="u-bob"), ["rec1"])

    kwargs = instance.resolve.await_args.kwargs
    assert (kwargs["actor"].user_id, kwargs["artifact_actor"].user_id) == ("u-alice", "u-bob")


@pytest.mark.asyncio
async def test_a_graph_outage_is_not_reported_to_the_model_as_not_found() -> None:
    """F58: the real resolver behind the facade, with a graph that cannot be read."""
    from app.agents.actions.util.attachments import resolve_attachments

    graph = AsyncMock()
    graph.get_record_by_id = AsyncMock(side_effect=ConnectionError("graph down"))
    bundle = await resolve_attachments(_make_state(graph_provider=graph), ["rec1"])

    assert not bundle.resolved
    [failure] = bundle.failures
    assert failure.ref == "rec1"
    assert failure.error_type == "RecordContentError"
    assert "try again" in failure.error.lower()
    assert "not found" not in failure.error.lower()
    assert "graph down" not in failure.error


@pytest.mark.asyncio
async def test_an_outage_no_query_recorded_is_logged_once_without_its_text(caplog: pytest.LogCaptureFixture) -> None:
    """No pooled connection: the db layer raises before any statement, so no db.query.failed names the failure."""
    from app.agents.actions.util.attachments import resolve_attachments
    from app.core.db.errors import PoolTimeout

    graph = AsyncMock()
    graph.get_record_by_id = AsyncMock(side_effect=PoolTimeout("no pooled connection became free in time"))
    with caplog.at_level(logging.DEBUG, logger="app.agents.actions.util.attachments"):
        bundle = await resolve_attachments(_make_state(graph_provider=graph), ["rec1"])

    assert bundle.failures[0].error_type == "RecordContentError"
    [line] = [r for r in caplog.records if r.name == "app.agents.actions.util.attachments" and r.levelno >= logging.WARNING]
    assert (line.getMessage(), line.levelno, line.ref, line.error_class) == ("attachment.resolve.failed", logging.WARNING, "rec1", "timeout")
    assert getattr(line, "error_message", None) is None and line.exc_info is None


@pytest.mark.asyncio
async def test_an_outage_the_db_layer_recorded_is_not_logged_again(caplog: pytest.LogCaptureFixture) -> None:
    from app.agents.actions.util.attachments import resolve_attachments

    down = ConnectionError("graph down")
    mark_error(down, "unavailable")  # what db.query.failed stamps on the error it logged
    graph = AsyncMock()
    graph.get_record_by_id = AsyncMock(side_effect=down)
    with caplog.at_level(logging.DEBUG, logger="app.agents.actions.util.attachments"):
        await resolve_attachments(_make_state(graph_provider=graph), ["rec1"])

    assert not [r for r in caplog.records if r.name == "app.agents.actions.util.attachments" and r.levelno >= logging.WARNING]


@pytest.mark.asyncio
async def test_a_provider_without_raise_on_error_is_logged_as_a_bug_not_reported_as_an_outage(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A provider (or fake) that lacks the kwarg would otherwise make every lookup a silent "try again later"."""
    from app.agents.actions.util.attachments import resolve_attachments

    async def _old_signature(record_id: str) -> None:
        return None

    graph = AsyncMock()
    graph.get_record_by_id = _old_signature
    with caplog.at_level(logging.DEBUG, logger="app.agents.actions.util.attachments"):
        bundle = await resolve_attachments(_make_state(graph_provider=graph), ["rec1"])

    [failure] = bundle.failures
    assert failure.error_type == "TypeError"
    [line] = [r for r in caplog.records if r.name == "app.agents.actions.util.attachments" and r.levelno >= logging.WARNING]
    assert line.levelno == logging.ERROR and line.exc_info is not None


def _live_record() -> MagicMock:
    record = MagicMock()
    record.id, record.org_id, record.is_deleted, record.record_type = "rec1", "org1", False, "FILE"
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("recorded", [True, False], ids=["db_layer_logged_it", "nothing_logged_it"])
async def test_an_access_check_the_graph_could_not_answer_is_safe_for_the_model(
    caplog: pytest.LogCaptureFixture, recorded: bool,
) -> None:
    """F58d: the record was read, the access check behind it could not be made. The model gets our text, not the driver's."""
    from app.agents.actions.util.attachments import resolve_attachments
    from app.services.record_content.resolver import RecordContentResolver

    down = ConnectionError("graph down at 10.0.0.7:8529")
    if recorded:
        mark_error(down, "unavailable")  # what db.query.failed stamps on the error it logged
    graph = AsyncMock()
    graph.get_record_by_id = AsyncMock(return_value=_live_record())
    InMemoryRecordAccess().user("user1", "org1").readable("user1", "org1", "rec1").fail_with(down).serve(graph)
    with patch.object(RecordContentResolver, "_fetch", new=AsyncMock()) as fetch, \
            caplog.at_level(logging.DEBUG):
        bundle = await resolve_attachments(_make_state(graph_provider=graph), ["rec1"])

    fetch.assert_not_awaited()
    [failure] = bundle.failures
    assert (failure.error_type, failure.error) == (
        "RecordContentError",
        "Could not check access to record rec1: the knowledge graph did not answer. Try again later.",
    )
    attachment_lines = [r for r in caplog.records if r.name == "app.agents.actions.util.attachments"]
    if recorded:
        assert not attachment_lines, "the db layer's line is the one line"
    else:
        assert [(r.getMessage(), r.levelno) for r in attachment_lines] == [("attachment.resolve.failed", logging.WARNING)]
        assert_text_free(caplog.records, "10.0.0.7", "graph down")
