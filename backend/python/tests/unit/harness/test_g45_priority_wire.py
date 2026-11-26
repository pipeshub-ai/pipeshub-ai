"""G-45 / GA-24 on the wire: the claim names the run's class beside the run (the worker's run model is strict), a
preempting claim-next sends ``aboveClass``, and the model grant names the class the gateway serves calls by. An
older Node names no class: the claim reads ``None`` and the worker behaves as before classes."""

from __future__ import annotations

import json

import httpx

from app.harness.access.grants import parse_grant
from tests.unit.harness.test_node_journal_client import _claimed, _Node


async def test_ga24_the_claim_names_the_runs_class_and_an_older_node_names_none() -> None:
    node = _Node()
    node.replies.append(httpx.Response(200, json={**_claimed(), "priorityClass": "background"}))
    node.replies.append(httpx.Response(200, json=_claimed()))
    node.replies.append(httpx.Response(200, json={**_claimed(), "priorityClass": "a_later_class"}))
    journal = node.journal()
    classes = []
    for _ in range(3):
        claim = await journal.claim(worker_id="w1")
        assert claim is not None
        classes.append(claim.priority_class)
    assert classes == ["background", None, None]


async def test_ga24_a_preempting_claim_next_asks_above_a_class_and_a_plain_one_does_not() -> None:
    node = _Node()
    node.replies.append(httpx.Response(204))
    node.replies.append(httpx.Response(204))
    journal = node.journal()
    assert await journal.claim(worker_id="w1", above="background") is None
    assert await journal.claim(worker_id="w1") is None
    sent = [json.loads(request.content) for request in node.requests]
    assert sent[0]["aboveClass"] == "background"
    assert "aboveClass" not in sent[1]


def test_ga24_the_model_grant_names_the_class_the_gateway_serves_calls_by() -> None:
    body = {"runId": "r", "routes": [{"provider": "p", "model": "m"}], "budgets": {}}
    assert parse_grant({**body, "priorityClass": "batch"}, tenant="org-1").priority_class == "batch"
    assert parse_grant(body, tenant="org-1").priority_class is None
