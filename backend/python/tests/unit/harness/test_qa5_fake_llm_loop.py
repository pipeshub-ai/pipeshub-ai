"""QA5-L6: the harness lane's scripted model answers a chat-loop turn from the directives the spec typed.

The model is the real server (``deployment/e2e/fake_llm/server.py``) on aiohttp's test server; requests are what
the query service's loop and its title generation send (OpenAI chat completions).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from aiohttp.test_utils import TestClient, TestServer

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SERVER = Path(__file__).resolve().parents[5] / "deployment" / "e2e" / "fake_llm" / "server.py"
KEY = "sk-qa5-test"
TOOL = "mcp_e2esaas_create_ticket"
LOOP_TOOLS = [{"type": "function", "function": {"name": TOOL, "parameters": {"type": "object"}}},
              {"type": "function", "function": {"name": "retrieval_search", "parameters": {"type": "object"}}}]


def _load() -> Any:  # noqa: ANN401 - a module loaded from a path
    os.environ.setdefault("FAKE_LLM_API_KEY", KEY)
    os.environ.setdefault("FAKE_LLM_CONTROL_TOKEN", "control")
    spec = importlib.util.spec_from_file_location("qa5_fake_llm_server", SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = _load()


def _body(messages: list[dict[str, Any]], *, tools: bool = True) -> dict[str, Any]:
    return {"model": "e2e-llm", "messages": messages, **({"tools": LOOP_TOOLS} if tools else {})}


async def _ask(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/v1/chat/completions", json=body, headers={"Authorization": f"Bearer {os.environ['FAKE_LLM_API_KEY']}"})
    assert response.status == 200
    return (await response.json())["choices"][0]["message"]


@pytest.fixture
async def model_and_client() -> AsyncIterator[tuple[Any, TestClient]]:
    model = server.FakeModel()
    client = TestClient(TestServer(server.app(model)))
    await client.start_server()
    try:
        yield model, client
    finally:
        await client.close()


SCRIPT = "File it.\ntool create_ticket {\"title\": \"T\", \"body\": \"B\"}\nsay Ticket filed."


async def test_a_scripted_loop_turn_calls_the_offered_tool_then_says_its_text(model_and_client: tuple[Any, TestClient]) -> None:
    model, client = model_and_client
    first = await _ask(client, _body([{"role": "user", "content": SCRIPT}]))
    assert [c["function"]["name"] for c in first["tool_calls"]] == [TOOL]
    assert json.loads(first["tool_calls"][0]["function"]["arguments"]) == {"title": "T", "body": "B"}

    second = await _ask(client, _body([
        {"role": "user", "content": SCRIPT},
        {"role": "assistant", "content": None, "tool_calls": first["tool_calls"]},
        {"role": "tool", "tool_call_id": first["tool_calls"][0]["id"], "content": "created TCK-1"},
    ]))
    assert second["content"] == "Ticket filed."
    assert [r["kind"] for r in model.requests] == ["loop", "loop"]


async def test_an_unscripted_turn_and_title_generation_get_the_fixed_reply(model_and_client: tuple[Any, TestClient]) -> None:
    model, client = model_and_client
    plain = await _ask(client, _body([{"role": "user", "content": "What changed this quarter?"}]))
    title = await _ask(client, _body([{"role": "user", "content": SCRIPT}], tools=False))
    assert plain["content"] == title["content"] == "E2E stack model reply."
    assert [r["kind"] for r in model.requests] == ["other", "other"]


async def test_title_generation_never_spends_the_turns_hold(model_and_client: tuple[Any, TestClient]) -> None:
    model, client = model_and_client
    held = "Write a long report.\nhold\nsay Done."
    await _ask(client, _body([{"role": "user", "content": held}], tools=False))
    assert not model.holding.is_set()

    loop_call = asyncio.create_task(_ask(client, _body([{"role": "user", "content": held}])))
    await asyncio.wait_for(model.holding.wait(), timeout=10)
    assert not loop_call.done()
    released = await client.post("/__control/release", headers={"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]})
    assert released.status == 200
    assert (await asyncio.wait_for(loop_call, timeout=10))["content"] == "Done."


async def test_a_release_sent_while_nothing_was_held_does_not_carry_past_a_reset(model_and_client: tuple[Any, TestClient]) -> None:
    """UXR-3: a spec's teardown release (nothing held) let the next spec's first hold through after its reset."""
    model, client = model_and_client
    control = {"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]}
    assert (await client.post("/__control/release", headers=control)).status == 200
    assert (await client.post("/__control/reset", headers=control)).status == 200

    loop_call = asyncio.create_task(_ask(client, _body([{"role": "user", "content": "Report.\nhold\nsay Done."}])))
    await asyncio.wait_for(model.holding.wait(), timeout=10)
    # A stale release would have let the call through before this coroutine ran again.
    assert model.holding.is_set()
    assert not loop_call.done()
    await client.post("/__control/release", headers=control)
    assert (await asyncio.wait_for(loop_call, timeout=10))["content"] == "Done."


async def test_r50jfix8_a_release_with_nothing_held_never_lets_the_next_hold_through(
    model_and_client: tuple[Any, TestClient],
) -> None:
    """R50JFIX-8: a recovered run's killed attempt left no hold, the runner released anyway, and the new attempt's
    hold passed straight through. With no reset between, the next hold still holds."""
    model, client = model_and_client
    control = {"X-E2E-Control": os.environ["FAKE_LLM_CONTROL_TOKEN"]}
    stray = await client.post("/__control/release", headers=control)
    assert stray.status == 200 and (await stray.json())["released"] is False

    loop_call = asyncio.create_task(_ask(client, _body([{"role": "user", "content": "Report.\nhold\nsay Done."}])))
    await asyncio.wait_for(model.holding.wait(), timeout=10)
    await asyncio.sleep(0.05)
    assert model.holding.is_set() and not loop_call.done()
    released = await client.post("/__control/release", headers=control)
    assert (await released.json())["released"] is True
    assert (await asyncio.wait_for(loop_call, timeout=10))["content"] == "Done."


async def test_ctl6_a_coder_agents_loop_hands_the_script_to_code_task_then_says_its_text(
    model_and_client: tuple[Any, TestClient],
) -> None:
    """CTL-6: the nightly engine lane's turn: the loop hands the whole script to its engine, then answers."""
    _model, client = model_and_client
    script = "shell echo hi > out/a.csv\ntool create_ticket {\"title\": \"t\"}\nsay All done."
    tools = [*LOOP_TOOLS, {"type": "function", "function": {"name": "code_task", "parameters": {"type": "object"}}}]
    first = await _ask(client, {"model": "e2e-llm", "messages": [{"role": "user", "content": script}], "tools": tools})
    (call,) = first["tool_calls"]
    assert call["function"]["name"] == "code_task"
    assert json.loads(call["function"]["arguments"]) == {"goal": script}
    after = [
        {"role": "user", "content": script},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": "{\"output\": \"done\", \"success\": true}"},
    ]
    second = await _ask(client, {"model": "e2e-llm", "messages": after, "tools": tools})
    assert second["content"] == "All done." and not second.get("tool_calls")


