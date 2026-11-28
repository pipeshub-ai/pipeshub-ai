"""Zoom API response models, shared by the connector and its permission reading."""

from typing import Optional

from pydantic import BaseModel, Field  # pyright: ignore[reportMissingImports]


class ZoomUser(BaseModel):
    """User object returned by GET /v2/users."""

    id: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    created_at: Optional[str] = None
    user_created_at: Optional[str] = None
    last_login_time: Optional[str] = None

    model_config = {"extra": "ignore"}


class ZoomMeetingReport(BaseModel):
    """Meeting object returned by GET /v2/report/users/{userId}/meetings."""

    uuid: str = ""
    id: Optional[int] = None
    topic: str = "Zoom Meeting"
    host_id: str = ""
    start_time: str = ""
    end_time: str = ""
    duration: Optional[int] = None
    type: Optional[int] = None

    model_config = {"extra": "ignore"}


class ZoomMeetingInvitee(BaseModel):
    """Invitee entry inside meeting settings."""

    email: str = ""

    model_config = {"extra": "ignore"}


class ZoomMeetingSettings(BaseModel):
    """Subset of meeting settings used by this connector."""

    alternative_hosts: str = ""
    meeting_invitees: list[ZoomMeetingInvitee] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ZoomMeetingDetail(BaseModel):
    """Full meeting object returned by GET /v2/meetings/{meetingId}."""

    id: Optional[int] = None
    join_url: str = ""
    settings: ZoomMeetingSettings = Field(default_factory=ZoomMeetingSettings)

    model_config = {"extra": "ignore"}


class ZoomParticipant(BaseModel):
    """Participant entry returned by GET /v2/report/meetings/{meetingId}/participants."""

    name: str = ""
    user_email: str = ""
    duration: int = 0
    join_time: str = ""
    leave_time: str = ""

    model_config = {"extra": "ignore"}


class ZoomRecordingDetail(BaseModel):
    """Top-level response from GET /v2/meetings/{meetingId}/recordings."""

    share_url: str = ""

    model_config = {"extra": "ignore"}
