# pylint: disable=E1101, W0718
import json
import asyncio
import uuid
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import BatchHttpRequest
from app.config.arangodb_constants import Connectors, RecordTypes
from app.config.configuration_service import ConfigurationService, config_node_constants, WebhookConfig
from app.utils.logger import create_logger
from app.connectors.utils.decorators import exponential_backoff, token_refresh
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.connectors.google.scopes import GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
from app.config.arangodb_constants import OriginTypes
from uuid import uuid4
from app.utils.time_conversion import get_epoch_timestamp_in_ms
import google.oauth2.credentials
from app.exceptions.connector_google_exceptions import (
    GoogleAuthError, GoogleDriveError, DriveOperationError, 
    DrivePermissionError, DriveSyncError, BatchOperationError
)

logger = create_logger(__name__)

class DriveUserService:
    """DriveService class for interacting with Google Drive API"""

    def __init__(self, config: ConfigurationService, rate_limiter: GoogleAPIRateLimiter, google_token_handler, credentials=None):
        """Initialize DriveService with config and rate limiter

        Args:
            config (Config): Configuration object
            rate_limiter (DriveAPIRateLimiter): Rate limiter for Drive API
        """
        logger.info("🚀 Initializing DriveService")
        self.config_service = config
        self.service = None

        self.credentials = credentials

        # Rate limiters
        self.rate_limiter = rate_limiter
        self.google_limiter = self.rate_limiter.google_limiter
        self.google_token_handler = google_token_handler
        self.token_expiry = None
        self.org_id = None
        self.user_id = None

    @token_refresh
    async def connect_individual_user(self, org_id: str, user_id: str) -> bool:
        """Connect using OAuth2 credentials for individual user"""
        try:
            self.org_id = org_id
            self.user_id = user_id
            
            SCOPES = GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            
            try:
                creds_data = await self.google_token_handler.get_individual_token(org_id, user_id)
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to get individual token",
                    details={
                        "org_id": org_id,
                        "user_id": user_id,
                        "error": str(e)
                    }
                )

            # Create credentials object
            try:
                creds = google.oauth2.credentials.Credentials(
                    token=creds_data.get('access_token'),
                    refresh_token=creds_data.get('refresh_token'),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=creds_data.get('client_id'),
                    client_secret=creds_data.get('client_secret'),
                    scopes=SCOPES
                )
            except Exception as e:
                raise GoogleAuthError(
                    "Failed to create credentials object",
                    details={
                        "org_id": org_id,
                        "user_id": user_id,
                        "error": str(e)
                    }
                )

            # Update token expiry time
            self.token_expiry = datetime.fromtimestamp(
                creds_data.get('access_token_expiry_time', 0) / 1000,
                tz=timezone.utc
            )
            
            try:
                self.service = build('drive', 'v3', credentials=creds)
            except Exception as e:
                raise DriveOperationError(
                    "Failed to build Drive service",
                    details={
                        "org_id": org_id,
                        "user_id": user_id,
                        "error": str(e)
                    }
                )

            logger.info("✅ DriveUserService connected successfully")
            return True

        except (GoogleAuthError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Failed to connect individual Drive service",
                details={
                    "org_id": org_id,
                    "user_id": user_id,
                    "error": str(e)
                }
            )

    async def _check_and_refresh_token(self):
        """Check token expiry and refresh if needed"""
        if not self.token_expiry:
            # logger.warning("⚠️ Token expiry time not set.")
            return
        
        if not self.org_id or not self.user_id:
            logger.warning("⚠️ Org ID or User ID not set yet.")
            return

        now = datetime.now(timezone.utc)
        time_until_refresh = self.token_expiry - now - timedelta(minutes=20)
        logger.info(f"Time until refresh: {time_until_refresh.total_seconds()} seconds")
        
        if time_until_refresh.total_seconds() <= 0:
            await self.google_token_handler.refresh_token(self.org_id, self.user_id)
                    
            creds_data = await self.google_token_handler.get_individual_token(self.org_id, self.user_id)

            creds = google.oauth2.credentials.Credentials(
                token=creds_data.get('access_token'),
                refresh_token=creds_data.get('refresh_token'),
                token_uri="https://oauth2.googleapis.com/token",
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=GOOGLE_CONNECTOR_INDIVIDUAL_SCOPES
            )

            self.service = build('drive', 'v3', credentials=creds)

            # Update token expiry time
            self.token_expiry = datetime.fromtimestamp(
                creds_data.get('access_token_expiry_time', 0) / 1000,
                tz=timezone.utc
            )

            logger.info("✅ Token refreshed, new expiry: %s", self.token_expiry)


    async def connect_enterprise_user(self) -> bool:
        """Connect using OAuth2 credentials for enterprise user"""
        try:
            self.service = build(
                'drive',
                'v3',
                credentials=self.credentials,
                cache_discovery=False
            )
            return True

        except Exception as e:
            logger.error(
                "❌ Failed to connect to Enterprise Drive Service: %s", str(e))
            return False

    async def disconnect(self):
        """Disconnect and cleanup Drive service"""
        try:
            logger.info("🔄 Disconnecting Drive service")

            # Close the service connections if they exist
            if self.service:
                self.service.close()
                self.service = None

            # Clear credentials
            self.credentials = None

            logger.info("✅ Drive service disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to disconnect Drive service: {str(e)}")
            return False

    @exponential_backoff()
    @token_refresh
    async def list_individual_user(self, org_id) -> List[Dict]:
        """Get individual user info"""
        try:
            logger.info("🚀 Getting individual user info")
            async with self.google_limiter:
                about = self.service.about().get(
                    fields="user"
                ).execute()

                user = about.get('user', {})
                logger.info("🚀 User info: %s", user)
                
                user = {
                    '_key': str(uuid4()),
                    'userId': str(uuid4()),
                    'orgId': org_id,
                    'email': user.get('emailAddress'),
                    'fullName': user.get('displayName'),
                    'firstName': user.get('givenName', ''),
                    'middleName': user.get('middleName', ''),
                    'lastName': user.get('familyName', ''),
                    'designation': user.get('designation', ''),
                    'businessPhones': user.get('businessPhones', []),
                    'isActive': False,
                    'createdAtTimestamp': get_epoch_timestamp_in_ms(),
                    'updatedAtTimestamp': get_epoch_timestamp_in_ms()
                }
                return [user]

        except Exception as e:
            logger.error("❌ Failed to get individual user info: %s", str(e))
            return []

    @exponential_backoff()
    @token_refresh
    async def list_files_in_folder(self, folder_id: str, include_subfolders: bool = True) -> List[Dict]:
        """List all files in a folder and optionally its subfolders using BFS"""
        try:
            logger.info("🚀 Listing files in folder %s", folder_id)
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
                            response = self.service.files().list(
                                q=f"'{current_folder}' in parents and trashed=false",
                                spaces='drive',
                                fields="nextPageToken, files(id, name, mimeType, size, webViewLink, md5Checksum, sha1Checksum, sha256Checksum, headRevisionId, parents, createdTime, modifiedTime, trashed, trashedTime, fileExtension)",
                                pageToken=page_token,
                                pageSize=1000,
                                supportsAllDrives=True,
                                includeItemsFromAllDrives=True
                            ).execute()
                    except HttpError as e:
                        if e.resp.status == 403:
                            raise DrivePermissionError(
                                "Permission denied for folder",
                                details={
                                    "folder_id": current_folder,
                                    "error": str(e)
                                }
                            )
                        raise DriveOperationError(
                            "Failed to list files",
                            details={
                                "folder_id": current_folder,
                                "error": str(e)
                            }
                        )

                    files = response.get('files', [])
                    for file in files:
                        file_path = f"{current_path}{file['name']}"
                        if file['mimeType'] == 'application/vnd.google-apps.folder':
                            folder_path = f"{file_path}/"
                            folder_paths[file['id']] = folder_path
                            if include_subfolders:
                                folders_to_process.append((file['id'], folder_path))
                        file['path'] = file_path

                    all_files.extend(files)
                    page_token = response.get('nextPageToken')
                    if not page_token:
                        break

            logger.info("✅ Found %s files in folder %s", len(all_files), folder_id)
            return all_files

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error listing files",
                details={
                    "folder_id": folder_id,
                    "error": str(e)
                }
            )

    @exponential_backoff()
    @token_refresh
    async def list_shared_drives(self) -> List[Dict]:
        """List all shared drives"""
        try:
            logger.info("🚀 Listing shared drives")
            async with self.google_limiter:
                drives = []
                page_token = None

                while True:
                    try:
                        response = self.service.drives().list(
                            pageSize=100,
                            fields="nextPageToken, drives(id, name, kind)",
                            pageToken=page_token
                        ).execute()
                    except HttpError as e:
                        if e.resp.status == 403:
                            raise DrivePermissionError(
                                "Permission denied listing shared drives",
                                details={"error": str(e)}
                            )
                        raise DriveOperationError(
                            "Failed to list shared drives",
                            details={"error": str(e)}
                        )

                    drives.extend(response.get('drives', []))
                    page_token = response.get('nextPageToken')

                    if not page_token:
                        break

                logger.info("✅ Found %s shared drives", len(drives))
                return drives

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error listing shared drives",
                details={"error": str(e)}
            )

    @exponential_backoff()
    @token_refresh
    async def create_changes_watch(self) -> Optional[Dict]:
        """Set up changes.watch for all changes"""
        try:
            logger.info("🚀 Creating changes watch")

            async with self.google_limiter:
                channel_id = str(uuid.uuid4())
                try:
                    endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
                    webhook_endpoint = endpoints.get('connectors', {}).get('publicEndpoint')
                    if not webhook_endpoint:
                        raise DriveOperationError(
                            "Missing webhook endpoint configuration",
                            details={"endpoints": endpoints}
                        )
                except Exception as e:
                    raise DriveOperationError(
                        "Failed to get webhook configuration",
                        details={"error": str(e)}
                    )

                webhook_url = f"{webhook_endpoint.rstrip('/')}/drive/webhook"
                expiration_time = datetime.now(timezone.utc) + timedelta(
                    hours=WebhookConfig.EXPIRATION_HOURS.value,
                    minutes=WebhookConfig.EXPIRATION_MINUTES.value
                )

                body = {
                    'id': channel_id,
                    'type': 'web_hook',
                    'address': webhook_url,
                    'expiration': int(expiration_time.timestamp() * 1000)
                }

                page_token = await self.get_start_page_token_api()
                if not page_token:
                    raise DriveOperationError(
                        "Failed to get start page token",
                        details={"channel_id": channel_id}
                    )

                try:
                    response = self.service.changes().watch(
                        pageToken=page_token,
                        body=body,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                        includeRemoved=True
                    ).execute()
                except HttpError as e:
                    if e.resp.status == 403:
                        raise DrivePermissionError(
                            "Permission denied creating changes watch",
                            details={
                                "channel_id": channel_id,
                                "error": str(e)
                            }
                        )
                    raise DriveOperationError(
                        "Failed to create changes watch",
                        details={
                            "channel_id": channel_id,
                            "error": str(e)
                        }
                    )

                if not response or not response.get('resourceId'):
                    raise DriveOperationError(
                        "Invalid response from changes.watch",
                        details={
                            "channel_id": channel_id,
                            "response": response
                        }
                    )

                data = {
                    'channel_id': channel_id,
                    'resource_id': response['resourceId'],
                    'page_token': page_token,
                    'expiration': expiration_time.isoformat()
                }

                # Store webhook data
                try:
                    os.makedirs('logs/webhook_headers', exist_ok=True)
                    headers_log_path = 'logs/webhook_headers/headers_log.json'
                    
                    if not os.path.exists(headers_log_path):
                        with open(headers_log_path, 'w') as f:
                            json.dump([], f)

                    with open(headers_log_path, 'r+') as f:
                        channels = json.loads(f.read() or '[]')
                        channels.append(data)
                        f.seek(0)
                        f.truncate()
                        json.dump(channels, f, indent=2)
                except Exception as e:
                    logger.warning(
                        "Failed to store webhook data: %s. Continuing anyway.", str(e)
                    )

                logger.info("✅ Changes watch created successfully")
                return data

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error creating changes watch",
                details={"error": str(e)}
            )

    @exponential_backoff()
    @token_refresh
    async def get_changes(self, page_token: str) -> Tuple[List[Dict], Optional[str]]:
        """Get all changes since the given page token"""
        try:
            logger.info("🚀 Getting changes since page token: %s", page_token)
            changes = []
            next_token = page_token

            while next_token:
                try:
                    async with self.google_limiter:
                        response = self.service.changes().list(
                            pageToken=next_token,
                            spaces='drive',
                            includeItemsFromAllDrives=True,
                            supportsAllDrives=True,
                            fields='changes/*, nextPageToken, newStartPageToken'
                        ).execute()
                except HttpError as e:
                    if e.resp.status == 404:  # Invalid page token
                        logger.error("❌ Invalid page token %s", page_token)
                        new_token = await self.get_start_page_token_api()
                        if not new_token:
                            raise DriveOperationError(
                                "Failed to get new start token after invalid token",
                                details={"page_token": page_token}
                            )
                        return [], new_token
                    elif e.resp.status == 403:
                        raise DrivePermissionError(
                            "Permission denied getting changes",
                            details={
                                "page_token": page_token,
                                "error": str(e)
                            }
                        )
                    raise DriveOperationError(
                        "Failed to list changes",
                        details={
                            "page_token": page_token,
                            "error": str(e)
                        }
                    )

                changes.extend(response.get('changes', []))
                next_token = response.get('nextPageToken')

                if 'newStartPageToken' in response:
                    return changes, response['newStartPageToken']

                if not next_token:
                    break

            logger.info("✅ Found %s changes since page token: %s",
                        len(changes), page_token)
            return changes, next_token

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting changes",
                details={
                    "page_token": page_token,
                    "error": str(e)
                }
            )

    @exponential_backoff()
    @token_refresh
    async def get_start_page_token_api(self) -> Optional[str]:
        """Get current page token for changes"""
        try:
            logger.info("🚀 Getting start page token")
            async with self.google_limiter:
                try:
                    response = self.service.changes().getStartPageToken(
                        supportsAllDrives=True
                    ).execute()
                except HttpError as e:
                    if e.resp.status == 403:
                        raise DrivePermissionError(
                            "Permission denied getting start page token",
                            details={"error": str(e)}
                        )
                    raise DriveOperationError(
                        "Failed to get start page token",
                        details={"error": str(e)}
                    )

                token = response.get('startPageToken')
                if not token:
                    raise DriveOperationError(
                        "Invalid response: missing startPageToken",
                        details={"response": response}
                    )

                logger.info("✅ Fetched start page token %s", token)
                return token

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting start page token",
                details={"error": str(e)}
            )

    def create_batch_request(self) -> BatchHttpRequest:
        """Create a new batch request"""
        return self.service.new_batch_http_request()

    @exponential_backoff()
    @token_refresh
    async def batch_fetch_metadata_and_permissions(self, file_ids: List[str], files: Optional[List[Dict]] = None) -> List[Dict]:
        """Fetch comprehensive metadata using batch requests"""
        try:
            logger.info("🚀 Batch fetching metadata and content for %s files", len(file_ids))
            failed_items = []
            metadata_results = {}
            
            if files:
                metadata_results = {f['id']: f.copy() for f in files if 'id' in f}

            basic_batch = self.create_batch_request()

            def metadata_callback(request_id, response, exception):
                req_type, file_id = request_id.split('_', 1)
                
                if exception is None:
                    if file_id not in metadata_results:
                        metadata_results[file_id] = {}

                    if req_type == 'meta':
                        metadata_results[file_id].update(response)
                    elif req_type == 'perm':
                        metadata_results[file_id]['permissions'] = response.get('permissions', [])
                else:
                    error_details = {
                        'file_id': file_id,
                        'request_type': req_type,
                        'error': str(exception)
                    }
                    failed_items.append(error_details)
                    if isinstance(exception, HttpError):
                        if exception.resp.status == 403:
                            logger.error(
                                "⚠️ Permission denied for %s request on file %s: %s",
                                req_type, file_id, str(exception)
                            )
                        else:
                            logger.error(
                                "❌ HTTP error for %s request on file %s: %s",
                                req_type, file_id, str(exception)
                            )
                    else:
                        logger.error(
                            "❌ Unexpected error for %s request on file %s: %s",
                            req_type, file_id, str(exception)
                        )
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
                        request_id=f"meta_{file_id}"
                    )

                basic_batch.add(
                    self.service.permissions().list(
                        fileId=file_id,
                        fields="permissions(id, displayName, type, role, domain, emailAddress, deleted)",
                        supportsAllDrives=True
                    ),
                    callback=metadata_callback,
                    request_id=f"perm_{file_id}"
                )

            try:
                async with self.google_limiter:
                    await asyncio.to_thread(basic_batch.execute)
            except Exception as e:
                raise DriveOperationError(
                    "Failed to execute batch request",
                    details={"error": str(e)}
                )

            final_results = []
            for file_id in file_ids:
                result = metadata_results.get(file_id)
                if result and result.get('mimeType') and result.get('mimeType').startswith('application/vnd.google-apps') and result.get('mimeType') != 'application/vnd.google-apps.folder':
                    try:
                        revisions = self.service.revisions().list(
                            fileId=file_id,
                            fields="revisions(id, modifiedTime)",
                            pageSize=10
                        ).execute()
                        
                        revisions_list = revisions.get('revisions', [])
                        if revisions_list:
                            result['headRevisionId'] = revisions_list[-1].get('id', '')
                        else:
                            result['headRevisionId'] = ''
                            
                        logger.info("✅ Fetched head revision ID for file: %s", file_id)
                    except HttpError as e:
                        if e.resp.status == 403:
                            logger.warning(
                                "⚠️ Insufficient permissions to read revisions for file %s", file_id)
                            result['headRevisionId'] = ""
                            failed_items.append({
                                'file_id': file_id,
                                'request_type': 'revision',
                                'error': str(e)
                            })
                        else:
                            raise DriveOperationError(
                                "Failed to fetch revisions",
                                details={
                                    "file_id": file_id,
                                    "error": str(e)
                                }
                            )
                final_results.append(result)

            if failed_items:
                raise BatchOperationError(
                    f"Batch operation partially failed for {len(failed_items)} items",
                    failed_items=failed_items,
                    details={"total_files": len(file_ids)}
                )

            logger.info("✅ Completed batch fetch for %s files", len(file_ids))
            return final_results

        except BatchOperationError:
            # Let BatchOperationError propagate as is
            raise
        except DriveOperationError:
            # Let DriveOperationError propagate as is
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error in batch fetch",
                details={
                    "total_files": len(file_ids),
                    "error": str(e)
                }
            )

    @exponential_backoff()
    @token_refresh
    async def get_drive_info(self, drive_id: str, org_id: str) -> dict:
        """Get drive information for root or shared drive"""
        try:
            if drive_id == 'root':
                try:
                    response = self.service.files().get(
                        fileId='root',
                        supportsAllDrives=True
                    ).execute()
                except HttpError as e:
                    if e.resp.status == 403:
                        raise DrivePermissionError(
                            "Permission denied accessing root drive",
                            details={"error": str(e)}
                        )
                    raise DriveOperationError(
                        "Failed to get root drive info",
                        details={"error": str(e)}
                    )

                drive_key = str(uuid.uuid4())
                current_time = get_epoch_timestamp_in_ms()

                return {
                    'drive': {
                        '_key': drive_key,
                        'id': response.get('id', 'root'),
                        'name': response.get('name', 'My Drive'),
                        'access_level': 'WRITER',
                        'isShared': False
                    },
                    'record': {
                        '_key': drive_key,
                        'orgId': org_id,
                        'recordName': response.get('name', 'My Drive'),
                        'externalRecordId': response.get('id', 'root'),
                        'externalRevisionId': '0',
                        'recordType': RecordTypes.DRIVE.value,
                        'version': 0,
                        "origin": OriginTypes.CONNECTOR.value,
                        'connectorName': Connectors.GOOGLE_DRIVE.value,
                        'createdAtTimestamp': current_time,
                        'updatedAtTimestamp': current_time,
                        'lastSyncTimestamp': current_time,
                        'sourceCreatedAtTimestamp': 0,
                        'sourceLastModifiedTimestamp': 0,
                        'isArchived': False,
                        'isDeleted': False,
                        'isDirty': True,
                        'indexingStatus': 'NOT_STARTED',
                        'extractionStatus': 'NOT_STARTED',
                        'lastIndexTimestamp': 0,
                        'lastExtractionTimestamp': 0,
                        'isLatestVersion': False,
                        'reason': None
                    }
                }
            else:
                try:
                    response = self.service.drives().get(
                        driveId=drive_id,
                        fields='id,name,capabilities,createdTime'
                    ).execute()
                except HttpError as e:
                    if e.resp.status == 403:
                        raise DrivePermissionError(
                            "Permission denied accessing shared drive",
                            details={
                                "drive_id": drive_id,
                                "error": str(e)
                            }
                        )
                    raise DriveOperationError(
                        "Failed to get shared drive info",
                        details={
                            "drive_id": drive_id,
                            "error": str(e)}
                        )

                drive_key = str(uuid.uuid4())
                current_time = get_epoch_timestamp_in_ms()

                return {
                    'drive': {
                        '_key': drive_key,
                        'id': response.get('id'),
                        'name': response.get('name'),
                        'access_level': 'writer' if response.get('capabilities', {}).get('canEdit') else 'reader',
                        'isShared': True
                    },
                    'record': {
                        '_key': drive_key,
                        'orgId': org_id,
                        'recordName': response.get('name'),
                        'recordType': RecordTypes.DRIVE.value,
                        'externalRecordId': response.get('id'),
                        'externalRevisionId': response.get('id'),
                        "origin": OriginTypes.CONNECTOR.value,
                        'connectorName': Connectors.GOOGLE_DRIVE.value,
                        'version': 0,
                        'createdAtTimestamp': current_time,
                        'updatedAtTimestamp': current_time,
                        'lastSyncTimestamp': current_time,
                        'sourceCreatedAtTimestamp': current_time,
                        'sourceLastModifiedTimestamp': current_time,
                        'isArchived': False,
                        'isDeleted': False,
                        'isDirty': True,
                        'isLatestVersion': False,
                        'indexingStatus': 'NOT_STARTED',
                        'extractionStatus': 'NOT_STARTED'
                    }
                }

        except (DrivePermissionError, DriveOperationError):
            raise
        except Exception as e:
            raise GoogleDriveError(
                "Unexpected error getting drive info",
                details={
                    "drive_id": drive_id,
                    "error": str(e)
                }
            )
