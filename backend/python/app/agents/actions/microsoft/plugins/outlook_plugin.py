import json
import logging
import uuid

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)

logger = logging.getLogger(__name__)


class AskUserQuestionOptionInput(BaseModel):
    label: str = Field(description="Tappable option label")
    description: str = Field(default="", description="Short description for the option")
    isUserInput: bool = Field(
        default=False,
        description="If true, selecting this option shows a free-text input field instead of selecting a fixed value"
    )

    @model_validator(mode='before')
    @classmethod
    def coerce_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"label": v, "description": v, "isUserInput": False}
        return v

class AskUserQuestionItemInput(BaseModel):
    question: str = Field(description="Question prompt to show to the user")
    header: str = Field(default="", description="Short heading shown above the question")
    options: list[AskUserQuestionOptionInput] = Field(
        description="2-4 tappable options for this question",
        min_length=2,
        max_length=4,
    )
    multiSelect: bool = Field(default=False, description="Whether this question supports multi-select")


class AskUserQuestionInput(BaseModel):
    questions: list[AskUserQuestionItemInput] = Field(
        description="1-4 focused questions with tappable options",
        min_length=1,
        max_length=4,
    )


class DraftMailInput(BaseModel):
    to: list[str] = Field(description="List of recipient email addresses")
    cc: Optional[list[str]] = Field(default=None, description="List of CC recipient email addresses")
    bcc: Optional[list[str]] = Field(default=None, description="List of BCC recipient email addresses")
    subject: str = Field(description="The subject line of the email")
    body: str = Field(description="The body content of the email")


class DraftMailRawInput(BaseModel):
    raw_text: str = Field(
        description="Raw unstructured text containing mail details such as recipients, subject, and body"
    )


class DraftMailOutput(BaseModel):
    to: list[str] = Field(description="List of recipient email addresses")
    cc: list[str] = Field(default_factory=list, description="List of CC recipient email addresses")
    bcc: list[str] = Field(default_factory=list, description="List of BCC recipient email addresses")
    subject: str = Field(description="The subject line of the email")
    body: str = Field(description="The body content of the email")



_DOCUMENT_BLOCK_FIELDS = frozenset({"text", "id", "formatting"})


