"""Every attachment-resolution entry point forwards the chat's conversation id
and aclVersion to the access check (PH07-10)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

_LOG = logging.getLogger("t")
_DOC = {"recordName": "a.pdf", "mimeType": "application/pdf", "virtualRecordId": "vrid-1"}


def _checker() -> AsyncMock:
    return AsyncMock(return_value=False)


def _assert_forwarded(checker: AsyncMock) -> None:
    checker.assert_awaited()
    kwargs = checker.await_args.kwargs
    assert kwargs["conversation_id"] == "conv-9"
    assert kwargs["acl_version"] == 11


async def test_attachment_utils_resolve_attachments() -> None:
    from app.utils.attachment_utils import resolve_attachments

    checker = _checker()
    with patch("app.utils.attachment_utils.caller_can_read_virtual_record", checker):
        blocks = await resolve_attachments(
            attachments=[_DOC], blob_store=MagicMock(), org_id="o", is_multimodal_llm=False, logger=_LOG,
            user_id="u", graph_provider=MagicMock(), conversation_id="conv-9", acl_version=11,
        )
    assert blocks == []
    _assert_forwarded(checker)


async def test_chat_modes_resolve_attachments() -> None:
    from app.agents.chat_modes.attachments import resolve_attachments
    from app.utils.chat_helpers import CitationRefMapper

    checker = _checker()
    with patch("app.agents.chat_modes.attachments.caller_can_read_virtual_record", checker):
        await resolve_attachments(
            [_DOC], blob_store=MagicMock(), org_id="o", ref_mapper=CitationRefMapper(), logger=_LOG,
            user_id="u", graph_provider=MagicMock(), conversation_id="conv-9", acl_version=11,
        )
    _assert_forwarded(checker)


async def test_resolve_history_attachments() -> None:
    from app.agents.agent_loop.hooks.attachment_resolver import (
        resolve_history_attachments,
    )
    from app.utils.chat_helpers import CitationRefMapper

    checker = _checker()
    with patch("app.utils.record_access.caller_can_read_virtual_record", checker):
        text, images = await resolve_history_attachments(
            [_DOC], AsyncMock(), "o", CitationRefMapper(), {},
            user_id="u", graph_provider=MagicMock(), conversation_id="conv-9", acl_version=11,
        )
    assert (text, images) == ("", [])
    _assert_forwarded(checker)
