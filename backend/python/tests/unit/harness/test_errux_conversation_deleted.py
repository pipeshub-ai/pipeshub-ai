"""EU-22 (QA21-F4): a run whose conversation was deleted while it ran is refused with a code naming the cause; the
worker stops it as lost and its log says why, never retrying."""

from __future__ import annotations

import logging

import httpx
import pytest

from harness_core.journal.types import AttemptToken, CommitRequest, OwnershipLost
from tests.unit.harness.test_node_journal_client import (  # pyright: ignore[reportPrivateUsage]
    _claimed,
    _Node,
)


async def test_eu_22_a_commit_after_the_conversation_was_deleted_is_lost_by_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    node = _Node()
    node.replies += [
        httpx.Response(200, json=_claimed()),
        httpx.Response(
            410,
            json={"error": {"code": "HTTP_AGENT_HARNESS_CONVERSATION_DELETED", "message": "deleted"}},
        ),
    ]
    journal = node.journal()
    claim = await journal.claim("run-1", worker_id="w1", org_id="org-1")
    assert claim is not None

    with caplog.at_level(logging.WARNING), pytest.raises(OwnershipLost) as lost:
        _ = await journal.commit(CommitRequest("c1", AttemptToken("run-1", "att-1", 1)))

    assert str(lost.value) == "CONVERSATION_DELETED"
    reasons = [r.harness.get("reason") for r in caplog.records if getattr(r, "harness_event", "") == "journal.ownership_lost"]  # type: ignore[attr-defined]
    assert reasons == ["CONVERSATION_DELETED"]
    assert len(node.requests) == 2, "the refused commit is not retried"
