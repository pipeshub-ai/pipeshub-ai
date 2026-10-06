"""Wire shape of the agent draft the assistant proposes in a chat."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DRAFT_TOOL_NAME = "draft_agent"


class AgentDraft(BaseModel):
    """`toolsets` is always empty: the requester ticks every tool in the card."""

    model_config = ConfigDict(extra="forbid")

    draftId: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    handleSuggestion: str
    description: str
    instructions: str
    knowledge: list[str] = Field(default_factory=list)
    toolsets: list[str] = Field(default_factory=list, max_length=0)
    suggestedTools: list[str] = Field(default_factory=list)
    provenance: Literal["sender", "content"] = "sender"
    requestedBy: str
