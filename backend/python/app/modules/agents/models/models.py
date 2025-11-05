
from pydantic import BaseModel


class AgentTemplateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    startMessage: str | None = None
    systemPrompt: str | None = None
    tools: list[str] | None = None
    models: list[str] | None = None
    memory: str | None = None
    tags: list[str] | None = None
    orgId: str | None = None
    isActive: bool | None = None
    createdBy: str | None = None
    updatedByUserId: str | None = None
    createdAtTimestamp: int | None = None
    updatedAtTimestamp: int | None = None


class AgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    startMessage: str | None = None
    systemPrompt: str | None = None
    tools: list[str] | None = None
    models: list[str] | None = None
    apps: list[str] | None = None
    kb: list[str] | None = None
    vectorDBs: list[str] | None = None
    tags: list[str] | None = None
    orgId: str | None = None
    createdBy: str | None = None
    updatedByUserId: str | None = None
    createdAtTimestamp: int | None = None
    updatedAtTimestamp: int | None = None




