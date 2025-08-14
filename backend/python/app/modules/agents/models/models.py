from typing import Optional

from pydantic import BaseModel


class AgentTemplateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    startMessage: Optional[str] = None
    systemPrompt: Optional[str] = None
    tools: Optional[list[str]] = None
    models: Optional[list[str]] = None
    memory: Optional[str] = None
    tags: Optional[list[str]] = None
    orgId: Optional[str] = None
    isActive: Optional[bool] = None
    createdBy: Optional[str] = None
    updatedByUserId: Optional[str] = None
    createdAtTimestamp: Optional[int] = None
    updatedAtTimestamp: Optional[int] = None


class AgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    startMessage: Optional[str] = None
    systemPrompt: Optional[str] = None
    tools: Optional[list[str]] = None
    models: Optional[list[str]] = None
    apps: Optional[list[str]] = None
    kb: Optional[list[str]] = None
    vectorDBs: Optional[list[str]] = None
    tags: Optional[list[str]] = None
    orgId: Optional[str] = None
    createdBy: Optional[str] = None
    updatedByUserId: Optional[str] = None
    createdAtTimestamp: Optional[int] = None
    updatedAtTimestamp: Optional[int] = None




