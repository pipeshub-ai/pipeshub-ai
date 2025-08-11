from dataclasses import dataclass
from typing import Optional

from app.agents.actions.google.auth.config import GoogleAuthConfig


@dataclass
class GoogleCalendarConfig(GoogleAuthConfig):
    """Configuration for Google Calendar Event"""
    calendar_id: Optional[str] = None
