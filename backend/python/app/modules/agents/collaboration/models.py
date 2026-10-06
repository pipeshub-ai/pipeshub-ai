from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.agents.collaboration.mentions import MentionRef

PARTICIPANT_REF: Final = r"^participant_[1-9][0-9]{0,2}$"


class Participant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(pattern=PARTICIPANT_REF)
    displayName: str = Field(max_length=200)
    isCurrentSender: bool = False


class CollaborationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    participants: list[Participant] = Field(min_length=2, max_length=50)
    currentSenderRef: str = Field(pattern=PARTICIPANT_REF)

    @model_validator(mode="after")
    def _check_roster(self) -> "CollaborationContext":
        refs = [p.ref for p in self.participants]
        if len(set(refs)) != len(refs):
            raise ValueError("participant refs must be unique")
        current = [p.ref for p in self.participants if p.isCurrentSender]
        if len(current) != 1:
            raise ValueError("exactly one participant must be the current sender")
        if current[0] != self.currentSenderRef:
            raise ValueError("currentSenderRef must match the participant flagged isCurrentSender")
        return self


class PreviousConversationTurn(BaseModel):
    """Extra keys pass through; `content` is never coerced (solo parity)."""

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None
    authorRef: str | None = Field(default=None, pattern=PARTICIPANT_REF)
    mentions: list[MentionRef] | None = None


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    toolCallMessageId: str = Field(min_length=1, max_length=64)
