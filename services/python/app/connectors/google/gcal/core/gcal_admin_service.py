"""Google Calendar Admin Service module for interacting with Google Calendar API"""

# pylint: disable=E1101, W0718
from typing import Dict, List
from uuid import uuid4

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config.configuration_service import ConfigurationService
from app.config.utils.named_constants.arangodb_constants import CollectionNames
from app.connectors.google.scopes import GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
from app.connectors.utils.decorators import exponential_backoff
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter


class GCalAdminService:
    """GCalAdminService class for interacting with Google Calendar API"""

    def __init__(
        self, logger, config: ConfigurationService, rate_limiter: GoogleAPIRateLimiter
    ):
        self.logger = logger
        self.config_service = config
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter
        self.admin_service = None
        self.credentials = None

    async def connect_admin(self, org_id: str) -> bool:
        """Initialize admin service with domain-wide delegation"""
        try:
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES

            credentials_json = await self.google_token_handler.get_enterprise_token(
                org_id
            )
            admin_email = credentials_json.get("adminEmail")

            # Create credentials from JSON
            self.credentials = service_account.Credentials.from_service_account_info(
                credentials_json, scopes=SCOPES, subject=admin_email
            )

            self.admin_service = build(
                "admin",
                "directory_v1",
                credentials=self.credentials,
                cache_discovery=False,
            )

            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to connect to Calendar Admin Service: %s", str(e)
            )
            return False

    @exponential_backoff()
    async def list_enterprise_users(self, org_id: str) -> List[Dict]:
        """List all users in the domain for enterprise setup"""
        try:
            self.logger.info("🚀 Listing domain users")
            users = []
            page_token = None

            while True:
                async with self.google_limiter:
                    results = (
                        self.admin_service.users()
                        .list(
                            customer="my_customer",
                            orderBy="email",
                            projection="full",
                            pageToken=page_token,
                        )
                        .execute()
                    )

                    current_users = results.get(CollectionNames.USERS.value, [])

                    users.extend(
                        [
                            {
                                "_key": str(uuid4()),
                                "userId": str(uuid4()),
                                "orgId": org_id,
                                "email": user.get("primaryEmail"),
                                "fullName": user.get("name", {}).get("fullName"),
                                "firstName": user.get("name", {}).get("givenName", ""),
                                "middleName": user.get("name", {}).get(
                                    "middleName", ""
                                ),
                                "lastName": user.get("name", {}).get("familyName", ""),
                                "designation": user.get("designation", "user"),
                                "businessPhones": user.get("phones", []),
                                "isActive": user.get("isActive", False),
                                "createdAtTimestamp": user.get("creationTime"),
                                "updatedAtTimestamp": user.get("creationTime"),
                            }
                            for user in current_users
                            if not user.get("suspended", False)
                        ]
                    )

                    page_token = results.get("nextPageToken")
                    if not page_token:
                        break

            self.logger.info("✅ Found %s active users in domain", len(users))
            return users

        except Exception as e:
            self.logger.error("❌ Failed to list domain users: %s", str(e))
            return []
