import json
import uuid

from typing import Any
from pydantic import BaseModel, Field, model_validator

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
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
            "Brief summary of your understanding of the user's query, your evaluation, "
            "and reasoning for why these specific questions are being asked. "
            "This is displayed to the user as context above the questions."
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
        app_name="internaltools",
        tool_name="ask_user_question",
        args_schema=AskUserQuestionInput,
        llm_description=(
            "MANDATORY: This is the ONLY way to ask the user ANY question. "
            "NEVER write a question in your response text — always call this tool instead. "
            "Use when: (a) required parameters for a write action are missing and only the user can provide them, "
            "(b) the user's INTENT is ambiguous between incompatible goals (not just a vague topic — vague topics go to retrieval), "
            "(c) the query is too incomplete to act on (no topic, no action, bare fragments with no antecedent). "
            "Do NOT use for clear information requests with a searchable keyword — use retrieval for those. "
            "Keep each question focused with tappable options. "
            "Do not include any 'Other' option or 'Something else' option. "
            "For EVERY question you MUST explicitly set multiSelect: "
            "use true when the user can logically pick more than one answer "
            "(topics, features, attendees, days, formats, categories, items to include — default to true if unsure); "
            "use false only for genuinely mutually-exclusive single choices "
            "(one priority, one date, one template, one format). "
            "A single call SHOULD and CAN mix multiSelect=true and multiSelect=false questions. "
            "Provide user_intent summarizing your understanding of the query and why you are asking. "
            "⚠️ Before generating options, analyze the query and decide dynamically: "
            "(1) Enumerable live data (channels, users, projects, boards, spaces) → check if a READ tool exists that can list them. If yes, call that tool FIRST; use its actual response as options. "
            "Example: 'Send Hello in a channel' + `teams.list_channels` available → call `list_channels` first, then present the real channel names returned. "
            "(2) Fixed capability values (issue types, priorities, formats) → derive only from the tool schema; never offer a value the tool does not accept. "
            "(3) No tool can enumerate the resource → add one `isUserInput:true` option so the user can type it. "
            "Every option presented MUST be something the available tools can actually execute."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "You would otherwise write a question in your response text — use this tool instead",
            "ANY required parameter for a write action is missing and only the user can provide it",
            "You need to elicit user preferences before proceeding",
            "You are about to ask clarifying questions with enumerated options",
            "You need fast, structured answers through tappable choices",
            "PowerPoint slide edit intent is ambiguous (e.g. what \"remove points\" should do)",
            "The user's intent is ambiguous between incompatible goals (e.g. 'help me with the project' could mean search, create task, or generate report)",
            "The query is too incomplete to act on — no clear topic, no clear action, or a bare fragment like 'do it' / 'handle this' with no antecedent",
            "MANDATORY: whenever clarification is needed and this tool is available, always use it instead of plain-text questions",
        ],
        when_not_to_use=[
            "User asks for direct analysis or recommendation with no missing fields",
            "User asks factual how-to questions",
            "The request is simple, unambiguous, and all required fields are present",
            "The question requires open-ended numeric input",
            "The query is vague on topic but not on intent (e.g. 'what is the process' → use retrieval, don't ask)",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Build me a financial model",
            "Help me analyze this data",
            "Create a budget template",
            "Make me a presentation about X",
        ],
    )
    def ask_user_question(self, user_intent: str, questions: list[AskUserQuestionItemInput]) -> str:
        """Return structured interactive questions with a wrapper message."""
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

            normalized_options: list[dict[str, str]] = []
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
