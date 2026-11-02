import json
import re
import uuid

from typing import Any
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.tags import TAG_LIFECYCLE_TERMINAL, TAG_UI_ONLY
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)

# One card: a few questions, each a small set of real choices (the app adds "Something else").
_ASK_USER_QUESTION_MIN_OPTIONS = 2
_ASK_USER_QUESTION_MAX_OPTIONS = 4
ASK_USER_QUESTION_MAX_QUESTIONS = 3
_MAX_QUESTION_CHARS = 300
_MAX_LABEL_CHARS = 120
_MAX_DESCRIPTION_CHARS = 200
_MAX_HEADER_CHARS = 20
_MAX_USER_INTENT_CHARS = 500

# The app adds its own 'Something else' choice, so a model's catch-all would show twice.
_CATCH_ALL_LABEL = re.compile(
    r"(?:other|others|other option|something else|anything else|none of (?:the above|these)"
    r"|not listed|enter your own|custom(?: option)?)(?:\s*\(.*\))?[.:]?",
    re.IGNORECASE,
)
_RECOMMENDED_MARK = re.compile(r"\s*[(\[]\s*recommended\s*[)\]]\s*$", re.IGNORECASE)

# Seen live: "Who is the owner?" offered "Project Atlas — Priya Shah", answering it in the card.
_NO_ANSWER_IN_OPTIONS_RULE = (
    "Never put the answer in an option: if you already know what each option would lead to "
    "(each project's owner or deadline), reply with those answers instead of asking."
)
_CATCH_ALL_RULE = (
    "NEVER use catch-all labels such as 'Other', 'Something else', 'None of the above' "
    "or 'Enter your own': the app adds its own 'Something else' choice for typed answers."
)


class AskUserQuestionOptionInput(BaseModel):
    label: str = Field(
        min_length=1,
        max_length=_MAX_LABEL_CHARS,
        description=f"Short, concrete choice the user taps. {_CATCH_ALL_RULE}",
    )
    description: str | None = Field(
        default=None,
        max_length=_MAX_DESCRIPTION_CHARS,
        description="Optional: one short line, only when the label alone is unclear. Never the answer itself.",
    )
    isUserInput: bool = Field(
        default=False,
        description=(
            "True only when the answer is a value the user must type (a name, an email, a date, an ID) "
            "and no tool can list the choices. The label says what to enter, e.g. 'Enter a ticket key'."
        ),
    )

    @model_validator(mode='before')
    @classmethod
    def coerce_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"label": v, "isUserInput": False}
        return v

    @field_validator("label")
    @classmethod
    def no_recommendation_mark(cls, label: str) -> str:
        # Options are shown without a pick of ours, even when a model tags one anyway.
        return _RECOMMENDED_MARK.sub("", label).strip() or label


