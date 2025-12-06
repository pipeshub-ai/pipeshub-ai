import asyncio
import base64
import os
import re
import tempfile
import traceback
import urllib.parse
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from http import HTTPStatus
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import httpx
from aiolimiter import AsyncLimiter
from azure.identity.aio import CertificateCredential, ClientSecretCredential
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from msgraph import GraphServiceClient
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.list_item import ListItem
from msgraph.generated.models.site import Site
from msgraph.generated.models.site_page import SitePage
from msgraph.generated.models.subscription import Subscription

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
    ProgressStatus,
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
    ConnectorBuilder,
    DocumentationLink,
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
    RecordType,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

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
    certificate_path: Optional[str] = None  # Path to certificate.pem file
    certificate_data: Optional[str] = None  # Raw certificate content (alternative to path)


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
        # .add_auth_field(AuthField(
        #     name="clientSecret",
        #     display_name="Client Secret",
        #     placeholder="Enter your Azure AD Client Secret",
        #     description="The Client Secret from Azure AD App Registration (Optional if using certificate)",
        #     field_type="PASSWORD",
        #     is_secret=True,
        #     required=False
        # ))
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
    )\
    .build_decorator()
class SharePointConnector(BaseConnector):
    """
    Complete SharePoint Online Connector implementation with robust error handling,
    proper URL encoding, and comprehensive data synchronization.
    Supports both Client Secret and Certificate-based authentication.
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

        self.filters = {"exclude_onedrive_sites": True, "exclude_pages": True, "exclude_lists": True, "exclude_document_libraries": False}
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
        """Initialize SharePoint connector with certificate or client secret authentication."""

        # Load configuration from service
        config = await self.config_service.get_config("/services/connectors/sharepointonline/config") or \
                            await self.config_service.get_config(f"/services/connectors/sharepointonline/config/{self.data_entities_processor.org_id}")

        if not config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ValueError("SharePoint Online credentials not found")

        credentials_config = config.get("auth", {})
        if not credentials_config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ValueError("SharePoint Online credentials not found")

        # Load credentials from config
        tenant_id = credentials_config.get("tenantId")
        client_id = credentials_config.get("clientId")
        client_secret = credentials_config.get("clientSecret")
        sharepoint_domain = credentials_config.get("sharepointDomain")

        # Load certificate data from config
        # Try both field names for backward compatibility
        certificate_data = credentials_config.get("certificate")
        private_key_data = credentials_config.get("privateKey")

        # Debug logging
        self.logger.debug(f"🔍 Certificate data present: {bool(certificate_data)}")
        self.logger.debug(f"🔍 Private key data present: {bool(private_key_data)}")
        self.logger.debug(f"🔍 Client secret present: {bool(client_secret)}")

        # Normalize SharePoint domain to scheme+host (no path)
        try:
            parsed_domain = urllib.parse.urlparse(sharepoint_domain or "")
            host = parsed_domain.hostname
            scheme = parsed_domain.scheme or "https"
            if not host:
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

        # Validation
        if not all((tenant_id, client_id, sharepoint_domain)):
            self.logger.error("❌ Incomplete SharePoint Online credentials. Ensure tenantId, clientId, and sharepointDomain are configured.")
            raise ValueError("Incomplete SharePoint Online credentials. Ensure tenantId, clientId, and sharepointDomain are configured.")

        # Check for one valid authentication method
        has_certificate = certificate_data and private_key_data
        has_client_secret = client_secret

        if not (has_certificate or has_client_secret):
            self.logger.error("❌ Authentication credentials missing. Provide either clientSecret or certificate + private key.")
            raise ValueError("Authentication credentials missing. Provide either clientSecret or certificate + private key.")

        has_admin_consent = credentials_config.get("hasAdminConsent", False)

        credentials = SharePointCredentials(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            sharepoint_domain=normalized_sharepoint_domain,
            has_admin_consent=has_admin_consent,
        )

        # Store class attributes
        self.sharepoint_domain = credentials.sharepoint_domain
        self.tenant_id = credentials.tenant_id
        self.client_id = credentials.client_id
        self.client_secret = credentials.client_secret

        # Initialize credential based on available authentication method
        self.temp_cert_file = None

        if has_certificate:
            try:
                # Decode certificate and private key if they're base64 encoded
                if isinstance(certificate_data, str):
                    # Check if it's base64 encoded or raw PEM
                    if not certificate_data.strip().startswith("-----BEGIN CERTIFICATE-----"):

                        certificate_pem = base64.b64decode(certificate_data).decode('utf-8')
                    else:
                        certificate_pem = certificate_data
                elif certificate_data is not None:
                    # Explicitly reject non-string types as requested
                    raise TypeError(f"Certificate data must be a string, but received type {type(certificate_data)}")
                else:
                    # Should technically be unreachable due to has_certificate check, but safe to keep
                    raise ValueError("Certificate data is missing")

                if isinstance(private_key_data, str):
                    # Check if it's base64 encoded or raw PEM
                    if not private_key_data.strip().startswith("-----BEGIN PRIVATE KEY-----"):
                        private_key_pem = base64.b64decode(private_key_data).decode('utf-8')
                    else:
                        private_key_pem = private_key_data
                elif private_key_data is not None:
                    # Explicitly reject non-string types
                    raise TypeError(f"Private key data must be a string, but received type {type(private_key_data)}")
                else:
                    raise ValueError("Private key data is missing")

                # Azure SDK requires certificate and private key in a SINGLE PEM file
                # Combine them: private key first, then certificate
                combined_pem = f"{private_key_pem.strip()}\n{certificate_pem.strip()}\n"

                # Create a single temporary file with both private key and certificate
                self.temp_cert_file = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)

                # Write combined PEM to temp file
                self.temp_cert_file.write(combined_pem)
                self.temp_cert_file.flush()
                self.temp_cert_file.close()

                self.logger.info(f"✅ Created combined PEM file at: {self.temp_cert_file.name}")

                # Create credential with the combined certificate file
                self.credential = CertificateCredential(
                    tenant_id=credentials.tenant_id,
                    client_id=credentials.client_id,
                    certificate_path=self.temp_cert_file.name,
                )

                # Store path for later use in REST API calls
                self.certificate_path = self.temp_cert_file.name
                self.certificate_password = None

                self.logger.info("✅ Using CertificateCredential for MS Graph client.")
            except Exception as cert_error:
                self.logger.error(f"❌ Error setting up certificate authentication: {cert_error}")
                self.logger.error(f"Traceback: {traceback.format_exc()}")
                # Clean up temp file if created
                if self.temp_cert_file:
                    try:
                        os.unlink(self.temp_cert_file.name)
                    except OSError:  # <--- The fix
                        pass
                raise ValueError(f"Failed to set up certificate authentication: {cert_error}")

        elif has_client_secret:
            self.credential = ClientSecretCredential(
                tenant_id=credentials.tenant_id,
                client_id=credentials.client_id,
                client_secret=credentials.client_secret,
            )
            self.certificate_path = None
            self.certificate_password = None
            self.logger.info("✅ Using ClientSecretCredential for MS Graph client.")
        else:
            # Should be caught by the earlier check, but kept for robustness
            raise ValueError("No valid credential (Certificate or Client Secret) found.")

        # Pre-initialize the credential to establish HTTP session
        # This prevents "HTTP transport has already been closed" errors
        try:
            await self.credential.get_token("https://graph.microsoft.com/.default")
            self.logger.info("✅ Credential initialized and HTTP session established")
        except Exception as token_error:
            self.logger.error(f"❌ Failed to initialize credential: {token_error}")
            # Clean up temp certificate file if it was created
            if self.temp_cert_file:
                try:
                    os.unlink(self.temp_cert_file.name)
                except OSError:
                    pass
            raise ValueError(f"Failed to initialize SharePoint credential: {token_error}")

        # Initialize Graph Client
        self.client = GraphServiceClient(
            self.credential,
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
                if any(term in error_str for term in [str(HttpStatusCode.FORBIDDEN.value), "accessdenied", "forbidden"]):
                    self.logger.error(f"Permission denied on API call (attempt {attempt + 1}): {e}")
                    return None

                # Don't retry on 404 errors
                if any(term in error_str for term in [str(HttpStatusCode.NOT_FOUND.value), "notfound"]):
                    self.logger.error(f"Resource not found on API call (attempt {attempt + 1}): {e}")
                    return None

                # Don't retry on 400 bad request errors (like invalid hostname)
                if any(term in error_str for term in [str(HttpStatusCode.BAD_REQUEST.value), "badrequest", "invalid"]):
                    self.logger.warning(f"⚠️ Bad request on API call (attempt {attempt + 1}): {e}")
                    return None

                # Retry on rate limiting and server errors
                if any(term in error_str for term in [str(HttpStatusCode.TOO_MANY_REQUESTS.value), str(HttpStatusCode.SERVICE_UNAVAILABLE.value), str(HttpStatusCode.BAD_GATEWAY.value), str(HttpStatusCode.INTERNAL_SERVER_ERROR.value), "throttle", "timeout"]):
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
                            if contains_onedrive:
                                self.logger.debug(f"Hostname matches expected OneDrive pattern: {bool(contains_onedrive)}")

                            if self.filters.get('exclude_onedrive_sites') and contains_onedrive:
                                self.logger.debug(f"Skipping OneDrive site: '{site.display_name or site.name}'")
                                continue

                            self.logger.debug(f"Site found: '{site.display_name or site.name}' - ID: '{site.id}'")
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
                    continue
                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue
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

            # Get permissions currently fetching permissions via site record group
            permissions = await self._get_item_permissions(site_id, drive_id, item_id)

            # Todo: Get permissions for the record
            # for user in users:
            #     permissions.append(Permission(
            #         email=user.email,
            #         type=PermissionType.READ,
            #         entity_type=EntityType.USER
            #     ))

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
                record_status=ProgressStatus.NOT_STARTED if not existing_record else existing_record.record_status,
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
                record_status=ProgressStatus.NOT_STARTED,
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
                record_status=ProgressStatus.NOT_STARTED,
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
                record_status=ProgressStatus.NOT_STARTED,
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
                inherit_permissions=True
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating document library record group: {e}")
            return None

    async def _get_sharepoint_access_token(self) -> Optional[str]:
        """Get access token for SharePoint REST API."""
        from azure.identity.aio import CertificateCredential, ClientSecretCredential

        try:
            # Use the same authentication method as the main Graph API client
            if self.certificate_path:
                # Certificate-based authentication
                async with CertificateCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    certificate_path=self.certificate_path,
                    password=self.certificate_password  # Will be None if no password
                ) as credential:
                    self.logger.debug("Using CertificateCredential for SharePoint REST API")

                    # Parse domain to ensure correct format
                    parsed = urllib.parse.urlparse(self.sharepoint_domain)
                    if parsed.hostname:
                        resource_host = parsed.hostname
                    else:
                        resource_host = self.sharepoint_domain.replace('https://', '').replace('http://', '').strip('/')

                    # Construct SharePoint resource URL
                    resource = f"https://{resource_host}"

                    self.logger.debug(f"Requesting SharePoint token for resource: {resource}/.default")

                    # Request token specifically for SharePoint (NOT Graph API)
                    token = await credential.get_token(f"{resource}/.default")

                    self.logger.info("✅ Successfully obtained SharePoint access token")

                    return token.token

            elif self.client_secret:
                # Client secret authentication
                async with ClientSecretCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    client_secret=self.client_secret
                ) as credential:
                    self.logger.debug("Using ClientSecretCredential for SharePoint REST API")

                    # Parse domain to ensure correct format
                    parsed = urllib.parse.urlparse(self.sharepoint_domain)
                    if parsed.hostname:
                        resource_host = parsed.hostname
                    else:
                        resource_host = self.sharepoint_domain.replace('https://', '').replace('http://', '').strip('/')

                    # Construct SharePoint resource URL
                    resource = f"https://{resource_host}"

                    self.logger.debug(f"Requesting SharePoint token for resource: {resource}/.default")

                    # Request token specifically for SharePoint (NOT Graph API)
                    token = await credential.get_token(f"{resource}/.default")

                    self.logger.info("✅ Successfully obtained SharePoint access token")

                    return token.token
            else:
                self.logger.error("❌ No valid authentication method available (neither certificate nor client secret)")
                return None

        except Exception as e:
            self.logger.error(f"❌ Error getting SharePoint token: {e}")
            self.logger.error("   Make sure your app has SharePoint permissions (Sites.Read.All)")
            self.logger.error(f"   Resource URL: {resource if 'resource' in locals() else 'N/A'}")
            return None

    async def _get_site_permissions(self, site_id: str) -> List[Permission]:
        permissions_dict = {} # Key: Email, Value: Permission Object


        try:
            # 1. Get Site URL from your existing cache
            site_metadata = self.site_cache.get(site_id)
            if not site_metadata or not site_metadata.site_url:
                self.logger.error(f"❌ Site metadata/URL not found for {site_id}")
                return []

            site_url = site_metadata.site_url

            # 2. Get SharePoint REST Token
            access_token = await self._get_sharepoint_access_token()
            if not access_token:
                self.logger.warning("❌ Could not get SharePoint access token")
                return []

            # Helper to check if user exists to avoid downgrading WRITE to READ
            def add_or_update_permission(user_email, user_id, perm_type) -> None:
                if not user_email:
                    return

                # If user exists and is already WRITE, don't downgrade to READ
                if user_email in permissions_dict:
                    if permissions_dict[user_email].type == PermissionType.WRITE:
                        return

                permissions_dict[user_email] = Permission(
                    external_id=str(user_id),
                    email=user_email,
                    type=perm_type,
                    entity_type=EntityType.USER
                )

            # Security Group Type constant (Pricipal type=4 means Security group) done to pass lint checks
            SECURITY_GROUP_TYPE = 4

            # ==================================================================
            # STEP 1 & 2: Process Associated Groups (Owners & Members) -> WRITE
            # ==================================================================
            for group_type in ['associatedownergroup', 'associatedmembergroup']:
                sp_users = await self._get_sharepoint_group_users(site_url, group_type, access_token)

                for sp_user in sp_users:
                    login_name = sp_user.get('LoginName', '')

                    # CASE A: It's an M365 Group (The "True" Team)
                    if 'federateddirectoryclaimprovider' in login_name:
                        # Extract GUID
                        match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', login_name)
                        if match:
                            group_id = match.group(1)
                            # Call Graph API (Owners or Members based on SP group type)
                            is_owner_group = 'owner' in group_type
                            graph_users = await self._fetch_graph_group_members(group_id, is_owner=is_owner_group)

                            for g_user in graph_users:
                                add_or_update_permission(g_user['email'], g_user['id'], PermissionType.WRITE)

                    # CASE B: It's the "Everyone" Claim (Public Site)
                    elif 'spo-grid-all-users' in login_name:
                        self.logger.info(f"🌍 Site {site_id} is Public (Everyone claim found)")
                        # Add org relation for public sites
                        permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                            type=PermissionType.READ, # Default to READ for public access
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id # Placeholder
                        )

                    # CASE C: It's an AD Security Group (PrincipalType == 4)
                    elif sp_user.get('PrincipalType') == SECURITY_GROUP_TYPE:
                        # Security Group LoginNames often look like: "c:0t.c|tenant|32537252-0676-4c47-a372-2d93563456"
                        # We need to extract that GUID at the end.
                        self.logger.info(f"🔒 Found Security Group: {login_name}")

                        # Regex to capture the GUID after 'tenant|'
                        match = re.search(r'\|tenant\|([0-9a-fA-F-]{36})', login_name)

                        if match:
                            group_id = match.group(1)

                            # This ID is a virtual claim, not a real Graph Group.
                            if group_id == '9908e57b-4444-4a0e-af96-e8ca83c0a0e5':
                                self.logger.info("     -> Found 'Everyone except external users' claim. Skipping.")
                                continue

                            self.logger.info(f"   -> Extracted Group ID: {group_id}")

                            # Use your existing Graph expander (reuse logic!)
                            # Note: Security groups can have nested groups, so transitive_members (which you use) is PERFECT.
                            graph_users = await self._fetch_graph_group_members(group_id, is_owner=False)

                            for g_user in graph_users:
                                add_or_update_permission(g_user['email'], g_user['id'], PermissionType.WRITE) # Or READ based on context
                        else:
                            self.logger.warning(f"   -> ⚠️ Could not extract GUID from Security Group LoginName: {login_name}")

                    # CASE D: It's a direct individual user (Rare in modern sites, but possible)
                    elif sp_user.get('PrincipalType') == 1: # 1 = User
                        email = sp_user.get('Email') or sp_user.get('UserPrincipalName')
                        add_or_update_permission(email, sp_user.get('Id'), PermissionType.WRITE)

            # ==================================================================
            # STEP 3: Process Explicit Visitors -> READ
            # ==================================================================
            visitors = await self._get_sharepoint_group_users(site_url, 'associatedvisitorgroup', access_token)

            # The standard GUID for "Everyone except external users"
            EVERYONE_EXCEPT_EXTERNAL_ID = '9908e57b-4444-4a0e-af96-e8ca83c0a0e5'

            for v in visitors:
                login_name = v.get('LoginName', '')
                principal_type = v.get('PrincipalType')
                title = v.get('Title', '')

                # DEBUG: Print everything found in visitors so you can see it in logs
                self.logger.info(f"👀 Visitor Found: '{title}' | Type: {principal_type} | Login: {login_name}")

                # CASE A: Standard User (Type 1)
                if principal_type == 1:
                    email = v.get('Email') or v.get('UserPrincipalName')
                    add_or_update_permission(email, v.get('Id'), PermissionType.READ)

                # CASE B: "Everyone" Claims (Modern)
                elif 'spo-grid-all-users' in login_name or 'c:0(.s|true' in login_name:
                    self.logger.info(f"🌍 Site {site_id} is Public (Everyone claim found)")
                    permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )

                # CASE C: "Everyone" Security Group (Type 4)
                elif principal_type == SECURITY_GROUP_TYPE:
                    # Check GUID or Title
                    if EVERYONE_EXCEPT_EXTERNAL_ID in login_name or 'Everyone except external users' in title:
                        self.logger.info(f"🌍 Site {site_id} is Public ('Everyone' Group found)")
                        permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id
                        )


            self.logger.info(f"Found {len(permissions_dict)} unique permissions for site {site_id}")
            return list(permissions_dict.values())

        except Exception as e:
            self.logger.error(f"❌ Error resolving site permissions: {e}")
            return []

    async def _get_sharepoint_group_users(self, site_url: str, group_type: str, access_token: str) -> List[dict]:
        """
        Fetches users/groups from the associated SharePoint security groups.
        group_type options: 'associatedownergroup', 'associatedmembergroup', 'associatedvisitorgroup'
        """

        # Construct the endpoint: e.g. .../sites/MySite/_api/web/associatedownergroup/users
        endpoint = f"{site_url}/_api/web/{group_type}/users"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json;odata=verbose"
        }

        try:
            self.logger.debug(f"📡 Fetching SharePoint group: {group_type}")

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status == HTTPStatus.OK:
                        data = await response.json()
                        results = data.get('d', {}).get('results', [])
                        self.logger.debug(f"✅ Found {len(results)} entries in {group_type}")
                        return results
                    else:
                        # 404 is common if the group is empty/doesn't exist (e.g. no visitors)
                        if response.status != HTTPStatus.NOT_FOUND:
                            error_text = await response.text()
                            self.logger.warning(f"⚠️ Failed to fetch {group_type}: {response.status} - {error_text}")
                        return []

        except Exception as e:
            self.logger.error(f"❌ Error in _get_sharepoint_group_users: {e}")
            return []

    async def _fetch_graph_group_members(self, group_id: str, is_owner: bool = False) -> List[dict]:
        """
        Fetches ALL user members from an M365 Group via Graph API, handling pagination.
        """
        users = []
        try:
            self.logger.debug(f"🔍 Expanding M365 Group {group_id} (Is Owner: {is_owner})")

            # 1. Initial Request
            if is_owner:
                response = await self.client.groups.by_group_id(group_id).owners.get()
            else:
                response = await self.client.groups.by_group_id(group_id).transitive_members.get()

            # 2. Pagination Loop
            while response:
                # Process current page
                if response.value:
                    for item in response.value:
                        # Extract user details (same logic as before)
                        odata_type = getattr(item, 'odata_type', '').lower()

                        # We only want real users (#microsoft.graph.user)
                        if 'user' in odata_type or hasattr(item, 'user_principal_name'):
                            email = getattr(item, 'mail', None) or getattr(item, 'user_principal_name', None)
                            user_id = getattr(item, 'id', None)

                            if email and user_id:
                                users.append({
                                    'id': user_id,
                                    'email': email,
                                    'name': getattr(item, 'display_name', 'Unknown')
                                })

                # Check if there is a next page
                next_link = getattr(response, 'odata_next_link', None)

                if next_link:
                    self.logger.debug(f"🔄 Fetching next page for group {group_id}...")
                    if is_owner:
                        # Use .with_url() to fetch the exact next page URL
                        response = await self.client.groups.by_group_id(group_id).owners.with_url(next_link).get()
                    else:
                        response = await self.client.groups.by_group_id(group_id).transitive_members.with_url(next_link).get()
                else:
                    # No more pages
                    response = None

            self.logger.info(f"✅ Extracted {len(users)} unique users from M365 Group {group_id}")
            return users

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to expand M365 Group {group_id}: {e}")
            return users # Return whatever we found so far

    async def _get_site_members_direct(self, site_url: str, access_token: str) -> List[dict]:
        """
        Get all site members directly using SharePoint REST API.
        Optimized to filter only for real users (PrincipalType=1) to avoid 503/Throttling.
        """

        # OPTIMIZATION: Filter for Users only (1) and exclude hidden system users.
        # We also only select the fields we need to reduce payload size.
        base_endpoint = f"{site_url}/_api/web/siteusers"
        query_params = (
            "?$filter=PrincipalType eq 1 and IsHiddenInUI eq false"
            "&$select=Id,Email,UserPrincipalName,Title,LoginName"
        )
        endpoint = base_endpoint + query_params

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json;odata=verbose"
        }

        self.logger.debug(f"Fetching site members from: {endpoint}")

        max_retries = 3

        async with aiohttp.ClientSession() as session:
            for attempt in range(max_retries):
                try:
                    async with session.get(endpoint, headers=headers) as response:
                        if response.status == HTTPStatus.OK:
                            data = await response.json()
                            members = data.get('d', {}).get('results', [])
                            self.logger.info(f"✅ Retrieved {len(members)} site members directly")
                            return members

                        # Handle 503 (Service Unavailable) or 429 (Throttling)
                        elif response.status in [503, 429, 504]:
                            self.logger.warning(f"⚠️ SharePoint is throttling/busy (Status {response.status}). Retrying {attempt + 1}/{max_retries}...")
                            await asyncio.sleep(2 * (attempt + 1)) # Exponential backoff: 2s, 4s, 6s
                            continue

                        else:
                            # Permanent error
                            error_text = await response.text()
                            self.logger.warning(f"❌ Failed to get site members: {response.status} - {error_text[:200]}") # Truncate error log
                            return []

                except Exception as e:
                    self.logger.error(f"❌ Exception fetching site members (Attempt {attempt + 1}): {e}")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(2)
                    else:
                        return []

            return []

    async def _get_site_role_assignments_with_members(self, site_url: str, access_token: str) -> List[dict]:
        """Get role assignments with expanded member and role information."""

        try:
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json;odata=verbose"
            }

            # Get role assignments with member and role definition details
            endpoint = f"{site_url}/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings"

            self.logger.debug(f"Fetching role assignments from: {endpoint}")

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status == HTTPStatus.OK:
                        data = await response.json()
                        assignments = data.get('d', {}).get('results', [])
                        self.logger.info(f"✅ Retrieved {len(assignments)} role assignments")
                        return assignments
                    else:
                        error_text = await response.text()
                        self.logger.warning(f"❌ Failed to get role assignments: {response.status} - {error_text}")
                        return []

        except Exception as e:
            self.logger.error(f"❌ Error fetching role assignments: {e}")
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

            async with self.rate_limiter:
                # Use the drives endpoint directly without going through sites
                perms_response = await self._safe_api_call(
                    self.client.drives.by_drive_id(drive_id)
                        .items.by_drive_item_id(item_id)
                        .permissions.get()
                )

            if perms_response and perms_response.value:
                permissions = await self._convert_to_permissions(perms_response.value)

            return permissions

        except Exception as e:
            self.logger.debug(f"❌ Could not get item permissions for item {item_id}: {e}")
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
        """
        Convert Microsoft Graph permissions to our Permission model.
        Handles both user and group permissions.
        """
        permissions = []


        for perm in msgraph_permissions:
            try:
                # Handle user permissions
                if hasattr(perm, 'granted_to_v2') and perm.granted_to_v2:
                    if hasattr(perm.granted_to_v2, 'user') and perm.granted_to_v2.user:
                        user = perm.granted_to_v2.user
                        permissions.append(Permission(
                            external_id=user.id,
                            email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                            entity_type=EntityType.USER
                        ))
                    if hasattr(perm.granted_to_v2, 'group') and perm.granted_to_v2.group:
                        group = perm.granted_to_v2.group
                        permissions.append(Permission(
                            external_id=group.id,
                            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                            type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                            entity_type=EntityType.GROUP
                        ))


                # Handle group permissions
                if hasattr(perm, 'granted_to_identities_v2') and perm.granted_to_identities_v2:
                    for identity in perm.granted_to_identities_v2:
                        if hasattr(identity, 'group') and identity.group:
                            group = identity.group
                            permissions.append(Permission(
                                external_id=group.id,
                                email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.GROUP
                            ))
                        elif hasattr(identity, 'user') and identity.user:
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.USER
                            ))

                # Handle link permissions (anyone with link)
                if hasattr(perm, 'link') and perm.link:
                    link = perm.link
                    if link.scope == "anonymous":
                        permissions.append(Permission(
                            external_id="anyone_with_link",
                            email=None,
                            type=map_msgraph_role_to_permission_type(link.type),
                            entity_type=EntityType.ANYONE_WITH_LINK
                        ))
                    elif link.scope == "organization":
                        permissions.append(Permission(
                            external_id="anyone_in_org",
                            email=None,
                            type=map_msgraph_role_to_permission_type(link.type),
                            entity_type=EntityType.ORG
                        ))

            except Exception as e:
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
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
    # TODO: rn sharepoint group doesnt direcctly support incremental sync need to add the logic to remove members from group if removed
    async def _sync_user_groups(self) -> None:
        """Sync SharePoint groups and their members."""
        try:
            self.logger.info("Starting SharePoint group synchronization")

            # Part 1: Sync Azure AD groups
            try:
                await self._sync_azure_ad_groups_delta()
            except Exception as groups_error:
                self.logger.error(f"❌ Error syncing Azure AD Groups with delta: {groups_error}")

            # Part 2: Sync SharePoint groups
            self.logger.info("Starting SharePoint Site Groups fetch...")

            sharepoint_groups_with_members = []
            total_groups = 0

            # Create credential context manager based on authentication method
            credential_context = (
                CertificateCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    certificate_path=self.certificate_path,
                )
                if self.certificate_path
                else ClientSecretCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    client_secret=self.client_secret
                )
            )

            try:
                async with credential_context as credential:
                    # Get all sites
                    sites = await self._get_all_sites()
                    SECURITY_GROUP_TYPE = 4

                    async with httpx.AsyncClient(timeout=30.0) as http_client:
                        for site in sites:
                            try:
                                site_id = site.id
                                site_name = site.display_name or site.name
                                self.logger.info(f"Fetching site groups for site: {site_name}")

                                async with self.rate_limiter:
                                    site_details = await self.client.sites.by_site_id(site_id).get()

                                if not site_details or not site_details.web_url:
                                    self.logger.debug(f"No web URL available for site: {site_name}")
                                    continue

                                site_web_url = site_details.web_url
                                rest_api_url = f"{site_web_url}/_api/web/sitegroups"
                                parsed_url = urlparse(site_web_url)
                                sharepoint_resource = f"https://{parsed_url.netloc}"

                                # Reuse credential to get a specific token for this site
                                token_response = await credential.get_token(f"{sharepoint_resource}/.default")
                                access_token = token_response.token

                                headers = {
                                    'Authorization': f'Bearer {access_token}',
                                    'Accept': 'application/json;odata=verbose',
                                    'Content-Type': 'application/json;odata=verbose'
                                }

                                # Reuse http_client
                                response = await http_client.get(rest_api_url, headers=headers)

                                if response.status_code == HTTPStatus.OK:
                                    data = response.json()
                                    site_groups = data.get('d', {}).get('results', [])

                                    self.logger.info(f"\n{'='*180}")
                                    self.logger.info(f"Site Groups for: {site_name} (Total: {len(site_groups)})")
                                    self.logger.info(f"{'='*100}")

                                    for idx, group in enumerate(site_groups, 1):
                                        group_title = group.get('Title', 'N/A')
                                        group_id = group.get('Id', 'N/A')
                                        description = group.get('Description', 'N/A')

                                        # Combine Site ID and Group ID to ensure global uniqueness across the tenant
                                        # Format: {SiteGUID}-{GroupID}
                                        unique_source_id = f"{site_id}-{group_id}"

                                        user_group = AppUserGroup(
                                            id=str(uuid.uuid4()),
                                            source_user_group_id=unique_source_id,
                                            app_name=self.connector_name,
                                            name=group_title,
                                            description=description if description != 'N/A' else None,
                                        )

                                        app_users = []
                                        users_url = f"{site_web_url}/_api/web/sitegroups/GetById({group_id})/users"

                                        try:
                                            # Reuse http_client
                                            users_response = await http_client.get(users_url, headers=headers)

                                            if users_response.status_code == HTTPStatus.OK:
                                                users_data = users_response.json()
                                                users = users_data.get('d', {}).get('results', [])

                                                if users:
                                                    self.logger.info(f"   - Raw Entities found: {len(users)}")

                                                    for user in users:
                                                        login_name = user.get('LoginName', '')
                                                        principal_type = user.get('PrincipalType')

                                                        # CASE A: M365 Unified Group (The "True" Team)
                                                        # Looks for 'federateddirectoryclaimprovider' in LoginName
                                                        if 'federateddirectoryclaimprovider' in login_name:
                                                            # Extract GUID
                                                            match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', login_name)
                                                            if match:
                                                                m365_id = match.group(1)
                                                                self.logger.info(f"     -> Expanding M365 Group: {m365_id}")

                                                                # Determine if we want Owners or Members based on the SP Group Title
                                                                is_owner_check = 'Owner' in group_title

                                                                # Call helper to get actual humans from Graph
                                                                expanded_users = await self._fetch_graph_group_members(m365_id, is_owner=is_owner_check)

                                                                for exp_u in expanded_users:
                                                                    app_users.append(AppUser(
                                                                        source_user_id=exp_u['id'],
                                                                        email=exp_u['email'],
                                                                        full_name=exp_u['name'],
                                                                        app_name=self.connector_name
                                                                    ))

                                                        # CASE B: AD Security Group (PrincipalType == 4)
                                                        elif principal_type == SECURITY_GROUP_TYPE:
                                                            # Security Group LoginNames often look like: "c:0t.c|tenant|GUID"
                                                            match = re.search(r'\|tenant\|([0-9a-fA-F-]{36})', login_name)
                                                            if match:
                                                                group_id = match.group(1)

                                                                # This ID is a virtual claim, not a real Graph Group.
                                                                if group_id == '9908e57b-4444-4a0e-af96-e8ca83c0a0e5':
                                                                    self.logger.info("     -> Found 'Everyone except external users' claim. Skipping.")
                                                                    continue


                                                                self.logger.info(f"     -> Expanding Security Group: {group_id}")
                                                                # Security groups imply members (is_owner=False)
                                                                expanded_users = await self._fetch_graph_group_members(group_id, is_owner=False)

                                                                for exp_u in expanded_users:
                                                                    app_users.append(AppUser(
                                                                        source_user_id=exp_u['id'],
                                                                        email=exp_u['email'],
                                                                        full_name=exp_u['name'],
                                                                        app_name=self.connector_name
                                                                    ))

                                                        # CASE C: Standard Individual User
                                                        else:
                                                            user_id = user.get('Id')
                                                            user_title = user.get('Title', 'N/A')
                                                            user_email = user.get('Email') or user.get('UserPrincipalName')

                                                            if user_email or user.get('UserPrincipalName'):
                                                                app_users.append(AppUser(
                                                                    source_user_id=str(user_id) if user_id else None,
                                                                    email=user_email or user.get('UserPrincipalName'),
                                                                    full_name=user_title if user_title != 'N/A' else None,
                                                                    app_name=self.connector_name,
                                                                ))

                                                else:
                                                    self.logger.info("   - No entities in this group")
                                            else:
                                                self.logger.info(f"   - Error fetching users: {users_response.status_code}")

                                        except Exception as user_error:
                                            self.logger.info(f"   - Exception fetching users: {user_error}")

                                        sharepoint_groups_with_members.append((user_group, app_users))
                                        total_groups += 1
                                    self.logger.info(f"\n{'='*180}\n")

                                elif response.status_code == HTTPStatus.UNAUTHORIZED:
                                    self.logger.info(" 401 Unauthorized Error")
                                else:
                                    self.logger.info(f" Error: {response.status_code}")


                            except Exception:
                                self.logger.info(f" Error processing site {site_name}: {traceback.format_exc()}")
                                continue

                    # Process all SharePoint site groups
                    if sharepoint_groups_with_members:
                        self.logger.info(f"Processing {len(sharepoint_groups_with_members)} SharePoint site groups")

                        await self.data_entities_processor.on_new_user_groups(
                            sharepoint_groups_with_members
                        )

                self.logger.info(f"Completed SharePoint group synchronization - processed {total_groups} groups")

            except Exception as outer_error:
                self.logger.debug(f"Site groups fetch wrapper error: {outer_error}")

        except Exception as e:
            self.logger.error(f"❌ Error syncing SharePoint groups: {e}")

    async def _sync_azure_ad_groups_delta(self) -> None:
        """
        Incremental Azure AD groups synchronization using Delta API.
        Uses Graph Delta API for BOTH initial full sync and subsequent incremental syncs.
        """
        try:
            sync_point_key = generate_record_sync_point_key(
                SyncDataPointType.GROUPS.value,
                "organization",
                self.data_entities_processor.org_id
            )
            sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)

            # 1. Determine starting URL
            # Default to fresh delta start
            url = "https://graph.microsoft.com/v1.0/groups/delta"
            # If we have a saved state, prefer nextLink (resuming interrupted sync) or deltaLink (incremental sync)
            if sync_point:
                url = sync_point.get('nextLink') or sync_point.get('deltaLink') or url

            self.logger.info("Starting Azure AD groups delta sync...")

            while True:
                # 2. Fetch page of results
                result = await self.msgraph_client.get_groups_delta_response(url)
                groups = result.get('groups', [])

                self.logger.info(f"Fetched page with {len(groups)} Azure AD groups")

                # 3. Process each group in the current page
                for group in groups:
                    # A) Check for DELETION marker
                    if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                        self.logger.info(f"[DELTA ACTION] 🗑️ REMOVE Group: {group.id}")
                        await self._handle_delete_group(group.id)
                        continue

                    # B) Process ADD/UPDATE
                    self.logger.info(f"[DELTA ACTION] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                    await self._handle_group_create(group)

                    # C) Check for specific MEMBER changes in this delta
                    if hasattr(group, 'additional_data') and group.additional_data:
                        member_changes = group.additional_data.get('members@delta', [])

                        if member_changes:
                            self.logger.info(f"    -> [ACTION] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                        for member_change in member_changes:
                            user_id = member_change.get('id')

                            # 1. Fetch email (needed for both add and remove)
                            email = await self.msgraph_client.get_user_email(user_id)

                            if not email:
                                self.logger.warning(f"Could not find email for user ID {user_id}, skipping member change processing.")
                                continue

                            # 2. Handle based on change type
                            if '@removed' in member_change:
                                self.logger.info(f"    -> [ACTION] 👤⛔ REMOVING member: {email} ({user_id}) from group {group.id}")
                                await self.data_entities_processor.on_user_group_member_removed(
                                    external_group_id=group.id,
                                    user_email=email,
                                    connector_name=self.connector_name
                                )
                            else:
                                self.logger.info(f"    -> [ACTION] 👤✨ ADDING member: {email} ({user_id}) to group {group.id}")
                                # Member addition is handled in _handle_group_create

                # 4. Handle pagination and completion
                if result.get('next_link'):
                    # More data available, update URL for next loop iteration
                    url = result.get('next_link')

                    # OPTIONAL: Save intermediate 'nextLink' for resumability during long initial sync
                    # await self.user_group_sync_point.update_sync_point(sync_point_key, {"nextLink": url, "deltaLink": None})

                elif result.get('delta_link'):
                    # End of current data stream. Save the delta_link for the NEXT run.
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key,
                        {"nextLink": None, "deltaLink": result.get('delta_link')}
                    )
                    self.logger.info("Azure AD groups delta sync cycle completed, delta link saved for next run.")
                    break
                else:
                    # Fallback ensuring loop terminates if API returns neither link
                    self.logger.warning("Received response with neither next_link nor delta_link.")
                    break

        except Exception as e:
            self.logger.error(f"❌ Error in Azure AD groups delta sync: {e}", exc_info=True)
            raise


    async def _handle_group_create(self, group: Group) -> None:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.
        """
        try:

            # 1. Fetch latest members for this group
            members = await self.msgraph_client.get_group_members(group.id)

            # 2. Create AppUserGroup entity
            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            # 3. Create AppUser entities for members
            app_users = []
            for member in members:
                app_user = AppUser(
                    source_user_id=member.id,
                    email=member.mail or member.user_principal_name,
                    full_name=member.display_name,
                    source_created_at=member.created_date_time.timestamp() if member.created_date_time else get_epoch_timestamp_in_ms(),
                    app_name=self.connector_name,
                )
                app_users.append(app_user)

            # 4. Send to processor (wrapped in list as expected by on_new_user_groups)
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])

            self.logger.info(f"Processed group creation/update for: {group.display_name}")

        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)

    async def _handle_delete_group(self, group_id: str) -> None:
        """
        Handles the deletion of a single user group.
        Calls the data processor to remove it from the database.

        Args:
            group_id: The external ID of the group to be deleted.
        """
        try:
            self.logger.info(f"Handling group deletion for: {group_id}")

            # Call the data entities processor to handle the deletion logic
            await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_name=self.connector_name
            )

            self.logger.info(f"Successfully processed group deletion for: {group_id}")

        except Exception as e:
            self.logger.error(f"❌ Error handling group delete for {group_id}: {e}", exc_info=True)

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

                if record_update.metadata_changed:
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
                if record_update.permissions_changed:
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
                if record_update.content_changed:
                    await self.data_entities_processor.on_record_content_update(record_update.record)
        except Exception as e:
            self.logger.error(f"❌ Error handling record updates: {e}")

    async def _reinitialize_credential_if_needed(self) -> None:
        """
        Reinitialize the credential and clients if the HTTP transport has been closed.
        This prevents "HTTP transport has already been closed" errors when the connector
        instance is reused across multiple scheduled runs that are days apart.
        """
        try:
            # Test if the credential is still valid by attempting to get a token
            await self.credential.get_token("https://graph.microsoft.com/.default")
            self.logger.debug("✅ Credential is valid and active")
        except Exception as e:
            self.logger.warning(f"⚠️ Credential needs reinitialization: {e}")

            # Close old credential if it exists
            if hasattr(self, 'credential') and self.credential:
                try:
                    await self.credential.close()
                except Exception:
                    pass

            # Determine which authentication method to use
            has_certificate = hasattr(self, 'certificate_path') and self.certificate_path

            if has_certificate:
                # Recreate certificate-based credential
                self.credential = CertificateCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    certificate_path=self.certificate_path,
                )
            else:
                # Recreate client secret credential
                if not all([self.tenant_id, self.client_id, self.client_secret]):
                    raise ValueError("Cannot reinitialize: credentials not found")

                self.credential = ClientSecretCredential(
                    tenant_id=self.tenant_id,
                    client_id=self.client_id,
                    client_secret=self.client_secret,
                )

            # Pre-initialize to establish HTTP session
            await self.credential.get_token("https://graph.microsoft.com/.default")

            # Recreate Graph client with new credential
            self.client = GraphServiceClient(
                self.credential,
                scopes=["https://graph.microsoft.com/.default"]
            )
            self.msgraph_client = MSGraphClient(self.connector_name, self.client, self.logger)

            self.logger.info("✅ Credential successfully reinitialized")

    async def run_sync(self) -> None:
        """Main entry point for the SharePoint connector."""
        start_time = datetime.now()

        try:
            self.logger.info("🚀 Starting SharePoint connector sync")

            # Reinitialize credential to prevent "HTTP transport has already been closed" errors
            # This is necessary because the connector instance may be reused across multiple
            # scheduled runs that are days apart, causing the HTTP session to timeout
            await self._reinitialize_credential_if_needed()

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            try:
                users = await self.msgraph_client.get_all_users()
                await self.data_entities_processor.on_new_app_users(users)
                self.logger.info(f"✅ Successfully synced {len(users)} users")
            except Exception as user_error:
                self.logger.error(f"❌ Error syncing users: {user_error}")

            # Step 2: Sync user groups
            self.logger.info("Syncing SharePoint groups...")
            try:
                await self._sync_user_groups()
                self.logger.info("✅ Successfully synced SharePoint groups")
            except Exception as group_error:
                self.logger.error(f"❌ Error syncing groups: {group_error}")

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

                # TODO: So we can fetch permissiosn and create edges between members and sites
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

                # Get site permissions
                site_permissions = await self._get_site_permissions(site_id)
                # Process site record group
                site_record_groups_with_permissions.append((site_record_group, site_permissions))

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

            # Reinitialize credential to prevent session timeout issues
            await self._reinitialize_credential_if_needed()

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

            # Reinitialize credential if needed (webhooks might arrive after days of inactivity)
            await self._reinitialize_credential_if_needed()

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
            # Reinitialize credential if needed (user might be accessing files after days of inactivity)
            await self._reinitialize_credential_if_needed()

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

            # 1. Clear caches first
            if hasattr(self, 'site_cache'):
                self.site_cache.clear()

            # 2. Clear MSGraph helper client reference
            if hasattr(self, 'msgraph_client'):
                self.msgraph_client = None

            # 3. Release Graph Client reference before closing credential
            if hasattr(self, 'client'):
                self.client = None

            # 4. Close the credential (closes HTTP transport/sessions)
            # This must be done after all API operations are complete
            if hasattr(self, 'credential') and self.credential:
                try:
                    await self.credential.close()
                    self.logger.info("✅ Authentication credential closed")
                except Exception as credential_error:
                    self.logger.warning(f"⚠️ Error closing credential (may already be closed): {credential_error}")
                finally:
                    self.credential = None

            # 5. Clean up temporary certificate file last
            if hasattr(self, 'certificate_path') and self.certificate_path:
                try:
                    if os.path.exists(self.certificate_path):
                        os.remove(self.certificate_path)
                        self.logger.info(f"✅ Removed temporary certificate file: {self.certificate_path}")
                except Exception as cert_error:
                    self.logger.warning(f"⚠️ Error removing temporary certificate: {cert_error}")

            self.logger.info("✅ SharePoint connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during SharePoint connector cleanup: {e}")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records - not implemented for SharePoint yet."""
        self.logger.warning("Reindex not implemented for SharePoint connector")
        pass

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
