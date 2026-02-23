"""
Agent-specific response schemas with referenceData support.
Separate from chatbot schemas to avoid any impact on chatbot performance.
"""
from typing import List, Literal, Optional

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
    reason: Optional[str] = None
    confidence: Literal["Very High", "High", "Medium", "Low"]
    answerMatchType: Optional[Literal[
        "Exact Match",
        "Derived From Blocks",
        "Derived From User Info",
        "Enhanced With Full Record",
        "Derived From Tool Execution"
    ]] = None
    blockNumbers: Optional[List[str]] = None
    referenceData: Optional[List[dict]] = None


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
    blockNumbers: List[str]
    referenceData: Optional[List[ReferenceDataItem]]
