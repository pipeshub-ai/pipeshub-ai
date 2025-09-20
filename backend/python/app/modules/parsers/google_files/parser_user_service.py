"""ParserUserService module for parsing Google Workspace files using user credentials"""

from datetime import datetime, timedelta, timezone

import google.oauth2.credentials
from googleapiclient.discovery import build

from app.config.configuration_service import ConfigurationService
from app.connectors.sources.google.common.google_token_handler import CredentialKeys
from app.connectors.sources.google.common.scopes import GOOGLE_PARSER_SCOPES
from app.connectors.sources.google.gmail.gmail_user_service import (
    GoogleAuthError,
    GoogleMailError,
    MailOperationError,
)
from app.connectors.utils.decorators import token_refresh
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter


class ParserUserService:
    """ParserUserService class for parsing Google Workspace files using user credentials"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        rate_limiter: GoogleAPIRateLimiter,
        google_token_handler,
        credentials=None,
    ) -> None:
        try:
            self.logger = logger
            self.config_service = config_service
            self.rate_limiter = rate_limiter
            self.google_token_handler = google_token_handler
            self.google_limiter = self.rate_limiter.google_limiter
            self.credentials = credentials

            # Services for different Google Workspace apps
            self.docs_service = None
            self.sheets_service = None
            self.slides_service = None

            self.token_expiry = None
            self.org_id = None
            self.user_id = None
            self.is_delegated = credentials is not None

        except Exception as e:
            raise GoogleMailError(
                "Failed to initialize ParserUserService: " + str(e),
                details={"error": str(e)},
            )

    @token_refresh
    async def connect_individual_user(self, org_id: str, user_id: str, app_name: str) -> bool:
        """Connect using Oauth2 credentials for individual user"""
        try:
            self.org_id = org_id
            self.user_id = user_id

            try:
                creds_data = await self.google_token_handler.get_individual_token(
                    org_id, user_id, app_name=app_name
                )
                if not creds_data:
                    raise GoogleAuthError(
                        "Failed to get individual token",
                        details={"org_id": org_id, "user_id": user_id},
                    )
            except Exception as e:
                raise GoogleAuthError(
                    "Error getting individual token: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            try:
                # Create credentials object from the response
                creds = google.oauth2.credentials.Credentials(
                    token=creds_data.get(CredentialKeys.ACCESS_TOKEN.value),
                    refresh_token=creds_data.get(CredentialKeys.REFRESH_TOKEN.value),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=creds_data.get(CredentialKeys.CLIENT_ID.value),
                    client_secret=creds_data.get(CredentialKeys.CLIENT_SECRET.value),
                    scopes=GOOGLE_PARSER_SCOPES,
                )
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to create credentials object: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

                        # Update token expiry time using created_at + expires_in if possible
            try:
                expires_in = creds_data.get("expires_in")
                created_at_str = creds_data.get("created_at")
                if expires_in and created_at_str:
                    created_at = datetime.fromisoformat(created_at_str)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    self.token_expiry = created_at + timedelta(seconds=int(expires_in))
                else:
                    expiry_ms = creds_data.get("access_token_expiry_time")
                    if expiry_ms:
                        self.token_expiry = datetime.fromtimestamp(
                            int(expiry_ms) / 1000, tz=timezone.utc
                        )
                    else:
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            except Exception as e:
                self.logger.warning("Failed to set refreshed token expiry: %s", str(e))
                raise GoogleAuthError(
                    "Failed to set token expiry: " + str(e),
                    details={
                        "org_id": org_id,
                        "user_id": user_id,
                        "error": str(e),
                        "expiry_time": creds_data.get("expires_in"),
                    },
                )


            self.logger.info("✅ Token expiry time: %s", self.token_expiry)


            try:
                self.docs_service = build("docs", "v1", credentials=creds)
                self.sheets_service = build("sheets", "v4", credentials=creds)
                self.slides_service = build("slides", "v1", credentials=creds)

            except Exception as e:
                raise MailOperationError(
                    "Failed to build ParserUserService: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            self.logger.info("✅ ParserUserService connected successfully")
            return True

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error connecting individual user: " + str(e),
                details={"org_id": org_id, "user_id": user_id, "error": str(e)},
            )

    async def _check_and_refresh_token(self, app_name: str) -> None:
        """Check token expiry and refresh if needed"""
        self.logger.info("Checking token expiry and refreshing if needed")

        if not self.token_expiry:
            # self.logger.warning("⚠️ Token expiry time not set.")
            return

        if not self.org_id or not self.user_id:
            self.logger.warning("⚠️ Org ID or User ID not set yet.")
            return

        now = datetime.now(timezone.utc)
        time_until_refresh = self.token_expiry - now - timedelta(minutes=20)
        self.logger.info(
            f"Time until refresh: {time_until_refresh.total_seconds()} seconds"
        )

        if time_until_refresh.total_seconds() <= 0:
            # Parser uses Docs/Sheets/Slides; use Drive connector tokens for content access
            await self.google_token_handler.refresh_token(self.org_id, self.user_id, app_name=app_name)

            creds_data = await self.google_token_handler.get_individual_token(
                self.org_id, self.user_id, app_name=app_name
            )

            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get(CredentialKeys.ACCESS_TOKEN.value),
                refresh_token=creds_data.get(CredentialKeys.REFRESH_TOKEN.value),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get(CredentialKeys.CLIENT_ID.value),
                client_secret=creds_data.get(CredentialKeys.CLIENT_SECRET.value),
                scopes=GOOGLE_PARSER_SCOPES,
            )

            self.docs_service = build("docs", "v1", credentials=creds)
            self.sheets_service = build("sheets", "v4", credentials=creds)
            self.slides_service = build("slides", "v1", credentials=creds)

                        # Update token expiry time using created_at + expires_in if possible
            try:
                expires_in = creds_data.get("expires_in")
                created_at_str = creds_data.get("created_at")
                if expires_in and created_at_str:
                    created_at = datetime.fromisoformat(created_at_str)
                    if created_at.tzinfo is None:
                        created_at = created_at.replace(tzinfo=timezone.utc)
                    self.token_expiry = created_at + timedelta(seconds=int(expires_in))
                else:
                    expiry_ms = creds_data.get("access_token_expiry_time")
                    if expiry_ms:
                        self.token_expiry = datetime.fromtimestamp(
                            int(expiry_ms) / 1000, tz=timezone.utc
                        )
                    else:
                        self.token_expiry = datetime.now(timezone.utc) + timedelta(hours=1)
            except Exception as e:
                self.logger.warning("Failed to set refreshed token expiry: %s", str(e))
                raise GoogleAuthError(
                    "Failed to set token expiry: " + str(e),
                    details={
                        "org_id": self.org_id,
                        "user_id": self.user_id,
                        "error": str(e),
                        "expiry_time": creds_data.get("expires_in"),
                    },
                )


            self.logger.info("✅ Token refreshed, new expiry: %s", self.token_expiry)

    async def connect_enterprise_user(self, org_id, user_id) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            if not self.credentials:
                raise GoogleAuthError(
                    "No credentials provided for enterprise connection."
                )
            self.org_id = org_id
            self.user_id = user_id
            try:
                # Initialize services
                self.docs_service = build("docs", "v1", credentials=self.credentials)
                self.sheets_service = build(
                    "sheets", "v4", credentials=self.credentials
                )
                self.slides_service = build(
                    "slides", "v1", credentials=self.credentials
                )

            except Exception as e:
                raise MailOperationError(
                    "Failed to build Parser Service: " + str(e),
                    details={"error": str(e)},
                )

            self.logger.info("✅ Parser Service connected successfully")
            return True

        except (GoogleAuthError, MailOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error connecting enterprise user: " + str(e),
                details={"error": str(e)},
            )

    async def disconnect(self) -> bool | None:
        """Disconnect and cleanup services"""
        try:
            self.logger.info("🔄 Disconnecting parser services")

            # Close the service connections if they exist
            if self.docs_service:
                self.docs_service.close()
                self.docs_service = None
            if self.sheets_service:
                self.sheets_service.close()
                self.sheets_service = None
            if self.slides_service:
                self.slides_service.close()
                self.slides_service = None

            # Clear credentials
            self.credentials = None

            self.logger.info("✅ Parser services disconnected successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect parser services: {str(e)}")
            return False
