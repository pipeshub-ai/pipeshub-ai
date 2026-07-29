import json
import uuid

from typing import Any
from pydantic import BaseModel, Field, model_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL, TAG_UI_ONLY
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)

# Max option labels when coercing pipe-separated planner shorthand (matches options max_length).
_ASK_USER_QUESTION_MAX_OPTIONS = 7


class AskUserQuestionOptionInput(BaseModel):
    label: str = Field(
        description=(
            "Tappable option label. "
            "NEVER use catch-all labels such as 'Other', 'Something else', 'None of the above', "
            "'Other option', 'Custom option', 'Not listed', 'None of these', 'Enter your own', "
            "or any similar open-ended fallback. The UI auto-appends a 'Something else' option — "
            "every option you provide must be concrete and specific."
        )
    )
    isUserInput: bool = Field(
        default=False,
        description=(
            "If true, selecting this option reveals a free-text input field for the user to type into. "
            "Set to true ONLY when the answer is a specific value the user must type — "
            "e.g. 'Enter a person by name', 'Enter an email address', 'Enter a date', "
            "'Specify a custom amount', 'Enter a project name', 'Type a URL'. "
            "The label MUST clearly state what the user should enter (noun or format). "
            "NEVER emit a generic catch-all option — labels like 'Other', 'Something else', "
            "'None of the above', 'Enter your own', 'Custom option', 'Not listed', or 'None of these' "
            "are FORBIDDEN regardless of isUserInput value. The UI automatically appends its own "
            "'Something else' free-text option to every question — never duplicate it. "
            "Keep to at most 1–2 isUserInput options per question; prefer tappable options for everything enumerable."
        )
    )

    @model_validator(mode='before')
    @classmethod
    def coerce_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"label": v, "isUserInput": False}
        return v


class AskUserQuestionItemInput(BaseModel):
    question: str = Field(description="Question prompt to show to the user")
    options: list[AskUserQuestionOptionInput] = Field(
        description=(
            "Sorted tappable options for this question. "
            "Every option must be concrete and specific. "
            "NEVER include 'Other', 'Something else', 'None of the above', or any open-ended catch-all option. "
            "The UI always adds its own 'Something else' free-text option — never duplicate it."
        ),
        min_length=3,
        max_length=7,
    )
    multiSelect: bool = Field(
        default=False,
        description=(
            "REQUIRED decision for every question — do not blindly default to false. "
            "Set to TRUE when the user can logically pick MORE THAN ONE option "
            "(e.g. 'Which topics to cover?', 'Which features do you need?', "
            "'Which attendees to include?', 'Select applicable categories', "
            "'Which days work for you?', 'Which formats should be included?'). "
            "Set to FALSE only for genuinely mutually-exclusive single-answer choices "
            "(e.g. 'Which priority level?', 'Pick a single date', 'Choose one template', "
            "'Select one format'). "
            "If in doubt, prefer TRUE — most preference questions allow multiple answers. "
            "A single ask_user_question call SHOULD mix true and false questions as appropriate."
        ),
    )

    @model_validator(mode='before')
    @classmethod
    def coerce_string_or_pipe_shorthand(cls, v: Any) -> Any:
        """Accept dicts, JSON object strings, or pipe-separated question|opt1|opt2|… shorthand."""
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("{"):
                try:
                    parsed = json.loads(s)
                except json.JSONDecodeError:
                    return v
                if isinstance(parsed, dict):
                    return parsed
                return v
            if "|" in s:
                parts = [p.strip() for p in s.split("|") if p.strip()]
                # One question segment plus at least three option labels (options min_length=3).
                if len(parts) >= 4:
                    question_text, *labels = parts
                    labels = labels[:_ASK_USER_QUESTION_MAX_OPTIONS]
                    return {
                        "question": question_text,
                        "options": labels,
                        "multiSelect": False,
                    }
            return v
        return v


class AskUserQuestionInput(BaseModel):
    user_intent: str = Field(
        description=(
            "Display text shown to the user above the questions, in the form "
            "'Understood: <what you understood>. Ambiguous: <what's unclear>. "
            "This helps me <how the answer changes your next action>.'"
        ),
    )
    questions: list[AskUserQuestionItemInput] = Field(
        description=(
            "Focused questions with tappable options. "
            "Each question independently sets multiSelect=true (when the user can pick multiple: "
            "topics, features, attendees, days, formats, categories) "
            "or multiSelect=false (only for truly mutually-exclusive single-answer choices). "
            "A single call SHOULD mix single-select and multi-select questions as needed."
        ),
        min_length=1,
        max_length=5,
    )
    # Ask User Tool Improvement Plan, Phase 4: audit-only, mirrors the
    # `reasoning` ToolParameter below so the coercion/validation tests here
    # stay representative of what the LLM can actually send — but this
    # model is NOT what the LLM sees (see `to_schema()` in
    # `agent_loop_lib/tools/base.py`); the `ToolParameter` list on the
    # `@tool` decorator is the real, LLM-facing contract. Optional and
    # excluded from `ask_user_question()`'s returned JSON payload —
    # logged by `hooks/ask_user_quality.py` instead of shown to the user.
    reasoning: str = Field(
        default="",
        max_length=200,
        description=(
            "Why you are asking instead of acting: what is ambiguous and how "
            "the answer changes your next action. Not shown to the user."
        ),
    )

    @model_validator(mode='before')
    @classmethod
    def coerce_questions_json_string(cls, data: Any) -> Any:
        """If the model double-encodes ``questions`` as a JSON array string, parse it."""
        if not isinstance(data, dict):
            return data
        raw = data.get("questions")
        if isinstance(raw, str):
            s = raw.strip()
            try:
                parsed = json.loads(s)
            except json.JSONDecodeError:
                return data
            if isinstance(parsed, list):
                return {**data, "questions": parsed}
        return data


