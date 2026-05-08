"""
Agent-specific response schemas with referenceData support.
Separate from chatbot schemas to avoid any impact on chatbot performance.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field
from typing_extensions import TypedDict


class ReferenceDataItem(TypedDict, total=False):
    name: str
    id: str
    type: str
    app: str
    webUrl: str
    metadata: dict[str, str]  # App-specific fields (e.g. key for Jira, siteId for SharePoint)


class PlannedToolCall(BaseModel):
    name: str = Field(description="Fully qualified tool name, e.g. 'retrieval.search_internal_knowledge'")
    args: dict[str, Any] = Field(default_factory=dict, description="Tool arguments as key-value pairs")


class PlannerOutput(BaseModel):
    intent: str = Field(default="", description="Brief description of the user's intent")
    reasoning: str = Field(default="", description="Why these tools were selected")
    can_answer_directly: bool = Field(default=False, description="True if the query can be answered without tools")
    needs_clarification: bool = Field(default=False, description="True only if a write action is missing required parameters")
    clarifying_question: str = Field(default="", description="Question to ask the user when needs_clarification is true")
    tools: list[PlannedToolCall] = Field(default_factory=list, description="Ordered list of tools to execute")


class AgentAnswerWithMetadataJSON(BaseModel):
    answer: str
    reason: str | None = None
    confidence: Literal["Very High", "High", "Medium", "Low"]
    answerMatchType: Literal["Exact Match", "Derived From Blocks", "Derived From User Info", "Enhanced With Full Record", "Derived From Tool Execution"] | None = None
    referenceData: list[dict] | None = None


class AgentAnswerWithMetadataDict(TypedDict, total=False):
    answer: str
    reason: str
    confidence: Literal["Very High", "High", "Medium", "Low"]
    answerMatchType: Literal[
        "Exact Match",
        "Derived From Blocks",
        "Derived From User Info",
        "Enhanced With Full Record",
        "Derived From Tool Execution"
    ]
    referenceData: list[ReferenceDataItem] | None
