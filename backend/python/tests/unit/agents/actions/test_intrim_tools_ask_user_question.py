"""Tests for ask_user_question tool input coercion and structured output (InternalTools)."""

import json

import pytest
from pydantic import ValidationError

from app.agents.actions.internal_tools.intrim_tools import (
    AskUserQuestionInput,
    AskUserQuestionItemInput,
    AskUserQuestionOptionInput,
    InternalTools,
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


def _ask_user_question_tool():
    """Builds the actual `BoundMethodTool` `ToolsetBuilder` produces from
    the `@tool`-decorated method — the ONLY object whose `to_schema()`
    reflects what the LLM sees (see `AskUserQuestionInput`'s Phase 4 note:
    that Pydantic model is validation-only, never the schema source)."""
    from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, BoundMethodTool

    instance = InternalTools()
    meta = getattr(InternalTools.ask_user_question, TOOL_META_ATTR)
    return BoundMethodTool(instance.ask_user_question, meta)


class TestReasoningToolSchema:
    """Ask User Tool Improvement Plan, Phase 4: `reasoning` must be visible
    to the LLM via the REAL tool schema (`ToolParameter` list on the
    `@tool` decorator), not just accepted by `AskUserQuestionInput`."""

    def test_reasoning_appears_in_schema_properties(self) -> None:
        schema = _ask_user_question_tool().to_schema()
        assert "reasoning" in schema.input_schema["properties"]

    def test_reasoning_is_not_required(self) -> None:
        """`required=False` by design — backward compatibility with
        callers/tests that omit it wins over making it mandatory."""
        schema = _ask_user_question_tool().to_schema()
        assert "reasoning" not in schema.input_schema["required"]

    def test_user_intent_and_questions_remain_required(self) -> None:
        schema = _ask_user_question_tool().to_schema()
        assert "user_intent" in schema.input_schema["required"]
        assert "questions" in schema.input_schema["required"]

    async def test_call_omitting_reasoning_still_executes(self) -> None:
        """Backward compatibility: existing callers (pre-run clarification,
        older conversation turns, tests) that never pass `reasoning` must
        keep working unchanged."""
        tools = InternalTools()
        result_json = await tools.ask_user_question(
            user_intent="Understood: report request. Ambiguous: which quarter.",
            questions=[
                AskUserQuestionItemInput(
                    question="Which quarter?",
                    options=[
                        AskUserQuestionOptionInput(label="Q1", isUserInput=False),
                        AskUserQuestionOptionInput(label="Q2", isUserInput=False),
                        AskUserQuestionOptionInput(label="Q3", isUserInput=False),
                    ],
                    multiSelect=False,
                ),
            ],
        )
        payload = json.loads(result_json)
        assert payload["name"] == "ask_user_question"
        assert "reasoning" not in payload

    async def test_reasoning_argument_never_leaks_into_ui_payload(self) -> None:
        """`reasoning` is audit-only (logged by `hooks/ask_user_quality.py`)
        — the UI/Node.js persistence payload must never carry it."""
        tools = InternalTools()
        result_json = await tools.ask_user_question(
            user_intent="Understood: report request. Ambiguous: which quarter.",
            questions=[
                AskUserQuestionItemInput(
                    question="Which quarter?",
                    options=[
                        AskUserQuestionOptionInput(label="Q1", isUserInput=False),
                        AskUserQuestionOptionInput(label="Q2", isUserInput=False),
                        AskUserQuestionOptionInput(label="Q3", isUserInput=False),
                    ],
                    multiSelect=False,
                ),
            ],
            reasoning="User asked for 'the report' with no quarter specified.",
        )
        payload = json.loads(result_json)
        assert "reasoning" not in payload
        assert set(payload.keys()) == {"name", "userIntent", "questions"}

    def test_ask_user_question_input_model_accepts_reasoning(self) -> None:
        """`AskUserQuestionInput` mirrors the real schema for coercion-test
        purposes (see its Phase 4 docstring) — it must accept `reasoning`
        too, even though it is not what the LLM actually sees."""
        raw = {
            "user_intent": "test",
            "questions": ["Pick one|A|B|C"],
            "reasoning": "why I'm asking",
        }
        model = AskUserQuestionInput.model_validate(raw)
        assert model.reasoning == "why I'm asking"

    def test_ask_user_question_input_reasoning_defaults_to_empty(self) -> None:
        raw = {"user_intent": "test", "questions": ["Pick one|A|B|C"]}
        model = AskUserQuestionInput.model_validate(raw)
        assert model.reasoning == ""


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
    assert "uuid" in question and question["uuid"]

    options = question["options"]
    assert len(options) == 3
    assert options[0]["label"] == "#general"
    assert options[0]["isUserInput"] is False
    assert options[0]["id"] == "opt_#general"
    assert options[2]["isUserInput"] is True