@ToolsetBuilder("InternalTools")\
    .in_group("Internal Tools")\
    .with_description("Interim interactive question tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .as_essential()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/draft_mail.svg"))\
    .build_decorator()
class InternalTools:
    """InternalTools tools exposed to the agents.

    Provides the structured question tool used to gather missing information
    from the user via tappable option cards before executing write operations.
    """

    def __init__(self) -> None:
        pass

    @tool(
        path="/tools/internaltools/ask_user_question",
        short_description="Ask the user a structured question with tappable options",
        # Ask User Tool Improvement Plan, Phase 4: shortened to a crisp
        # purpose statement — the full ask-vs-act decision rubric (when to
        # ask vs. act, how to ground options in live data) now lives in the
        # system prompt's dedicated "When to Ask the User" section
        # (`prompt_builder.py`'s `_ASK_VS_ACT_RUBRIC`, rendered whenever
        # this tool is granted), so it no longer needs to be duplicated
        # here.
        description=(
            "The only way to ask the user a question — never write a question in your response "
            "text. Use when required parameters for a write action are missing, or the request "
            "maps to 2+ incompatible targets/scopes with nothing in context to resolve it; never "
            "for a broad-but-searchable topic, which goes to retrieval instead. Options must be "
            "concrete and grounded in tool schemas, retrieved data, or conversation history — "
            "never invented placeholders or an 'Other'/'Something else' catch-all — and every "
            "question must explicitly set multiSelect."
        ),
        parameters=[
            ToolParameter(
                name="user_intent",
                type=ParameterType.STRING,
                description=(
                    "Display text shown to the user above the questions, in the form "
                    "'Understood: <what you understood>. Ambiguous: <what's unclear>. "
                    "This helps me <how the answer changes your next action>.'"
                ),
                required=True,
            ),
            ToolParameter(
                name="questions",
                type=ParameterType.ARRAY,
                description="Focused questions with tappable options. Each question independently sets multiSelect.",
                required=True,
                items={"type": "object"},
            ),
            ToolParameter(
                name="reasoning",
                type=ParameterType.STRING,
                required=False,
                description=(
                    "Why you are asking instead of acting: what is ambiguous and how "
                    "the answer changes your next action. Not shown to the user."
                ),
            ),
        ],
        tags=[
            Tag(key="category", value="utility"),
            Tag(key="type", value="utility"),
            TAG_UI_ONLY,
            TAG_LIFECYCLE_TERMINAL,
        ],
    )
    async def ask_user_question(
        self,
        user_intent: str,
        questions: list[AskUserQuestionItemInput],
        reasoning: str = "",
    ) -> str:
        """Return structured interactive questions with a wrapper message.

        `reasoning` (optional, Phase 4) is an audit-only field — logged by
        `hooks/ask_user_quality.py`, deliberately left OUT of the returned
        JSON payload below so the UI and the Node.js persistence path never
        need to change in step with it (see `respond.py`'s fallback-emitter
        audit note)."""
        normalized_questions: list[dict[str, Any]] = []
        for item in questions:
            if isinstance(item, AskUserQuestionItemInput):
                question_text = item.question
                options = item.options
                multi_select = item.multiSelect
            else:
                question_text = str(item.get("question", ""))
                options = item.get("options", [])
                multi_select = bool(item.get("multiSelect", False))

            normalized_options: list[dict[str, Any]] = []
            for option in options:
                if isinstance(option, AskUserQuestionOptionInput):
                    label = option.label
                    is_user_input = option.isUserInput
                else:
                    label = str(option.get("label", ""))
                    is_user_input = bool(option.get("isUserInput", False))
                option_id = "opt_" + label.lower().replace(" ", "_")
                normalized_options.append({
                    "id": option_id,
                    "label": label,
                    "isUserInput": is_user_input,
                })

            normalized_questions.append({
                "uuid": str(uuid.uuid4()),
                "question": question_text,
                "options": normalized_options,
                "multiSelect": multi_select,
            })

        response_payload = {
            "name": "ask_user_question",
            "userIntent": user_intent,
            "questions": normalized_questions,
        }
        return json.dumps(response_payload, ensure_ascii=False)
