"""Inputs and outputs of `AgentService`, independent of the HTTP layer."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from pydantic.alias_generators import to_camel

AgentOrigin = Literal["ui", "chat"]

# The request body has always treated these flags by truthiness, not by type.
Truthy = Annotated[bool, BeforeValidator(bool)]


def _lenient_list(value: object) -> object:
    return value if isinstance(value, list) else []


# `builders._parse_*` have always read anything but a list as empty.
LenientList = Annotated[list[Any], BeforeValidator(_lenient_list)]


class AgentActor(BaseModel):
    """The authenticated caller. `user_key` is the graph key of the user
    document; `user_id` is the Mongo id carried by the token."""

    user_key: str
    user_id: str
    org_id: str
    is_admin: bool = False


class ChatProvenance(BaseModel):
    """The chat message an agent was drafted from (audit only)."""

    conversation_id: str
    message_id: str


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class AgentSpec(_Camel):
    """Everything needed to create an agent; mirrors the frontend
    `AgentFormPayload`. Attachment lists stay loosely typed because
    `builders._parse_*` normalise and drop malformed entries."""

    name: str
    handle: str | None = None
    description: str = ""
    start_message: str = ""
    system_prompt: str = ""
    instructions: str = ""
    models: LenientList = Field(default_factory=list)
    tags: list[Any] | None = None
    default_reasoning_effort: Any = None
    web_search: Any = None
    toolsets: LenientList = Field(default_factory=list)
    mcp_servers: LenientList = Field(default_factory=list)
    knowledge: LenientList = Field(default_factory=list)
    skills: LenientList = Field(default_factory=list)
    is_service_account: Truthy = False
    share_with_org: Truthy = False
    send_user_context: Truthy = True


class AgentPatch(_Camel):
    """A partial update. A field that is present, even if empty, replaces the
    stored value, so use `model_fields_set` / `to_update_body()` to tell
    "absent" from "cleared". Unknown keys are kept, as the provider ignores
    them."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="allow",
    )

    models: Any = None
    default_reasoning_effort: Any = None
    web_search: Any = None
    toolsets: Any = None
    mcp_servers: Any = None
    knowledge: Any = None
    skills: Any = None
    is_service_account: Any = None
    share_with_org: Any = None

    def to_update_body(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_unset=True)


class CreatedAgent(BaseModel):
    """`agent` is the response document, including the created attachments.
    `handle` is the handle the agent was stored with."""

    agent_key: str
    handle: str | None = None
    agent: dict[str, Any]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
