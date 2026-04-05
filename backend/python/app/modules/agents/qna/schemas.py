"""
Agent-specific response schemas with referenceData support.
Separate from chatbot schemas to avoid any impact on chatbot performance.
"""
from typing import Literal

from pydantic import BaseModel
from typing_extensions import TypedDict


class ReferenceDataItem(TypedDict, total=False):
    name: str
    id: str
    type: str
    key: str
    accountId: str
    url: str


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
