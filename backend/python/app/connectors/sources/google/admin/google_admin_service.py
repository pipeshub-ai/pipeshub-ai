from typing import Dict, List, Optional
from uuid import uuid4

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import (
    HttpStatusCode,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.sources.google.common.connector_google_exceptions import (
    AdminAuthError,
    AdminDelegationError,
    AdminListError,
    AdminQuotaError,
    AdminServiceError,
    BatchOperationError,
    GoogleMailError,
    UserOperationError,
)
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
    GOOGLE_PARSER_SCOPES,
)
from app.connectors.sources.google.gmail.gmail_user_service import GmailUserService
from app.connectors.sources.google.google_drive.drive_user_service import (
    DriveUserService,
)
from app.connectors.utils.decorators import exponential_backoff
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.modules.parsers.google_files.parser_user_service import ParserUserService
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


class GoogleAdminService:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        rate_limiter: GoogleAPIRateLimiter,
        google_token_handler,
        arango_service,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter
        self.google_token_handler = google_token_handler
        self.arango_service = arango_service
        self.admin_reports_service = None
        self.admin_directory_service = None
        self.credentials = None

    async def connect_admin(self, org_id: str, app_name: str = "DRIVE") -> bool:
        """Initialize admin service with domain-wide delegation"""
        try:
            SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES + GOOGLE_PARSER_SCOPES

            try:
                credentials_json = await self.google_token_handler.get_enterprise_token(
                    org_id, app_name
                )

                self.logger.info(f"🔍 Retrieved credentials for org {org_id}, app {app_name}")
                self.logger.info(f"🔍 Credentials keys: {list(credentials_json.keys())}")
                self.logger.info(f"🔍 Admin email: {credentials_json.get('adminEmail')}")
                self.logger.info(f"🔍 Client email: {credentials_json.get('client_email')}")

                admin_email = credentials_json.get("adminEmail")
                if not credentials_json:
                    raise AdminAuthError(
                        "Failed to get enterprise credentials",
                        details={"org_id": org_id},
                    )

                admin_email = credentials_json.get("adminEmail")
                if not admin_email:
                    raise AdminAuthError(
                        "Admin email not found in credentials",
                        details={"org_id": org_id},
                    )
            except AdminAuthError:
                raise
            except Exception as e:
                raise AdminAuthError(
                    "Error getting enterprise token: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

            try:
                self.credentials = (
                    service_account.Credentials.from_service_account_info(
                        credentials_json, scopes=SCOPES, subject=admin_email
                    )
                )
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials: " + str(e),
                    details={
                        "org_id": org_id,
                        "admin_email": admin_email,
                        "error": str(e),
                    },
                )

            try:
                self.admin_reports_service = build(
                    "admin",
                    "reports_v1",
                    credentials=self.credentials,
                    cache_discovery=False,
                )
                self.admin_directory_service = build(
                    "admin",
                    "directory_v1",
                    credentials=self.credentials,
                    cache_discovery=False,
                )
            except Exception as e:
                raise AdminServiceError(
                    "Failed to build admin service: " + str(e),
                    details={"org_id": org_id, "error": str(e)},
                )

            return True

        except (AdminAuthError, AdminDelegationError, AdminServiceError):
            raise
        except Exception as e:
            raise AdminServiceError(
                "Failed to connect to Admin Service: " + str(e),
                details={"error": str(e)},
            )

    @exponential_backoff()
    async def list_enterprise_users(self, org_id: str) -> List[Dict]:
        """List all users in the domain for enterprise setup"""
        try:
            self.logger.info("🚀 Listing domain users")
            users = []
            page_token = None
            failed_items = []

            while True:
                try:
                    async with self.google_limiter:
                        results = (
                            self.admin_directory_service.users()
                            .list(
                                customer="my_customer",
                                orderBy="email",
                                projection="full",
                                pageToken=page_token,
                            )
                            .execute()
                        )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise AdminAuthError(
                            "Permission denied listing users: " + str(e),
                            details={"org_id": org_id, "error": str(e)},
                        )
                    elif e.resp.status == HttpStatusCode.TOO_MANY_REQUESTS.value:
                        raise AdminQuotaError(
                            "Rate limit exceeded listing users: " + str(e),
                            details={"org_id": org_id, "error": str(e)},
                        )
                    raise AdminListError(
                        "Failed to list users: " + str(e),
                        details={"org_id": org_id, "error": str(e)},
                    )

                current_users = results.get("users", [])

                for user in current_users:
                    if not user.get("suspended", False):
                        try:
                            users.append(
                                {
                                    "_key": str(uuid4()),
                                    "userId": str(uuid4()),
                                    "orgId": org_id,
                                    "email": user.get("primaryEmail"),
                                    "fullName": user.get("name", {}).get("fullName"),
                                    "firstName": user.get("name", {}).get(
                                        "givenName", ""
                                    ),
                                    "middleName": user.get("name", {}).get(
                                        "middleName", ""
                                    ),
                                    "lastName": user.get("name", {}).get(
                                        "familyName", ""
                                    ),
                                    "designation": user.get("designation", "user"),
                                    "businessPhones": user.get("phones", []),
                                    "isActive": user.get("isActive", False),
                                    "createdAtTimestamp": int(
                                        parse_timestamp(
                                            user.get("creationTime")
                                        )
                                    ),
                                    "updatedAtTimestamp": int(
                                        parse_timestamp(
                                            user.get("creationTime")
                                        )
                                    ),
                                }
                            )
                        except Exception as e:
                            failed_items.append(
                                {"email": user.get("primaryEmail"), "error": str(e)}
                            )

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            if failed_items:
                raise BatchOperationError(
                    f"Failed to process {len(failed_items)} users",
                    failed_items=failed_items,
                    details={"org_id": org_id, "total_users": len(current_users)},
                )

            self.logger.info("✅ Found %s active users in domain", len(users))
            return users

        except (AdminAuthError, AdminQuotaError, AdminListError, BatchOperationError):
            raise
        except Exception as e:
            raise AdminServiceError(
                "Unexpected error listing users: " + str(e),
                details={"org_id": org_id, "error": str(e)},
            )

    @exponential_backoff()
    async def list_groups(self, org_id: str) -> Optional[List[Dict]]:
        """List all groups in the domain for enterprise setup"""
        try:
            self.logger.info("🚀 Listing domain groups")
            groups = []
            page_token = None

            while True:
                try:
                    async with self.google_limiter:
                        results = (
                            self.admin_directory_service.groups()
                            .list(customer="my_customer", pageToken=page_token)
                            .execute()
                        )
                except Exception as e:
                    if "quota" in str(e).lower():
                        raise AdminQuotaError(
                            "API quota exceeded while listing groups: " + str(e),
                            details={"error": str(e)},
                        )
                    raise AdminListError(
                        "Failed to list groups" + str(e), details={"error": str(e)}
                    )

                current_groups = results.get("groups", [])
                if current_groups is None:
                    raise AdminListError(
                        "Invalid response format from Admin API",
                        details={"results": results},
                    )

                groups.extend(
                    [
                        {
                            "_key": group.get("id"),
                            "groupId": group.get("id"),
                            "orgId": org_id,
                            "groupName": group.get("name"),
                            "email": group.get("email"),
                            "description": group.get("description", ""),
                            "adminCreated": group.get("adminCreated", False),
                            "createdAt": group.get("creationTime"),
                        }
                        for group in current_groups
                    ]
                )

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            self.logger.info("✅ Found %s groups", len(groups))
            return groups

        except (AdminQuotaError, AdminListError):
            raise
        except Exception as e:
            raise AdminServiceError(
                "Unexpected error while listing groups: " + str(e),
                details={"org_id": org_id, "error": str(e)},
            )

    @exponential_backoff()
    async def list_domains(self) -> List[Dict]:
        """List all domains for the enterprise"""
        try:
            self.logger.info("🚀 Listing domains")
            domains = []
            page_token = None

            while True:
                async with self.google_limiter:
                    results = (
                        self.admin_directory_service.domains()
                        .list(
                            customer="my_customer",
                        )
                        .execute()
                    )

                    current_domains = results.get("domains", [])

                    domains.extend(
                        [
                            {
                                "_key": f"gdr_domain_{domain.get('domainName')}",
                                "domainName": domain.get("domainName"),
                                "verified": domain.get("verified", False),
                                "isPrimary": domain.get("isPrimary", False),
                                "createdAt": domain.get("creationTime"),
                            }
                            for domain in current_domains
                        ]
                    )

                    page_token = results.get("nextPageToken")
                    if not page_token:
                        break

            self.logger.info("✅ Found %s domains", len(domains))
            return domains

        except Exception as e:
            self.logger.error("❌ Failed to list domains: %s", str(e))
            return []

    @exponential_backoff()
    async def list_group_members(self, group_email: str) -> List[Dict]:
        """List all members of a specific group"""
        try:
            self.logger.info(f"🚀 Listing members for group: {group_email}")
            members = []
            page_token = None

            while True:
                try:
                    async with self.google_limiter:
                        results = (
                            self.admin_directory_service.members()
                            .list(groupKey=group_email, pageToken=page_token)
                            .execute()
                        )
                except Exception as e:
                    if "quota" in str(e).lower():
                        raise AdminQuotaError(
                            "API quota exceeded while listing group members: " + str(e),
                            details={"group_email": group_email, "error": str(e)},
                        )
                    raise AdminListError(
                        "Failed to list group members: " + str(e),
                        details={"group_email": group_email, "error": str(e)},
                    )

                current_members = results.get("members", [])
                if current_members is None:
                    raise AdminListError(
                        "Invalid response format from Admin API",
                        details={"group_email": group_email, "results": results},
                    )

                members.extend(
                    [
                        {
                            "email": member.get("email"),
                            "role": member.get("role", "member").lower(),
                            "type": member.get("type"),
                            "status": member.get("status", "active"),
                        }
                        for member in current_members
                    ]
                )

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

            self.logger.info(f"✅ Found {len(members)} members in group {group_email}")
            return members

        except (AdminQuotaError, AdminListError):
            raise
        except Exception as e:
            raise AdminServiceError(
                "Unexpected error while listing group members: " + str(e),
                details={"group_email": group_email, "error": str(e)},
            )

    async def handle_new_user(self, org_id: str, user_email: str) -> None:
        """Handle new user creation event"""
        try:
            self.logger.info(f"Handling new user creation for {user_email}")

            current_timestamp = get_epoch_timestamp_in_ms()

            # Get user info from Google Admin API
            user_info = await self.get_user_info(org_id, user_email)
            if not user_info:
                self.logger.error(f"Failed to get user info for {user_email}")
                return

            user_key = await self.arango_service.get_entity_id_by_email(user_email)
            if not user_key:
                # Create user in Arango with isActive=False
                await self.arango_service.batch_upsert_nodes(
                    [user_info], CollectionNames.USERS.value
                )

                # Create edge between org and user if it doesn't exist
                edge_data = {
                    "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                    "_from": f"{CollectionNames.USERS.value}/{user_info['_key']}",
                    "entityType": "ORGANIZATION",
                    "createdAtTimestamp": current_timestamp,
                }
                await self.arango_service.batch_create_edges(
                    [edge_data],
                    CollectionNames.BELONGS_TO.value,
                )

            self.logger.info(f"Successfully created user record for {user_email}")

        except Exception as e:
            self.logger.error(
                f"Error handling new user creation for {user_email}: {str(e)}"
            )
            raise

    async def handle_deleted_user(self, org_id: str, user_email: str) -> None:
        """Handle user deletion event"""
        try:
            self.logger.info(f"Handling user deletion for {user_email}")

            user_key = await self.arango_service.get_entity_id_by_email(user_email)
            if not user_key:
                self.logger.warning(f"User {user_email} not found in ArangoDB")
                return

            user = await self.arango_service.get_document(
                user_key, CollectionNames.USERS.value
            )
            if not user:
                self.logger.warning(f"User {user_email} not found in ArangoDB")
                return

            user["isActive"] = False
            await self.arango_service.batch_upsert_nodes(
                [user], CollectionNames.USERS.value
            )

            self.logger.info(f"Successfully marked user {user_email} as inactive")

        except Exception as e:
            self.logger.error(
                f"Error handling user deletion for {user_email}: {str(e)}"
            )
            raise

    async def handle_new_group(self, org_id: str, group_email: str) -> None:
        """Handle new group creation event"""
        try:
            self.logger.info(f"Handling new group creation for {group_email}")

            # Get group info from Google Admin API
            group_info = await self.get_group_info(org_id, group_email)
            if not group_info:
                self.logger.error(f"Failed to get group info for {group_email}")
                return

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            if not group_key:
                # Create group in Arango
                await self.arango_service.batch_upsert_nodes(
                    [group_info], CollectionNames.GROUPS.value
                )

            self.logger.info(f"Successfully created group record for {group_email}")

        except Exception as e:
            self.logger.error(
                f"Error handling new group creation for {group_email}: {str(e)}"
            )
            raise

    async def handle_deleted_group(self, org_id: str, group_email: str) -> None:
        """Handle group deletion event"""
        try:
            self.logger.info(f"Handling group deletion for {group_email}")

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            if not group_key:
                self.logger.warning(f"Group {group_email} not found in ArangoDB")
                return

            # AQL query to delete group and all its associated edges
            query = f"""
            LET group = DOCUMENT({CollectionNames.GROUPS.value}, '{group_key}')

            // Delete the group document and return the deleted document
            LET deleted_group = (
                FOR doc IN {CollectionNames.GROUPS.value}
                FILTER doc._key == '{group_key}'
                REMOVE doc IN {CollectionNames.GROUPS.value}
                RETURN OLD
            )

            RETURN {{
                deleted_group: deleted_group[0]
            }}
            """

            cursor = self.arango_service.db.aql.execute(query)
            cursor.next()
            self.logger.info(
                f"Successfully deleted group {group_email} and its associated edges"
            )

        except Exception as e:
            self.logger.error(
                f"Error handling group deletion for {group_email}: {str(e)}"
            )
            raise

    async def handle_group_member_added(
        self, org_id: str, group_email: str, user_email: str
    ) -> None:
        """Handle group member addition event"""
        try:
            self.logger.info(
                f"Handling member addition to group {group_email}: {user_email}"
            )

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            user_key = await self.arango_service.get_entity_id_by_email(user_email)

            if not group_key or not user_key:
                self.logger.error(
                    f"Group {group_email} or user {user_email} not found in ArangoDB"
                )
                return

            current_timestamp = get_epoch_timestamp_in_ms()

            # Create BELONGS_TO edge
            belongs_to_data = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.GROUPS.value}/{group_key}",
                "entityType": "GROUP",
                "createdAtTimestamp": current_timestamp,
            }

            # Create both edges
            await self.arango_service.batch_create_edges(
                [belongs_to_data], CollectionNames.BELONGS_TO.value
            )

            self.logger.info(f"Successfully added {user_email} to group {group_email}")

        except Exception as e:
            self.logger.error(f"Error handling group member addition: {str(e)}")
            raise

    async def handle_group_member_removed(
        self, org_id: str, group_email: str, user_email: str
    ) -> None:
        """Handle group member removal event"""
        try:
            self.logger.info(
                f"Handling member removal from group {group_email}: {user_email}"
            )

            group_key = await self.arango_service.get_entity_id_by_email(group_email)
            user_key = await self.arango_service.get_entity_id_by_email(user_email)

            if not group_key or not user_key:
                self.logger.error(
                    f"Group {group_email} or user {user_email} not found in ArangoDB"
                )
                return

            # Query to delete both membership and belongs_to edges
            query = f"""
            LET user_id = CONCAT({CollectionNames.USERS.value}, '/', '{user_key}')
            LET group_id = CONCAT({CollectionNames.GROUPS.value}, '/', '{group_key}')

            // Delete belongs_to edge
            LET deleted_belongs = (
                FOR edge IN {CollectionNames.BELONGS_TO.value}
                FILTER edge._from == user_id
                AND edge._to == group_id
                AND edge.entityType == 'GROUP'
                REMOVE edge IN {CollectionNames.BELONGS_TO.value}
                RETURN OLD
            )

            RETURN {{
                deleted_belongs: deleted_belongs[0]
            }}
            """

            cursor = self.arango_service.db.aql.execute(query)
            cursor.next()
            self.logger.info(
                f"Successfully removed {user_email} from group {group_email}"
            )

        except Exception as e:
            self.logger.error(f"Error handling group member removal: {str(e)}")
            raise

    @exponential_backoff()
    async def get_user_info(self, org_id: str, user_email: str) -> Optional[Dict]:
        """Get user information from Google Admin API"""
        try:
            if not await self.connect_admin(org_id):
                raise AdminServiceError(
                    "Failed to connect admin service for user info retrieval",
                    details={"org_id": org_id},
                )

            async with self.google_limiter:
                user_info = (
                    self.admin_directory_service.users()
                    .get(userKey=user_email)
                    .execute()
                )

                return {
                    "_key": str(uuid4()),
                    "userId": str(uuid4()),
                    "orgId": org_id,
                    "email": user_info.get("primaryEmail"),
                    "fullName": user_info.get("name", {}).get("fullName"),
                    "firstName": user_info.get("name", {}).get("givenName", ""),
                    "middleName": user_info.get("name", {}).get("middleName", ""),
                    "lastName": user_info.get("name", {}).get("familyName", ""),
                    "designation": user_info.get("designation", ""),
                    "businessPhones": user_info.get("phones", []),
                    "isActive": False,  # New users start as inactive
                    "createdAtTimestamp": int(
                        parse_timestamp(user_info.get("creationTime"))
                    ),
                    "updatedAtTimestamp": int(
                        parse_timestamp(user_info.get("creationTime"))
                    ),
                }

        except Exception as e:
            self.logger.error(f"Failed to get user info for {user_email}: {str(e)}")
            return None

    @exponential_backoff()
    async def get_group_info(self, org_id: str, group_email: str) -> Optional[Dict]:
        """Get group information from Google Admin API"""
        try:
            if not await self.connect_admin(org_id):
                raise AdminServiceError(
                    "Failed to connect admin service for group info retrieval",
                    details={"org_id": org_id},
                )

            async with self.google_limiter:
                group_info = (
                    self.admin_directory_service.groups()
                    .get(groupKey=group_email)
                    .execute()
                )

                return {
                    "_key": str(uuid4()),
                    "groupId": str(uuid4()),
                    "orgId": org_id,
                    "email": group_info.get("email"),
                    "name": group_info.get("name"),
                    "description": group_info.get("description", ""),
                    # 'createdAtTimestamp': int(parse_timestamp(group_info.get('creationTime')).timestamp()),
                    # 'updatedAtTimestamp': int(datetime.now(timezone.utc).timestamp())
                }

        except Exception as e:
            self.logger.error(f"Failed to get group info for {group_email}: {str(e)}")
            return None

    @exponential_backoff()
    async def create_admin_watch(self, org_id: str, app_name: str) -> None:
        """Create a watch for admin activities (user creation/deletion)"""
        try:
            self.logger.info("🔍 Setting up admin activity watch")

            if not await self.connect_admin(org_id, app_name):
                raise AdminServiceError(
                    "Failed to connect admin service for watch creation",
                    details={"org_id": org_id},
                )

            # Create a channel for notifications
            channel_id = str(uuid4())
            channel_token = str(uuid4())
            try:
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                webhook_endpoint = endpoints.get("connectors", {}).get("publicEndpoint")
                if not webhook_endpoint:
                    webhook_endpoint = endpoints.get("connectors", {}).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
                    if not webhook_endpoint:
                        raise AdminServiceError(
                            "Missing webhook endpoint configuration",
                            details={"endpoints": endpoints},
                        )
                # Return None if webhook uses HTTP or localhost
                if (
                    webhook_endpoint.startswith("http://")
                    or "localhost" in webhook_endpoint
                ):
                    self.logger.warning(
                        "⚠️ Skipping changes watch - webhook endpoint uses HTTP or localhost"
                    )
                    return

            except Exception as e:
                raise AdminServiceError(
                    "Failed to get webhook configuration: " + str(e),
                    details={"error": str(e)},
                )

            webhook_url = f"{webhook_endpoint.rstrip('/')}/admin/webhook"

            channel_body = {
                "id": channel_id,
                "type": "web_hook",
                "address": webhook_url,
                "token": channel_token,
                "payload": True,
            }

            self.logger.info(f"🔍 Creating admin watch for {org_id}")

            try:
                async with self.google_limiter:
                    (
                        self.admin_reports_service.activities()
                        .watch(
                            userKey="all", applicationName="admin", body=channel_body
                        )
                        .execute()
                    )
                    self.logger.debug(
                        f"🔍 Admin watch created successfully for {org_id}"
                    )
                    return
            except HttpError as http_err:
                # Decode the error content if needed
                error_details = http_err.content.decode("utf-8")
                self.logger.error(f"❌ HttpError: {error_details}")
                raise AdminServiceError(
                    "Failed to create admin watch",
                    details={"org_id": org_id, "error": error_details},
                )
            except Exception as e:
                self.logger.error(f"❌ Failed to create admin watch: {str(e)}")
                raise

        except Exception as e:
            self.logger.error(f"❌ Failed to create admin watch: {str(e)}")
            raise

    async def create_drive_user_service(
        self, user_email: str
    ) -> Optional[DriveUserService]:
        """Get or create a DriveUserService for a specific user"""
        try:
            # Create delegated credentials for the user
            try:
                user_key = await self.arango_service.get_entity_id_by_email(user_email)
                user = await self.arango_service.get_document(user_key, CollectionNames.USERS.value,)
                if self.credentials is None:
                    await self.connect_admin(user.get("orgId"), app_name="DRIVE")
                user_credentials = self.credentials.with_subject(user_email)
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials for user: " + str(e),
                    details={"user_email": user_email, "error": str(e)},
                )

            # Create new user service
            user_service = DriveUserService(
                logger=self.logger,
                config_service=self.config_service,
                rate_limiter=self.rate_limiter,
                google_token_handler=self.google_token_handler,
                credentials=user_credentials,
            )

            # Connect the user service
            try:
                org_id = user.get("orgId")
                user_id = user.get("userId")
                if not await user_service.connect_enterprise_user(org_id, user_id):
                    raise UserOperationError(
                        "Failed to connect user service",
                        details={"user_email": user_email},
                    )
            except Exception as e:
                raise UserOperationError(
                    "Error connecting user service: " + str(e),
                    details={"user_email": user_email, "error": str(e)},
                )

            return user_service

        except (AdminDelegationError, UserOperationError):
            raise
        except Exception as e:
            self.logger.error(
                f"❌ Failed to create user service for {user_email}: {str(e)}"
            )
            raise AdminServiceError(
                "Unexpected error creating user service: " + str(e),
                details={"user_email": user_email, "error": str(e)},
            )

    async def create_gmail_user_service(
        self, user_email: str
    ) -> Optional[GmailUserService]:
        """Get or create a GmailUserService for a specific user"""
        try:
            # Create delegated credentials for the user
            try:
                user_key = await self.arango_service.get_entity_id_by_email(user_email)
                user = await self.arango_service.get_document(user_key, CollectionNames.USERS.value)
                if self.credentials is None:
                    await self.connect_admin(user.get("orgId"), app_name="GMAIL")
                user_credentials = self.credentials.with_subject(user_email)
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials for user: " + str(e),
                    details={"user_email": user_email, "error": str(e)},
                )

            # Create new user service
            user_service = GmailUserService(
                logger=self.logger,
                config_service=self.config_service,
                rate_limiter=self.rate_limiter,
                google_token_handler=self.google_token_handler,
                credentials=user_credentials,
                admin_service=self,  # Pass the current GoogleAdminService instance
            )

            # Connect the user service
            try:
                org_id = user.get("orgId")
                user_id = user.get("userId")
                if not await user_service.connect_enterprise_user(org_id, user_id):
                    raise UserOperationError(
                        "Failed to connect user service",
                        details={"user_email": user_email},
                    )
            except Exception as e:
                raise UserOperationError(
                    "Error connecting user service",
                    details={"user_email": user_email, "error": str(e)},
                )

            return user_service

        except (AdminDelegationError, UserOperationError):
            raise
        except Exception as e:
            raise GoogleMailError(
                "Unexpected error creating user service: " + str(e),
                details={"user_email": user_email, "error": str(e)},
            )

    async def create_parser_user_service(
        self, user_email: str
    ) -> Optional[ParserUserService]:
        """Create a ParserUserService for a specific user"""
        try:
            self.logger.info("🚀 Creating parser user service for %s", user_email)
            try:
                user_key = await self.arango_service.get_entity_id_by_email(user_email)
                user = await self.arango_service.get_document(user_key, CollectionNames.USERS.value)
                if self.credentials is None:
                    await self.connect_admin(user.get("orgId"))
                user_credentials = self.credentials.with_subject(user_email)
            except Exception as e:
                raise AdminDelegationError(
                    "Failed to create delegated credentials for user: " + str(e),
                    details={"user_email": user_email, "error": str(e)},
                )
            # Create new user service
            user_service = ParserUserService(
                logger=self.logger,
                config_service=self.config_service,
                rate_limiter=self.rate_limiter,
                google_token_handler=self.google_token_handler,
                credentials=user_credentials,
            )
            org_id = user.get("orgId")
            user_id = user.get("userId")
            if not await user_service.connect_enterprise_user(org_id, user_id):
                return None

            return user_service

        except Exception as e:
            self.logger.error(
                f"❌ Failed to create user service for {user_email}: {str(e)}"
            )
            raise AdminServiceError(
                "Unexpected error creating user service: " + str(e),
                details={"user_email": user_email, "error": str(e)},
            )
