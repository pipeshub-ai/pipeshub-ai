"""DUR2-T3: DUR1-F7 reproduced through the replay rig.

``quick-04`` on today's loop: the model calls ``search_tools``, then streams a
``jira__create_issue`` call whose description runs on for thousands of argument
chunks (a runaway toward the output limit). The transport used to fold the
chunks pairwise, re-parsing the arguments on every addition, so the loop sat at
100% CPU after the ``search_tools`` result and wrote nothing. Both drivers go
through the product's ``LangChainTransport`` here, as on the live lane.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessageChunk
from langchain_core.messages import ai as ai_messages

from app.agents.agent_loop.langchain_transport import LangChainTransport
from tests.harness_replay import durable_driver, legacy_driver
from tests.support.parity_cases.harness_cases import load_cases

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

RUNAWAY_CHUNKS = 15_000
# Pairwise, every addition re-parsed the arguments so far: ≈ RUNAWAY_CHUNKS² / 2 fragments of text (≈ 20 s of CPU).
# Assembled at once, the whole call is parsed a few times; this bound is in characters, so it holds on any host.
PARSED_PER_ARGUMENT_CHAR = 10


def _call(name: str, call_id: str, args: list[str]) -> list[AIMessageChunk]:
    head = AIMessageChunk(content="", tool_call_chunks=[{"name": name, "args": "", "id": call_id, "index": 0}])
    return [head] + [
        AIMessageChunk(content="", tool_call_chunks=[{"name": None, "args": a, "id": None, "index": 0}]) for a in args
    ]


def _fragments(chunk: AIMessageChunk) -> list[str]:
    return [c["args"] or "" for c in chunk.tool_call_chunks]


def _turns() -> list[list[AIMessageChunk]]:
    runaway = [
        '{"project": "PROJ", "summary": "Login returns 500 on SSO", "description": "',
        *(["P1 "] * RUNAWAY_CHUNKS),
        '"}',
    ]
    return [
        _call("search_tools", "c1", ['{"query": "create jira issue"}']),
        _call("jira__create_issue", "c2", runaway),
        [AIMessageChunk(content="Created PROJ-1: Login returns 500 on SSO.")],
    ]


class _ScriptedChatModel:
    """A LangChain chat model streaming one scripted chunk list per call."""

    def __init__(self, turns: list[list[AIMessageChunk]]) -> None:
        self._turns = turns
        self.calls = 0

    def bind_tools(self, tools: list[Any], **_: Any) -> _ScriptedChatModel:  # noqa: ANN401
        return self

    async def astream(self, messages: list[Any], config: Any = None) -> AsyncIterator[AIMessageChunk]:  # noqa: ANN401
        turn = self._turns[min(self.calls, len(self._turns) - 1)]
        self.calls += 1
        for chunk in turn:
            yield chunk


@pytest.mark.parametrize("driver", [legacy_driver.drive, durable_driver.drive], ids=["legacy", "durable"])
async def test_dur2_t3_quick_04_with_a_runaway_tool_call_after_search_tools_finishes(
    driver: Any, monkeypatch: pytest.MonkeyPatch  # noqa: ANN401
) -> None:
    # The runaway repeats one fragment, which P4.17's repetition cut stops after ~4 KB; this measures assembling the
    # whole 15,000-chunk call, the cost a call below the cut's threshold still has.
    monkeypatch.setenv("HARNESS_MODEL_OUTPUT_LIMIT", "0")
    case = next(c for c in load_cases() if c.id.startswith("quick-04"))
    spec = case.drive_spec()
    spec.script = None
    model = _ScriptedChatModel(_turns())
    spec.transport = LangChainTransport(model, model_name="scripted-langchain")
    parsed: list[int] = []
    parse = ai_messages.parse_partial_json

    def counted(text: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        parsed.append(len(text))
        return parse(text, *args, **kwargs)

    monkeypatch.setattr(ai_messages, "parse_partial_json", counted)
    result = await driver(spec)
    arguments = sum(len(fragment) for turn in _turns() for chunk in turn for fragment in _fragments(chunk))
    assert sum(parsed) <= PARSED_PER_ARGUMENT_CHAR * arguments
    assert model.calls == 3
    assert result.recording.final.status == "completed"
    assert [s.name for s in result.recording.tool_steps()] == ["search_tools", "jira__create_issue"]
