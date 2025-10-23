# pylint: disable=E1101, W0718
import asyncio
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple, TypeVar
from uuid import uuid4

import google.oauth2.credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import BatchHttpRequest

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordTypes,
)
from app.config.constants.http_status_code import (
    HttpStatusCode,
)
from app.config.constants.service import (
    DefaultEndpoints,
    WebhookConfig,
    config_node_constants,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    BatchOperationError,
    DriveOperationError,
    DrivePermissionError,
    GoogleAuthError,
    GoogleDriveError,
)
from app.connectors.sources.google.common.google_token_handler import CredentialKeys
from app.connectors.utils.decorators import exponential_backoff, token_refresh
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.utils.time_conversion import get_epoch_timestamp_in_ms

T = TypeVar('T')


class DriveUserService:
    """DriveService class for interacting with Google Drive API"""

    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        rate_limiter: GoogleAPIRateLimiter,
        google_token_handler,
        credentials=None,
    ) -> None:
        """Initialize DriveService with config and rate limiter

        Args:
            config (Config): Configuration object
            rate_limiter (DriveAPIRateLimiter): Rate limiter for Drive API
        """
        self.logger = logger
        self.logger.info("🚀 Initializing DriveService")
        self.config_service = config_service
        self.service = None

        self.credentials = credentials

        # Rate limiters
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter
        self.google_token_handler = google_token_handler
        self.token_expiry = None
        self.org_id = None
        self.user_id = None
        self.is_delegated = credentials is not None
        # Protect shared google service across threads
        self._service_lock = threading.Lock()

    async def _get_drive_scopes(self) -> List[str]:
        """Get scopes for drive, with fallback to default readonly scope."""
        SCOPES = await self.google_token_handler.get_account_scopes(app_name="drive")
        if not SCOPES:
            SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
            self.logger.warning("Scopes for drive not found in config, using default.")
        self.logger.debug("Using scopes for drive: %s", SCOPES)
        return SCOPES

    @token_refresh
    async def connect_individual_user(self, org_id: str, user_id: str) -> bool:
        """Connect using OAuth2 credentials for individual user"""
        try:
            self.org_id = org_id
            self.user_id = user_id

            SCOPES = await self._get_drive_scopes()
            try:
                creds_data = await self.google_token_handler.get_individual_token(
                    org_id, user_id, app_name="drive"
                )
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to get individual token: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            # Create credentials object
            try:
                creds = google.oauth2.credentials.Credentials(
                    token=creds_data.get(CredentialKeys.ACCESS_TOKEN.value),
                    refresh_token=creds_data.get(CredentialKeys.REFRESH_TOKEN.value),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=creds_data.get(CredentialKeys.CLIENT_ID.value),
                    client_secret=creds_data.get(CredentialKeys.CLIENT_SECRET.value),
                    scopes=SCOPES,
                )
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to create credentials object: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            # Update token expiry time
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
                self.logger.info("✅ Token expiry time: %s", self.token_expiry)
            except Exception as e:
                self.logger.warning("Failed to set token expiry: %s", str(e))

            try:
                self.service = build("drive", "v3", credentials=creds)
                self.logger.debug("Self Drive Service: %s", self.service)
            except Exception as e:
                raise DriveOperationError(
                    "Failed to build Drive service: " + str(e),
                    details={"org_id": org_id, "user_id": user_id, "error": str(e)},
                )

            self.logger.info("✅ DriveUserService connected successfully")
            return True

        except (GoogleAuthError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Failed to connect individual Drive service: " + str(e),
                details={"org_id": org_id, "user_id": user_id, "error": str(e)},
            )

    async def _check_and_refresh_token(self) -> None:
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
            await self.google_token_handler.refresh_token(self.org_id, self.user_id, app_name="drive")

            creds_data = await self.google_token_handler.get_individual_token(
                self.org_id, self.user_id, app_name="drive"
            )
            SCOPES = await self._get_drive_scopes()
            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get(CredentialKeys.ACCESS_TOKEN.value),
                refresh_token=creds_data.get(CredentialKeys.REFRESH_TOKEN.value),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get(CredentialKeys.CLIENT_ID.value),
                client_secret=creds_data.get(CredentialKeys.CLIENT_SECRET.value),
                scopes=SCOPES,
            )

            self.service = build("drive", "v3", credentials=creds)
            self.logger.debug("Self Drive Service: %s", self.service)

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

            self.logger.info("✅ Token refreshed, new expiry: %s", self.token_expiry)

    async def connect_enterprise_user(self, org_id, user_id) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            self.org_id = org_id
            self.user_id = user_id

            self.service = build(
                "drive", "v3", credentials=self.credentials, cache_discovery=False
            )
            self.logger.debug("Self Drive Service: %s", self.service)
            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to connect to Enterprise Drive Service: %s", str(e)
            )
            return False

    async def disconnect(self) -> bool | None:
        """Disconnect and cleanup Drive service"""
        try:
            self.logger.info("🔄 Disconnecting Drive service")

            # Close the service connections if they exist
            if self.service:
                self.service.close()
                self.service = None

            # Clear credentials
            self.credentials = None

            self.logger.info("✅ Drive service disconnected successfully")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to disconnect Drive service: {str(e)}")
            return False

    @exponential_backoff()
    @token_refresh
    async def list_individual_user(self, org_id) -> List[Dict]:
        """Get individual user info"""
        try:
            self.logger.info("🚀 Getting individual user info")
            async with self.google_limiter:
                about = self.service.about().get(fields="user").execute()

                user = about.get("user", {})
                self.logger.info("🚀 User info: %s", user)

                user = {
                    "_key": str(uuid4()),
                    "userId": str(uuid4()),
                    "orgId": org_id,
                    "email": user.get("emailAddress"),
                    "fullName": user.get("displayName"),
                    "firstName": user.get("givenName", ""),
                    "middleName": user.get("middleName", ""),
                    "lastName": user.get("familyName", ""),
                    "designation": user.get("designation", ""),
                    "businessPhones": user.get("businessPhones", []),
                    "isActive": False,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                return [user]

        except Exception as e:
            self.logger.error("❌ Failed to get individual user info: %s", str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def list_files_in_folder(
        self, folder_id: str, include_subfolders: bool = True
    ) -> List[Dict]:
        """List all files in a folder and optionally its subfolders using BFS"""
        try:
            self.logger.info("🚀 Listing files in folder %s", folder_id)
            all_files = []
            folders_to_process = [(folder_id, "/")]
            processed_folders = set()
            folder_paths = {folder_id: "/"}

            while folders_to_process:
                current_folder, current_path = folders_to_process.pop(0)

                if current_folder in processed_folders:
                    continue

                processed_folders.add(current_folder)
                page_token = None

                while True:
                    try:
                        async with self.google_limiter:
                            response = (
                                self.service.files()
                                .list(
                                    q=f"'{current_folder}' in parents and trashed=false",
                                    spaces="drive",
                                    fields="nextPageToken, files(id, name, mimeType, size, webViewLink, md5Checksum, sha1Checksum, sha256Checksum, headRevisionId, parents, createdTime, modifiedTime, trashed, trashedTime, fileExtension)",
                                    pageToken=page_token,
                                    pageSize=1000,
                                    supportsAllDrives=True,
                                    includeItemsFromAllDrives=True,
                                )
                                .execute()
                            )
                    except HttpError as e:
                        if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                            raise DrivePermissionError(
                                "Permission denied for folder: " + str(e),
                                details={"folder_id": current_folder, "error": str(e)},
                            )
                        raise DriveOperationError(
                            "Failed to list files: " + str(e),
                            details={"folder_id": current_folder, "error": str(e)},
                        )

                    files = response.get("files", [])
                    for file in files:
                        file_path = f"{current_path}{file['name']}"
                        if file["mimeType"] == MimeTypes.GOOGLE_DRIVE_FOLDER.value:
                            folder_path = f"{file_path}/"
                            folder_paths[file["id"]] = folder_path
                            if include_subfolders:
                                folders_to_process.append((file["id"], folder_path))
                        file["path"] = file_path

                    all_files.extend(files)
                    page_token = response.get("nextPageToken")
                    if not page_token:
                        break

            self.logger.info(
                "✅ Found %s files in folder %s", len(all_files), folder_id
            )
            return all_files

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error listing files: " + str(e),
                details={"folder_id": folder_id, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def list_shared_drives(self) -> List[Dict]:
        """List all shared drives"""
        try:
            self.logger.info("🚀 Listing shared drives")
            async with self.google_limiter:
                drives = []
                page_token = None

                while True:
                    try:
                        response = (
                            self.service.drives()
                            .list(
                                pageSize=100,
                                fields="nextPageToken, drives(id, name, kind)",
                                pageToken=page_token,
                            )
                            .execute()
                        )
                    except HttpError as e:
                        if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                            raise DrivePermissionError(
                                "Permission denied listing shared drives: " + str(e),
                                details={"error": str(e)},
                            )
                        raise DriveOperationError(
                            "Failed to list shared drives: " + str(e),
                            details={"error": str(e)},
                        )

                    drives.extend(response.get("drives", []))
                    page_token = response.get("nextPageToken")

                    if not page_token:
                        break

                self.logger.info("✅ Found %s shared drives", len(drives))
                return drives

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error listing shared drives: " + str(e),
                details={"error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def create_changes_watch(self, token = None) -> Optional[Dict]:
        """Set up changes.watch for all changes"""
        try:
            self.logger.info("🚀 Creating changes watch")

            async with self.google_limiter:
                channel_id = str(uuid.uuid4())
                try:
                    endpoints = await self.config_service.get_config(
                        config_node_constants.ENDPOINTS.value
                    )
                    webhook_endpoint = endpoints.get("connectors", {}).get(
                        "publicEndpoint"
                    )
                    if not webhook_endpoint:
                        webhook_endpoint = endpoints.get("connectors", {}).get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)
                        if not webhook_endpoint:
                            raise DriveOperationError(
                                "Missing webhook endpoint configuration",
                                details={"endpoints": endpoints},
                            )
                    if token is None:
                        page_token = await self.get_start_page_token_api()
                    else:
                        page_token = token.get("token", None)

                    if not page_token:
                        raise DriveOperationError(
                            "Failed to get start page token",
                            details={"channel_id": channel_id},
                        )

                    # Return None if webhook uses HTTP or localhost
                    if (
                        webhook_endpoint.startswith("http://")
                        or "localhost" in webhook_endpoint
                    ):
                        self.logger.warning(
                            "⚠️ Skipping changes watch - webhook endpoint uses HTTP or localhost"
                        )
                        data = {
                            "channelId": None,
                            "resourceId": None,
                            "token": page_token,
                            "expiration": None,
                        }
                        return data

                except Exception as e:
                    raise DriveOperationError(
                        "Failed to get webhook configuration: " + str(e),
                        details={"error": str(e)},
                    )

                webhook_url = f"{webhook_endpoint.rstrip('/')}/drive/webhook"
                expiration_time = datetime.now(timezone.utc) + timedelta(
                    days=WebhookConfig.EXPIRATION_DAYS.value,
                    hours=WebhookConfig.EXPIRATION_HOURS.value,
                    minutes=WebhookConfig.EXPIRATION_MINUTES.value,
                )

                body = {
                    "id": channel_id,
                    "type": "web_hook",
                    "address": webhook_url,
                    "expiration": int(expiration_time.timestamp() * 1000),
                }

                try:
                    response = (
                        self.service.changes()
                        .watch(
                            pageToken=page_token,
                            body=body,
                            supportsAllDrives=True,
                            includeItemsFromAllDrives=True,
                            includeRemoved=True,
                        )
                        .execute()
                    )
                    self.logger.info(
                        "🚀 Changes watch created successfully: %s", response
                    )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise DrivePermissionError(
                            "Permission denied creating changes watch: " + str(e),
                            details={"channel_id": channel_id, "error": str(e)},
                        )
                    raise DriveOperationError(
                        "Failed to create changes watch: " + str(e),
                        details={"channel_id": channel_id, "error": str(e)},
                    )

                if not response or not response.get("resourceId"):
                    raise DriveOperationError(
                        "Invalid response from changes.watch",
                        details={"channel_id": channel_id, "response": response},
                    )

                data = {
                    "channelId": channel_id,
                    "resourceId": response["resourceId"],
                    "token": page_token,
                    "expiration": int(response["expiration"]),
                }

                self.logger.info("✅ Changes watch created successfully")
                return data

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error creating changes watch: " + str(e),
                details={"error": str(e)},
            )

    async def stop_watch(self, channel_id: Optional[str], resource_id: Optional[str]) -> bool:
        """Stop a changes watch"""
        try:
            if channel_id is None or resource_id is None:
                self.logger.warning("⚠️ No channel ID or resource ID to stop")
                return True

            self.service.channels().stop(
                body={"id": channel_id, "resourceId": resource_id}
            ).execute()
            self.logger.info("✅ Changes watch stopped successfully")
            return True
        except Exception as e:
            self.logger.error("Failed to stop changes watch: %s", str(e))
            return False

    @exponential_backoff()
    @token_refresh
    async def get_changes(self, page_token: str) -> Tuple[List[Dict], Optional[str]]:
        """Get all changes since the given page token"""
        try:
            self.logger.info("🚀 Getting changes since page token: %s", page_token)
            changes = []
            next_token = page_token

            if self.service is None:
                self.logger.error("Service is not initialized yet")
                return [], None

            while next_token:
                try:
                    async with self.google_limiter:
                        response = (
                            self.service.changes()
                            .list(
                                pageToken=next_token,
                                spaces="drive",
                                includeItemsFromAllDrives=True,
                                supportsAllDrives=True,
                                fields="changes/*, nextPageToken, newStartPageToken",
                            )
                            .execute()
                        )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.NOT_FOUND.value:  # Invalid page token
                        self.logger.error("❌ Invalid page token %s", page_token)
                        new_token = await self.get_start_page_token_api()
                        if not new_token:
                            raise DriveOperationError(
                                "Failed to get new start token after invalid token: "
                                + str(e),
                                details={"page_token": page_token},
                            )
                        return [], new_token
                    elif e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise DrivePermissionError(
                            "Permission denied getting changes: " + str(e),
                            details={"page_token": page_token, "error": str(e)},
                        )
                    raise DriveOperationError(
                        "Failed to list changes: " + str(e),
                        details={"page_token": page_token, "error": str(e)},
                    )

                changes.extend(response.get("changes", []))
                next_token = response.get("nextPageToken")

                if "newStartPageToken" in response:
                    return changes, response["newStartPageToken"]

                if not next_token:
                    break

            self.logger.info(
                "✅ Found %s changes since page token: %s", len(changes), page_token
            )
            return changes, next_token

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting changes: " + str(e),
                details={"page_token": page_token, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def get_start_page_token_api(self) -> Optional[str]:
        """Get current page token for changes"""
        try:
            self.logger.info("🚀 Getting start page token")
            async with self.google_limiter:
                try:
                    response = (
                        self.service.changes()
                        .getStartPageToken(supportsAllDrives=True)
                        .execute()
                    )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise DrivePermissionError(
                            "Permission denied getting start page token: " + str(e),
                            details={"error": str(e)},
                        )
                    raise DriveOperationError(
                        "Failed to get start page token: " + str(e),
                        details={"error": str(e)},
                    )

                token = response.get("startPageToken")
                if not token:
                    raise DriveOperationError(
                        "Invalid response: missing startPageToken",
                        details={"response": response},
                    )

                self.logger.info("✅ Fetched start page token %s", token)
                self.logger.debug("Token type: %s", type(token))
                return token

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting start page token: " + str(e),
                details={"error": str(e)},
            )

    def create_batch_request(self) -> BatchHttpRequest:
        """Create a new batch request"""
        return self.service.new_batch_http_request()

    @exponential_backoff()
    @token_refresh
    async def batch_fetch_metadata_and_permissions(
        self, file_ids: List[str], files: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """Fetch comprehensive metadata using batch requests"""
        try:
            self.logger.info(
                "🚀 Batch fetching metadata and content for %s files", len(file_ids)
            )
            failed_items = []
            metadata_results = {}

            if files:
                metadata_results = {f["id"]: f.copy() for f in files if "id" in f}

            basic_batch = self.create_batch_request()

            def metadata_callback(request_id, response, exception) -> None:
                req_type, file_id = request_id.split("_", 1)

                if exception is None:
                    if file_id not in metadata_results:
                        metadata_results[file_id] = {}

                    if req_type == "meta":
                        metadata_results[file_id].update(response)
                    elif req_type == "perm":
                        metadata_results[file_id]["permissions"] = response.get(
                            "permissions", []
                        )
                else:
                    error_details = {
                        "file_id": file_id,
                        "request_type": req_type,
                        "error": str(exception),
                    }

                    if isinstance(exception, HttpError):
                        if exception.resp.status == HttpStatusCode.FORBIDDEN.value:
                            self.logger.info(
                                "⚠️ Permission denied for %s request on file %s: %s",
                                req_type,
                                file_id,
                                str(exception),
                            )
                            # For permission requests, just set empty permissions and continue
                            if req_type == "perm":
                                if file_id in metadata_results:
                                    metadata_results[file_id]["permissions"] = []
                                return
                            failed_items.append(error_details)
                        else:
                            self.logger.error(
                                "❌ HTTP error for %s request on file %s: %s",
                                req_type,
                                file_id,
                                str(exception),
                            )
                            failed_items.append(error_details)
                    else:
                        self.logger.error(
                            "❌ Unexpected error for %s request on file %s: %s",
                            req_type,
                            file_id,
                            str(exception),
                        )
                        failed_items.append(error_details)

                    if req_type == "meta":
                        metadata_results[file_id] = None

            # Add requests to batch
            for file_id in file_ids:
                if not files or file_id not in metadata_results:
                    basic_batch.add(
                        self.service.files().get(
                            fileId=file_id,
                            fields="id, name, mimeType, size, webViewLink, md5Checksum, sha1Checksum, sha256Checksum, headRevisionId, parents, createdTime, modifiedTime, trashed, trashedTime, fileExtension",
                            supportsAllDrives=True,
                        ),
                        callback=metadata_callback,
                        request_id=f"meta_{file_id}",
                    )

                basic_batch.add(
                    self.service.permissions().list(
                        fileId=file_id,
                        fields="permissions(id, displayName, type, role, domain, emailAddress, deleted)",
                        supportsAllDrives=True,
                    ),
                    callback=metadata_callback,
                    request_id=f"perm_{file_id}",
                )

            try:
                async with self.google_limiter:
                    await asyncio.to_thread(basic_batch.execute)
            except Exception as e:
                raise DriveOperationError(
                    "Failed to execute batch request: " + str(e),
                    details={"error": str(e)},
                )

            final_results = []
            for file_id in file_ids:
                result = metadata_results.get(file_id)
                if (
                    result
                    and result.get("mimeType")
                    and result.get("mimeType").startswith("application/vnd.google-apps")
                    and result.get("mimeType") != MimeTypes.GOOGLE_DRIVE_FOLDER.value
                ):
                    try:
                        revisions = (
                            self.service.revisions()
                            .list(
                                fileId=file_id,
                                fields="revisions(id, modifiedTime)",
                                pageSize=10,
                            )
                            .execute()
                        )

                        revisions_list = revisions.get("revisions", [])
                        if revisions_list:
                            result["headRevisionId"] = revisions_list[-1].get("id", "")
                        else:
                            result["headRevisionId"] = ""

                        self.logger.info(
                            "✅ Fetched head revision ID for file: %s", file_id
                        )
                    except HttpError as e:
                        if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                            self.logger.warning(
                                "⚠️ Insufficient permissions to read revisions for file %s",
                                file_id,
                            )
                            result["headRevisionId"] = ""
                            failed_items.append(
                                {
                                    "file_id": file_id,
                                    "request_type": "revision",
                                    "error": str(e),
                                }
                            )
                        else:
                            raise DriveOperationError(
                                "Failed to fetch revisions: " + str(e),
                                details={"file_id": file_id, "error": str(e)},
                            )
                final_results.append(result)

            if failed_items:
                raise BatchOperationError(
                    f"Batch operation partially failed for {len(failed_items)} items",
                    failed_items=failed_items,
                    details={"total_files": len(file_ids)},
                )

            self.logger.info("✅ Completed batch fetch for %s files", len(file_ids))
            return final_results

        except BatchOperationError:
            # Let BatchOperationError propagate as is
            raise
        except DriveOperationError:
            # Let DriveOperationError propagate as is
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error in batch fetch: " + str(e),
                details={"total_files": len(file_ids), "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def get_drive_info(self, drive_id: str, org_id: str) -> dict:
        """Get drive information for root or shared drive"""
        try:
            if drive_id == "root":
                try:
                    response = (
                        self.service.files()
                        .get(fileId="root", supportsAllDrives=True)
                        .execute()
                    )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise DrivePermissionError(
                            "Permission denied accessing root drive: " + str(e),
                            details={"error": str(e)},
                        )
                    raise DriveOperationError(
                        "Failed to get root drive info: " + str(e),
                        details={"error": str(e)},
                    )

                drive_key = str(uuid.uuid4())
                current_time = get_epoch_timestamp_in_ms()

                return {
                    "drive": {
                        "_key": drive_key,
                        "id": response.get("id", "root"),
                        "name": response.get("name", "My Drive"),
                        "access_level": "WRITER",
                        "isShared": False,
                    },
                    "record": {
                        "_key": drive_key,
                        "orgId": org_id,
                        "recordName": response.get("name", "My Drive"),
                        "externalRecordId": response.get("id", "root"),
                        "externalRevisionId": "0",
                        "recordType": RecordTypes.DRIVE.value,
                        "version": 0,
                        "origin": OriginTypes.CONNECTOR.value,
                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                        "createdAtTimestamp": current_time,
                        "updatedAtTimestamp": current_time,
                        "lastSyncTimestamp": current_time,
                        "sourceCreatedAtTimestamp": 0,
                        "sourceLastModifiedTimestamp": 0,
                        "isArchived": False,
                        "isDeleted": False,
                        "virtualRecordId": None,
                    },
                }
            else:
                try:
                    response = (
                        self.service.drives()
                        .get(
                            driveId=drive_id, fields="id,name,capabilities,createdTime"
                        )
                        .execute()
                    )
                except HttpError as e:
                    if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                        raise DrivePermissionError(
                            "Permission denied accessing shared drive: " + str(e),
                            details={"drive_id": drive_id, "error": str(e)},
                        )
                    raise DriveOperationError(
                        "Failed to get shared drive info: " + str(e),
                        details={"drive_id": drive_id, "error": str(e)},
                    )

                drive_key = str(uuid.uuid4())
                current_time = get_epoch_timestamp_in_ms()

                return {
                    "drive": {
                        "_key": drive_key,
                        "id": response.get("id"),
                        "name": response.get("name"),
                        "access_level": (
                            "writer"
                            if response.get("capabilities", {}).get("canEdit")
                            else "reader"
                        ),
                        "isShared": True,
                    },
                    "record": {
                        "_key": drive_key,
                        "orgId": org_id,
                        "recordName": response.get("name"),
                        "recordType": RecordTypes.DRIVE.value,
                        "externalRecordId": response.get("id"),
                        "externalRevisionId": response.get("id"),
                        "origin": OriginTypes.CONNECTOR.value,
                        "connectorName": Connectors.GOOGLE_DRIVE.value,
                        "version": 0,
                        "createdAtTimestamp": current_time,
                        "updatedAtTimestamp": current_time,
                        "lastSyncTimestamp": current_time,
                        "sourceCreatedAtTimestamp": current_time,
                        "sourceLastModifiedTimestamp": current_time,
                        "isArchived": False,
                        "isDeleted": False,
                        "virtualRecordId": None,
                    },
                }

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting drive info: " + str(e),
                details={"drive_id": drive_id, "error": str(e)},
            )

    @exponential_backoff()
    @token_refresh
    async def get_shared_with_me_files(self, user_email: str) -> List[Dict]:
        """Get all files shared with the user along with their metadata and permissions"""
        try:
            self.logger.info("🚀 Getting files shared with me")

            if self.service is None:
                self.logger.error("Service is not initialized yet")
                return []

            try:
                async with self.google_limiter:
                    response = (
                        self.service.files()
                        .list(
                            q="sharedWithMe=true",
                            spaces="drive",
                            fields="files(*)",
                            supportsAllDrives=True,
                            includeItemsFromAllDrives=True,
                        )
                        .execute()
                    )
            except HttpError as e:
                if e.resp.status == HttpStatusCode.FORBIDDEN.value:
                    raise DrivePermissionError(
                        "Permission denied getting shared files: " + str(e),
                        details={"error": str(e)},
                    )
                raise DriveOperationError(
                    "Failed to list shared files: " + str(e),
                    details={"error": str(e)},
                )

            files = response.get("files", [])

            # Process each shared file to add permissions
            for file in files:
                # Create permission entry for the current user
                user_permission = {
                    "id": str(uuid.uuid4()),  # Generate unique permission ID
                    "type": "user",
                    "role": file.get("sharingUser", {}).get("me", True) and "owner" or "reader",
                    "emailAddress": user_email,
                    "displayName": file.get("sharingUser", {}).get("displayName", ""),
                    "photoLink": file.get("sharingUser", {}).get("photoLink", ""),
                    "kind": "drive#permission",
                    "deleted": False,
                    "permissionDetails": [
                        {
                            "permissionType": "user",
                            "role": file.get("sharingUser", {}).get("me", True) and "owner" or "reader",
                            "inherited": False
                        }
                    ]
                }

                # Create permission entry for the owner
                owner_permission = {
                    "id": str(uuid.uuid4()),
                    "type": "user",
                    "role": "owner",
                    "emailAddress": file.get("owners", [{}])[0].get("emailAddress", ""),
                    "displayName": file.get("owners", [{}])[0].get("displayName", ""),
                    "photoLink": file.get("owners", [{}])[0].get("photoLink", ""),
                    "kind": "drive#permission",
                    "deleted": False,
                    "permissionDetails": [
                        {
                            "permissionType": "user",
                            "role": "owner",
                            "inherited": False
                        }
                    ]
                }

                # Add permissions array to file metadata
                file["permissions"] = [owner_permission, user_permission]

                # Add shared flag
                file["isSharedWithMe"] = True

                # Add sharing user details if available
                if "sharingUser" in file:
                    file["sharingUserEmail"] = file["sharingUser"].get("emailAddress")
                    file["sharingUserName"] = file["sharingUser"].get("displayName")

            self.logger.info("✅ Found %s files shared with me", len(files))
            for file in files:
                self.logger.info(
                    "📄 Shared file: %s (id: %s)", file.get("name"), file.get("id")
                )
                self.logger.debug("File details: %s", file)

            return files

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting shared files: " + str(e),
                details={"error": str(e)},
            )

    # Async wrapper methods for blocking operations
    async def list_files_in_folder_async(self, folder_id: str, include_subfolders: bool = True) -> List[Dict]:
        """Async wrapper for list_files_in_folder to run in separate thread"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_with_service_lock(self.list_files_in_folder, folder_id, include_subfolders))

    async def batch_fetch_metadata_and_permissions_async(self, file_ids: List[str], files: Optional[List[Dict]] = None) -> List[Dict]:
        """Async wrapper for batch_fetch_metadata_and_permissions to run in separate thread"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_with_service_lock(self.batch_fetch_metadata_and_permissions, file_ids, files))

    async def get_drive_info_async(self, drive_id: str, org_id: str) -> dict:
        """Async wrapper for get_drive_info to run in separate thread"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_with_service_lock(self.get_drive_info, drive_id, org_id))

    async def get_shared_with_me_files_async(self, user_email: str) -> List[Dict]:
        """Async wrapper for get_shared_with_me_files to run in separate thread"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self._run_with_service_lock(self.get_shared_with_me_files, user_email))

    def _run_with_service_lock(self, func: Callable[..., T], *args, **kwargs) -> T:
        with self._service_lock:
            return asyncio.run(func(*args, **kwargs))
