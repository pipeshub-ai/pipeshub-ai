import asyncio
import base64
import json
import logging
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
from urllib.parse import quote, unquote, urlparse

import aiohttp
import httpx
from aiolimiter import AsyncLimiter
from azure.identity.aio import CertificateCredential, ClientSecretCredential
from bs4 import BeautifulSoup, Comment
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
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.microsoft.common.apps import SharePointOnlineApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_role_to_permission_type,
)
from app.connectors.sources.microsoft.sharepoint_online.utils import clean_html_output
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logging.getLogger("azure").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

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

class MicrosoftRegion(str, Enum):
    """Microsoft 365 Multi-Geo region codes for Search API."""

    APC = "APC"  # Asia Pacific (Singapore, Malaysia, Hong Kong)
    AUS = "AUS"  # Australia
    AUT = "AUT"  # Austria
    BRA = "BRA"  # Brazil
    CAN = "CAN"  # Canada
    CHL = "CHL"  # Chile
    EUR = "EUR"  # Europe (Netherlands, Ireland, Finland)
    FRA = "FRA"  # France
    DEU = "DEU"  # Germany
    IND = "IND"  # India
    IDN = "IDN"  # Indonesia
    ISR = "ISR"  # Israel
    ITA = "ITA"  # Italy
    JPN = "JPN"  # Japan
    KOR = "KOR"  # South Korea
    MEX = "MEX"  # Mexico
    NZL = "NZL"  # New Zealand
    NOR = "NOR"  # Norway
    POL = "POL"  # Poland
    QAT = "QAT"  # Qatar
    ZAF = "ZAF"  # South Africa
    ESP = "ESP"  # Spain
    SWE = "SWE"  # Sweden
    CHE = "CHE"  # Switzerland
    TWN = "TWN"  # Taiwan
    ARE = "ARE"  # United Arab Emirates
    GBR = "GBR"  # United Kingdom
    NAM = "NAM"  # North America (United States)


