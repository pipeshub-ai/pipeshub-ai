"""End-to-end test that a follow-up question survives context compaction,
through `POST /chat/stream`.

In a chat the history is seeded oldest-first, so the first user message the
model sees is the conversation's FIRST question. The context shapers used to
protect that message, so when the current turn's tool results filled the
window they summarised or evicted the CURRENT question, and the model answered
the old one (docs/context-engineering.md, CE-01, cases E2E-01/03/05).

Everything below the HTTP route runs for real, as in
`test_agent_turn_limit_e2e.py`: `run_chat_stream()`, `PipesHubAgentFactory`
with its PRE_MODEL shaping pipeline, the agent loop and `LangChainTransport`.
The chat model is scripted and records every request; the web fetch service
returns large pages; the retrieval/graph/config services are mocks.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import Field

from app.agents.chat_modes.attachments import ResolvedAttachments
from app.api.routes.chatbot import askAIStream
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.utils.html_to_blocks import TextBlock
from app.web.chain import ChainOutcome
from app.web.models import WebPage
from tests.integration.test_agent_turn_limit_e2e import (
    _request,
    _route_kwargs,
    _ScriptedChatModel,
)
from tests.integration.test_chat_stream_agent_loop_e2e import _drain, _events_by_name

if TYPE_CHECKING:
    from collections.abc import Iterator

_A = "How do I set up automated appointment reminders by SMS and email, and can patients confirm or cancel?"
_A_ANSWER = "Enable them under Practice Settings > Reminders; patients reply C to confirm or X to cancel."
_B = "I have access to more than one practice. How do I pick which practice to log into and set a default?"
_ANSWER = "Pick the practice in the picker after login, and set Profile > Preferences > Default practice."
_FETCHES = 12
_PAGE_CHARS = 60_000
_WINDOW = 32_000
# Smaller than the system prompt and tool schemas alone, so no request fits.
_TINY_WINDOW = 4_000
_SUMMARY_REQUEST = "Summarize the conversation history below"
# Loop-written summaries reach the provider as user turns, wrapped as context notes (CE-05, P1).
_CONTEXT_NOTE = '<context_note source="agent">\n'
_LOOP_NOTE_PREFIXES = ("[Loop compaction", "[Auto-compacted summary")
# What artifact compaction sends in place of a fetched page.
_ARTIFACT_REFERENCE = "[artifact:"
_IMAGE_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_IMAGE_VRID = "vr-screenshot"
_INVARIANT_REPAIRED = "context_invariant_repaired"


class _RecordingChatModel(_ScriptedChatModel):
    """The scripted model, plus answers to the auto-compact summariser.

    Summary requests go to the same model mid-shaping; answering them from the
    script would shift every later scripted turn, so they are recorded apart.
    """

    summary_calls: list[list[BaseMessage]] = Field(default_factory=list)

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        if _text(messages[-1]).startswith(_SUMMARY_REQUEST):
            self.summary_calls.append(list(messages))
            return AIMessage(content="Earlier tool results were read; nothing in them answers the question yet.")
        return super()._next(messages)


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part)
        for part in content
        if not isinstance(part, dict) or part.get("type") == "text"
    )


def _images(message: BaseMessage) -> list[Any]:
    content = message.content
    if isinstance(content, str):
        return []
    return [part for part in content if isinstance(part, dict) and part.get("type") in ("image_url", "image")]


def _page(url: str) -> ChainOutcome[WebPage]:
    paragraph = (
        f"Manual page {url}. Calendar, invoices, lab orders and audit trail reference text "
        "for administrators of every practice in the account. "
    )
    body = (paragraph * (_PAGE_CHARS // len(paragraph) + 1))[:_PAGE_CHARS]
    blocks = tuple(TextBlock(content=body[i:i + 2_000]) for i in range(0, _PAGE_CHARS, 2_000))
    return ChainOutcome(WebPage(url=url, title=f"Manual {url}", blocks=blocks, fetched_by="scripted"), "scripted", ())


async def _fetch(self: object, url: str, caller: object) -> ChainOutcome[WebPage]:
    return _page(url)


def _fetch_script() -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[{
            "name": "dynamic__fetch_url",
            "args": {"url": f"https://manuals.example.com/admin/{i}"},
            "id": f"fetch-{i}",
        }])
        for i in range(_FETCHES)
    ] + [AIMessage(content=_ANSWER)]


def _previous_conversations() -> list[dict[str, Any]]:
    return [
        {"role": "user_query", "content": _A},
        {
            "role": "bot_response",
            "content": _A_ANSWER,
            "tool_results": [
                {
                    "tool_name": "dynamic__fetch_url", "tool_id": f"history-{i}", "status": "success",
                    "args": {"url": f"https://manuals.example.com/reminders/{i}"},
                    "result": f"Reminder settings, page {i}. " * 120,
                }
                for i in range(2)
            ],
        },
    ]


@contextmanager
def _stack(model: _RecordingChatModel, *, window: int, multimodal: bool = False) -> Iterator[None]:
    with (
        patch("app.utils.execute_query.has_sql_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.utils.fetch_slack_thread.has_slack_connector_configured", new=AsyncMock(return_value=False)),
        patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=(
            model, {"provider": "openai", "isMultimodal": multimodal, "contextLength": window}, {},
        ))),
        patch("app.web.services.WebFetchService.fetch", new=_fetch),
    ):
        yield


@contextmanager
def _image_attachment() -> Iterator[None]:
    """The current turn's attachment, as blob storage resolves an uploaded PNG."""
    with (
        patch(
            "app.agents.chat_modes.bridge.resolve_attachments",
            new=AsyncMock(return_value=ResolvedAttachments(has_image_attachments=True)),
        ),
        patch(
            "app.utils.attachment_utils.resolve_attachments",
            new=AsyncMock(return_value=[{"type": "image_url", "image_url": {"url": _IMAGE_URL}}]),
        ),
    ):
        yield


