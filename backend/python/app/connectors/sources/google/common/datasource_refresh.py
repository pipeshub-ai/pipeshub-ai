"""
Common utilities for refreshing Google datasource OAuth credentials.

This module provides functionality to ensure Google datasources (Drive, Gmail, etc.)
always have fresh OAuth credentials by fetching them from etcd and updating the
Google client when credentials change.
"""

from datetime import datetime, timezone
from logging import Logger

from google.oauth2.credentials import Credentials
from typing_extensions import Union

from app.config.configuration_service import ConfigurationService
from app.connectors.sources.google.common.connector_google_exceptions import (
    GoogleAuthError,
)
from app.sources.client.google.google import GoogleClient
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource


async def refresh_google_datasource_credentials(
    google_client: GoogleClient,
    data_source: Union[GoogleDriveDataSource, GoogleGmailDataSource],
    config_service: ConfigurationService,
    connector_id: str,
    logger: Logger,
    service_name: str = "Google"
) -> None:
    """
    Ensure datasource has ALWAYS-FRESH OAuth credentials.

    Creates a new Credentials object when credentials change.
    After calling this, use the data_source directly.

    The datasource wraps a Google client by reference, so replacing
    the client's credentials automatically updates the datasource.

    Args:
        google_client: The Google client wrapper instance
        data_source: The data source instance (GoogleDriveDataSource, GoogleGmailDataSource, etc.)
        config_service: Configuration service for fetching credentials from etcd
        connector_id: The connector ID for config lookup
        logger: Logger instance for logging credential updates
        service_name: Service name for error messages ("Drive", "Gmail", etc.)

    Raises:
        GoogleAuthError: If config not found or no OAuth credentials available
    """

    # Fetch current credentials from etcd (source of truth)
    config = await config_service.get_config(
        f"/services/connectors/{connector_id}/config"
    )

    if not config:
        raise GoogleAuthError(f"Google {service_name} configuration not found")

    credentials_config = config.get("credentials", {}) or {}
    fresh_access_token = credentials_config.get("access_token", "")
    fresh_refresh_token = credentials_config.get("refresh_token", "")


    if not fresh_access_token and not fresh_refresh_token:
        raise GoogleAuthError("No OAuth credentials available")

    # Get current credentials from the Google client
    current_client = google_client.get_client()

    if hasattr(current_client, '_http') and hasattr(current_client._http, 'credentials'):
        current_credentials = current_client._http.credentials
        current_token = getattr(current_credentials, 'token', None)
        current_refresh_token = getattr(current_credentials, 'refresh_token', None)

        # Detect if credentials changed (especially after re-authentication)
        credentials_changed = False

        # Refresh token change is critical - indicates re-authentication
        if current_refresh_token != fresh_refresh_token:
            logger.info("🔄 Refresh token changed - user re-authenticated, updating credentials")
            credentials_changed = True

        # Access token change might indicate external token refresh
        elif current_token != fresh_access_token:
            logger.debug("🔄 Access token changed, updating credentials")
            credentials_changed = True

        logger.info(f"\n\n\nCredentials changed: {credentials_changed}")
        logger.info(f"Current token: {current_token}")
        logger.info(f"Fresh access token: {fresh_access_token}")
        logger.info(f"Current refresh token: {current_refresh_token}")
        logger.info(f"Fresh refresh token: {fresh_refresh_token}")

        # Create new Credentials object if changed
        if credentials_changed:
            logger.debug("🔨 Creating new Google Credentials object with fresh tokens")

            # Get scopes and client info from current credentials
            scopes = getattr(current_credentials, 'scopes', None)
            client_id = getattr(current_credentials, 'client_id', None)
            client_secret = getattr(current_credentials, 'client_secret', None)

            # Create new credentials object (read-only properties require new object)
            new_credentials = Credentials(
                token=fresh_access_token,
                refresh_token=fresh_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=scopes,
            )

            # Update expiry if available
            token_expiry_ms = credentials_config.get("access_token_expiry_time")
            if token_expiry_ms:
                token_expiry = datetime.fromtimestamp(
                    token_expiry_ms / 1000, timezone.utc
                ).replace(tzinfo=None)
                new_credentials.expiry = token_expiry

            # Replace the credentials object in the client
            current_client._http.credentials = new_credentials
            logger.info("✅ Credentials updated successfully")
