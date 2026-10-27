import asyncio
import base64
import binascii
import hashlib
import json
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
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import unquote, urlparse

import httpx
from aiolimiter import AsyncLimiter
from azure.core.exceptions import (
    ClientAuthenticationError,
)
from azure.identity import CredentialUnavailableError
from azure.identity.aio import CertificateCredential, ClientSecretCredential
from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.drives.item.drive_item_request_builder import (
    DriveItemRequestBuilder,
)
from msgraph.generated.groups.item.group_item_request_builder import (
    GroupItemRequestBuilder,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.list_item import ListItem
from msgraph.generated.models.site import Site
from msgraph.generated.models.site_page import SitePage
from msgraph.generated.models.subscription import Subscription

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector, ConnectorInitError
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO, IconPaths
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
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
from app.connectors.core.registry.types import (
    FileContentValidationRule,
    ValidationRuleType,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.microsoft.common.apps import SharePointOnlineApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_roles_to_permission_type,
)
from app.connectors.sources.microsoft.sharepoint_online.container_tracker import (
    ContainerTracker,
)
from app.connectors.sources.microsoft.sharepoint_online.drive_reconciler import (
    DriveReconciler,
)
from app.connectors.sources.microsoft.sharepoint_online.everyone_groups import (
    everyone_group_for_claim,
    everyone_groups,
)
from app.connectors.sources.microsoft.sharepoint_online.graph_permission_check import (
    GRAPH_PERMISSION,
    graph_sites_fullcontrol_missing,
)
from app.connectors.sources.microsoft.sharepoint_online.sync_health import (
    SharePointSyncIncomplete,
    SyncHealth,
)
from app.connectors.sources.microsoft.common.sharepoint_permission_scope import (
    GUID_PATTERN,
    SHAREPOINT_ITEMS_PAGE_SIZE,
    SHAREPOINT_REST_HEADERS,
    SHAREPOINT_REST_MAX_ATTEMPTS,
    SHAREPOINT_REST_MAX_RETRY_AFTER_S,
    SHAREPOINT_REST_RETRY_STATUSES,
    ListPermissionScope,
    drive_item_unique_id,
)
from app.connectors.sources.microsoft.common.sign_in_errors import known_sign_in_problem
from app.connectors.sources.microsoft.sharepoint_online.utils import (
    clean_html_output,
    get_sharepoint_auth_notification,
    sanitize_azure_error,
)
from app.exceptions.graph_db_exceptions import GraphQueryError
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
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms


# Constants for SharePoint site ID composite format
# A composite site ID has the format: "hostname,site-id,web-id"
COMPOSITE_SITE_ID_COMMA_COUNT = 2
COMPOSITE_SITE_ID_PARTS_COUNT = 3


def _sharepoint_filter_options_graph_batch_size(filtered_page_limit: int) -> int:
    """Microsoft Search ``size`` for one batch when building site / library filter options."""

    # Many raw hits are dropped (MySite URLs, lists vs libraries, duplicates, query filters).
    # Request roughly 2× the UI page size so a single Graph call can still populate ``limit``.
    widened = max(filtered_page_limit * 2, 20)

    # Enforce sane ``size``: avoid tiny payloads (noise + round-trips); cap keeps each batch
    # bounded for Graph/payload sizing (typically ≤100 hits per Search request anyway).
    return max(20, min(100, widened))


def _sharepoint_filter_options_max_search_batches(
    page: int,
    filtered_page_limit: int,
    graph_batch: int,
    *,
    resume_with_cursor: bool,
) -> int:
    """Max sequential Graph Search calls allowed for one ``get_filter_options`"""

    batch = max(graph_batch, 1)

    # Latency budget guide: dropdown requests run this many awaited ``search_query`` calls in series
    # (often rate-limited). ``ceiling`` is the hard worst-case knob for wall-clock wait; tune it
    # alongside product timeouts (assume ~few hundred ms per call ⇒ tens of seconds at ceiling).
    floor_batches = 8
    ceiling = 40

    if resume_with_cursor:
        # Resume path has no "(page−1)×limit" skip; we only need batches to collect another
        # ``filtered_page_limit`` accepted rows plus filtering slack. Scale with limit versus
        # ``batch`` (~hits per Graph call); +12 is fixed headroom when accept rate is poor.
        scaled = filtered_page_limit * 6 // batch + 12
    else:
        # Stateless page N skips (N−1)×limit accepted items in-app, which can require scanning
        # far more raw search rows. Rough linear growth with page × limit normalized by batch;
        # +15 is baseline slack for sparse accept rates on early pages.
        scaled = page * filtered_page_limit * 10 // batch + 15

    # Clamp: never fewer than ``floor_batches`` (avoid bailing too early); never more than ``ceiling``
    # (remainder continues via ``has_more`` / cursor—not one giant HTTP stall).
    return min(ceiling, max(floor_batches, scaled))


# Microsoft Search ``from`` accepted in inbound filter-option cursors. Payloads above this are
# rejected so clients cannot force unbounded offsets (Graph quota abuse). Honestly issued
# cursors may grow arbitrarily and are unchanged on encode—only tampered/doctored payloads hit this.
SHAREPOINT_FILTER_OPTIONS_MAX_SEARCH_FROM_OFFSET = 250_000


# Guard inflated accepted-skip values in unsigned cursors (abuse / corruption).
SHAREPOINT_FILTER_OPTIONS_MAX_CURSOR_ACCEPTED_SKIP = 250_000


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


# Stored records written before version 2 carry a copy of every inherited grant. Their next
# run reads every item again so those copies are replaced (see run_sync).
PERMISSION_MODEL_VERSION = 2
PERMISSION_MODEL_SYNC_POINT_KEY = "sharepoint-permission-model"
SITES_SYNC_POINT_KEY = "sharepoint-known-sites"
INACTIVE_USERS_SYNC_POINT_KEY = "sharepoint-inactive-users"
GRAPH_PERMISSION_SYNC_POINT_KEY = "sharepoint-graph-permission"
FILTERED_SITES_SYNC_POINT_KEY = "sharepoint-filtered-out-sites"
GRAPH_NOT_FOUND = HTTPStatus.NOT_FOUND
T = TypeVar("T")
GRAPH_RESYNC_REQUIRED = HTTPStatus.GONE
# The Graph SDK retries 429, 503 and 504 itself; a listing also gets another try after these, and
# after a dropped connection, before a run gives up on what it lists.
GRAPH_TRANSIENT_STATUSES = frozenset({HTTPStatus.INTERNAL_SERVER_ERROR, HTTPStatus.BAD_GATEWAY})
GRAPH_READ_ATTEMPTS = 3

# Yielded by _process_drive_delta after each page: what came before it must be stored before the
# library's delta token moves past it.
DRIVE_CHECKPOINT = ("drive-checkpoint",)

SP_PRINCIPAL_USER = 1
SP_PRINCIPAL_SECURITY_GROUP = 4
SP_PRINCIPAL_SHAREPOINT_GROUP = 8
# RoleTypeKind 1 is Limited Access: SharePoint grants it so a principal can reach a
# uniquely shared child, and it opens nothing at the level it is assigned on.
SP_LIMITED_ACCESS_ROLE_KIND = 1
SP_LIMITED_ACCESS_ROLE_NAMES = frozenset({"Limited Access", "Web-Only Limited Access"})

TENANT_CLAIM_PATTERN = re.compile(r'\|tenant\|([0-9a-fA-F-]{36})')
DIRECTORY_ROLE_ODATA_TYPE = "#microsoft.graph.directoryRole"
M365_OWNERS_DISPLAY_SUFFIX = " Owners"

_PERMISSION_RANK = {PermissionType.READ: 0, PermissionType.WRITE: 1, PermissionType.OWNER: 2}


def is_org_wide_claim(login_name: str) -> bool:
    """Everyone except external users, or Everyone."""
    return 'spo-grid-all-users' in login_name or 'c:0(.s|true' in login_name


def is_m365_owners_claim(login_name: str) -> bool:
    return 'federateddirectoryclaimprovider' in login_name.lower() and login_name.lower().endswith('_o')


def role_bindings_to_permission_type(bindings: list[dict]) -> PermissionType | None:
    """The role a SharePoint role assignment grants, or None when it grants only Limited Access."""
    granting = [
        b for b in bindings
        if b.get('RoleTypeKind') != SP_LIMITED_ACCESS_ROLE_KIND and b.get('Name') not in SP_LIMITED_ACCESS_ROLE_NAMES
    ]
    if not granting:
        return None
    names = [b.get('Name', '') for b in granting]
    if any(name in ('Full Control', 'Design') for name in names):
        return PermissionType.OWNER
    if any(name in ('Edit', 'Contribute') for name in names):
        return PermissionType.WRITE
    return PermissionType.READ


def unique_file_grants(role_grants: list[Permission], graph_permissions: list[Permission]) -> list[Permission]:
    """Role assignments from SharePoint, plus link grants that only Graph reports."""
    merged: dict[tuple[str, str], Permission] = {}
    for permission in role_grants:
        merge_permission(merged, permission)
    for permission in graph_permissions:
        if permission.entity_type == EntityType.ORG or permission.link_grant:
            merge_permission(merged, permission)
    return list(merged.values())


def merge_permission(permissions: dict[tuple[str, str], Permission], permission: Permission) -> None:
    """Keep one grant per principal, at the strongest role it is given."""
    principal = permission.email or permission.external_id or ''
    key = (permission.entity_type.value, principal.lower())
    current = permissions.get(key)
    if current is None or _PERMISSION_RANK[permission.type] > _PERMISSION_RANK[current.type]:
        permissions[key] = permission



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
                name="sharepointDomain",
                display_name="SharePoint Domain",
                placeholder="https://your-domain.sharepoint.com",
                description="Your SharePoint domain URL",
                field_type="URL",
                max_length=2000
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
                name="certificate",
                display_name="Client Certificate",
                placeholder="Click to upload certificate file (.crt, .cer, or .pem)",
                description="Upload the client certificate for certificate-based authentication. Required together with Private Key.",
                field_type="FILE",
                required=True,
                min_length=0,
                accepted_file_types=[".crt", ".cer", ".pem"],
                validation_rules=[
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_CONTAINS,
                        pattern="-----BEGIN CERTIFICATE-----",
                        error_message="Invalid certificate format. Must contain BEGIN CERTIFICATE marker.",
                    ),
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_CONTAINS,
                        pattern="-----END CERTIFICATE-----",
                        error_message="Invalid certificate format. Must contain END CERTIFICATE marker.",
                    ),
                ],
                is_secret=True,
            ),
            AuthField(
                name="privateKey",
                display_name="Private Key (PKCS#8)",
                placeholder="Click to upload private key file (.key or .pem)",
                description="Upload the private key in PKCS#8 format. Required together with Client Certificate.",
                field_type="FILE",
                required=True,
                min_length=0,
                accepted_file_types=[".key", ".pem"],
                validation_rules=[
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_NOT_CONTAINS,
                        pattern="-----BEGIN RSA PRIVATE KEY-----",
                        error_message=(
                            "Private key must be in PKCS#8 format, not RSA. Convert with: "
                            "openssl pkcs8 -topk8 -inform PEM -outform PEM -in privatekey.key "
                            "-out privatekey.key -nocrypt"
                        ),
                    ),
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_CONTAINS,
                        pattern="-----BEGIN PRIVATE KEY-----",
                        error_message="Invalid private key format. Must contain BEGIN PRIVATE KEY marker.",
                    ),
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_CONTAINS,
                        pattern="-----END PRIVATE KEY-----",
                        error_message="Invalid private key format. Must contain END PRIVATE KEY marker.",
                    ),
                    FileContentValidationRule(
                        type=ValidationRuleType.TEXT_NOT_CONTAINS,
                        pattern="-----BEGIN ENCRYPTED PRIVATE KEY-----",
                        error_message=(
                            "Private key must not be encrypted. Use the -nocrypt flag during conversion."
                        ),
                    ),
                ],
                is_secret=True,
            ),
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SHAREPOINT_ONLINE.value))
        .add_documentation_link(DocumentationLink(
            "SharePoint Online API Setup",
            "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
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
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
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
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(SharePointOnlineApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by)

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

        # Per site, the site groups (ids as strings) that hold a tenant-wide claim, and
        # which one. Rebuilt by _sync_user_groups.
        self._org_wide_site_groups: dict[str, dict[str, set[str]]] = {}
        # Per site, the site groups whose members this run could not read.
        self._unreadable_site_groups: dict[str, set[str]] = {}
        # Sites whose SharePoint groups this run listed in full; a group grant elsewhere is unknown.
        self._site_groups_read: set[str] = set()
        self._reset_permission_caches()
        self._health = SyncHealth()
        # Emails of accounts the tenant has disabled or deleted, from this run's user list.
        self._inactive_emails: set[str] = set()
        self._drive_roots: dict[str, str] = {}
        self._discovered_site_ids: set[str] = set()
        # Sites the sync filters excluded this run; what PipesHub stores for them is deleted.
        self._filtered_out_site_ids: set[str] = set()
        # Every SharePoint REST read asks for a token, so a credential that stopped working is reported once a run.
        self._token_failure_notified = False
        self._discovery_complete = False
        # Set for a run that has to read every item again (see PERMISSION_MODEL_VERSION).
        self._rewrite_all_items = False
        # Set for a run that has to read every Entra group's members again.
        self._refresh_all_groups = False

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

    def _reset_permission_caches(self) -> None:
        """Per-run caches of permission lookups that every item of a site or library repeats."""
        # None marks a lookup that failed, so it is not retried for every item.
        self._library_scopes: dict[str, ListPermissionScope | None] = {}
        self._site_pages_scopes: dict[str, ListPermissionScope | None] = {}
        self._m365_group_owners: dict[str, list[dict]] = {}
        self._unified_groups: dict[str, bool | None] = {}
        self._directory_object_types: dict[str, str | None] = {}
        self._directory_role_members: dict[str, list[dict]] = {}

    @staticmethod
    def _build_credential(credential_cls: type, **kwargs: str) -> object:
        try:
            return credential_cls(**kwargs)
        except ValueError as error:
            # azure-identity checks the tenant ID's format here, before any request; other ValueErrors
            # (such as a certificate it cannot parse) are not about the tenant.
            if "tenant" not in str(error).lower():
                raise
            raise ConnectorInitError(
                "The Directory (Tenant) ID is not valid. Check it in the app registration."
            ) from error

    async def init(self) -> bool:
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ConnectorInitError("SharePoint Online credentials not found")

        credentials_config = config.get("auth", {})
        if not credentials_config:
            self.logger.error("❌ SharePoint Online credentials not found")
            raise ConnectorInitError("SharePoint Online credentials not found")

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
            raise ConnectorInitError("Incomplete SharePoint Online credentials. Ensure tenantId, clientId, and sharepointDomain are configured.")

        # Check for one valid authentication method
        has_certificate = certificate_data and private_key_data
        has_client_secret = client_secret

        if not (has_certificate or has_client_secret):
            self.logger.error("❌ Authentication credentials missing. Provide either clientSecret or certificate + private key.")
            raise ConnectorInitError("Authentication credentials missing. Provide either clientSecret or certificate + private key.")

        credentials = SharePointCredentials(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
            sharepoint_domain=normalized_sharepoint_domain,
            has_admin_consent=True,
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
                self.credential = self._build_credential(
                    CertificateCredential,
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
                if isinstance(cert_error, ConnectorInitError):
                    raise
                raise ConnectorInitError(
                    "The certificate or private key in the connector's settings could not be read. Paste the "
                    "certificate and its private key in PEM format, then try again."
                ) from cert_error

        elif has_client_secret:
            self.credential = self._build_credential(
                ClientSecretCredential,
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
            sanitized_error = sanitize_azure_error(token_error)
            self.logger.error(f"❌ Failed to initialize credential: {sanitized_error}")
            # Clean up temp certificate file if it was created
            if self.temp_cert_file:
                try:
                    os.unlink(self.temp_cert_file.name)
                except OSError:
                    pass
            # An AADSTS code not in the table may be transient (e.g. AADSTS90033), so it stays a 5xx.
            problem = known_sign_in_problem(token_error)
            if problem:
                raise ConnectorInitError(problem) from token_error
            raise ValueError(
                f"Failed to initialize SharePoint credential: {sanitized_error}"
            )
        
        try:
            await self._get_sharepoint_access_token(raise_errors=True)
        except Exception as token_error:
            sanitized_error = sanitize_azure_error(token_error)
            self.logger.error(f"❌ Failed to obtain SharePoint access token during initialization: {sanitized_error}")
            problem = known_sign_in_problem(token_error, resource_setting="SharePoint domain")
            if problem:
                raise ConnectorInitError(
                    f"Could not get a SharePoint access token for {self.sharepoint_domain}. {problem}"
                ) from token_error
            raise ValueError(f"Unable to acquire a SharePoint access token: {sanitized_error}") from token_error

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

    async def _safe_api_call(self, api_call: Callable[[], Awaitable[T]], max_retries: int = 3, retry_delay: float = 1.0) -> T | None:
        """
        Enhanced safe API call execution with intelligent retry logic and error handling.

        ``api_call`` is a zero-argument callable returning a fresh awaitable, because a
        coroutine object can only be awaited once and each retry needs a new request.
        """

        for attempt in range(max_retries + 1):
            try:
                result = await api_call()
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

    def _cache_site(self, site: Site, *, is_root: bool = False, parent_site_id: str | None = None) -> None:
        self.site_cache[site.id] = SiteMetadata(
            site_id=site.id,
            site_url=site.web_url,
            site_name=site.display_name or site.name,
            is_root=is_root,
            parent_site_id=parent_site_id,
            created_at=site.created_date_time,
            updated_at=site.last_modified_date_time
        )

    def _is_personal_site(self, site: Site) -> bool:
        hostname = urllib.parse.urlparse(getattr(site, 'web_url', None) or '').hostname
        return bool(hostname and re.fullmatch(r"[a-zA-Z0-9-]+-my\.sharepoint\.com", hostname))

    @staticmethod
    def _is_embedded_container(site: Site) -> bool:
        """A SharePoint Embedded container (the storage of Loop and similar apps), which Graph's site search
        lists. Sites.* application permissions can't read it: a live tenant answered 403 for its subsites."""
        return '/contentstorage/' in (getattr(site, 'web_url', None) or '')

    async def _discover_sites(self) -> tuple[list[Site], bool]:
        """Every site of the tenant but OneDrive sites, subsites at any depth included, and whether the
        listing is complete. Graph's site list leaves subsites out, so each site's subsites are walked."""
        sites: dict[str, Site] = {}
        complete = True
        try:
            root_site = await self._graph_read(self.client.sites.by_site_id("root").get)
            if root_site:
                sites[root_site.id] = root_site
                self._cache_site(root_site, is_root=True)
        except Exception as e:
            self.logger.warning(f"⚠️ Root site access failed: {e}")
            complete = False

        try:
            response = await self._graph_read(self.client.sites.get)
            while response is not None:
                for site in response.value or []:
                    if self.filters.get('exclude_onedrive_sites') and self._is_personal_site(site):
                        continue
                    if self._is_embedded_container(site):
                        continue
                    if site.id not in sites:
                        sites[site.id] = site
                        self._cache_site(site)
                next_link = getattr(response, 'odata_next_link', None)
                if not next_link:
                    break
                response = await self._graph_read(self.client.sites.with_url(next_link).get)
        except Exception as e:
            self.logger.warning(f"⚠️ Site listing failed: {e}")
            complete = False

        if self.enable_subsite_discovery:
            pending = list(sites.values())
            while pending:
                parent = pending.pop()
                subsites = await self._get_subsites(parent.id)
                if subsites is None:
                    complete = False
                    continue
                for subsite in subsites:
                    if subsite.id not in sites:
                        sites[subsite.id] = subsite
                        pending.append(subsite)
        return list(sites.values()), complete

    async def _get_all_sites(self) -> List[Site]:
        """Sites in the sync's scope: valid ids that pass the site filter. A site that could not be listed is
        reported to the run's health."""
        sites, complete = await self._discover_sites()
        if not complete:
            self._health.skipped("site discovery", "part of the site listing failed")
        valid_sites = []
        self._filtered_out_site_ids = set()
        for site in sites:
            if not self._validate_site_id(site.id):
                self.logger.warning(f"⚠️ Invalid site ID format, skipping: '{site.id}' ({site.display_name or site.name})")
                continue
            if not self._pass_site_ids_filters(site.id):
                self.logger.info(f"⏭️ Skipping excluded site: '{site.display_name or site.name}' (ID: {site.id})")
                self._filtered_out_site_ids.add(site.id)
                continue
            valid_sites.append(site)
        self.logger.info(f"SharePoint sites in sync scope: {len(valid_sites)} (from {len(sites)} discovered)")
        self._discovered_site_ids = {site.id for site in sites}
        self._discovery_complete = complete
        return valid_sites

    async def _get_subsites(self, site_id: str) -> List[Site] | None:
        """The direct subsites of a site, every page of them; None when they can't be listed."""
        subsites = []
        builder = self.client.sites.by_site_id(self._construct_site_url(site_id)).sites
        try:
            response = await self._graph_read(builder.get)
            while response is not None:
                for subsite in response.value or []:
                    subsites.append(subsite)
                    self._cache_site(subsite, parent_site_id=site_id)
                next_link = getattr(response, 'odata_next_link', None)
                if not next_link:
                    break
                response = await self._graph_read(builder.with_url(next_link).get)
        except Exception as e:
            self.logger.warning(f"⚠️ Subsite discovery failed for site {site_id}: {e}")
            return None
        return subsites

    async def _remove_deleted_sites(self) -> None:
        """Remove the sites the last complete discovery had and Graph now answers 404 for."""
        if not getattr(self, '_discovery_complete', False):
            return
        tracker = ContainerTracker(self.drive_delta_sync_point, SITES_SYNC_POINT_KEY)
        known = await tracker.known()
        keep = set(self._discovered_site_ids)
        for site_id in known - self._discovered_site_ids:
            if await self._graph_says_gone(lambda s=site_id: self.client.sites.by_site_id(s).get()):
                self.logger.info(f"🗑️ Site {site_id} is gone at the source; removing it")
                await self.data_entities_processor.on_record_group_deleted(site_id, self.connector_id)
            else:
                keep.add(site_id)
        await tracker.remember(keep)

    async def _sync_site_content(self, site_record_group: RecordGroup) -> None:
        """Sync a site's libraries and pages.

        Records are stored before the delta cursor that covers them moves on: the delta generator yields a
        checkpoint after each page, and the batch is written at that checkpoint. A write that fails stops
        this site's run where it is, so the next run reads that page again.
        """
        site_id = site_record_group.external_group_id
        site_name = site_record_group.name
        self.logger.info(f"Starting sync for site: '{site_name}' (ID: {site_id})")
        batch_records: list[tuple[Record, list[Permission]]] = []
        total_processed = 0

        async def flush() -> None:
            nonlocal batch_records
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                batch_records = []

        async def consume(item_tuple: tuple) -> None:
            nonlocal total_processed
            if item_tuple is DRIVE_CHECKPOINT:
                await flush()
                return
            record, permissions, record_update = item_tuple
            if record_update.is_deleted or record_update.is_updated:
                await flush()
                await self._handle_record_updates(record_update)
            elif record and await self._replace_unchanged_record_permissions(record, permissions, record_update):
                return
            elif record:
                batch_records.append((record, permissions))
                total_processed += 1
                if len(batch_records) >= self.batch_size:
                    await flush()

        try:
            modified_after, modified_before, created_after, created_before = self._get_date_filters()
            async for item_tuple in self._process_site_drives(site_id, internal_site_record_group_id=site_record_group.id, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before):
                await consume(item_tuple)
            await flush()
            async for item_tuple in self._process_site_pages(site_id, site_name, modified_after, modified_before, created_after, created_before):
                await consume(item_tuple)
            await flush()
            self.logger.info(f"Completed sync for site '{site_name}' - processed {total_processed} items")
            self.stats['sites_processed'] += 1
        except Exception as e:
            self.logger.error(f"❌ Failed to sync site '{site_name}': {e}")
            self.stats['sites_failed'] += 1
            raise

    async def _replace_unchanged_record_permissions(
        self, record: Record, permissions: list[Permission], record_update: RecordUpdate
    ) -> bool:
        """Replace the stored grants of a stored record the source reports with an unchanged eTag.

        A sharing-only change leaves the eTag alone, and on_new_records only adds
        grant edges, so a grant revoked at the source would never be removed.
        Returns False when the record must go through on_new_records instead.
        """
        if record_update.is_new or not record_update.permissions_changed:
            return False
        await self.data_entities_processor.on_updated_record_permissions(record, permissions)
        return True

    async def _list_site_drives(self, site_id: str) -> list | None:
        """Every document library Graph lists for the site, following @odata.nextLink; None when the listing fails."""
        drives = []
        builder = self.client.sites.by_site_id(self._normalize_site_id(site_id)).drives
        try:
            response = await self._graph_read(builder.get)
            while response is not None:
                drives.extend(response.value or [])
                next_link = getattr(response, 'odata_next_link', None)
                if not next_link:
                    break
                response = await self._graph_read(builder.with_url(next_link).get)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not list the libraries of site {site_id}: {e}")
            return None
        return drives

    async def _graph_read(self, call: Callable[[], Awaitable[T]]) -> T:
        """A Graph read, tried again after a transient server error or a dropped connection."""
        for attempt in range(1, GRAPH_READ_ATTEMPTS + 1):
            try:
                async with self.rate_limiter:
                    return await call()
            except Exception as e:
                status = getattr(e, 'response_status_code', None)
                transient = status in GRAPH_TRANSIENT_STATUSES or (
                    status is None and isinstance(e, (httpx.TransportError, asyncio.TimeoutError))
                )
                if not transient or attempt == GRAPH_READ_ATTEMPTS:
                    raise
                await asyncio.sleep(attempt)
        # Reached only when a context manager swallowed the error: the read gave no answer.
        raise RuntimeError(f"Graph read gave no answer after {GRAPH_READ_ATTEMPTS} attempts")

    async def _graph_says_gone(self, call: Callable[[], Any]) -> bool:
        """Whether Graph answers 404 for a container: the one answer that lets a missing container be removed."""
        try:
            async with self.rate_limiter:
                await call()
        except Exception as e:
            return getattr(e, 'response_status_code', None) == GRAPH_NOT_FOUND
        return False

    async def _remove_filtered_out(
        self, tracker_key: str, excluded_ids: set[str], forget: Callable[[str], Awaitable[None]]
    ) -> None:
        """Delete what PipesHub stores for sites or libraries the sync filters now exclude.

        They still exist at the source, so nothing reports them gone, and a full sync only unlinks what it did
        not see. Each is deleted once, when it becomes excluded: the tracker remembers the ones already removed,
        and forgets one the filters include again, so a later exclusion deletes it again. ``forget`` drops the
        container's delta tokens, so including it again lists it in full instead of reading changes only.
        """
        tracker = ContainerTracker(self.drive_delta_sync_point, tracker_key)
        removed = await tracker.known() & excluded_ids
        for group_id in sorted(excluded_ids - removed):
            await self.data_entities_processor.on_record_group_deleted(group_id, self.connector_id)
            await forget(group_id)
            self.logger.info(f"🗑️ Removed {group_id}: the sync filters exclude it")
            removed.add(group_id)
        await tracker.remember(removed)

    async def _forget_library(self, site_id: str, drive_id: str) -> None:
        await self.drive_delta_sync_point.delete_sync_point(
            generate_record_sync_point_key(SharePointRecordType.DOCUMENT_LIBRARY.value, site_id, drive_id)
        )

    async def _forget_site(self, site_id: str) -> None:
        tracker = ContainerTracker(self.drive_delta_sync_point, f"sharepoint-known-libraries/{site_id}")
        for drive_id in await tracker.known():
            await self._forget_library(site_id, drive_id)
        await tracker.remember(set())

    async def _remove_deleted_libraries(self, site_id: str, listed_drive_ids: set[str]) -> None:
        """Remove the libraries of a site that its last listing had and Graph now says are gone."""
        tracker = ContainerTracker(self.drive_delta_sync_point, f"sharepoint-known-libraries/{site_id}")
        known = await tracker.known()
        keep = set(listed_drive_ids)
        for drive_id in known - listed_drive_ids:
            if await self._graph_says_gone(lambda d=drive_id: self.client.drives.by_drive_id(d).get()):
                self.logger.info(f"🗑️ Library {drive_id} of site {site_id} is gone at the source; removing it")
                await self.data_entities_processor.on_record_group_deleted(drive_id, self.connector_id)
            else:
                keep.add(drive_id)
        await tracker.remember(keep)

    @staticmethod
    def _is_site_pages_library(drive: object) -> bool:
        """The Site Pages library: its pages are synced as pages, not as .aspx files."""
        web_url = getattr(drive, 'web_url', None) or ''
        return urllib.parse.unquote(urllib.parse.urlparse(web_url).path).rstrip('/').lower().endswith('/sitepages')

    async def _process_site_drives(self, site_id: str, internal_site_record_group_id: str, modified_after: Optional[datetime] = None, modified_before: Optional[datetime] = None, created_after: Optional[datetime] = None, created_before: Optional[datetime] = None) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """
        Process all document libraries (drives) in a SharePoint site.
        """
        drives = await self._list_site_drives(site_id)
        if drives is None:
            self._health.skipped(f"libraries of site {site_id}", "listing failed")
            return

        listed_drive_ids = {drive.id for drive in drives if getattr(drive, 'id', None)}
        await self._remove_deleted_libraries(site_id, listed_drive_ids)

        drive_record_groups_with_permissions = []
        filtered_out_drive_ids: set[str] = set()
        for drive in drives:
            drive_name = getattr(drive, 'name', 'Unknown Drive')
            drive_id = getattr(drive, 'id', None)
            if not drive_id:
                self.logger.warning(f"⚠️ No drive ID found for drive {drive_name}")
                continue
            if self._is_site_pages_library(drive):
                continue

            normalized_url = self._normalize_document_library_url(getattr(drive, 'web_url', None))
            if not self._pass_drive_key_filters(normalized_url):
                self.logger.debug(f"Skipping drive (Document Library filter) '{normalized_url}' {drive_name}")
                filtered_out_drive_ids.add(drive_id)
                continue

            drive_record_group = self._create_document_library_record_group(drive, site_id, internal_site_record_group_id)
            if drive_record_group:
                drive_record_group.inherit_permissions, library_grants = await self._get_library_access(site_id, drive)
                if library_grants is None:
                    self._health.skipped(f"permissions of library {drive_id}", "unreadable")
                drive_record_groups_with_permissions.append((drive_record_group, library_grants))

        await self._remove_filtered_out(
            f"sharepoint-filtered-out-libraries/{site_id}", filtered_out_drive_ids,
            lambda drive_id: self._forget_library(site_id, drive_id),
        )
        self.logger.info(f"Found {len(drive_record_groups_with_permissions)} drive record groups to process.")
        if drive_record_groups_with_permissions:
            await self.data_entities_processor.on_new_record_groups(drive_record_groups_with_permissions)

        for drive_record_group, _permissions in drive_record_groups_with_permissions:
            async for item_tuple in self._process_drive_delta(site_id, drive_record_group.external_group_id, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before):
                yield item_tuple

    async def _drive_root_id(self, drive_id: str) -> str | None:
        """The id of a library's root folder item, read once per run (delta's order does not put it first)."""
        if drive_id not in self._drive_roots:
            try:
                root = await self._graph_read(self.client.drives.by_drive_id(drive_id).root.get)
                self._drive_roots[drive_id] = getattr(root, 'id', None)
            except Exception as e:
                self.logger.warning(f"⚠️ Could not read the root folder of library {drive_id}: {e}")
                return None
        return self._drive_roots[drive_id]

    @staticmethod
    def _delta_external_id(item: DriveItem, drive_id: str) -> str:
        if getattr(item, 'root', None) is not None:
            return f"{drive_id}:root:{item.id}"
        return item.id

    async def _process_drive_delta(self, site_id: str, drive_id: str, modified_after: Optional[datetime] = None, modified_before: Optional[datetime] = None, created_after: Optional[datetime] = None, created_before: Optional[datetime] = None) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
        """Items of one library changed since its delta token, or all of them when there is none.

        After every page it yields DRIVE_CHECKPOINT; the consumer stores what it has before the token is
        saved. A page that could not be read or stored leaves the stored token where it was, so the next
        run reads it again. A listing without a token that runs to the end without an error also says
        what the library no longer has, and those records are removed (DriveReconciler).
        """
        sync_point_key = generate_record_sync_point_key(
            SharePointRecordType.DOCUMENT_LIBRARY.value,
            site_id,
            drive_id
        )
        root_url = f"https://graph.microsoft.com/v1.0/sites/{self._construct_site_url(site_id)}/drives/{drive_id}/root/delta"
        sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
        delta_url = None if self._rewrite_all_items else (
            (sync_point.get('deltaLink') or sync_point.get('nextLink')) if sync_point else None
        )
        if delta_url:
            parsed_url = urllib.parse.urlparse(delta_url)
            if not (parsed_url.scheme == 'https' and parsed_url.hostname == 'graph.microsoft.com'):
                self.logger.error(f"❌ Invalid delta URL format for drive {drive_id}; starting a full listing")
                delta_url = None

        if await self._drive_root_id(drive_id) is None:
            # Without the root's id, the items at the top of the library can't be placed under it, and
            # stored ones would be moved under a placeholder; the library waits for the next run.
            self._health.skipped(f"root folder of library {drive_id}", "unreadable")
            return
        users = await self.data_entities_processor.get_all_active_users()
        # Only a listing that starts without a token in this very run sees every item.
        listing_everything = delta_url is None
        listed: set[str] = set()
        resynced = False
        # An item that could not be processed keeps the token where it was, so the next run reads it
        # again; the items after it are still processed in this one.
        item_failed = False
        unreadable_noted: set[str] = set()
        url = delta_url or root_url

        while url:
            try:
                result = await self._graph_read(lambda u=url: self.msgraph_client.get_delta_response_sharepoint(u))
            except Exception as e:
                if getattr(e, 'response_status_code', None) == GRAPH_RESYNC_REQUIRED and not resynced:
                    self.logger.warning(f"⚠️ Graph refused the delta token of library {drive_id}; listing it in full")
                    resynced, listing_everything, listed, url = True, True, set(), root_url
                    continue
                self._health.skipped(f"changes of library {drive_id}", e)
                return

            for item in result.get('drive_items', []):
                if not getattr(item, 'id', None):
                    continue
                listed.add(self._delta_external_id(item, drive_id))
                try:
                    record_update = await self._process_drive_item(item, site_id, drive_id, users, modified_after=modified_after, modified_before=modified_before, created_after=created_after, created_before=created_before)
                except Exception as item_error:
                    self._health.skipped(f"item {item.id} of library {drive_id}", item_error)
                    item_failed = True
                    continue
                if record_update is None:
                    continue
                if not record_update.permissions_known:
                    item_failed = True
                    if drive_id not in unreadable_noted:
                        unreadable_noted.add(drive_id)
                        self._health.skipped(f"permissions in library {drive_id}", "unreadable")
                if record_update.is_deleted:
                    yield (None, [], record_update)
                elif record_update.record is not None:
                    if not self.indexing_filters.is_enabled(IndexingFilterKey.DOCUMENTS, default=True):
                        record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    yield (record_update.record, record_update.new_permissions or [], record_update)
                    self.stats['items_processed'] += 1
                await asyncio.sleep(0)

            yield DRIVE_CHECKPOINT
            next_link = result.get('next_link')
            if next_link:
                if not item_failed:
                    await self.drive_delta_sync_point.update_sync_point(sync_point_key, sync_point_data={"nextLink": next_link, "deltaLink": None})
                url = next_link
                continue
            if listing_everything:
                await DriveReconciler(self.data_entities_processor, self.connector_id, self.logger).remove_unlisted(
                    drive_id, listed, lambda external_id: self._drive_item_gone(drive_id, external_id)
                )
            if listing_everything and not item_failed:
                await self._remove_root_placeholder(drive_id)
            if not item_failed:
                await self.drive_delta_sync_point.update_sync_point(sync_point_key, sync_point_data={"nextLink": None, "deltaLink": result.get('delta_link')})
            url = None

    def _parent_external_id(self, item: DriveItem, drive_id: str) -> str | None:
        """The external id of the item's parent record. Delta never returns parentReference.path, so a child
        of the library's root folder is told by the root's id; the path is a fallback for other reads."""
        reference = getattr(item, 'parent_reference', None)
        if getattr(item, 'root', None) is not None or not reference:
            return None
        parent_id = getattr(reference, 'id', None)
        path = getattr(reference, 'path', None)
        root_id = self._drive_roots.get(drive_id)
        if parent_id and ((root_id and parent_id == root_id) or (path and (path.endswith('/root:') or path == '/drive/root:'))):
            return f"{drive_id}:root:{parent_id}"
        return parent_id

    async def _remove_root_placeholder(self, drive_id: str) -> None:
        """Remove the placeholder older runs left for the library's root folder under its bare id."""
        root_id = self._drive_roots.get(drive_id)
        if not root_id:
            return
        stale = await self.data_entities_processor.get_record_by_external_id(self.connector_id, root_id)
        if stale is not None and stale.is_placeholder:
            await self.data_entities_processor.on_record_deleted(record_id=stale.id)

    async def _drive_item_gone(self, drive_id: str, external_id: str) -> bool:
        item_id = external_id.split(":root:")[-1]
        return await self._graph_says_gone(
            lambda: self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()
        )

    async def _process_drive_item(self, item: DriveItem, site_id: str, drive_id: str, users: List[AppUser], modified_after: Optional[str] = None, modified_before: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> Optional[RecordUpdate]:
        """What a delta item means for its record. Raises when it can't be told, so the run does not move
        past it; None when the item is out of scope and nothing is stored for it.

        An item that inherits stores no grants of its own: it reaches its folder's, and the library's and
        the site's through them, so a grant removed above it is gone for it too. Only an item with its own
        permissions stores its role assignments, plus the people a sharing link lists.
        """
        item_id = getattr(item, 'id', None)
        if not item_id:
            return None
        is_root = getattr(item, 'root', None) is not None
        external_record_id = self._delta_external_id(item, drive_id)

        if getattr(item, 'deleted', None) is not None:
            return RecordUpdate(record=None, external_record_id=external_record_id, is_new=False, is_updated=False,
                                is_deleted=True, metadata_changed=False, content_changed=False,
                                permissions_changed=False)

        existing_record = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, external_record_id
        )
        out_of_scope = not self._pass_drive_date_filters(item) or not self._pass_extension_filter(item) or (
            getattr(item, 'folder', None) is None and not is_root and '.' not in (getattr(item, 'name', None) or '')
        )
        if out_of_scope:
            if existing_record:
                # The item is still there but no longer in the sync's scope (a rename to an excluded
                # extension, say): it leaves PipesHub like a deleted one.
                return RecordUpdate(record=None, external_record_id=external_record_id, is_new=False,
                                    is_updated=False, is_deleted=True, metadata_changed=False,
                                    content_changed=False, permissions_changed=False)
            return None

        is_new = existing_record is None
        is_updated = False
        metadata_changed = False
        content_changed = False
        if existing_record:
            # A parent that moved without the item changing: records written before the library's root
            # folder was recognised in delta hang under a placeholder of the root's bare id.
            if existing_record.external_revision_id != getattr(item, 'e_tag', None) or (
                existing_record.parent_external_record_id != self._parent_external_id(item, drive_id)
            ):
                metadata_changed = True
                is_updated = True
            if getattr(item, 'file', None) and getattr(item.file, 'hashes', None):
                current_hash = getattr(item.file.hashes, 'quick_xor_hash', None)
                # The lookup above returns a base Record, which has no file hashes. If the file
                # node is missing or can't be read, the content can't be shown to be unchanged, so re-index.
                try:
                    existing_file_record = await self.data_entities_processor.get_file_record_by_id(existing_record.id)
                except GraphQueryError as read_error:
                    self.logger.warning(f"Re-indexing {item_id}: its stored file record could not be read: {read_error}")
                    existing_file_record = None
                if existing_file_record is None or existing_file_record.quick_xor_hash != current_hash:
                    content_changed = True
                    is_updated = True

        if is_root:
            access: tuple[bool, list[Permission]] | None = (True, [])
        else:
            access = await self._drive_item_access(item, drive_id, site_id)
        if access is None and (is_new or not is_updated):
            # Nothing can be written without its permissions: a new item would be stored with the
            # wrong access, and an unchanged one has nothing else to write.
            return RecordUpdate(record=None, external_record_id=external_record_id, is_new=is_new,
                                is_updated=False, is_deleted=False, metadata_changed=False, content_changed=False,
                                permissions_changed=False, permissions_known=False)

        file_record = await self._create_file_record(item, drive_id, existing_record)
        if not file_record:
            raise RuntimeError(f"item {item_id} could not be mapped to a record")
        if access is None:
            # An edit is still synced; the record keeps its stored access and inheritance.
            file_record.inherit_permissions = await self._inheritance_when_unreadable(
                CollectionNames.RECORDS.value, existing_record.id
            )
            permissions: list[Permission] = []
        else:
            file_record.inherit_permissions, permissions = access

        return RecordUpdate(
            record=file_record,
            is_new=is_new,
            is_updated=is_updated,
            is_deleted=False,
            metadata_changed=metadata_changed,
            content_changed=content_changed,
            permissions_changed=access is not None,
            new_permissions=permissions,
            permissions_known=access is not None,
        )

    async def _drive_item_access(self, item: DriveItem, drive_id: str, site_id: str) -> tuple[bool, list[Permission]] | None:
        """``(inherit_permissions, grants)`` for a drive item, or None when they can't be read."""
        has_unique_permissions = await self._drive_item_has_unique_permissions(item, drive_id)
        if has_unique_permissions is None:
            return None
        if not has_unique_permissions:
            return True, []
        role_grants = await self._unique_drive_item_role_grants(item, drive_id, site_id)
        graph_permissions = await self._get_item_permissions(site_id, drive_id, item.id)
        if role_grants is None or graph_permissions is None:
            return None
        return False, unique_file_grants(role_grants, graph_permissions)

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

            parent_id = self._parent_external_id(item, drive_id)
            path = getattr(item.parent_reference, 'path', None) if getattr(item, 'parent_reference', None) else None

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
                # A download URL is a credential for an hour; it is read when the file is opened.
                signed_url=None,
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
                    lambda: self.client.sites.by_site_id(encoded_site_id).lists.get()
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
                                lambda token=skip_token: self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.get(
                                    request_configuration={
                                        "query_parameters": {"$skiptoken": token}
                                    }
                                )
                            )
                        else:
                            items_response = await self._safe_api_call(
                                lambda: self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.get()
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

    async def _list_site_pages(self, site_id: str) -> list | None:
        """Every page of the site, following @odata.nextLink; None when the listing fails.

        The whole list is read on every run. A filter on lastModifiedDateTime compared the source's
        timestamps with this server's clock, so an edit stamped a moment before the last run's start was
        never read, and it could not tell a deleted page from an unchanged one.
        """
        pages = []
        builder = self.client.sites.by_site_id(self._construct_site_url(site_id)).pages
        try:
            response = await self._graph_read(builder.get)
            while response is not None:
                pages.extend(response.value or [])
                next_link = getattr(response, 'odata_next_link', None)
                if not next_link:
                    break
                response = await self._graph_read(builder.with_url(next_link).get)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not list the pages of site {site_id}: {e}")
            return None
        return pages

    async def _remove_deleted_pages(self, site_id: str, listed_page_ids: set[str]) -> None:
        """Remove the stored pages of a site that its complete page listing no longer has."""
        stored = []
        after_key = None
        while True:
            page = await self.data_entities_processor.get_records_in_record_group(
                self.connector_id, site_id, 500, after_key=after_key
            )
            stored.extend(page)
            if len(page) < 500:
                break
            after_key = page[-1].id
        pages = self.client.sites.by_site_id(self._construct_site_url(site_id)).pages
        for record in stored:
            if record.record_type != RecordType.SHAREPOINT_PAGE or record.external_record_id in listed_page_ids:
                continue
            # Out of the listing is a candidate; only Graph's 404 for the page itself removes it.
            if await self._graph_says_gone(lambda p=record.external_record_id: pages.by_base_site_page_id(p).get()):
                self.logger.info(f"🗑️ Page {record.record_name} is gone at the source; removing it")
                await self.data_entities_processor.on_record_deleted(record_id=record.id)

    @staticmethod
    def _is_system_page(page: SitePage) -> bool:
        created_by = getattr(page, 'created_by', None)
        user_identity = getattr(created_by, 'user', None) if created_by else None
        display_name = getattr(user_identity, 'display_name', '') or '' if user_identity else ''
        return display_name.lower() == 'system account'

    async def _process_site_pages(self, site_id: str, site_name: str, modified_after: Optional[str] = None, modified_before: Optional[str] = None, created_after: Optional[str] = None, created_before: Optional[str] = None) -> AsyncGenerator[Tuple[Record, List[Permission], RecordUpdate], None]:
        """The site's pages: new and changed ones (by eTag), and deletions once the listing is complete."""
        pages = await self._list_site_pages(site_id)
        if pages is None:
            self._health.skipped(f"pages of site {site_id}", "listing failed")
            return

        pages_scope = await self._get_site_pages_scope(site_id)
        listed: set[str] = set()
        pages_processed_count = 0
        for page in pages:
            page_id = getattr(page, 'id', None)
            page_name = getattr(page, 'title', getattr(page, 'name', 'unknown'))
            if not page_id:
                continue
            if not self._pass_page_date_filters(page) or self._is_system_page(page):
                continue
            listed.add(page_id)

            existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, page_id)
            is_new = existing_record is None
            is_updated = bool(existing_record) and existing_record.external_revision_id != getattr(page, 'e_tag', None)

            page_access = await self._get_page_access(site_id, page_id, pages_scope)
            if page_access is None:
                self._health.skipped(f"permissions of page {page_name}", "unreadable")
                if not existing_record or not is_updated:
                    continue
            page_record = await self._create_page_record(page, site_id, site_name, existing_record)
            if not page_record:
                self._health.skipped(f"page {page_name}", "could not be mapped to a record")
                continue
            if page_access is None:
                # An edit is still synced; the page keeps its stored access and inheritance.
                page_record.inherit_permissions = await self._inheritance_when_unreadable(
                    CollectionNames.RECORDS.value, existing_record.id
                )
                permissions = []
            else:
                page_record.inherit_permissions, permissions = page_access
            if not self.indexing_filters.is_enabled(IndexingFilterKey.PAGES, default=True):
                page_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            yield (page_record, permissions, RecordUpdate(
                record=page_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=is_updated,
                content_changed=is_updated,
                permissions_changed=page_access is not None,
                new_permissions=permissions,
                permissions_known=page_access is not None,
            ))
            self.stats['pages_processed'] += 1
            pages_processed_count += 1

        yield DRIVE_CHECKPOINT
        await self._remove_deleted_pages(site_id, listed)
        self.logger.info(f"✅ Processed {pages_processed_count} pages for site {site_name}")

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

    async def _get_sharepoint_access_token(
        self, resource_host: str = None, *, raise_errors: bool = False
    ) -> Optional[str]:
        """An app-only token for SharePoint REST on a host (default: the tenant's SharePoint domain).

        The connector's credential keeps its tokens until they are close to expiring, so this asks the
        token endpoint once per host per hour instead of once per SharePoint call. A failure returns None,
        or with ``raise_errors`` is raised as it came, so the caller can tell its cause.
        """
        parsed = urllib.parse.urlparse(resource_host or self.sharepoint_domain or "")
        host = parsed.hostname or (self.sharepoint_domain or "").replace('https://', '').replace('http://', '').strip('/')
        resource = f"https://{host}"
        if raise_errors:
            return (await self.credential.get_token(f"{resource}/.default")).token
        try:
            token = await self.credential.get_token(f"{resource}/.default")
            return token.token
        except CredentialUnavailableError as credential_error:
            self.logger.error(
                f"❌ SharePoint credential unavailable for {resource}: {sanitize_azure_error(credential_error)}"
            )
            await self._notify_token_failure(
                "SharePoint authentication failed",
                "Unable to authenticate using current credentials. Please verify the connector authentication credentials.",
            )
            return None
        except ClientAuthenticationError as auth_error:
            title, message = get_sharepoint_auth_notification(auth_error)
            self.logger.error(f"❌ Error getting SharePoint token for {resource}: {sanitize_azure_error(auth_error)}")
            await self._notify_token_failure(title, message)
            return None
        except Exception as e:
            self.logger.error(f"❌ Unexpected error getting SharePoint token for {resource}: {sanitize_azure_error(e)}")
            await self._notify_token_failure(
                "SharePoint authentication Failed",
                "Unable to acquire a SharePoint access token. Please verify SharePoint host URL "
                "and connector authentication credentials.",
            )
            return None

    async def _notify_token_failure(self, title: str, message: str) -> None:
        if self._token_failure_notified:
            return
        self._token_failure_notified = True
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=title,
            message=message,
            recipient_user_ids=[self.created_by],
        )

    async def _get_site_permissions(self, site_id: str) -> list[Permission] | None:
        """The site's (web's) role assignments as grants, or None when they can't be read.

        Every principal type counts: SharePoint groups, Entra groups, people granted on the site directly,
        and the tenant-wide claims. None keeps what is stored: an empty list would remove everyone's access.
        """
        site_metadata = self.site_cache.get(site_id)
        if not site_metadata or not site_metadata.site_url:
            self.logger.error(f"❌ Site metadata/URL not found for {site_id}")
            return None
        return await self._get_role_assignment_grants(
            f"{site_metadata.site_url.rstrip('/')}/_api/web/roleassignments?$expand=Member,RoleDefinitionBindings",
            site_id,
        )

    @staticmethod
    def _graph_member_user(item: object) -> dict | None:
        """A Graph directory object as ``{'id', 'email', 'name'}`` when it is a user with an address."""
        odata_type = getattr(item, 'odata_type', '').lower()
        if 'user' in odata_type or hasattr(item, 'user_principal_name'):
            email = getattr(item, 'mail', None) or getattr(item, 'user_principal_name', None)
            user_id = getattr(item, 'id', None)
            if email and user_id:
                return {'id': user_id, 'email': email, 'name': getattr(item, 'display_name', 'Unknown')}
        return None

    async def _fetch_graph_group_members(self, group_id: str, is_owner: bool = False) -> List[dict]:
        """The enabled user accounts of an Entra group (through nested groups), or of its owners.

        Raises when any page can't be read: a partial list saved as the group's members would take
        access away from everyone on the pages that failed. A group Graph answers 404 for has been deleted
        and has nobody in it; SharePoint keeps the claims of deleted Microsoft 365 groups in its site groups
        and role assignments.
        """
        group = self.client.groups.by_group_id(group_id)
        builder = group.owners if is_owner else group.transitive_members
        users: list[dict] = []
        try:
            async with self.rate_limiter:
                response = await builder.get()
        except Exception as e:
            if getattr(e, 'response_status_code', None) != HTTPStatus.NOT_FOUND:
                raise
            self.logger.warning(f"⚠️ Entra group {group_id} no longer exists; it grants nobody")
            return []
        while response:
            for item in response.value or []:
                user = self._graph_member_user(item)
                if user and user['email'].lower() not in self._inactive_emails:
                    users.append(user)
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await builder.with_url(next_link).get()
        return users

    async def _get_drive_permissions(self, site_id: str, drive_id: str) -> List[Permission]:
        """Get permissions for a document library."""
        try:
            permissions = []
            encoded_site_id = self._construct_site_url(site_id)

            async with self.rate_limiter:
                # Use the correct Graph API structure for drive permissions
                # For SharePoint, we need to get the root item first, then its permissions
                root_item = await self._safe_api_call(
                    lambda: self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).root.get()
                )
                if root_item:
                    perms_response = await self._safe_api_call(
                        lambda: self.client.sites.by_site_id(encoded_site_id).drives.by_drive_id(drive_id).items.by_drive_item_id(root_item.id).permissions.get()
                    )
                else:
                    perms_response = None

            if perms_response and perms_response.value:
                permissions = await self._convert_to_permissions(perms_response.value, site_id=site_id)

            return permissions

        except Exception as e:
            self.logger.debug(f"❌ Could not get drive permissions: {e}")
            return []

    async def _get_item_permissions(self, site_id: str, drive_id: str, item_id: str) -> list[Permission] | None:
        """A drive item's Graph permissions, every page of them, or None when they can't be read."""
        try:
            graph_permissions = await self.msgraph_client.get_file_permission(drive_id, item_id, raise_on_error=True)
            return await self._convert_to_permissions(graph_permissions, site_id=site_id)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read the permissions of item {item_id}: {e}")
            return None

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

    async def _convert_to_permissions(self, msgraph_permissions: List, site_id: str | None = None) -> list[Permission]:
        """
        Convert Microsoft Graph permissions to our Permission model.
        Handles user, group and SharePoint site group permissions; site groups
        need the ``site_id`` they were synced under.
        """
        permissions = []


        for perm in msgraph_permissions:
            try:
                # Handle user permissions
                if hasattr(perm, 'granted_to_v2') and perm.granted_to_v2:
                    granted_to = perm.granted_to_v2
                    role = map_msgraph_roles_to_permission_type(perm.roles)
                    if hasattr(granted_to, 'user') and granted_to.user and not self._is_inactive_identity(granted_to.user):
                        user = granted_to.user
                        permissions.append(Permission(
                            external_id=user.id,
                            email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                            type=role,
                            entity_type=EntityType.USER
                        ))
                    if hasattr(granted_to, 'group') and granted_to.group:
                        permissions.extend(
                            await self._graph_group_grant(granted_to.group, getattr(granted_to, 'site_user', None), role)
                        )
                    site_group = getattr(granted_to, 'site_group', None)
                    site_group_id = getattr(site_group, 'id', None) if site_group else None
                    if site_id and isinstance(site_group_id, str):
                        permissions.extend(self._site_group_permissions(site_id, site_group_id, role))


                # Handle group permissions
                if hasattr(perm, 'granted_to_identities_v2') and perm.granted_to_identities_v2:
                    for identity in perm.granted_to_identities_v2:
                        if hasattr(identity, 'group') and identity.group:
                            link_groups = await self._graph_group_grant(
                                identity.group,
                                getattr(identity, 'site_user', None),
                                map_msgraph_roles_to_permission_type(perm.roles),
                            )
                            for grant in link_groups:
                                grant.link_grant = True
                            permissions.extend(link_groups)
                        elif hasattr(identity, 'user') and identity.user and not self._is_inactive_identity(identity.user):
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.USER,
                                link_grant=True,
                            ))

                # A link grants only the identities Graph lists on it. An organization or
                # anonymous link opens the item only to the people who redeem it. Graph does
                # not list them; on a uniquely permissioned item they arrive through the
                # link's SharePoint group in its role assignments.

            except (AttributeError, TypeError, ValueError) as e:
                # A permission in a shape this code does not know grants nobody here.
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
                continue

        return permissions

    def _is_inactive_identity(self, user: object) -> bool:
        """A Graph identity of an account the tenant has disabled or deleted (it can no longer read anything)."""
        email = (getattr(user, 'additional_data', None) or {}).get("email")
        return isinstance(email, str) and email.lower() in self._inactive_emails

    async def _graph_group_grant(self, group: object, site_user: object, role: PermissionType) -> list[Permission]:
        """A Graph group grant. An M365 group's owners claim names the whole group, so it is narrowed to the owners."""
        if await self._is_m365_owners_grant(group, site_user):
            return await self._m365_owner_permissions(group.id, role)
        return [Permission(
            external_id=group.id,
            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
            type=role,
            entity_type=EntityType.GROUP
        )]

    async def _is_m365_owners_grant(self, group: object, site_user: object) -> bool:
        # The site user's claim says owners or members outright. Without one, an " Owners"
        # name means the owners grant only on an M365 group: a security group can be named that.
        login_name = getattr(site_user, 'login_name', None) if site_user else None
        if isinstance(login_name, str) and 'federateddirectoryclaimprovider' in login_name.lower():
            return is_m365_owners_claim(login_name)
        display_name = getattr(group, 'display_name', None)
        if not (isinstance(display_name, str) and display_name.endswith(M365_OWNERS_DISPLAY_SUFFIX)):
            return False
        return await self._is_unified_group(group.id) is not False

    async def _is_unified_group(self, group_id: str) -> bool | None:
        """Whether a Graph group is an M365 (Unified) group, or None when it can't be read."""
        if group_id in self._unified_groups:
            return self._unified_groups[group_id]
        is_unified = None
        try:
            query = GroupItemRequestBuilder.GroupItemRequestBuilderGetQueryParameters(select=["groupTypes"])
            async with self.rate_limiter:
                graph_group = await self.client.groups.by_group_id(group_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            is_unified = "Unified" in (getattr(graph_group, 'group_types', None) or [])
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read the type of group {group_id}; its Owners grant is narrowed to the group owners: {e}"
            )
        self._unified_groups[group_id] = is_unified
        return is_unified

    @staticmethod
    def _everyone_permission(group_id: str, role: PermissionType) -> Permission:
        """A grant to a tenant-wide claim, held by its group (everyone_groups.py), not by the organization."""
        return Permission(external_id=group_id, email=None, type=role, entity_type=EntityType.GROUP)

    def _site_group_permissions(self, site_id: str, group_id: object, role: PermissionType) -> list[Permission]:
        """A SharePoint site group grant, keyed as _sync_user_groups saves the group.

        Raises for a group whose members this run did not read (its site's groups could not be listed, or a
        reindex ran without listing them): whether it holds a tenant-wide claim is then unknown, so what it
        grants is unknown too, and the caller keeps what is stored.
        """
        if group_id is None or not site_id:
            return []
        if site_id not in self._site_groups_read:
            raise RuntimeError(f"the SharePoint groups of site {site_id} were not read in this run")
        if str(group_id) in self._unreadable_site_groups.get(site_id, set()):
            raise RuntimeError(f"the members of SharePoint group {group_id} on site {site_id} could not be read")
        permissions = [Permission(
            external_id=f"{site_id}-{group_id}",
            email=None,
            type=role,
            entity_type=EntityType.GROUP
        )]
        # A site group can't hold another group as a member in PipesHub, so a claim inside
        # it is granted alongside it.
        for claim_group in self._org_wide_site_groups.get(site_id, {}).get(str(group_id), ()):
            permissions.append(self._everyone_permission(claim_group, role))
        return permissions

    async def _m365_owner_permissions(self, group_id: str, role: PermissionType) -> list[Permission]:
        if group_id not in self._m365_group_owners:
            self._m365_group_owners[group_id] = await self._fetch_graph_group_members(group_id, is_owner=True)
        return [
            Permission(external_id=owner['id'], email=owner['email'], type=role, entity_type=EntityType.USER)
            for owner in self._m365_group_owners[group_id]
        ]

    async def _sharepoint_principal_permissions(self, principal: dict, role: PermissionType, site_id: str) -> list[Permission]:
        """Grants for one SharePoint principal: a role assignment's Member, or a user of an associated site group."""
        login_name = principal.get('LoginName') or ''
        principal_type = principal.get('PrincipalType')

        if 'federateddirectoryclaimprovider' in login_name:
            match = GUID_PATTERN.search(login_name)
            if not match:
                self.logger.warning(f"⚠️ Could not extract a group id from M365 claim {login_name}")
                return []
            group_id = match.group(1)
            if is_m365_owners_claim(login_name):
                return await self._m365_owner_permissions(group_id, role)
            self.logger.info(f"      🔵 Found M365 Group: {principal.get('Title', 'M365 Group')} (ID: {group_id})")
            return [Permission(external_id=group_id, email=None, type=role, entity_type=EntityType.GROUP)]

        claim_group = everyone_group_for_claim(login_name)
        if claim_group:
            return [self._everyone_permission(claim_group, role)]

        if principal_type == SP_PRINCIPAL_SECURITY_GROUP:
            self.logger.info(f"🔒 Found Security Group: {principal.get('Title', 'Security Group')} ({login_name})")
            return await self._tenant_claim_permissions(login_name, role)

        if principal_type == SP_PRINCIPAL_SHAREPOINT_GROUP:
            return self._site_group_permissions(site_id, principal.get('Id'), role)

        if principal_type == SP_PRINCIPAL_USER:
            email = principal.get('Email') or principal.get('UserPrincipalName')
            if email and email.lower() not in self._inactive_emails:
                return [Permission(external_id=str(principal.get('Id')), email=email, type=role, entity_type=EntityType.USER)]
        return []

    async def _tenant_claim_permissions(self, login_name: str, role: PermissionType) -> list[Permission]:
        """A ``c:0t.c|tenant|<guid>`` claim: a security group, or a directory role expanded to its members."""
        match = TENANT_CLAIM_PATTERN.search(login_name)
        if not match:
            self.logger.warning(f"   -> ⚠️ Could not extract GUID from Security Group LoginName: {login_name}")
            return []
        object_id = match.group(1)
        if await self._get_directory_object_type(object_id) == DIRECTORY_ROLE_ODATA_TYPE:
            return [
                Permission(external_id=member['id'], email=member['email'], type=role, entity_type=EntityType.USER)
                for member in await self._get_directory_role_member_users(object_id)
            ]
        return [Permission(external_id=object_id, email=None, type=role, entity_type=EntityType.GROUP)]

    async def _get_directory_object_type(self, object_id: str) -> str | None:
        """The ``@odata.type`` of a directory object, or None when it can't be read."""
        if object_id in self._directory_object_types:
            return self._directory_object_types[object_id]
        object_type = None
        try:
            async with self.rate_limiter:
                directory_object = await self.client.directory_objects.by_directory_object_id(object_id).get()
            odata_type = getattr(directory_object, 'odata_type', None)
            object_type = odata_type if isinstance(odata_type, str) else None
        except Exception as e:
            self.logger.warning(f"⚠️ Could not resolve directory object {object_id}; treating it as a group: {e}")
        self._directory_object_types[object_id] = object_type
        return object_type

    async def _get_directory_role_member_users(self, role_id: str) -> list[dict]:
        """Users holding a directory role, directly or through a role-assignable group. Raises when the
        members can't all be read; a partial answer is not kept for the rest of the run."""
        if role_id in self._directory_role_members:
            return self._directory_role_members[role_id]
        users: list[dict] = []
        members = self.client.directory_roles.by_directory_role_id(role_id).members
        async with self.rate_limiter:
            response = await members.get()
        while response:
            for member in response.value or []:
                user = self._graph_member_user(member)
                if user:
                    if user['email'].lower() not in self._inactive_emails:
                        users.append(user)
                elif 'group' in (getattr(member, 'odata_type', None) or '').lower() and getattr(member, 'id', None):
                    users.extend(await self._fetch_graph_group_members(member.id))
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await members.with_url(next_link).get()
        self._directory_role_members[role_id] = users
        return users

    async def _sharepoint_rest_pages(self, url: str, *, follow_next: bool = False) -> AsyncGenerator[dict, None]:
        """The ``d`` body of a SharePoint REST GET; with ``follow_next``, of every page of the collection.

        Raises on any answer but 200, so a failed read can't pass for "nothing is unique".
        Callers must exhaust the generator so its HTTP client is closed.
        """
        host = urlparse(url).netloc
        next_url: str | None = url
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            while next_url:
                if urlparse(next_url).netloc != host:
                    # The bearer token is for this host only.
                    raise RuntimeError(f"SharePoint REST paged to another host: {urlparse(next_url).netloc}")
                payload = (await self._sharepoint_rest_get(http_client, next_url, host)).json()
                body = payload.get('d', {})
                yield body
                next_url = (body.get('__next') or payload.get('odata.nextLink')) if follow_next else None

    async def _sharepoint_rest_get(self, http_client: httpx.AsyncClient, url: str, host: str) -> httpx.Response:
        """A SharePoint REST GET with a current token, tried again after throttling, a dropped connection,
        or a 401 (a token that expired while a long listing ran)."""
        attempt = 1
        while True:
            access_token = await self._get_sharepoint_access_token(resource_host=f"https://{host}")
            if not access_token:
                raise RuntimeError(f"no SharePoint access token for {host}")
            headers = {**SHAREPOINT_REST_HEADERS, 'Authorization': f'Bearer {access_token}'}
            try:
                async with self.rate_limiter:
                    response = await http_client.get(url, headers=headers)
            except httpx.TransportError as e:
                if attempt >= SHAREPOINT_REST_MAX_ATTEMPTS:
                    raise RuntimeError(f"SharePoint REST unreachable for {url}: {e}") from e
                await asyncio.sleep(2 ** attempt)
                attempt += 1
                continue
            if response.status_code == HTTPStatus.OK:
                return response
            retryable = response.status_code in SHAREPOINT_REST_RETRY_STATUSES or (
                response.status_code == HTTPStatus.UNAUTHORIZED and attempt == 1
            )
            if not retryable or attempt >= SHAREPOINT_REST_MAX_ATTEMPTS:
                raise RuntimeError(f"SharePoint REST answered {response.status_code} for {url}")
            retry_after = response.headers.get('Retry-After', '')
            delay = int(retry_after) if retry_after.isdigit() else 2 ** attempt
            if response.status_code != HTTPStatus.UNAUTHORIZED:
                await asyncio.sleep(min(delay, SHAREPOINT_REST_MAX_RETRY_AFTER_S))
            attempt += 1

    async def _read_list_permission_scope(self, web_url: str, list_url: str) -> ListPermissionScope | None:
        """A list's unique-permission flag and its uniquely permissioned items, or None when they can't be read."""
        try:
            list_body: dict = {}
            async for body in self._sharepoint_rest_pages(f"{list_url}?$select=Id,HasUniqueRoleAssignments"):
                list_body = body

            unique_items: dict[str, int] = {}
            items_url = f"{list_url}/items?$select=Id,UniqueId,HasUniqueRoleAssignments&$top={SHAREPOINT_ITEMS_PAGE_SIZE}"
            async for body in self._sharepoint_rest_pages(items_url, follow_next=True):
                for item in body.get('results', []):
                    unique_id = item.get('UniqueId')
                    if item.get('HasUniqueRoleAssignments') is True and isinstance(unique_id, str):
                        unique_items[unique_id.lower()] = item.get('Id')

            return ListPermissionScope(
                web_url=web_url,
                list_url=list_url,
                has_unique_role_assignments=list_body.get('HasUniqueRoleAssignments') is True,
                unique_items=unique_items,
            )
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read unique permissions of {list_url}; new content is saved without inheritance "
                f"and stored content keeps its access for this run: {e}"
            )
            return None

    async def _get_drive_sharepoint_ids(self, drive_id: str) -> object:
        try:
            query = DriveItemRequestBuilder.DriveItemRequestBuilderGetQueryParameters(select=["id", "sharePointIds"])
            async with self.rate_limiter:
                drive = await self.client.drives.by_drive_id(drive_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            return getattr(drive, 'share_point_ids', None)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read the SharePoint ids of drive {drive_id}: {e}")
            return None

    async def _get_library_scope(self, drive_id: str, drive: object = None) -> ListPermissionScope | None:
        """The unique permissions of a document library, read once per sync run."""
        if drive_id in self._library_scopes:
            return self._library_scopes[drive_id]

        sharepoint_ids = getattr(drive, 'share_point_ids', None) if drive is not None else None
        if not isinstance(getattr(sharepoint_ids, 'list_id', None), str):
            sharepoint_ids = await self._get_drive_sharepoint_ids(drive_id)
        list_id = getattr(sharepoint_ids, 'list_id', None)
        web_url = getattr(sharepoint_ids, 'site_url', None)

        scope = None
        if isinstance(list_id, str) and list_id and isinstance(web_url, str) and web_url:
            web_url = web_url.rstrip('/')
            scope = await self._read_list_permission_scope(web_url, f"{web_url}/_api/web/lists(guid'{list_id}')")
        else:
            self.logger.warning(
                f"⚠️ No SharePoint list id for drive {drive_id}; new items are saved without inheritance "
                f"and stored items keep their access for this run"
            )
        self._library_scopes[drive_id] = scope
        return scope

    async def _get_site_pages_scope(self, site_id: str, web_url: str | None = None) -> ListPermissionScope | None:
        """The unique permissions of a site's Site Pages library, read once per sync run."""
        if site_id in self._site_pages_scopes:
            return self._site_pages_scopes[site_id]

        if not isinstance(web_url, str) or not web_url:
            site_metadata = self.site_cache.get(site_id)
            web_url = site_metadata.site_url if site_metadata else None

        scope = None
        if isinstance(web_url, str) and web_url:
            web_url = web_url.rstrip('/')
            scope = await self._read_list_permission_scope(web_url, f"{web_url}/_api/web/lists/getbytitle('Site%20Pages')")
        else:
            self.logger.warning(
                f"⚠️ No web URL for site {site_id}; new pages are saved without inheritance "
                f"and stored pages keep their access for this run"
            )
        self._site_pages_scopes[site_id] = scope
        return scope

    async def _get_role_assignment_grants(self, role_assignments_url: str, site_id: str) -> list[Permission] | None:
        """Grants from a SharePoint role assignments collection, or None when it, or the members of a
        group it names, can't be read."""
        try:
            assignments: list[dict] = []
            async for body in self._sharepoint_rest_pages(role_assignments_url, follow_next=True):
                assignments.extend(body.get('results', []))
            return await self._role_assignments_to_permissions(assignments, site_id)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read role assignments {role_assignments_url}: {e}")
            return None

    async def _role_assignments_to_permissions(self, assignments: list[dict], site_id: str) -> list[Permission]:
        permissions: dict[tuple[str, str], Permission] = {}
        for assignment in assignments:
            bindings = (assignment.get('RoleDefinitionBindings') or {}).get('results', [])
            role = role_bindings_to_permission_type(bindings)
            if role is None:
                continue
            for permission in await self._sharepoint_principal_permissions(assignment.get('Member') or {}, role, site_id):
                merge_permission(permissions, permission)
        return list(permissions.values())

    async def _inheritance_when_unreadable(self, collection: str, stored_id: str | None) -> bool:
        """``inherit_permissions`` when the scope can't be read: a stored node keeps its inheritance edge, a new one gets none."""
        return await self.data_entities_processor.inheritance_when_unreadable(collection, stored_id)

    async def _get_library_access(self, site_id: str, drive: object) -> tuple[bool, list[Permission] | None]:
        """``(inherit_permissions, grants)`` for a library; the grants are None when they can't be read."""
        drive_id = getattr(drive, 'id', None)
        scope = await self._get_library_scope(drive_id, drive)
        if scope is None:
            stored = await self.data_entities_processor.get_record_group_by_external_id(self.connector_id, drive_id)
            inherits = await self._inheritance_when_unreadable(
                CollectionNames.RECORD_GROUPS.value, stored.id if stored else None
            )
            return inherits, None
        if not scope.has_unique_role_assignments:
            return True, []
        return False, await self._get_role_assignment_grants(
            f"{scope.list_url}/roleassignments?$expand=Member,RoleDefinitionBindings", site_id
        )

    async def _drive_item_has_unique_permissions(self, item: DriveItem, drive_id: str) -> bool | None:
        """None when the library's unique permissions can't be read."""
        scope = await self._get_library_scope(drive_id)
        if scope is None:
            return None
        unique_id = drive_item_unique_id(item)
        return unique_id is not None and unique_id in scope.unique_items

    async def _unique_drive_item_role_grants(self, item: DriveItem, drive_id: str, site_id: str) -> list[Permission] | None:
        """Role assignments of a drive item that broke inheritance, or None when they can't be read."""
        scope = await self._get_library_scope(drive_id)
        unique_id = drive_item_unique_id(item)
        list_item_id = scope.unique_items.get(unique_id) if scope is not None and unique_id else None
        if scope is None or list_item_id is None:
            return None
        return await self._get_role_assignment_grants(
            f"{scope.list_url}/items({list_item_id})/roleassignments?$expand=Member,RoleDefinitionBindings",
            site_id,
        )

    async def _get_page_access(
        self, site_id: str, page_id: str, pages_scope: ListPermissionScope | None
    ) -> tuple[bool, list[Permission]] | None:
        """``(inherit_permissions, grants)`` for a page; None when its permissions can't be told."""
        if pages_scope is None or not isinstance(page_id, str):
            return None
        item_id = pages_scope.unique_items.get(page_id.lower())
        if item_id is None:
            return True, []
        grants = await self._get_role_assignment_grants(
            f"{pages_scope.list_url}/items({item_id})/roleassignments?$expand=Member,RoleDefinitionBindings", site_id
        )
        if grants is None:
            return None
        return False, grants

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
    async def _sync_user_groups(self, sites: list[Site] | None = None) -> None:
        """Sync Entra groups and every site's SharePoint groups, with their members.

        A group whose members can't all be read keeps the members stored for it: writing what was read
        would take access away from everyone who was not. Such a group is reported to the run's health.
        """
        try:
            await self._sync_azure_ad_groups()
        except Exception as groups_error:
            self.logger.error(f"❌ Error syncing Azure AD Groups with delta: {groups_error}")
            self._health.skipped("Entra groups", groups_error)

        self.logger.info("Starting SharePoint Site Groups fetch...")
        self._org_wide_site_groups = {}
        self._unreadable_site_groups = {}
        self._site_groups_read = set()
        if sites is None:
            sites = await self._get_all_sites()
        sharepoint_groups_with_members: list[tuple[AppUserGroup, list[AppUser]]] = []
        for site in sites:
            try:
                sharepoint_groups_with_members.extend(await self._site_groups_with_members(site))
            except Exception as site_error:
                self._health.skipped(f"SharePoint groups of site {site.display_name or site.name}",
                                     sanitize_azure_error(site_error))
        if sharepoint_groups_with_members:
            await self.data_entities_processor.on_new_user_groups(sharepoint_groups_with_members)
        self.logger.info(f"Completed SharePoint group synchronization - processed {len(sharepoint_groups_with_members)} groups")

    async def _site_groups_with_members(self, site: Site) -> list[tuple[AppUserGroup, list[AppUser]]]:
        """A site's SharePoint groups with their members. A group whose members can't be read is left out
        (its stored members stay); a group the site no longer has is written with no members."""
        site_id = site.id
        site_web_url = (site.web_url or '').rstrip('/')
        if not site_web_url:
            return []
        groups: list[dict] = []
        async for body in self._sharepoint_rest_pages(f"{site_web_url}/_api/web/sitegroups", follow_next=True):
            groups.extend(body.get('results', []))

        tracker = ContainerTracker(self.drive_delta_sync_point, f"sharepoint-known-site-groups/{site_id}")
        known = await tracker.known()
        listed = {str(group.get('Id')) for group in groups}
        out: list[tuple[AppUserGroup, list[AppUser]]] = []
        for group in groups:
            group_id = group.get('Id')
            title = group.get('Title', 'N/A')
            description = group.get('Description')
            user_group = AppUserGroup(
                id=str(uuid.uuid4()),
                # Site id and group id: SharePoint group ids are only unique within a site collection.
                source_user_group_id=f"{site_id}-{group_id}",
                app_name=self.connector_name,
                connector_id=self.connector_id,
                name=title,
                description=description if description not in (None, 'N/A') else None,
            )
            try:
                principals: list[dict] = []
                async for body in self._sharepoint_rest_pages(
                    f"{site_web_url}/_api/web/sitegroups/GetById({group_id})/users", follow_next=True
                ):
                    principals.extend(body.get('results', []))
                members = await self._site_group_member_users(site_id, str(group_id), principals)
            except Exception as member_error:
                self._health.skipped(f"members of SharePoint group {title} on site {site_id}",
                                     sanitize_azure_error(member_error))
                self._unreadable_site_groups.setdefault(site_id, set()).add(str(group_id))
                continue
            out.append((user_group, members))
        for gone in known - listed:
            # The site no longer has this group: nobody is in it any more.
            out.append((AppUserGroup(source_user_group_id=f"{site_id}-{gone}", app_name=self.connector_name,
                                     connector_id=self.connector_id, name=f"Deleted group {gone}"), []))
        await tracker.remember(listed)
        self._site_groups_read.add(site_id)
        return out

    async def _site_group_member_users(self, site_id: str, group_id: str, principals: list[dict]) -> list[AppUser]:
        """The enabled people a SharePoint group's members stand for. Raises when a member group can't be read.
        A tenant-wide claim is noted for the group (see _site_group_permissions), not expanded."""
        users: list[dict] = []
        for principal in principals:
            login_name = principal.get('LoginName', '') or ''
            claim_group = everyone_group_for_claim(login_name)
            if claim_group:
                self._org_wide_site_groups.setdefault(site_id, {}).setdefault(group_id, set()).add(claim_group)
            elif 'federateddirectoryclaimprovider' in login_name.lower():
                match = GUID_PATTERN.search(login_name)
                if match:
                    # The claim says which half of an M365 group it is; the SharePoint group's
                    # title is free text, and in another language never says "Owner".
                    users.extend(await self._fetch_graph_group_members(
                        match.group(1), is_owner=is_m365_owners_claim(login_name)
                    ))
            elif principal.get('PrincipalType') == SP_PRINCIPAL_SECURITY_GROUP:
                match = TENANT_CLAIM_PATTERN.search(login_name)
                if match:
                    claim_id = match.group(1)
                    if await self._get_directory_object_type(claim_id) == DIRECTORY_ROLE_ODATA_TYPE:
                        users.extend(await self._get_directory_role_member_users(claim_id))
                    else:
                        users.extend(await self._fetch_graph_group_members(claim_id))
            else:
                email = principal.get('Email') or principal.get('UserPrincipalName')
                if email and email.lower() not in self._inactive_emails:
                    users.append({'id': str(principal.get('Id')), 'email': email, 'name': principal.get('Title')})
        return [
            AppUser(source_user_id=user['id'], email=user['email'], full_name=user.get('name'),
                    app_name=self.connector_name, connector_id=self.connector_id)
            for user in users
        ]

    async def _sync_azure_ad_groups(self) -> None:
        """Entra groups: all of them on the first run (or when every membership must be read again), then
        the groups Graph's delta reports, with the groups that contain them."""
        sync_point_key = generate_record_sync_point_key(
            SyncDataPointType.GROUPS.value,
            "organization",
            self.data_entities_processor.org_id
        )
        sync_point = await self.user_group_sync_point.read_sync_point(sync_point_key)
        # A run stopped mid-delta leaves only nextLink. Resuming there reads the page
        # it stopped on again; a full sync would start a new delta link past it, and
        # past the group deletions on the remaining pages.
        delta_link = (sync_point.get('deltaLink') or sync_point.get('nextLink')) if sync_point else None

        if delta_link is None or self._refresh_all_groups:
            caught_up = True
            if delta_link is not None:
                # Only the stored link reports the groups deleted since it was taken: the full read below lists
                # the groups that exist, and the new link starts after the deletions.
                try:
                    caught_up = await self._perform_delta_sync(delta_link, sync_point_key)
                except Exception as e:
                    caught_up = False
                    self._health.skipped("Entra group changes", e)
            self.logger.info("Reading every Entra group's members...")
            # The delta link is taken before the groups are read, so no change made meanwhile is missed.
            new_delta_link = await self._get_initial_delta_link()
            complete = await self._perform_initial_full_sync()
            if new_delta_link and complete and caught_up:
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": new_delta_link}
                )
                await self.user_sync_point.update_sync_point(
                    INACTIVE_USERS_SYNC_POINT_KEY, sync_point_data={"emails": sorted(self._inactive_emails)}
                )
        else:
            await self._perform_delta_sync(delta_link, sync_point_key)

    async def _get_initial_delta_link(self) -> Optional[str]:
        """
        Consumes the delta API to obtain a deltaLink checkpoint.
        Called BEFORE initial full sync to ensure no changes are missed.

        Returns:
            The deltaLink string, or None if unable to obtain one.
        """
        url = "https://graph.microsoft.com/v1.0/groups/delta"
        try:
            while True:
                result = await self.msgraph_client.get_groups_delta_response(url)
                if result.get('next_link'):
                    url = result.get('next_link')
                elif result.get('delta_link'):
                    return result.get('delta_link')
                else:
                    self.logger.warning("Delta initialization: no next_link or delta_link received")
                    return None
        except Exception as e:
            self.logger.error(f"❌ Error obtaining initial delta link: {e}", exc_info=True)
            self._health.skipped("Entra group changes", e)
            return None

    def _app_user_group(self, group: object) -> AppUserGroup:
        created = getattr(group, 'created_date_time', None)
        return AppUserGroup(
            source_user_group_id=group.id,
            app_name=self.connector_name,
            connector_id=self.connector_id,
            name=getattr(group, 'display_name', None) or group.id,
            description=getattr(group, 'description', None),
            source_created_at=created.timestamp() if created else get_epoch_timestamp_in_ms(),
        )

    def _member_app_users(self, users: list[dict]) -> list[AppUser]:
        return [
            AppUser(source_user_id=user['id'], email=user['email'], full_name=user.get('name'),
                    app_name=self.connector_name, connector_id=self.connector_id)
            for user in users
        ]

    async def _group_with_members(self, group: object) -> tuple[AppUserGroup, list[AppUser]]:
        """An Entra group and every enabled person in it, through nested groups. Raises when they can't be read."""
        return self._app_user_group(group), self._member_app_users(await self._fetch_graph_group_members(group.id))

    async def _perform_initial_full_sync(self) -> bool:
        """Every Entra group with its members. True when every group's members were read."""
        groups = await self.msgraph_client.get_all_user_groups()
        results = await asyncio.gather(*[self._group_with_members(group) for group in groups], return_exceptions=True)
        group_with_members = []
        complete = True
        for group, result in zip(groups, results):
            if isinstance(result, Exception):
                complete = False
                self._health.skipped(f"members of Entra group {getattr(group, 'display_name', group.id)}", result)
            else:
                group_with_members.append(result)
        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)
        self.logger.info(f"Initial full sync completed: processed {len(groups)} Azure AD groups")
        return complete

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """An Entra group with its members, or None when they can't be read (its stored members stay)."""
        try:
            return await self._group_with_members(group)
        except Exception as e:
            self.logger.error(f"❌ Error processing group {getattr(group, 'display_name', group.id)}: {e}")
            return None

    async def _containing_groups(self, group_id: str) -> list[object]:
        """The groups a group is in, at any depth: their flattened members change when its members do."""
        builder = self.client.groups.by_group_id(group_id).transitive_member_of
        groups = []
        async with self.rate_limiter:
            response = await builder.get()
        while response:
            groups.extend(item for item in response.value or []
                          if 'group' in (getattr(item, 'odata_type', None) or '').lower())
            next_link = getattr(response, 'odata_next_link', None)
            if not next_link:
                break
            async with self.rate_limiter:
                response = await builder.with_url(next_link).get()
        return groups

    async def _perform_delta_sync(self, url: str, sync_point_key: str) -> bool:
        """Apply the Entra group changes since the stored delta link. True when every change was applied.

        A changed group has its members read again, and so does every group that contains it: Graph reports
        a member change only on the group the member was added to or removed from, while PipesHub holds
        each group's members flattened. The link moves on only when every change on the page was applied.
        """
        while True:
            result = await self.msgraph_client.get_groups_delta_response(url)
            groups = result.get('groups', [])
            changed: dict[str, object] = {}
            applied = True
            for group in groups:
                if getattr(group, 'additional_data', None) and '@removed' in group.additional_data:
                    if not await self._handle_delete_group(group.id):
                        applied = False
                    continue
                changed[group.id] = group
            for group_id in list(changed):
                try:
                    for parent in await self._containing_groups(group_id):
                        changed.setdefault(parent.id, parent)
                except Exception as e:
                    applied = False
                    self._health.skipped(f"groups containing Entra group {group_id}", e)
            refreshed = []
            for group in changed.values():
                try:
                    refreshed.append(await self._group_with_members(group))
                except Exception as e:
                    applied = False
                    self._health.skipped(f"members of Entra group {getattr(group, 'display_name', group.id)}", e)
            if refreshed:
                await self.data_entities_processor.on_new_user_groups(refreshed)
            if not applied:
                return False
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(sync_point_key, {"nextLink": url, "deltaLink": None})
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key, {"nextLink": None, "deltaLink": result.get('delta_link')}
                )
                return True
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                return False

    async def _handle_group_create(self, group: Group) -> bool:
        """Write an Entra group with its members. False when they could not be read (stored members stay)."""
        try:
            await self.data_entities_processor.on_new_user_groups([await self._group_with_members(group)])
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False

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

    async def _sync_users(self) -> None:
        """Sync the tenant's users, note who is disabled, and rewrite the tenant-wide claim groups.

        When the set of disabled accounts changes, every Entra group's members are read again, so an
        account that can no longer sign in drops out of the groups that gave it access.
        """
        try:
            users = await self.msgraph_client.get_all_users()
        except Exception as user_error:
            self.logger.error(f"❌ Error syncing users: {user_error}")
            await self.notify(
                type=NotificationType.CONNECTOR_USER_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title="Unable to sync SharePoint users",
                message=(
                    "Cannot sync SharePoint users. Please check your authentication credentials and try again."
                ),
                recipient_user_ids=[self.created_by],
            )
            raise
        await self.data_entities_processor.on_new_app_users(users)
        self._inactive_emails = {user.email.lower() for user in users if user.email and user.is_active is False}
        stored = await self.user_sync_point.read_sync_point(INACTIVE_USERS_SYNC_POINT_KEY)
        # Saved once every group has been read with the new set (_sync_azure_ad_groups).
        self._refresh_all_groups = set((stored or {}).get("emails") or []) != self._inactive_emails
        await self.data_entities_processor.on_new_user_groups(
            everyone_groups(self.connector_name, self.connector_id, users)
        )
        self.logger.info(f"✅ Successfully synced {len(users)} users")

    # Record update handling
    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """Apply a change to a stored record. Raises on failure, so the delta token does not move past it."""
        if record_update.is_deleted:
            # Graph reports the item's own id; records are deleted by their key. A folder takes what
            # is under it: Graph may report the folder alone when it is deleted.
            db_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, record_update.external_record_id
            )
            if db_record:
                result = await self.data_entities_processor.on_records_deleted_cascade(
                    [db_record.id], self.connector_id, cascade_children=True, include_trashed_roots=True
                )
                if not (result or {}).get("success", False):
                    raise RuntimeError(f"deleting record {db_record.id} failed: {(result or {}).get('reason')}")
        elif record_update.is_updated:
            if record_update.metadata_changed:
                self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                await self.data_entities_processor.on_record_metadata_update(record_update.record)
            if record_update.permissions_changed:
                await self.data_entities_processor.on_updated_record_permissions(
                    record_update.record,
                    record_update.new_permissions
                )
            if record_update.content_changed:
                self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                await self.data_entities_processor.on_record_content_update(record_update.record)

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
        """One sync run: users, groups, sites, then each site's libraries and pages.

        The run goes on past a part of the source it cannot read, then fails if there was one: a sync
        that did not read everything must not report success, and a full sync that did not must not
        remove what it did not see (full_sync_coverage).
        """
        start_time = datetime.now()
        self.stats = {
            'sites_processed': 0,
            'sites_failed': 0,
            'drives_processed': 0,
            'lists_processed': 0,
            'pages_processed': 0,
            'items_processed': 0,
            'errors_encountered': 0
        }
        self._health = SyncHealth()
        self._drive_roots = {}
        self._filtered_out_site_ids = set()
        self._token_failure_notified = False
        self.last_sync_summary = None

        try:
            self.logger.info("🚀 Starting SharePoint connector sync")
            await self._reinitialize_credential_if_needed()
            self._reset_permission_caches()
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "sharepointonline", self.connector_id, self.logger
            )
            model = await self.drive_delta_sync_point.read_sync_point(PERMISSION_MODEL_SYNC_POINT_KEY)
            self._rewrite_all_items = int((model or {}).get("version") or 0) < PERMISSION_MODEL_VERSION

            try:
                await self._report_missing_graph_permission()
            except Exception as e:
                # A warning about the app's permissions must not stop the sync it is about.
                self.logger.warning(f"⚠️ Could not check the app's Microsoft Graph permissions: {e}")
            await self._sync_users()

            sites = await self._get_all_sites()
            await self._sync_user_groups(sites)
            await self._remove_deleted_sites()
            await self._remove_filtered_out(FILTERED_SITES_SYNC_POINT_KEY, self._filtered_out_site_ids, self._forget_site)

            site_record_groups_with_permissions = []
            for site in sites:
                if not site.name and not site.display_name:
                    self.logger.warning(f"Site name is empty: '{site.id}'")
                    continue
                source_created_at = int(site.created_date_time.timestamp() * 1000) if site.created_date_time else None
                source_updated_at = int(site.last_modified_date_time.timestamp() * 1000) if site.last_modified_date_time else source_created_at
                site_record_group = RecordGroup(
                    name=site.display_name or site.name,
                    short_name=site.name,
                    description=getattr(site, 'description', None),
                    external_group_id=site.id,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    web_url=site.web_url,
                    group_type=RecordGroupType.SHAREPOINT_SITE,
                    source_created_at=source_created_at,
                    source_updated_at=source_updated_at
                )
                site_permissions = await self._get_site_permissions(site.id)
                if site_permissions is None:
                    self._health.skipped(f"permissions of site {site.display_name or site.name}", "unreadable")
                site_record_groups_with_permissions.append((site_record_group, site_permissions))

            if site_record_groups_with_permissions:
                await self.data_entities_processor.on_new_record_groups(site_record_groups_with_permissions)

            for site_record_group, _permissions in site_record_groups_with_permissions:
                try:
                    await self._sync_site_content(site_record_group)
                except Exception as site_error:
                    self._health.skipped(f"site {site_record_group.name}", site_error)

            if self._health.complete and self._rewrite_all_items:
                await self.drive_delta_sync_point.update_sync_point(
                    PERMISSION_MODEL_SYNC_POINT_KEY, sync_point_data={"version": PERMISSION_MODEL_VERSION}
                )

            duration = datetime.now() - start_time
            self.last_sync_summary = {
                **self.stats,
                "skipped_parts": len(self._health.problems),
                "skipped": self._health.summary() if not self._health.complete else "",
            }
            self.logger.info(f"🎉 SharePoint connector sync completed in {duration}")
            self.logger.info(f"📈 Statistics: {self.stats}")
            if not self._health.complete:
                await self.notify(
                    type=NotificationType.CONNECTOR_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title="SharePoint sync could not read everything",
                    message=(
                        f"{len(self._health.problems)} part(s) of SharePoint could not be read and were left as they "
                        f"were in PipesHub: {self._health.summary(3)}"
                    ),
                    recipient_user_ids=[self.created_by],
                )
            self._health.raise_if_incomplete()

        except SharePointSyncIncomplete:
            raise
        except Exception as e:
            duration = datetime.now() - start_time
            self.logger.error(f"💥 Critical error in SharePoint connector after {duration}: {e}")
            raise

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A run that skipped part of the source removes nothing it did not see."""
        return FullSyncCoverage(complete=self._health.complete)

    async def run_incremental_sync(self) -> None:
        """Every run reads the changes since the last one (delta tokens, page eTags), so this is run_sync."""
        await self.run_sync()

    async def test_connection_and_access(self) -> bool:
        """Whether the app can read what a sync needs: the sites (Graph) and site permissions (SharePoint REST).

        Raises ConnectorInitError naming the Graph permission when the app is found without it.
        """
        try:
            async with self.rate_limiter:
                root_site = await self.client.sites.by_site_id("root").get()
            root_url = (getattr(root_site, 'web_url', None) or self.sharepoint_domain or '').rstrip('/')
            async with self.rate_limiter:
                await self.client.sites.get()
            async for _ in self._sharepoint_rest_pages(f"{root_url}/_api/web/roleassignments?$top=1"):
                pass
        except Exception as e:
            self.logger.error(f"❌ SharePoint connection test failed: {sanitize_azure_error(e)}")
            return False
        if await graph_sites_fullcontrol_missing(self.client, self.rate_limiter):
            self.logger.error(
                f"❌ SharePoint connection test failed: the app lacks the Microsoft Graph application permission "
                f"{GRAPH_PERMISSION}; without it changes to who can open uniquely shared items are not reported"
            )
            raise ConnectorInitError(
                f"The app lacks the Microsoft Graph application permission {GRAPH_PERMISSION}. Without it, "
                "Microsoft does not report changes to who can open items that have their own permissions. "
                "Grant it in the app registration with admin consent."
            )
        return True

    async def _report_missing_graph_permission(self) -> None:
        """Tells the admin once, when the app is found without Graph Sites.FullControl.All, and again only
        after it was granted and later removed."""
        missing = await graph_sites_fullcontrol_missing(self.client, self.rate_limiter)
        if missing is None:
            return
        known = await self.drive_delta_sync_point.read_sync_point(GRAPH_PERMISSION_SYNC_POINT_KEY)
        if bool((known or {}).get("missing")) == missing:
            return
        await self.drive_delta_sync_point.update_sync_point(
            GRAPH_PERMISSION_SYNC_POINT_KEY, sync_point_data={"missing": missing}
        )
        if missing:
            self.logger.error(f"❌ The app lacks the Microsoft Graph application permission {GRAPH_PERMISSION}")
            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=f"SharePoint: grant Microsoft Graph {GRAPH_PERMISSION}",
                message=(
                    f"The SharePoint app lacks the Microsoft Graph application permission {GRAPH_PERMISSION}. "
                    "Without it, Microsoft does not report when someone is added to or removed from an item that "
                    "has its own permissions, so PipesHub keeps that item's old access. Grant the permission "
                    "with admin consent."
                ),
                recipient_user_ids=[self.created_by],
            )

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream record content (file or page) from SharePoint."""
        try:
            if record.record_type == RecordType.FILE:
                # Get signed URL for file download
                signed_url = await self.get_signed_url(record)
                if not signed_url:
                    raise not_found_at_source(self.display_name)

                return create_stream_record_response(
                    stream_content(
                        signed_url,
                        record_id=record.id,
                        file_name=record.record_name,
                        connector=self.display_name,
                    ),
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
                    raise not_found_at_source(self.display_name)

                async def generate_page() -> AsyncGenerator[bytes, None]:
                    yield page_content.encode('utf-8')

                return create_stream_record_response(
                    generate_page(),
                    filename=record.record_name,
                    mime_type='text/html',
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(status_code=HttpStatusCode.BAD_REQUEST.value, detail=f"Unsupported record type: {record.record_type}")

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to stream record {record.id}: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

    async def _get_page_content(self, site_id: str, page_id: str) -> str:
        """
        Fetches SharePoint page content via REST API using the page's UniqueId (GUID), and cleans it up.
        Images and list web parts become labels; their content is not fetched.
        """
        try:
            # 1. Resolve Site URL via Graph
            site_info = await self.client.sites.by_site_id(site_id).get()
            site_url = site_info.web_url

            # 2. Get Token for SharePoint Scope
            access_token = await self._get_sharepoint_access_token()
            if not access_token:
                self.logger.error(f"Failed to obtain SharePoint access token for page {page_id}")
                raise connector_not_ready(self.display_name)
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
                    raise map_source_status(resp.status_code, connector=self.display_name)

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

                # Images and list web parts are not fetched. The app reads with rights to the whole tenant;
                # an image or a list's rows inlined here would reach every reader of the page, including
                # people SharePoint does not let open that image or list.
                for img in soup.find_all('img'):
                    src = img.get('src') or ''
                    if src.startswith('data:'):
                        continue
                    label = img.get('alt') or unquote(src.split('?')[0]).rsplit('/', 1)[-1] or 'image'
                    img.replace_with(soup.new_string(f"[image: {label}]"))
                for wp in soup.find_all("div", attrs={"data-sp-webpartdata": True}):
                    try:
                        wp_data = json.loads(wp["data-sp-webpartdata"])
                    except (ValueError, TypeError):
                        continue
                    props = wp_data.get("properties", {}) or {}
                    if not props.get("selectedListId"):
                        continue
                    searchable = (wp_data.get("serverProcessedContent", {}) or {}).get("searchablePlainTexts", {}) or {}
                    list_title = (searchable.get("listTitle") or searchable.get("title") or props.get("webPartTitle")
                                  or wp_data.get("title") or "Embedded list")
                    wp.clear()
                    wp.append(soup.new_string(f"[embedded list: {list_title}]"))

                # HTML cleanup - remove unnecessary attributes and clean up structure
                cleaned_html = clean_html_output(soup, logger=self.logger)

                return cleaned_html

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to process SharePoint page {page_id}: {e}", exc_info=True)
            raise to_stream_error(e, connector=self.display_name) from e

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
                            lambda: self.client.sites.by_site_id(site_id).get()
                        )

                    if site:
                        # A change notification can't say what changed at which depth; a run
                        # reads every library's changes since its delta token.
                        await self.run_sync()

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
                raise not_downloadable(
                    "This item is missing the document library it belongs to and cannot "
                    "be downloaded.",
                    connector=self.display_name,
                )

            # Get download URL
            signed_url = await self.msgraph_client.get_signed_url(
                drive_id, record.external_record_id, raise_on_error=True
            )
            return signed_url

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"❌ Error creating signed URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e

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
            self._reset_permission_caches()

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
                    lambda: self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()
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
                        lambda: self.client.sites.by_site_id(encoded_site_id).pages.by_base_site_page_id(page_id).get()
                    )
                except Exception as page_error:
                    if any(term in str(page_error).lower() for term in [HttpStatusCode.FORBIDDEN.value, "accessdenied", HttpStatusCode.NOT_FOUND.value, "notfound"]):
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
            site_web_url = None
            try:
                async with self.rate_limiter:
                    site_response = await self._safe_api_call(
                        lambda: self.client.sites.by_site_id(encoded_site_id).get()
                    )
                if site_response:
                    site_name = getattr(site_response, 'display_name', '') or getattr(site_response, 'name', '')
                    site_web_url = getattr(site_response, 'web_url', None)
            except Exception as e:
                self.logger.warning(f"Could not fetch site name for site {site_id}: {e}")

            # Create updated page record
            page_record = await self._create_page_record(page, site_id, site_name, record)

            if not page_record:
                return None

            pages_scope = await self._get_site_pages_scope(site_id, web_url=site_web_url)
            page_access = await self._get_page_access(site_id, page_id, pages_scope)
            if page_access is None:
                page_record.inherit_permissions = await self._inheritance_when_unreadable(
                    CollectionNames.RECORDS.value, record.id
                )
                permissions = await self._get_page_permissions(site_id, page_id)
            else:
                page_record.inherit_permissions, permissions = page_access

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
                        lambda: self.client.sites.by_site_id(encoded_site_id).lists.by_list_id(list_id).items.by_list_item_id(item_id).get()
                    )
                except Exception as item_error:
                    if any(term in str(item_error).lower() for term in [HttpStatusCode.FORBIDDEN.value, "accessdenied", HttpStatusCode.NOT_FOUND.value, "notfound"]):
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
            return await self._get_site_options(page, limit, search, cursor)
        elif filter_key == SyncFilterKey.DRIVE_IDS:
            return await self._get_document_library_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _paginate_filter_options_search(
        self,
        entity_types: List[str],
        full_query: str,
        query_key: str,
        page: int,
        limit: int,
        cursor: Optional[str],
        *,
        stale_cursor_warning: str,
        debug_log_prefix: str,
        accept_hit: Callable[[Dict[str, Any], set[str]], Optional[FilterOption]],
    ) -> FilterOptionsResponse:
        """Microsoft Search pagination for site / library filter dropdowns.

        Graph pagination uses fixed ``from``/``size`` windows. After connector-side filtering we may
        return a full ``limit`` while still leaving *accepted* hits in the same Graph slice; those
        continue via cursor field ``a`` (accepted skips) instead of prematurely advancing ``f``.
        """
        graph_batch = _sharepoint_filter_options_graph_batch_size(limit)

        use_cursor = False
        scan_from = 0
        accepted_skip = 0  # Accepted options already returned for slice ``scan_from`` (cursor ``a``).
        if cursor and cursor.strip():
            payload = self._sharepoint_search_cursor_decode(cursor.strip())
            if (
                payload
                and payload.get("q") == query_key
                and int(payload.get("b", -1)) == graph_batch
            ):
                use_cursor = True
                scan_from = max(0, int(payload["f"]))
                accepted_skip = int(payload.get("a", 0) or 0)
            else:
                self.logger.warning(stale_cursor_warning)

        if use_cursor:
            skip_remaining = 0
        else:
            skip_remaining = max(0, (page - 1) * limit)
        max_graph_iterations = _sharepoint_filter_options_max_search_batches(
            page, limit, graph_batch, resume_with_cursor=use_cursor
        )

        collected: List[FilterOption] = []
        seen_keys: set[str] = set()
        iterations = 0
        index_reports_more_after_last_batch = False

        while iterations < max_graph_iterations:
            if len(collected) >= limit:
                break

            iterations += 1
            last_more_available = False

            emitted_this_batch = 0
            found_extra_filtered = False
            skip_budget_start = accepted_skip
            skip_rem = skip_budget_start

            raw_result = await self.msgraph_client.search_query(
                entity_types=entity_types,
                query=full_query,
                page=1,
                limit=graph_batch,
                region=self.tenant_region,
                from_offset=scan_from,
            )
            if not raw_result:
                break

            additional_data = getattr(raw_result, 'additional_data', {}) or {}
            value_list = additional_data.get('value', [])
            if not value_list:
                break

            # MS Search batches may expose multiple hitsContainers across ``value[]`` rows; OR-merge
            # ``moreResultsAvailable`` instead of trusting only the final container inspected.
            batch_had_hits = False
            for search_resp in value_list:
                hits_containers = search_resp.get('hitsContainers', [])
                for container in hits_containers:
                    last_more_available |= bool(container.get('moreResultsAvailable', False))
                    hits = container.get('hits', [])
                    if hits:
                        batch_had_hits = True

                    for hit in hits:
                        opt = accept_hit(hit, seen_keys)
                        if opt is None:
                            continue
                        if skip_remaining > 0:
                            skip_remaining -= 1
                            continue
                        if skip_rem > 0:
                            skip_rem -= 1
                            continue
                        if len(collected) < limit:
                            collected.append(opt)
                            emitted_this_batch += 1
                        else:
                            found_extra_filtered = True

            if found_extra_filtered:
                accepted_skip = skip_budget_start + emitted_this_batch
            else:
                scan_from += graph_batch
                accepted_skip = 0

            index_reports_more_after_last_batch = (
                found_extra_filtered or last_more_available
            )

            if len(collected) >= limit:
                break
            if not last_more_available:
                break
            if not batch_had_hits:
                continue

        has_more = index_reports_more_after_last_batch
        out_cursor: Optional[str] = None
        if has_more:
            out_cursor = self._sharepoint_search_cursor_encode(
                scan_from,
                graph_batch,
                query_key,
                accepted_skip=accepted_skip,
            )

        self.logger.debug(
            "%s page=%s limit=%s cursor=%s returned=%s "
            "has_more=%s graph_iterations=%s next_from=%s next_accepted_skip=%s",
            debug_log_prefix,
            page,
            limit,
            bool(use_cursor),
            len(collected),
            has_more,
            iterations,
            scan_from if has_more else None,
            accepted_skip if has_more else None,
        )

        return FilterOptionsResponse(
            success=True,
            options=collected,
            page=page,
            limit=limit,
            has_more=has_more,
            cursor=out_cursor,
        )

    async def _get_site_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint sites.

        ``page`` / ``limit`` refer to sites **after** skipping OneDrive personal hosts,
        not raw Microsoft Search rows. Optional ``cursor`` resumes search pagination; the
        opaque string encodes Microsoft ``from``, batch size, query key, and optionally
        accepted-skip count ``a`` when more sites remain in the same Graph result slice
        (see ``_sharepoint_search_cursor_encode``).

        Microsoft Search is queried in bounded batches per request (see
        ``_sharepoint_filter_options_max_search_batches``); use ``has_more``/``cursor`` when
        not all options fit in that budget.
        """

        search_query = search.strip() if search else ""
        full_query = f"{search_query}*"
        query_key = hashlib.sha256(full_query.encode("utf-8")).hexdigest()[:16]

        def accept_hit(hit: Dict[str, Any], seen_keys: set[str]) -> Optional[FilterOption]:
            resource = hit.get('resource', {})
            site_id = resource.get('id')
            web_url = resource.get('webUrl')
            if not site_id and not web_url:
                return None
            if web_url and re.match(
                r'^https?://[^/]*-my\.sharepoint\.com(/|$)',
                web_url,
                re.IGNORECASE,
            ):
                return None
            dedupe_key = str(site_id or web_url)
            if dedupe_key in seen_keys:
                return None
            seen_keys.add(dedupe_key)

            label = (
                resource.get('displayName') or
                resource.get('name') or
                web_url or
                "Unknown Site"
            )
            return FilterOption(id=site_id or web_url, label=label)

        return await self._paginate_filter_options_search(
            entity_types=["site"],
            full_query=full_query,
            query_key=query_key,
            page=page,
            limit=limit,
            cursor=cursor,
            stale_cursor_warning=(
                "Ignoring invalid or stale SharePoint site filter cursor "
                "(search/limit changed or corrupt payload)"
            ),
            debug_log_prefix="SharePoint site filter options",
            accept_hit=accept_hit,
        )

    @staticmethod
    def _sharepoint_search_cursor_encode(
        from_offset: int,
        graph_batch: int,
        query_key: str,
        *,
        accepted_skip: int = 0,
    ) -> str:
        """Encode cursor for SharePoint filter-options Search pagination.

        ``a`` (accepted_skip) continues within the same Graph ``from`` slice after returning a full
        page but leaving further accepted hits in that slice—see ``_paginate_filter_options_search``.
        """
        payload: Dict[str, Any] = {
            "f": from_offset,
            "b": graph_batch,
            "q": query_key,
        }
        if accepted_skip > 0:
            payload["a"] = accepted_skip
        raw = json.dumps(payload, separators=(",", ":"))
        return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    @staticmethod
    def _sharepoint_search_cursor_decode(cursor: str) -> Optional[Dict[str, Any]]:
        """Decode filter-options search cursor.

        Rejects ``f`` outside ``SHAREPOINT_FILTER_OPTIONS_MAX_SEARCH_FROM_OFFSET``
        (unsigned payload) and optional accepted-skip ``a`` outside
        ``SHAREPOINT_FILTER_OPTIONS_MAX_CURSOR_ACCEPTED_SKIP``.
        """
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            blob = base64.urlsafe_b64decode(padded.encode("ascii"))
            data = json.loads(blob.decode("utf-8"))
            if not isinstance(data, dict):
                return None
            if "f" not in data or "b" not in data or "q" not in data:
                return None
            f_val = int(data["f"])
            if f_val < 0 or f_val > SHAREPOINT_FILTER_OPTIONS_MAX_SEARCH_FROM_OFFSET:
                return None
            skip_raw = data.get("a", 0)
            skip_val = int(skip_raw) if skip_raw is not None else 0
            if skip_val < 0 or skip_val > SHAREPOINT_FILTER_OPTIONS_MAX_CURSOR_ACCEPTED_SKIP:
                return None
            result: Dict[str, Any] = {"f": f_val, "b": int(data["b"]), "q": str(data["q"])}
            if skip_val:
                result["a"] = skip_val
            return result
        except (
            binascii.Error,
            json.JSONDecodeError,
            UnicodeDecodeError,
            ValueError,
            TypeError,
        ):
            return None

    async def _get_document_library_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for SharePoint document libraries (drives only).

        ``page`` and ``limit`` apply to the list *after* deduplication and connector-side
        filters—not to raw Microsoft Search rows.

        Without ``cursor``: scans from search offset 0, skips ``(page-1)*limit`` accepted
        libraries, returns up to ``limit`` more (stateless replay).

        With ``cursor``: resumes at the encoded Microsoft Search ``from`` offset (no skip);
        use the cursor from the previous response when ``has_more`` is true. When more accepted
        libraries remain in that Graph slice after a full page, the cursor may include field ``a``
        (accepted skip) alongside ``f``—see ``_sharepoint_search_cursor_encode``.

        Graph search calls per request are capped (``_sharepoint_filter_options_max_search_batches``);
        continue with ``cursor`` when results are truncated before the page is filled.
        """

        search_query = search.strip() if search else ""
        full_query = f"{search_query}* contentclass:STS_List_DocumentLibrary"
        query_key = hashlib.sha256(full_query.encode("utf-8")).hexdigest()[:16]

        SYSTEM_LIBRARY_NAMES = frozenset({
            'FormServerTemplates',
            'SiteAssets',
            'Style Library',
            'SitePages',
            '_catalogs',
            'appdata',
            'AppCatalog',
        })

        def accept_hit(hit: Dict[str, Any], seen_keys: set[str]) -> Optional[FilterOption]:
            resource = hit.get('resource', {})
            summary = hit.get('summary', '')
            list_id = resource.get('id')
            web_url = resource.get('webUrl', '')
            name = resource.get('name', '')
            display_name = resource.get('displayName', '')
            site_id = resource.get('parentReference', {}).get('siteId', '')
            if not site_id:
                return None
            unique_key = f"{list_id}:{site_id}"
            if not list_id or unique_key in seen_keys:
                return None

            is_document_library = 'DocumentLibrary' in summary or not summary
            if not is_document_library:
                return None
            if '/Lists/' in web_url:
                return None
            if name in SYSTEM_LIBRARY_NAMES:
                return None
            if '/contentstorage/' in web_url:
                return None
            if re.match(r'^https?://[^/]*-my\.sharepoint\.com(/|$)', web_url, re.IGNORECASE):
                return None
            if '/calendar.aspx' in web_url:
                return None

            seen_keys.add(unique_key)

            library_name = display_name or name or "Unknown Library"
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
            if " - " in library_name and site_name != "Unknown Site":
                library_name = library_name.split(" - ")[-1]

            final_label = f"{library_name} ({site_name})"
            normalized_url = self._normalize_document_library_url(web_url)
            return FilterOption(id=normalized_url, label=final_label)

        return await self._paginate_filter_options_search(
            entity_types=["list"],
            full_query=full_query,
            query_key=query_key,
            page=page,
            limit=limit,
            cursor=cursor,
            stale_cursor_warning=(
                "Ignoring invalid or stale document-library filter cursor "
                "(search/limit changed or corrupt payload)"
            ),
            debug_log_prefix="Document library filter options",
            accept_hit=accept_hit,
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
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        return SharePointConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

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
