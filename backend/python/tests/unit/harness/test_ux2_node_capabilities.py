"""UX2 VS-3, VS-5: the worker reads what the Node of its claim accepts (``nodeCapabilities``); an older Node sends
none and the worker writes only what every v2 Node accepts. An older worker on a newer Node ignores the field."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.harness.journal_node.client import node_accepts
from harness_core.journal.types import APPROVAL_REASONS, CHILD_LABELS
from harness_core.logs import HarnessLog
from tests.unit.harness.test_node_journal_client import _claimed, _Node

LOG = HarnessLog.named("tests.ux2")


async def _claim(body: dict[str, object]):  # noqa: ANN202
    node = _Node()
    node.replies.append(httpx.Response(200, json=body))
    claim = await node.journal().claim(worker_id="w1")
    assert claim is not None
    return claim


async def test_vs3_a_claim_carries_what_its_node_accepts() -> None:
    body = {**_claimed(), "nodeCapabilities": {"harnessMajor": 2, "features": [APPROVAL_REASONS, CHILD_LABELS, "later_thing"]}}
    claim = await _claim(body)
    assert claim.journal_accepts(APPROVAL_REASONS) and claim.journal_accepts(CHILD_LABELS)
    assert claim.accepts == frozenset({APPROVAL_REASONS, CHILD_LABELS, "later_thing"})


async def test_vs3_an_older_node_sends_nothing_and_accepts_nothing_more() -> None:
    claim = await _claim(_claimed())
    assert claim.accepts == frozenset()
    assert not claim.journal_accepts(APPROVAL_REASONS)
    assert not claim.journal_accepts(CHILD_LABELS)


@pytest.mark.parametrize(
    "advertised",
    [{"harnessMajor": 3, "features": []}, {"features": ["approval_reasons"]}, {"harnessMajor": 2, "features": ["Not A Name"]}, "yes"],
)
def test_vs3_an_unreadable_advertisement_is_none_and_logged(advertised: object, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert node_accepts(advertised, LOG) == frozenset()
    assert "journal.node_capabilities_unreadable" in caplog.text


async def test_vs5_nodes_first_todays_claim_parser_ignores_fields_it_does_not_know() -> None:
    """An older worker's parser reads the response by key: a newer Node's extra fields cannot break it. Pinned here
    on today's ``_adopt`` with a field no build knows."""
    body = {**_claimed(), "nodeCapabilities": {"harnessMajor": 2, "features": [APPROVAL_REASONS]}, "somethingNewer": {"x": 1}}
    claim = await _claim(body)
    assert claim.run_id == "run-1"