class CountryToRegionMapper:

    _COUNTRY_TO_REGION: dict[str, MicrosoftRegion] = {
        "SG": MicrosoftRegion.APC,
        "MY": MicrosoftRegion.APC,
        "HK": MicrosoftRegion.APC,
        "AU": MicrosoftRegion.AUS,
        "AT": MicrosoftRegion.AUT,
        "BR": MicrosoftRegion.BRA,
        "CA": MicrosoftRegion.CAN,
        "CL": MicrosoftRegion.CHL,
        "NL": MicrosoftRegion.EUR,
        "IE": MicrosoftRegion.EUR,
        "FI": MicrosoftRegion.EUR,
        "FR": MicrosoftRegion.FRA,
        "DE": MicrosoftRegion.DEU,
        "IN": MicrosoftRegion.IND,
        "ID": MicrosoftRegion.IDN,
        "IL": MicrosoftRegion.ISR,
        "IT": MicrosoftRegion.ITA,
        "JP": MicrosoftRegion.JPN,
        "KR": MicrosoftRegion.KOR,
        "MX": MicrosoftRegion.MEX,
        "NZ": MicrosoftRegion.NZL,
        "NO": MicrosoftRegion.NOR,
        "PL": MicrosoftRegion.POL,
        "QA": MicrosoftRegion.QAT,
        "ZA": MicrosoftRegion.ZAF,
        "ES": MicrosoftRegion.ESP,
        "SE": MicrosoftRegion.SWE,
        "CH": MicrosoftRegion.CHE,
        "TW": MicrosoftRegion.TWN,
        "AE": MicrosoftRegion.ARE,
        "GB": MicrosoftRegion.GBR,
        "US": MicrosoftRegion.NAM,
    }

    DEFAULT_REGION = MicrosoftRegion.NAM

    @classmethod
    def get_region(cls, country_code: Optional[str]) -> MicrosoftRegion:
        """
        Get the Microsoft region for a given ISO country code.

        Args:
            country_code: ISO 3166-1 alpha-2 country code (e.g., "US", "GB", "IN")

        Returns:
            MicrosoftRegion enum value
        """
        if not country_code:
            return cls.DEFAULT_REGION

        return cls._COUNTRY_TO_REGION.get(country_code.upper(), cls.DEFAULT_REGION)

    @classmethod
    def get_region_string(cls, country_code: Optional[str]) -> str:
        """
        Get the Microsoft region string for a given ISO country code.

        Args:
            country_code: ISO 3166-1 alpha-2 country code (e.g., "US", "GB", "IN")

        Returns:
            Region code string (e.g., "NAM", "GBR", "IND")
        """
        return cls.get_region(country_code).value

    @classmethod
    def is_valid_region(cls, region: str) -> bool:
        """
        Check if a region code is valid.

        Args:
            region: Region code to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            MicrosoftRegion(region.upper())
            return True
        except ValueError:
            return False

    @classmethod
    def get_all_regions(cls) -> list[str]:
        """Get all valid region codes."""
        return [r.value for r in MicrosoftRegion]

    @classmethod
    def get_all_country_codes(cls) -> list[str]:
        """Get all supported country codes."""
        return list(cls._COUNTRY_TO_REGION.keys())

@ConnectorBuilder("SharePoint Online")\
    .in_group("Microsoft 365")\
    .with_description("Sync documents and lists from SharePoint Online")\
    .with_categories(["Storage", "Documentation"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH_ADMIN_CONSENT).fields([
            AuthField(
                name="clientId",
                display_name="Application (Client) ID",
                placeholder="Enter your Azure AD Application ID",
                description="The Application (Client) ID from Azure AD App Registration"
            ),
            AuthField(
                name="tenantId",
                display_name="Directory (Tenant) ID (Optional)",
                placeholder="Enter your Azure AD Tenant ID",
                description="The Directory (Tenant) ID from Azure AD"
            ),
            AuthField(
                name="hasAdminConsent",
                display_name="Has Admin Consent",
                description="Check if admin consent has been granted for the application",
                field_type="CHECKBOX",
                required=True,
                default_value=False
            ),
            AuthField(
                name="sharepointDomain",
                display_name="SharePoint Domain",
                placeholder="https://your-domain.sharepoint.com",
                description="Your SharePoint domain URL",
                field_type="URL",
                max_length=2000
            )
        ])
    ])\
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
        .add_filter_field(FilterField(
            name="site_ids",
            display_name="Site Names",
            description="Filter specific sites by name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="page_ids",
            display_name="Page name",
            description="Filter specific pages by name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="drive_ids",
            display_name="Document Library Names",
            description="Filter specific document libraries by name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            default_value=[],
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(CommonFields.modified_date_filter("Filter pages and blogposts by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter pages and blogposts by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="documents",
            display_name="Index Documents",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of documents",
            default_value=True
        ))
        # .add_filter_field(FilterField(
        #     name="lists",
        #     display_name="Index Lists",
        #     filter_type=FilterType.BOOLEAN,
        #     category=FilterCategory.INDEXING,
        #     description="Enable indexing of lists",
        #     default_value=True
        # ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
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
        connector_id: str
    ) -> None:
        super().__init__(SharePointOnlineApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id)

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
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
        self.connector_id = connector_id

        self.filters = {"exclude_onedrive_sites": True, "exclude_pages": True, "exclude_lists": True, "exclude_document_libraries": False}
        # Batch processing configuration
        self.batch_size = 50  # Reduced for better memory management
        self.max_concurrent_batches = 1 # set to 1 for now to avoid write write conflicts for small number of records
        self.rate_limiter = AsyncLimiter(30, 1)  # 30 requests per second (conservative)

        # Cache for site metadata
        self.site_cache: Dict[str, SiteMetadata] = {}

        self.tenant_region: str = None
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

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

    async def init(self) -> bool:
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

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
        self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)

        try:
            self.logger.info("🔍 Fetching tenant region...")

            from msgraph.generated.sites.item.site_item_request_builder import (
                SiteItemRequestBuilder,
            )

            query_params = SiteItemRequestBuilder.SiteItemRequestBuilderGetQueryParameters(
                select=["siteCollection"]
            )
            request_config = SiteItemRequestBuilder.SiteItemRequestBuilderGetRequestConfiguration(
                query_parameters=query_params
            )

            root_site = await self.client.sites.by_site_id("root").get(request_configuration=request_config)

            if (
                root_site
                and root_site.site_collection
                and root_site.site_collection.data_location_code
            ):
                self.tenant_region = root_site.site_collection.data_location_code.upper()
                self.logger.info(f"✅ Region detected via Root Site: {self.tenant_region}")
            else:
                self.logger.info("🔍 Root site dataLocationCode empty, trying organization endpoint...")

                org_collection = await self.client.organization.get()

                if org_collection and org_collection.value:
                    country_code = org_collection.value[0].country_letter_code
                    self.logger.info(f"🔍 Tenant country code: {country_code}")

                    # Use the mapper
                    self.tenant_region = CountryToRegionMapper.get_region_string(country_code)
                    self.logger.info(
                        f"✅ Region mapped from country '{country_code}': {self.tenant_region}"
                    )
                else:
                    self.logger.warning("⚠️ Could not determine region, filter site/pages/drives might not work.")

        except Exception as e:
            self.logger.warning(f"⚠️ Failed to fetch tenant region: {e}. Filter site/pages/drives might not work.")

        return True


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
            # Pagination: fetch all sites using nextLink (same pattern as groups/users)
            total_found = 0
            existing_site_ids = {s.id for s in sites}
            try:
                async with self.rate_limiter:
                    search_results = await self._safe_api_call(
                        self.client.sites.get()
                    )

                while True:
                    if not search_results or not getattr(search_results, 'value', None):
                        if total_found == 0:
                            self.logger.info("No additional sites found from search endpoint")
                        break
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
                        if site.id not in existing_site_ids:
                            sites.append(site)
                            existing_site_ids.add(site.id)
                            self.site_cache[site.id] = SiteMetadata(
                                site_id=site.id,
                                site_url=site.web_url,
                                site_name=site.display_name or site.name,
                                is_root=False,
                                created_at=site.created_date_time,
                                updated_at=site.last_modified_date_time
                            )
                            total_found += 1
                    # Pagination: follow nextLink using the same pattern as groups/users
                    if hasattr(search_results, 'odata_next_link') and search_results.odata_next_link:
                        self.logger.info(f"Following nextLink for more sites (found {total_found} so far)...")
                        async with self.rate_limiter:
                            search_results = await self._safe_api_call(
                                self.client.sites.with_url(search_results.odata_next_link).get()
                            )
                    else:
                        break
                self.logger.info(f"Found {total_found} additional sites from search (pagination)")
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

            modified_after, modified_before, created_after, created_before = self._get_date_filters()

            # Process drives (document libraries)
            self.logger.info(f"Processing drives for site: {site_name}")
            async for record, permissions, record_update in self._process_site_drives(site_id, internal_site_record_group_id=site_record_group.id, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before):
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                    continue
                elif record_update.is_updated:
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
            #     elif record_update.is_updated:
            #         await self._handle_record_updates(record_update)
            #         continue
            #     elif record:
            #         batch_records.append((record, permissions))
            #         total_processed += 1

            #         if len(batch_records) >= self.batch_size:
            #             await self.data_entities_processor.on_new_records(batch_records)
            #             batch_records = []
            #             await asyncio.sleep(0.1)

            # Process pages
            self.logger.info(f"Processing pages for site: {site_name}")
            async for record, permissions, record_update in self._process_site_pages(site_id, site_name, modified_after, modified_before, created_after, created_before):
                if record_update.is_deleted:
                    await self._handle_record_updates(record_update)
                    continue
                elif record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue
                elif record:
                    batch_records.append((record, permissions))
                    total_processed += 1

                    if len(batch_records) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        await asyncio.sleep(0.1)

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

    async def _process_site_drives(self, site_id: str, internal_site_record_group_id: str, modified_after: Optional[datetime] = None, modified_before: Optional[datetime] = None, created_after: Optional[datetime] = None, created_before: Optional[datetime] = None) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
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

                    drive_web_url = getattr(drive, 'web_url', None)
                    normalized_url = self._normalize_document_library_url(drive_web_url)

                    if not self._pass_drive_key_filters(normalized_url):
                        self.logger.debug(f"Skipping drive (Document Library filter) '{normalized_url}' {drive_name}")
                        continue

                    # Create document library record
                    drive_record_group = self._create_document_library_record_group(drive, site_id, internal_site_record_group_id)
                    if drive_record_group:
                        permissions, should_inherit = await self._get_drive_permissions(site_id, drive_id)
                        drive_record_group.inherit_permissions = should_inherit
                        drive_record_groups_with_permissions.append((drive_record_group, permissions))

            self.logger.info(f"Found {len(drive_record_groups_with_permissions)} drive record groups to process.")
            await self.data_entities_processor.on_new_record_groups(drive_record_groups_with_permissions)

            for drive_record_group, _permissions in drive_record_groups_with_permissions:
                # Process items in the drive using delta
                item_count = 0
                async for item_tuple in self._process_drive_delta(site_id, drive_record_group.external_group_id, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before):
                    yield item_tuple
                    item_count += 1

        except Exception:
            self.logger.exception(f"❌ Error processing drives for site {site_id}")

    async def _process_drive_delta(self, site_id: str, drive_id: str, modified_after: Optional[datetime] = None, modified_before: Optional[datetime] = None, created_after: Optional[datetime] = None, created_before: Optional[datetime] = None) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
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
                parsed_url = urllib.parse.urlparse(delta_url)
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
                        record_update = await self._process_drive_item(item, site_id, drive_id, users, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before)
                        if record_update:
                            if record_update.is_deleted:
                                yield (None, [], record_update)
                            elif record_update.record:
                                if not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS, default=True):
                                    record_update.record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

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

    async def _process_drive_item(self, item: DriveItem, site_id: str, drive_id: str, users: List[AppUser], modified_after: Optional[str] = None, modified_before: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> Optional[RecordUpdate]:
        """
        Process a single drive item from SharePoint.
        """
        try:
            item_name = getattr(item, 'name', 'Unknown Item')
            item_id = getattr(item, 'id', None)

            if not item_id:
                return None

            is_root = hasattr(item, 'root') and item.root is not None

            external_record_id = None
            # Create a composite ID that includes drive_id for root folders
            if is_root:
                external_record_id = f"{drive_id}:root:{item_id}"
            else:
                external_record_id = item_id


            # Check if item is deleted
            if hasattr(item, 'deleted') and item.deleted is not None:
                return RecordUpdate(
                    record=None,
                    external_record_id=external_record_id,
                    is_new=False,
                    is_updated=False,
                    is_deleted=True,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False
                )

            # Date/Extension filters
            if not self._pass_drive_date_filters(item):
                return None
            if not self._pass_extension_filter(item):
                return None

            # Get existing record
            existing_record = None
            async with self.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_record_id
                )

            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False

            if existing_record:
                current_etag = getattr(item, 'e_tag', None)
                if existing_record.external_revision_id != current_etag:
                    metadata_changed = True
                    is_updated = True

                if hasattr(item, 'file') and item.file and hasattr(item.file, 'hashes') and item.file.hashes:
                    current_hash = getattr(item.file.hashes, 'quick_xor_hash', None)
                    if getattr(existing_record, 'quick_xor_hash', None) != current_hash:
                        content_changed = True
                        is_updated = True

            # Create file record
            file_record = await self._create_file_record(item, drive_id, existing_record)
            if not file_record:
                return None

            permissions = await self._get_item_permissions(site_id, drive_id, item_id)

            if is_root:
                file_record.inherit_permissions = True

            if existing_record and permissions:
                is_updated = True
                permissions_changed = True

            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
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

            is_root = hasattr(item, 'root') and item.root is not None
            external_record_id = None
            if is_root:
                external_record_id = f"{drive_id}:root:{item_id}"
            else:
                external_record_id = item_id

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

                # Check if parent is root - if path ends with '/root:' or is '/drive/root:', parent is root
                # Convert parent_id to composite format if parent is root
                if parent_id and path:
                    # Check if parent is at root level (path ends with '/root:' or '/drive/root:')
                    if path.endswith('/root:') or path == '/drive/root:':
                        parent_id = f"{drive_id}:root:{parent_id}"

            return FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=item_name,
                record_type=record_type,
                record_status=ProgressStatus.NOT_STARTED if not existing_record else existing_record.record_status,
                record_group_type=RecordGroupType.DRIVE,
                parent_record_type=RecordType.FILE,
                external_record_id=external_record_id,
                external_revision_id=getattr(item, 'e_tag', None),
                version=0 if not existing_record else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
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
                inherit_permissions=False,
            )

        except Exception as e:
            self.logger.error(f"❌ Error creating file record: {e}")
            return None

    def _pass_drive_date_filters(self, item: DriveItem) -> bool:
        """
        Checks if the DriveItem passes the configured CREATED and MODIFIED date filters.
        Relies on client-side filtering since OneDrive Delta API does not support $filter.
        """
        # 1. ALWAYS Allow Folders
        # We must sync folders regardless of date to ensure the directory structure
        # exists for any new files that might be inside them.
        if item.folder is not None:
            return True

        # 2. Check Created Date Filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_filter:
            created_after_iso, created_before_iso = created_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps for easy comparison
            item_ts = self._parse_datetime(item.created_date_time)
            start_ts = self._parse_datetime(created_after_iso)
            end_ts = self._parse_datetime(created_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        # 3. Check Modified Date Filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_filter:
            modified_after_iso, modified_before_iso = modified_filter.get_datetime_iso()

            # Use _parse_datetime to get millisecond timestamps
            item_ts = self._parse_datetime(item.last_modified_date_time)
            start_ts = self._parse_datetime(modified_after_iso)
            end_ts = self._parse_datetime(modified_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        return True

    def _pass_extension_filter(self, item: DriveItem) -> bool:
        """
        Checks if the DriveItem passes the configured file extensions filter.

        For MULTISELECT filters:
        - Operator IN: Only allow files with extensions in the selected list
        - Operator NOT_IN: Allow files with extensions NOT in the selected list

        Folders always pass this filter to maintain directory structure.
        """
        # 1. ALWAYS Allow Folders
        if item.folder is not None:
            return True

        # 2. Get the extensions filter
        extensions_filter = self.sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)

        # If no filter configured or filter is empty, allow all files
        if extensions_filter is None or extensions_filter.is_empty():
            return True

        # 3. Get the file extension from the item
        # The extension is stored without the dot (e.g., "pdf", "docx")
        file_extension = None
        if item.name and "." in item.name:
            file_extension = item.name.rsplit(".", 1)[-1].lower()

        # Files without extensions: behavior depends on operator
        # If using IN operator and file has no extension, it won't match any allowed extensions
        # If using NOT_IN operator and file has no extension, it passes (not in excluded list)
        if file_extension is None:
            operator = extensions_filter.get_operator()
            operator_str = operator.value if hasattr(operator, 'value') else str(operator)
            if operator_str == FilterOperator.NOT_IN:
                return True
            return False

        # 4. Get the list of extensions from the filter value
        allowed_extensions = extensions_filter.value
        if not isinstance(allowed_extensions, list):
            return True  # Invalid filter value, allow the file

        # Normalize extensions (lowercase, without dots)
        normalized_extensions = [ext.lower().lstrip(".") for ext in allowed_extensions]

        # 5. Apply the filter based on operator
        operator = extensions_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow files with extensions in the list
            return file_extension in normalized_extensions
        elif operator_str == FilterOperator.NOT_IN:
            # Allow files with extensions NOT in the list
            return file_extension not in normalized_extensions

        # Unknown operator, default to allowing the file
        return True

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
                external_record_id=list_id,
                external_revision_id=getattr(list_obj, 'e_tag', None),
                version=0,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(list_obj, 'web_url', None),
                parent_external_record_id=site_id,
                external_record_group_id=site_id,
                semantic_metadata=metadata
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
                record_type=RecordType.SHAREPOINT_LIST_ITEM,
                record_status=ProgressStatus.NOT_STARTED,
                record_group_type=RecordGroupType.SHAREPOINT_SITE,
                parent_record_type=RecordType.SHAREPOINT_LIST,
                external_record_id=item_id,
                external_revision_id=getattr(item, 'e_tag', None),
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(item, 'web_url', None),
                parent_external_record_id=list_id,
                external_record_group_id=site_id,
                semantic_metadata=metadata
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating list item record: {e}")
            return None

    async def _process_site_pages(self, site_id: str, site_name: str, modified_after: Optional[str] = None, modified_before: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process all pages in a SharePoint site.
        """
        try:
            encoded_site_id = self._construct_site_url(site_id)

            # Always perform a full sync — no incremental filtering via sync point
            request_config = None

            async with self.rate_limiter:
                try:
                    pages_response = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).pages.get(
                            request_configuration=request_config
                        )
                    )

                except Exception as pages_error:
                    if any(term in str(pages_error).lower() for term in [str(HttpStatusCode.FORBIDDEN.value), "accessdenied", str(HttpStatusCode.NOT_FOUND.value), "notfound"]):
                        self.logger.debug(f"Pages not accessible for site {site_id}: {pages_error}")
                        return
                    else:
                        raise pages_error

            if not pages_response or not pages_response.value:
                self.logger.debug(f"No pages found for site {site_id}")
                return

            pages = pages_response.value
            self.logger.debug(f"Found {len(pages)} pages in site")

            pages_processed_count = 0

            for page in pages:
                try:
                    page_id = getattr(page, 'id', None)
                    page_name = getattr(page, 'title', getattr(page, 'name', 'unknown'))

                    if not page_id:
                        self.logger.debug(f"Skipping page with missing ID in site {site_id}")
                        continue

                    # Apply date filters
                    if not self._pass_page_date_filters(page):
                        self.logger.debug(f"⏭️ Skipping page (date filter) '{page_id}' {page_name}")
                        continue

                    page_key = f"{page_id}:{site_id}"
                    if not self._pass_page_ids_filters(page_key):
                        self.logger.debug(f"⏭️ Skipping page (ID filter) '{page_key}' {page_name}")
                        continue

                    # Check the 'created_by' field to skip System Account created pages
                    is_system_page = False
                    created_by = getattr(page, 'created_by', None)

                    if created_by:
                        user_identity = getattr(created_by, 'user', None)
                        display_name = getattr(user_identity, 'display_name', '').lower() if user_identity else ''

                        if display_name == 'system account':
                            is_system_page = True

                    if is_system_page:
                        self.logger.info(f"⏭️ Skipping System Account/Template page: '{page_name}'")
                        continue

                    existing_record = None
                    async with self.data_store_provider.transaction() as tx_store:
                        existing_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=page_id
                        )

                    is_new = existing_record is None
                    is_updated = False
                    metadata_changed = False
                    content_changed = False
                    permissions_changed = False

                    if existing_record:
                        # Use eTag for change detection (same pattern as drive items)
                        current_etag = getattr(page, 'e_tag', None)
                        if existing_record.external_revision_id != current_etag:
                            is_updated = True
                            content_changed = True


                    page_record = await self._create_page_record(page, site_id, site_name, existing_record)

                    if page_record:
                        permissions, should_inherit = await self._get_page_permissions(site_id, page.id)
                        page_record.inherit_permissions = should_inherit


                        if existing_record and permissions:
                            permissions_changed = True
                            is_updated = True

                        if not self.indexing_filters.is_enabled(IndexingFilterKey.PAGES, default=True):
                            page_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        yield (page_record, permissions, RecordUpdate(
                            record=page_record,
                            is_new=is_new,
                            is_updated=is_updated,
                            is_deleted=False,
                            metadata_changed=metadata_changed,
                            content_changed=content_changed,
                            permissions_changed=permissions_changed,
                            new_permissions=permissions
                        ))
                        self.stats['pages_processed'] += 1
                        pages_processed_count += 1

                except Exception as page_error:
                    page_name = getattr(page, 'title', getattr(page, 'name', 'unknown'))
                    self.logger.warning(f"Error processing page '{page_name}': {page_error}")
                    continue

            self.logger.info(f"✅ Processed {pages_processed_count} pages for site {site_name}")

        except Exception as e:
            self.logger.error(f"❌ Error processing pages for site {site_id}: {e}")
            # Don't update sync point on error - will retry from last successful point

    async def _create_page_record(self, page: SitePage, site_id: str, site_name: str, existing_record: Optional[SharePointPageRecord] = None) -> Optional[SharePointPageRecord]:
        """
        Create a record for a SharePoint page.
        """
        try:
            page_id = getattr(page, 'id', None)
            if not page_id:
                return None
            page_name = getattr(page, 'title', None) or getattr(page, 'name', f'Page {page_id}')

            if page_name:
                page_name = f"{page_name} - {site_name}"

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
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=str(page_name)[:255],
                record_type=RecordType.SHAREPOINT_PAGE,
                record_status=ProgressStatus.NOT_STARTED if not existing_record else existing_record.record_status,
                record_group_type=RecordGroupType.SHAREPOINT_SITE,
                external_record_id=page_id,
                external_revision_id=getattr(page, 'e_tag', None),
                version=0 if not existing_record else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=created_at,
                updated_at=updated_at,
                source_created_at=created_at,
                source_updated_at=updated_at,
                weburl=getattr(page, 'web_url', None),
                external_record_group_id=site_id,
                mime_type=MimeTypes.HTML.value,
                inherit_permissions=True,
                semantic_metadata=metadata,
                preview_renderable=False,
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating page record: {e}")
            return None

    def _pass_page_date_filters(self, page: SitePage) -> bool:
        """
        Checks if the SitePage passes the configured CREATED and MODIFIED date filters.
        Client-side filtering since SharePoint Pages API may have limited $filter support.

        Args:
            page: SitePage object from Microsoft Graph API

        Returns:
            bool: True if page passes all date filters, False otherwise
        """
        # 1. Check Created Date Filter
        created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_filter:
            created_after_iso, created_before_iso = created_filter.get_datetime_iso()

            item_ts = self._parse_datetime(getattr(page, 'created_date_time', None))
            start_ts = self._parse_datetime(created_after_iso)
            end_ts = self._parse_datetime(created_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        # 2. Check Modified Date Filter
        modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_filter:
            modified_after_iso, modified_before_iso = modified_filter.get_datetime_iso()

            item_ts = self._parse_datetime(getattr(page, 'last_modified_date_time', None))
            start_ts = self._parse_datetime(modified_after_iso)
            end_ts = self._parse_datetime(modified_before_iso)

            if item_ts is not None:
                if start_ts and item_ts < start_ts:
                    return False
                if end_ts and item_ts > end_ts:
                    return False

        return True

    def _pass_site_ids_filters(self, site_id: str) -> bool:
        """
        Checks if the site passes the configured site IDs filter.

        For MULTISELECT filters:
        - Operator IN: Only allow sites with IDs in the selected list (inclusion list)
        - Operator NOT_IN: Allow sites with IDs NOT in the selected list (exclusion list)

        Args:
            site_id: The SharePoint site ID to check

        Returns:
            True if the site should be synced, False if it should be skipped
        """
        # Get the site IDs filter
        site_ids_filter = self.sync_filters.get(SyncFilterKey.SITE_IDS)

        # If no filter configured or filter is empty, allow all sites
        if site_ids_filter is None or site_ids_filter.is_empty():
            return True

        # Get the list of site IDs from the filter value
        filter_site_ids = site_ids_filter.value
        if not isinstance(filter_site_ids, list):
            return True  # Invalid filter value, allow the site

        # Handle empty or None site_id
        if not site_id:
            self.logger.warning("Site ID is empty or None, skipping")
            return False

        # Apply the filter based on operator
        operator = site_ids_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow sites with IDs in the inclusion list
            return site_id in filter_site_ids
        elif operator_str == FilterOperator.NOT_IN:
            # Allow sites with IDs NOT in the exclusion list
            return site_id not in filter_site_ids

        # Unknown operator, default to allowing the site
        self.logger.warning(f"Unknown filter operator '{operator_str}' for SITE_IDS filter, allowing site")
        return True

    def _pass_drive_key_filters(self, drive_key: str) -> bool:
        """
        Checks if the drive passes the configured drive IDs filter.

        For MULTISELECT filters:
        - Operator IN: Only allow drives with IDs in the selected list (inclusion list)
        - Operator NOT_IN: Allow drives with IDs NOT in the selected list (exclusion list)

        Args:
            drive_key: The SharePoint drive key/ID to check

        Returns:
            True if the drive should be synced, False if it should be skipped
        """
        # Get the drive IDs filter
        drive_ids_filter = self.sync_filters.get(SyncFilterKey.DRIVE_IDS)

        # If no filter configured or filter is empty, allow all drives
        if drive_ids_filter is None or drive_ids_filter.is_empty():
            return True

        # Get the list of drive IDs from the filter value
        filter_drive_ids = drive_ids_filter.value
        if not isinstance(filter_drive_ids, list):
            return True  # Invalid filter value, allow the drive

        # Handle empty or None drive_key
        if not drive_key:
            self.logger.warning("Drive key is empty or None, skipping")
            return False

        # Create set for O(1) lookup (case-sensitive for GUIDs)
        filter_drive_ids_set = {did.strip() for did in filter_drive_ids if did}
        drive_key_normalized = drive_key.strip()

        # Apply the filter based on operator
        operator = drive_ids_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow drives with IDs in the inclusion list
            return drive_key_normalized in filter_drive_ids_set
        elif operator_str == FilterOperator.NOT_IN:
            # Allow drives with IDs NOT in the exclusion list
            return drive_key_normalized not in filter_drive_ids_set

        # Unknown operator, default to allowing the drive
        self.logger.warning(f"Unknown filter operator '{operator_str}' for DRIVE_IDS filter, allowing drive")
        return True

    def _pass_page_ids_filters(self, page_key: str) -> bool:
        """
        Checks if the page passes the configured page IDs filter.

        For MULTISELECT filters:
        - Operator IN: Only allow pages with IDs in the selected list (inclusion list)
        - Operator NOT_IN: Allow pages with IDs NOT in the selected list (exclusion list)

        Args:
            page_key: The SharePoint page key to check

        Returns:
            True if the page should be synced, False if it should be skipped
        """
        # Get the page IDs filter
        page_ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS)

        # If no filter configured or filter is empty, allow all pages
        if page_ids_filter is None or page_ids_filter.is_empty():
            return True

        # Get the list of page IDs from the filter value
        filter_page_ids = page_ids_filter.value
        if not isinstance(filter_page_ids, list):
            return True  # Invalid filter value, allow the page

        # Handle empty or None page_key
        if not page_key:
            self.logger.warning("Page key is empty or None, skipping")
            return False

        # Create set for O(1) lookup (case-sensitive for GUIDs)
        filter_page_ids_set = {pid.strip() for pid in filter_page_ids if pid}
        page_key_normalized = page_key.strip()

        # Apply the filter based on operator
        operator = page_ids_filter.get_operator()
        operator_str = operator.value if hasattr(operator, 'value') else str(operator)

        if operator_str == FilterOperator.IN:
            # Only allow pages with IDs in the inclusion list
            return page_key_normalized in filter_page_ids_set
        elif operator_str == FilterOperator.NOT_IN:
            # Allow pages with IDs NOT in the exclusion list
            return page_key_normalized not in filter_page_ids_set

        # Unknown operator, default to allowing the page
        self.logger.warning(f"Unknown filter operator '{operator_str}' for PAGE_IDS filter, allowing page")
        return True

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
                connector_id=self.connector_id,
                web_url=getattr(drive, 'web_url', None),
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                inherit_permissions=True
            )

        except Exception as e:
            self.logger.debug(f"❌ Error creating document library record group: {e}")
            return None

    def _get_date_filters(self) -> Tuple[Optional[datetime], Optional[datetime], Optional[datetime], Optional[datetime]]:
        """
        Extract date filter values from sync_filters.

        Returns:
            Tuple of (modified_after, modified_before, created_after, created_before)
        """
        modified_after: Optional[datetime] = None
        modified_before: Optional[datetime] = None
        created_after: Optional[datetime] = None
        created_before: Optional[datetime] = None

        # Get modified date filter
        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                modified_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: after {modified_after}")
            if before_iso:
                modified_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying modified date filter: before {modified_before}")

        # Get created date filter
        created_date_filter = self.sync_filters.get(SyncFilterKey.CREATED)
        if created_date_filter and not created_date_filter.is_empty():
            after_iso, before_iso = created_date_filter.get_datetime_iso()
            if after_iso:
                created_after = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: after {created_after}")
            if before_iso:
                created_before = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                self.logger.info(f"Applying created date filter: before {created_before}")

        return modified_after, modified_before, created_after, created_before

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

            self.logger.info(f"🔍 Getting site permissions for site ({site_url})")

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
                            group_title = sp_user.get('Title', 'M365 Group')

                            # Create edge with the M365 group itself instead of expanding members
                            group_key = f"M365_GROUP_{group_id}"

                            self.logger.info(f"      🔵 Found M365 Group: {group_title} (ID: {group_id})")

                            permissions_dict[group_key] = Permission(
                                external_id=group_id,
                                email=None,
                                type=PermissionType.WRITE,
                                entity_type=EntityType.GROUP
                            )

                    # CASE B: It's the "Everyone" Claim (Public Site)
                    elif 'spo-grid-all-users' in login_name:
                        self.logger.info(f"🌍 Site {site_id} is Public (Everyone claim found)")
                        # Add org relation for public sites
                        permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                            type=PermissionType.READ, # Default to READ for public access
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id
                        )

                    # CASE C: It's an AD Security Group (PrincipalType == 4)
                    elif sp_user.get('PrincipalType') == SECURITY_GROUP_TYPE:
                        # Security Group LoginNames often look like: "c:0t.c|tenant|32537252-0676-4c47-a372-2d93563456"
                        # We need to extract that GUID at the end.
                        group_title = sp_user.get('Title', 'Security Group')
                        self.logger.info(f"🔒 Found Security Group: {group_title} ({login_name})")

                        # Regex to capture the GUID after 'tenant|'
                        match = re.search(r'\|tenant\|([0-9a-fA-F-]{36})', login_name)

                        if match:
                            group_id = match.group(1)

                            # This ID is a virtual claim, not a real Graph Group.
                            if group_id == '9908e57b-4444-4a0e-af96-e8ca83c0a0e5':
                                self.logger.info("     -> Found 'Everyone except external users' claim. Skipping.")
                                continue

                            self.logger.info(f"   -> Extracted Group ID: {group_id}")

                            # Create edge with the Security group itself instead of expanding members
                            group_key = f"SECURITY_GROUP_{group_id}"

                            permissions_dict[group_key] = Permission(
                                external_id=group_id,
                                email=None,
                                type=PermissionType.WRITE,
                                entity_type=EntityType.GROUP
                            )
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

                # CASE A: Standard User (Type 1)
                if principal_type == 1:
                    email = v.get('Email') or v.get('UserPrincipalName')
                    add_or_update_permission(email, v.get('Id'), PermissionType.READ)

                # CASE B: M365 Group in visitors
                elif 'federateddirectoryclaimprovider' in login_name:
                    match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', login_name)
                    if match:
                        group_id = match.group(1)
                        group_key = f"M365_GROUP_{group_id}"

                        self.logger.info(f"      🔵 Found M365 Group in visitors: {title} (ID: {group_id})")

                        permissions_dict[group_key] = Permission(
                            external_id=group_id,
                            email=None,
                            type=PermissionType.READ,
                            entity_type=EntityType.GROUP
                        )

                # CASE C: "Everyone" Claims (Modern)
                elif 'spo-grid-all-users' in login_name or 'c:0(.s|true' in login_name:
                    self.logger.info(f"🌍 Site {site_id} is Public (Everyone claim found)")
                    permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                        type=PermissionType.READ,
                        entity_type=EntityType.ORG,
                        external_id=self.data_entities_processor.org_id
                    )

                # CASE D: Security Groups (Type 4)
                elif principal_type == SECURITY_GROUP_TYPE:
                    # Check GUID or Title for "Everyone except external users"
                    if EVERYONE_EXCEPT_EXTERNAL_ID in login_name or 'Everyone except external users' in title:
                        self.logger.info(f"🌍 Site {site_id} is Public ('Everyone' Group found)")
                        permissions_dict['ORGANIZATION_ACCESS'] = Permission(
                            type=PermissionType.READ,
                            entity_type=EntityType.ORG,
                            external_id=self.data_entities_processor.org_id
                        )
                    else:
                        # Handle other security groups in visitors
                        self.logger.info(f"      🔒 Found Security Group in visitors: {title} ({login_name})")
                        match = re.search(r'\|tenant\|([0-9a-fA-F-]{36})', login_name)
                        if match:
                            group_id = match.group(1)
                            group_key = f"SECURITY_GROUP_{group_id}"

                            self.logger.info(f"         -> Extracted Group ID: {group_id}")

                            permissions_dict[group_key] = Permission(
                                external_id=group_id,
                                email=None,
                                type=PermissionType.READ,
                                entity_type=EntityType.GROUP
                            )
                        else:
                            self.logger.warning(f"         -> ⚠️ Could not extract GUID from Security Group LoginName: {login_name}")

            # ==================================================================
            # STEP 4: Process Custom SharePoint Groups -> GROUP entity type
            # ==================================================================
            self.logger.info("📋 Fetching custom SharePoint groups")
            custom_groups = await self._get_custom_sharepoint_groups(site_url, access_token)
            self.logger.info(f"   Found {len(custom_groups)} custom groups")

            for group in custom_groups:
                group_id = group.get('id')
                group_title = group.get('title', '')
                permission_level = group.get('permission_level', PermissionType.READ)

                # Create a unique key for the group (use group_id as identifier)
                group_key = f"GROUP_{group_id}"

                # Skip if already processed (avoid duplicating default groups)
                if group_key in permissions_dict:
                    self.logger.debug(f"      Skipping already processed group: {group_title}")
                    continue

                self.logger.info(f"    👥 Found custom group: {group_title} (ID: {group_id}, Permission: {permission_level.value})")

                # Add group permission with entity type GROUP
                # Use site_id-group_id format to match the format used elsewhere (line 2924)
                unique_group_id = f"{site_id}-{group_id}"

                permissions_dict[group_key] = Permission(
                    external_id=unique_group_id,
                    email=None,  # Groups don't have emails in SharePoint
                    type=permission_level,
                    entity_type=EntityType.GROUP
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

    async def _get_custom_sharepoint_groups(self, site_url: str, access_token: str) -> List[dict]:
        """
        Fetches custom SharePoint groups via role assignments API.
        This includes groups that are not part of the default associated groups.
        Returns list of dicts with group info: {'id', 'title', 'login_name', 'permission_level'}
        """
        # Construct the endpoint to get role assignments
        endpoint = f"{site_url}/_api/web/roleassignments?$expand=RoleDefinitionBindings,Member"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json;odata=verbose"
        }

        SHAREPOINT_GROUP_TYPE = 8

        try:
            self.logger.debug("📡 Fetching custom SharePoint groups via role assignments")

            async with aiohttp.ClientSession() as session:
                async with session.get(endpoint, headers=headers) as response:
                    if response.status == HTTPStatus.OK:
                        data = await response.json()
                        results = data.get('d', {}).get('results', [])

                        custom_groups = []
                        for assignment in results:
                            member = assignment.get('Member', {})
                            principal_type = member.get('PrincipalType')

                            # PrincipalType 8 = SharePoint Group
                            if principal_type == SHAREPOINT_GROUP_TYPE:
                                login_name = member.get('LoginName', '')
                                title = member.get('Title', '')
                                group_id = member.get('Id')

                                # Get permission level (highest level if multiple)
                                role_bindings = assignment.get('RoleDefinitionBindings', {}).get('results', [])
                                permission_levels = [r.get('Name', '') for r in role_bindings]

                                # Determine permission type based on role name
                                perm_type = PermissionType.READ
                                if any(level in ['Full Control', 'Design'] for level in permission_levels):
                                    perm_type = PermissionType.OWNER
                                elif any(level in ['Edit', 'Contribute'] for level in permission_levels):
                                    perm_type = PermissionType.WRITE

                                custom_groups.append({
                                    'id': group_id,
                                    'title': title,
                                    'login_name': login_name,
                                    'permission_level': perm_type
                                })

                        self.logger.debug(f"✅ Found {len(custom_groups)} custom SharePoint groups")
                        return custom_groups
                    else:
                        error_text = await response.text()
                        self.logger.warning(f"⚠️ Failed to fetch role assignments: {response.status} - {error_text}")
                        return []

        except Exception as e:
            self.logger.error(f"❌ Error in _get_custom_sharepoint_groups: {e}")
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

    async def _get_drive_permissions(self, site_id: str, drive_id: str) -> tuple[List[Permission], bool]:
        """Get permissions for a document library via its Root Folder."""
        try:
            permissions = []
            should_inherit = True

            async with self.rate_limiter:
                # 1. Define the base drive client
                drive_client = self.client.drives.by_drive_id(drive_id)

                # 2. Step 1: Get the Root Item first to find its real ID
                # The 'root' builder has .get() but lacks .permissions in the SDK
                root_item = await self._safe_api_call(
                    drive_client.root.get()
                )

                if not root_item or not root_item.id:
                    self.logger.warning(f"⚠️ Could not find root item for drive {drive_id}")
                    return [], True

                # 3. Step 2: Use the 'items' accessor with the ID we just found
                # The 'items' builder correctly has the .permissions property
                perms_response = await self._safe_api_call(
                    drive_client.items.by_drive_item_id(root_item.id).permissions.get()
                )

            if perms_response and perms_response.value:
                should_inherit = self._check_should_inherit(perms_response.value)
                if should_inherit:
                    return [], True
                permissions = await self._convert_to_permissions(perms_response.value, site_id)

            return permissions, False

        except Exception as e:
            self.logger.warning(f"❌ Could not get drive permissions for {drive_id}: {e}")
            return [], True

    async def _get_item_permissions(self, site_id: str, drive_id: str, item_id: str) -> List[Permission]:
        """Get permissions for a drive item.

        Returns:
            List[Permission]: permissions list
        """
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
                permissions = await self._convert_to_permissions(perms_response.value, site_id)

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

    async def _get_page_permissions(self, site_id: str, page_id: str) -> tuple[List[Permission], bool]:
        """
        Get permissions for a SharePoint page using SharePoint REST API.
        Pages are list items in the 'Site Pages' library.

        Note: page_id from Graph Pages API is a GUID, but SharePoint REST API
        items() expects an integer list item ID. We must resolve it first.
        """
        self.logger.debug(f"Page ID: {page_id}")
        try:
            site_web_url = await self._get_site_web_url(site_id)
            if not site_web_url:
                self.logger.debug(f"Could not get site web URL for {site_id}")
                return [], True

            headers = {
                "Authorization": f"Bearer {await self._get_sharepoint_access_token()}",
                "Accept": "application/json;odata=verbose"
            }

            # Use a single ClientSession for all HTTP calls within this method
            # (including the helper _resolve_page_list_item_id) to reuse TCP connections.
            async with aiohttp.ClientSession() as session:
                # Step 1: Resolve the GUID page_id to an integer list item ID
                list_item_id = await self._resolve_page_list_item_id(site_web_url, page_id, headers, session=session)
                if not list_item_id:
                    self.logger.debug(f"Could not resolve list item ID for page {page_id}")
                    return [], True

                # Step 2: Check if the page has unique permissions
                has_unique_url = (
                    f"{site_web_url}/_api/web/lists/getbytitle('Site Pages')"
                    f"/items({list_item_id})/HasUniqueRoleAssignments"
                )

                async with self.rate_limiter:
                    async with session.get(has_unique_url, headers=headers) as resp:
                        if resp.status != HTTPStatus.OK:
                            resp_text = await resp.text()
                            self.logger.debug(
                                f"Failed to check unique permissions for page {page_id}: "
                                f"status={resp.status}, response={resp_text[:500]}"
                            )
                            return [], True
                        data = await resp.json()
                        has_unique = data.get('d', {}).get('HasUniqueRoleAssignments', False)

                if not has_unique:
                    self.logger.debug(f"Page {page_id} (item {list_item_id}) inherits permissions from site")
                    return [], True

                # Step 3: Fetch the actual role assignments
                role_assignments_url = (
                    f"{site_web_url}/_api/web/lists/getbytitle('Site Pages')"
                    f"/items({list_item_id})/roleassignments"
                    f"?$expand=Member,RoleDefinitionBindings"
                )

                async with self.rate_limiter:
                    async with session.get(role_assignments_url, headers=headers) as resp:
                        if resp.status != HTTPStatus.OK:
                            resp_text = await resp.text()
                            self.logger.debug(
                                f"Failed to get role assignments for page {page_id}: "
                                f"status={resp.status}, response={resp_text[:500]}"
                            )
                            return [], True
                        data = await resp.json()

            role_assignments = data.get('d', {}).get('results', [])
            permissions = self._convert_rest_role_assignments_to_permissions(role_assignments, site_id)


            return permissions, False

        except Exception as e:
            self.logger.warning(f"Could not get page permissions for {page_id}: {e}", exc_info=True)
            return [], True


    async def _resolve_page_list_item_id(
        self,
        site_web_url: str,
        page_id: str,
        headers: dict,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> Optional[int]:
        """
        Resolve a Graph Pages API GUID to the integer list item ID
        used by SharePoint REST API.

        Tries multiple strategies:
        1. If page_id is already numeric, use it directly
        2. Filter by GUID field
        3. Filter by UniqueId

        Args:
            session: Optional shared aiohttp.ClientSession. When provided the
                     existing session (and its TCP connection pool) is reused
                     instead of opening a new one for every strategy.
        """
        # If it's already numeric, use directly
        if page_id.isdigit():
            return int(page_id)

        # If no session was provided by the caller, create one that lives for the
        # duration of this method so all three strategies share TCP connections.
        owned_session = None
        if session is None:
            owned_session = aiohttp.ClientSession()
            session = owned_session

        try:
            # Strategy 1: Try using getitembyuniqueid (fastest — direct lookup by GUID)
            unique_id_url = (
                f"{site_web_url}/_api/web/lists/getbytitle('Site Pages')"
                f"/getitembyuniqueid('{page_id}')?$select=Id"
            )

            try:
                async with self.rate_limiter:
                    async with session.get(unique_id_url, headers=headers) as resp:
                        if resp.status == HTTPStatus.OK:
                            data = await resp.json()
                            item_id = data.get('d', {}).get('Id')
                            if item_id:
                                return item_id
                        else:
                            resp_text = await resp.text()
                            self.logger.debug(
                                f"getitembyuniqueid lookup failed: status={resp.status}, "
                                f"response={resp_text[:300]}, falling back to GUID filter"
                            )
            except Exception as e:
                self.logger.debug(f"getitembyuniqueid lookup failed: {e}, falling back to GUID filter")

            # Strategy 2: Fallback — filter Site Pages list by UniqueId (GUID) field
            filter_url = (
                f"{site_web_url}/_api/web/lists/getbytitle('Site Pages')"
                f"/items?$filter=GUID eq '{page_id}'&$select=Id"
            )

            try:
                async with self.rate_limiter:
                    async with session.get(filter_url, headers=headers) as resp:
                        if resp.status == HTTPStatus.OK:
                            data = await resp.json()
                            results = data.get('d', {}).get('results', [])
                            if results:
                                return results[0].get('Id')
                        else:
                            self.logger.debug(
                                f"GUID filter fallback also failed (status {resp.status})"
                            )
            except Exception as e:
                self.logger.debug(f"GUID filter lookup failed: {e}")

            self.logger.warning(f"Could not resolve page GUID {page_id} to list item ID")

        finally:
            # Only close the session if we created it here (not the caller's session)
            if owned_session is not None:
                await owned_session.close()

        self.logger.warning(f"Could not resolve page GUID {page_id} to list item ID")
        return None


    def _convert_rest_role_assignments_to_permissions(
        self, role_assignments: List[dict], site_id: str
    ) -> List[Permission]:
        """Convert SharePoint REST API role assignments to Permission objects."""
        permissions = []
        SharePointGroupType = 8
        SecurityGroupType = 4
        UserType = 1
        MIN_PARTS_FOR_AZURE_GROUP = 3

        for assignment in role_assignments:
            try:
                member = assignment.get('Member', {})
                role_bindings = assignment.get('RoleDefinitionBindings', {}).get('results', [])

                # Skip "Limited Access" — it's a system permission, not meaningful
                # Also skip empty role bindings
                if not role_bindings:
                    continue

                meaningful_roles = [
                    r for r in role_bindings
                    if 'limited access' not in r.get('Name', '').lower()
                ]
                if not meaningful_roles:
                    continue

                # Determine permission type from role definitions (highest wins)
                perm_type = PermissionType.READ  # default
                for role in meaningful_roles:
                    role_name = role.get('Name', '').lower()
                    if 'full control' in role_name or 'owner' in role_name:
                        perm_type = PermissionType.OWNER
                        break  # highest possible, no need to continue
                    elif 'contribute' in role_name or 'edit' in role_name:
                        perm_type = PermissionType.WRITE

                principal_type = member.get('PrincipalType', 0)
                member_id = member.get('Id', '')
                login_name = member.get('LoginName', '')
                title = member.get('Title', '')

                if principal_type == UserType:
                    # Individual user
                    email = None
                    if '|membership|' in login_name:
                        email = login_name.split('|membership|')[-1]

                    permissions.append(Permission(
                        external_id=str(member_id),
                        email=email,
                        type=perm_type,
                        entity_type=EntityType.USER
                    ))

                elif principal_type == SharePointGroupType:
                    # SharePoint group — use composite ID
                    external_id = f"{site_id}-{member_id}"

                    title_lower = title.lower()
                    if 'visitor' in title_lower:
                        self.logger.debug(f"Visitor group in page permissions: {title}")

                    permissions.append(Permission(
                        external_id=external_id,
                        email=None,
                        type=perm_type,
                        entity_type=EntityType.GROUP
                    ))

                elif principal_type == SecurityGroupType:
                    # Security group (Azure AD group)
                    # Format: c:0t.c|tenant|<azure-ad-group-id>
                    azure_group_id = None
                    if '|' in login_name:
                        parts = login_name.split('|')
                        if len(parts) >= MIN_PARTS_FOR_AZURE_GROUP:
                            azure_group_id = parts[-1]

                    permissions.append(Permission(
                        external_id=azure_group_id or str(member_id),
                        email=None,
                        type=perm_type,
                        entity_type=EntityType.GROUP
                    ))

                else:
                    self.logger.debug(
                        f"Unknown principal type {principal_type} for member '{title}'"
                    )

            except Exception as e:
                self.logger.debug(f"Error converting role assignment: {e}")
                continue

        return permissions


    async def _get_site_web_url(self, site_id: str) -> Optional[str]:
        """Get the web URL of a SharePoint site."""
        try:
            encoded_site_id = self._construct_site_url(site_id)
            async with self.rate_limiter:
                site_response = await self._safe_api_call(
                    self.client.sites.by_site_id(encoded_site_id).get()
                )
            if site_response and site_response.web_url:
                return site_response.web_url.rstrip('/')
            return None
        except Exception as e:
            self.logger.debug(f"Error getting site web URL: {e}")
            return None

    def _check_should_inherit(self, raw_permissions: List) -> bool:
        """
        Determine whether a drive's root permissions are inherited from a parent.

        Returns True if any permission has a non-None `inherited_from` field
        (even an ItemReference with all-None fields counts as inherited).
        Returns False only when all permissions have inherited_from=None,
        or when the list is empty.
        """
        if not raw_permissions:
            return False
        return any(
            getattr(perm, "inherited_from", None) is not None
            for perm in raw_permissions
        )

    async def _convert_to_permissions(self, msgraph_permissions: List, site_id: str) -> List[Permission]:
        """
        Convert Microsoft Graph permissions to our Permission model.
        Handles users, Azure AD groups, and SharePoint site groups.

        Returns:
            List[Permission]: converted permissions list

        Distinguishes between:
        1. Pure SharePoint site groups (use composite ID: site_id:site_group_id)
        2. Azure AD groups auto-mapped to site roles (use composite ID: site_id:site_user_id)
        3. True Azure AD groups added directly (use Azure AD group ID)

        Detection is based on site_user.login_name suffix:
        - _o, _m, _v = role mapping (Owners/Members/Visitors)
        - No suffix = true Azure AD group
        """
        permissions = []
        has_org_permission = False  # Track if "Everyone except external users" is found

        # A single ClientSession is shared across all _check_site_group_for_org_access
        # calls in the loop below to avoid opening a new TCP connection per iteration.
        async with aiohttp.ClientSession() as shared_session:
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

                        # Handle group permissions - need to distinguish between real Azure AD groups
                        # and Azure AD groups that are auto-mapped to SharePoint site roles
                        if hasattr(perm.granted_to_v2, 'group') and perm.granted_to_v2.group:
                            group = perm.granted_to_v2.group

                            # Check if this is a role mapping (Owners/Members/Visitors)
                            # by looking at the site_user login_name suffix
                            is_role_mapping = False
                            site_user_id = None

                            if hasattr(perm.granted_to_v2, 'site_user') and perm.granted_to_v2.site_user:
                                site_user = perm.granted_to_v2.site_user
                                login_name = getattr(site_user, 'login_name', '')

                                # Check for role mapping suffixes: _o (owners), _m (members), _v (visitors)
                                if login_name and ('_o' in login_name.split('|')[-1] or
                                                '_m' in login_name.split('|')[-1] or
                                                '_v' in login_name.split('|')[-1]):
                                    is_role_mapping = True
                                    site_user_id = site_user.id

                            # If it's a role mapping, use composite ID (site_id + site_user_id)
                            # Otherwise, use the Azure AD group ID directly
                            if is_role_mapping and site_user_id:
                                external_id = f"{site_id}-{site_user_id}"
                                self.logger.debug(f"Role mapping detected: {group.display_name} -> {external_id}")
                            else:
                                external_id = group.id
                                self.logger.debug(f"True Azure AD group: {group.display_name} -> {external_id}")

                            permissions.append(Permission(
                                external_id=external_id,
                                email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.GROUP
                            ))

                        # Handle pure SharePoint site groups (no Azure AD backing)
                        elif hasattr(perm.granted_to_v2, 'site_group') and perm.granted_to_v2.site_group:
                            site_group = perm.granted_to_v2.site_group
                            # Use composite ID for local site groups
                            external_id = f"{site_id}-{site_group.id}"

                            self.logger.debug(f"Pure SharePoint site group: {site_group.display_name} -> {external_id}")

                            # Check if this group contains "Everyone except external users"
                            if not has_org_permission:
                                has_org_permission = await self._check_site_group_for_org_access(
                                    site_id, site_group.id, session=shared_session
                                )

                            permissions.append(Permission(
                                external_id=external_id,
                                email=site_group.additional_data.get("email", None) if hasattr(site_group, 'additional_data') else None,
                                type=map_msgraph_role_to_permission_type(perm.roles[0] if perm.roles else "read"),
                                entity_type=EntityType.GROUP
                            ))

                    # Handle group permissions in granted_to_identities_v2
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

        # Add organization permission if "Everyone except external users" was found
        if has_org_permission:
            self.logger.info(f"🌍 Adding organization permission for site {site_id}")
            permissions.append(Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=self.data_entities_processor.org_id
            ))

        return permissions

    async def _check_site_group_for_org_access(
        self,
        site_id: str,
        site_group_id: str,
        session: Optional[aiohttp.ClientSession] = None,
    ) -> bool:
        """
        Check if a SharePoint site group contains "Everyone except external users" member.

        Args:
            site_id: The site ID to get the site URL from cache
            site_group_id: The SharePoint site group ID to check
            session: Optional shared aiohttp.ClientSession. When provided the
                     existing session (and its TCP connection pool) is reused
                     across the multiple calls made from _convert_to_permissions.

        Returns:
            bool: True if "Everyone except external users" is found, False otherwise
        """
        EVERYONE_EXCEPT_EXTERNAL_ID = '9908e57b-4444-4a0e-af96-e8ca83c0a0e5'

        try:
            # Get site URL from cache
            site_metadata = self.site_cache.get(site_id)
            if not site_metadata or not site_metadata.site_url:
                self.logger.debug(f"Site metadata/URL not found for {site_id}")
                return False

            site_url = site_metadata.site_url

            # Get SharePoint access token
            access_token = await self._get_sharepoint_access_token()
            if not access_token:
                self.logger.debug("Could not get SharePoint access token")
                return False

            # Fetch group members
            users_url = f"{site_url}/_api/web/sitegroups/GetById({site_group_id})/users"
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json;odata=verbose"
            }

            # Reuse the caller's session when available; otherwise open a short-lived one.
            owned_session = None
            if session is None:
                owned_session = aiohttp.ClientSession()
                session = owned_session

            try:
                async with session.get(users_url, headers=headers) as response:
                    if response.status == HTTPStatus.OK:
                        data = await response.json()
                        users = data.get('d', {}).get('results', [])

                        # Check each member for "Everyone except external users"
                        for user in users:
                            login_name = user.get('LoginName', '')
                            title = user.get('Title', '')

                            # Check if this is "Everyone except external users"
                            if EVERYONE_EXCEPT_EXTERNAL_ID in login_name or 'Everyone except external users' in title:
                                self.logger.info(f"🌍 Site {site_id} has organization access ('Everyone except external users' found in group {site_group_id})")
                                return True

                        return False
                    else:
                        self.logger.debug(f"Failed to fetch site group members: {response.status}")
                        return False
            finally:
                if owned_session is not None:
                    await owned_session.close()

        except Exception as e:
            self.logger.debug(f"Error checking site group for org access: {e}")
            return False

    def _parse_datetime(self, dt_obj) -> Optional[int]:
        """
        Parse datetime object or string to epoch timestamp in milliseconds.

        Args:
            dt_obj: datetime object or ISO format string

        Returns:
            int: Epoch timestamp in milliseconds, or None if parsing fails
        """
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
                await self._sync_azure_ad_groups()
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
                                            connector_id=self.connector_id,
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
                                                                        app_name=self.connector_name,
                                                                        connector_id=self.connector_id,
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
                                                                        app_name=self.connector_name,
                                                                        connector_id=self.connector_id,
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
                                                                    connector_id=self.connector_id,
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

    async def _sync_azure_ad_groups(self) -> None:
        """
        Unified Azure AD groups synchronization.
        - First sync: Uses standard /groups API for clean current state
        - Subsequent syncs: Uses Graph Delta API for incremental changes
        """
        try:
            sync_point_key = generate_record_sync_point_key(
                SyncDataPointType.GROUPS.value,
                "organization",
                self.data_entities_processor.org_id
            )
            sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)

            delta_link = sync_point.get('deltaLink') if sync_point else None

            if delta_link is None:
                self.logger.info("No sync point found, performing initial full sync...")

                # IMPORTANT: Get delta link BEFORE full sync to avoid missing changes
                # that occur during the sync
                delta_link = await self._get_initial_delta_link()

                # Perform the full sync
                await self._perform_initial_full_sync()

                # Only save the delta link if full sync succeeded
                if delta_link:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key,
                        {"nextLink": None, "deltaLink": delta_link}
                    )
                    self.logger.info("Initial sync completed and delta link saved for future syncs")
                else:
                    self.logger.warning("Initial sync completed but no delta link was obtained")
            else:
                self.logger.info("Sync point found, performing incremental delta sync...")
                await self._perform_delta_sync(delta_link, sync_point_key)

        except Exception as e:
            self.logger.error(f"❌ Error in Azure AD groups sync: {e}", exc_info=True)
            raise

    async def _get_initial_delta_link(self) -> Optional[str]:
        """
        Consumes the delta API to obtain a deltaLink checkpoint.
        Called BEFORE initial full sync to ensure no changes are missed.

        Returns:
            The deltaLink string, or None if unable to obtain one.
        """
        self.logger.info("Obtaining delta link checkpoint before full sync...")

        url = "https://graph.microsoft.com/v1.0/groups/delta"

        try:
            while True:
                result = await self.msgraph_client.get_groups_delta_response(url)

                # We ignore the data - just consuming to get the deltaLink
                groups_count = len(result.get('groups', []))
                self.logger.debug(f"Delta initialization: skipping page with {groups_count} groups")

                if result.get('next_link'):
                    url = result.get('next_link')
                elif result.get('delta_link'):
                    self.logger.info("Delta link obtained successfully")
                    return result.get('delta_link')
                else:
                    self.logger.warning("Delta initialization: no next_link or delta_link received")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Error obtaining initial delta link: {e}", exc_info=True)
            return None

    async def _perform_initial_full_sync(self) -> None:
        """
        Performs initial full sync using the standard /groups API.
        Gets current state of all groups and their members.
        """
        self.logger.info("Starting initial full Azure AD group synchronization")

        groups = await self.msgraph_client.get_all_user_groups()

        # Process all groups concurrently using asyncio.gather
        results = await asyncio.gather(
            *[self._process_single_group(group) for group in groups],
            return_exceptions=True
        )

        # Filter out None results (failed groups) and exceptions
        group_with_members = []
        for result in results:
            if isinstance(result, Exception):
                self.logger.error(f"❌ Error processing group: {result}", exc_info=True)
            elif result is not None:
                group_with_members.append(result)

        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)

        self.logger.info(f"Initial full sync completed: processed {len(groups)} Azure AD groups")

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Processes a single group and returns a tuple of (user_group, app_users).
        Returns None if processing fails.
        """
        try:
            members = await self.msgraph_client.get_group_members(group.id)

            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            app_users = []
            for member in members:
                odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in odata_type:
                    app_user = self._create_app_user_from_member(member)
                    if app_user:
                        app_users.append(app_user)
                elif '#microsoft.graph.group' in odata_type:
                    nested_users = await self._get_users_from_nested_group(member)
                    app_users.extend(nested_users)
                else:
                    self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

            return (user_group, app_users)

        except Exception as e:
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}", exc_info=True)
            return None

    async def _perform_delta_sync(self, url: str, sync_point_key: str) -> None:
        """
        Performs incremental sync using the Graph Delta API.
        Processes only changes since the last sync.
        """

        if not url:
            self.logger.warning("No valid URL in sync point, falling back to full delta sync")
            url = "https://graph.microsoft.com/v1.0/groups/delta"

        self.logger.info("Starting incremental delta sync...")

        while True:
            result = await self.msgraph_client.get_groups_delta_response(url)
            groups = result.get('groups', [])

            self.logger.info(f"Fetched delta page with {len(groups)} group changes")

            for group in groups:
                # Handle group DELETION
                if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                    self.logger.info(f"[DELTA] 🗑️ REMOVE Group: {group.id}")
                    success = await self._handle_delete_group(group.id)
                    if not success:
                        self.logger.error(f"❌ Error handling group delete for {group.id}")
                    continue

                # Handle ADD/UPDATE
                self.logger.info(f"[DELTA] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                success = await self._handle_group_create(group)
                if not success:
                    self.logger.error(f"❌ Error handling group create for {group.id}")
                    continue

                # Handle MEMBER changes
                member_changes = (group.additional_data or {}).get('members@delta', [])

                if member_changes:
                    self.logger.info(f"    -> [DELTA] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                for member_change in member_changes:
                    await self._process_member_change(group.id, member_change)

            # Handle pagination and completion
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": url, "deltaLink": None}
                )
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": result.get('delta_link')}
                )
                self.logger.info("Delta sync completed, delta link saved for next run")
                break
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                break

    async def _process_member_change(self, group_id: str, member_change: dict) -> None:
        """
        Processes a single member change from the delta response.
        """
        user_id = member_change.get('id')
        email = await self.msgraph_client.get_user_email(user_id)

        if not email:
            return

        if '@removed' in member_change:
            self.logger.info(f"    -> [DELTA] 👤⛔ REMOVING member: {email} ({user_id}) from group {group_id}")
            success = await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=email,
                connector_id=self.connector_id
            )
            if not success:
                self.logger.error(f"❌ Error removing member {email} from group {group_id}")
        else:
            self.logger.info(f"    -> [DELTA] 👤✨ ADDING member: {email} ({user_id}) to group {group_id}")

    async def _handle_group_create(self, group: Group) -> bool:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.

        Supported member types:
        - User: Added directly
        - Group (nested): Fetch its users and add them (only one level deep)
        - Device, Service Principal, Org Contact: Ignored

        Returns:
            True if group creation/update was successful, False otherwise.
        """
        try:
            # 1. Fetch latest members for this group
            members = await self.msgraph_client.get_group_members(group.id)

            # 2. Create AppUserGroup entity
            user_group = AppUserGroup(
                source_user_group_id=group.id,
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=group.display_name,
                description=group.description,
                source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
            )

            # 3. Create AppUser entities for members (filter by type)
            app_users = []
            for member in members:
                # Check the odata type to determine member type
                odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in odata_type:
                    # Direct user member
                    app_user = self._create_app_user_from_member(member)
                    if app_user:
                        app_users.append(app_user)

                elif '#microsoft.graph.group' in odata_type:
                    # Nested group - fetch its users (one level deep only)
                    nested_users = await self._get_users_from_nested_group(member)
                    app_users.extend(nested_users)

                else:
                    self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

            # 4. Send to processor (wrapped in list as expected by on_new_user_groups)
            await self.data_entities_processor.on_new_user_groups([(user_group, app_users)])

            self.logger.info(f"Processed group creation/update for: {group.display_name} with {len(app_users)} user members")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False


    async def _get_users_from_nested_group(self, nested_group) -> List[AppUser]:
        """
        Fetches users from a nested group (one level deep only).

        Args:
            nested_group: A group member object from Microsoft Graph API

        Returns:
            List of AppUser entities from the nested group
        """
        nested_group_name = getattr(nested_group, 'display_name', nested_group.id)
        self.logger.info(f"Processing nested group member: {nested_group_name}")

        app_users = []

        try:
            nested_members = await self.msgraph_client.get_group_members(nested_group.id)

            for nested_member in nested_members:
                nested_odata_type = getattr(nested_member, 'odata_type', None) or (nested_member.additional_data or {}).get('@odata.type', '')

                if '#microsoft.graph.user' in nested_odata_type:
                    app_user = self._create_app_user_from_member(nested_member)
                    if app_user:
                        app_users.append(app_user)
                else:
                    self.logger.debug(f"Skipping non-user member '{nested_odata_type}' in nested group {nested_group_name}")

        except Exception as e:
            self.logger.warning(f"Failed to fetch members from nested group {nested_group_name}: {e}")

        return app_users


    def _create_app_user_from_member(self, member) -> Optional[AppUser]:
        """
        Helper method to create an AppUser from a Graph API user member.

        Args:
            member: A user object from Microsoft Graph API

        Returns:
            AppUser if successful, None if user has no valid email
        """
        email = getattr(member, 'mail', None) or getattr(member, 'user_principal_name', None)

        if not email:
            self.logger.warning(f"User {member.id} has no email or user_principal_name, skipping")
            return None

        return AppUser(
            app_name=self.connector_name,
            source_user_id=member.id,
            email=email,
            full_name=getattr(member, 'display_name', None),
            source_created_at=member.created_date_time.timestamp() if hasattr(member, 'created_date_time') and member.created_date_time else get_epoch_timestamp_in_ms(),
            connector_id=self.connector_id,
        )

    async def _handle_delete_group(self, group_id: str) -> bool:
        """
        Handles the deletion of a single user group.
        Calls the data processor to remove it from the database.

        Args:
            group_id: The external ID of the group to be deleted.

        Returns:
            True if group deletion was successful, False otherwise.
        """
        try:
            self.logger.info(f"Handling group deletion for: {group_id}")

            # Call the data entities processor to handle the deletion logic
            success = await self.data_entities_processor.on_user_group_deleted(
                external_group_id=group_id,
                connector_id=self.connector_id
            )

            if not success:
                self.logger.error(f"❌ Error handling group delete for {group_id}")
                return False

            return True

        except Exception as e:
            self.logger.error(f"❌ Error handling group delete for {group_id}: {e}", exc_info=True)
            return False

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
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
                if record_update.permissions_changed:
                    self.logger.info(f"Permissions changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_updated_record_permissions(
                        record_update.record,
                        record_update.new_permissions
                    )
                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
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
            self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)

            self.logger.info("✅ Credential successfully reinitialized")

    async def run_sync(self) -> None:
        """Main entry point for the SharePoint connector."""
        start_time = datetime.now()

        # Reset statistics for this sync run
        self.stats = {
            'sites_processed': 0,
            'sites_failed': 0,
            'drives_processed': 0,
            'lists_processed': 0,
            'pages_processed': 0,
            'items_processed': 0,
            'errors_encountered': 0
        }

        try:
            self.logger.info("🚀 Starting SharePoint connector sync")

            # Reinitialize credential to prevent "HTTP transport has already been closed" errors
            # This is necessary because the connector instance may be reused across multiple
            # scheduled runs that are days apart, causing the HTTP session to timeout
            await self._reinitialize_credential_if_needed()

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "sharepointonline", self.connector_id, self.logger
            )

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

            self.logger.info(f"📁 Found {len(sites)} SharePoint sites")

            # Filter sites based on sync filters BEFORE processing
            filtered_sites = []
            for site in sites:
                if not self._pass_site_ids_filters(site.id):
                    self.logger.info(f"⏭️ Skipping excluded site: '{site.display_name or site.name}' (ID: {site.id})")
                    continue
                filtered_sites.append(site)

            self.logger.info(f"📁 {len(filtered_sites)} sites to sync after applying filters (excluded {len(sites) - len(filtered_sites)})")

            if not filtered_sites:
                self.logger.warning("⚠️ No SharePoint sites to sync after applying filters")
                return

            # Create site record groups (only for filtered sites)
            site_record_groups_with_permissions = []
            for site in filtered_sites:  # Use filtered_sites instead of sites
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
                    connector_id=self.connector_id,
                    web_url=site.web_url,
                    group_type=RecordGroupType.SHAREPOINT_SITE,
                    source_created_at=source_created_at,
                    source_updated_at=source_updated_at
                )

                # Get site permissions
                site_permissions = await self._get_site_permissions(site_id)
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
        """Stream record content (file or page) from SharePoint."""
        try:
            if record.record_type == RecordType.FILE:
                # Get signed URL for file download
                signed_url = await self.get_signed_url(record)
                if not signed_url:
                    raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="File not found or access denied")

                return create_stream_record_response(
                    stream_content(signed_url),
                    filename=record.record_name,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            elif record.record_type == RecordType.SHAREPOINT_PAGE:
                self.logger.info("streaming sharepoint page")
                site_id = record.external_record_group_id
                page_id = record.external_record_id

                page_content = await self._get_page_content(site_id, page_id)

                if not page_content:
                    raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Page not found or access denied")

                async def generate_page() -> AsyncGenerator[bytes, None]:
                    yield page_content.encode('utf-8')

                return StreamingResponse(
                    generate_page(),
                    media_type='text/html',
                    headers={
                        "Content-Disposition": f'inline; filename="{record.record_name}.html"'
                    }
                )

            else:
                raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=f"Unsupported record type: {record.record_type}")

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to stream record {record.id}: {e}")
            raise HTTPException(status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value, detail=f"Failed to stream record: {str(e)}")

    async def _get_page_content(self, site_id: str, page_id: str) -> str:
        """
        Fetches SharePoint page content via REST API using the page's UniqueId (GUID).
        Parses dynamic content (Web Parts), downloads images, and performs aggressive cleanup.
        """
        try:
            # 1. Resolve Site URL via Graph
            site_info = await self.client.sites.by_site_id(site_id).get()
            site_url = site_info.web_url

            # 2. Get Token for SharePoint Scope
            access_token = await self._get_sharepoint_access_token()
            if not access_token:
                self.logger.error(f"Failed to obtain SharePoint access token for page {page_id}")
                return None
            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json;odata=verbose"
            }

            # 3. Fetch Page Content
            api_url = f"{site_url}/_api/web/GetFileById('{page_id}')/ListItemAllFields"

            self.logger.info(f"Fetching page content from: {api_url}")

            params = {"$select": "CanvasContent1,Title"}

            timeout_config = httpx.Timeout(30.0, connect=10.0)

            async with httpx.AsyncClient(timeout=timeout_config) as http_client:
                resp = await http_client.get(api_url, headers=headers, params=params)

                if resp.status_code == HttpStatusCode.NOT_FOUND.value:
                    self.logger.warning(f"❌ Page not found via GetFileById: {page_id}")
                    return None

                if resp.status_code != HTTPStatus.OK.value:
                    self.logger.error(f"❌ API Error: {resp.status_code} - {resp.text}")
                    return None

                data = resp.json()
                item = data.get('d', {})
                raw_html = item.get('CanvasContent1', '')

                if not raw_html:
                    self.logger.warning(f"⚠️ Page found but CanvasContent1 is empty: {page_id}")
                    return "<div></div>"

                # 4. Parse HTML
                soup = BeautifulSoup(raw_html, "html.parser")

                # Remove unwanted tags
                for tag in soup(['script', 'style', 'meta', 'link', 'noscript']):
                    tag.decompose()

                # Remove comments
                for c in soup.find_all(string=lambda text: isinstance(text, Comment)):
                    c.extract()

                # --- 4a. PROCESS IMAGES ---
                images = soup.find_all('img')

                for img in images:
                    src = img.get('src')
                    if not src or "data:image" in src or src.lower().startswith(('http:', 'https:')):
                        continue

                    try:
                        clean_path = unquote(src.split('?')[0])
                        image_api_url = f"{site_url}/_api/web/GetFileByServerRelativeUrl('{quote(clean_path)}')/$value"

                        img_resp = await http_client.get(image_api_url, headers=headers)

                        if img_resp.status_code == HTTPStatus.OK.value:
                            content_type = img_resp.headers.get('Content-Type', 'image/jpeg')
                            b64_str = base64.b64encode(img_resp.content).decode('utf-8')
                            img['src'] = f"data:{content_type};base64,{b64_str}"
                    except Exception as img_error:
                        self.logger.warning(f"Failed to process image {src}: {img_error}")

                # --- 4b. PROCESS DYNAMIC WEB PARTS (Lists, Docs, Events) ---
                webparts = soup.find_all("div", attrs={"data-sp-webpartdata": True})

                for wp in webparts:
                    try:
                        wp_data = json.loads(wp["data-sp-webpartdata"])
                        props = wp_data.get("properties", {})

                        list_id = props.get("selectedListId")

                        if list_id:
                            searchable = wp_data.get("serverProcessedContent", {}).get("searchablePlainTexts", {})
                            list_title = (
                                searchable.get("listTitle") or
                                searchable.get("title") or
                                searchable.get("displayTitle") or
                                props.get("webPartTitle") or
                                wp_data.get("title") or
                                "Embedded List"
                            )

                            self.logger.info(f"Found WebPart: '{list_title}' (ID: {list_id})")

                            list_api_url = f"{site_url}/_api/web/lists(guid'{list_id}')/items"
                            list_resp = await http_client.get(list_api_url, headers=headers, timeout=10)

                            if list_resp.status_code == HTTPStatus.OK.value:
                                items = list_resp.json().get('d', {}).get('results', [])

                                if items:
                                    table_html = f"<h3>{list_title}</h3><table border='1' style='border-collapse: collapse; width: 100%;'><thead><tr><th style='padding: 8px;'>Title</th><th style='padding: 8px;'>Info</th></tr></thead><tbody>"

                                    for row_item in items:
                                        text_main = row_item.get('Title') or row_item.get('FileLeafRef') or "Untitled"
                                        text_sub = row_item.get('Description') or row_item.get('EventDate') or row_item.get('Created') or ""
                                        table_html += f"<tr><td style='padding: 8px;'>{text_main}</td><td style='padding: 8px;'>{text_sub}</td></tr>"

                                    table_html += "</tbody></table>"

                                    new_tag = soup.new_tag("div")
                                    new_tag.append(BeautifulSoup(table_html, 'html.parser'))

                                    wp.clear()
                                    wp.append(new_tag)
                            else:
                                self.logger.warning(f"⚠️ Failed to fetch list items for {list_title}: {list_resp.status_code}")

                    except Exception as wp_error:
                        self.logger.warning(f"Failed to process web part: {wp_error}")

                # HTML cleanup - remove unnecessary attributes and clean up structure
                cleaned_html = clean_html_output(soup, logger=self.logger)

                return cleaned_html

        except Exception as e:
            self.logger.error(f"Failed to process SharePoint page {page_id}: {e}", exc_info=True)

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

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records for SharePoint."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} SharePoint records")

            if not self.msgraph_client:
                self.logger.error("MS Graph client not initialized. Call init() first.")
                raise Exception("MS Graph client not initialized. Call init() first.")

            # Get all active users for permissions
            users = await self.data_entities_processor.get_all_active_users()

            # Check records at source for updates
            updated_records = []
            non_updated_records = []

            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(record, users)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB only for records that changed at source
            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB that changed at source")

            # Publish reindex events for non updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non updated records")

        except Exception as e:
            self.logger.error(f"Error during SharePoint reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, record: Record, users: List[AppUser]
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from MS Graph and return data for reindexing if changed."""
        try:
            record_type = record.record_type
            external_record_id = record.external_record_id

            if not external_record_id:
                self.logger.warning(f"Missing external_record_id for record {record.id}")
                return None

            # Handle different record types
            if record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_file_record(record, users)
            elif record_type == RecordType.SHAREPOINT_PAGE:
                return await self._check_and_fetch_updated_page_record(record)
            elif record_type == RecordType.SHAREPOINT_LIST_ITEM:
                return await self._check_and_fetch_updated_list_item_record(record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking SharePoint record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_file_record(
        self, record: Record, users: List[AppUser]
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check and fetch updated file record from a SharePoint drive."""
        try:
            drive_id = record.external_record_group_id
            item_id = record.external_record_id

            if not drive_id or not item_id:
                self.logger.warning(f"Missing drive_id or item_id for file record {record.id}")
                return None

            # Fetch fresh item from MS Graph
            async with self.rate_limiter:
                item = await self._safe_api_call(
                    self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()
                )

            if not item:
                self.logger.warning(f"File item {item_id} not found at source")
                return None

            # Check if item is deleted
            if hasattr(item, 'deleted') and item.deleted is not None:
                self.logger.info(f"File item {item_id} has been deleted at source")
                return None

            # Get the site_id from the drive to fetch permissions
            # We need to extract site_id from parent_reference or use a cached mapping
            site_id = None
            if hasattr(item, 'parent_reference') and item.parent_reference:
                site_id = getattr(item.parent_reference, 'site_id', None)

            # Use existing logic to detect changes and create FileRecord
            record_update = await self._process_drive_item(
                item=item,
                site_id=site_id or "",
                drive_id=drive_id,
                users=users
            )

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update
            if record_update.is_updated:
                self.logger.info(f"File record {item_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions or [])

            return None

        except Exception as e:
            self.logger.error(f"Error checking SharePoint file record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_page_record(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check and fetch updated page record from SharePoint."""
        try:
            site_id = record.external_record_group_id
            page_id = record.external_record_id

            if not site_id or not page_id:
                self.logger.warning(f"Missing site_id or page_id for page record {record.id}")
                return None

            encoded_site_id = self._construct_site_url(site_id)

            # Fetch fresh page from MS Graph
            async with self.rate_limiter:
                try:
                    page = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).pages.by_base_site_page_id(page_id).get()
                    )
                except Exception as page_error:
                    if any(term in str(page_error).lower() for term in [str(HttpStatusCode.FORBIDDEN.value), "accessdenied", str(HttpStatusCode.NOT_FOUND.value), "notfound"]):
                        self.logger.warning(f"Page {page_id} not accessible or not found at source")
                        return None
                    raise page_error

            if not page:
                self.logger.warning(f"Page {page_id} not found at source")
                return None

            # Check for changes using eTag
            current_etag = getattr(page, 'e_tag', None)
            is_updated = record.external_revision_id != current_etag

            if not is_updated:
                return None

            self.logger.info(f"Page record {page_id} has changed at source. Updating.")

            # Get site name for page record creation
            site_name = ""
            try:
                async with self.rate_limiter:
                    site_response = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).get()
                    )
                if site_response:
                    site_name = getattr(site_response, 'display_name', '') or getattr(site_response, 'name', '')
            except Exception as e:
                self.logger.warning(f"Could not fetch site name for site {site_id}: {e}")

            # Create updated page record
            page_record = await self._create_page_record(page, site_id, site_name, record)

            if not page_record:
                return None

            # Get permissions
            permissions = await self._get_page_permissions(site_id, page_id)

            # Ensure we keep the internal DB ID
            page_record.id = record.id

            return (page_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking SharePoint page record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_list_item_record(
        self, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Check and fetch updated list item record from SharePoint."""
        try:
            # Extract site_id and list_id from semantic_metadata
            semantic_metadata = getattr(record, 'semantic_metadata', {}) or {}
            site_id = semantic_metadata.get('site_id')
            list_id = semantic_metadata.get('list_id')
            item_id = record.external_record_id

            if not site_id or not list_id or not item_id:
                self.logger.warning(f"Missing site_id, list_id, or item_id for list item record {record.id}")
                return None

            encoded_site_id = self._construct_site_url(site_id)

            # Fetch fresh list item from MS Graph
            async with self.rate_limiter:
                try:
                    item = await self._safe_api_call(
                        self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.by_list_item_id(item_id).get()
                    )
                except Exception as item_error:
                    if any(term in str(item_error).lower() for term in [str(HttpStatusCode.FORBIDDEN.value), "accessdenied", str(HttpStatusCode.NOT_FOUND.value), "notfound"]):
                        self.logger.warning(f"List item {item_id} not accessible or not found at source")
                        return None
                    raise item_error

            if not item:
                self.logger.warning(f"List item {item_id} not found at source")
                return None

            # Check for changes using eTag
            current_etag = getattr(item, 'e_tag', None)
            is_updated = record.external_revision_id != current_etag

            if not is_updated:
                return None

            self.logger.info(f"List item record {item_id} has changed at source. Updating.")

            # Create updated list item record
            list_item_record = await self._create_list_item_record(item, site_id, list_id)

            if not list_item_record:
                return None

            # Get permissions
            permissions = await self._get_list_item_permissions(site_id, list_id, item_id)

            # Ensure we keep the internal DB ID
            list_item_record.id = record.id

            return (list_item_record, permissions)

        except Exception as e:
            self.logger.error(f"Error checking SharePoint list item record {record.id} at source: {e}")
            return None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint sites and pages."""

        if filter_key == SyncFilterKey.SITE_IDS:
            return await self._get_site_options(page, limit, search)
        elif filter_key == SyncFilterKey.PAGE_IDS:
            return await self._get_page_options(page, limit, search)
        elif filter_key == SyncFilterKey.DRIVE_IDS:  # Add this
            return await self._get_document_library_options(page, limit, search)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_site_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint sites."""

        search_query = search.strip() if search else ""
        search_query = f"{search_query}*"

        # 1. Get Raw Result
        raw_result = await self.msgraph_client.search_query(
            entity_types=["site"],
            query=search_query,
            page=page,
            limit=limit,
            region=self.tenant_region
        )

        options = []
        total = 0

        # 2. Process Raw Result locally
        if raw_result:
            additional_data = getattr(raw_result, 'additional_data', {}) or {}
            value_list = additional_data.get('value', [])

            for search_resp in value_list:
                hits_containers = search_resp.get('hitsContainers', [])

                for container in hits_containers:
                    # Update total from the container
                    total = container.get('total', 0)

                    for hit in container.get('hits', []):
                        resource = hit.get('resource', {})

                        # A. Extract ID and WebUrl
                        site_id = resource.get('id')
                        web_url = resource.get('webUrl')

                        if not site_id and not web_url:
                            continue

                        # B. Determine Label (Display Name > Name > WebUrl)
                        label = (
                            resource.get('displayName') or
                            resource.get('name') or
                            web_url or
                            "Unknown Site"
                        )

                        options.append(FilterOption(
                            id=site_id or web_url,
                            label=label
                        ))

        has_more = (page * limit) < total

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=has_more,
            cursor=None
        )

    async def _get_page_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint pages."""

        search_query = search.strip() if search else ""
        full_query = f"{search_query}* filetype:aspx"

        # 1. Get Raw Result
        raw_result = await self.msgraph_client.search_query(
            entity_types=["listItem"],
            query=full_query,
            page=page,
            limit=limit,
            region=self.tenant_region
        )

        options = []
        total = 0

        if raw_result:
            additional_data = getattr(raw_result, 'additional_data', {}) or {}
            value_list = additional_data.get('value', [])

            for search_resp in value_list:
                hits_containers = search_resp.get('hitsContainers', [])

                for container in hits_containers:
                    total = container.get('total', 0)

                    for hit in container.get('hits', []):
                        resource = hit.get('resource', {})

                        item_id = resource.get('id')
                        web_url = resource.get('webUrl')
                        site_id = resource.get('parentReference', {}).get('siteId')

                        # Skip System Account pages (templates)
                        created_by = resource.get('createdBy', {})
                        user = created_by.get('user', {})
                        user.get('displayName', '').lower()

                        if not site_id:
                            continue


                        if not item_id:
                            continue

                        # --- 1. Extract Page Name ---
                        name = resource.get('name')
                        if not name and web_url:
                            try:
                                # Get filename: .../SitePages/Home.aspx -> Home.aspx
                                name = unquote(web_url.split('/')[-1])
                            except Exception:
                                name = "Unknown Page"

                        page_label = name or resource.get('displayName') or "Unknown Page"

                        # --- 2. Extract Site Name from URL ---
                        # Structure is usually: https://tenant.sharepoint.com/sites/SiteName/...
                        site_name = "Unknown Site"
                        if web_url and "/sites/" in web_url:
                            try:
                                # Split at /sites/ and take the chunk immediately after
                                after_sites = web_url.split("/sites/")[1]
                                raw_site_name = after_sites.split("/")[0]
                                site_name = unquote(raw_site_name)
                            except Exception:
                                pass
                        elif web_url:
                            site_name = "Root Site"

                        # --- 3. Format Final Label ---
                        final_label = f"{page_label} ({site_name})"
                        page_key = f"{item_id}:{site_id}"

                        options.append(FilterOption(
                            id=page_key,
                            label=final_label
                        ))

        has_more = (page * limit) < total

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=has_more,
            cursor=None
        )

    async def _get_document_library_options(
        self,
        page: int,
        limit: int,
        search: Optional[str]
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint document libraries (drives only)."""

        search_query = search.strip() if search else ""

        # Search query - best effort to narrow down
        full_query = f"{search_query}* contentclass:STS_List_DocumentLibrary"

        raw_result = await self.msgraph_client.search_query(
            entity_types=["list"],
            query=full_query,
            page=page,
            limit=limit,
            region=self.tenant_region
        )

        options = []
        total = 0
        seen_keys = set()  # To handle duplicates

        # System library names to exclude
        SYSTEM_LIBRARY_NAMES = {
            'FormServerTemplates',
            'SiteAssets',
            'Style Library',
            'SitePages',
            '_catalogs',
            'appdata',
            'AppCatalog',
        }

        if raw_result:
            additional_data = getattr(raw_result, 'additional_data', {}) or {}
            value_list = additional_data.get('value', [])

            for search_resp in value_list:
                hits_containers = search_resp.get('hitsContainers', [])

                for container in hits_containers:
                    total = container.get('total', 0)

                    for hit in container.get('hits', []):
                        resource = hit.get('resource', {})
                        summary = hit.get('summary', '')

                        list_id = resource.get('id')
                        web_url = resource.get('webUrl', '')
                        name = resource.get('name', '')
                        display_name = resource.get('displayName', '')
                        site_id = resource.get('parentReference', {}).get('siteId', '')
                        if not site_id:
                            continue
                        unique_key = f"{list_id}:{site_id}"

                        # Skip if no ID or already seen (duplicates in results)
                        if not list_id or unique_key in seen_keys:
                            continue

                        # === FILTERING LOGIC ===
                        # 1. Must be a document library: summary contains 'DocumentLibrary',
                        #    or summary is empty (treat as document library by default)
                        is_document_library = 'DocumentLibrary' in summary or not summary
                        if not is_document_library:
                            continue
                        # 2. Skip SharePoint Lists (URL contains /Lists/)
                        if '/Lists/' in web_url:
                            continue
                        # 3. Skip system libraries by name
                        if name in SYSTEM_LIBRARY_NAMES:
                            continue
                        # 4. Skip contentstorage (internal Microsoft storage)
                        if '/contentstorage/' in web_url:
                            continue
                        # 5. Skip OneDrive personal libraries
                        if re.match(r'^https?://[^/]*-my\.sharepoint\.com(/|$)', web_url, re.IGNORECASE):
                            continue
                        # 6. Skip calendar/events URLs
                        if '/calendar.aspx' in web_url:
                            continue
                        # === PASSED ALL FILTERS - This is a valid drive ===

                        seen_keys.add(unique_key)

                        # Extract library name
                        library_name = display_name or name or "Unknown Library"

                        # Extract site name from URL
                        site_name = "Unknown Site"
                        if web_url and "/sites/" in web_url:
                            try:
                                after_sites = web_url.split("/sites/")[1]
                                raw_site_name = after_sites.split("/")[0]
                                site_name = unquote(raw_site_name)
                            except Exception:
                                pass
                        elif web_url:
                            site_name = "Root Site"

                        # Clean up display name if it already contains site info
                        # e.g., "IT Team Site - Documents" -> "Documents"
                        if " - " in library_name and site_name != "Unknown Site":
                            library_name = library_name.split(" - ")[-1]

                        final_label = f"{library_name} ({site_name})"
                        normalized_url = self._normalize_document_library_url(web_url)

                        options.append(FilterOption(
                            id=normalized_url,
                            label=final_label
                        ))

        has_more = (page * limit) < total

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=has_more,
            cursor=None
        )

    def _normalize_document_library_url(self, web_url: str) -> str:
        """
        Normalize a SharePoint document library URL to a consistent format.

        Examples:
            Input:  'https://pipeshubinc.sharepoint.com/sites/okay/Shared%20Documents'
            Output: 'pipeshubinc.sharepoint.com/sites/okay/shared documents'

            Input:  'https://pipeshubinc.sharepoint.com/sites/ITTeamSite/Shared Documents/Forms/AllItems.aspx'
            Output: 'pipeshubinc.sharepoint.com/sites/itteamsite/shared documents'
        """
        if not web_url:
            return ""

        # Decode URL encoding (%20 -> space, etc.)
        decoded_url = unquote(web_url)

        # Remove protocol (https://)
        if "://" in decoded_url:
            decoded_url = decoded_url.split("://", 1)[1]

        # Remove trailing /Forms/AllItems.aspx or similar view pages
        # Common patterns: /Forms/AllItems.aspx, /Forms/AllItems.aspx?viewid=...
        if "/Forms/" in decoded_url:
            decoded_url = decoded_url.split("/Forms/")[0]

        # Lowercase for consistent comparison
        decoded_url = decoded_url.lower()

        # Remove trailing slashes
        decoded_url = decoded_url.rstrip("/")

        return decoded_url

    @classmethod
    async def create_connector(cls, logger: Logger,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return SharePointConnector(logger, data_entities_processor, data_store_provider, config_service, connector_id)

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