async def _stream(body: dict[str, Any], **route_kwargs: object) -> dict[str, list[dict]]:
    response = await askAIStream(request=_request(body), **{**_route_kwargs(), **route_kwargs})
    return _events_by_name(await _drain(response))


def _graph_granting_the_attachment() -> AsyncMock:
    """The uploader may read the attachment, as its upload's OWNER edge grants."""
    graph = AsyncMock()
    graph.get_nodes_by_filters.return_value = []
    graph.get_user_by_user_id.return_value = {"_key": "user-key-1"}
    graph.check_access.return_value = AccessCheck(records_by_vrid={_IMAGE_VRID: "rec-screenshot"})
    return graph


def _body(query: str = _B, **extra: object) -> dict[str, Any]:
    return {"query": query, "chatMode": "agent", "previousConversations": _previous_conversations(), **extra}


def _humans(call: list[BaseMessage]) -> list[BaseMessage]:
    return [m for m in call if isinstance(m, HumanMessage)]


def _is_loop_note(text: str) -> bool:
    return text.startswith(_CONTEXT_NOTE) and text.removeprefix(_CONTEXT_NOTE).startswith(_LOOP_NOTE_PREFIXES)


def _person_turns(call: list[BaseMessage]) -> list[str]:
    """What the person wrote, in order: human turns that are not runtime context notes."""
    return [t for t in (_text(m) for m in _humans(call)) if not t.startswith(_CONTEXT_NOTE)]


def _assert_sends_current_request(n: int, call: list[BaseMessage]) -> BaseMessage:
    """B reaches the call verbatim and nothing the person wrote comes after it."""
    humans = _humans(call)
    texts = [_text(m) for m in humans]
    assert texts.count(_B) == 1, f"call #{n + 1}: the current question is not sent verbatim once: {texts}"
    after = texts[texts.index(_B) + 1:]
    assert all(_is_loop_note(t) for t in after), (
        f"call #{n + 1}: a human turn other than a loop note follows the current question: {after}"
    )
    assert texts[-1] != _A, f"call #{n + 1}: the first question is the last human message"
    return humans[texts.index(_B)]


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> _RecordingChatModel:
    monkeypatch.delenv("SANDBOX_MODE", raising=False)
    return _RecordingChatModel()


@pytest.fixture
def errors(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.ERROR)
    return caplog


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if text in r.getMessage()]


def _assert_failed_on_context(events: dict[str, list[dict]], caplog: pytest.LogCaptureFixture) -> None:
    assert "RUN_ERROR" not in events, events.get("RUN_ERROR")
    [finished] = events["RUN_FINISHED"]
    result = finished["result"]
    assert result["answerMatchType"] == "Error", result
    assert result["errorCode"] == "request_too_large", result
    assert result["answer"].startswith("This conversation is too long for the selected model"), result
    failures = _logged(caplog, "Context could not be shaped")
    assert failures, f"the run did not fail on the context: {[r.getMessage() for r in caplog.records]}"
    assert "exceeds budget" in failures[0]


