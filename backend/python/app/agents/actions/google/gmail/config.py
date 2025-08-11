from dataclasses import dataclass

from app.agents.actions.google.auth.config import GoogleAuthConfig


@dataclass
class GoogleGmailConfig(GoogleAuthConfig):
    """Configuration for Google Gmail"""
    pass
