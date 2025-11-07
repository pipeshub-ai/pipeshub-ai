import asyncio
import re
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple

import httpx
from aiolimiter import AsyncLimiter
from azure.identity.aio import ClientSecretCredential
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from msgraph import GraphServiceClient
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.list_item import ListItem
from msgraph.generated.models.site import Site
from msgraph.generated.models.site_page import SitePage
from msgraph.generated.models.subscription import Subscription

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    DocumentationLink,
    FilterField,
)
from app.connectors.sources.microsoft.common.apps import SharePointOnlineApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordStatus,
    RecordType,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import stream_content

# Constants for SharePoint site ID composite format
# A composite site ID has the format: "hostname,site-id,web-id"
COMPOSITE_SITE_ID_COMMA_COUNT = 2
COMPOSITE_SITE_ID_PARTS_COUNT = 3


class SharePointRecordType(Enum):
    """Extended record types for SharePoint"""
    SITE = "SITE"
    SUBSITE = "SUBSITE"
    DOCUMENT_LIBRARY = "SHAREPOINT_DOCUMENT_LIBRARY"
    LIST = "SHAREPOINT_LIST"
    LIST_ITEM = "SHAREPOINT_LIST_ITEM"
    PAGE = "WEBPAGE"
    FILE = "FILE"


@dataclass
class SharePointCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    sharepoint_domain: str
    has_admin_consent: bool = False
    root_site_url: Optional[str] = None  # e.g., "contoso.sharepoint.com"
    enable_subsite_discovery: bool = True  # Whether to attempt subsite discovery


@dataclass
class SiteMetadata:
    """Metadata for a SharePoint site"""
    site_id: str
    site_url: str
    site_name: str
    is_root: bool
    parent_site_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