# Define the schema for individual text segments
class DocumentBlock(BaseModel):
    """Office.js document segment; LLM may omit id or flatten style keys onto the block."""

    model_config = ConfigDict(extra="ignore")

    text: str = Field(
        description="The actual text content to be formatted."
    )
    id: str = Field(
        description=(
            "A unique identifier for this range, or the specific text snippet to search for. "
            "Omitted values are filled server-side."
        )
    )
    formatting: Dict[str, Any] = Field(
        description=(
            "A nested dictionary of Office.js range properties. "
            "Example: {'font': {'color': 'green', 'bold': True}, 'shading': {'background': '#F0F0F0'}}"
        )
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_block(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw = dict(data)
        spillover: Dict[str, Any] = {}
        for key in list(raw.keys()):
            if key not in _DOCUMENT_BLOCK_FIELDS:
                spillover[key] = raw.pop(key)
        text = raw.get("text")
        if text is None:
            raise ValueError("text is required")
        block_id_raw = raw.get("id")
        explicit_fmt = raw.get("formatting")
        fmt: Dict[str, Any] = {**spillover}
        if isinstance(explicit_fmt, dict):
            fmt = {**fmt, **explicit_fmt}
        if not isinstance(block_id_raw, str) or not block_id_raw.strip():
            block_id = str(uuid.uuid4())
        else:
            block_id = block_id_raw.strip()
        return {"text": text, "id": block_id, "formatting": fmt}


class FormatDocumentInput(BaseModel):
    blocks: List[DocumentBlock]

# Register DraftMail toolset (internal - always available, no auth required, backend-only)
@ToolsetBuilder("OutlookPluginTools")\
    .in_group("Internal Tools")\
    .with_description("Email drafting and formatting tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/draft_mail.svg"))\
    .build_decorator()
class OutlookPluginTools:
    """OutlookPluginTools tool exposed to the agents.

    Converts raw or structured mail input into a properly formatted draft
    with to, cc, bcc, subject, and body fields.
    """

    def __init__(self) -> None:
        """Initialize the DraftMail tool.

        Args:
            None
        Returns:
            None
        """
        logger.info("🚀 Initializing DraftMail tool")

    def get_supported_fields(self) -> list[str]:
        """Get the supported mail fields.

        Args:
            None
        Returns:
            A list of supported mail fields.
        """
        return ["to", "cc", "bcc", "subject", "body"]

    # ------------------------------------------------------------------ #
    #  Tool: format a structured mail draft
    # ------------------------------------------------------------------ #
    @tool(
        app_name="outlookplugintools",
        tool_name="format_mail",
        args_schema=DraftMailInput,
        llm_description=(
            "Format a structured email draft with to, cc, bcc, subject, and body fields. "
            "Use when the individual fields are already known."
            "Use when the user draft or edits a mail with explicit to, cc, bcc, subject, and body information."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "User provides explicit to, cc, bcc, subject, and body for an email",
            "User wants to compose a well-structured email draft",
            "User wants to format an email before sending",
            "User wants to edit an email before sending",
        ],
        when_not_to_use=[
            "User provides raw unstructured text that needs parsing into mail fields",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Draft an email to john@example.com with subject 'Meeting Tomorrow'",
            "Compose a mail to the team about the project update",
            "Create an email draft to alice@corp.com cc bob@corp.com about quarterly review",
            "Write a formal email to hr@company.com regarding leave application",
        ],
    )
    def format_mail(
        self,
        to: list[str],
        subject: str,
        body: str,
        cc: Optional[list[str]] = None,
        bcc: Optional[list[str]] = None,
    ) -> dict:
        """Format a structured email draft.

        Args:
            to: List of recipient email addresses.
            subject: The subject line of the email.
            body: The body content of the email.
            cc: Optional list of CC recipient email addresses.
            bcc: Optional list of BCC recipient email addresses.

        Returns:
            A dictionary representing the formatted email draft.
        """
        self._validate_recipients(to)

        if cc:
            self._validate_recipients(cc)
        if bcc:
            self._validate_recipients(bcc)

        draft = DraftMailOutput(
            to=to,
            cc=cc or [],
            bcc=bcc or [],
            subject=subject.strip(),
            body=body.strip(),
        )
        print(f"Formatted mail draft: {draft}")

        # logger.info("✉️  Mail draft formatted — to: %s, subject: %s", draft.to, draft.subject)
        return draft.model_dump()

    # ------------------------------------------------------------------ #
    #  Tool: parse raw text into a mail draft
    # ------------------------------------------------------------------ #
    @tool(
        app_name="outlookplugintools",
        tool_name="parse_raw_mail",
        args_schema=DraftMailRawInput,
        llm_description=(
            "Parse raw unstructured text and extract email fields (to, cc, bcc, subject, body). "
            "Use when the user provides a blob of text that needs to be converted into a structured email draft."
        ),
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "User provides raw or loosely formatted text that contains email information",
            "User pastes an unstructured block of text and asks to turn it into an email",
        ],
        when_not_to_use=[
            "User already provides structured to, cc, bcc, subject, and body fields",
        ],
        primary_intent=ToolIntent.ACTION,
        typical_queries=[
            "Convert this text into a proper email draft",
            "Parse the following into an email: To: alice@corp.com Subject: Hello Body: Hi Alice …",
            "Turn my notes into a formatted email",
        ],
    )
    def parse_raw_mail(self, raw_text: str) -> dict:
        """Parse raw unstructured text into a structured email draft.

        Args:
            raw_text: Raw text containing mail details.

        Returns:
            A dictionary representing the parsed email draft.
        """
        to = self._extract_field(raw_text, "to")
        cc = self._extract_field(raw_text, "cc")
        bcc = self._extract_field(raw_text, "bcc")
        subject = self._extract_single_field(raw_text, "subject")
        body = self._extract_body(raw_text)

        if not to:
            raise ValueError("Could not extract any 'to' recipients from the raw text")
        if not subject:
            raise ValueError("Could not extract a 'subject' from the raw text")

        draft = DraftMailOutput(
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
        )

        logger.info("✉️  Raw mail parsed — to: %s, subject: %s", draft.to, draft.subject)
        return draft.model_dump()

    @tool(
        app_name="outlookplugintools",
        tool_name="ask_user_question",
        args_schema=AskUserQuestionInput,
        llm_description=(
            "MANDATORY: This is the ONLY way to ask the user ANY question. "
            "NEVER write a question in your response text — always call this tool instead. "
            "Use whenever ANY required information is missing or unclear before proceeding with a task. "
            "Keep each question focused with 2–4 tappable options. "
            "Do not include an 'Other' option."
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
        ],
        when_not_to_use=[
            "User asks for direct analysis or recommendation with no missing fields",
            "User asks factual how-to questions",
            "The request is simple, unambiguous, and all required fields are present",
            "The question requires open-ended numeric input",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Build me a financial model",
            "Help me analyze this data",
            "Create a budget template",
            "Make me a presentation about X",
        ],
    )
    def ask_user_question(self, questions: list[AskUserQuestionItemInput]) -> str:
        """Return structured interactive questions with a wrapper message."""
        normalized_questions: list[dict[str, Any]] = []
        for item in questions:
            if isinstance(item, AskUserQuestionItemInput):
                question_text = item.question
                header = item.header
                options = item.options
                multi_select = item.multiSelect
            else:
                question_text = str(item.get("question", ""))
                header = str(item.get("header", ""))
                options = item.get("options", [])
                multi_select = bool(item.get("multiSelect", False))

            normalized_options: list[dict[str, str]] = []
            for option in options:
                if isinstance(option, AskUserQuestionOptionInput):
                    label = option.label
                    description = option.description
                    is_user_input = option.isUserInput
                else:
                    label = str(option.get("label", ""))
                    description = str(option.get("description", ""))
                    is_user_input = bool(option.get("isUserInput", False))
                option_id = "opt_" + label.lower().replace(" ", "_")
                normalized_options.append({
                    "id": option_id,
                    "label": label,
                    "description": description,
                    "isUserInput": is_user_input,
                })

            normalized_questions.append({
                "uuid": str(uuid.uuid4()),
                "question": question_text,
                "header": header,
                "options": normalized_options,
                "multiSelect": multi_select,
            })

        response_payload = {
            "message": "I have a couple quick questions to tailor this for you.",
            "type": "custom",
            "name": "ask_user_question",
            "description": (
                "Present tappable options to gather user preferences before starting work."
            ),
            "questions": normalized_questions,
        }
        return json.dumps(response_payload, ensure_ascii=False)

    @tool(
    app_name="outlookplugintools",
    tool_name="format_document_content",
    args_schema=FormatDocumentInput,
    llm_description=(
        "Converts user requests into a structured list of text blocks and Office.js formatting properties. "
        "Each block must include 'text' and a 'formatting' object (or put style keys at the top level; they are merged into formatting). "
        "'id' may be omitted; the server assigns a unique id. "
        "The 'formatting' dictionary must map to Word.Range or Office API property paths. "
        "Use for complex styling like font colors, weights, shading, and sizes across multiple ranges."
    ),
    category=ToolCategory.UTILITY,
    is_essential=True,
    requires_auth=False,
    when_to_use=[
        "User says 'Change the color of the invoice number to green'",
        "User wants to highlight specific ranges: 'Make all dates bold and blue'",
        "User requests specific document styling like 'Set all headers to Aptos size 24'",
    ],
    when_not_to_use=[
        "General conversation not involving document modification",
        "When the user is asking to delete text rather than format it",
    ],
    primary_intent=ToolIntent.ACTION,
    typical_queries=[
        "Format the text '#1234' as green and bold",
        "Apply a light gray background to the signature block",
        "Make the title 'Project Alpha' red and size 20",
    ],
)
    def format_document_content(
        self,
        blocks: List[DocumentBlock],
    ) -> dict:
        """
        Groups the formatted content blocks into a structured response for the Office.js Add-in.
        
        The 'formatting' dict in each block is designed to be iterated over by the frontend 
        and applied directly to the Office.js Range object.
        """
        
        # Optional: Add backend validation here if needed (e.g., checking for valid hex codes)
        
        output = {
            "status": "success",
            "execution_type": "DYNAMIC_PROPERTY_ASSIGNMENT",
            "blocks": [block.model_dump() for block in blocks]
        }
        
        print(f"Tool triggered: Generated {len(blocks)} blocks with dynamic formatting.")
        return output