class TestFollowUpQuestionUnderCompaction:
    """E2E-01: B, after A, while B's own fetches overflow a 32K window."""

    async def test_every_model_call_carries_the_follow_up_question(
        self, model: _RecordingChatModel, errors: pytest.LogCaptureFixture,
    ) -> None:
        model.responses = _fetch_script()

        with _stack(model, window=_WINDOW):
            events = await _stream(_body())

        assert "RUN_ERROR" not in events, events.get("RUN_ERROR")
        [finished] = events["RUN_FINISHED"]
        assert finished["result"]["answer"] == _ANSWER
        assert len(events["TOOL_CALL_RESULT"]) == _FETCHES
        assert len(model.calls) == _FETCHES + 1
        # Without compaction this test proves nothing. Loop summaries come in
        # batches (context/watermark.py), so compacted fetches count too, and
        # protected fetches are never summarised, only evicted whole: a call
        # that carries fewer results than were fetched before it was shaped.
        assert model.summary_calls or any(
            _is_loop_note(_text(m)) or _text(m).startswith(_ARTIFACT_REFERENCE)
            for call in model.calls for m in call
        ) or any(
            sum(isinstance(m, ToolMessage) for m in call) < n for n, call in enumerate(model.calls)
        ), "nothing was compacted; raise the fetched page size or lower the window"
        for n, call in enumerate(model.calls):
            _assert_sends_current_request(n, call)
        assert not _logged(errors, _INVARIANT_REPAIRED), _logged(errors, _INVARIANT_REPAIRED)

    async def test_the_first_question_stays_before_the_follow_up(self, model: _RecordingChatModel) -> None:
        model.responses = _fetch_script()

        with _stack(model, window=_WINDOW):
            await _stream(_body())

        for n, call in enumerate(model.calls):
            texts = [_text(m) for m in _humans(call)]
            if _A in texts and _B in texts:
                assert texts.index(_A) < texts.index(_B), f"call #{n + 1}: {texts}"

    async def test_the_follow_up_question_is_the_last_user_turn_on_the_wire(
        self, model: _RecordingChatModel,
    ) -> None:
        model.responses = _fetch_script()

        with _stack(model, window=_WINDOW):
            await _stream(_body())

        for n, call in enumerate(model.calls):
            assert _person_turns(call)[-1] == _B, f"call #{n + 1}: {[_text(m)[:60] for m in _humans(call)]}"


class TestImageOnTheFollowUpQuestion:
    """E2E-03: an image attached to B goes on B, never on A."""

    async def test_the_image_rides_on_the_current_question(
        self, model: _RecordingChatModel, errors: pytest.LogCaptureFixture,
    ) -> None:
        model.responses = _fetch_script()

        with _stack(model, window=_WINDOW, multimodal=True), _image_attachment():
            events = await _stream(
                _body(attachments=[{"virtualRecordId": _IMAGE_VRID, "mimeType": "image/png", "fileName": "picker.png"}]),
                graph_provider=_graph_granting_the_attachment(),
            )

        assert "RUN_ERROR" not in events, events.get("RUN_ERROR")
        assert events["RUN_FINISHED"][0]["result"]["answer"] == _ANSWER
        assert len(model.calls) == _FETCHES + 1
        for n, call in enumerate(model.calls):
            current = _assert_sends_current_request(n, call)
            assert len(_images(current)) == 1, f"call #{n + 1}: the question carries {_images(current)}"
            others = [_text(m)[:60] for m in call if m is not current and _images(m)]
            assert not others, f"call #{n + 1}: the image is also on {others}"
        assert not _logged(errors, _INVARIANT_REPAIRED), _logged(errors, _INVARIANT_REPAIRED)


class TestContextThatCannotFit:
    """E2E-05: a request that cannot fit the window fails the run cleanly
    instead of being sent to the provider over budget."""

    async def test_a_window_the_prompt_alone_fills_is_never_sent(
        self, model: _RecordingChatModel, errors: pytest.LogCaptureFixture,
    ) -> None:
        model.responses = _fetch_script()

        with _stack(model, window=_TINY_WINDOW):
            events = await _stream(_body())

        assert model.calls == [], f"the model was called {len(model.calls)} time(s) with an over-budget request"
        _assert_failed_on_context(events, errors)

    async def test_a_current_question_larger_than_the_window_is_never_sent(
        self, model: _RecordingChatModel, errors: pytest.LogCaptureFixture,
    ) -> None:
        # The current question is protected from every shaper, so a pasted log
        # larger than the window cannot be shrunk to fit.
        pasted = f"{_B}\n\nHere is the login log:\n" + "2026-10-23 login ok practice=north-clinic\n" * 4_000
        model.responses = _fetch_script()

        with _stack(model, window=_WINDOW):
            events = await _stream(_body(pasted))

        assert model.calls == [], f"the model was called {len(model.calls)} time(s) with an over-budget request"
        _assert_failed_on_context(events, errors)