@ConnectorBuilder("SharePoint Online")\
    .in_group("Microsoft 365")\
    .with_auth_type("OAUTH_ADMIN_CONSENT")\
    .with_description("Sync documents and lists from SharePoint Online")\
    .with_categories(["Storage", "Documentation"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/sharepoint.svg")
        .add_documentation_link(DocumentationLink(
            "SharePoint Online API Setup",
            "https://docs.microsoft.com/en-us/sharepoint/dev/sp-add-ins/register-sharepoint-add-ins",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/microsoft-365/sharepoint',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/SharePoint Online", False)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Azure AD Application ID",
            description="The Application (Client) ID from Azure AD App Registration"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Azure AD Client Secret",
            description="The Client Secret from Azure AD App Registration",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="tenantId",
            display_name="Directory (Tenant) ID (Optional)",
            placeholder="Enter your Azure AD Tenant ID",
            description="The Directory (Tenant) ID from Azure AD"
        ))
        .add_auth_field(AuthField(
            name="hasAdminConsent",
            display_name="Has Admin Consent",
            description="Check if admin consent has been granted for the application",
            field_type="CHECKBOX",
            required=True,
            default_value=False
        ))
        .add_auth_field(AuthField(
            name="sharepointDomain",
            display_name="SharePoint Domain",
            placeholder="https://your-domain.sharepoint.com",
            description="Your SharePoint domain URL",
            field_type="URL",
            max_length=2000
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .add_filter_field(FilterField(
            name="sites",
            display_name="SharePoint Sites",
            description="Select SharePoint sites to sync content from"
        ), "https://graph.microsoft.com/v1.0/sites")
        .add_filter_field(FilterField(
            name="documentLibraries",
            display_name="Document Libraries",
            description="Select document libraries to sync from"
        ), "https://graph.microsoft.com/v1.0/sites/{siteId}/drives")
        .add_filter_field(CommonFields.file_types_filter(), "static")
    )\
    .build_decorator()
class SharePointConnector(BaseConnector):
    """
    Complete SharePoint Online Connector implementation with robust error handling,
    proper URL encoding, and comprehensive data synchronization.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
    ) -> None:
        super().__init__(SharePointOnlineApp(), logger, data_entities_processor, data_store_provider, config_service)

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_name=self.connector_name,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )

        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.list_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.page_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        self.filters = {"exclude_onedrive_sites": True, "exclude_pages": True, "exclude_lists": True, "exclude_document_libraries": True}
        # Batch processing configuration
        self.batch_size = 50  # Reduced for better memory management
        self.max_concurrent_batches = 1  # Reduced to avoid rate limiting
        self.rate_limiter = AsyncLimiter(30, 1)  # 30 requests per second (conservative)

        # Cache for site metadata
        self.site_cache: Dict[str, SiteMetadata] = {}

        # Configuration flags
        self.enable_subsite_discovery = True
        # Statistics tracking
        self.stats = {
            'sites_processed': 0,
            'sites_failed': 0,
            'drives_processed': 0,
            'lists_processed': 0,
            'pages_processed': 0,
            'items_processed': 0,
            'errors_encountered': 0
        }

    async def init(self) -> None:
        config = await self.config_service.get_config("/services/connectors/sharepointonline/config") or \
                            await self.config_service.get_config(f"/services/connectors/sharepointonline/config/{self.data_entities_processor.org_id}")
        if not config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ValueError("SharePoint Online credentials not found")
        credentials_config = config.get("auth",{})
        if not credentials_config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ValueError("SharePoint Online credentials not found")
        tenant_id = credentials_config.get("tenantId")
        client_id = credentials_config.get("clientId")
        client_secret = credentials_config.get("clientSecret")
        sharepoint_domain = credentials_config.get("sharepointDomain")
        # Normalize SharePoint domain to scheme+host (no path), since tokens must target the tenant host
        try:
            parsed_domain = urllib.parse.urlparse(sharepoint_domain or "")
            host = parsed_domain.hostname
            scheme = parsed_domain.scheme or "https"
            if not host:
                # Handle cases where a bare host or host with path is provided without scheme
                candidate = sharepoint_domain or ""
                if "://" not in candidate:
                    candidate = f"https://{candidate.lstrip('/')}"
                parsed_candidate = urllib.parse.urlparse(candidate)
                host = parsed_candidate.hostname
                scheme = parsed_candidate.scheme or scheme
            if host:
                normalized_sharepoint_domain = f"{scheme}://{host}"
            else:
                normalized_sharepoint_domain = sharepoint_domain
        except Exception:
            normalized_sharepoint_domain = sharepoint_domain

        if not all((tenant_id, client_id, client_secret, sharepoint_domain)):
            self.logger.error("❌ Incomplete SharePoint Online credentials. Ensure tenantId, clientId, and clientSecret are configured.")
            raise ValueError("Incomplete SharePoint Online credentials. Ensure tenantId, clientId, and clientSecret are configured.")
        has_admin_consent = credentials_config.get("hasAdminConsent", False)
        credentials = SharePointCredentials(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            sharepoint_domain=normalized_sharepoint_domain,
            has_admin_consent=has_admin_consent,
        )
        credential = ClientSecretCredential(
                tenant_id=credentials.tenant_id,
                client_id=credentials.client_id,
                client_secret=credentials.client_secret,
            )
        self.sharepoint_domain = credentials.sharepoint_domain
        self.tenant_id = credentials.tenant_id
        self.client_id = credentials.client_id
        self.client_secret = credentials.client_secret
        self.client = GraphServiceClient(
            credential,
            scopes=["https://graph.microsoft.com/.default"]
        )
        self.msgraph_client = MSGraphClient(self.connector_name, self.client, self.logger)

    def _construct_site_url(self, site_id: str) -> str:
        """
        Properly construct SharePoint site URLs for Graph API calls.
        SharePoint site IDs often come in format: hostname,site-guid,web-guid
        These need to be properly URL encoded for Graph API calls.
        """
        if not site_id:
            self.logger.error("❌ Site ID cannot be empty")
            return ""

        return site_id

    def _validate_site_id(self, site_id: str) -> bool:
        """
        Validate SharePoint site ID format.
        """
        if not site_id:
            return False

        # Check for valid composite site ID format (hostname,guid,guid)
        SITE_ID_PARTS = 3
        GUID_LENGTH = 32
        ROOT_SITE_ID_LENGTH = 10
        if ',' in site_id:
            parts = site_id.split(',')
            if len(parts) == SITE_ID_PARTS:
                hostname, site_guid, web_guid = parts
                # Basic validation - hostname should contain a dot, GUIDs should be reasonable length
                if (hostname and '.' in hostname and
                    len(site_guid) >= GUID_LENGTH and  # GUID-like length
                    len(web_guid) >= GUID_LENGTH):     # GUID-like length
                    return True
            else:
                self.logger.warning(f"⚠️ Composite site ID has {len(parts)} parts, expected 3: {site_id}")
                return False

        # Single part site IDs are also valid (like "root")
        if site_id == "root" or len(site_id) > ROOT_SITE_ID_LENGTH:
            return True

        self.logger.warning(f"❌ Site ID format not recognized: {site_id}")
        return False

    def _normalize_site_id(self, site_id: str) -> str:
        if not site_id:
            return site_id

        # Already composite?
        if site_id.count(',') == COMPOSITE_SITE_ID_COMMA_COUNT:
            return site_id

        # Try to infer from cache (keys are composite IDs)
        for composite_id in self.site_cache:
            parts = composite_id.split(',')
            if len(parts) == COMPOSITE_SITE_ID_PARTS_COUNT and site_id == f"{parts[1]},{parts[2]}":
                return composite_id

        # Fallback: prepend tenant hostname if available
        if site_id.count(',') == 1 and getattr(self, 'sharepoint_domain', None):
            host = urllib.parse.urlparse(self.sharepoint_domain).hostname or self.sharepoint_domain
            if host and '.' in host:
                return f"{host},{site_id}"

        return site_id

    async def _safe_api_call(self, api_call, max_retries: int = 3, retry_delay: float = 1.0) -> None:
        """
        Enhanced safe API call execution with intelligent retry logic and error handling.
        """

        for attempt in range(max_retries + 1):
            try:
                result = await api_call
                return result

            except Exception as e:
                error_str = str(e).lower()

                # Don't retry on permission errors
                if any(term in error_str for term in [HttpStatusCode.FORBIDDEN.value, "accessdenied", "forbidden"]):
                    self.logger.debug(f"Permission denied on API call (attempt {attempt + 1}): {e}")
                    return None

                # Don't retry on 404 errors
                if any(term in error_str for term in [HttpStatusCode.NOT_FOUND.value, "notfound"]):
                    self.logger.debug(f"Resource not found on API call (attempt {attempt + 1}): {e}")
                    return None

                # Don't retry on 400 bad request errors (like invalid hostname)
                if any(term in error_str for term in [HttpStatusCode.BAD_REQUEST.value, "badrequest", "invalid"]):
                    self.logger.warning(f"⚠️ Bad request on API call (attempt {attempt + 1}): {e}")
                    return None

                # Retry on rate limiting and server errors
                if any(term in error_str for term in [HttpStatusCode.TOO_MANY_REQUESTS.value, HttpStatusCode.SERVICE_UNAVAILABLE.value, HttpStatusCode.BAD_GATEWAY.value, HttpStatusCode.INTERNAL_SERVER_ERROR.value, "throttle", "timeout"]):
                    if attempt < max_retries:
                        wait_time = retry_delay * (2 ** attempt)  # Exponential backoff
                        self.logger.warning(f"⚠️ Retryable error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {wait_time}s...")
                        await asyncio.sleep(wait_time)
                        continue

                # For other errors, retry with shorter backoff
                if attempt < max_retries:
                    wait_time = retry_delay * (1.5 ** attempt)
                    self.logger.warning(f"⚠️ API call failed (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    self.logger.error(f"❌ API call failed after {max_retries + 1} attempts. Last error: {e}")
                    self.stats['errors_encountered'] += 1
                    return None

        return None

    async def _get_all_sites(self) -> List[Site]:
        """
        Get all SharePoint sites in the tenant including root and subsites.
        Handles permission errors gracefully and continues with accessible sites.
        """
        sites = []

        try:
            self.logger.info("✅ Discovering SharePoint sites...")

            # Get root site using tenant root endpoint
            async with self.rate_limiter:
                try:
                    root_site = await self._safe_api_call(
                        self.client.sites.by_site_id("root").get()
                    )
                    if root_site:
                        sites.append(root_site)
                        self.logger.info(f"Root site found: '{root_site.display_name or root_site.name}' - ID: '{root_site.id}'")

                        self.site_cache[root_site.id] = SiteMetadata(
                            site_id=root_site.id,
                            site_url=root_site.web_url,
                            site_name=root_site.display_name or root_site.name,
                            is_root=True,
                            created_at=root_site.created_date_time,
                            updated_at=root_site.last_modified_date_time
                        )
                    else:
                        self.logger.warning("Could not fetch root site. Continuing with site search...")
                except Exception as root_error:
                    self.logger.warning(f"⚠️ Root site access failed: {root_error}. Continuing with site search...")

            # Get all sites using search
            async with self.rate_limiter:
                try:
                    search_results = await self._safe_api_call(
                        self.client.sites.get()
                    )
                    if search_results and search_results.value:
                        for site in search_results.value:
                            self.logger.debug(f"Checking site: '{site.display_name or site.name}' - URL: '{site.web_url}'")
                            self.logger.debug(f"exclude_onedrive_sites: {self.filters.get('exclude_onedrive_sites')}")
                            parsed_url = urllib.parse.urlparse(site.web_url)
                            hostname = parsed_url.hostname
                            contains_onedrive = (
                                hostname is not None and
                                re.fullmatch(r"[a-zA-Z0-9-]+-my\.sharepoint\.com", hostname)
                            )
                            self.logger.debug(f"Hostname matches expected OneDrive pattern: {bool(contains_onedrive)}")

                            if self.filters.get('exclude_onedrive_sites') and contains_onedrive:
                                self.logger.debug(f"Skipping OneDrive site: '{site.display_name or site.name}'")
                                continue

                            self.logger.debug(f"Site found: '{site.display_name or site.name}' - ID: '{site.id}'")
                            self.logger.debug(f"Site URL: '{site}'")
                            # Avoid duplicates
                            if not any(existing_site.id == site.id for existing_site in sites):
                                sites.append(site)
                                self.site_cache[site.id] = SiteMetadata(
                                    site_id=site.id,
                                    site_url=site.web_url,
                                    site_name=site.display_name or site.name,
                                    is_root=False,
                                    created_at=site.created_date_time,
                                    updated_at=site.last_modified_date_time
                                )
                        self.logger.info(f"Found {len(search_results.value)} additional sites from search")
                    else:
                        self.logger.info("No additional sites found from search endpoint")
                except Exception as search_error:
                    self.logger.warning(f"⚠️ Site search failed: {search_error}. Continuing with available sites...")

            # Get subsites for each site (optional)
            subsite_count = 0
            if self.enable_subsite_discovery and sites:
                self.logger.info("Discovering subsites...")
                for site in list(sites):
                    try:
                        subsites = await self._get_subsites(site.id)
                        if subsites:
                            for subsite in subsites:
                                if not any(existing_site.id == subsite.id for existing_site in sites):
                                    sites.append(subsite)
                                    subsite_count += 1
                    except Exception as subsite_error:
                        self.logger.debug(f"Subsite discovery failed for {site.display_name or site.name}: {subsite_error}")

            if subsite_count > 0:
                self.logger.info(f"Found {subsite_count} additional subsites")

            # Validate and filter sites
            valid_sites = []
            for site in sites:
                if self._validate_site_id(site.id):
                    valid_sites.append(site)
                else:
                    self.logger.warning(f"⚠️ Invalid site ID format, skipping: '{site.id}' ({site.display_name or site.name})")

            self.logger.info(f"Total valid SharePoint sites discovered: {len(valid_sites)}")
            return valid_sites

        except Exception as e:
            self.logger.error(f"❌ Critical error during site discovery: {e}")
            return sites  # Return whatever we managed to collect

    async def _get_subsites(self, site_id: str) -> List[Site]:
        """
        Get all subsites for a given site with comprehensive error handling.
        """
        try:
            subsites = []
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                result = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).sites.get()
                )

            if result and result.value:
                for subsite in result.value:
                    subsites.append(subsite)
                    self.site_cache[subsite.id] = SiteMetadata(
                        site_id=subsite.id,
                        site_url=subsite.web_url,
                        site_name=subsite.display_name or subsite.name,
                        is_root=False,
                        parent_site_id=site_id,
                        created_at=subsite.created_date_time,
                        updated_at=subsite.last_modified_date_time
                    )

            return subsites

        except Exception as e:
            self.logger.debug(f"⚠️ Subsite discovery failed for site {site_id}: {e}")
            return []

    async def _sync_site_content(self, site_record_group: RecordGroup) -> None:
        """
        Sync all content from a SharePoint site with comprehensive error tracking.
        """
        try:
            site_id = site_record_group.external_group_id
            site_name = site_record_group.name
            self.logger.info(f"Starting sync for site: '{site_name}' (ID: {site_id})")


            # Process all content types
            batch_records = []
            total_processed = 0

            # Process drives (document libraries)
            self.logger.info(f"Processing drives for site: {site_name}")
            async for record, permissions, record_update in self._process_site_drives(site_id, internal_site_record_group_id=site_record_group.id):
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                elif record:
                    batch_records.append((record, permissions))
                    total_processed += 1

                    if len(batch_records) >= self.batch_size:

                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        await asyncio.sleep(0.1)  # Brief pause

            # # Process lists
            # self.logger.info(f"Processing lists for site: {site_name}")
            # async for record, permissions, record_update in self._process_site_lists(site_id):
            #     if record_update.is_deleted:
            #         await self._handle_record_updates(record_update)
            #     elif record:
            #         batch_records.append((record, permissions))
            #         total_processed += 1

            #         if len(batch_records) >= self.batch_size:
            #             await self.data_entities_processor.on_new_records(batch_records)
            #             batch_records = []
            #             await asyncio.sleep(0.1)

            # # Process pages
            # self.logger.info(f"Processing pages for site: {site_name}")
            # async for record, permissions, record_update in self._process_site_pages(site_id):
                # if record_update.is_deleted:
                #     await self._handle_record_updates(record_update)
                # elif record:
                #     batch_records.append((record, permissions))
                #     total_processed += 1

                #     if len(batch_records) >= self.batch_size:
                #         await self.data_entities_processor.on_new_records(batch_records)
                #         batch_records = []
                #         await asyncio.sleep(0.1)

            # # Process remaining records
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                pass

            self.logger.info(f"Completed sync for site '{site_name}' - processed {total_processed} items")
            self.stats['sites_processed'] += 1

        except Exception as e:
            site_name = site_record_group.name
            self.logger.error(f"❌ Failed to sync site '{site_name}': {e}")
            self.stats['sites_failed'] += 1
            raise

    async def _process_site_drives(self, site_id: str, internal_site_record_group_id: str) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process all document libraries (drives) in a SharePoint site.
        """
        try:
            async with self.rate_limiter:
                encoded_site_id = self._normalize_site_id(site_id)
                drives_response = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).drives.get()
                )

            if not drives_response or not drives_response.value:
                self.logger.debug(f"No drives found for site {site_id}")
                return

            drives = drives_response.value

            drive_record_groups_with_permissions = []
            for drive in drives:
                    drive_name = getattr(drive, 'name', 'Unknown Drive')
                    drive_id = getattr(drive, 'id', None)

                    if not drive_id:
                        self.logger.warning(f"⚠️ No drive ID found for drive {drive_name}")
                        continue

                    # Create document library record
                    drive_record_group = self._create_document_library_record_group(drive, site_id, internal_site_record_group_id)
                    if drive_record_group:
                        drive_record_groups_with_permissions.append((drive_record_group, []))
                        # permissions = await self._get_drive_permissions(site_id, drive_id)

            self.logger.info(f"Found {len(drive_record_groups_with_permissions)} drive record groups to process.")
            await self.data_entities_processor.on_new_record_groups(drive_record_groups_with_permissions)

            for drive_record_group, _permissions in drive_record_groups_with_permissions:
                # Process items in the drive using delta
                item_count = 0
                self.logger.info(f"Drive record group: {drive_record_group}")
                async for item_tuple in self._process_drive_delta(site_id, drive_record_group.external_group_id):
                    yield item_tuple
                    item_count += 1

        except Exception:
            self.logger.exception(f"❌ Error processing drives for site {site_id}")

    async def _process_drive_delta(self, site_id: str, drive_id: str) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
        """
        Process drive items using delta API for a specific drive.
        """
        try:
            sync_point_key = generate_record_sync_point_key(
                SharePointRecordType.DOCUMENT_LIBRARY.value,
                site_id,
                drive_id
            )
            sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)

            users = await self.data_entities_processor.get_all_active_users()

            # Determine starting point
            delta_url = None
            if sync_point:
                delta_url = sync_point.get('deltaLink') or sync_point.get('nextLink')

            if delta_url:
                # Continue from previous sync point - use the URL as-is

                # Ensure we're not accidentally processing this URL
                self.logger.debug(f"Delta URL for drive_id: {drive_id} is {delta_url}")
                parsed_url = urllib.parse.urlparse(delta_url)
                self.logger.debug(f"Parsed URL for drive_id: {drive_id} is {parsed_url}")
                if not (
                    parsed_url.scheme == 'https' and
                    parsed_url.hostname == 'graph.microsoft.com'
                ):
                    self.logger.error(f"❌ Invalid delta URL format: {delta_url}")
                    # Clear the sync point and start fresh
                    await self.drive_delta_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={"nextLink": None, "deltaLink": None}
                    )
                    delta_url = None
                else:
                    result = await self.msgraph_client.get_delta_response_sharepoint(delta_url)

            if not delta_url:
                self.logger.info(f"No delta URL found for drive_id: {drive_id}, starting fresh delta sync")
                # Start fresh delta sync
                encoded_site_id = self._construct_site_url(site_id)
                root_url = f"https://graph.microsoft.com/v1.0/sites/{encoded_site_id}/drives/{drive_id}/root/delta"
                result = await self.msgraph_client.get_delta_response_sharepoint(root_url)

                if not result:
                    return

            # Process delta changes
            while result:
                drive_items = result.get('drive_items', [])
                if not drive_items:
                    break

                for item in drive_items:
                    try:
                        record_update = await self._process_drive_item(item, site_id, drive_id, users)
                        if record_update:
                            if record_update.is_deleted:
                                yield (None, [], record_update)
                            elif record_update.record:
                                yield (record_update.record, record_update.new_permissions or [], record_update)
                                self.stats['items_processed'] += 1
                    except Exception as item_error:
                        self.logger.error(f"❌ Error processing drive item: {item_error}")
                        continue

                    await asyncio.sleep(0)

                # Handle pagination
                next_link = result.get('next_link')
                if next_link:
                    await self.drive_delta_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={"nextLink": next_link}
                    )
                    result = await self.msgraph_client.get_delta_response_sharepoint(next_link)
                else:
                    delta_link = result.get('delta_link')
                    await self.drive_delta_sync_point.update_sync_point(
                        sync_point_key,
                        sync_point_data={
                            "nextLink": None,
                            "deltaLink": delta_link
                        }
                    )
                    break

        except Exception as e:
            self.logger.error(f"❌ Error processing drive delta for drive {drive_id}: {e}")
            # Clear the sync point to force a fresh start on next attempt
            try:
                await self.drive_delta_sync_point.update_sync_point(
                    sync_point_key,
                    sync_point_data={"nextLink": None, "deltaLink": None}
                )
                self.logger.info(f"✅ Cleared sync point for drive {drive_id} due to error")
            except Exception as clear_error:
                self.logger.error(f"Failed to clear sync point: {clear_error}")

    async def _process_drive_item(self, item: DriveItem, site_id: str, drive_id: str, users: List[AppUser]) -> Optional[RecordUpdate]:
        """
        Process a single drive item from SharePoint.
        """
        try:
            item_name = getattr(item, 'name', 'Unknown Item')
            item_id = getattr(item, 'id', None)

            if not item_id:
                return None

            # Check if item is deleted
            if hasattr(item, 'deleted') and item.deleted is not None:
                return RecordUpdate(
                    record=None,
                    external_record_id=item_id,
                    is_new=False,
                    is_updated=False,
                    is_deleted=True,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False
                )
            existing_record = None
            # Get existing record for change detection
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_name=self.connector_name,
                    external_id=item_id
                )

            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False

            if existing_record:
                # Detect changes
                current_etag = getattr(item, 'e_tag', None)
                if existing_record.external_revision_id != current_etag:
                    metadata_changed = True
                    is_updated = True

                # Check content changes for files
                if hasattr(item, 'file') and item.file and hasattr(item.file, 'hashes') and item.file.hashes:
                    current_hash = getattr(item.file.hashes, 'quick_xor_hash', None)
                    if getattr(existing_record, 'quick_xor_hash', None) != current_hash:
                        content_changed = True
                        is_updated = True

            # Create file record
            file_record = await self._create_file_record(item, drive_id, existing_record)
            if not file_record:
                return None

            # Get permissions
            permissions = await self._get_item_permissions(site_id, drive_id, item_id)

            # Todo: Get permissions for the record
            for user in users:
                permissions.append(Permission(
                    email=user.email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER
                ))

            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=True,
                new_permissions=permissions
            )

        except Exception as e:
            item_name = getattr(item, 'name', 'unknown')
            self.logger.error(f"❌ Error processing drive item '{item_name}': {e}")
            return None

    async def _create_file_record(self, item: DriveItem, drive_id: str, existing_record: Optional[Record]) -> Optional[FileRecord]:
        """
        Create a FileRecord from a DriveItem with comprehensive data extraction.
        """
        try:
            item_name = getattr(item, 'name', 'Unknown Item')
            item_id = getattr(item, 'id', None)

            if not item_id:
                return None

            # Determine if it's a file or folder
            is_file = hasattr(item, 'folder') and item.folder is None
            record_type = RecordType.FILE

            # Get file extension for files
            extension = None
            if is_file and '.' in item_name:
                extension = item_name.split('.')[-1].lower()
            elif not is_file:
                extension = None

            # Skip files without extensions
            if is_file and not extension:
                return None

            # Get timestamps
            created_at = None
            updated_at = None
            if hasattr(item, 'created_date_time') and item.created_date_time:
                created_at = int(item.created_date_time.timestamp() * 1000)
            if hasattr(item, 'last_modified_date_time') and item.last_modified_date_time:
                updated_at = int(item.last_modified_date_time.timestamp() * 1000)

            # Get file hashes
            hashes = {}
            if hasattr(item, 'file') and item.file and hasattr(item.file, 'hashes') and item.file.hashes:
                file_hashes = item.file.hashes
                hashes = {
                    'quick_xor_hash': getattr(file_hashes, 'quick_xor_hash', None),
                    'crc32_hash': getattr(file_hashes, 'crc32_hash', None),
                    'sha1_hash': getattr(file_hashes, 'sha1_hash', None),
                    'sha256_hash': getattr(file_hashes, 'sha256_hash', None)
                }

            # Get download URL for files
            signed_url = None
            if is_file:
                try:
                    signed_url = await self.msgraph_client.get_signed_url(drive_id, item_id)
                except Exception:
                    pass  # Download URL is optional

            # Get parent reference
            parent_id = None
            path = None
            if hasattr(item, 'parent_reference') and item.parent_reference:
                parent_id = getattr(item.parent_reference, 'id', None)
                path = getattr(item.parent_reference, 'path', None)

            return FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=item_name,
                record_type=record_type,
                record_status=RecordStatus.NOT_STARTED if not existing_record else existing_record.record_status,
                record_group_type=RecordGroupType.DRIVE,
                parent_record_type=RecordType.FILE,
                external_record_id=item_id,
                external_revision_id=getattr(item, 'e_tag', None),
                version=0 if not existing_record else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(item, 'web_url', None),
                signed_url=signed_url,
                mime_type=item.file.mime_type if item.file else MimeTypes.FOLDER.value,
                parent_external_record_id=parent_id,
                external_record_group_id=drive_id,
                size_in_bytes=getattr(item, 'size', 0),
                is_file=is_file,
                extension=extension,
                path=path,
                etag=getattr(item, 'e_tag', None),
                ctag=getattr(item, 'c_tag', None),
                quick_xor_hash=hashes.get('quick_xor_hash'),
                crc32_hash=hashes.get('crc32_hash'),
                sha1_hash=hashes.get('sha1_hash'),
                sha256_hash=hashes.get('sha256_hash'),
            )

        except Exception as e:
            self.logger.error(f"❌ Error creating file record: {e}")
            return None

    async def _process_site_lists(self, site_id: str) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process all lists in a SharePoint site.
        """
        try:
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                lists_response = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).lists.get()
                )

            if not lists_response or not lists_response.value:
                self.logger.debug(f"No lists found for site {site_id}")
                return

            lists = lists_response.value
            self.logger.info(f"Found {len(lists)} lists in site")

            for list_obj in lists:
                try:
                    list_name = getattr(list_obj, 'display_name', None) or getattr(list_obj, 'name', 'Unknown List')
                    list_id = getattr(list_obj, 'id', None)

                    if not list_id:
                        continue

                    # Check if list should be skipped
                    if self._should_skip_list(list_obj, list_name):
                        continue

                    # Create list record
                    list_record = await self._create_list_record(list_obj, site_id)
                    if list_record:
                        permissions = await self._get_list_permissions(site_id, list_id)
                        yield (list_record, permissions, RecordUpdate(
                            record=list_record,
                            is_new=True,
                            is_updated=False,
                            is_deleted=False,
                            metadata_changed=False,
                            content_changed=False,
                            permissions_changed=False,
                            new_permissions=permissions
                        ))

                        # Process list items (with limit for performance)
                        item_count = 0
                        max_items_per_list = 1000  # Reasonable limit
                        async for item_tuple in self._process_list_items(site_id, list_id):
                            yield item_tuple
                            item_count += 1
                            if item_count >= max_items_per_list:
                                self.logger.warning(f"⚠️ Reached item limit ({max_items_per_list}) for list '{list_name}'")
                                break

                        self.logger.debug(f"Processed {item_count} items from list '{list_name}'")
                        self.stats['lists_processed'] += 1

                except Exception as list_error:
                    list_name = getattr(list_obj, 'display_name', 'unknown')
                    self.logger.warning(f"⚠️ Error processing list '{list_name}': {list_error}")
                    continue

        except Exception as e:
            self.logger.error(f"❌ Error processing lists for site {site_id}: {e}")

    def _should_skip_list(self, list_obj: dict, list_name: str) -> bool:
        """
        Determine if a list should be skipped based on various criteria.
        """
        # Check if list is hidden
        if hasattr(list_obj, 'list') and list_obj.list:
            if getattr(list_obj.list, 'hidden', False):
                return True
        elif hasattr(list_obj, 'hidden') and list_obj.hidden:
            return True

        # Skip system lists by name patterns
        system_prefixes = ['_', 'form templates', 'workflow', 'master page gallery', 'site assets']
        if any(list_name.lower().startswith(prefix) for prefix in system_prefixes):
            return True

        # Skip by template type
        template_name = None
        if hasattr(list_obj, 'list') and hasattr(list_obj.list, 'template'):
            template_name = str(list_obj.list.template).lower()
        elif hasattr(list_obj, 'template'):
            template_name = str(list_obj.template).lower()

        if template_name:
            system_templates = ['catalog', 'workflow', 'webtemplate', 'masterpage', 'survey']
            if any(tmpl in template_name for tmpl in system_templates):
                return True

        return False

    async def _create_list_record(self, list_obj: dict, site_id: str) -> Optional[SharePointListRecord]:
        """
        Create a record for a SharePoint list.
        """
        try:
            list_id = getattr(list_obj, 'id', None)
            if not list_id:
                return None

            list_name = getattr(list_obj, 'display_name', None) or getattr(list_obj, 'name', 'Unknown List')

            # Get timestamps
            created_at = None
            updated_at = None
            if hasattr(list_obj, 'created_date_time') and list_obj.created_date_time:
                created_at = int(list_obj.created_date_time.timestamp() * 1000)
            if hasattr(list_obj, 'last_modified_date_time') and list_obj.last_modified_date_time:
                updated_at = int(list_obj.last_modified_date_time.timestamp() * 1000)

            # Get list metadata
            metadata = {
                "site_id": site_id,
                "list_template": None,
                "item_count": 0,
            }

            # Try to get template and item count
            if hasattr(list_obj, 'list') and list_obj.list:
                metadata["list_template"] = str(getattr(list_obj.list, 'template', None))
                metadata["item_count"] = getattr(list_obj.list, 'item_count', 0)

            return SharePointListRecord(
                id=str(uuid.uuid4()),
                record_name=list_name,
                record_type=RecordType.SHAREPOINT_LIST,
                record_status=RecordStatus.NOT_STARTED,
                record_group_type=RecordGroupType.SHAREPOINT_SITE,
                parent_record_type=RecordType.SITE,
                external_record_id=list_id,
                external_revision_id=getattr(list_obj, 'e_tag', None),
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(list_obj, 'web_url', None),
                parent_external_record_id=site_id,
                external_record_group_id=site_id,
                metadata=metadata
            )

        except Exception as e:
            self.logger.error(f"❌ Error creating list record: {e}")
            return None

    async def _process_list_items(self, site_id: str, list_id: str) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process items in a SharePoint list with pagination.
        """
        try:
            encoded_site_id = self._construct_site_url(site_id)

            sync_point_key = generate_record_sync_point_key(
                SharePointRecordType.LIST.value,
                site_id,
                list_id
            )
            sync_point = await self.list_sync_point.read_sync_point(sync_point_key)
            skip_token = sync_point.get('skipToken') if sync_point else None

            page_count = 0
            max_pages = 50  # Safety limit

            while page_count < max_pages:
                try:
                    async with self.rate_limiter:
                        if skip_token:
                            items_response = await self._safe_api_call(
                                self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.get(
                                    request_configuration={
                                        "query_parameters": {"$skiptoken": skip_token}
                                    }
                                )
                            )
                        else:
                            items_response = await self._safe_api_call(
                                self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.get()
                            )

                    if not items_response or not items_response.value:
                        break

                    for item in items_response.value:
                        try:
                            list_item_record = await self._create_list_item_record(item, site_id, list_id)
                            if list_item_record:
                                permissions = await self._get_list_item_permissions(site_id, list_id, item.id)
                                yield (list_item_record, permissions, RecordUpdate(
                                    record=list_item_record,
                                    is_new=True,
                                    is_updated=False,
                                    is_deleted=False,
                                    metadata_changed=False,
                                    content_changed=False,
                                    permissions_changed=False,
                                    new_permissions=permissions
                                ))
                        except Exception as item_error:
                            self.logger.error(f"❌ Error processing list item: {item_error}")
                            continue

                    # Handle pagination
                    skip_token = None
                    if hasattr(items_response, 'odata_next_link') and items_response.odata_next_link:
                        try:
                            parsed_url = urllib.parse.urlparse(items_response.odata_next_link)
                            query_params = urllib.parse.parse_qs(parsed_url.query)
                            skip_token = query_params.get('$skiptoken', [None])[0]
                        except Exception:
                            skip_token = None

                    if skip_token:
                        await self.list_sync_point.update_sync_point(
                            sync_point_key,
                            sync_point_data={"skipToken": skip_token}
                        )
                    else:
                        await self.list_sync_point.update_sync_point(
                            sync_point_key,
                            sync_point_data={"skipToken": None}
                        )
                        break

                    page_count += 1

                except Exception as page_error:
                    self.logger.error(f"❌ Error processing page {page_count + 1} of list items: {page_error}")
                    break

        except Exception as e:
            self.logger.error(f"❌ Error processing list items for list {list_id}: {e}")

    async def _create_list_item_record(self, item: ListItem, site_id: str, list_id: str) -> Optional[SharePointListItemRecord]:
        """
        Create a record for a list item.
        """
        try:
            item_id = getattr(item, 'id', None)
            if not item_id:
                return None

            # Extract title from fields
            title = f"Item {item_id}"
            fields_data = {}

            try:
                if hasattr(item, 'fields') and item.fields and hasattr(item.fields, 'additional_data'):
                    fields_data = dict(item.fields.additional_data)
                    title = (fields_data.get('Title') or
                            fields_data.get('LinkTitle') or
                            fields_data.get('Name') or
                            title)
            except Exception:
                pass

            # Get timestamps
            created_at = None
            updated_at = None
            if hasattr(item, 'created_date_time') and item.created_date_time:
                created_at = int(item.created_date_time.timestamp() * 1000)
            if hasattr(item, 'last_modified_date_time') and item.last_modified_date_time:
                updated_at = int(item.last_modified_date_time.timestamp() * 1000)

            # Build metadata
            metadata = {
                "site_id": site_id,
                "list_id": list_id,
                "content_type": getattr(item.content_type, 'name', None) if hasattr(item, 'content_type') and item.content_type else None,
                "fields": fields_data
            }

            return SharePointListItemRecord(
                id=str(uuid.uuid4()),
                record_name=str(title)[:255],
                record_type=RecordType.SHAREPOINT_LIST_ITEM.value,
                record_status=RecordStatus.NOT_STARTED,
                record_group_type=RecordGroupType.SHAREPOINT_LIST.value,
                parent_record_type=RecordType.SHAREPOINT_LIST.value,
                external_record_id=item_id,
                external_revision_id=getattr(item, 'e_tag', None),
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(item, 'web_url', None),
                parent_external_record_id=list_id,
                external_record_group_id=site_id,
                metadata=metadata
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating list item record: {e}")
            return None

    async def _process_site_pages(self, site_id: str) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process all pages in a SharePoint site.
        """
        try:
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                try:
                    pages_response = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).pages.get()
                    )
                except Exception as pages_error:
                    if any(term in str(pages_error).lower() for term in [HttpStatusCode.FORBIDDEN.value, "accessdenied", HttpStatusCode.NOT_FOUND.value, "notfound"]):
                        self.logger.debug(f"Pages not accessible for site {site_id}: {pages_error}")
                        return
                    else:
                        raise pages_error

            if not pages_response or not pages_response.value:
                self.logger.debug(f"No pages found for site {site_id}")
                return

            pages = pages_response.value
            self.logger.debug(f"Found {len(pages)} pages in site")

            for page in pages:
                try:
                    page_record = await self._create_page_record(page, site_id)
                    if page_record:
                        permissions = await self._get_page_permissions(site_id, page.id)
                        yield (page_record, permissions, RecordUpdate(
                            record=page_record,
                            is_new=True,
                            is_updated=False,
                            is_deleted=False,
                            metadata_changed=False,
                            content_changed=False,
                            permissions_changed=False,
                            new_permissions=permissions
                        ))
                        self.stats['pages_processed'] += 1

                except Exception as page_error:
                    page_name = getattr(page, 'title', getattr(page, 'name', 'unknown'))
                    self.logger.warning(f"Error processing page '{page_name}': {page_error}")
                    continue

        except Exception as e:
            self.logger.error(f"❌ Error processing pages for site {site_id}: {e}")

    async def _create_page_record(self, page: SitePage, site_id: str) -> Optional[SharePointPageRecord]:
        """
        Create a record for a SharePoint page.
        """
        try:
            page_id = getattr(page, 'id', None)
            if not page_id:
                return None

            page_name = getattr(page, 'title', None) or getattr(page, 'name', f'Page {page_id}')

            # Get timestamps
            created_at = None
            updated_at = None
            if hasattr(page, 'created_date_time') and page.created_date_time:
                created_at = int(page.created_date_time.timestamp() * 1000)
            if hasattr(page, 'last_modified_date_time') and page.last_modified_date_time:
                updated_at = int(page.last_modified_date_time.timestamp() * 1000)

            # Build metadata
            metadata = {
                "site_id": site_id,
                "page_layout": getattr(page.page_layout, 'type', None) if hasattr(page, 'page_layout') and page.page_layout else None,
                "promotion_kind": getattr(page, 'promotion_kind', None)
            }

            return SharePointPageRecord(
                id=str(uuid.uuid4()),
                record_name=str(page_name)[:255],
                record_type=SharePointRecordType.PAGE.value,
                record_status=RecordStatus.NOT_STARTED,
                record_group_type="SHAREPOINT_SITE",
                parent_record_type="SITE",
                external_record_id=page_id,
                external_revision_id=getattr(page, 'e_tag', None),
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(page, 'web_url', None),
                parent_external_record_id=site_id,
                external_record_group_id=site_id,
                metadata=metadata
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating page record: {e}")
            return None

    def _create_document_library_record_group(self, drive: dict, site_id: str, internal_site_record_group_id: str) -> Optional[RecordGroup]:
        """
        Create a record group for a document library.
        """
        try:
            drive_id = getattr(drive, 'id', None)
            if not drive_id:
                return None

            drive_name = getattr(drive, 'name', 'Unknown Drive')

            # Get timestamps
            source_created_at = None
            source_updated_at = None
            if hasattr(drive, 'created_date_time') and drive.created_date_time:
                source_created_at = int(drive.created_date_time.timestamp() * 1000)
            if hasattr(drive, 'last_modified_date_time') and drive.last_modified_date_time:
                source_updated_at = int(drive.last_modified_date_time.timestamp() * 1000)


            return RecordGroup(
                external_group_id=drive_id,
                name=drive_name,
                group_type=RecordGroupType.DRIVE.value,
                parent_external_group_id=site_id,
                parent_record_group_id=internal_site_record_group_id,
                connector_name=self.connector_name,
                web_url=getattr(drive, 'web_url', None),
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating document library record group: {e}")
            return None

    # Permission methods
    async def _get_site_permissions(self, site_id: str) -> List[Permission]:
        """Get permissions for a SharePoint site using both Graph API and SharePoint REST API."""
        try:
            permissions = []

            # Method 1: Try Graph API permissions first (for explicit Graph API permissions)
            try:
                encoded_site_id = self._construct_site_url(site_id)
                async with self.rate_limiter:
                    perms_response = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).permissions.get()
                    )

                if perms_response and perms_response.value:
                    graph_permissions = await self._convert_to_permissions(perms_response.value)
                    permissions.extend(graph_permissions)
                    self.logger.debug(f"✅ Retrieved {len(graph_permissions)} Graph API permissions for site {site_id}")
            except Exception as graph_error:
                self.logger.debug(f"Graph API permissions failed for site {site_id}: {graph_error}")

            # Method 2: Get SharePoint site groups (the main method for standard SharePoint permissions)
            try:
                site_groups_permissions = await self._get_sharepoint_site_groups_permissions(site_id)
                permissions.extend(site_groups_permissions)
                self.logger.info(f"✅ Retrieved {len(site_groups_permissions)} SharePoint group permissions for site {site_id}")
            except Exception as rest_error:
                self.logger.warning(f"❌ SharePoint REST API permissions failed for site {site_id}: {rest_error}")

            # Method 3: Fallback to role assignments if available
            if not permissions:
                try:
                    role_permissions = await self._get_site_role_assignments(site_id)
                    permissions.extend(role_permissions)
                    self.logger.info(f"✅ Retrieved {len(role_permissions)} role assignment permissions for site {site_id}")
                except Exception as role_error:
                    self.logger.debug(f"Role assignments failed for site {site_id}: {role_error}")

            self.logger.info(f"✅  Total permissions retrieved for site {site_id}: {len(permissions)}")
            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to get site permissions for {site_id}: {e}")
            return []

    async def _get_sharepoint_access_token(self) -> Optional[str]:
        """Get access token for SharePoint REST API."""
        credential = None
        try:
            # Create a new credential instance for SharePoint-specific token
            credential = ClientSecretCredential(
                tenant_id=self.tenant_id,
                client_id=self.client_id,
                client_secret=self.client_secret
            )

            # Parse domain to ensure correct format
            # SharePoint domain should be like: https://company.sharepoint.com
            parsed = urllib.parse.urlparse(self.sharepoint_domain)
            if parsed.hostname:
                resource_host = parsed.hostname
            else:
                # Handle case where domain is just "company.sharepoint.com"
                resource_host = self.sharepoint_domain.replace('https://', '').replace('http://', '').strip('/')

            # Construct SharePoint resource URL (no trailing slash, no path)
            resource = f"https://{resource_host}"

            self.logger.debug(f"Requesting SharePoint token for resource: {resource}/.default")

            # Request token specifically for SharePoint (NOT Graph API)
            token = await credential.get_token(f"{resource}/.default")

            self.logger.info("✅ Successfully obtained SharePoint access token")

            return token.token

        except Exception as e:
            self.logger.error(f"❌ Error getting SharePoint token: {e}")
            self.logger.error("   Make sure your app has SharePoint permissions (Sites.Read.All)")
            self.logger.error(f"   Resource URL: {resource if 'resource' in locals() else 'N/A'}")
            return None
        finally:
            # Close the credential to avoid "Unclosed client session" warnings
            if credential:
                await credential.close()

    async def _get_sharepoint_site_groups_permissions(self, site_id: str) -> List[Permission]:
        """Get SharePoint site groups and their members using SharePoint REST API."""
        try:
            permissions = []

            # Get site metadata to construct the site URL
            site_metadata = self.site_cache.get(site_id)
            if not site_metadata:
                self.logger.warning(f"❌ No site metadata found for site {site_id}")
                return []

            site_url = site_metadata.site_url
            if not site_url:
                self.logger.warning(f"❌ No site URL found for site {site_id}")
                return []

            # Get access token for SharePoint REST API
            access_token = await self._get_sharepoint_access_token()
            if not access_token:
                self.logger.warning("Could not get access token for SharePoint REST API")
                return []

            # Get site groups using REST API
            site_groups = await self._get_sharepoint_site_groups(site_url, access_token)
            if not site_groups:
                self.logger.debug(f"No site groups found for site {site_id}")
                return []

            # Process each group and get its members
            for group in site_groups:
                try:
                    group_id = group.get('Id')
                    group_name = group.get('Title', 'Unknown Group')
                    group_login_name = group.get('LoginName', '')
                    group_permission_type = self._map_sharepoint_group_to_permission_type(group_name, group_login_name)

                    self.logger.debug(f"Processing group '{group_name}' (ID: {group_id}, LoginName: '{group_login_name}') with permission type: {group_permission_type}")

                    # Get group members
                    group_members = await self._get_sharepoint_group_members(site_url, group_id, access_token)

                    for member in group_members:
                        member_id = member.get('Id')
                        member_email = member.get('Email')
                        member_login = member.get('LoginName')
                        member_name = member.get('Title', 'Unknown User')
                        member_principal_type = member.get('PrincipalType', 1)  # 1 = User, 8 = SecurityGroup

                        if member_id:
                            # Determine entity type based on PrincipalType
                            entity_type = EntityType.USER if member_principal_type == 1 else EntityType.GROUP

                            permissions.append(Permission(
                                external_id=str(member_id),
                                email=member_email or member_login,
                                type=group_permission_type,
                                entity_type=entity_type
                            ))

                            self.logger.debug(f"Added permission for {entity_type.value.lower()} '{member_name}' ({member_email or member_login}) in group '{group_name}'")

                except Exception as group_error:
                    self.logger.warning(f"Error processing group '{group.get('Title', 'unknown')}': {group_error}")
                    continue

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Error getting SharePoint site groups permissions: {e}")
            return []

    def _map_sharepoint_group_to_permission_type(self, group_name: str, group_login_name: str = "") -> PermissionType:
        """Map SharePoint group names and login names to permission types."""
        if not group_name and not group_login_name:
            return PermissionType.READ

        # Combine both name and login name for matching
        combined_name = f"{group_name} {group_login_name}".lower()

        # Site Owners patterns
        owner_patterns = [
            'owner', 'owners', 'admin', 'administrator', 'fullcontrol', 'full control',
            'site owners', 'siteowners', '_o', 'owners group'
        ]
        if any(pattern in combined_name for pattern in owner_patterns):
            return PermissionType.OWNER

        # Site Members patterns (Contributors/Edit permissions)
        member_patterns = [
            'member', 'members', 'contributor', 'contributors', 'editor', 'editors',
            'site members', 'sitemembers', '_m', 'members group', 'contribute', 'edit'
        ]
        if any(pattern in combined_name for pattern in member_patterns):
            return PermissionType.WRITE

        # Site Visitors patterns (Read-only)
        visitor_patterns = [
            'visitor', 'visitors', 'reader', 'readers', 'read only', 'readonly',
            'site visitors', 'sitevisitors', '_v', 'visitors group', 'view only', 'viewonly'
        ]
        if any(pattern in combined_name for pattern in visitor_patterns):
            return PermissionType.READ

        # Special SharePoint groups
        if 'everyone except external users' in combined_name:
            return PermissionType.READ
        elif 'everyone' in combined_name:
            return PermissionType.READ
        elif 'limited access' in combined_name:
            return PermissionType.READ

        # Default to read for unknown groups
        self.logger.debug(f"Unknown SharePoint group pattern, defaulting to READ: '{group_name}' (LoginName: '{group_login_name}')")
        return PermissionType.READ

    async def _get_sharepoint_site_groups(self, site_url: str, access_token: str) -> List[Dict]:
        """Get SharePoint site groups using REST API."""
        try:
            # Construct the REST API URL for site groups
            rest_url = f"{site_url.rstrip('/')}/_api/web/sitegroups"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json;odata=verbose',
                'Content-Type': 'application/json'
            }

            # Apply rate limiting
            async with self.rate_limiter:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(rest_url, headers=headers)
                    response.raise_for_status()

                    data = response.json()
                    groups = data.get('d', {}).get('results', [])

                    self.logger.debug(f"Retrieved {len(groups)} site groups from {site_url}")

                    # Log group details for debugging
                    for group in groups:
                        self.logger.debug(f"Found group: '{group.get('Title')}' (LoginName: '{group.get('LoginName')}', ID: {group.get('Id')})")

                    return groups

        except httpx.HTTPStatusError as e:
            if e.response.status_code == HttpStatusCode.FORBIDDEN.value:
                self.logger.warning(f"Access denied when getting site groups from {site_url}: {e}")
            elif e.response.status_code == HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(f"Site groups endpoint not found for {site_url}: {e}")
            elif e.response.status_code == HttpStatusCode.UNAUTHORIZED.value:
                self.logger.error(
                    (
                        f"❌ Unauthorized when getting site groups from {site_url}: {e}. "
                        f"This usually indicates the token is for the wrong audience/resource or missing SharePoint app permissions. "
                        f"Ensure your Azure AD app has SharePoint application permissions (e.g., Sites.Read.All or Sites.FullControl.All) "
                        f"with admin consent, and that the token is requested for 'https://<tenant>.sharepoint.com/.default' (not Microsoft Graph)."
                    )
                )
            else:
                self.logger.error(f"❌ HTTP error getting SharePoint site groups from {site_url}: {e}")
            return []
        except Exception as e:
            self.logger.error(f"Error getting SharePoint site groups from {site_url}: {e}")
            return []

    async def _get_site_role_assignments(self, site_id: str) -> List[Permission]:
        """Get site role assignments as a fallback method."""
        try:
            permissions = []
            site_metadata = self.site_cache.get(site_id)

            if not site_metadata:
                return []

            site_url = site_metadata.site_url
            access_token = await self._get_sharepoint_access_token()

            if not site_url or not access_token:
                return []
            self.logger.info(f"Site URL: {site_url}")
            # Do not log raw access tokens; just confirm retrieval
            self.logger.debug("Obtained SharePoint access token for role assignments")
            # Get role assignments
            rest_url = f"{site_url.rstrip('/')}/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json;odata=verbose',
                'Content-Type': 'application/json'
            }

            async with self.rate_limiter:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(rest_url, headers=headers)
                    response.raise_for_status()

                    data = response.json()
                    role_assignments = data.get('d', {}).get('results', [])

                    for assignment in role_assignments:
                        try:
                            member = assignment.get('Member', {})
                            role_bindings = assignment.get('RoleDefinitionBindings', {}).get('results', [])

                            member_id = member.get('Id')
                            member_title = member.get('Title', 'Unknown')
                            member_login = member.get('LoginName', '')
                            member_type = member.get('PrincipalType', 1)

                            # Determine permission type from role definitions
                            permission_type = PermissionType.READ
                            for role_def in role_bindings:
                                role_name = role_def.get('Name', '').lower()
                                if role_name in ['full control', 'site owner']:
                                    permission_type = PermissionType.OWNER
                                    break
                                elif role_name in ['contribute', 'edit', 'design']:
                                    permission_type = PermissionType.WRITE
                                    break

                            entity_type = EntityType.USER if member_type == 1 else EntityType.GROUP

                            permissions.append(Permission(
                                external_id=str(member_id),
                                email=member_login if '@' in member_login else None,
                                type=permission_type,
                                entity_type=entity_type
                            ))

                            self.logger.debug(f"Added role assignment permission for {entity_type.value.lower()} '{member_title}' with type {permission_type}")

                        except Exception as assignment_error:
                            self.logger.debug(f"❌ Error processing role assignment: {assignment_error}")
                            continue

                    return permissions

        except Exception as e:
            self.logger.debug(f"❌ Error getting role assignments for site {site_id}: {e}")
            return []

    async def _get_sharepoint_site_groups(self, site_url: str, access_token: str) -> List[Dict]:
        """Get site groups via SharePoint REST API."""
        try:
            self.logger.info(f"Site URL: {site_url}")
            rest_url = f"{site_url.rstrip('/')}/_api/web/sitegroups"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json;odata=verbose'
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(rest_url, headers=headers)

                if response.status_code == HttpStatusCode.UNAUTHORIZED.value:
                    self.logger.error(f"❌ 401 Unauthorized for {rest_url}")
                    self.logger.error("   This usually means the token audience is wrong.")
                    self.logger.error(f"   Token must be requested for '{site_url.split('/')[2]}/.default'")
                    self.logger.error("   NOT for 'https://graph.microsoft.com/.default'")
                    return []

                response.raise_for_status()

                data = response.json()
                groups = data.get('d', {}).get('results', [])

                return groups

        except httpx.HTTPStatusError as e:
            self.logger.error(f"❌ HTTP {e.response.status_code} error getting site groups: {e}")
            return []
        except Exception as e:
            self.logger.error(f"❌ Error getting site groups: {e}")
            return []

    async def _get_sharepoint_group_members(self, site_url: str, group_id: int, access_token: str) -> List[Dict]:
        """Get members of a SharePoint group using REST API."""
        try:
            # Construct the REST API URL
            rest_url = f"{site_url}/_api/web/sitegroups({group_id})/users"

            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json;odata=verbose',
                'Content-Type': 'application/json'
            }

            # Apply rate limiting
            async with self.rate_limiter:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(rest_url, headers=headers)
                    response.raise_for_status()

                    data = response.json()
                    members = data.get('d', {}).get('results', [])

                    self.logger.debug(f"Retrieved {len(members)} members for group {group_id}")
                    return members

        except httpx.HTTPStatusError as e:
            if e.response.status_code == HttpStatusCode.FORBIDDEN.value:
                self.logger.warning(f"Access denied when getting group members for group {group_id}: {e}")
            elif e.response.status_code == HttpStatusCode.NOT_FOUND.value:
                self.logger.warning(f"Group members endpoint not found for group {group_id}: {e}")
            else:
                self.logger.error(f"❌ HTTP error getting SharePoint group members for group {group_id}: {e}")
            return []
        except Exception as e:
            self.logger.error(f"❌ Error getting SharePoint group members for group {group_id}: {e}")
            return []

    async def _get_drive_permissions(self, site_id: str, drive_id: str) -> List[Permission]:
        """Get permissions for a document library."""
        try:
            permissions = []
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                # Use the correct Graph API structure for drive permissions
                # For SharePoint, we need to get the root item first, then its permissions
                root_item = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).root.get()
                )
                if root_item:
                    perms_response = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).items.by_drive_item_id(root_item.id).permissions.get()
                    )
                else:
                    perms_response = None

            if perms_response and perms_response.value:
                permissions = await self._convert_to_permissions(perms_response.value)

            return permissions

        except Exception as e:
            self.logger.debug(f"❌ Could not get drive permissions: {e}")
            return []

    async def _get_item_permissions(self, site_id: str, drive_id: str, item_id: str) -> List[Permission]:
        """Get permissions for a drive item."""
        try:
            permissions = []
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                perms_response = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).permissions.get()
                )

            if perms_response and perms_response.value:
                permissions = await self._convert_to_permissions(perms_response.value)

            return permissions

        except Exception as e:
            self.logger.debug(f"❌ Could not get item permissions: {e}")
            return []

    async def _get_list_permissions(self, site_id: str, list_id: str) -> List[Permission]:
        """Get permissions for a SharePoint list."""
        try:
            # SharePoint lists don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint lists
            self.logger.debug(f"List permissions not directly accessible via Graph API for list {list_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get list permissions: {e}")
            return []

    async def _get_list_item_permissions(self, site_id: str, list_id: str, item_id: str) -> List[Permission]:
        """Get permissions for a list item."""
        try:
            # SharePoint list items don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint list items
            self.logger.debug(f"List item permissions not directly accessible via Graph API for item {item_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get list item permissions: {e}")
            return []

    async def _get_page_permissions(self, site_id: str, page_id: str) -> List[Permission]:
        """Get permissions for a SharePoint page."""
        try:
            # SharePoint pages don't have direct permissions endpoint
            # Instead, we'll return site-level permissions or empty list
            # This is a limitation of the Microsoft Graph API for SharePoint pages
            self.logger.debug(f"Page permissions not directly accessible via Graph API for page {page_id}")
            return []

        except Exception as e:
            self.logger.debug(f"❌ Could not get page permissions: {e}")
            return []

    async def _convert_to_permissions(self, msgraph_permissions: List) -> List[Permission]:
        """Convert Microsoft Graph permissions to our Permission model."""
        permissions = []

        for perm in msgraph_permissions:
            try:
                # Handle user permissions
                if hasattr(perm, 'granted_to') and perm.granted_to:
                    if hasattr(perm.granted_to, 'user') and perm.granted_to.user:
                        user = perm.granted_to.user
                        permissions.append(Permission(
                            external_id=user.id,
                            email=getattr(user, 'mail', None) or getattr(user, 'user_principal_name', None),
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                            entity_type=EntityType.USER
                        ))

                # Handle group permissions
                if hasattr(perm, 'granted_to_identities') and perm.granted_to_identities:
                    for identity in perm.granted_to_identities:
                        try:
                            if hasattr(identity, 'group') and identity.group:
                                group = identity.group
                                permissions.append(Permission(
                                    external_id=group.id,
                                    email=getattr(group, 'mail', None),
                                    type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                    entity_type=EntityType.GROUP
                                ))
                            elif hasattr(identity, 'user') and identity.user:
                                user = identity.user
                                permissions.append(Permission(
                                    external_id=user.id,
                                    email=getattr(user, 'mail', None) or getattr(user, 'user_principal_name', None),
                                    type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                    entity_type=EntityType.USER
                                ))
                        except Exception:
                            continue

                # Handle link permissions
                if hasattr(perm, 'link') and perm.link:
                    link = perm.link
                    if hasattr(link, 'scope'):
                        if link.scope == "anonymous":
                            permissions.append(Permission(
                                external_id="anyone_with_link",
                                email=None,
                                type=map_msgraph_role_to_permission_type(getattr(link, 'type', 'read')),
                                entity_type=EntityType.ANYONE_WITH_LINK
                            ))
                        elif link.scope == "organization":
                            permissions.append(Permission(
                                external_id="anyone_in_org",
                                email=None,
                                type=map_msgraph_role_to_permission_type(getattr(link, 'type', 'read')),
                                entity_type=EntityType.ORG
                            ))

                # Handle invitation permissions
                if hasattr(perm, 'invitation') and perm.invitation:
                    invitation = perm.invitation
                    if hasattr(invitation, 'email') and invitation.email:
                        permissions.append(Permission(
                            external_id=invitation.email,
                            email=invitation.email,
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                            entity_type=EntityType.USER
                        ))

            except Exception:
                continue

        return permissions

    def _parse_datetime(self, dt_obj) -> Optional[int]:
        """Parse datetime object or string to epoch timestamp in milliseconds."""
        if not dt_obj:
            return None
        try:
            if isinstance(dt_obj, str):
                dt = datetime.fromisoformat(dt_obj.replace('Z', '+00:00'))
            else:
                dt = dt_obj
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    # User and group sync methods
    async def _sync_user_groups(self) -> None:
        """Sync SharePoint groups and their members."""
        try:
            self.logger.info("Starting SharePoint group synchronization")

            # Get Microsoft 365 Groups instead of trying to access site groups directly
            # Microsoft Graph API doesn't expose site groups directly through sites.groups
            total_groups = 0
            group_with_members = []

            try:
                groups = await self.msgraph_client.get_all_user_groups()
                self.logger.debug(f"Groups type: {type(groups)}")
                self.logger.debug(f"Groups: {groups}")
                for group in groups:
                        # Get group members
                        user_group = AppUserGroup(
                            id=str(uuid.uuid4()),
                            source_user_group_id=group.id,
                            app_name=self.connector_name,
                            name=group.display_name,
                            mail=group.mail,
                            description=group.description,
                            created_at_timestamp=self._parse_datetime(group.created_date_time),
                        )
                        self.logger.info(f"User group: {user_group}")

                        app_users = []
                        try:
                            members = await self.msgraph_client.get_group_members(group.id)
                            self.logger.info(f"Members: {members}")
                            for member in members:
                                app_users.append(AppUser(
                                    source_user_id=member.id,
                                    email=member.mail or member.user_principal_name,
                                    full_name=member.display_name,
                                    created_at_timestamp=self._parse_datetime(member.created_date_time),
                                    app_name=self.connector_name,
                                ))
                        except Exception as member_error:
                            self.logger.info(f"❌ Error getting members for group {group.display_name}: {member_error}")
                            # Continue with empty permissions
                        self.logger.info(f"Member permissions: {app_users}")
                        group_with_members.append((user_group, app_users))
                        total_groups += 1

                # Process all collected groups
                if group_with_members:
                    await self.data_entities_processor.on_new_user_groups(
                        group_with_members
                    )

            except Exception as groups_error:
                self.logger.error(f"❌ Error getting Microsoft 365 Groups: {groups_error}")

            # Also try to get site permissions which might include group information
            # try:
            #     sites = await self._get_all_sites()
            #     for site in sites:
            #         try:
            #             encoded_site_id = self._construct_site_url(site.id)

            #             # Get site permissions which might include group information
            #             async with self.rate_limiter:
            #                 permissions_response = await self._safe_api_call(
            #                     self.client.sites.by_site_id(encoded_site_id).permissions.get()
            #                 )

            #             if permissions_response and permissions_response.value:
            #                 for permission in permissions_response.value:
            #                     # Check if this permission is for a group
            #                     if hasattr(permission, 'granted_to_identities') and permission.granted_to_identities:
            #                         for identity in permission.granted_to_identities:
            #                             if hasattr(identity, 'application') and identity.application:
            #                                 # This is a group permission
            #                                 group_name = getattr(identity.application, 'display_name', 'Unknown Group')
            #                                 user_group = {
            #                                     "id": str(uuid.uuid4()),
            #                                     "name": group_name,
            #                                     "source_user_group_id": getattr(identity.application, 'id', str(uuid.uuid4())),
            #                                     "email": None,
            #                                     "description": f"Site permission group for {site.display_name or site.name}",
            #                                     "metadata": {
            #                                         "site_id": site.id,
            #                                         "site_name": site.display_name or site.name,
            #                                         "group_type": "SITE_PERMISSION_GROUP",
            #                                         "permission_level": getattr(permission, 'roles', ['Read'])
            #                                     }
            #                                 }

            #                                 await self.data_entities_processor.on_new_user_groups(
            #                                     [user_group],
            #                                     []  # No member permissions for site permission groups
            #                                 )
            #                                 total_groups += 1

                    # except Exception as site_error:
                    #     self.logger.debug(f"❌ Error processing permissions for site {site.display_name or site.name}: {site_error}")
                    #     continue

            except Exception as sites_error:
                self.logger.error(f"❌ Error processing sites for permissions: {sites_error}")

            self.logger.info(f"Completed SharePoint group synchronization - processed {total_groups} groups")

        except Exception as e:
            self.logger.error(f"❌ Error syncing SharePoint groups: {e}")

    def _map_group_to_permission_type(self, group_name: str) -> PermissionType:
        """Map SharePoint group names to permission types."""
        if not group_name:
            return PermissionType.READ

        group_name_lower = group_name.lower()

        if any(term in group_name_lower for term in ['owner', 'admin', 'full control']):
            return PermissionType.WRITE
        elif any(term in group_name_lower for term in ['member', 'contributor', 'editor']):
            return PermissionType.WRITE
        else:
            return PermissionType.READ

    # Record update handling
    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Handle different types of record updates."""
        try:
            if record_update.is_deleted:
                await self.data_entities_processor.on_record_deleted(
                    record_id=record_update.external_record_id
                )
            elif record_update.is_updated:
                if record_update.content_changed:
                    await self.data_entities_processor.on_record_content_update(record_update.record)
                if record_update.metadata_changed:
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
                if record_update.permissions_changed:
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
        except Exception as e:
            self.logger.error(f"❌ Error handling record updates: {e}")

    async def run_sync(self) -> None:
        """Main entry point for the SharePoint connector."""
        start_time = datetime.now()

        try:
            self.logger.info("🚀 Starting SharePoint connector sync")

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            try:
                users = await self.msgraph_client.get_all_users()
                await self.data_entities_processor.on_new_app_users(users)
                self.logger.info(f"✅ Successfully synced {len(users)} users")
            except Exception as user_error:
                self.logger.error(f"❌ Error syncing users: {user_error}")

            # Step 2: Sync user groups
            # self.logger.info("Syncing SharePoint groups...")
            # try:
            #     await self._sync_user_groups()
            #     self.logger.info("✅ Successfully synced SharePoint groups")
            # except Exception as group_error:
            #     self.logger.error(f"❌ Error syncing groups: {group_error}")

            # Step 3: Discover and sync sites
            sites = await self._get_all_sites()

            if not sites:
                self.logger.warning("⚠️ No SharePoint sites found - check permissions")
                return

            self.logger.info(f"📁 Found {len(sites)} SharePoint sites to sync")
            # Create site record groups
            site_record_groups_with_permissions = []
            for site in sites:
                if not self._validate_site_id(site.id):
                    self.logger.warning(f"Invalid site ID format: '{site.id}'")
                    continue
                if not site.name and not site.display_name:
                    self.logger.warning(f"Site name is empty: '{site.id}'")
                    continue

                site_name = site.display_name or site.name
                site_id = site.id
                source_created_at = int(site.created_date_time.timestamp() * 1000) if site.created_date_time else None
                source_updated_at = int(site.last_modified_date_time.timestamp() * 1000) if site.last_modified_date_time else source_created_at
                # Create site record group
                site_record_group = RecordGroup(
                    name=site_name,
                    short_name=site.name,
                    description=getattr(site, 'description', None),
                    external_group_id=site_id,
                    connector_name=self.connector_name,
                    web_url=site.web_url,
                    group_type=RecordGroupType.SHAREPOINT_SITE,
                    source_created_at=source_created_at,
                    source_updated_at=source_updated_at
                )
                site_record_groups_with_permissions.append((site_record_group, []))
                # Get site permissions
                # site_permissions = await self._get_site_permissions(site_id)
                # self.logger.debug(f"Site permissions: '{site_permissions}'")
                # Process site record group

            await self.data_entities_processor.on_new_record_groups(site_record_groups_with_permissions)
            # Step 4: Process sites in batches
            for i in range(0, len(site_record_groups_with_permissions), self.max_concurrent_batches):
                batch = site_record_groups_with_permissions[i:i + self.max_concurrent_batches]
                batch_start = i + 1
                batch_end = min(i + len(batch), len(site_record_groups_with_permissions))

                self.logger.info(f"📊 Processing site batch {batch_start}-{batch_end} of {len(site_record_groups_with_permissions)}")

                # Process batch
                tasks = [self._sync_site_content(site_record_group) for site_record_group, _permissions in batch]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Count results
                for idx, result in enumerate(results):
                    if isinstance(result, Exception):
                        self.logger.error(f"❌ Site sync failed: {batch[idx][0].name}")
                    else:
                        self.logger.info(f"✅ Site sync completed: {batch[idx][0].name}")

                # Brief pause between batches
                if batch_end < len(site_record_groups_with_permissions):
                    await asyncio.sleep(2)

            # Final statistics
            duration = datetime.now() - start_time
            self.logger.info(f"🎉 SharePoint connector sync completed in {duration}")
            self.logger.info(f"📈 Statistics: {self.stats}")

        except Exception as e:
            duration = datetime.now() - start_time
            self.logger.error(f"💥 Critical error in SharePoint connector after {duration}: {e}")
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync for SharePoint content."""
        try:
            self.logger.info("🔄 Starting SharePoint incremental sync")

            sites = await self._get_all_sites()

            for site in sites:
                try:
                    await self._sync_site_content(site)
                except Exception as site_error:
                    self.logger.error(f"❌ Error in incremental sync for site {site.display_name or site.name}: {site_error}")
                    continue

            self.logger.info("✅ SharePoint incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ Error in SharePoint incremental sync: {e}")
            raise

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to SharePoint."""
        try:
            self.logger.info("Testing connection and access to SharePoint")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error testing connection and access to SharePoint: {e}")
            return False

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from SharePoint."""

        if record.record_type != RecordType.FILE:
            raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail="File not found or access denied")

        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found or access denied")

        return StreamingResponse(
            stream_content(signed_url),
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )

    # Utility methods
    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications from Microsoft Graph for SharePoint."""
        try:
            self.logger.info("📬 Processing SharePoint webhook notification")

            resource = notification.get('resource', '')
            notification.get('changeType', '')

            if 'sites' in resource:
                # Extract site ID and process
                parts = resource.split('/')
                site_idx = parts.index('sites') if 'sites' in parts else -1
                if site_idx >= 0 and site_idx + 1 < len(parts):
                    site_id = parts[site_idx + 1]

                    async with self.rate_limiter:
                        site = await self._safe_api_call(
                            self.client.sites.by_site_id(site_id).get()
                        )

                    if site:
                        await self._sync_site_content(site)

            self.logger.info("✅ SharePoint webhook notification processed")

        except Exception as e:
            self.logger.error(f"❌ Error handling SharePoint webhook notification: {e}")

    async def get_signed_url(self, record: Record) -> str:
        """Create a signed URL for a specific SharePoint record."""
        try:
            if record.record_type != RecordType.FILE:
                return None

            drive_id = record.external_record_group_id

            if not drive_id:
                self.logger.error(f"Missing drive_id for record {record.id}")
                return None

            # Get download URL
            signed_url = await self.msgraph_client.get_signed_url(drive_id, record.external_record_id)
            return signed_url

        except Exception as e:
            self.logger.error(f"❌ Error creating signed URL for record {record.id}: {e}")
            raise

    async def cleanup(self) -> None:
        """Cleanup resources when shutting down the connector."""
        try:
            self.logger.info("🧹 Starting SharePoint connector cleanup")

            # Clear caches
            if hasattr(self, 'site_cache'):
                self.site_cache.clear()

            # Close client connections
            if hasattr(self, 'client') and self.client:
                try:
                    if hasattr(self.client, '_credential'):
                        credential = self.client._credential
                        if hasattr(credential, 'close'):
                            await credential.close()
                except Exception as client_error:
                    self.logger.debug(f"❌ Error closing Graph client: {client_error}")
                finally:
                    self.client = None

            # Clean up MSGraph client
            if hasattr(self, 'msgraph_client') and self.msgraph_client:
                try:
                    if hasattr(self.msgraph_client, 'cleanup'):
                        await self.msgraph_client.cleanup()
                except Exception as msgraph_error:
                    self.logger.debug(f"❌ Error cleaning up MSGraph client: {msgraph_error}")

            self.logger.info("✅ SharePoint connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during SharePoint connector cleanup: {e}")

    @classmethod
    async def create_connector(cls, logger: Logger,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return SharePointConnector(logger, data_entities_processor, data_store_provider, config_service)

# Subscription manager for webhook handling
class SharePointSubscriptionManager:
    """Manages webhook subscriptions for SharePoint change notifications."""

    def __init__(self, msgraph_client: MSGraphClient, logger: Logger) -> None:
        self.client = msgraph_client
        self.logger = logger
        self.subscriptions: Dict[str, str] = {}

    async def create_site_subscription(self, site_id: str, notification_url: str) -> Optional[str]:
        """Create a subscription for SharePoint site changes."""
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription = Subscription(
                change_type="updated",
                notification_url=notification_url,
                resource=f"sites/{site_id}",
                expiration_date_time=expiration_datetime,
                client_state="SharePointConnector"
            )

            result = await self.client.subscriptions.post(subscription)

            if result and result.id:
                self.subscriptions[f"sites/{site_id}"] = result.id
                self.logger.info(f"Created subscription {result.id} for site {site_id}")
                return result.id

            return None

        except Exception as e:
            self.logger.error(f"❌ Error creating subscription for site {site_id}: {e}")
            return None

    async def create_drive_subscription(self, site_id: str, drive_id: str, notification_url: str) -> Optional[str]:
        """Create a subscription for document library changes."""
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription = Subscription(
                change_type="updated",
                notification_url=notification_url,
                resource=f"sites/{site_id}/drives/{drive_id}/root",
                expiration_date_time=expiration_datetime,
                client_state="SharePointConnector"
            )

            result = await self.client.subscriptions.post(subscription)

            if result and result.id:
                resource_key = f"sites/{site_id}/drives/{drive_id}"
                self.subscriptions[resource_key] = result.id
                self.logger.info(f"Created subscription {result.id} for drive {drive_id}")
                return result.id

            return None

        except Exception as e:
            self.logger.error(f"❌ Error creating subscription for drive {drive_id}: {e}")
            return None

    async def renew_subscription(self, subscription_id: str) -> bool:
        """Renew an existing subscription."""
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription_update = Subscription(
                expiration_date_time=expiration_datetime
            )

            await self.client.subscriptions.by_subscription_id(subscription_id).patch(subscription_update)
            self.logger.info(f"Renewed subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error renewing subscription {subscription_id}: {e}")
            return False

    async def delete_subscription(self, subscription_id: str) -> bool:
        """Delete a subscription."""
        try:
            await self.client.subscriptions.by_subscription_id(subscription_id).delete()

            # Remove from tracking
            resource = next((k for k, v in self.subscriptions.items() if v == subscription_id), None)
            if resource:
                del self.subscriptions[resource]

            self.logger.info(f"Deleted subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting subscription {subscription_id}: {e}")
            return False

    async def cleanup_subscriptions(self) -> None:
        """Clean up all subscriptions during shutdown."""
        try:
            self.logger.info("Cleaning up SharePoint subscriptions")

            for subscription_id in list(self.subscriptions.values()):
                await self.delete_subscription(subscription_id)

            self.subscriptions.clear()
            self.logger.info("SharePoint subscription cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during subscription cleanup: {e}")