class AskUserQuestionItemInput(BaseModel):
    question: str = Field(
        max_length=_MAX_QUESTION_CHARS,
        description="One decision, naming the thing: 'Which Jira project should the bug go in?'",
    )
    header: str | None = Field(
        default=None,
        max_length=_MAX_HEADER_CHARS,
        description="Optional label of one or two words shown above the question, e.g. 'Project'.",
    )
    options: list[AskUserQuestionOptionInput] = Field(
        description=(
            "2 to 4 options that lead to different results. "
            f"{_NO_ANSWER_IN_OPTIONS_RULE} {_CATCH_ALL_RULE}"
        ),
        min_length=_ASK_USER_QUESTION_MIN_OPTIONS,
        max_length=_ASK_USER_QUESTION_MAX_OPTIONS,
    )
    multiSelect: bool = Field(
        default=False,
        description=(
            "True when the user can pick several options (topics, attendees, days); "
            "false when exactly one answer makes sense (one project, one date, one template)."
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
                # One question segment plus at least the minimum number of option labels.
                if len(parts) > _ASK_USER_QUESTION_MIN_OPTIONS:
                    question_text, *labels = parts
                    labels = labels[:_ASK_USER_QUESTION_MAX_OPTIONS]
                    return {
                        "question": question_text,
                        "options": labels,
                        "multiSelect": False,
                    }
            return v
        return v

    @model_validator(mode='after')
    def options_are_distinct_choices(self) -> "AskUserQuestionItemInput":
        self.options = [o for o in self.options if not _CATCH_ALL_LABEL.fullmatch(o.label.strip())]
        if len(self.options) < _ASK_USER_QUESTION_MIN_OPTIONS:
            raise ValueError(
                f"give at least {_ASK_USER_QUESTION_MIN_OPTIONS} concrete options; the app already adds "
                "'Something else'"
            )
        labels = [option.label.strip().lower() for option in self.options]
        if len(set(labels)) != len(labels):
            raise ValueError("each option needs a different label")
        return self


class AskUserQuestionInput(BaseModel):
    user_intent: str = Field(
        max_length=_MAX_USER_INTENT_CHARS,
        description=(
            "One or two sentences: what you understood and why you need the answer. "
            "Shown to the user above the questions."
        ),
    )
    questions: list[AskUserQuestionItemInput] = Field(
        description="Every question you need, in one call.",
        min_length=1,
        max_length=ASK_USER_QUESTION_MAX_QUESTIONS,
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


_NO_QUESTIONS_ERROR = (
    "ask_user_question was called without a question, so nothing was asked. "
    "If you need no clarification, continue the task with your other tools "
    "or give your answer."
)

# The argument check before a call covers names and top-level types only, so the
# per-question limits are enforced here.
_MAX_REPORTED_ERRORS = 3


def _invalid_input_error(error: ValidationError) -> str:
    problems = []
    for item in error.errors()[:_MAX_REPORTED_ERRORS]:
        where = ".".join(str(part) for part in item.get("loc", ()))
        problems.append(f"{where}: {item.get('msg', 'invalid')}" if where else str(item.get("msg", "invalid")))
    return (
        "ask_user_question was not shown: " + "; ".join(problems) + ". "
        f"Use 1 to {ASK_USER_QUESTION_MAX_QUESTIONS} questions, each with "
        f"{_ASK_USER_QUESTION_MIN_OPTIONS} to {_ASK_USER_QUESTION_MAX_OPTIONS} different options, "
        "and call it again, or carry on without asking."
    )


def _question_text(item: AskUserQuestionItemInput | dict[str, Any] | str) -> str:
    if isinstance(item, AskUserQuestionItemInput):
        return item.question
    if isinstance(item, dict):
        question = item.get("question", "")
        # str() would turn a null question into a card reading "None".
        return question if isinstance(question, str) else ""
    return str(item)


def asked_questions(questions: list[Any] | None) -> list[Any]:
    """The questions with text to show; a blank one would render as an empty card."""
    return [item for item in questions or [] if _question_text(item).strip()]


def question_payload(
    user_intent: str, questions: list[AskUserQuestionItemInput] | list[dict[str, Any]],
) -> dict[str, Any]:
    """The structured payload a client renders as question cards. New fields
    appear only when set, so a card without them is the shape older clients know."""
    normalized_questions: list[dict[str, Any]] = []
    for raw in questions:
        item = raw if isinstance(raw, AskUserQuestionItemInput) else AskUserQuestionItemInput.model_construct(
            question=_question_text(raw),
            header=raw.get("header") if isinstance(raw.get("header"), str) else None,
            options=[
                AskUserQuestionOptionInput.model_construct(
                    label=str(option.get("label", "")),
                    description=option.get("description") if isinstance(option.get("description"), str) else None,
                    isUserInput=bool(option.get("isUserInput", False)),
                )
                for option in raw.get("options", [])
            ],
            multiSelect=bool(raw.get("multiSelect", False)),
        )

        normalized_options: list[dict[str, Any]] = []
        for option in item.options:
            normalized: dict[str, Any] = {
                "id": "opt_" + option.label.lower().replace(" ", "_"),
                "label": option.label,
                "isUserInput": option.isUserInput,
            }
            if option.description and option.description.strip():
                normalized["description"] = option.description.strip()
            normalized_options.append(normalized)

        normalized_question: dict[str, Any] = {
            "uuid": str(uuid.uuid4()),
            "question": item.question,
            "options": normalized_options,
            "multiSelect": item.multiSelect,
        }
        if item.header and item.header.strip():
            normalized_question["header"] = item.header.strip()
        normalized_questions.append(normalized_question)

    return {
        "name": "ask_user_question",
        "userIntent": user_intent,
        "questions": normalized_questions,
    }


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
        short_description="Ask the user a short multiple-choice question when you cannot continue well without their answer",
        description=(
            "Ask the user a short multiple-choice question. The run stops until they answer, so ask only "
            "when all three are true: "
            "(1) the answer is not in the user's message, the conversation so far, or anything your other "
            "tools can look up; "
            "(2) different answers would make you do different things, and one short reply cannot cover "
            "them all (when it can, such as each project's deadline or owner, give that reply instead); "
            "(3) a wrong guess would be costly: the action is hard to undo, other people would see it "
            "(sending, creating, updating or deleting something), or it is writing meant for other "
            "people (an email, a message, a document, a presentation) whose subject the request leaves "
            "open, however short. "
            "When the user asks you to ask them something (a question, a quiz, a choice), asking is the "
            "task itself, not a guess: ask it, with this tool when it has options or in your reply when "
            "it is open, and never drop the question. "
            "Otherwise do the task with the most likely meaning and state your assumption in one line of "
            "your answer. "
            "A vague but searchable topic is not a reason to ask: search first. "
            "Never ask for permission to do what the user already asked for, or for reassurance. "
            "If the user already answered the same question earlier in this conversation and that "
            "answer still fits the current task, use it instead of asking again. "
            "Before changing data the user did not ask you to change, confirm with this tool. "
            "Ask for the details of an action (a recipient, a subject, a project) only when you can do "
            "that action: check that one of your tools, or a toolset you can load, does it. When none "
            "does, tell the user you cannot do it instead of asking; you can still draft text they asked "
            "you to write. "
            "When you do need an answer before continuing, ask with this tool, not in your reply text. "
            f"Put every question you need in one call (at most {ASK_USER_QUESTION_MAX_QUESTIONS}), each "
            f"about one decision, with {_ASK_USER_QUESTION_MIN_OPTIONS} to {_ASK_USER_QUESTION_MAX_OPTIONS} "
            "options that lead to different results. Add a short description only when the label alone "
            "does not make the choice clear, and never put the answer to the question in an option. "
            "Don't mark any option as recommended or best. "
            "Choosing options: "
            "(1) live data (channels, users, projects, boards, spaces): if a read tool can list them, call it "
            "first and use its results; "
            "(2) fixed values (issue types, priorities, formats): offer only values the target tool accepts; "
            "(3) nothing can list the value: add one isUserInput option so the user can type it. "
            "The app adds a 'Something else' choice for typed answers and a Skip button, so never add an "
            "'Other' option. Provide user_intent saying what you understood and why you are asking."
        ),
        parameters=[
            ToolParameter(
                name="user_intent",
                type=ParameterType.STRING,
                description=(
                    "One or two sentences: what you understood and why you need the answer. "
                    "Shown to the user above the questions."
                ),
                required=True,
            ),
            ToolParameter(
                name="questions",
                type=ParameterType.ARRAY,
                description=f"Every question you need, in one call: 1 to {ASK_USER_QUESTION_MAX_QUESTIONS}.",
                required=True,
                items={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": _MAX_QUESTION_CHARS,
                            "description": "One decision, naming the thing: 'Which Jira project should the bug go in?'",
                        },
                        "header": {
                            "type": "string",
                            "maxLength": _MAX_HEADER_CHARS,
                            "description": "Optional label of one or two words shown above the question, e.g. 'Project'.",
                        },
                        "options": {
                            "type": "array",
                            "description": (
                                f"{_ASK_USER_QUESTION_MIN_OPTIONS} to {_ASK_USER_QUESTION_MAX_OPTIONS} options "
                                f"that lead to different results. {_NO_ANSWER_IN_OPTIONS_RULE} {_CATCH_ALL_RULE}"
                            ),
                            "minItems": _ASK_USER_QUESTION_MIN_OPTIONS,
                            "maxItems": _ASK_USER_QUESTION_MAX_OPTIONS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": _MAX_LABEL_CHARS,
                                        "description": "Short, concrete choice the user taps (1 to 5 words).",
                                    },
                                    "description": {
                                        "type": "string",
                                        "maxLength": _MAX_DESCRIPTION_CHARS,
                                        "description": "Optional: one short line, only when the label alone is unclear. Never the answer itself.",
                                    },
                                    "isUserInput": {
                                        "type": "boolean",
                                        "default": False,
                                        "description": (
                                            "True only when the user must type the value (a name, an email, a "
                                            "date, an ID) and no tool can list the choices. The label says what "
                                            "to enter, e.g. 'Enter a ticket key'."
                                        ),
                                    },
                                },
                                "required": ["label"],
                            },
                        },
                        "multiSelect": {
                            "type": "boolean",
                            "default": False,
                            "description": (
                                "True when the user can pick several options (topics, attendees, days); "
                                "false when exactly one answer makes sense."
                            ),
                        },
                    },
                    "required": ["question", "options"],
                },
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
        self, user_intent: str, questions: list[AskUserQuestionItemInput],
    ) -> str | ToolOutput:
        """Return structured interactive questions with a wrapper message.

        A call with no question, or one outside the limits, is refused rather
        than ending the run: this tool is terminal, and a refused call lets the
        model fix it or carry on instead of handing its filler text back as
        the final answer.
        """
        # Argument validation checks only names, so a blank item can sit
        # beside a real one.
        questions = asked_questions(questions)
        if not questions:
            return ToolOutput(success=False, error=_NO_QUESTIONS_ERROR)
        try:
            parsed = AskUserQuestionInput.model_validate({"user_intent": user_intent, "questions": questions})
        except ValidationError as e:
            return ToolOutput(success=False, error=_invalid_input_error(e))
        return json.dumps(question_payload(parsed.user_intent, parsed.questions), ensure_ascii=False)