BATCH = 'File three.\ntools create_ticket [{"title": "A"}, {"title": "B"}, {"title": "C"}]\nsay Three filed.'


async def test_qafix_a_tools_line_is_one_loop_step_of_three_calls(model_and_client: tuple[Any, TestClient]) -> None:
    model, client = model_and_client
    first = await _ask(client, _body([{"role": "user", "content": BATCH}]))
    assert [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in first["tool_calls"]] == [
        (TOOL, {"title": "A"}), (TOOL, {"title": "B"}), (TOOL, {"title": "C"}),
    ]
    second = await _ask(client, _body([
        {"role": "user", "content": BATCH},
        {"role": "assistant", "content": None, "tool_calls": first["tool_calls"]},
        *({"role": "tool", "tool_call_id": c["id"], "content": "created"} for c in first["tool_calls"]),
    ]))
    assert second["content"] == "Three filed."
    assert [r["kind"] for r in model.requests] == ["loop", "loop"]


def test_qafix_a_batch_counts_as_its_calls_when_the_loop_decides_whose_step_it_is() -> None:
    script = 'tools create_ticket [{"title": "A"}, {"title": "B"}]\ntool retrieval_search {"query": "q"}\nsay ok'
    batch = [server.ToolCall(f"c{n}", TOOL, "{}") for n in range(2)]
    after = [
        server.ChatMessage("user", script),
        server.ChatMessage("assistant", tool_calls=tuple(batch)),
        *(server.ChatMessage("tool", "created", tool_call_id=c.id) for c in batch),
    ]
    # The search is due next: a step that offers only another tool is not the script's.
    assert server._scripted(after, ["task_complete"]) is False
    assert server._scripted(after, ["retrieval_search"]) is True
    assert server._scripted(after[:1], [TOOL]) is True