# The Tool Definition
    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_recipients(recipients: list[str]) -> None:
        """Validate that every recipient string looks like an email address.

        Args:
            recipients: A list of email address strings.

        Raises:
            ValueError: If any recipient is not a valid email address.
        """
        for addr in recipients:
            if "@" not in addr or "." not in addr.split("@")[-1]:
                raise ValueError(f"Invalid email address: {addr}")

    @staticmethod
    def _extract_field(text: str, field_name: str) -> list[str]:
        """Extract a comma-separated list field from raw text.

        Looks for patterns like ``To: a@b.com, c@d.com``.

        Args:
            text: The raw text to search.
            field_name: The field label to look for (case-insensitive).

        Returns:
            A list of trimmed values, or an empty list if not found.
        """
        import re

        pattern = rf"(?i)^{field_name}\s*:\s*(.+)$"
        match = re.search(pattern, text, re.MULTILINE)
        if not match:
            return []
        raw_value = match.group(1).strip()
        # Split on comma or semicolon
        return [v.strip() for v in re.split(r"[;,]", raw_value) if v.strip()]

    @staticmethod
    def _extract_single_field(text: str, field_name: str) -> str:
        """Extract a single-value field from raw text.

        Args:
            text: The raw text to search.
            field_name: The field label to look for (case-insensitive).

        Returns:
            The extracted value, or an empty string if not found.
        """
        import re

        pattern = rf"(?i)^{field_name}\s*:\s*(.+)$"
        match = re.search(pattern, text, re.MULTILINE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_body(text: str) -> str:
        """Extract the body content from raw text.

        Looks for a ``Body:`` label and captures everything after it,
        or falls back to the remaining text after known fields.

        Args:
            text: The raw text to search.

        Returns:
            The extracted body string.
        """
        import re

        # Try explicit Body: label first
        match = re.search(r"(?i)^body\s*:\s*(.*)", text, re.MULTILINE | re.DOTALL)
        if match:
            return match.group(1).strip()

        # Fallback: strip out known fields and return the rest
        cleaned = re.sub(
            r"(?i)^(to|cc|bcc|subject)\s*:.+$", "", text, flags=re.MULTILINE
        )
        return cleaned.strip()