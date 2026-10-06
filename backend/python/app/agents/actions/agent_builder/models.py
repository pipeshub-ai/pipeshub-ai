"""Wire shape of the agent draft the assistant proposes in a chat."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DRAFT_TOOL_NAME = "draft_agent"


class DraftKnowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    kind: Literal["collection", "connector"]
    connectorType: str | None = None


class DraftTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    fullName: str
    description: str = ""


class DraftToolset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instanceId: str
    instanceName: str | None = None
    name: str
    displayName: str
    iconPath: str = ""
    category: str = "app"
    tools: list[DraftTool]


class DraftWebSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    providerLabel: str


class DraftUnresolved(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["knowledge", "tool", "webSearch"]
    query: str
    reason: Literal["not_found", "ambiguous", "not_connected", "unavailable"]
    candidates: list[str] = Field(default_factory=list)


class AgentDraft(BaseModel):
    """`toolsets` and `suggestedTools` are legacy: stored drafts carry them, new drafts use `actions`."""

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
    knowledgeSources: list[DraftKnowledge] = Field(default_factory=list)
    actions: list[DraftToolset] = Field(default_factory=list)
    webSearch: DraftWebSearch | None = None
    unresolved: list[DraftUnresolved] = Field(default_factory=list)
    revisesDraftId: str | None = None
