"""R31CAPFIX-FU-1: the journeys' ``knowledge`` line reads the seeded page's text where it is whole.

Root cause (R32-ENGFU's validation): the line looked for "14 days ahead" in the person's stream, whose
``TOOL_CALL_RESULT`` carries a tool result's first 2000 characters only. The search lists its records by virtual
record id, not relevance (``relevance_ordered_context`` is off by default), so the travel-policy page sat past the cut
whenever its random id sorted late: red on some fresh stacks, green on others. The line now asks the scripted model
whether the page's text reached it in a request of the turn (``/__control/reached``).

* KP-1: a search result whose travel-policy record sorts last: its stream preview lacks the text, so the old check
  (the preview alone) is red; the model's record has it, so the new one is green.
* KP-2: the model marks a phrase reached only for a request that holds the turn's marker too; a stack whose model
  keeps no record falls back to the stream, and says so.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

from harness_core.kernel.agent.tool_loop import _result_preview  # pyright: ignore[reportPrivateUsage]
from tests.unit.harness.test_qa5_fake_llm_loop import LOOP_TOOLS, server

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

E2E = Path(__file__).resolve().parents[5] / "deployment" / "e2e"
sys.path.insert(0, str(E2E))
import journeys  # noqa: E402

CONTROL = {"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]}
MARKER = "done-kp1"


def _listing() -> str:
    """The search's answer as the model reads it: records in virtual record id order, the travel policy last."""
    records = [{"virtualRecordId": f"{n:032x}", "recordName": f"runbook-{n}.md",
                "content": f"On-call runbook {n}: page the secondary after 15 minutes. " * 6} for n in range(8)]
    records.append({"virtualRecordId": "f" * 32, "recordName": "travel-policy.md",
                    "content": "Book flights at least 14 days ahead. Economy class for trips under 6 hours."})
    return json.dumps({"records": records})


@pytest.fixture
async def client() -> AsyncIterator[TestClient]:
    test_client = TestClient(TestServer(server.app(server.FakeModel())))
    await test_client.start_server()
    try:
        yield test_client
    finally:
        await test_client.close()


async def _turn(client: TestClient, marker: str, result: str) -> None:
    body = {"model": "e2e-llm", "tools": LOOP_TOOLS, "messages": [
        {"role": "user", "content": f'tool retrieval_search {{"query": "travel policy"}}\nsay {marker}'},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "retrieval_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": result},
    ]}
    answered = await client.post("/v1/chat/completions", json=body,
                                 headers={"Authorization": f"Bearer {os.environ['FAKE_LLM_API_KEY']}"})
    assert answered.status == 200


async def _reached(client: TestClient, marker: str) -> list[str]:
    answered = await client.get(f"/__control/reached?marker={marker}", headers=CONTROL)
    return list((await answered.json())["reached"])


async def test_kp_1_a_page_sorted_past_the_preview_cut_is_green_from_the_models_record(client: TestClient) -> None:
    listing = _listing()
    preview = _result_preview(listing)
    stream = json.dumps([{"toolCallId": "c1", "content": preview}])
    # The old line: the stream's preview alone. Red whenever the page's id sorts late.
    assert journeys.KB_PHRASE in listing and journeys.KB_PHRASE not in stream

    watched = await client.post("/__control/reached", json={"marker": MARKER, "phrase": journeys.KB_PHRASE},
                                headers=CONTROL)
    assert watched.status == 200
    await _turn(client, MARKER, listing)
    assert journeys.knowledge_found(stream, await _reached(client, MARKER)) == (True, "in the model's request")


async def test_kp_2_only_the_turns_own_requests_count_and_an_older_model_falls_back(client: TestClient) -> None:
    for marker in (MARKER, "done-other"):
        _ = await client.post("/__control/reached", json={"marker": marker, "phrase": journeys.KB_PHRASE}, headers=CONTROL)
    await _turn(client, "done-other", _listing())
    await _turn(client, MARKER, json.dumps({"records": []}))
    assert await _reached(client, MARKER) == []
    assert await _reached(client, "done-other") == [journeys.KB_PHRASE]
    assert journeys.knowledge_found("[]", []) == (False, "not in the model's request")
    assert journeys.knowledge_found(f'["{journeys.KB_PHRASE}"]', None) == (True, "in the stream")
    assert journeys.knowledge_found("[]", None) == (False, "the model keeps no record; not in the stream")
    refused = await client.post("/__control/reached", json={"marker": MARKER}, headers=CONTROL)
    assert refused.status == 400


def test_kp_3_the_journey_watches_its_own_turn() -> None:
    sent: list[tuple[str, str, Any]] = []

    class _Model:
        def request(self, method: str, path: str, body: Any = None) -> Any:  # noqa: ANN401
            sent.append((method, path, body))
            return type("Answer", (), {"status": 200, "body": {"reached": [journeys.KB_PHRASE]}})()

    ctx = type("Ctx", (), {"stack": type("Stack", (), {"model": _Model()})()})()
    journeys.watch_reached(ctx, MARKER)  # type: ignore[arg-type]
    assert journeys.reached_phrases(ctx, MARKER) == [journeys.KB_PHRASE]  # type: ignore[arg-type]
    assert sent == [("POST", "/__control/reached", {"marker": MARKER, "phrase": journeys.KB_PHRASE}),
                    ("GET", f"/__control/reached?marker={MARKER}", None)]


def test_kp_4_the_repository_probes_read_the_answers_could_not_do_kinds() -> None:
    """R32REPO-FU-1's journey check: the parent's answer names what its job could not do, by kind."""
    events = [
        {"type": "x.pipeshub.answer.finalized", "runId": "run-1",
         "data": {"limits": {"items": [{"kind": "repository.declined", "repository": "github.com/pxh-e2e/j"}], "line": "x"}}},
        {"type": "x.pipeshub.answer.finalized", "runId": "run-child", "data": {"limits": {"items": [{"kind": "repository.failed"}]}}},
    ]
    assert journeys.answer_limits(events, "run-1") == ["repository.declined"]
    assert journeys.answer_limits(events, "run-2") == []


def test_kp_5_the_journeys_know_which_engines_lose_output_as_the_profiles_say() -> None:
    from harness_engines.acp.registry import packaged_acp_agents

    marked = {f"acp-{a.id}" for a in packaged_acp_agents(unsigned_ok=True) if a.output_may_be_lost}
    assert set(journeys.LOSES_OUTPUT) == marked
