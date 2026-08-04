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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.record_content.models import (
    AttachmentFailure,
    RecordAccessDeniedError,
    RecordNotFoundError,
    ResolvedRecordContent,
)


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
    from app.agents.actions.util.attachments import MAX_ATTACHMENTS_PER_CALL, resolve_attachments

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
