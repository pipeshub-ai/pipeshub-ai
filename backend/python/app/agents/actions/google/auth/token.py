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
import logging
from typing import Callable, List, Optional

from fastapi import Depends
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.agents.actions.google.auth.config import GoogleAuthConfig
from app.agents.actions.google.gmail.config import GoogleGmailConfig
from app.agents.actions.google.google_calendar.config import GoogleCalendarConfig
from app.agents.actions.google.google_drive.config import GoogleDriveConfig
from app.config.configuration_service import ConfigurationService
from app.containers.connector import ConnectorAppContainer
from app.containers.query import QueryAppContainer
from app.modules.retrieval.retrieval_arango import ArangoService

logger = logging.getLogger(__name__)


class GoogleToken:
    """Google Token class for fetching tokens"""

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

    def __init__(
        self, config: GoogleAuthConfig,
        config_service: Optional[ConfigurationService] = Depends(QueryAppContainer.config_service),
        arango_service: Optional[ArangoService] = Depends(QueryAppContainer.arango_service)) -> None:
        """Initialize the Google Token"""
        """
        Args:
            config: Google authentication configuration
        Returns:
            None
        """
        logger.info("🚀 Initializing Google Token")
        self.config = config
        self.credentials: Optional[Credentials] = None
        self.service = None
        self.config_service = config_service
        self.arango_service = arango_service

    def get_token(self, service_name: str, version: str = "v3") -> Optional[Credentials]:
        """
        Authenticate with Google API and build service
        Args:
            service_name (str): Name of the Google service (gmail, calendar, drive)
            version (str): API version (default: v3)
        """
        #TODO:
        # fetch org and user id from arango or request


        # if individual, fetch token using get_individual_token

        # if enterprise, fetch token using get_enterprise_token

        # return credentials



def google_auth(
    service_name: str,
    version: str = "v3",
    scopes: Optional[List[str]] = None,
    config: Optional[GoogleAuthConfig] = None,
    config_service: Optional[ConfigurationService] = Depends(ConnectorAppContainer.config_service),
    arango_service: Optional[ArangoService] = Depends(ConnectorAppContainer.arango_service)
) -> Callable:
    """
    Decorator for Google API authentication
    Args:
        service_name (str): Google service name (gmail, calendar, drive)
        version (str): API version (default: v1)
        scopes (Optional[List[str]]): OAuth scopes (uses defaults if None)
        config (Optional[GoogleAuthConfig]): Authentication configuration
    """
    def decorator(func) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs) -> object:
            # Get or create authenticator
            if not hasattr(self, '_google_token'):
                # Use provided config or create default
                auth_config = config or GoogleAuthConfig()

                # Set default scopes if not provided
                if scopes is None:
                    auth_config.scopes = GoogleToken.DEFAULT_SCOPES.get(
                        service_name,
                        GoogleToken.DEFAULT_SCOPES.get(service_name, [])
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

                self._google_token = GoogleToken(auth_config, config_service, arango_service)

            try:
                # get token
                self.credentials = self._google_token.get_token(service_name, version)

                # Set service and credentials on the instance
                self.service = build(service_name, version, credentials=self.credentials)

                # Call the original function
                return func(self, *args, **kwargs)

            except HttpError as error:
                raise error
            except Exception as error:
                raise Exception(f"Authentication failed for {service_name}: {error}") from error

        return wrapper
    return decorator

def gmail_auth(
    scopes: Optional[List[str]] = None,
    config: Optional[GoogleGmailConfig] = None,
    config_service: Optional[ConfigurationService] = Depends(ConnectorAppContainer.config_service)) -> Callable:
    """Decorator specifically for Gmail authentication"""
    return google_auth("gmail", "v3", scopes, config)


def calendar_auth(
    scopes: Optional[List[str]] = None,
    config: Optional[GoogleCalendarConfig] = None,
    config_service: Optional[ConfigurationService] = Depends(ConnectorAppContainer.config_service),
    arango_service: Optional[ArangoService] = Depends(ConnectorAppContainer.arango_service)) -> Callable:
    """Decorator specifically for Google Calendar authentication"""
    return google_auth("calendar", "v3", scopes, config, config_service, arango_service)


def drive_auth(
    scopes: Optional[List[str]] = None,
    config: Optional[GoogleDriveConfig] = None,
    config_service: Optional[ConfigurationService] = Depends(ConnectorAppContainer.config_service),
    arango_service: Optional[ArangoService] = Depends(ConnectorAppContainer.arango_service)) -> Callable:
    """Decorator specifically for Google Drive authentication"""
    return google_auth("drive", "v3", scopes, config, config_service, arango_service)
