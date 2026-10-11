"""Discovery request and result models.

``None`` on a capability flag means the source did not say, which is not the
same as a confirmed false.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DiscoveryErrorCode(str, Enum):
    AUTH_ERROR = "auth_error"
    UNREACHABLE = "unreachable"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    NOT_SUPPORTED = "not_supported"
    INVALID_RESPONSE = "invalid_response"


class RawModel(BaseModel):
    """What a strategy parsed, before catalog and ID rules fill the gaps."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str | None = None
    capabilities: list[str] | None = None
    context_length: int | None = None
    is_multimodal: bool | None = None
    is_reasoning: bool | None = None
    supports_tools: bool | None = None
    deprecated: bool | None = None


class DiscoveredModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    display_name: str = Field(serialization_alias="displayName")
    capabilities: list[str]
    context_length: int | None = Field(default=None, serialization_alias="contextLength")
    is_multimodal: bool | None = Field(default=None, serialization_alias="isMultimodal")
    is_reasoning: bool | None = Field(default=None, serialization_alias="isReasoning")
    supports_tools: bool | None = Field(default=None, serialization_alias="supportsTools")
    deprecated: bool = False
    source: Literal["provider_api", "catalog", "heuristic"]


class DiscoveryRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    provider: str
    capability: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)
    query: str | None = None


class DiscoveryResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    supported: bool
    models: list[DiscoveredModel] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: DiscoveryErrorCode | None = Field(default=None, serialization_alias="errorCode")
    message: str | None = None


class DiscoveryHttpError(Exception):
    def __init__(self, code: DiscoveryErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
