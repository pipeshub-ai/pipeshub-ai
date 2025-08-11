"""
Google Authentication Decorator for Gmail, Google Calendar, and Google Drive Tools

This module provides a unified authentication decorator that can be used across
different Google API tools to handle OAuth2 authentication flow.

Required Environment Variables:
-----------------------------
- GOOGLE_CLIENT_ID: Google OAuth client ID
- GOOGLE_CLIENT_SECRET: Google OAuth client secret
- GOOGLE_PROJECT_ID: Google Cloud project ID
- GOOGLE_REDIRECT_URI: Google OAuth redirect URI (default: http://localhost)

How to Get These Credentials:
---------------------------
1. Go to Google Cloud Console (https://console.cloud.google.com)
2. Create a new project or select an existing one
3. Enable the required APIs (Gmail, Calendar, Drive)
4. Create OAuth 2.0 credentials and set environment variables
"""
import functools
from os import getenv
from pathlib import Path
from typing import List, Optional

from google.auth.external_account_authorized_user import (
    Credentials as ExternalAccountCredential,
)
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials as OAuth2Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.agents.actions.google.auth.config import GoogleAuthConfig
from app.agents.actions.google.gmail.config import GoogleGmailConfig
from app.agents.actions.google.google_calendar.config import GoogleCalendarConfig
from app.agents.actions.google.google_drive.config import GoogleDriveConfig


class GoogleAuthenticator:
    """Google Authenticator class for handling authentication"""

    ADMIN_SCOPES = [
        "https://www.googleapis.com/auth/admin.directory.user.readonly",
        "https://www.googleapis.com/auth/admin.directory.group.readonly",
        "https://www.googleapis.com/auth/admin.directory.domain.readonly",
        "https://www.googleapis.com/auth/admin.reports.audit.readonly",
        "https://www.googleapis.com/auth/admin.directory.orgunit",
    ]

    # Default scopes for different Google services
    DEFAULT_SCOPES = {
        "gmail": [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.compose",
        ],
        "calendar": [
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/calendar.readonly",
        ],
        "drive": [
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.readonly",

        ],
    }

    def __init__(self, config: GoogleAuthConfig):
        self.config = config
        self.credentials: Optional[OAuth2Credentials | ExternalAccountCredential] = None
        self.service = None

    def authenticate(self, service_name: str, version: str = "v3") -> None:
        """
        Authenticate with Google API and build service
        Args:
            service_name (str): Name of the Google service (gmail, calendar, drive)
            version (str): API version (default: v3)
        """
        if not self.credentials or not self.credentials.valid:
            self._perform_auth()

        if not self.service:
            self.service = build(service_name, version, credentials=self.credentials)

    def _perform_auth(self) -> None:
        """Perform OAuth2 authentication flow"""
        token_file = Path(self.config.token_file_path or "token.json")
        credentials_file = Path(self.config.credentials_file_path or "credentials.json")

        # Load existing credentials from token file
        if token_file.exists():
            self.credentials = OAuth2Credentials.from_authorized_user_file(
                str(token_file),
                self.config.scopes
            )

        # If credentials are invalid, refresh or perform new authentication
        if not self.credentials or not self.credentials.valid:
            if self.credentials and self.credentials.expired and self.credentials.refresh_token:
                # Refresh expired credentials
                self.credentials.refresh(Request())
            else:
                # Perform new authentication
                self._new_authentication(credentials_file)

        # Save credentials for future use
        if self.credentials and self.credentials.valid:
            token_file.write_text(self.credentials.to_json())

    def _new_authentication(self, credentials_file: Path) -> None:
        """Perform new OAuth2 authentication"""
        # Client configuration from environment variables
        client_config = {
            "installed": {
                "client_id": getenv("GOOGLE_CLIENT_ID"),
                "client_secret": getenv("GOOGLE_CLIENT_SECRET"),
                "project_id": getenv("GOOGLE_PROJECT_ID"),
                "auth_uri":"https://accounts.google.com/o/oauth2/auth",
                "token_uri":"https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url":"https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris":["http://localhost:8080/","http://localhost:8090/"]
            }
        }

        # Create OAuth flow from file or config
        if credentials_file.exists():
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_file),
                self.config.scopes
            )
        else:
            flow = InstalledAppFlow.from_client_config(
                client_config,
                self.config.scopes
            )

        # Run OAuth flow
        self.credentials = flow.run_local_server(port=self.config.oauth_port or 8080)


def google_auth(
    service_name: str,
    version: str = "v3",
    scopes: Optional[List[str]] = None,
    config: Optional[GoogleAuthConfig] = None
):
    """
    Decorator for Google API authentication
    Args:
        service_name (str): Google service name (gmail, calendar, drive)
        version (str): API version (default: v1)
        scopes (Optional[List[str]]): OAuth scopes (uses defaults if None)
        config (Optional[GoogleAuthConfig]): Authentication configuration
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # Get or create authenticator
            if not hasattr(self, '_google_authenticator'):
                # Use provided config or create default
                auth_config = config or GoogleAuthConfig()

                # Set default scopes if not provided
                if scopes is None:
                    auth_config.scopes = GoogleAuthenticator.DEFAULT_SCOPES.get(
                        service_name,
                        GoogleAuthenticator.DEFAULT_SCOPES.get(service_name)
                    )
                else:
                    auth_config.scopes = scopes

                # Override config with instance attributes if they exist
                if hasattr(self, 'credentials_path') and self.credentials_path:
                    auth_config.credentials_file_path = self.credentials_path
                if hasattr(self, 'token_path') and self.token_path:
                    auth_config.token_file_path = self.token_path
                if hasattr(self, 'oauth_port') and self.oauth_port:
                    auth_config.oauth_port = self.oauth_port
                if hasattr(self, 'scopes') and self.scopes:
                    auth_config.scopes = self.scopes

                self._google_authenticator = GoogleAuthenticator(auth_config)

            try:
                # Perform authentication
                self._google_authenticator.authenticate(service_name, version)

                # Set service and credentials on the instance
                self.service = self._google_authenticator.service
                self.credentials = self._google_authenticator.credentials

                # Call the original function
                return func(self, *args, **kwargs)

            except HttpError as error:
                raise error
            except Exception as error:
                raise Exception(f"Authentication failed for {service_name}: {error}")

        return wrapper
    return decorator

def gmail_auth(scopes: Optional[List[str]] = None, config: Optional[GoogleGmailConfig] = None):
    """Decorator specifically for Gmail authentication"""
    return google_auth("gmail", "v3", scopes, config)


def calendar_auth(scopes: Optional[List[str]] = None, config: Optional[GoogleCalendarConfig] = None):
    """Decorator specifically for Google Calendar authentication"""
    return google_auth("calendar", "v3", scopes, config)


def drive_auth(scopes: Optional[List[str]] = None, config: Optional[GoogleDriveConfig] = None):
    """Decorator specifically for Google Drive authentication"""
    return google_auth("drive", "v3", scopes, config)