# SMOKEFIX: a chat's tool list is fixed at its first request (R4). The smoke's script finds an MCP tool with
# search_tools and then names it; the tool never joins the list, so the scripted model calls it the way the product
# tells a model to (search_tools' how_to_call): through call_tool, by the exact name search_tools answered.
FIXED_TOOLS = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
               for name in ("search_tools", "call_tool", "knowledgegraph__search")]
FOUND = "mcp_e2esaas_lookup_note"
LAZY_SCRIPT = ('tool search_tools {"query": "lookup_note", "make_visible": true}\n'
               'tool lookup_note {"topic": "onboarding"}\nsay looked')
SEARCH_ANSWER = json.dumps({"matches": [{"name": FOUND, "description": "Look a note up.", "inputSchema": {}}],
                            "how_to_call": "Call any tool above with call_tool."})


async def test_smokefix_a_tool_search_tools_found_is_called_through_call_tool_on_a_fixed_list(
    model_and_client: tuple[Any, TestClient],
) -> None:
    model, client = model_and_client
    asked = [{"role": "user", "content": LAZY_SCRIPT}]
    first = await _ask(client, {"model": "e2e-llm", "messages": asked, "tools": FIXED_TOOLS})
    (search,) = first["tool_calls"]
    assert search["function"]["name"] == "search_tools"
    asked += [{"role": "assistant", "content": None, "tool_calls": [search]},
              {"role": "tool", "tool_call_id": search["id"], "content": SEARCH_ANSWER}]
    second = await _ask(client, {"model": "e2e-llm", "messages": asked, "tools": FIXED_TOOLS})
    (call,) = second["tool_calls"]
    assert call["function"]["name"] == "call_tool"
    assert json.loads(call["function"]["arguments"]) == {"name": FOUND, "arguments": {"topic": "onboarding"}}
    asked += [{"role": "assistant", "content": None, "tool_calls": [call]},
              {"role": "tool", "tool_call_id": call["id"], "content": "note: day one"}]
    third = await _ask(client, {"model": "e2e-llm", "messages": asked, "tools": FIXED_TOOLS})
    assert third["content"] == "looked"
    assert [r["kind"] for r in model.requests] == ["loop", "loop", "loop"]


def test_r27restfu2_a_tool_no_search_answered_is_called_through_call_tool_by_its_name() -> None:
    """R27REST-FU-2 (tools load lazily on demand): a call naming a granted server's tool is its first use, so a
    model calls a tool it has the name of through call_tool, searched for or not; the product builds it or refuses."""
    after = [server.ChatMessage("user", 'tool lookup_note {"topic": "x"}\nsay looked')]
    assert server._scripted(after, ["search_tools", "call_tool"]) is True
    assert server._scripted(after, ["task_complete"]) is False, "a step that offers no way to the tool is not the loop's"


