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
