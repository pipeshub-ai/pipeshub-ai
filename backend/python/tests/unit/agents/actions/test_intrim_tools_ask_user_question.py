"""Tests for ask_user_question tool input coercion and structured output (InternalTools)."""

import json

import pytest
from pydantic import ValidationError

from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agents.actions.internal_tools.intrim_tools import (
    AskUserQuestionInput,
    AskUserQuestionItemInput,
    AskUserQuestionOptionInput,
    InternalTools,
    _ask_user_question_outcome,
)


def test_pipe_shorthand_coerces_to_structured_item() -> None:
    raw = {
        "user_intent": "User wants to pick a focus area for research.",
        "questions": [
            "Choose focus|Product docs|API reference|Tutorials|Market trends",
        ],
    }
    model = AskUserQuestionInput.model_validate(raw)
    assert model.user_intent == "User wants to pick a focus area for research."
    assert len(model.questions) == 1
    assert model.questions[0].question == "Choose focus"
    assert len(model.questions[0].options) == 4
    labels = [o.label for o in model.questions[0].options]
    assert labels == ["Product docs", "API reference", "Tutorials", "Market trends"]


def test_questions_field_json_string() -> None:
    inner = [
        {
            "question": "Pick one",
            "options": [
                {"label": "A", "description": "", "isUserInput": False},
                {"label": "B", "description": "", "isUserInput": False},
                {"label": "C", "description": "", "isUserInput": False},
            ],
        },
    ]
    raw = {"user_intent": "Clarifying preference.", "questions": json.dumps(inner)}
    model = AskUserQuestionInput.model_validate(raw)
    assert model.user_intent == "Clarifying preference."
    assert model.questions[0].question == "Pick one"
    assert [o.label for o in model.questions[0].options] == ["A", "B", "C"]


def test_item_json_object_string() -> None:
    payload = {
        "question": "X?",
        "options": ["a", "b", "c"],
    }
    item = AskUserQuestionItemInput.model_validate(json.dumps(payload))
    assert item.question == "X?"
    assert [o.label for o in item.options] == ["a", "b", "c"]


def test_insufficient_pipe_options_still_raises() -> None:
    raw = {"user_intent": "test", "questions": ["Only two opts|A|B"]}
    with pytest.raises(ValidationError):
        AskUserQuestionInput.model_validate(raw)


def test_plain_string_question_without_pipe_raises() -> None:
    raw = {"user_intent": "test", "questions": ["What should I search tutorials Market trends"]}
    with pytest.raises(ValidationError):
        AskUserQuestionInput.model_validate(raw)


def test_missing_user_intent_raises() -> None:
    raw = {
        "questions": [
            "Choose focus|Product docs|API reference|Tutorials|Market trends",
        ],
    }
    with pytest.raises(ValidationError):
        AskUserQuestionInput.model_validate(raw)


async def test_ask_user_question_returns_structured_json_for_ui() -> None:
    """Tool output must match the shape persisted by Node and consumed by the frontend."""
    tools = InternalTools()
    result_json = await tools.ask_user_question(
        user_intent="User wants to pick a Slack channel for the message.",
        questions=[
            AskUserQuestionItemInput(
                question="Which channel?",
                options=[
                    AskUserQuestionOptionInput(label="#general", isUserInput=False),
                    AskUserQuestionOptionInput(label="#random", isUserInput=False),
                    AskUserQuestionOptionInput(label="Enter channel name", isUserInput=True),
                ],
                multiSelect=False,
            ),
        ],
    )

    payload = json.loads(result_json)
    assert payload["name"] == "ask_user_question"
    assert payload["userIntent"] == "User wants to pick a Slack channel for the message."
    assert len(payload["questions"]) == 1

    question = payload["questions"][0]
    assert question["question"] == "Which channel?"
    assert question["multiSelect"] is False
    assert question.get("uuid")

    options = question["options"]
    assert len(options) == 3
    assert options[0]["label"] == "#general"
    assert options[0]["isUserInput"] is False
    assert options[0]["id"] == "opt_#general"
    assert options[2]["isUserInput"] is True


class TestAskUserQuestionOutcomeExtractor:
    """`_ask_user_question_outcome` (task engine plan Part D, Phase 5) —
    a headless caller (`TaskExecutor`) must be able to tell this terminal
    tool's result apart from an ordinary successful completion, or a
    pending question is silently dropped and the run is marked SUCCEEDED
    instead of AWAITING_INPUT."""

    def test_sets_needs_input_from_user_intent(self) -> None:
        payload = json.dumps({
            "name": "ask_user_question",
            "userIntent": "Which Slack channel should I post to?",
            "questions": [],
        })
        tr = CoreToolResult(tool_call_id="c1", name="ask_user_question", content=payload)
        call = ToolCall(id="c1", name="ask_user_question", arguments={})

        outcome = _ask_user_question_outcome(tr, call, fallback_text="")

        assert outcome.task_done is True
        assert outcome.needs_input == "Which Slack channel should I post to?"
        assert outcome.final_output == payload

    def test_falls_back_to_generic_needs_input_on_malformed_content(self) -> None:
        tr = CoreToolResult(tool_call_id="c1", name="ask_user_question", content="not json")
        call = ToolCall(id="c1", name="ask_user_question", arguments={})

        outcome = _ask_user_question_outcome(tr, call, fallback_text="")

        assert outcome.task_done is True
        assert outcome.needs_input