# BGUI-8: a turn that hands out several sub-agents at once, each with its own script, kept out of the request's lines.
JOBS_TURN = "\n".join([
    "Close the quarter.",
    "usage 1000",
    'job {"goal": "File the ticket.", "script": ["tool create_ticket {\\"title\\": \\"T\\"}", "say Filed."]}',
    'job {"goal": "Read the note.", "script": ["say Read."]}',
    "answer I handed it to two agents.",
])
COORDINATOR = [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
               for name in ("create_plan", "critique_plan", "spawn_agent")]


async def test_bgui8_a_jobs_turn_plans_a_step_a_job_and_spawns_them_in_one_batch(model_and_client: tuple[Any, TestClient]) -> None:
    _model, client = model_and_client
    messages: list[dict[str, Any]] = [{"role": "user", "content": JOBS_TURN}]
    replies = []
    for _ in range(3):
        reply = await _ask(client, {"model": "e2e-llm", "messages": messages, "tools": COORDINATOR})
        replies.append(reply)
        messages += [{"role": "assistant", "content": None, "tool_calls": reply["tool_calls"]},
                     *({"role": "tool", "tool_call_id": c["id"], "content": "ok"} for c in reply["tool_calls"])]
    plan, critique, spawn = replies
    assert [s["id"] for s in json.loads(plan["tool_calls"][0]["function"]["arguments"])["steps"]] == ["job_1", "job_2"]
    assert critique["tool_calls"][0]["function"]["name"] == "critique_plan"
    goals = [json.loads(c["function"]["arguments"]) for c in spawn["tool_calls"]]
    assert [(g["task_id"], g["goal"]) for g in goals] == [("job_1", "File the ticket."), ("job_2", "Read the note.")]
    answer = await _ask(client, {"model": "e2e-llm", "messages": messages, "tools": COORDINATOR})
    assert answer["content"] == "I handed it to two agents." and not answer.get("tool_calls")


def test_bgui8_a_job_line_is_no_directive_so_a_sibling_handed_the_request_runs_only_its_own() -> None:
    assert [d.verb for d in server.parse_directives(JOBS_TURN)] == ["usage"]


def test_bgui8_a_sub_agent_handed_one_job_runs_that_jobs_script_and_hands_out_nothing() -> None:
    child = f"## The user's request (verbatim)\n\n{JOBS_TURN}\n\n## Your delegated task\n\nRead the note."
    assert server._jobs(child) == []
    (expanded,) = server._with_job_scripts([server.ChatMessage("user", child)])
    assert [(d.verb, d.arg) for d in server.parse_directives(expanded.content)] == [("usage", "1000"), ("say", "Read.")]


@pytest.mark.parametrize(("declared", "named"), [("filePath", "filePath"), ("file_path", "file_path"), ("path", "path")])
async def test_r31jfix_an_engines_write_is_called_with_the_argument_its_schema_declares(
    model_and_client: tuple[Any, TestClient], declared: str, named: str,
) -> None:
    """R31-JFIX: the stack's scripted model named every engine's file ``filePath``, so DeepSeek Harness's and Goose's
    ``write`` refused the call and a job's ``cat`` of it found nothing."""
    _, client = model_and_client
    tools = [
        {"type": "function", "function": {"name": "bash", "parameters": {"type": "object", "properties": {"command": {}}}}},
        {"type": "function", "function": {"name": "write", "parameters": {
            "type": "object", "properties": {declared: {"type": "string"}, "content": {"type": "string"}}}}},
    ]
    body = {"model": "e2e-llm", "messages": [{"role": "user", "content": "write a.txt hi\nsay done"}], "tools": tools}
    message = await _ask(client, body)
    [call] = message["tool_calls"]
    assert call["function"]["name"] == "write" and named in json.loads(call["function"]["arguments"])
