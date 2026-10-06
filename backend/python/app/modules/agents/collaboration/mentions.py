from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SELF_AGENT_REF: Final = "agent:self"
MENTION_REF: Final = r"^(participant_[1-9][0-9]{0,2}|agent:self)$"

_MAX_MENTIONS: Final = 10


class MentionRef(BaseModel):
    """A mention as the AI backend sees it. Node maps stored `{type, id}` mentions to roster refs
    before sending, so no user, team or agent id ever reaches this service. The assistant and the
    chat's own agent are both `agent:self`."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["participant", "agent"]
    ref: str = Field(pattern=MENTION_REF)

    @model_validator(mode="after")
    def _type_matches_ref(self) -> "MentionRef":
        if (self.type == "agent") != (self.ref == SELF_AGENT_REF):
            raise ValueError("an agent mention must be agent:self and a participant mention a participant ref")
        return self


def _label(m: MentionRef, names: dict[str, str]) -> str:
    if m.type == "agent":
        return "you (the assistant)"
    name = names.get(m.ref)
    return f"{m.ref} ({name})" if name else m.ref


def render_mentions(mentions: list[MentionRef] | None, names: dict[str, str]) -> str | None:
    """Who a message mentions, as roster refs and sanitized names; None when it mentions nobody."""
    if not mentions:
        return None
    seen: set[str] = set()
    labels: list[str] = []
    for m in mentions[:_MAX_MENTIONS]:
        if m.ref not in seen:
            seen.add(m.ref)
            labels.append(_label(m, names))
    return ", ".join(labels)
