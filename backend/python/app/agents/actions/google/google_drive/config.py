from dataclasses import dataclass

from app.agents.actions.google.auth.config import GoogleAuthConfig


@dataclass
class GoogleDriveConfig(GoogleAuthConfig):
    """Configuration for Google Drive"""
    pass
