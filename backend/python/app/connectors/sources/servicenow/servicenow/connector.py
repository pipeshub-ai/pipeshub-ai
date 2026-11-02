"""
ServiceNow Knowledge Base Connector

This connector syncs knowledge base articles, categories, attachments, and permissions
from ServiceNow into the PipesHub AI platform.

Synced Entities:
- Users and Groups (for permissions)
- Roles (for role-based permissions)
- Organizational Entities (companies, departments, locations)
- Knowledge Bases (containers)
- Categories (hierarchy)
- KB Articles (content)
- Attachments (files)
"""

import asyncio
import base64
import hashlib
import html
import json
import mimetypes
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from logging import Logger
from typing import AsyncGenerator, Dict, List, Optional, Tuple, TypeVar
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AccessRule,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    ActionItem,
    BaseConnector,
    ConnectorInitError,
    oauth_app_not_linked_error,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    FailedItems,
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.constants import (
    CONNECTOR_EMAIL_IDENTITY_INFO,
    AuthFieldKeys,
    CommonStrings,
    ConnectorRegistryCategories,
    IconPaths,
    OAuthConfigKeys,
    OAuthDefaults,
    OAuthRedirectPaths,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
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
    MultiselectOperator,
    OptionSourceType,
    SyncFilterKey,
    name_passes_filter,
)
from app.connectors.core.registry.types import FieldType
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.servicenow.common.apps import ServicenowApp
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
    OrganizationalEntityConfig,
    ServiceNowConfigPaths,
    ServiceNowConnectorMetadata,
    ServiceNowDefaults,
    ServiceNowDictKeys,
    ServiceNowFields,
    ServiceNowPrefixes,
    ServiceNowQueryValues,
    ServiceNowRoles,
    ServiceNowSyncPointKeys,
    ServiceNowTables,
    ServiceNowURLPatterns,
)
from app.models.blocks import (
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import (
    NotificationOutcome,
    NotificationRecipientRole,
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.http.address_guard import AddressNotAllowedError
from app.sources.client.http.exception.exception import BadRequestError
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.servicenow.servicenow import (
    ServiceNowRateLimited,
    ServiceNowRateLimitPolicy,
    ServiceNowRESTClientViaAPIKey,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
    ServiceNowRESTClientViaOAuthClientCredentials,
    ServiceNowRESTClientViaUsernamePassword,
    check_servicenow_instance_url,
    rate_limit_reset_label,
)
from app.sources.external.servicenow.models import (
    PARSE_ERROR_MESSAGE,
    REDIRECT_MESSAGE,
    AttachmentMetadata,
    ServiceNowAccessError,
    ServiceNowAPIError,
    ServiceNowAuthError,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    TableAPIRecord,
    TableAPIResponse,
)
from app.sources.external.servicenow.servicenow import ServiceNowDataSource
from app.utils.html_markdown import html_to_markdown_async
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import datetime_to_epoch_ms
from app.utils.url_redaction import redact_urls_in_text

T = TypeVar("T")

# Articles stored before they were keyed by number carry a version's sys_id (32 hex) instead.
_LEGACY_ARTICLE_KEY = re.compile(r"^[0-9a-f]{32}$")

# The kb_knowledge fields an article is written from
_ARTICLE_FIELDS = [
    ServiceNowFields.SYS_ID,
    ServiceNowFields.NUMBER,
    ServiceNowFields.SHORT_DESCRIPTION,
    ServiceNowFields.TEXT,
    ServiceNowFields.WIKI,
    ServiceNowFields.ARTICLE_TYPE,
    ServiceNowFields.KB_KNOWLEDGE_BASE,
    ServiceNowFields.KB_CATEGORY,
    ServiceNowFields.WORKFLOW_STATE,
    ServiceNowFields.ACTIVE,
    ServiceNowFields.PUBLISHED,
    ServiceNowFields.CAN_READ_USER_CRITERIA,
    ServiceNowFields.CANNOT_READ_USER_CRITERIA,
    ServiceNowFields.OWNERSHIP_GROUP,
    ServiceNowFields.SYS_CREATED_ON,
    ServiceNowFields.SYS_UPDATED_ON,
    ServiceNowFields.SYS_MOD_COUNT,  # for _row_key
]

# Tables that say who may read what. A listing of one that ServiceNow counts rows for but shows
# none of is unreadable to this account, not empty: read as empty it would take access away
# (no members, no admins) or give it (no Cannot Read).
_ACCESS_TABLES = frozenset({
    ServiceNowTables.USER_CRITERIA,
    ServiceNowTables.KB_UC_CAN_READ_MTOM,
    ServiceNowTables.KB_UC_CANNOT_READ_MTOM,
    ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM,
    ServiceNowTables.KB_UC_CANNOT_CONTRIBUTE_MTOM,
    ServiceNowTables.SYS_USER,
    ServiceNowTables.SYS_USER_GROUP,
    ServiceNowTables.SYS_USER_GRMEMBER,
    ServiceNowTables.SYS_USER_ROLE,
    ServiceNowTables.SYS_USER_HAS_ROLE,
    # Companies, departments and locations decide criteria matches too.
    ServiceNowTables.CORE_COMPANY,
    ServiceNowTables.CMN_DEPARTMENT,
    ServiceNowTables.CMN_LOCATION,
})

# The tables a sync reads: the connection test fails, naming them, when the account may not read
# one. sys_user_role_contains is optional (sys_user_has_role already holds the inherited roles),
# and sys_properties and sp_portal fall back to the settings that grant less (see init).
_REQUIRED_TABLES = (
    (ServiceNowTables.SYS_USER, None),
    (ServiceNowTables.SYS_USER_GROUP, None),
    (ServiceNowTables.SYS_USER_GRMEMBER, None),
    (ServiceNowTables.SYS_USER_ROLE, None),
    (ServiceNowTables.SYS_USER_HAS_ROLE, None),
    (ServiceNowTables.CORE_COMPANY, None),
    (ServiceNowTables.CMN_DEPARTMENT, None),
    (ServiceNowTables.CMN_LOCATION, None),
    (ServiceNowTables.KB_KNOWLEDGE_BASE, None),
    (ServiceNowTables.KB_CATEGORY, None),
    (ServiceNowTables.KB_KNOWLEDGE, None),
    (ServiceNowTables.SYS_ATTACHMENT, f"{ServiceNowFields.TABLE_NAME}={ServiceNowTables.KB_KNOWLEDGE}"),
    (ServiceNowTables.KB_FEEDBACK, None),
    (ServiceNowTables.USER_CRITERIA, None),
    (ServiceNowTables.KB_UC_CAN_READ_MTOM, None),
    (ServiceNowTables.KB_UC_CANNOT_READ_MTOM, None),
    (ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM, None),
    (ServiceNowTables.KB_UC_CANNOT_CONTRIBUTE_MTOM, None),
)

# A knowledge base's criteria ids by kind, from these tables (see _kb_criteria)
_KB_MTOM = (
    (ServiceNowDictKeys.READ, ServiceNowTables.KB_UC_CAN_READ_MTOM),
    (ServiceNowDictKeys.CANNOT_READ, ServiceNowTables.KB_UC_CANNOT_READ_MTOM),
    (ServiceNowDictKeys.WRITE, ServiceNowTables.KB_UC_CAN_CONTRIBUTE_MTOM),
    (ServiceNowDictKeys.CANNOT_WRITE, ServiceNowTables.KB_UC_CANNOT_CONTRIBUTE_MTOM),
)

# The tables whose rows decide who reads an article with criteria or an ownership group, without
# the article changing (see _access_reread_due)
_ACCESS_DEPENDENCIES = (
    ServiceNowTables.USER_CRITERIA,
    *(table for _, table in _KB_MTOM),
    ServiceNowTables.SYS_USER_GRMEMBER,
    ServiceNowTables.SYS_USER_HAS_ROLE,
    ServiceNowTables.SYS_USER,
    ServiceNowTables.SYS_USER_GROUP,
    ServiceNowTables.KB_KNOWLEDGE_BASE,
)

# Shown when the account cannot read the instance settings the connector reads at start (see init)
_PROPERTIES_HIDDEN = (
    "The integration user cannot read the glide.knowman properties, so PipesHub uses the settings that "
    "grant less: knowledge bases with no user criteria stay closed, and contributors are held to each "
    "article's own criteria."
)
_PORTAL_HIDDEN = "The integration user cannot read the Service Portal settings, so article links open the /kb portal."
_CLEANUP_FAILED = (
    "Some articles or knowledge bases that ServiceNow no longer shows, or the filters leave out, could not "
    "be removed; this is retried on the next sync"
)
_NOT_ADMIN = (
    "The integration user is not a ServiceNow admin, so PipesHub syncs only the articles, users and "
    "groups it can read."
)

# The name prefixes of the user groups that stand for a company, department or location
_ORG_GROUP_PREFIXES = {
    ServiceNowFields.COMPANY: ServiceNowPrefixes.COMPANY,
    ServiceNowFields.DEPARTMENT: ServiceNowPrefixes.DEPARTMENT,
    ServiceNowFields.LOCATION: ServiceNowPrefixes.LOCATION,
}

# Stands for "every user" where a knowledge base with no user criteria is open (see _kb_criteria)
_EVERYONE = "everyone"

_SCRIPT_COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_SYS_ID = re.compile(r"[0-9a-f]{32}")

_IMG_SRC = re.compile(r'(<img\b[^>]*?\bsrc=["\'])([^"\']+)(["\'])', re.I)
# An image stored on the instance (the editor's inline images); the text escapes its "=" as "&#61;"
_INSTANCE_IMAGE = re.compile(r"/?sys_attachment\.do\?sys_id=([0-9a-f]{32})(?:&|$)", re.I)

# The kb_feedback fields an article streams: never reason or work_notes
_FEEDBACK_FIELDS = [
    ServiceNowFields.COMMENTS,
    ServiceNowFields.USEFUL,
    ServiceNowFields.RATING,
    ServiceNowFields.FLAGGED,
    ServiceNowFields.PARENT_COMMENT,
    ServiceNowFields.ROOT_FEEDBACK,
    ServiceNowFields.USER_DISPLAY_NAME,
    ServiceNowFields.SYS_CREATED_ON,
]


@dataclass
class _Listing:
    """What one _iter_table listing saw: ``total`` rows counted before ACLs, ``seen`` shown."""
    total: int = 0
    seen: int = 0

    @property
    def partial(self) -> bool:
        return self.seen < self.total


class _CriteriaUnreadable(Exception):
    """A user criteria (Can Read or Cannot Read) could not be read or resolved to users;
    the grants it governs are left as stored."""


class _CriteriaHidden(_CriteriaUnreadable):
    """An ACL hides an allow criterion, or a knowledge base's criteria rows, from this account:
    unknown until the account may read them, not a failure the next run would get past."""


def _plural(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def _raise_if_run_stop(error: BaseException | None) -> None:
    """Raise the rate limit, auth failure or unreadable access table behind ``error``, if any.
    Every later call would fail the same way, so the run ends there (checkpoints unmoved, nothing
    unlinked) instead of failing item by item."""
    while error is not None:
        if isinstance(error, (ServiceNowRateLimited, ServiceNowAuthError, ServiceNowAccessError)) or (
            isinstance(error, ServiceNowAPIError) and error.status_code == HttpStatusCode.TOO_MANY_REQUESTS.value
        ):
            raise error
        error = error.__cause__


def _stream_error(error: Exception, connector: str) -> HTTPException:
    """The answer to a failed stream: a spent rate limit is a 429 that says when to try again."""
    if isinstance(error, ServiceNowRateLimited):
        return map_source_status(
            HttpStatusCode.TOO_MANY_REQUESTS.value, connector=connector,
            retry_after=str(max(1, int(error.reset_at - time.time()))),
        )
    return to_stream_error(error, connector=connector)


def _instance_url_field() -> AuthField:
    return AuthField(
        name=AuthFieldKeys.INSTANCE_URL,
        display_name="ServiceNow Instance URL",
        placeholder="https://your-instance.service-now.com",
        description="Your ServiceNow instance URL (e.g., https://acme.service-now.com)",
        field_type=FieldType.URL.value,
        required=True,
        max_length=OAuthDefaults.MAX_URL_LENGTH,
    )


# The client-credentials grant, as AuthType.CUSTOM: the platform refreshes and authorizes only
# OAUTH types, and this grant has no refresh token or consent; the connector mints its own tokens.
_CLIENT_CREDENTIALS = "the ServiceNow OAuth application whose OAuth application user the connector acts as"
_API_KEY_FIELD = "apiKey"
# A client-credentials token this close to expiry is replaced before the next request.
_TOKEN_RENEW_MARGIN_S = 60
_MISSING_CREDENTIALS = "The ServiceNow connector needs {what}. Enter them on the connector's Authenticate tab."


@ConnectorBuilder(ServiceNowConnectorMetadata.NAME)\
    .in_group(ServiceNowConnectorMetadata.NAME)\
    .with_description("Sync knowledge base articles, categories, and permissions from ServiceNow")\
    .with_categories([ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_resilience_config(rate_limit=10, max_retries=3, base_delay=1.0, max_delay=60.0)\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name=ServiceNowConnectorMetadata.NAME,
            authorize_url="https://example.service-now.com/oauth_auth.do",
            token_url="https://example.service-now.com/oauth_token.do",
            redirect_uri=OAuthRedirectPaths.CONNECTOR_CALLBACK.format(
                connector_name=ServiceNowConnectorMetadata.NAME
            ),
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["useraccount"],
                agent=[]
            ),
            fields=[
                _instance_url_field(),
                AuthField(
                    name=AuthFieldKeys.AUTHORIZE_URL,
                    display_name="ServiceNow Authorize URL",
                    placeholder="https://your-instance.service-now.com/oauth_auth.do",
                    description="Your ServiceNow authorize URL (e.g., https://acme.service-now.com/oauth_auth.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                AuthField(
                    name=AuthFieldKeys.TOKEN_URL,
                    display_name="ServiceNow Token URL",
                    placeholder="https://your-instance.service-now.com/oauth_token.do",
                    description="Your ServiceNow token URL (e.g., https://acme.service-now.com/oauth_token.do)",
                    field_type=FieldType.URL.value,
                    required=True,
                    max_length=OAuthDefaults.MAX_URL_LENGTH,
                ),
                CommonFields.client_id(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry"),
                CommonFields.client_secret(f"{ServiceNowConnectorMetadata.NAME} OAuth Application Registry")
            ],
            icon_path=IconPaths.connector_icon(Connectors.SERVICENOW.value),
            app_group=ServiceNowConnectorMetadata.NAME,
            app_description=f"OAuth application for accessing {ServiceNowConnectorMetadata.NAME} API and knowledge base services",
            app_categories=[ConnectorRegistryCategories.KNOWLEDGE_MANAGEMENT]
        ),
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            _instance_url_field(), CommonFields.username(), CommonFields.password(),
        ]),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            _instance_url_field(),
            CommonFields.api_token("ServiceNow API key", field_name=_API_KEY_FIELD),
        ]),
        AuthBuilder.type(AuthType.CUSTOM).fields([
            _instance_url_field(),
            CommonFields.client_id(_CLIENT_CREDENTIALS),
            CommonFields.client_secret(_CLIENT_CREDENTIALS),
        ]),
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SERVICENOW.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "ServiceNow OAuth Setup",
                "https://www.servicenow.com/docs/r/platform-security/authentication/c_OAuthApplications.html",
                "Setup"
            )
        )
        .add_documentation_link(
            DocumentationLink(
                "Pipeshub Documentation",
                "https://docs.pipeshub.com/connectors/servicenow/servicenow",
                "Pipeshub"
            )
        )
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .add_filter_field(FilterField(
            name=SyncFilterKey.KNOWLEDGE_BASES.value,
            display_name="Knowledge bases",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Sync only these knowledge bases (In) or all but these (Not in). Leave empty to sync "
                        "every knowledge base the account can read.",
            default_operator=MultiselectOperator.IN.value,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(FilterField(
            name=SyncFilterKey.FOLDER_IDS.value,
            display_name="Categories",
            filter_type=FilterType.MULTISELECT,
            category=FilterCategory.SYNC,
            description="Sync only articles in these categories and their sub-categories (In), or skip them "
                        "(Not in). Articles with no category are kept by Not in and dropped by In.",
            default_operator=MultiselectOperator.IN.value,
            option_source_type=OptionSourceType.DYNAMIC,
        ))
        .add_filter_field(CommonFields.modified_date_filter("Sync only articles last updated in this range (UTC)."))
        .add_filter_field(CommonFields.created_date_filter(
            "Sync only articles whose current version was created in this range (UTC)."
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name=IndexingFilterKey.KNOWLEDGE_BASE.value,
            display_name="Index articles",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Index knowledge articles. Applies to new and changed articles.",
            default_value=True,
        ))
        .add_filter_field(FilterField(
            name=IndexingFilterKey.ATTACHMENTS.value,
            display_name="Index attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Index article attachments. Applies to new and changed attachments.",
            default_value=True,
        ))
    )\
    .build_decorator()

class ServiceNowConnector(BaseConnector):
    """
    ServiceNow Knowledge Base Connector

    This connector syncs ServiceNow Knowledge Base data including:
    - Knowledge bases and categories
    - KB articles with metadata
    - Article attachments
    - User and group permissions
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
        """
        Initialize the ServiceNow KB Connector.

        Args:
            logger: Logger instance
            data_entities_processor: Processor for handling entities
            data_store_provider: Data store provider
            config_service: Configuration service
        """
        super().__init__(
            ServicenowApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # The declared resilience as a policy that stops on an hourly rate limit instead of
        # retrying into it. Built once, so a re-init keeps an armed stop (see BaseConnector.resilience).
        self._resilience = ServiceNowRateLimitPolicy.from_config(
            self.connector_metadata.get("resilienceConfig"), name=str(self.connector_name), logger=self.logger
        )
        self._resilience_loaded = True

        # ServiceNow API client instances
        self.servicenow_client: Optional[HTTPClient] = None
        # The saved auth type (OAUTH, BASIC_AUTH, API_TOKEN, or CUSTOM for client credentials), set by init
        self.auth_type: str = AuthType.OAUTH
        self.servicenow_datasource: Optional[ServiceNowDataSource] = None
        self.connector_id = connector_id

        # Configuration
        self.instance_url: Optional[str] = None
        # The portal that serves the knowledge pages. Read from the instance
        # during init(), because it differs from one instance to the next.
        self.kb_portal_suffix: str = ServiceNowDefaults.KB_PORTAL_SUFFIX
        # Whether contributors are held to an article's own criteria (readers always are).
        # Read from the instance during init(); false is the ServiceNow default.
        self.apply_article_read_criteria: bool = False
        # Whether a knowledge base with no user criteria is closed (see _resolve_block_no_criteria).
        self.block_kb_without_criteria: bool = True
        # The connector's users, read once per sync run after the user sync (see _all_app_users)
        self._app_users: list[AppUser] | None = None
        # source user sys_id -> email, read once per sync run (see _process_criteria_permissions)
        self._email_by_source_id: dict[str, str] | None = None
        # user_criteria rows by sys_id, read once per sync run (see _fetch_user_criteria)
        self._criteria_rows: dict[str, TableAPIRecord | None] = {_EVERYONE: TableAPIRecord(sys_id=_EVERYONE, active="true")}
        # KB sys_id -> its criteria ids by kind (see _kb_criteria), read once per run
        self._kb_criteria_cache: dict[str, dict[str, list[str]]] = {}
        # Every KB's criteria ids by kind, from one listing of each mtom table per run (see _load_kb_criteria)
        self._kb_mtom: dict[str, dict[str, list[str]]] | None = None
        # source user ids the out-of-box "Has a role" criterion matches, per run (see _has_role_users)
        self._has_role_users_cache: set[str] | None = None
        # sys_id of the user named guest, per run (see _guest_ids)
        self._guest_cache: set[str] | None = None
        # KB sys_id -> the READ grants that stand for its contributors (see _kb_contributor_grants)
        self._kb_contributor_cache: dict[str, list[Permission]] = {}
        # (criteria sys_id, deny) -> the source user ids it matches, per run (see _users_matching_criteria)
        self._criteria_users: dict[tuple[str, bool], set[str]] = {}
        # sys_user_group rows and their flattened members (source user ids), per run (see _sync_user_groups)
        self._groups: dict[str, SysUserGroup] = {}
        self._group_members: dict[str, set[str]] = {}
        # What this run's listings already hold, per criteria field (group, role, department, location,
        # company): id -> member source user ids, and whether an ACL hid some rows (see _members_of)
        self._held: dict[str, tuple[dict[str, set[str]], bool]] = {}
        # source user id -> the names of its roles in every state, from the role listing (see _has_role_users)
        self._role_names: dict[str, list[str]] | None = None
        self._admin_source_ids: set | None = None
        self._kb_owner_cache: dict[str, set] = {}
        # Set when this run skipped something after an error it did not raise, so a full
        # sync's cleanup unlinks nothing (see full_sync_coverage).
        self._sync_read_incomplete = False
        # The run that moves attachments stored under their sys_id to their key (see _attachment_keys)
        self._rekey_attachments = False
        # What this run could not do, reported once when it ends (see _report_sync_problems):
        # lines as they are shown, and counts of skipped things by the line that names them.
        self._problems: list[str] = []
        self._skipped: dict[str, int] = defaultdict(int)
        # Conditions of the instance's set-up rather than of this run, shown when they first appear
        self._persistent: set[str] = set()
        self._kbs_unread: list[str] = []
        self._criteria_noted: dict[str, set[str]] = defaultdict(set)
        self._no_reader = 0
        # What init found the integration user cannot read (see _notify_setup_warnings)
        self._setup_warnings: list[str] = []
        # Set by the knowledge base and category steps of a run (see _sync_knowledge_bases, _sync_categories)
        self._live_kb_ids: set[str] | None = None
        self._kbs_back_in_scope: set[str] = set()
        self._category_ids: set[str] | None = None
        # The knowledge base and category scopes to store once the articles are synced, and
        # whether a category was deleted (see _remove_groups_out_of_scope)
        self._scopes_to_store: dict[str, list[str]] = {}
        self._categories_gone = False
        # The admin's filters, read at the start of every run (see _load_filters)
        self.sync_filters = FilterCollection()
        self.indexing_filters = FilterCollection()
        # (In, the chosen categories with their sub-categories) when the categories filter is set
        self._category_filter: tuple[bool, set[str]] | None = None
        self.client_id: str | None = None
        self.client_secret: str | None = None
        self.redirect_uri: str | None = None

        # OAuth tokens (managed by framework/client)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        # Sync points for different entity types. Groups, roles and knowledge
        # bases have none: those three reads must stay full, see
        # _fetch_all_memberships and _sync_knowledge_bases.
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.category_sync_point = _create_sync_point(SyncDataPointType.RECORD_GROUPS)
        self.article_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        # Organizational entity sync points
        self.company_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.department_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.location_sync_point = _create_sync_point(SyncDataPointType.GROUPS)

        # Map entity types to their sync points for easy lookup
        self.org_entity_sync_points = {
            "company": self.company_sync_point,
            "department": self.department_sync_point,
            "location": self.location_sync_point,
        }

    async def init(self) -> bool:
        """
        Initialize the connector's API client for its saved auth type (OAuth from the linked OAuth
        app, basic auth, an API key or client credentials) and test the connection.

        Returns:
            bool: True if initialization successful, False on an unexpected error

        Raises:
            ConnectorInitError: with the reason, for the user, when the settings, the sign-in or
                the integration user's access do not allow a sync
        """
        try:
            self.logger.info("🔧 Initializing ServiceNow KB Connector...")
            self._setup_warnings = []
            connector_id = self.connector_id
            # Load configuration
            config = await self.config_service.get_config(
                ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id)
            )

            if not config:
                raise ConnectorInitError(
                    "The ServiceNow connector has no saved settings. Configure it on its Authenticate tab."
                )

            # Extract OAuth configuration
            auth_config = config.get(OAuthConfigKeys.AUTH, {})
            self.auth_type = str(auth_config.get("authType") or AuthType.OAUTH).upper()
            if self.auth_type != AuthType.OAUTH:
                await self._close_client()
                self.servicenow_client = await self._build_client(auth_config)
                return await self._connect()
            oauth_config_id = auth_config.get(OAuthConfigKeys.OAUTH_CONFIG_ID)

            if not oauth_config_id:
                self.logger.error(
                    "ServiceNow has no oauthConfigId in its auth config; it reads its "
                    "client ID and secret only from a linked OAuth app."
                )
                raise oauth_app_not_linked_error()

            oauth_config = await self._fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=ServiceNowDefaults.CONNECTOR_TYPE,
                auth_config=auth_config,
            )

            if not oauth_config:
                raise ConnectorInitError(
                    "The OAuth app this connector is linked to was not found. Choose or create an OAuth app "
                    "on the connector's Authenticate tab, then sign in again on the Authorize tab."
                )

            oauth_config_data = oauth_config.get(OAuthConfigKeys.CONFIG, {})

            # A trailing slash would give "https://<instance>.service-now.com//kb?...".
            self.instance_url = (oauth_config_data.get(AuthFieldKeys.INSTANCE_URL) or "").rstrip("/") or None
            self.client_id = oauth_config_data.get(AuthFieldKeys.CLIENT_ID)
            self.client_secret = oauth_config_data.get(AuthFieldKeys.CLIENT_SECRET)
            self.redirect_uri = oauth_config.get(AuthFieldKeys.REDIRECT_URI)
            self.logger.info("Using shared OAuth config for ServiceNow connector")

            # OAuth tokens (stored after authorization flow completes)
            credentials = config.get(OAuthConfigKeys.CREDENTIALS, {})
            self.access_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)
            self.refresh_token = credentials.get(OAuthConfigKeys.REFRESH_TOKEN)

            if not all(
                [
                    self.instance_url,
                    self.client_id,
                    self.client_secret,
                    self.redirect_uri,
                ]
            ):
                raise ConnectorInitError(
                    "The linked OAuth app is missing the instance URL, client ID, client secret or redirect URI. "
                    "Complete it on the connector's Authenticate tab."
                )
            try:
                self.instance_url = check_servicenow_instance_url(self.instance_url, same_host={
                    "token URL": oauth_config_data.get(AuthFieldKeys.TOKEN_URL),
                    "authorize URL": oauth_config_data.get(AuthFieldKeys.AUTHORIZE_URL),
                })
            except ValueError as e:
                raise ConnectorInitError(str(e)) from e

            # Check if OAuth flow is complete
            if not self.access_token:
                raise ConnectorInitError(
                    "The connector is not authorized yet. Sign in to ServiceNow on the connector's Authorize tab."
                )

            # Initialize ServiceNow OAuth client
            self.logger.info("🔗 Connecting to the ServiceNow instance")
            await self._close_client()
            self.servicenow_client = ServiceNowRESTClientViaOAuthAuthorizationCode(
                instance_url=self.instance_url,
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                access_token=self.access_token,
                resilience=self.resilience,
            )

            # Store refresh token if available
            if self.refresh_token:
                self.servicenow_client.refresh_token = self.refresh_token

            return await self._connect()

        except ConnectorInitError:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize connector: {e}", exc_info=True)
            return False

    async def _connect(self) -> bool:
        """The part of init after the client is built, the same for every auth type."""
        self.servicenow_datasource = ServiceNowDataSource(self.servicenow_client)

        # Raises ConnectorInitError with the reason
        await self.test_connection_and_access()

        self.kb_portal_suffix = await self._resolve_kb_portal_suffix()

        self.apply_article_read_criteria = await self._resolve_article_read_criteria()
        self.block_kb_without_criteria = await self._resolve_block_no_criteria()
        await self._notify_setup_warnings()

        self.logger.info("✅ ServiceNow KB Connector initialized successfully")
        return True

    async def _build_client(self, auth_config: dict) -> HTTPClient:
        """The client for basic auth, an API key or client credentials, from the fields saved on
        the connector (OAuth authorization code is built in init, from the linked OAuth app). A
        client-credentials client comes back holding a token.

        Raises:
            ConnectorInitError: a field is missing or not allowed, or ServiceNow refused the token request
        """
        try:
            self.instance_url = check_servicenow_instance_url(auth_config.get(AuthFieldKeys.INSTANCE_URL) or "")
        except ValueError as e:
            raise ConnectorInitError(str(e)) from e
        if self.auth_type == AuthType.BASIC_AUTH:
            username, password = auth_config.get("username"), auth_config.get("password")
            if not (username and password):
                raise ConnectorInitError(_MISSING_CREDENTIALS.format(what="a username and password"))
            return ServiceNowRESTClientViaUsernamePassword(
                self.instance_url, username, password, resilience=self.resilience
            )
        if self.auth_type == AuthType.API_TOKEN:
            api_key = auth_config.get(_API_KEY_FIELD)
            if not api_key:
                raise ConnectorInitError(_MISSING_CREDENTIALS.format(what="an API key"))
            return ServiceNowRESTClientViaAPIKey(self.instance_url, api_key, resilience=self.resilience)
        if self.auth_type == AuthType.CUSTOM:
            client_id, client_secret = auth_config.get(AuthFieldKeys.CLIENT_ID), auth_config.get(AuthFieldKeys.CLIENT_SECRET)
            if not (client_id and client_secret):
                raise ConnectorInitError(_MISSING_CREDENTIALS.format(what="a client ID and client secret"))
            client = ServiceNowRESTClientViaOAuthClientCredentials(
                self.instance_url, client_id, client_secret, resilience=self.resilience
            )
            host = urlsplit(self.instance_url).hostname
            try:
                token = await client.fetch_token()
            except BadRequestError as e:
                await client.close()
                raise ConnectorInitError(
                    f"ServiceNow at {host} refused the client-credentials token request ({e}). Check the client ID "
                    "and secret, that the OAuth application has an OAuth application user, and that the system "
                    "property glide.oauth.inbound.client.credential.grant_type.enabled is true."
                ) from e
            except Exception as e:  # a network error, a rate limit, a page that is not JSON (a hibernating instance)
                await client.close()
                raise ConnectorInitError(f"Could not get a token from ServiceNow at {host}: {e}") from e
            if not token:
                await client.close()
                raise ConnectorInitError(f"ServiceNow at {host} answered the token request without an access token.")
            return client
        raise ConnectorInitError(f"ServiceNow does not support the auth type {self.auth_type}.")

    def _sign_in_fix(self) -> str:
        """What the user does when ServiceNow refuses the connector's sign-in."""
        if self.auth_type == AuthType.OAUTH:
            return "Re-authorize the connector on its Authorize tab."
        return "Check the credentials on the connector's Authenticate tab."

    async def _get_fresh_datasource(self) -> ServiceNowDataSource:
        """A datasource on a client with a current credential. OAuth: the access token stored in the
        connector's config (the platform refreshes it), adopted when it changed. Client credentials:
        a new token when the held one is about to expire. Basic auth and API keys need nothing."""
        if not self.servicenow_client:
            raise connector_not_ready(self.display_name)
        if self.auth_type != AuthType.OAUTH:
            client = self.servicenow_client
            if isinstance(client, ServiceNowRESTClientViaOAuthClientCredentials) and client.expires_at is not None \
                    and client.expires_at - time.time() < _TOKEN_RENEW_MARGIN_S:
                await self._renew_client_credentials_token(client)
            return ServiceNowDataSource(client)

        connector_id = self.connector_id

        # Fetch current token from config (async I/O)
        config = await self.config_service.get_config(ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=connector_id))

        if not config:
            raise connector_not_ready(self.display_name)

        credentials = config.get(OAuthConfigKeys.CREDENTIALS) or {}
        fresh_token = credentials.get(OAuthConfigKeys.ACCESS_TOKEN)

        if not fresh_token:
            raise connector_not_ready(self.display_name)

        if self.servicenow_client.access_token != fresh_token:
            self.servicenow_client.set_access_token(fresh_token)
        # The refresh token a refresh after a 401 passes on (see _with_token_retry).
        self.refresh_token = credentials.get(OAuthConfigKeys.REFRESH_TOKEN) or self.refresh_token

        return ServiceNowDataSource(self.servicenow_client)

    async def _with_token_retry(self, call: Callable[[ServiceNowDataSource], Awaitable[T]]) -> T:
        """Run ``call`` on a fresh datasource. After a 401, get a working token once and run it
        again; a 401 that remains raises ServiceNowAuthError. The access token lives 1,800 s, so a
        long sync outlives the token it started with."""
        for attempt in range(2):
            datasource = await self._get_fresh_datasource()
            # Held before the call: refresh_now adopts a token rotated meanwhile instead of posting this one.
            held = (getattr(self.servicenow_client, "access_token", None), self.refresh_token)
            try:
                return await call(datasource)
            except ServiceNowAPIError as e:
                if e.status_code != HttpStatusCode.UNAUTHORIZED.value or isinstance(e, ServiceNowAuthError):
                    raise
                if attempt or not await self._refresh_after_unauthorized(*held):
                    raise ServiceNowAuthError(e.message, e.details) from e
        raise AssertionError("unreachable: the second attempt returns or raises")

    async def _refresh_after_unauthorized(self, held_access: str | None, held_refresh: str | None) -> bool:
        """After a 401: adopt an access token stored since the call, else refresh with the refresh
        token held before it. False when there is nothing to refresh with or the refresh failed."""
        client = self.servicenow_client
        if client is None:
            return False
        if self.auth_type != AuthType.OAUTH:
            # Basic and API key: the same credentials would fail again, and spend the
            # instance's failed-login lockout budget. Client credentials: mint a new token.
            if not isinstance(client, ServiceNowRESTClientViaOAuthClientCredentials):
                return False
            return await self._renew_client_credentials_token(client)
        try:
            config = await self.config_service.get_config(
                ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=self.connector_id), use_cache=False
            )
            stored = ((config or {}).get(OAuthConfigKeys.CREDENTIALS) or {}).get(OAuthConfigKeys.ACCESS_TOKEN)
            if stored and stored != held_access:
                client.set_access_token(stored)
                return True
            refresh_service = startup_service.get_token_refresh_service()
            if not held_refresh or not refresh_service:
                self.logger.error("ServiceNow returned 401 and there is no refresh token or refresh service")
                return False
            token = await refresh_service.refresh_now(self.connector_id, self.connector_name.value, held_refresh)
        except Exception as e:
            self.logger.error(f"ServiceNow token refresh after a 401 failed: {e}")
            return False
        if not token or not token.access_token:
            return False
        client.set_access_token(token.access_token)
        self.logger.info("Refreshed the ServiceNow access token after a 401")
        return True

    async def _renew_client_credentials_token(self, client: ServiceNowRESTClientViaOAuthClientCredentials) -> bool:
        """A new client-credentials token on ``client``; False when ServiceNow refused it. The token
        stays in memory only: nothing else uses it, and a new one is a request away."""
        try:
            renewed = bool(await client.fetch_token())
        except Exception as e:
            self.logger.error(f"Could not get a new ServiceNow client-credentials token: {e}")
            return False
        if renewed:
            self.logger.info("Got a new ServiceNow client-credentials token")
        return renewed

    async def _table_get(self, table: str, **params: str | None) -> TableAPIResponse:
        """The one place the connector reads the Table API, with the options every read uses."""
        return await self._with_token_retry(lambda datasource: datasource.get_now_table_tableName(
            tableName=table,
            **{
                "sysparm_display_value": ServiceNowQueryValues.DISPLAY_VALUE_FALSE,
                "sysparm_no_count": ServiceNowQueryValues.NO_COUNT_TRUE,
                "sysparm_exclude_reference_link": ServiceNowQueryValues.EXCLUDE_REFERENCE_LINK_TRUE,
                **params,
            },
        ))

    async def _iter_table(
        self, table: str, query: str | None, fields: list[str], page: int = ServiceNowDefaults.BATCH_SIZE,
        listing: _Listing | None = None, *, narrow: bool = False, after: tuple[str, str] | None = None,
    ) -> AsyncGenerator[list[TableAPIRecord], None]:
        """Every row of ``table`` that ``query`` matches (see _iter_rows). ``listing`` gets how many
        rows ServiceNow counted and how many it showed: fewer shown means an ACL hid some, so what
        the listing lacks may still be there. On a table in _ACCESS_TABLES, a 403, or a listing
        that shows none of the rows it counts, raises ServiceNowAccessError. ``narrow``: the caller
        reads some rows by a condition and handles a partial listing itself, so a few hidden rows
        do not stop the run; a 403 still does. ``after``: start past this (sys_updated_on, sys_id)."""
        listing = listing if listing is not None else _Listing()
        try:
            async for rows in self._iter_rows(table, query, fields, page, listing, after):
                yield rows
        except ServiceNowAPIError as e:
            if table in _ACCESS_TABLES and e.status_code == HttpStatusCode.FORBIDDEN.value \
                    and not isinstance(e, ServiceNowAccessError):
                self._sync_read_incomplete = True
                raise ServiceNowAccessError(table, e.message, e.details) from e
            raise
        if table in _ACCESS_TABLES and not narrow and listing.total and not listing.seen:
            self._sync_read_incomplete = True
            raise ServiceNowAccessError(table, f"ServiceNow counts {listing.total} rows but shows this account none")

    async def _iter_rows(
        self, table: str, query: str | None, fields: list[str], page: int, listing: _Listing,
        after: tuple[str, str] | None = None,
    ) -> AsyncGenerator[list[TableAPIRecord], None]:
        """Every row of ``table`` that ``query`` matches, a page at a time, oldest update first.

        Pages are keyset on (sys_updated_on, sys_id), not offsets, so rows that share a timestamp,
        or a row updated during the walk, are not skipped. ServiceNow removes the rows an ACL hides
        after it cuts a page, so a page can come back short, or empty in the middle of the list.
        So a page's size says nothing about the end: X-Total-Count does, which counts the rows of
        the current keyset query before ACLs. A page with rows moves the cursor to its last row; an
        empty one reads the same query at the next offset. The listing ends once ``offset + page``
        reaches the count. ``query`` is repeated in both halves of the keyset condition, so it must
        not hold ``^NQ`` or ``ORDERBY``. ``after``: the keyset to start past (a resumed listing).
        """
        assert "^NQ" not in (query or "") and "ORDERBY" not in (query or ""), "repeated in both keyset halves"
        names = CommonStrings.COMMA.join(dict.fromkeys([*fields, ServiceNowFields.SYS_UPDATED_ON, ServiceNowFields.SYS_ID]))
        scope = f"{query}^" if query else ""
        offset = 0
        counted = False
        while True:
            keyset = "" if after is None else (
                f"{ServiceNowFields.SYS_UPDATED_ON}>{after[0]}^NQ{scope}"
                f"{ServiceNowFields.SYS_UPDATED_ON}={after[0]}^{ServiceNowFields.SYS_ID}>{after[1]}^"
            )
            response = await self._table_get(
                table,
                sysparm_query=f"{scope}{keyset}{ServiceNowQueryValues.ORDER_BY_KEYSET}",
                sysparm_fields=names,
                sysparm_limit=str(page),
                sysparm_offset=str(offset),
                sysparm_no_count=None,  # the count is the end of the listing
            )
            total = response.total_count
            if total is None:
                raise ServiceNowAPIError(status_code=500, message=f"{table} listing returned no X-Total-Count")
            if not counted:  # the first call counts what this listing has to read
                listing.total, counted = total, True
            rows = response.result
            if rows:
                listing.seen += len(rows)
                yield rows
                last = (
                    str(rows[-1].get(ServiceNowFields.SYS_UPDATED_ON) or ""),
                    str(rows[-1].get(ServiceNowFields.SYS_ID) or ""),
                )
                if after is not None and last <= after:
                    # The instance did not apply the keyset: reading on would repeat or skip rows.
                    raise ServiceNowAPIError(status_code=500, message=f"{table} listing did not advance past {after}")
            if offset + page >= total:
                return
            if rows:
                after, offset = last, 0
            else:
                offset += page

    @staticmethod
    def _chunk_ids(ids: list[str]) -> list[list[str]]:
        """Split an ``IN`` list so the request URL stays under MAX_URL_LENGTH. A keyset query
        repeats the list and every comma is sent as %2C, so a chunk gets a quarter of the limit."""
        budget = ServiceNowDefaults.MAX_URL_LENGTH // 4
        chunks: list[list[str]] = []
        size = 0
        for item in ids:
            if not chunks or size + len(item) + 3 > budget:
                chunks.append([])
                size = 0
            chunks[-1].append(item)
            size += len(item) + 3
        return chunks

    async def _gone_at_source(self, table: str, query: str) -> bool:
        """Whether no row of ``table`` matches ``query``, hidden or not: X-Total-Count counts the
        rows before ACLs. For what a listing an ACL cut short did not show."""
        response = await self._table_get(
            table, sysparm_query=query, sysparm_fields=ServiceNowFields.SYS_ID, sysparm_limit="1", sysparm_no_count=None
        )
        if response.total_count is None:
            raise ServiceNowAPIError(status_code=500, message=f"{table} lookup returned no X-Total-Count")
        return response.total_count == 0

    async def _read_checkpoint(self, sync_point: SyncPoint, key: str) -> str | None:
        """The stored checkpoint, or None while a full sync runs. A full sync whose sync points
        could not be cleared would otherwise read only the delta, and its cleanup would then
        unlink everything that did not change."""
        return (await self._read_delta_point(sync_point, key))[0]

    async def _read_delta_point(self, sync_point: SyncPoint, key: str) -> tuple[str | None, set[str]]:
        """The stored checkpoint and the rows its delta's overlap reads again that were synced
        already (see _overlap_keys); (None, none) while a full sync runs (see _read_checkpoint)."""
        if self.data_entities_processor.full_sync_running(self.connector_id):
            return None, set()
        data = await sync_point.read_sync_point(key) or {}
        return data.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME), set(
            data.get(ServiceNowSyncPointKeys.SYNCED_IN_OVERLAP) or ())

    @staticmethod
    def _delta_since(checkpoint: str) -> str:
        """The UTC literal an incremental read starts from: DELTA_OVERLAP_SECONDS before ``checkpoint``."""
        stored = datetime.strptime(checkpoint, ServiceNowDefaults.DATETIME_FORMAT).replace(tzinfo=timezone.utc)
        start = stored - timedelta(seconds=ServiceNowDefaults.DELTA_OVERLAP_SECONDS)
        return start.strftime(ServiceNowDefaults.DATETIME_FORMAT)

    @staticmethod
    def _row_key(row: TableAPIRecord) -> str:
        """A row as it was when read: every change raises its sys_mod_count, so a second change
        within the second sys_updated_on keeps still gives another key. A key stored before the
        count was in it matches no row with a count, which is then synced once more."""
        key = f"{row.sys_updated_on}|{row.sys_id}"
        count = row.get(ServiceNowFields.SYS_MOD_COUNT)
        return f"{key}|{count}" if count else key

    @classmethod
    def _overlap_keys(cls, keys: Iterable[str], checkpoint: str) -> list[str]:
        """Of the keys of the rows synced, the ones the next delta from ``checkpoint`` reads again,
        to store beside it: that delta skips them, so a run with nothing new syncs nothing. The
        overlap stays for rows committed late into it, whose keys are not among them."""
        since = cls._delta_since(checkpoint)
        kept = sorted(k for k in keys if k.split("|", 1)[0] > since)
        # shortcut: past the cap a bulk change is synced again by the next run; raise it if that shows
        return kept if len(kept) <= ServiceNowDefaults.OVERLAP_KEYS_MAX else []

    def _current_article_query(self) -> str:
        """The article rows PipesHub indexes: the ones ServiceNow search shows (its knowledge
        search sources use this filter), within the sync filters. Every other row is not indexed,
        and a stored article whose number has no such row is deleted: retired, outdated, draft or
        review rows, ``active=false``, ``valid_to`` before today (UTC; empty never expires),
        articles in an inactive knowledge base, and articles the filters leave out. Each number
        has at most one such row. Change the rule here only: the full read, the version
        resolution, the reconcile and streaming all use it. The categories filter, and a
        knowledge base list too long for a URL, are checked per row instead (_article_in_scope)."""
        today = datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATE_FORMAT)
        query = (
            f"{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}^{ServiceNowFields.ACTIVE}=true"
            f"^{ServiceNowFields.KB_KNOWLEDGE_BASE}.{ServiceNowFields.ACTIVE}=true"
            f"^{ServiceNowFields.VALID_TO}ISEMPTY^OR{ServiceNowFields.VALID_TO}>={today}"
        )
        query += self._kb_filter_clause(ServiceNowFields.KB_KNOWLEDGE_BASE)
        for key, field in ((SyncFilterKey.MODIFIED, ServiceNowFields.SYS_UPDATED_ON),
                           (SyncFilterKey.CREATED, ServiceNowFields.SYS_CREATED_ON)):
            window = self.sync_filters.get(key)
            if window is None:
                continue
            for bound, operator in ((window.get_datetime_start(), ">="), (window.get_datetime_end(), "<=")):
                if bound is not None:
                    # Query literals are UTC
                    utc = datetime.fromtimestamp(bound / 1000, tz=timezone.utc)
                    query += f"^{field}{operator}{utc.strftime(ServiceNowDefaults.DATETIME_FORMAT)}"
        return query

    def _kb_filter_clause(self, field: str) -> str:
        """``^<field>IN<ids>`` (or ``NOT IN``) for the knowledge bases filter. Empty when the filter
        is unset, or when the list is too long for a URL: the keyset repeats the query, and
        _chunk_ids keeps a quarter of the URL for an IN list. _article_in_scope checks it anyway."""
        kbs = self.sync_filters.get(SyncFilterKey.KNOWLEDGE_BASES)
        if kbs is None or kbs.is_empty():
            return ""
        # Only sys_ids go into the query: other text could carry conditions of its own (^NQ...).
        values = [str(v) for v in kbs.as_list()]
        ids = [v for v in values if _SYS_ID.fullmatch(v)]
        if len(ids) < len(values):
            self.logger.warning(f"Ignoring {len(values) - len(ids)} knowledge base filter values that are not sys_ids")
        if not ids:
            return ""
        operator = "NOT IN" if kbs.operator_value == FilterOperator.NOT_IN else "IN"
        clause = f"^{field}{operator}{CommonStrings.COMMA.join(ids)}"
        return clause if len(clause) <= ServiceNowDefaults.MAX_URL_LENGTH // 4 else ""

    def _article_in_scope(self, row: TableAPIRecord) -> bool:
        """Whether an article row passes the knowledge bases and categories filters. Categories are
        checked here, not in the query: a category with its sub-categories can be a list too long
        for a URL. Not in keeps an article with no category, In drops it."""
        kb = row.get(ServiceNowFields.KB_KNOWLEDGE_BASE) or None
        if not name_passes_filter(self.sync_filters, SyncFilterKey.KNOWLEDGE_BASES.value, kb):
            return False
        if self._category_filter is None:
            return True
        keep_in, chosen = self._category_filter
        return ((row.get(ServiceNowFields.KB_CATEGORY) or None) in chosen) == keep_in

    async def _load_filters(self) -> None:
        """The admin's sync and indexing filters. load_connector_filters answers a failed config
        read with no filters, which would sync every knowledge base: this raises instead."""
        config = await self.config_service.get_config(
            ServiceNowConfigPaths.CONNECTOR_CONFIG.format(connector_id=self.connector_id), raise_on_error=True
        )
        filters = (config.get("filters") if isinstance(config, dict) else None) or {}
        self.sync_filters = FilterCollection.from_dict((filters.get("sync") or {}).get("values") or {}, self.logger)
        self.indexing_filters = FilterCollection.from_dict(
            (filters.get("indexing") or {}).get("values") or {}, self.logger
        )

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATETIME_FORMAT)

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that skipped an article or a knowledge base after an error did not see
        it, so it unlinks nothing."""
        return FullSyncCoverage(complete=not self._sync_read_incomplete)

    async def test_connection_and_access(self) -> bool:
        """Check that the integration user can sign in and read every table a sync reads.

        One row of each table in _REQUIRED_TABLES: a table it may not read fails the test, naming
        it, because a sync would stop there. A user that is not an admin passes with a warning (it
        syncs what its ACLs let it read; see _notify_setup_warnings).

        Raises:
            ConnectorInitError: why the test failed, for the user
        """
        host = urlsplit(self.instance_url or "").hostname or "the instance"
        self.logger.info("🔍 Testing the ServiceNow connection and the integration user's access...")
        try:
            # An address that never answers would otherwise take every retry's connect timeout.
            async with asyncio.timeout(ServiceNowDefaults.CONNECTION_TEST_TIMEOUT_SECONDS):
                me = await self._table_get(
                    ServiceNowTables.SYS_USER, sysparm_query=ServiceNowQueryValues.CURRENT_USER,
                    sysparm_fields=ServiceNowFields.SYS_ID, sysparm_limit="2",
                )
            missing = []
            for table, query in _REQUIRED_TABLES:
                try:
                    await self._table_get(
                        table, sysparm_query=query, sysparm_fields=ServiceNowFields.SYS_ID,
                        sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    )
                except ServiceNowAPIError as e:
                    if e.status_code != HttpStatusCode.FORBIDDEN.value:
                        raise
                    missing.append(table)
            if missing:
                raise ConnectorInitError(
                    f"The ServiceNow integration user cannot read these tables: {', '.join(missing)}. Give it "
                    "the admin role, or at least the knowledge_admin and user_admin roles, then try again."
                )
            rows = me.result or []
            if len(rows) == 1 and rows[0].sys_id:
                admin = await self._table_get(
                    ServiceNowTables.SYS_USER_HAS_ROLE,
                    sysparm_query=f"{ServiceNowFields.USER}={rows[0].sys_id}^{ServiceNowFields.ROLE_NAME}="
                                  f"{ServiceNowRoles.ADMIN}^{ServiceNowFields.STATE}={ServiceNowFields.ACTIVE}",
                    sysparm_fields=ServiceNowFields.SYS_ID, sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_no_count=None,
                )
                if not (admin.result or admin.total_count):
                    self.logger.warning(_NOT_ADMIN)
                    self._add_setup_warning(_NOT_ADMIN)
        except ConnectorInitError:
            raise
        except ServiceNowAuthError as e:
            raise ConnectorInitError(
                f"ServiceNow at {host} refused the connector's sign-in (HTTP 401: {e.message[:200]}). "
                + self._sign_in_fix()
            ) from e
        except ServiceNowAPIError as e:
            if e.message == REDIRECT_MESSAGE:
                raise ConnectorInitError(
                    f"ServiceNow at {host} redirected the request (HTTP {e.status_code}), so this URL is not the "
                    "instance's REST API. Enter the instance's own address, such as https://<instance>.service-now.com."
                ) from e
            if e.message == PARSE_ERROR_MESSAGE:
                raise ConnectorInitError(
                    f"ServiceNow at {host} did not answer with its REST API; the instance may be hibernating. "
                    "Wake it up, then try again."
                ) from e
            raise ConnectorInitError(
                f"ServiceNow at {host} answered HTTP {e.status_code}: {e.message[:200]}. "
                + ("Try again later." if e.status_code >= HttpStatusCode.INTERNAL_SERVER_ERROR.value
                   else "Check the instance URL.")
            ) from e
        except (ServiceNowRateLimited, AddressNotAllowedError) as e:
            raise ConnectorInitError(str(e)) from e
        except (httpx.HTTPError, TimeoutError) as e:
            raise ConnectorInitError(f"Could not reach ServiceNow at {host}. Check the instance URL.") from e
        self.logger.info("✅ ServiceNow connection test passed")
        return True

    @classmethod
    def auth_config_problems(cls, auth_config: dict) -> list[str]:
        """An instance URL in the settings about to be saved must pass check_servicenow_instance_url.
        The token and authorize URLs are checked against it in init: a save may still carry the
        registry's placeholder URLs, which the save drops."""
        url = (auth_config or {}).get(AuthFieldKeys.INSTANCE_URL)
        if not url:
            return []
        try:
            check_servicenow_instance_url(url)
        except ValueError as e:
            return [str(e)]
        return []

    def _add_setup_warning(self, warning: str) -> None:
        if warning not in self._setup_warnings:
            self._setup_warnings.append(warning)

    async def _notify_setup_warnings(self) -> None:
        """One warning naming what init found the integration user cannot read; repeats back off."""
        if not self._setup_warnings:
            return
        await self._notify(
            NotificationType.CONNECTOR_WARNING, NotificationSeverity.WARNING,
            "the integration user can read less than an admin",
            "\n".join(f"- {w}" for w in self._setup_warnings)
            + "\nFor a complete sync, give the integration user the admin role.",
        )

    async def _notify(
        self, type: NotificationType, severity: NotificationSeverity, event: str, message: str,  # noqa: A002
        *, suppress_repeats: bool = True,
    ) -> None:
        """A notice about this connector instance, to its creator and the org admins. The platform
        backs off repeats of the same notice. Never raises: it reports a failure, it must not hide one."""
        try:
            outcome = await self.notify_and_wait(
                type=type, severity=severity, title=self._notification_title(event), message=message,
                recipient_user_ids=[self.created_by] if self.created_by else None,
                recipient_roles=[NotificationRecipientRole.ADMIN],
                suppress_repeats=suppress_repeats,
            )
            if outcome == NotificationOutcome.FAILED:
                self.logger.warning(f"The notification \"{event}\" was not sent")
        except Exception as e:
            self.logger.error(f"Could not send the notification \"{event}\": {e}")

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector."""
        email, source_user_id = None, None
        try:
            response = await self._table_get(
                ServiceNowTables.SYS_USER,
                sysparm_query=ServiceNowQueryValues.CURRENT_USER,
                sysparm_fields=CommonStrings.COMMA.join([ServiceNowFields.SYS_ID, ServiceNowFields.EMAIL]),
                sysparm_limit="2",
            )
            rows = response.result or []
            # An instance that will not evaluate the script can drop the condition and list
            # other users, so anything but exactly one row could link the wrong account
            if len(rows) == 1:
                email = rows[0].get(ServiceNowFields.EMAIL)
                source_user_id = rows[0].get(ServiceNowFields.SYS_ID)
        except Exception as e:
            self.logger.debug("Could not read the authenticated ServiceNow user: %s", e)
            return
        await self.register_authenticated_source_user(
            email.strip() if isinstance(email, str) else None, source_user_id
        )

    async def run_sync(self) -> None:
        """One sync (see _run_sync). A stop is reported to the connector's creator and the org
        admins, then raised; a run that finished with problems sends one summary of them."""
        self._problems, self._skipped, self._persistent = [], defaultdict(int), set()
        self._kbs_unread, self._criteria_noted, self._no_reader = [], defaultdict(set), 0
        try:
            await self._run_sync()
        except ServiceNowAuthError as e:
            oauth = self.auth_type == AuthType.OAUTH
            self.last_action_items = [ActionItem(
                code="servicenow_reauthorize", severity="error",
                title="Re-authorize ServiceNow" if oauth else "Check the ServiceNow credentials",
                message="ServiceNow refused the connector's sign-in. "
                        + ("Sign in again on the Authorize tab." if oauth else "Update them on the Authenticate tab."),
                action="authenticate",
            )]
            retried = ", also after a token refresh" if oauth else (
                ", also with a new token" if self.auth_type == AuthType.CUSTOM else "")
            await self._notify(
                NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR, "needs to be re-authorized",
                f"ServiceNow refused the connector's sign-in (HTTP 401: {e.message[:200]}){retried}. The sync "
                f"stopped; what it did not read stays as it was. {self._sign_in_fix()}",
            )
            raise
        except ServiceNowAccessError as e:
            await self._notify(
                NotificationType.CONNECTOR_NOT_ACCESSIBLE, NotificationSeverity.ERROR, f"cannot read {e.table}",
                f"The ServiceNow integration user cannot read the table {e.table} ({e.message[:200]}), which "
                "decides who may read knowledge articles. The sync stopped; what it did not read stays as it "
                "was. Give the user a role that reads it (admin, or knowledge_admin and user_admin), then sync again.",
            )
            raise
        except ServiceNowRateLimited as e:
            until = rate_limit_reset_label(e.reset_at)
            # The platform clears every sync point before a full sync, the resume point too.
            again = "runs the full sync again" if self.data_entities_processor.full_sync_running(
                self.connector_id) else "continues where this one stopped"
            # The reset time is in the text, so the platform's back-off sends this once per reset.
            await self._notify(
                NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR,
                "sync paused, ServiceNow rate limit reached",
                f"A ServiceNow rate limit rule is used up until {until} UTC, so the sync stopped. Nothing "
                f"it did not read was removed; the next sync after that time {again}.",
            )
            raise
        except Exception as e:
            await self._notify(
                NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.ERROR, "sync failed",
                f"The sync stopped: {redact_urls_in_text(str(e))[:200]}. Recent ServiceNow changes may not be in "
                "search yet; the sync is retried on its next run.",
            )
            raise
        self.last_action_items = []
        await self._report_sync_problems()

    # Per connector, across instances: the persistent conditions last reported.
    # shortcut: kept in memory per process, so a restart re-sends persistent lines once; move to the sync point if that matters
    _persistent_noticed: dict[str, set[str]] = {}

    # Most lines one notice lists; the rest are counted.
    _MAX_PROBLEMS_SHOWN = 10

    def _note_problem(self, problem: str, *, persistent: bool = False) -> None:
        """Note something this run could not do; all of it is reported once when the run ends.
        ``persistent``: a condition of the instance's set-up (users with no email, articles nobody
        can read), shown when it first appears and again only once it changed or came back."""
        if problem not in self._problems:
            self._problems.append(problem)
        if persistent:
            self._persistent.add(problem)

    def _note_skipped(self, what: str) -> None:
        """Count one item this run skipped, under the line that reports it ("{n} could not ...")."""
        self._skipped[what] += 1

    def _note_criteria(self, kind: str, sys_id: str) -> None:
        """A user criterion this run could not evaluate in full, by why (see _report_sync_problems)."""
        self._criteria_noted[kind].add(sys_id)

    async def _report_sync_problems(self) -> None:
        """One warning listing what a finished run could not do; nothing for a clean run. Never
        names an article, a user or a secret: counts, knowledge base titles and table names."""
        problems = list(self._problems)
        if self._kbs_unread:
            titles = sorted(set(self._kbs_unread))
            more = len(titles) - self._MAX_PROBLEMS_SHOWN
            problems.append(
                f"{_plural(len(titles), 'knowledge base kept its', 'knowledge bases kept their')} stored access "
                f"because {'its' if len(titles) == 1 else 'their'} user criteria could not be read: "
                f"{', '.join(titles[:self._MAX_PROBLEMS_SHOWN])}"
                + (f" and {more} more" if more > 0 else "")
            )
        problems += [line.format(n=count) for line, count in self._skipped.items()]
        if self._no_reader:
            problem = (f"{_plural(self._no_reader, 'article has', 'articles have')} no reader in PipesHub: "
                       f"{'its' if self._no_reader == 1 else 'their'} user criteria match nobody PipesHub can "
                       "evaluate (for example, a scripted criterion)")
            problems.append(problem)
            self._persistent.add(problem)
        for kind, one, many in (
            ("scripted",
             "uses a script, which PipesHub does not run: it grants nobody here, and as Cannot Read or Cannot "
             "Contribute it denies everyone it may cover",
             "use a script, which PipesHub does not run: they grant nobody here, and as Cannot Read or Cannot "
             "Contribute they deny everyone they may cover"),
            ("hidden",
             "is hidden from the integration user: it grants nobody here, and as Cannot Read or Cannot Contribute "
             "it denies everyone",
             "are hidden from the integration user: they grant nobody here, and as Cannot Read or Cannot "
             "Contribute they deny everyone"),
            ("partial",
             "names groups or users partly hidden from the integration user: as Cannot Read or Cannot Contribute "
             "it denies everyone it may cover",
             "name groups or users partly hidden from the integration user: as Cannot Read or Cannot Contribute "
             "they deny everyone they may cover"),
        ):
            if count := len(self._criteria_noted.get(kind) or ()):
                problem = f"{_plural(count, 'user criterion', 'user criteria')} {one if count == 1 else many}"
                problems.append(problem)
                self._persistent.add(problem)
        # Shown when it first appears, and again once it has changed, or gone and come back.
        noticed = ServiceNowConnector._persistent_noticed.get(self.connector_id, set())
        ServiceNowConnector._persistent_noticed[self.connector_id] = set(self._persistent)
        problems = [p for p in problems if p not in self._persistent or p not in noticed]
        if not problems:
            return
        shown = problems[: self._MAX_PROBLEMS_SHOWN]
        lines = "\n".join(f"- {p}" for p in shown)
        if len(problems) > len(shown):
            lines += f"\n- and {len(problems) - len(shown)} more (see the connector logs)"
        self.logger.warning(f"ServiceNow sync finished with problems:\n{lines}")
        await self._notify(
            NotificationType.CONNECTOR_SYNC_ERROR, NotificationSeverity.WARNING, "sync finished with problems",
            f"The sync finished, but:\n{lines}",
        )

    async def on_background_init_failed(self, error: Optional[Exception]) -> None:
        """A sync could not start: tell the creator and the org admins why (repeats back off)."""
        reason = str(error)[:300] if isinstance(error, ConnectorInitError) else (
            "the connector could not be set up; the connector's log has the details."
        )
        await self._notify(
            NotificationType.CONNECTOR_AUTH_ERROR, NotificationSeverity.ERROR, "could not start",
            f"PipesHub could not start the ServiceNow connector. {reason} Syncs fail until this is fixed.",
        )

    async def _run_sync(self) -> None:
        """
        Sync ServiceNow knowledge data. Full and incremental runs both come here: users,
        organizational entities, categories and articles read from their checkpoints (none
        during a full sync); groups, roles and knowledge bases are always read in full.

        Sync order:
        1. Users and Groups (global)
        2. Get admin users from ServiceNow
        3. Knowledge Bases (with admin permissions)
        4. Categories
        5. KB Articles and their attachments
        """
        try:
            self._app_users = None  # user listing is read once per run, after the user sync
            self._email_by_source_id = None
            # A knowledge base with no user criteria may be open to every user (see _kb_criteria).
            self._criteria_rows = {_EVERYONE: TableAPIRecord(sys_id=_EVERYONE, active="true")}
            self._kb_criteria_cache, self._kb_contributor_cache, self._criteria_users = {}, {}, {}
            self._kb_mtom, self._has_role_users_cache, self._guest_cache = None, None, None
            self._groups, self._group_members = {}, {}
            self._held, self._role_names = {}, None
            self._admin_source_ids = None
            self._kb_owner_cache = {}
            self._sync_read_incomplete = False
            self._live_kb_ids, self._kbs_back_in_scope, self._category_ids = None, set(), None
            self._scopes_to_store, self._categories_gone = {}, False
            self._category_filter = None
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting ServiceNow KB sync for org: {org_id}")

            # Ensure client is initialized
            if not self.servicenow_client:
                raise Exception("ServiceNow client not initialized. Call init() first.")

            # Before anything is written: a run with the wrong scope would write, then remove, the wrong things.
            await self._load_filters()
            await self._register_authenticated_identity()

            access = await self._read_access_state()
            # No table behind access changed since the last pass, and none is due: memberships,
            # roles, organizational members and knowledge base grants are as stored.
            quiet = not access[1]

            # Step 1: Sync users and groups globally
            self.logger.info("Step 1/5: Syncing users and groups...")
            if quiet:
                self.logger.info("Access is unchanged: reading user and organizational entity changes only")
                await self._sync_organizational_entities()
                await self._sync_users()
                self._groups = {g.sys_id: g for g in await self._fetch_all_groups()}
            else:
                await self._sync_users_and_groups()

            # Step 2: Get admin users from ServiceNow
            self.logger.info("Step 2/5: Fetching admin users from ServiceNow...")
            # The article grants need the same set (_kb_privileged_users): read once per run. On a
            # quiet run the role assignments did not change, so the admins of the last listing hold.
            stored = (await self.user_sync_point.read_sync_point(ServiceNowSyncPointKeys.ADMINS) or {}) if quiet else {}
            self._admin_source_ids = set(stored[ServiceNowDictKeys.GRANTED]) if ServiceNowDictKeys.GRANTED in stored \
                else await self._get_admin_users()
            if not self._admin_source_ids:
                self.logger.warning("No admin users found, proceeding without explicit admin permissions")
            self.logger.info(f"✅ Found {len(self._admin_source_ids)} admin users")

            # Step 3: Knowledge Bases
            self.logger.info("Step 3/5: Syncing Knowledge Bases...")
            await self._sync_knowledge_bases(self._admin_source_ids, grants=not quiet)

            # Step 4: Categories
            self.logger.info("Step 4/5: Syncing Categories...")
            await self._sync_categories()

            # Step 5: Articles & Attachments
            self.logger.info("Step 5/5: Syncing Articles & Attachments...")
            await self._sync_articles(access)

            self.logger.info("🎉 ServiceNow KB sync completed successfully")

        except (ServiceNowRateLimited, ServiceNowAuthError, ServiceNowAccessError) as e:
            # Expected stops, reported to the user (see run_sync): no traceback.
            self.logger.error(f"❌ Sync stopped: {e}")
            raise
        except Exception as e:
            self.logger.error(f"❌ Error during sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Runs the same sync as run_sync: each step reads from its checkpoint (sys_updated_on)."""
        await self.run_sync()

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content from ServiceNow.

        For articles (WebpageRecord): the article's blocks (see _fetch_article_content)
        For attachments (FileRecord): the file, streamed from the attachment API

        Args:
            record: The record to stream (article or attachment)

        Returns:
            StreamingResponse: the article's BlocksContainer, or the file
        """
        # A stub for a parent that never synced: there is nothing behind it to serve.
        if getattr(record, "is_placeholder", False) is True:
            raise not_found_at_source(self.display_name)
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type == RecordType.WEBPAGE:
                content = await self._fetch_article_content(
                    record.external_record_id, weburl=getattr(record, "weburl", None)
                )
                return StreamingResponse(iter([content]), media_type=MimeTypes.BLOCKS.value)

            elif record.record_type == RecordType.FILE:
                chunks = await self._open_attachment(
                    await self._current_attachment(record), is_html=record.mime_type == MimeTypes.HTML.value
                )
                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    chunks,
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise _stream_error(e, self.display_name) from e

    async def _read_instance_property(self, name: str) -> Optional[str]:
        """Read one sys_properties value. None means the property is not set.

        A read that fails raises, because "not set" and "could not tell" are
        different answers and only the caller knows which way to lean on the
        second one.
        """
        response = await self._table_get(
            ServiceNowTables.SYS_PROPERTIES,
            sysparm_query=f"{ServiceNowFields.NAME}={name}",
            sysparm_fields=ServiceNowFields.VALUE,
            sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
            sysparm_no_count=None,
        )
        if response.result:
            return (response.result[0].get(ServiceNowFields.VALUE) or "").strip()
        if response.total_count:
            # Counted before ACLs: the property is set, and hidden from this account.
            raise ServiceNowAPIError(status_code=HttpStatusCode.FORBIDDEN.value, message=f"{name} is hidden from this account")
        return None

    async def _resolve_article_read_criteria(self) -> bool:
        """Whether contributors are held to an article's own Can Read and Cannot Read.

        An article's own criteria always narrow its knowledge base for readers. When
        glide.knowman.apply_article_read_criteria is true they bind the knowledge base's
        contributors too; otherwise contributors read every article of it. The property is unset
        on a default instance, so unset means false.

        A property that cannot be read leans to true, which gives contributors less.
        """
        try:
            value = await self._read_instance_property(
                ServiceNowDefaults.ARTICLE_READ_CRITERIA_PROPERTY
            )
        except Exception as e:
            self.logger.warning(
                "Could not read "
                f"{ServiceNowDefaults.ARTICLE_READ_CRITERIA_PROPERTY} ({e}); "
                "applying article read criteria as an override until it can be read"
            )
            if not isinstance(e, (ServiceNowRateLimited, ServiceNowAuthError)):  # not the account's access
                self._add_setup_warning(_PROPERTIES_HIDDEN)
            return True

        applies = (value or "").strip().lower() == "true"
        self.logger.info(f"Article read criteria override the knowledge base: {applies}")
        return applies

    async def _resolve_block_no_criteria(self) -> bool:
        """Whether a knowledge base with no user criteria is closed to everyone but its owner,
        managers and knowledge admins (glide.knowman.block_access_with_no_user_criteria). Only an
        explicit false opens one, to every user; unset or unreadable keeps it closed, which gives
        less. (ServiceNow documents false as the default; only true was seen on an instance.)"""
        try:
            value = await self._read_instance_property(ServiceNowDefaults.BLOCK_NO_CRITERIA_PROPERTY)
        except Exception as e:
            self.logger.warning(
                f"Could not read {ServiceNowDefaults.BLOCK_NO_CRITERIA_PROPERTY} ({e}); "
                "knowledge bases with no user criteria stay closed"
            )
            if not isinstance(e, (ServiceNowRateLimited, ServiceNowAuthError)):  # not the account's access
                self._add_setup_warning(_PROPERTIES_HIDDEN)
            return True
        return (value or "").strip().lower() != "false"

    async def _resolve_kb_portal_suffix(self) -> str:
        """Find the Service Portal that serves the knowledge pages.

        The article pages (kb_article_view, kb_category, kb_home) belong to a
        portal, and each instance chooses its own. The Knowledge Management
        portal plugin records that choice in a system property. When the
        property is empty the default portal serves the pages.

        Returns:
            str: The portal URL suffix, for example "kb" or "esc".
        """
        try:
            suffix = await self._read_instance_property(ServiceNowDefaults.KB_PORTAL_PROPERTY)
        except Exception as e:
            # A link that points at the wrong portal is wrong on screen only, so
            # this one falls back rather than leaning any particular way.
            self.logger.warning(f"Could not read the knowledge portal property: {e}")
            suffix = None
        if suffix:
            self.logger.info(f"Knowledge portal from the instance property: /{suffix}")
            return suffix

        try:
            response = await self._table_get(
                ServiceNowTables.SP_PORTAL,
                sysparm_query=f"{ServiceNowFields.DEFAULT}=true",
                sysparm_fields=ServiceNowFields.URL_SUFFIX,
                sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                sysparm_no_count=None,
            )
            suffix = (response.result[0].get(ServiceNowFields.URL_SUFFIX) or "").strip() if response.result else ""
            if suffix:
                self.logger.info(f"Knowledge portal from the default portal: /{suffix}")
                return suffix
            if not response.result and response.total_count:
                self._add_setup_warning(_PORTAL_HIDDEN)
        except Exception as e:
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not read the default portal: {e}")
            self._add_setup_warning(_PORTAL_HIDDEN)

        self.logger.info(
            f"Knowledge portal falls back to /{ServiceNowDefaults.KB_PORTAL_SUFFIX}"
        )
        return ServiceNowDefaults.KB_PORTAL_SUFFIX

    async def _fetch_article_content(self, article_sys_id: str, weburl: str | None = None) -> bytes:
        """
        The current version (see _current_article_query) of an article as a BlocksContainer:
        its body headed by its title and number, then one COMMENT group per feedback comment
        (a reply under the comment it answers) and a summary of the helpful votes and ratings.

        Feedback is read on every version of the article (it stays on the version it was given
        to). Every reader of an article reads all of its feedback, so it carries the article's
        access. Flagged feedback is left out, as ServiceNow shows it only to knowledge admins and
        managers, and so are its reason and work notes.

        Args:
            article_sys_id: The record's key: the article number, or a version's sys_id for a
                record stored before articles were keyed by number
            weburl: The article's page, for the blocks

        Raises:
            HTTPException: not found when the article has no current version (retired, expired, ...)
        """
        key_field = ServiceNowFields.SYS_ID if _LEGACY_ARTICLE_KEY.match(article_sys_id) else ServiceNowFields.NUMBER
        try:
            try:
                response = await self._table_get(
                    ServiceNowTables.KB_KNOWLEDGE,
                    sysparm_query=f"{key_field}={article_sys_id}^{self._current_article_query()}",
                    sysparm_fields=CommonStrings.COMMA.join([
                        ServiceNowFields.SYS_ID,
                        ServiceNowFields.NUMBER,
                        ServiceNowFields.SHORT_DESCRIPTION,
                        ServiceNowFields.TEXT,
                        ServiceNowFields.WIKI,
                        ServiceNowFields.ARTICLE_TYPE,
                    ]),
                    sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                )
            except ServiceNowAPIError as e:
                self.logger.error(f"Failed to fetch article {article_sys_id}: {e.message}")
                raise map_source_status(e.status_code, connector=self.display_name) from e
            if not response.result:
                raise not_found_at_source(self.display_name)
            article = response.result[0]
            number = article.get(ServiceNowFields.NUMBER) or article_sys_id
            title = article.get(ServiceNowFields.SHORT_DESCRIPTION) or number

            body = self._article_body(article)
            if article.get(ServiceNowFields.ARTICLE_TYPE) == ServiceNowFields.WIKI and article.get(ServiceNowFields.WIKI):
                # The wiki field's display value is the markup rendered as HTML.
                rendered = await self._table_get(
                    ServiceNowTables.KB_KNOWLEDGE, sysparm_query=f"{ServiceNowFields.SYS_ID}={article.sys_id}",
                    sysparm_fields=ServiceNowFields.WIKI, sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_display_value=ServiceNowQueryValues.DISPLAY_VALUE_TRUE,
                )
                body = (rendered.result[0].get(ServiceNowFields.WIKI) if rendered.result else None) or body
            if body:
                body = await html_to_markdown_async(await self._inline_images_as_data_uris(body, article.sys_id))
            groups = [BlockGroup(
                id=str(uuid.uuid4()), index=0, name=title,
                type=GroupType.TEXT_SECTION, sub_type=GroupSubType.CONTENT, description="Article body",
                source_group_id=number, data=f"# {title}\n\n{number}\n\n{body}".rstrip(),
                format=DataFormat.MARKDOWN, weburl=weburl, requires_processing=True,
            )]

            feedback = sorted(
                (row for row in (await self._feedback([number], _FEEDBACK_FIELDS)).get(number, [])
                 if str(row.get(ServiceNowFields.FLAGGED)).lower() != "true"),
                key=lambda row: (row.get(ServiceNowFields.SYS_CREATED_ON) or "", row.sys_id),
            )
            group_of: dict[str, int] = {}
            for row in feedback:
                comment = (row.get(ServiceNowFields.COMMENTS) or "").strip()
                if not comment:
                    continue
                parent = group_of.get(row.get(ServiceNowFields.PARENT_COMMENT)) \
                    or group_of.get(row.get(ServiceNowFields.ROOT_FEEDBACK)) or 0
                group_of[row.sys_id] = len(groups)
                groups.append(BlockGroup(
                    id=str(uuid.uuid4()), index=len(groups), parent_index=parent,
                    name=f"Comment by {row.get(ServiceNowFields.USER_DISPLAY_NAME) or 'a reader'}",
                    type=GroupType.TEXT_SECTION, sub_type=GroupSubType.COMMENT, description="Article feedback",
                    source_group_id=row.sys_id, data=comment, format=DataFormat.MARKDOWN,
                    weburl=weburl, requires_processing=True,
                ))
            votes = [str(row.get(ServiceNowFields.USEFUL) or "").lower() for row in feedback]
            ratings = [int(r) for r in (str(row.get(ServiceNowFields.RATING) or "") for row in feedback)
                       if r.isdigit() and int(r) > 0]
            summary = []
            if votes.count("yes") or votes.count("no"):
                summary.append(f"{votes.count('yes')} of {votes.count('yes') + votes.count('no')} readers "
                               "found this article helpful.")
            if ratings:
                summary.append(f"Rated {sum(ratings) / len(ratings):.1f} of 5 by {len(ratings)} readers.")
            if summary:
                groups.append(BlockGroup(
                    id=str(uuid.uuid4()), index=len(groups), parent_index=0, name="Feedback summary",
                    type=GroupType.TEXT_SECTION, sub_type=GroupSubType.CONTENT, description="Article feedback",
                    source_group_id=f"{number}_feedback", data=" ".join(summary), format=DataFormat.MARKDOWN,
                    weburl=weburl, requires_processing=True,
                ))
            for group in groups:
                children = [g.index for g in groups if g.parent_index == group.index]
                if children:
                    group.children = BlockGroupChildren.from_indices(block_group_indices=children)
            return BlocksContainer(blocks=[], block_groups=groups).model_dump_json().encode("utf-8")

        except HTTPException:
            raise  # Re-raise HTTP exceptions
        except Exception as e:
            self.logger.error(f"Failed to fetch article content: {e}", exc_info=True)
            raise _stream_error(e, self.display_name) from e

    @staticmethod
    def _article_body(article: TableAPIRecord) -> str:
        """The article's body: HTML in ``text``, or wiki markup in ``wiki`` for a wiki article."""
        wiki = getattr(article, ServiceNowFields.ARTICLE_TYPE, None) == ServiceNowFields.WIKI
        return (getattr(article, ServiceNowFields.WIKI, None) if wiki else None) \
            or getattr(article, ServiceNowFields.TEXT, None) or ""

    @staticmethod
    def _content_revision(*parts: object) -> str:
        """A hash of what a record streams. The title and place are left out: a rename or a move
        updates the record without indexing it again."""
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    async def _feedback(
        self, numbers: list[str] | None, fields: list[str], listing: _Listing | None = None,
    ) -> dict[str, list[TableAPIRecord]]:
        """The kb_feedback rows on every version of the articles ``numbers``, by article number;
        every row when ``numbers`` is None (one listing, for a full read), whose counts ``listing``
        gets.

        Raises:
            ServiceNowAPIError: a failed listing is not "no feedback"
        """
        rows: dict[str, list[TableAPIRecord]] = defaultdict(list)
        queries = [None] if numbers is None else [
            f"{ServiceNowFields.ARTICLE_NUMBER}IN{CommonStrings.COMMA.join(chunk)}" for chunk in self._chunk_ids(numbers)
        ]
        for query in queries:
            async for page in self._iter_table(
                ServiceNowTables.KB_FEEDBACK, query, [ServiceNowFields.ARTICLE_NUMBER, *fields],
                page=ServiceNowDefaults.ID_PAGE_SIZE if numbers is None else ServiceNowDefaults.BATCH_SIZE,
                listing=listing if numbers is None else None,
            ):
                for row in page:
                    rows[row.get(ServiceNowFields.ARTICLE_NUMBER)].append(row)
        return rows

    def _instance_image_id(self, src: str) -> str | None:
        """The sys_attachment sys_id an <img> src names on this instance, or None."""
        url = html.unescape(src)
        if self.instance_url and url.startswith(self.instance_url):
            url = url[len(self.instance_url):]
        match = _INSTANCE_IMAGE.match(url)
        return match.group(1) if match else None

    async def _inline_images_as_data_uris(self, body: str, article_sys_id: str) -> str:
        """The article HTML with each image stored on this instance embedded as a data: URI, so
        the indexer, which cannot sign in to ServiceNow, sees it.

        Only this article version's own images are embedded (its inline images and its
        attachments): any other attachment on the instance stays a link, as its readers are not
        the article's. So does an image on another host, one larger than MAX_INLINE_IMAGE_BYTES,
        one that cannot be read, and every image past MAX_INLINE_IMAGES or
        MAX_INLINE_IMAGES_TOTAL_BYTES for the article."""
        ids = {src: image_id for m in _IMG_SRC.finditer(body) if (image_id := self._instance_image_id(src := m.group(2)))}
        if not ids:
            return body
        own: dict[str, int] = {}  # sys_id -> size_bytes
        async for rows in self._iter_table(
            ServiceNowTables.SYS_ATTACHMENT,
            f"{ServiceNowFields.TABLE_SYS_ID}={article_sys_id}^{ServiceNowFields.TABLE_NAME}IN"
            f"{ServiceNowTables.INLINE_IMAGE},{ServiceNowTables.KB_KNOWLEDGE}",
            [ServiceNowFields.SYS_ID, ServiceNowFields.SIZE_BYTES], page=ServiceNowDefaults.ID_PAGE_SIZE,
        ):
            for row in rows:
                size = str(row.get(ServiceNowFields.SIZE_BYTES) or "")
                own[row.sys_id] = int(size) if size.isdigit() else 0
        by_id: dict[str, str] = {}  # sys_id -> data: URI, the embedded ones
        budget = ServiceNowDefaults.MAX_INLINE_IMAGES_TOTAL_BYTES
        for image_id in dict.fromkeys(ids.values()):
            limit = min(budget, ServiceNowDefaults.MAX_INLINE_IMAGE_BYTES)
            size = own.get(image_id)
            if size is None or size > limit or len(by_id) >= ServiceNowDefaults.MAX_INLINE_IMAGES:
                continue
            embedded = await self._image_data_uri(image_id, limit)
            if embedded:
                by_id[image_id], read = embedded
                budget -= read  # the bytes read, whatever size_bytes said
        return _IMG_SRC.sub(
            lambda m: f"{m.group(1)}{by_id.get(ids.get(m.group(2), '')) or m.group(2)}{m.group(3)}", body
        )

    async def _image_data_uri(self, image_id: str, max_bytes: int) -> tuple[str, int] | None:
        """The image ``image_id`` as a data: URI and its size, or None when it is not an image,
        cannot be read or is larger than ``max_bytes``."""
        try:
            response = await self._with_token_retry(lambda datasource: datasource.stream_attachment(image_id))
            data = bytearray()
            try:
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if len(data) > max_bytes:
                        self.logger.warning(f"Inline image {image_id} is too large to embed; it stays a link")
                        return None
            finally:
                await response.aclose()
        except Exception as e:
            # A rate limit or a lost sign-in fails the stream, so indexing tries again later.
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not read inline image {image_id}; it stays a link: {e}")
            return None
        mime = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if not mime.startswith("image/"):
            return None
        return f"data:{mime};base64,{base64.b64encode(bytes(data)).decode('ascii')}", len(data)

    async def _current_attachment(self, record: Record) -> str:
        """The sys_id of the file behind an attachment record on its article's current version
        (see _current_article_query). The article must still be current, so a retired or expired
        article's files are not served to its old readers until the next sync removes them.

        Raises:
            HTTPException: not found when the article has no current version or that version
                no longer has the file
        """
        article_key = record.parent_external_record_id or ""
        key_field = ServiceNowFields.SYS_ID if _LEGACY_ARTICLE_KEY.match(article_key) else ServiceNowFields.NUMBER
        try:
            response = await self._table_get(
                ServiceNowTables.KB_KNOWLEDGE,
                sysparm_query=f"{key_field}={article_key}^{self._current_article_query()}",
                sysparm_fields=f"{ServiceNowFields.SYS_ID},{ServiceNowFields.NUMBER}",
                sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
            )
            article = response.result[0] if article_key and response.result else None
            rows = (await self._fetch_attachments_for_articles([article.sys_id])).get(article.sys_id, []) \
                if article else []
        except ServiceNowAPIError as e:
            raise map_source_status(e.status_code, connector=self.display_name) from e
        for key, row in self._attachment_keys(article.number if article else "", rows):
            # A record stored before attachments had their key is under the file's sys_id.
            if record.external_record_id in (key, row.sys_id):
                return row.sys_id
        raise not_found_at_source(self.display_name)

    async def _open_attachment(self, attachment_sys_id: str, *, is_html: bool = False) -> AsyncGenerator[bytes, None]:
        """
        An attachment's file, in chunks: GET /api/now/attachment/{sys_id}/file.

        The download is opened before this returns, so a failure is raised (mapped) before any
        response goes out. An empty file streams empty.

        Args:
            attachment_sys_id: The sys_id of the attachment
            is_html: Whether the file is HTML. Otherwise an HTML answer is not the file but a page
                of the instance's own (a hibernating instance answers every URL with one).

        Raises:
            HTTPException: the mapped ServiceNow failure
        """
        try:
            response = await self._with_token_retry(
                lambda datasource: datasource.stream_attachment(attachment_sys_id)
            )
        except ServiceNowAPIError as e:
            self.logger.error(f"ServiceNow API error downloading attachment: {e.message}")
            raise map_source_status(e.status_code, connector=self.display_name) from e
        content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
        if content_type == MimeTypes.HTML.value and not is_html:
            await response.aclose()
            self.logger.error(f"ServiceNow answered attachment {attachment_sys_id} with an HTML page, not the file")
            raise map_source_status(HttpStatusCode.SERVICE_UNAVAILABLE.value, connector=self.display_name)

        async def chunks() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in response.aiter_bytes(64 * 1024):
                    yield chunk
            finally:
                await response.aclose()

        return chunks()

    def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for record access.

        ServiceNow doesn't support pre-signed URLs in the traditional sense,
        so this returns None. Access is controlled through the stream_record method.

        Args:
            record: The record to get URL for

        Returns:
            Optional[str]: None for ServiceNow
        """
        return None

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """ServiceNow sends no webhooks to this connector; changes are read by the scheduled sync."""
        return

    async def cleanup(self) -> None:
        """
        Clean up resources used by the connector.

        This is called when the connector is being shut down.
        """
        try:
            self.logger.info("🧹 Cleaning up ServiceNow KB Connector...")
            await self._close_client()

            self.logger.info("✅ Cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}", exc_info=True)

    async def _close_client(self) -> None:
        """Close the current client's connection pool; a re-init or shutdown would leak it otherwise."""
        client, self.servicenow_client, self.servicenow_datasource = self.servicenow_client, None, None
        if client is not None:
            await client.close()

    async def reindex_records(self, record_results: List[Record]) -> None:
        # Index events only: on_new_records would re-run permission handling.
        if not record_results:
            return
        await self.data_entities_processor.reindex_existing_records(record_results)

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """The active knowledge bases, or the categories under them labelled with their knowledge
        base and parents ("IT › Network › VPN"), that this account can read, by label. ``search``
        matches any part of the label. A failed read gives success=False with the reason.

        shortcut: every page lists every knowledge base (and category) and pages in memory, as
        Zendesk does; fine at thousands of categories, page in ServiceNow if lists get much bigger.
        """
        if filter_key not in (SyncFilterKey.KNOWLEDGE_BASES.value, SyncFilterKey.FOLDER_IDS.value):
            raise ValueError(f"Unsupported filter key: {filter_key}")
        what = "knowledge bases" if filter_key == SyncFilterKey.KNOWLEDGE_BASES.value else "categories"
        try:
            labels: dict[str, str] = {}
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE_BASE, f"{ServiceNowFields.ACTIVE}=true", [ServiceNowFields.TITLE]
            ):
                labels.update({r.sys_id: str(r.get(ServiceNowFields.TITLE) or r.sys_id) for r in rows})
            if filter_key == SyncFilterKey.FOLDER_IDS.value:
                categories: dict[str, tuple[str, str]] = {}
                async for rows in self._iter_table(
                    ServiceNowTables.KB_CATEGORY, None, [ServiceNowFields.LABEL, ServiceNowFields.PARENT_ID],
                    page=ServiceNowDefaults.ID_PAGE_SIZE,
                ):
                    categories.update({r.sys_id: (str(r.get(ServiceNowFields.LABEL) or r.sys_id),
                                                  str(r.get(ServiceNowFields.PARENT_ID) or "")) for r in rows})
                parents = {c: parent for c, (_, parent) in categories.items()}
                kb_labels, labels = labels, {}
                for category, (label, _) in categories.items():
                    root = self._category_root(category, parents)
                    if root in kb_labels:  # a category under an inactive knowledge base is never synced
                        path = [categories[c][0] for c in self._parent_categories(category, parents)]
                        labels[category] = " › ".join([kb_labels[root], *path[::-1], label])
        except (ServiceNowAPIError, ServiceNowRateLimited) as e:
            reason = f"HTTP {e.status_code}: {e.message}" if isinstance(e, ServiceNowAPIError) else str(e)
            self.logger.error(f"Could not load the ServiceNow {what} for the filter: {reason}")
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False,
                message=f"Could not load ServiceNow {what} ({reason}).",
            )
        needle = (search or "").strip().casefold()
        found = sorted((label.casefold(), label, sys_id) for sys_id, label in labels.items()
                       if needle in label.casefold())
        start = (max(page, 1) - 1) * limit
        return FilterOptionsResponse(
            success=True,
            options=[FilterOption(id=sys_id, label=label) for _, label, sys_id in found[start:start + limit]],
            page=page,
            limit=limit,
            has_more=len(found) > start + limit,
        )

    async def _sync_users_and_groups(self) -> None:
        """
        Sync users, groups, roles, and organizational entities from ServiceNow.

        This is the foundation for permission management.

        API Endpoints:
        - /api/now/table/sys_user - Users
        - /api/now/table/sys_user_group - Groups
        - /api/now/table/sys_user_grmember - Group memberships
        - /api/now/table/sys_user_role - Roles
        - /api/now/table/sys_user_role_contains - Role hierarchy
        - /api/now/table/sys_user_has_role - User-role assignments
        """
        self.logger.info("Syncing organizational entities...")
        await self._sync_organizational_entities()

        self.logger.info("Syncing users...")
        await self._sync_users()

        self.logger.info("Syncing user groups...")
        group_listing = await self._sync_user_groups()

        self.logger.info("Syncing roles...")
        role_ids, role_listing = await self._sync_roles()

        self.logger.info("Syncing organizational memberships, removing deleted groups...")
        await self._reconcile_user_groups(role_ids, group_listing, role_listing)

        self.logger.info("✅ Users, groups, roles, and organizational entities synced successfully")

    async def _get_admin_users(self) -> set[str]:
        """
        The sys_ids of the users who read every knowledge base and article without user criteria:
        active holders of the admin or knowledge_admin role (not knowledge_manager, which the
        platform gives every KB owner and manager and which grants nothing by itself). They are
        granted through _users_to_permissions, so one with no synced app user gets nothing. A
        failed read raises: an empty set would be written as "no admins" and remove their grants.
        When an ACL hides some of the rows, the admins of the last full listing are kept as well.
        """
        role_assignments: list[TableAPIRecord] = []
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_HAS_ROLE,
            f"{ServiceNowFields.ROLE}.{ServiceNowFields.NAME}IN{ServiceNowRoles.ADMIN},{ServiceNowRoles.KNOWLEDGE_ADMIN}"
            f"^{ServiceNowFields.STATE}={ServiceNowFields.ACTIVE}^{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [ServiceNowFields.USER], listing=listing, narrow=True,
        ):
            role_assignments.extend(rows)

        admin_sys_ids = {str(a.user) for a in role_assignments if getattr(a, "user", None)}
        complete = sorted(admin_sys_ids)
        if listing.partial:
            stored = await self.user_sync_point.read_sync_point(ServiceNowSyncPointKeys.ADMINS) or {}
            complete = list(stored.get(ServiceNowDictKeys.IDS) or [])
            admin_sys_ids |= set(complete)
            self.logger.warning("Some admin role assignments are hidden from this account; keeping the admins read last")
            self._sync_read_incomplete = True
        # What a run whose role assignments did not change grants again (see _run_sync)
        await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.ADMINS, {
            ServiceNowDictKeys.IDS: complete, ServiceNowDictKeys.GRANTED: sorted(admin_sys_ids),
        })
        self.logger.info(f"Found {len(admin_sys_ids)} unique admin users")
        return admin_sys_ids


    async def _sync_users(self) -> None:
        """
        Sync users from ServiceNow, oldest update first.

        First sync: Fetches all users
        Subsequent syncs: Only fetches users modified since last sync

        An inactive user cannot sign in to ServiceNow, so it is not written, and a stored one
        loses the connector (withdraw_app_user_access): no grant of it reaches them then. Until
        one run has read every user, every run does, so users deactivated before are found too.
        """
        migrated = bool(await self.user_sync_point.read_sync_point(ServiceNowSyncPointKeys.USERS_V2))
        last_sync_time = (
            await self._read_checkpoint(self.user_sync_point, ServiceNowSyncPointKeys.USERS) if migrated else None
        )

        query = None
        if last_sync_time:
            since = self._delta_since(last_sync_time)
            self.logger.info(f"🔄 Delta sync: fetching users updated after {since}")
            query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
        else:
            self.logger.info("🆕 Full sync: fetching all users")

        total_synced = 0
        latest_update_time = None
        inactive: set[str] = set()
        no_email = 0

        # A failed page raises: a short list is indistinguishable from a complete one to
        # everything downstream, so the sync would report success over what it missed.
        async for users_data in self._iter_table(
            ServiceNowTables.SYS_USER,
            query,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.USER_NAME,
                ServiceNowFields.EMAIL,
                ServiceNowFields.FIRST_NAME,
                ServiceNowFields.LAST_NAME,
                ServiceNowFields.TITLE,
                ServiceNowFields.ACTIVE,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            narrow=query is not None,  # a delta of hidden users only moves on
        ):
            # Pages come oldest first, so the last row is the newest seen
            latest_update_time = users_data[-1].sys_updated_on

            app_users = []
            for user_data in users_data:
                # The anonymous guest signs in to no PipesHub account. Synced, it would turn the
                # default knowledge bases' Guest User deny into a grant per user (_fetch_deny_records).
                if not self._is_active(user_data) or user_data.get(ServiceNowFields.USER_NAME) == ServiceNowDefaults.GUEST_USER_NAME:
                    inactive.add(user_data.sys_id)
                    continue
                if not (user_data.email or "").strip():
                    # Signs in as no PipesHub user: one stored with the email it had loses access too.
                    no_email += 1
                    inactive.add(user_data.sys_id)
                    continue
                app_user = await self._transform_to_app_user(user_data)
                if app_user:
                    app_users.append(app_user)

            if app_users:
                await self.data_entities_processor.on_new_app_users(app_users)
                total_synced += len(app_users)

        # A deleted user leaves no row a delta could see: the stored users are checked against
        # the active ones with an email on every full read, and at least once per
        # RECONCILE_INTERVAL_SECONDS.
        now = datetime.now(timezone.utc)
        checked = await self._read_checkpoint(self.user_sync_point, ServiceNowSyncPointKeys.USERS_RECONCILE)
        reconcile = query is None or not checked or now - datetime.strptime(
            checked, ServiceNowDefaults.DATETIME_FORMAT
        ).replace(tzinfo=timezone.utc) >= timedelta(seconds=ServiceNowDefaults.RECONCILE_INTERVAL_SECONDS)
        if inactive or reconcile:
            stored_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id, raise_on_error=True
            )
            gone = {u.source_user_id for u in stored_users if u.source_user_id in inactive}
            if reconcile:
                gone |= await self._users_gone({u.source_user_id for u in stored_users if u.source_user_id} - gone)
            stored = [u for u in stored_users if u.source_user_id in gone]
            if stored:
                await self.data_entities_processor.withdraw_app_user_access(self.connector_id, stored)
            if reconcile:
                await self.user_sync_point.update_sync_point(
                    ServiceNowSyncPointKeys.USERS_RECONCILE,
                    {ServiceNowSyncPointKeys.LAST_SYNC_TIME: now.strftime(ServiceNowDefaults.DATETIME_FORMAT)},
                )

        # Save checkpoint for next sync
        if latest_update_time:
            await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.USERS, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})
        if not migrated:
            await self.user_sync_point.update_sync_point(ServiceNowSyncPointKeys.USERS_V2, {ServiceNowDictKeys.DONE: True})

        if no_email:
            self._note_problem(
                f"{_plural(no_email, 'active ServiceNow user has', 'active ServiceNow users have')} no email "
                "address and could not be synced, so they get no access in PipesHub", persistent=True,
            )
        self.logger.info(
            f"User sync complete, Total synced: {total_synced}, inactive: {len(inactive)}, no email: {no_email}"
        )

    async def _users_gone(self, stored: set[str]) -> set[str]:
        """The ``stored`` user sys_ids that are no active user with an email at the source (nor the guest): deleted,
        or changed without a delta seeing it. Under an ACL that hides some users, only those
        ServiceNow counts no such row for."""
        live_query = (f"{ServiceNowFields.ACTIVE}=true^{ServiceNowFields.EMAIL}ISNOTEMPTY"
                      f"^{ServiceNowFields.USER_NAME}!={ServiceNowDefaults.GUEST_USER_NAME}")
        live: set[str] = set()
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER, live_query, [ServiceNowFields.SYS_ID],
            page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing,
        ):
            live.update(str(r.sys_id) for r in rows if r.sys_id)
        gone = stored - live
        if listing.partial:
            gone = {u for u in gone if await self._gone_at_source(
                ServiceNowTables.SYS_USER, f"{ServiceNowFields.SYS_ID}={u}^{live_query}"
            )}
        return gone

    @staticmethod
    def _is_active(row: object) -> bool:
        return str(getattr(row, ServiceNowFields.ACTIVE, "") or "").lower() != "false"

    async def _sync_user_groups(self) -> _Listing:
        """
        Write every group with its flattened members, also a group with none: the people who
        left it lose it. Returns how the group listing went (see _reconcile_user_groups).
        """
        self.logger.info("Starting user group synchronization")
        membership_listing = _Listing()
        memberships_data = await self._fetch_all_memberships(membership_listing)
        listing = _Listing()
        groups_data = await self._fetch_all_groups(listing)
        self._groups = {g.sys_id: g for g in groups_data}

        group_with_permissions = await self._flatten_and_create_user_groups(groups_data, memberships_data)
        # The ownership group of an article grants its members (see _article_access), and group
        # criteria match them (see _members_of).
        self._group_members = {
            g.source_user_group_id: {u.source_user_id for u in users if u.source_user_id}
            for g, users in group_with_permissions
        }
        self._held[ServiceNowFields.GROUP] = (self._group_members, membership_listing.partial or listing.partial)
        if group_with_permissions:
            await self.data_entities_processor.on_new_user_groups(group_with_permissions)

        self.logger.info(f"✅ Processed {len(group_with_permissions)} user groups")
        return listing

    async def _fetch_all_groups(self, listing: _Listing | None = None) -> List[SysUserGroup]:
        """Fetch all groups from ServiceNow (no delta sync), inactive ones too: those are written
        with no members (an inactive group matches nobody).

        Returns:
            List of SysUserGroup Pydantic models
        """
        all_groups: List[SysUserGroup] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_GROUP,
            None,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.DESCRIPTION,
                ServiceNowFields.PARENT,
                ServiceNowFields.MANAGER,
                ServiceNowFields.ACTIVE,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_groups.extend(SysUserGroup(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_groups)} groups")
        return all_groups

    async def _fetch_all_memberships(self, listing: _Listing | None = None) -> List[SysUserGroupMembership]:
        """Fetch every user-group membership of an active user from ServiceNow.

        This read must stay full. ``on_new_user_groups`` deletes all permission
        edges that point to a group before it writes the members given to it, so
        a delta read would drop every membership whose ``sys_user_grmember`` row
        did not change inside the delta window.

        Returns:
            List of SysUserGroupMembership Pydantic models
        """
        self.logger.info("Fetching all user memberships")

        all_memberships: List[SysUserGroupMembership] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_GRMEMBER,
            f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.USER,
                ServiceNowFields.GROUP,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_memberships.extend(SysUserGroupMembership(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_memberships)} memberships")
        return all_memberships

    async def _sync_roles(self) -> tuple[set[str], _Listing]:
        """
        Sync roles as ROLE_-prefixed user groups, each with every active holder, also a role
        with none. sys_user_has_role already holds a row per role a user gets from a group or
        a containing role (inherited=true); the role hierarchy is flattened as well.

        Returns the listed role ids and how the role listing went (see _reconcile_user_groups).
        """
        self.logger.info("Starting role synchronization")

        assignment_listing = _Listing()
        role_assignments = await self._fetch_all_role_assignments(assignment_listing)
        # The out-of-box "Has a role" criterion counts a user's roles in every state (see _has_role_users).
        self._role_names = defaultdict(list)
        for assignment in role_assignments:
            self._role_names[assignment.user].append(str(getattr(assignment, ServiceNowFields.ROLE_NAME, "") or ""))
        role_assignments = [
            a for a in role_assignments
            if str(getattr(a, ServiceNowFields.STATE, "") or "").lower() == ServiceNowFields.ACTIVE
        ]
        listing = _Listing()
        roles_data = await self._fetch_all_roles(listing)
        hierarchy_data = await self._fetch_role_hierarchy()

        # A holder of a role holds every role it contains, so a contained role's members
        # include the holders of each role that contains it (a role can contain several).
        containers: dict[str, set[str]] = defaultdict(set)
        for hierarchy_record in hierarchy_data:
            if hierarchy_record.contains and hierarchy_record.role:
                containers[hierarchy_record.contains].add(hierarchy_record.role)

        roles_as_groups = [
            SysUserGroup(
                sys_id=role.sys_id, name=role.name, description=role.description,
                sys_created_on=role.sys_created_on, sys_updated_on=role.sys_updated_on, active="true",
            )
            for role in roles_data
        ]
        role_assignments_as_memberships = [
            SysUserGroupMembership(
                sys_id=assignment.sys_id, user=assignment.user, group=assignment.role,
                sys_updated_on=assignment.sys_updated_on,
            )
            for assignment in role_assignments
        ]
        roles_with_permissions = await self._flatten_and_create_user_groups(
            roles_as_groups, role_assignments_as_memberships, children=containers
        )

        for role_group, _users in roles_with_permissions:
            if not role_group.name.startswith(ServiceNowPrefixes.ROLE):
                role_group.name = f"{ServiceNowPrefixes.ROLE}{role_group.name}"

        self._held[ServiceNowFields.ROLE] = ({
            g.source_user_group_id: {u.source_user_id for u in users if u.source_user_id}
            for g, users in roles_with_permissions
        }, assignment_listing.partial)
        if roles_with_permissions:
            await self.data_entities_processor.on_new_user_groups(roles_with_permissions)

        self.logger.info(f"✅ Processed {len(roles_with_permissions)} roles")
        return {r.sys_id for r in roles_data}, listing

    async def _fetch_all_roles(self, listing: _Listing | None = None) -> List[SysUserRole]:
        """Fetch all roles from sys_user_role table.

        Returns:
            List of SysUserRole Pydantic models
        """
        self.logger.info("Fetching all roles")

        all_roles: List[SysUserRole] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_ROLE,
            None,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.DESCRIPTION,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_roles.extend(SysUserRole(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_roles)} roles")
        return all_roles

    async def _fetch_all_role_assignments(self, listing: _Listing | None = None) -> List[SysUserRoleAssignment]:
        """
        Fetch the role assignments of active users from sys_user_has_role, in every state, with
        the state and the role's name (role groups take the active ones; see _sync_roles).

        This read must stay full, for the reason given in
        ``_fetch_all_memberships``: roles go through the same
        ``on_new_user_groups`` replace-on-write path.

        Returns:
            List of SysUserRoleAssignment Pydantic models
        """
        self.logger.info("Fetching all active role assignments")
        all_assignments: List[SysUserRoleAssignment] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild.
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER_HAS_ROLE,
            f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.USER,
                ServiceNowFields.ROLE,
                ServiceNowFields.ROLE_NAME,
                ServiceNowFields.STATE,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            all_assignments.extend(SysUserRoleAssignment(**record.model_dump()) for record in rows)

        self.logger.info(f"Fetched {len(all_assignments)} role assignments")
        return all_assignments

    async def _fetch_role_hierarchy(self) -> List[SysUserRoleContains]:
        """
        Fetch role hierarchy from sys_user_role_contains table.

        This is a full sync (no checkpoint) since role hierarchy changes are rare.

        Returns:
            List of SysUserRoleContains Pydantic models
        """
        self.logger.info("Fetching role hierarchy")

        all_hierarchy: List[SysUserRoleContains] = []
        # A failed page raises: a partial list makes the caller delete permission
        # edges that it cannot rebuild. A table closed to the account is read as empty:
        # sys_user_has_role already has a row for every role a user gets from another.
        try:
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER_ROLE_CONTAINS,
                None,
                [ServiceNowFields.SYS_ID, ServiceNowFields.CONTAINS, ServiceNowFields.ROLE],
                page=ServiceNowDefaults.ID_PAGE_SIZE,
            ):
                all_hierarchy.extend(SysUserRoleContains(**record.model_dump()) for record in rows)
        except ServiceNowAPIError as e:
            if e.status_code != HttpStatusCode.FORBIDDEN.value:
                raise
            self.logger.info("This account cannot read sys_user_role_contains; roles come from sys_user_has_role")
            return []

        self.logger.info(f"Fetched {len(all_hierarchy)} role hierarchy records")
        return all_hierarchy

    async def _reconcile_user_groups(self, role_ids: set[str], group_listing: _Listing, role_listing: _Listing) -> None:
        """Give each company, department and location group exactly its active users, and
        delete the stored groups and roles that ServiceNow no longer has.

        A user's company, department and location are read from every active user each run,
        so a user who moved, or was deactivated, leaves the old one (memberships written one
        at a time could only be added). A group or role missing from a listing that an ACL cut
        short is deleted only once ServiceNow counts no row for it.
        """
        # An inactive group is listed, and not written (see _flatten_and_create_user_groups): a stored one goes.
        listed = {g for g, row in self._groups.items() if self._is_active(row)} | role_ids
        stored = [g for g in await self.data_entities_processor.get_all_user_groups(self.connector_id, raise_on_error=True)
                  if g.source_user_group_id not in listed]
        org_prefixes = tuple(_ORG_GROUP_PREFIXES.values())
        await self._sync_org_memberships([g for g in stored if g.name.startswith(org_prefixes)])

        for group in stored:
            if group.name.startswith(org_prefixes):
                continue
            is_role = group.name.startswith(ServiceNowPrefixes.ROLE)
            table, listing = (
                (ServiceNowTables.SYS_USER_ROLE, role_listing) if is_role else (ServiceNowTables.SYS_USER_GROUP, group_listing)
            )
            if group.source_user_group_id not in self._groups and listing.partial and not await self._gone_at_source(
                table, f"{ServiceNowFields.SYS_ID}={group.source_user_group_id}"
            ):
                self._sync_read_incomplete = True  # kept, not seen: a full sync must not unlink it
                continue
            self.logger.info(f"Removing {'role' if is_role else 'group'} {group.source_user_group_id}, deleted or inactive in ServiceNow")
            await self.data_entities_processor.on_user_group_deleted(group.source_user_group_id, self.connector_id)

    async def _sync_org_memberships(self, org_groups: list[AppUserGroup]) -> None:
        """Replace the members of the stored company, department and location groups with the
        active users whose field names them (exact match: ServiceNow criteria do not include
        child departments, locations or companies)."""
        if not org_groups:
            return
        members: dict[str, set[str]] = defaultdict(set)
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.SYS_USER,
            f"{ServiceNowFields.ACTIVE}=true^" + "^OR".join(f"{f}ISNOTEMPTY" for f in _ORG_GROUP_PREFIXES),
            [ServiceNowFields.SYS_ID, *_ORG_GROUP_PREFIXES],
            page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing,
        ):
            for row in rows:
                for field in _ORG_GROUP_PREFIXES:
                    if org_id := getattr(row, field, None):
                        members[str(org_id)].add(row.sys_id)
        # Department, location and company criteria match exactly these (see _members_of).
        self._held.update(dict.fromkeys(_ORG_GROUP_PREFIXES, (members, listing.partial)))
        users = {u.source_user_id: u for u in await self._all_app_users()}
        await self.data_entities_processor.on_new_user_groups([
            (group, [users[s] for s in sorted(members.get(group.source_user_group_id, ())) if s in users])
            for group in org_groups
        ])


    async def _flatten_and_create_user_groups(
        self,
        groups_data: List[SysUserGroup],
        memberships_data: List[SysUserGroupMembership],
        children: dict[str, set[str]] | None = None,
    ) -> List[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Flatten group hierarchy and create AppUserGroup objects: a group's members are its
        direct members plus those of its child groups (ServiceNow group criteria match child
        group members too). An inactive group matches nobody: it is not written and adds no
        members to its parent (_reconcile_user_groups removes a stored one).

        Args:
            groups_data: List of SysUserGroup Pydantic models
            memberships_data: List of SysUserGroupMembership Pydantic models
            children: group id -> the groups whose members it includes; by default each
                group's child groups (``parent``)

        Returns:
            List of (AppUserGroup, [AppUser]) tuples
        """
        inactive = {g.sys_id for g in groups_data if not self._is_active(g)}
        group_by_id = {group.sys_id: group for group in groups_data}
        if children is None:
            children = defaultdict(set)  # parent_id -> {child_ids}
            for group in groups_data:
                if group.parent:
                    children[group.parent].add(group.sys_id)

        # Build direct user memberships
        direct_users = defaultdict(set)  # group_id -> {user_ids}

        for membership in memberships_data:
            user_id = membership.user
            group_id = membership.group

            if user_id and group_id:
                direct_users[group_id].add(user_id)

        def get_all_users(group_id: str, visited: set) -> set:
            """Get all users including inherited from active child groups."""
            if group_id in visited or group_id in inactive:
                return set()
            visited.add(group_id)
            all_users = set(direct_users.get(group_id, []))
            for child_id in children.get(group_id, []):
                all_users.update(get_all_users(child_id, visited))
            return all_users

        # Create AppUserGroup objects with flattened members
        result = []

        # Get all existing users from database for lookup
        existing_app_users = await self._all_app_users()

        # Create lookup map: source_user_id -> AppUser
        user_lookup = {user.source_user_id: user for user in existing_app_users}

        for group_id, group_data in group_by_id.items():
            if group_id in inactive:
                continue
            # Create AppUserGroup
            user_group = self._transform_to_user_group(group_data)

            if not user_group:
                continue

            app_users = [user_lookup[u] for u in get_all_users(group_id, set()) if u in user_lookup]
            result.append((user_group, app_users))

        self.logger.info(f"Flattened {len(result)} groups")
        return result

    async def _sync_organizational_entities(self) -> None:
        """
        Sync all organizational entities from ServiceNow.

        Syncs in order:
        1. Companies (top-level)
        2. Departments
        3. Locations

        Each entity type creates AppUserGroup nodes with prefix (COMPANY_, DEPARTMENT_, etc.); their
        members are written by _sync_org_memberships.
        """
        self.logger.info("🏢 Starting organizational entities sync")

        # Sync each entity type in order
        for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
            await self._sync_single_organizational_entity(entity_type, config)

        self.logger.info("✅ All organizational entities synced successfully")

    async def _sync_single_organizational_entity(
        self, entity_type: str, config: OrganizationalEntityConfig
    ) -> None:
        """
        Generic sync method for a single organizational entity type: each row becomes an AppUserGroup.

        Args:
            entity_type: Type of entity (company, department, location)
            config: OrganizationalEntityConfig with table name, fields, prefix, and sync point key
        """
        table_name = config["table"]
        fields = config["fields"]
        prefix = config["prefix"]
        sync_point_key = config["sync_point_key"]

        # Get sync point for this entity type
        sync_point = self.org_entity_sync_points.get(entity_type)

        self.logger.info(f"📊 Starting {entity_type} sync from table {table_name}")

        last_sync_time = await self._read_checkpoint(sync_point, sync_point_key)

        query = None
        if last_sync_time:
            since = self._delta_since(last_sync_time)
            self.logger.info(f"🔄 Delta sync: fetching {entity_type} updated after {since}")
            query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
        else:
            self.logger.info(f"🆕 Full sync: fetching all {entity_type}")

        total_synced = 0
        latest_update_time = None

        # A failed page raises: a short list is indistinguishable from a complete one to
        # everything downstream, so the sync would report success over what it missed.
        async for entities_data in self._iter_table(
            table_name, query, fields.split(CommonStrings.COMMA), page=ServiceNowDefaults.ID_PAGE_SIZE
        ):
            # Pages come oldest first, so the last row is the newest seen
            latest_update_time = entities_data[-1].sys_updated_on

            # Transform to AppUserGroup entities
            user_groups = []
            for entity_data in entities_data:
                user_group = self._transform_to_organizational_group(
                    entity_data, prefix
                )
                if user_group:
                    user_groups.append(user_group)

            if user_groups:
                await self.data_entities_processor.batch_upsert_user_groups(user_groups)

                total_synced += len(user_groups)

        # Save checkpoint
        if latest_update_time:
            await sync_point.update_sync_point(
                sync_point_key, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time}
            )

        self.logger.info(
            f"✅ {entity_type.capitalize()} sync complete. Total synced: {total_synced}"
        )

    async def _sync_knowledge_bases(self, admin_source_ids: Iterable[str], *, grants: bool = True) -> None:
        """
        Sync knowledge bases from ServiceNow kb_knowledge_base table.

        Uses data_entities_processor.on_new_record_groups() so that BELONGS_TO edges
        (RecordGroup → Org, RecordGroup → App) are created; Knowledge Hub tree relies on these.

        Creates:
        - RecordGroup nodes (type=SERVICENOW) in recordGroups collection
        - BELONGS_TO edges: RecordGroup → Org, RecordGroup → App
        - grants: READ for Can Read minus Cannot Read, WRITE for Can Contribute minus Cannot
          Contribute (contributors read every article; a Cannot Read never stops one), OWNER for
          the owner, READ for the managers, admins and knowledge admins

        Every run reads every KB (see the comment in the body).

        Args:
            admin_source_ids: sys_ids of the admins and knowledge admins (_get_admin_users)
            grants: False when no table behind the grants changed (see _run_sync): the KBs are
                listed for their owners, managers and scope, and not written
        """
        # This read stays full. A knowledge base holds its read grants in
        # kb_uc_can_read_mtom, and a change there does not touch
        # kb_knowledge_base.sys_updated_on. A delta on the base row would
        # therefore never see a new or a removed grant.
        self.logger.info("Fetching all knowledge bases")
        if grants:
            await self._load_kb_criteria()
        total_synced = 0
        listed: set[str] = set()
        live: set[str] = set()
        listing = _Listing()

        # A failed page raises: a short list is indistinguishable from a complete one to
        # everything downstream, so the sync would report success over what it missed.
        async for kbs_data in self._iter_table(
            ServiceNowTables.KB_KNOWLEDGE_BASE,
            None,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.TITLE,
                ServiceNowFields.DESCRIPTION,
                ServiceNowFields.OWNER,
                ServiceNowFields.KB_MANAGERS,
                ServiceNowFields.ACTIVE,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            listing=listing,
        ):
            # Transform to RecordGroup entities
            kb_record_groups = []
            for kb_data in kbs_data:
                listed.add(kb_data.sys_id)
                # ServiceNow search leaves an inactive knowledge base out, and its readers lose it.
                # One the filter leaves out goes the same way (see _remove_groups_out_of_scope).
                inactive = str(getattr(kb_data, ServiceNowFields.ACTIVE, "") or "").lower() == "false"
                if inactive or not name_passes_filter(
                    self.sync_filters, SyncFilterKey.KNOWLEDGE_BASES.value, kb_data.sys_id
                ):
                    continue
                live.add(kb_data.sys_id)
                # The articles that do not inherit name the same people (see _kb_privileged_users).
                self._kb_owner_cache[kb_data.sys_id] = set(self._split_ids(getattr(kb_data, ServiceNowFields.OWNER, None))) | set(
                    self._split_ids(getattr(kb_data, ServiceNowFields.KB_MANAGERS, None)))
                kb_record_group = self._transform_to_kb_record_group(kb_data)
                if kb_record_group:
                    kb_record_groups.append((kb_record_group, kb_data))

            # Build (RecordGroup, permissions) list and route through on_new_record_groups so that
            # BELONGS_TO edges (RecordGroup → Org, RecordGroup → App) are created by the processor.
            if grants and kb_record_groups:
                kb_list_with_permissions = []
                for kb_record_group, kb_data in kb_record_groups:
                    kb_sys_id = kb_data.sys_id
                    managers = set(self._split_ids(getattr(kb_data, ServiceNowFields.KB_MANAGERS, None)))

                    try:
                        criteria = await self._kb_criteria(kb_sys_id)
                        read_permissions = await self._process_criteria_permissions(
                            criteria[ServiceNowDictKeys.READ],
                            PermissionType.READ,
                            deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_READ],
                        )
                        write_permissions = await self._process_criteria_permissions(
                            criteria[ServiceNowDictKeys.WRITE],
                            PermissionType.WRITE,
                            deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_WRITE],
                        )
                        manager_permissions = await self._users_to_permissions(managers, PermissionType.READ)
                        admin_permissions = await self._users_to_permissions(set(admin_source_ids), PermissionType.READ)
                    except (_CriteriaUnreadable, ServiceNowAPIError) as e:
                        _raise_if_run_stop(e)
                        # Unknown is not empty: write no grants for this KB, keep what is stored.
                        self.logger.error(f"Skipping knowledge base {kb_sys_id}, its user criteria were not read in full: {e}")
                        self._kbs_unread.append(str(kb_data.title or kb_sys_id))
                        await self.data_entities_processor.note_kept_record_group(self.connector_id, kb_sys_id)
                        self._sync_read_incomplete = True
                        continue

                    # Admins and knowledge admins read every knowledge base
                    permission_objects = read_permissions + write_permissions + manager_permissions + admin_permissions

                    permission_objects += await self._users_to_permissions(
                        set(self._split_ids(kb_data.owner)), PermissionType.OWNER
                    )

                    kb_list_with_permissions.append((kb_record_group, permission_objects))

                await self.data_entities_processor.on_new_record_groups(kb_list_with_permissions)
                total_synced += len(kb_list_with_permissions)

        if listing.partial:
            # A hidden knowledge base was not seen: a full sync must not unlink it or what is under it.
            self._sync_read_incomplete = True
        # The listing read to the end (a failed page raises), so what it left out is gone, unless
        # an ACL hid it. An empty one says nothing (see _remove_groups_out_of_scope), so no
        # category leaves scope. A hidden one that is still there keeps its categories.
        self._kbs_back_in_scope, kept, _ = await self._remove_groups_out_of_scope(
            ServiceNowSyncPointKeys.KB_SCOPE, listed, live,
            hidden_in=(
                ServiceNowTables.KB_KNOWLEDGE_BASE,
                f"^{ServiceNowFields.ACTIVE}=true{self._kb_filter_clause(ServiceNowFields.SYS_ID)}",
            ) if listing.partial else None,
        )
        self._live_kb_ids = (live | kept) if listed else None
        self.logger.info(f"✅ Knowledge base sync complete, Total synced: {total_synced}")

    async def _sync_categories(self) -> None:
        """
        Sync categories from ServiceNow kb_category table.

        Creates:
        - RecordGroup nodes (type=SERVICENOW_CATEGORY) in recordGroups collection
        - PARENT_CHILD edges in nodeRelations collection

        First sync: Fetches all categories
        Subsequent syncs: Only fetches categories modified since last sync. Every run also lists
        every category id, which tells the categories that are gone, and the ones out of scope:
        a category is synced only when its parent chain ends at a knowledge base this run wrote.
        """
        parents: dict[str, str] = {}
        listing = _Listing()
        async for rows in self._iter_table(
            ServiceNowTables.KB_CATEGORY, None, [ServiceNowFields.PARENT_ID], page=ServiceNowDefaults.ID_PAGE_SIZE,
            listing=listing,
        ):
            parents.update({r.sys_id: r.parent_id or "" for r in rows})
        if listing.partial:
            self._sync_read_incomplete = True  # a hidden category was not seen (see _sync_knowledge_bases)
        in_scope = set(parents)
        if self._live_kb_ids is not None:
            in_scope = {c for c in parents if self._category_root(c, parents) in self._live_kb_ids}
        chosen_filter = self.sync_filters.get(SyncFilterKey.FOLDER_IDS)
        if chosen_filter is not None and not chosen_filter.is_empty():
            chosen = self._with_sub_categories(set(chosen_filter.as_list()), parents)
            keep_in = chosen_filter.operator_value != FilterOperator.NOT_IN
            self._category_filter = (keep_in, chosen)
            # In also writes the parents of what it picks, so the tree under the knowledge base is whole.
            wanted = chosen | {a for c in chosen for a in self._parent_categories(c, parents)}
            in_scope &= wanted if keep_in else set(parents) - chosen
        self._category_ids = in_scope

        # A knowledge base back in scope lost its categories when it left, and they did not change since.
        last_sync_time = None if self._kbs_back_in_scope else await self._read_checkpoint(
            self.category_sync_point, ServiceNowSyncPointKeys.CATEGORIES
        )

        query = None
        if last_sync_time:
            since = self._delta_since(last_sync_time)
            self.logger.info(f"🔄 Delta sync: Fetching categories updated after {since}")
            query = f"{ServiceNowFields.SYS_UPDATED_ON}>{since}"
        else:
            self.logger.info("🆕 Full sync: Fetching all categories")

        total_synced = 0
        latest_update_time = None

        # A failed page raises: a short list is indistinguishable from a complete one to
        # everything downstream, so the sync would report success over what it missed.
        async for categories_data in self._iter_table(
            ServiceNowTables.KB_CATEGORY,
            query,
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.LABEL,
                ServiceNowFields.VALUE,
                ServiceNowFields.PARENT_TABLE,
                ServiceNowFields.PARENT_ID,
                ServiceNowFields.ACTIVE,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ],
            page=ServiceNowDefaults.ID_PAGE_SIZE,
        ):
            # Pages come oldest first, so the last row is the newest seen
            latest_update_time = categories_data[-1].sys_updated_on

            # Transform categories to RecordGroups with hierarchy information
            categories_with_permissions = []
            for cat_data in categories_data:
                if cat_data.sys_id not in in_scope:
                    continue
                category_rg = self._transform_to_category_record_group(cat_data)
                if not category_rg:
                    continue

                # Categories inherit permissions from parent KB
                categories_with_permissions.append((category_rg, []))

            # Use on_new_record_groups to create nodes and edges in one transaction
            if categories_with_permissions:
                await self.data_entities_processor.on_new_record_groups(categories_with_permissions)
                total_synced += len(categories_with_permissions)

        # Update sync checkpoint
        if latest_update_time:
            await self.category_sync_point.update_sync_point(ServiceNowSyncPointKeys.CATEGORIES, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: latest_update_time})

        # An inactive category changes nothing in ServiceNow (its articles stay searchable), so it stays.
        _, _, gone = await self._remove_groups_out_of_scope(
            ServiceNowSyncPointKeys.CATEGORY_SCOPE, set(parents), in_scope,
            hidden_in=(ServiceNowTables.KB_CATEGORY, "") if listing.partial else None,
        )
        # Deleting a category clears its articles' kb_category without changing their rows, and
        # its group took them along: the articles with no category are read again (_sync_articles).
        self._categories_gone = bool(gone)
        self.logger.info(f"✅ Categories synced: {total_synced} total")

    @staticmethod
    def _with_sub_categories(chosen: set[str], parents: dict[str, str]) -> set[str]:
        """``chosen`` and every category below one of them."""
        children: dict[str, list[str]] = defaultdict(list)
        for child, parent in parents.items():
            children[parent].append(child)
        found, frontier = set(chosen), list(chosen)
        while frontier:
            for child in children[frontier.pop()]:
                if child not in found:
                    found.add(child)
                    frontier.append(child)
        return found

    @staticmethod
    def _parent_categories(category_id: str, parents: dict[str, str]) -> list[str]:
        """The categories above ``category_id``, nearest first, up to its knowledge base."""
        found: list[str] = []
        parent = parents.get(category_id)
        while parent in parents and parent not in found and parent != category_id:
            found.append(parent)
            parent = parents[parent]
        return found

    @staticmethod
    def _category_root(category_id: str, parents: dict[str, str]) -> str:
        """The id a category's parent chain ends at: its knowledge base, or a parent the listing lacks."""
        seen: set[str] = set()
        while category_id in parents and category_id not in seen:
            seen.add(category_id)
            category_id = parents[category_id]
        return category_id

    async def _remove_groups_out_of_scope(
        self, key: str, listed: set[str], in_scope: set[str], *, hidden_in: tuple[str, str] | None = None,
    ) -> tuple[set[str], set[str], set[str]]:
        """Delete the stored knowledge bases or categories that a complete listing no longer has
        in scope, with everything below them. Returns the ones in scope now that were not in the
        last listing, the ones kept because an ACL hid them, and the ones removed that the
        listing lacks (deleted at the source).

        What is stored is the last listing's scope, kept under ``key``; before there is one, only
        the listed groups out of scope can be stored ones. A listing with nothing in it removes
        nothing: that is the account failing, not every group leaving. The new scope is stored
        only once the articles step ran without failures (see _sync_articles), so a run that
        fails or stops before then removes the same groups again, and reads the articles of a
        group back in scope again. A failed removal keeps the last scope too. ``hidden_in`` (table, condition) is set when an
        ACL hid rows from the listing: a group it left out is removed only once ServiceNow counts
        no row with its sys_id and the condition.
        """
        if not listed:
            self.logger.warning(f"The {key} listing came back empty; nothing is removed")
            return set(), set(), set()
        stored = await self.category_sync_point.read_sync_point(key)
        previous = set(stored.get(ServiceNowDictKeys.IDS) or []) if stored else listed
        removed_all = True
        kept: set[str] = set()
        gone: set[str] = set()
        for group_id in sorted(previous - in_scope):
            try:
                # Before there is a stored scope, a group out of scope may never have been written.
                if not stored and not await self.data_entities_processor.get_record_group_by_external_id(
                    self.connector_id, group_id
                ):
                    continue
                if hidden_in and group_id not in listed and not await self._gone_at_source(
                    hidden_in[0], f"{ServiceNowFields.SYS_ID}={group_id}{hidden_in[1]}"
                ):
                    kept.add(group_id)
                    continue
                await self.data_entities_processor.on_record_group_deleted(group_id, self.connector_id)
                if group_id not in listed:
                    gone.add(group_id)
            except Exception as e:
                _raise_if_run_stop(e)
                self.logger.error(f"Could not remove {group_id}, which is out of scope now; retrying next sync: {e}")
                self._note_problem(_CLEANUP_FAILED)
                removed_all = False
        if removed_all:
            self._scopes_to_store[key] = sorted(in_scope | kept)
        return in_scope - previous, kept, gone

    async def _article_checkpoint(self) -> tuple[bool, str | None]:
        """(whether articles are stored under their number and attachments under their key, the
        article checkpoint): no checkpoint until they are, so every run reads every article."""
        migrated = bool(await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLE_KEY_V2)) \
            and bool(await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ATTACHMENT_KEY_V2))
        return migrated, (
            await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.ARTICLES) if migrated else None
        )

    async def _read_access_state(self) -> tuple[dict | None, bool]:
        """The instance properties, then _access_reread_due, before the steps read what it guards:
        a change made while they run is then seen by the next run. The properties change without
        a restart, and the fingerprint includes them."""
        self.apply_article_read_criteria = await self._resolve_article_read_criteria()
        self.block_kb_without_criteria = await self._resolve_block_no_criteria()
        _, last_sync_time = await self._article_checkpoint()
        return await self._access_reread_due(full=not last_sync_time)

    async def _sync_articles(self, access: tuple[dict | None, bool] | None = None) -> None:
        """
        Sync KB articles and attachments from ServiceNow using batch processing.

        An article is stored under its number: each version of it is a row with its own sys_id,
        and the record follows the current row (see _current_article_query). A full read lists the
        current rows. A delta lists every row changed since the checkpoint, in any state, and
        resolves each changed number to its current row, which is written; a stored number with
        none is deleted. Then stored articles with no current row are deleted (_reconcile_articles).
        That also removes the records stored under a version's sys_id before articles were keyed by
        number: until it has run to the end once, every run reads every article.

        API Endpoints:
        - /api/now/table/kb_knowledge - Articles
        - /api/now/table/user_criteria - Permissions
        - /api/now/table/sys_attachment - Attachments
        - /api/now/table/kb_feedback - Feedback (for the revision; see _fetch_article_content)

        ``access``: _access_reread_due, taken by _run_sync before the steps that read what it guards.
        """
        migrated, last_sync_time = await self._article_checkpoint()
        self._rekey_attachments = not migrated
        current = self._current_article_query()
        # Inline images and feedback change without their article row changing.
        inline_changed, inline_checkpoint, inline_keys = await self._changed_articles(
            ServiceNowTables.SYS_ATTACHMENT, ServiceNowFields.TABLE_SYS_ID, ServiceNowSyncPointKeys.INLINE_IMAGES,
            f"{ServiceNowFields.TABLE_NAME}={ServiceNowTables.INLINE_IMAGE}^",
        )
        feedback_changed, feedback_checkpoint, feedback_keys = await self._changed_articles(
            ServiceNowTables.KB_FEEDBACK, ServiceNowFields.ARTICLE, ServiceNowSyncPointKeys.FEEDBACK
        )
        # A deleted feedback row leaves no row a delta could see, only a lower count.
        stored_count = ((await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.FEEDBACK))
                        or {}).get(ServiceNowDictKeys.COUNT)
        # A full read counts them with its feedback listing (below).
        feedback_count = await self._feedback_count() if last_sync_time else None
        feedback_deleted = None not in (feedback_count, stored_count) and feedback_count < stored_count
        if feedback_count is None:
            feedback_count = stored_count

        # (query, whether its rows move the checkpoint, whether its rows are versions to resolve)
        queries = [(current, True, False)]
        reread_query = None
        fingerprint, reread = access or await self._access_reread_due(full=not last_sync_time)
        # Where a full read that stopped got to: it continues from there instead of starting over
        # (its pages are a keyset on (sys_updated_on, sys_id), so a row changed since comes again).
        # Only a full read writes it. A full sync of the platform clears every sync point first, so
        # it starts over; a full read without one (the first run, an upgrade) continues from it.
        resume = {} if last_sync_time else (
            await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES) or {})
        resume_after = tuple(resume.get(ServiceNowSyncPointKeys.FULL_RESUME_AFTER) or ()) or None
        if resume_after:
            self.logger.info(f"🔁 Resuming the full article read after {resume_after[0]}")
            # This run does not see what the earlier part read: a full sync must not unlink it.
            self._sync_read_incomplete = True
            # What changed since the earlier part read its pages is looked for from when it started.
            fingerprint = None  # not stored: the next run re-reads the articles with criteria
            inline_checkpoint = resume.get(ServiceNowSyncPointKeys.INLINE_IMAGES) or inline_checkpoint
            feedback_checkpoint = resume.get(ServiceNowSyncPointKeys.FEEDBACK) or feedback_checkpoint
            inline_keys, feedback_keys = [], []
            feedback_count = resume.get(ServiceNowDictKeys.COUNT, feedback_count)
        # The rows the last run synced that this delta's overlap reads again: skipped (_overlap_keys).
        # Not while the re-read pass runs: it lists only the rows before the overlap.
        synced_before = (await self._read_delta_point(self.article_sync_point, ServiceNowSyncPointKeys.ARTICLES))[1] \
            if last_sync_time and not reread else set()
        synced: set[str] = set()  # the keys of the checkpoint rows this run synced
        if last_sync_time:
            since = self._delta_since(last_sync_time)
            self.logger.info(f"🔄 Delta sync: Fetching articles updated after {since}")
            reread_query = (
                f"{current}^{ServiceNowFields.CAN_READ_USER_CRITERIA}ISNOTEMPTY"
                f"^OR{ServiceNowFields.CANNOT_READ_USER_CRITERIA}ISNOTEMPTY"
                f"^OR{ServiceNowFields.OWNERSHIP_GROUP}ISNOTEMPTY"
                f"^{ServiceNowFields.SYS_UPDATED_ON}<={since}"
            )
            queries = [
                # Every state: a new version, a retire or an unpublish changes some row of the article.
                (f"{ServiceNowFields.SYS_UPDATED_ON}>{since}", True, True),
                # An article with its own criteria or an ownership group carries grants that name
                # users (resolved criteria, the privileged readers, the contributors) or that
                # follow its knowledge base's criteria, which change without the article
                # changing: such articles are re-read and their grants replaced every run.
                # Only when one of the tables they depend on changed, and once a day (_access_reread_due).
                *([(reread_query, False, False)] if reread else []),
                # The rows whose inline images or feedback changed (feedback may be on an old version).
                *[(f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(c)}", False, True)
                  for c in self._chunk_ids(sorted({*inline_changed, *feedback_changed}))],
                # A knowledge base back in scope: its articles left with it and did not change since.
                *[(f"{current}^{ServiceNowFields.KB_KNOWLEDGE_BASE}IN{CommonStrings.COMMA.join(c)}", False, False)
                  for c in self._chunk_ids(sorted(self._kbs_back_in_scope))],
                # A deleted category: its articles left with its group and now have no category.
                *([(f"{current}^{ServiceNowFields.KB_CATEGORY}ISEMPTY", False, False)] if self._categories_gone else []),
                # shortcut: a feedback delete re-reads every article, as nothing names the article
                # whose last feedback went; keep per-article counts if deletes become frequent.
                # A delete and an add in the same run keep the count, and go unseen.
                *([(current, False, False)] if feedback_deleted else []),
            ]
        else:
            self.logger.info("🆕 Full sync: Fetching all articles")

        total_articles_synced = 0
        total_attachments_synced = 0
        latest_update_time = resume_after[0] if resume_after else None
        failed = FailedItems()
        failed_ids: set[str] = set()  # kept by the reconcile (see _reconcile_articles)
        # The ones a later run may get past. An article whose criteria an ACL hides fails every
        # run; it must not hold the checkpoints below, or no run would be quiet again.
        retry_ids: set[str] = set()
        brought_by: dict[str, TableAPIRecord] = {}  # number -> the changed row that brought it, this page
        # A full read lists every attachment and feedback row once, not by IN chunks per page.
        everything: tuple[dict, dict, _Listing] | Exception | None = None

        def note_failed(article_data: TableAPIRecord, *, moves_checkpoint: bool, what: str, retry: bool = True) -> None:
            # The checkpoint stays before the row that brought it, so the next run retries it.
            # The re-read pass lists its articles every run, so its failures do not hold it.
            row = brought_by.get(getattr(article_data, ServiceNowFields.NUMBER, None) or "", article_data)
            synced.discard(self._row_key(row))
            failed.add(
                datetime_to_epoch_ms(row.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)
                if moves_checkpoint else None
            )
            failed_ids.add(article_data.sys_id)
            if retry:
                retry_ids.add(article_data.sys_id)
            self._sync_read_incomplete = True
            self._note_skipped(what)

        for query, moves_checkpoint, resolve in queries:
            # The re-read pass rewrites access only: its articles did not change, and their files
            # inherit from them. Their attachments are listed only where the body names an image
            # by sys_id, which the revision counts by its hash.
            access_only = query == reread_query
            listing = _Listing()
            # A failed page raises: a short list is indistinguishable from a complete one to
            # everything downstream, so the sync would report success over what it missed.
            full_read = not last_sync_time and moves_checkpoint
            async for page in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE, query,
                [ServiceNowFields.NUMBER, ServiceNowFields.SYS_MOD_COUNT] if resolve else _ARTICLE_FIELDS,
                listing=listing, after=resume_after if full_read else None,
            ):
                rows = page
                if moves_checkpoint:
                    # Pages come oldest first, so the last row is the newest seen
                    latest_update_time = page[-1].sys_updated_on
                    synced.update(map(self._row_key, page))
                    rows = [r for r in page if self._row_key(r) not in synced_before]
                    if not rows:
                        continue

                articles_data = rows
                brought_by = {r.number: r for r in rows if r.number} if resolve else {}
                if resolve:
                    numbers = set(brought_by)
                    articles_data, gone = await self._current_versions(sorted(numbers), _ARTICLE_FIELDS)
                    try:
                        await self._delete_articles(gone)
                    except Exception as e:
                        _raise_if_run_stop(e)
                        self.logger.error(f"❌ Failed to delete {len(gone)} articles with no current version: {e}")
                        for row in rows:
                            if row.number in gone:
                                note_failed(row, moves_checkpoint=moves_checkpoint, what="Articles with no current version that could not be removed, retried on the next sync: {n}")
                # A resolved row is in scope already (_current_versions); the others are checked here.
                articles_data = [a for a in articles_data if self._article_in_scope(a)]
                if not articles_data:
                    continue

                try:
                    if full_read:
                        if everything is None:
                            try:
                                attachment_listing, feedback_listing = _Listing(), _Listing()
                                everything = (
                                    await self._fetch_attachments_for_articles(None, attachment_listing),
                                    await self._feedback(None, [], feedback_listing),
                                    attachment_listing,
                                )
                                if not resume_after:
                                    feedback_count = feedback_listing.total
                            except Exception as e:
                                _raise_if_run_stop(e)
                                everything = e  # every page's articles fail, as a failed page's do
                        if isinstance(everything, Exception):
                            raise everything
                        attachments, feedback, attachment_listing = everything
                    else:
                        attachment_listing = _Listing()
                        attachments = await self._fetch_attachments_for_articles(
                            [a.sys_id for a in articles_data
                             if not access_only or _SYS_ID.search(self._article_body(a))], attachment_listing
                        )
                        # Only what the revision needs: the rows and when each last changed
                        feedback = await self._feedback(
                            [n for a in articles_data if (n := a.get(ServiceNowFields.NUMBER))], []
                        )
                except Exception as e:
                    _raise_if_run_stop(e)
                    self.logger.error(
                        f"❌ Failed to list the attachments or feedback of {len(articles_data)} articles: {e}"
                    )
                    for article_data in articles_data:
                        note_failed(article_data, moves_checkpoint=moves_checkpoint, what="Articles whose attachments or feedback could not be listed, retried on the next sync: {n}")
                    continue
                try:
                    # The page's own criteria in one read, not one per article (cached for the run).
                    await self._fetch_user_criteria(sorted({
                        c for a in articles_data for f in (
                            ServiceNowFields.CAN_READ_USER_CRITERIA, ServiceNowFields.CANNOT_READ_USER_CRITERIA
                        ) for c in self._split_ids(a.get(f))
                    }))
                except Exception as e:
                    _raise_if_run_stop(e)  # each article reads its own again, and fails on its own

                records: list[tuple[Record, list[Permission]]] = []
                processed = []

                for article_data in articles_data:
                    try:
                        updates = await self._process_single_article(
                            article_data, attachments.get(article_data.sys_id, []),
                            feedback.get(article_data.get(ServiceNowFields.NUMBER), []),
                        )
                    except _CriteriaHidden as e:
                        # Its stored grants stay. A criterion that stays hidden does not hold the
                        # checkpoint: the re-read pass lists the articles with criteria.
                        self.logger.error(f"❌ Skipping article {article_data.sys_id}, its user criteria were not read in full: {e}")
                        note_failed(article_data, moves_checkpoint=False, what="Articles whose user criteria could not be read in full, kept as they were: {n}", retry=False)
                        continue
                    except Exception as e:
                        _raise_if_run_stop(e)
                        self.logger.error(
                            f"❌ Failed to process article {getattr(article_data, 'sys_id', 'unknown')}: {e}",
                            exc_info=True,
                        )
                        note_failed(article_data, moves_checkpoint=moves_checkpoint, what="Articles that could not be synced, retried on the next sync: {n}")
                        continue
                    if updates:
                        records.extend(updates[:1] if access_only else updates)
                        processed.append(article_data)
                        total_articles_synced += 1
                        total_attachments_synced += 0 if access_only else len(updates) - 1

                if records:
                    await self.data_entities_processor.on_new_records(records)

                if attachment_listing.partial:
                    # An ACL hid attachments: what the listing lacks may still be there.
                    self._sync_read_incomplete = True
                # After the write, which moved the listed attachments under the article's number.
                for article_data in [] if access_only else processed:
                    try:
                        await self._remove_attachments_gone(
                            article_data.number, attachments.get(article_data.sys_id, []),
                            complete=not attachment_listing.partial,
                        )
                    except Exception as e:
                        _raise_if_run_stop(e)
                        self.logger.error(f"❌ Failed to remove the deleted attachments of {article_data.sys_id}: {e}")
                        note_failed(article_data, moves_checkpoint=moves_checkpoint, what="Articles whose deleted attachments could not be removed, retried on the next sync: {n}")
                if full_read and not retry_ids:
                    # Never past a row that failed: a run that stops later continues before it.
                    await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.ARTICLES, {
                        ServiceNowSyncPointKeys.FULL_RESUME_AFTER: [rows[-1].sys_updated_on, rows[-1].sys_id],
                        ServiceNowSyncPointKeys.INLINE_IMAGES: inline_checkpoint,
                        ServiceNowSyncPointKeys.FEEDBACK: feedback_checkpoint,
                        ServiceNowDictKeys.COUNT: feedback_count,
                    })
            if listing.partial:
                # An ACL hid articles from this account: a full sync did not see them, so it unlinks nothing.
                self._sync_read_incomplete = True

        # Update sync checkpoint
        if latest_update_time:
            checkpoint_ms = failed.checkpoint(
                datetime_to_epoch_ms(latest_update_time, ServiceNowDefaults.DATETIME_FORMAT)
            )
            checkpoint = datetime.fromtimestamp(checkpoint_ms / 1000, tz=timezone.utc).strftime(
                ServiceNowDefaults.DATETIME_FORMAT
            )
            keys = self._overlap_keys(synced_before | synced, checkpoint)
            if checkpoint != last_sync_time or keys != sorted(synced_before):
                await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.ARTICLES, {
                    ServiceNowSyncPointKeys.LAST_SYNC_TIME: checkpoint, ServiceNowSyncPointKeys.FULL_RESUME_AFTER: [],
                    **({ServiceNowSyncPointKeys.SYNCED_IN_OVERLAP: keys} if keys else {}),
                })

        # Their articles are not held by the article checkpoint, so a failure reads them again.
        if not retry_ids:
            for key, ids in self._scopes_to_store.items():
                await self.category_sync_point.update_sync_point(key, {ServiceNowDictKeys.IDS: ids})
            # A knowledge base whose criteria were not read kept its old grants: the next run reads them again.
            if fingerprint is not None and not self._kbs_unread:
                await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.ACCESS_FINGERPRINT, fingerprint)
            await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.INLINE_IMAGES, {
                ServiceNowSyncPointKeys.LAST_SYNC_TIME: inline_checkpoint,
                **({ServiceNowSyncPointKeys.SYNCED_IN_OVERLAP: inline_keys} if inline_keys else {}),
            })
            await self.article_sync_point.update_sync_point(ServiceNowSyncPointKeys.FEEDBACK, {
                ServiceNowSyncPointKeys.LAST_SYNC_TIME: feedback_checkpoint, ServiceNowDictKeys.COUNT: feedback_count,
                **({ServiceNowSyncPointKeys.SYNCED_IN_OVERLAP: feedback_keys} if feedback_keys else {}),
            })
        for removal in (self._remove_expired_articles, self._apply_audited_deletes):
            try:
                await removal()
            except Exception as e:
                _raise_if_run_stop(e)
                self.logger.error(f"❌ {removal.__name__} failed; it runs again next sync: {e}")
                self._note_problem(_CLEANUP_FAILED)
        if not migrated and not retry_ids:
            # This full read wrote every article's files under their key and removed the ones
            # under a sys_id. After a failure the next run reads every article again and re-keys.
            await self.article_sync_point.update_sync_point(
                ServiceNowSyncPointKeys.ATTACHMENT_KEY_V2, {ServiceNowDictKeys.DONE: True}
            )
        if await self._reconcile_articles(force=not migrated, keep=failed_ids) and not migrated:
            await self.article_sync_point.update_sync_point(
                ServiceNowSyncPointKeys.ARTICLE_KEY_V2, {ServiceNowDictKeys.DONE: True}
            )
            self.logger.info("Articles are now stored under their number")

        self.logger.info(
            f"✅ Articles synced: {total_articles_synced} articles, "
            f"{total_attachments_synced} attachments, "
            f"{failed.count} failed"
        )

    async def _access_reread_due(self, *, full: bool) -> tuple[dict | None, bool]:
        """Whether an incremental run re-reads the articles with criteria or an ownership group,
        and what to store once this run synced without failures.

        Their grants follow rows that change without the article changing (criteria, the mtom
        tables, memberships, roles, users, groups, knowledge base owners and managers, and the two
        instance properties). Each table's row count and newest sys_updated_on (one 1-row read,
        newest first) tell whether any row was added, changed or deleted since the last pass.
        The pass runs when one differs, when there is no stored state, and at least once every
        RECONCILE_INTERVAL_SECONDS. A full read covers those articles anyway and stores the state.
        """
        now = datetime.now(timezone.utc)
        try:
            tables: dict[str, list] = {}
            for table in _ACCESS_DEPENDENCIES:
                response = await self._table_get(
                    table, sysparm_query=f"ORDERBYDESC{ServiceNowFields.SYS_UPDATED_ON}",
                    sysparm_fields=ServiceNowFields.SYS_UPDATED_ON, sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_no_count=None,
                )
                tables[table] = [response.total_count,
                                 str(response.result[0].get(ServiceNowFields.SYS_UPDATED_ON) or "") if response.result else ""]
            tables["properties"] = [self.apply_article_read_criteria, self.block_kb_without_criteria]
            # A string: a graph store keeps only primitive values and lists of them, not maps
            fingerprint = json.dumps(tables, sort_keys=True)
        except ServiceNowAPIError as e:
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not tell whether article access changed; re-reading those articles: {e}")
            return None, True
        stored = await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ACCESS_FINGERPRINT) or {}
        last = stored.get(ServiceNowSyncPointKeys.LAST_SYNC_TIME)
        due = full or not last or now - datetime.strptime(last, ServiceNowDefaults.DATETIME_FORMAT).replace(
            tzinfo=timezone.utc) >= timedelta(seconds=ServiceNowDefaults.RECONCILE_INTERVAL_SECONDS)
        reread = due or stored.get(ServiceNowDictKeys.FINGERPRINT) != fingerprint
        if not reread:
            self.logger.info("No table behind article access changed; not re-reading the articles with criteria")
            return None, False
        return {
            ServiceNowDictKeys.FINGERPRINT: fingerprint,
            ServiceNowSyncPointKeys.LAST_SYNC_TIME: now.strftime(ServiceNowDefaults.DATETIME_FORMAT) if due else last,
        }, True

    async def _feedback_count(self) -> int | None:
        """How many kb_feedback rows ServiceNow counts (before ACLs), or None when it cannot tell."""
        try:
            response = await self._table_get(
                ServiceNowTables.KB_FEEDBACK, sysparm_fields=ServiceNowFields.SYS_ID,
                sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT, sysparm_no_count=None,
            )
        except ServiceNowAPIError as e:
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not count the feedback rows; a deleted one is seen on a later sync: {e}")
            return None
        return response.total_count

    async def _current_versions(self, numbers: list[str], fields: list[str]) -> tuple[list[TableAPIRecord], set[str]]:
        """The current row (see _current_article_query) of each of ``numbers`` that has one in
        scope, and the numbers that have none. A current row an ACL hides is not none: under a
        partial listing a number is gone only once ServiceNow counts no current row for it."""
        rows: list[TableAPIRecord] = []
        partial = False
        for chunk in self._chunk_ids(numbers):
            listing = _Listing()
            async for page in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE,
                f"{ServiceNowFields.NUMBER}IN{CommonStrings.COMMA.join(chunk)}^{self._current_article_query()}",
                [*fields, ServiceNowFields.KB_KNOWLEDGE_BASE, ServiceNowFields.KB_CATEGORY], listing=listing,
            ):
                rows.extend(r for r in page if self._article_in_scope(r))
            partial = partial or listing.partial
        gone = set(numbers) - {r.number for r in rows}
        if partial:
            # shortcut: a listed row out of scope then counts as there, and the reconcile removes it.
            gone = {n for n in gone if await self._gone_at_source(
                ServiceNowTables.KB_KNOWLEDGE, f"{ServiceNowFields.NUMBER}={n}^{self._current_article_query()}"
            )}
        return rows, gone

    async def _delete_articles(self, numbers: set[str]) -> None:
        """Delete the stored articles among ``numbers``, with their attachments."""
        for number in sorted(numbers):
            # Most numbers without a current version were never stored (drafts of new articles).
            if await self.data_entities_processor.get_record_by_external_id(self.connector_id, number):
                await self.data_entities_processor.delete_record_by_external_id(self.connector_id, number)

    async def _remove_attachments_gone(
        self, number: str, listed: Sequence[AttachmentMetadata], *, complete: bool = True,
    ) -> None:
        """Delete the stored attachments of article ``number`` that its attachment listing
        ``listed`` no longer has. When an ACL hid some (not ``complete``), only the ones stored
        under the sys_id of a listed file go: that file is stored under its key now."""
        keys = {key for key, _ in self._attachment_keys(number, listed)}
        sys_ids = {a.sys_id for a in listed}
        children = await self.data_entities_processor.get_records_by_parent(
            self.connector_id, number, RecordType.FILE.value
        )
        for child in children:
            if child.external_record_id not in keys and (complete or child.external_record_id in sys_ids):
                await self.data_entities_processor.on_record_deleted(child.id)

    @staticmethod
    def _attachment_keys(
        number: str, rows: Sequence[AttachmentMetadata],
    ) -> list[tuple[str, AttachmentMetadata]]:
        """Each file attachment of article ``number`` (not its inline images) with the key it is
        stored under: ``<number>/<file name>/<hash>``, or the size where ServiceNow shows no hash.

        A new version of an article copies its files with new sys_ids and the same bytes, so the
        key, unlike the sys_id, stays. A second identical file on the article gets ``#2`` and so on;
        which of the two is which does not matter, as their bytes are the same."""
        keyed: list[tuple[str, AttachmentMetadata]] = []
        seen: dict[str, int] = defaultdict(int)
        files = sorted(
            (a for a in rows if (a.table_name or ServiceNowTables.KB_KNOWLEDGE) == ServiceNowTables.KB_KNOWLEDGE),
            key=lambda a: (a.sys_created_on or "", a.sys_id),
        )
        for row in files:
            key = f"{number}/{row.file_name}/{row.hash or row.size_bytes}"
            seen[key] += 1
            keyed.append((key if seen[key] == 1 else f"{key}#{seen[key]}", row))
        return keyed

    async def _changed_articles(
        self, table: str, article_field: str, key: str, scope: str = "",
    ) -> tuple[list[str], str, list[str]]:
        """The sys_ids of the article rows that the rows of ``table`` (matching ``scope``) changed
        since the last run point at through ``article_field``, and the checkpoint ``key`` to store
        once those are synced, with its overlap keys (_overlap_keys). Before there is a checkpoint
        nothing is read: that run reads every article."""
        checkpoint, synced = await self._read_delta_point(self.article_sync_point, key)
        if not checkpoint:
            return [], self._utc_now(), []
        seen = set(synced)
        changed: set[str] = set()
        async for rows in self._iter_table(
            table, f"{scope}{ServiceNowFields.SYS_UPDATED_ON}>{self._delta_since(checkpoint)}",
            [article_field, ServiceNowFields.SYS_MOD_COUNT],
        ):
            # A row the last run synced already is read again by the overlap only.
            changed.update(str(r.get(article_field)) for r in rows
                           if r.get(article_field) and self._row_key(r) not in seen)
            synced.update(map(self._row_key, rows))
            checkpoint = rows[-1].sys_updated_on
        return sorted(changed), checkpoint, self._overlap_keys(synced, checkpoint)

    async def _reconcile_articles(self, *, force: bool, keep: set[str]) -> bool:
        """Delete the stored articles whose key is no current article's number: deleted at the
        source, which leaves no row a delta could see, or stored under a version's sys_id.
        Runs when forced, on a full sync, and otherwise at most once per
        RECONCILE_INTERVAL_SECONDS. True when it ran to the end; a listing that failed or came
        back empty deletes nothing.

        ``keep``: the sys_ids of the rows this run failed to write. Such an article is not under
        its number yet, so a record under its sys_id still holds its attachments; the run that
        writes it moves them. While such records are kept, every run reconciles.
        """
        kept_before = ((await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES_RECONCILE))
                       or {}).get(ServiceNowDictKeys.KEPT)
        last = await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.ARTICLES_RECONCILE)
        now = datetime.now(timezone.utc)
        if not force and not kept_before and last and now - datetime.strptime(
            last, ServiceNowDefaults.DATETIME_FORMAT
        ).replace(tzinfo=timezone.utc) < timedelta(seconds=ServiceNowDefaults.RECONCILE_INTERVAL_SECONDS):
            return False
        try:
            current: set[str] = set()
            out_of_scope: set[str] = set()  # listed, so known to be there, but left out by a filter
            listing = _Listing()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE, self._current_article_query(),
                [ServiceNowFields.NUMBER, ServiceNowFields.KB_KNOWLEDGE_BASE, ServiceNowFields.KB_CATEGORY],
                page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing,
            ):
                for r in rows:
                    if r.number:
                        (current if self._article_in_scope(r) else out_of_scope).add(r.number)
            # Empty is the account failing, unless a filter left everything out: rows it listed, or
            # a filtered query ServiceNow counts no row for (a count is taken before ACLs).
            filtered = self._category_filter is not None or any(
                f is not None and not f.is_empty() for f in map(self.sync_filters.get, (
                    SyncFilterKey.KNOWLEDGE_BASES, SyncFilterKey.MODIFIED, SyncFilterKey.CREATED))
            )
            if not current and not out_of_scope and not (filtered and listing.total == 0):
                self.logger.warning("The article listing came back empty; no stored article is removed")
                return False
            stored = {r.external_record_id for r in
                      await self.data_entities_processor.get_records_by_record_type(self.connector_id, RecordType.WEBPAGE)}
            kept = sorted((stored - current) & keep)
            gone = sorted(stored - current - keep)
            if listing.partial:
                # An ACL hid some current rows from this account: a key the listing lacks is
                # gone only once ServiceNow counts no current row for it.
                gone = [key for key in gone if key in out_of_scope or await self._gone_at_source(
                    ServiceNowTables.KB_KNOWLEDGE, f"{ServiceNowFields.NUMBER}={key}^{self._current_article_query()}"
                )]
            if gone:
                self.logger.info(f"Removing {len(gone)} stored articles with no current version in ServiceNow")
            for key in gone:
                await self.data_entities_processor.delete_record_by_external_id(self.connector_id, key)
        except Exception as e:
            _raise_if_run_stop(e)
            self.logger.error(f"❌ Could not reconcile the stored articles; trying again next sync: {e}")
            self._note_problem(_CLEANUP_FAILED)
            return False
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.ARTICLES_RECONCILE,
            {ServiceNowSyncPointKeys.LAST_SYNC_TIME: now.strftime(ServiceNowDefaults.DATETIME_FORMAT),
             ServiceNowDictKeys.KEPT: kept},
        )
        return True

    async def _remove_expired_articles(self) -> None:
        """Delete the articles whose ``valid_to`` passed since the last check: expiry changes no
        row, so no delta sees it. The first check only records the date; the run that makes it
        reads every article."""
        today = datetime.now(timezone.utc).strftime(ServiceNowDefaults.DATE_FORMAT)
        last = ((await self.article_sync_point.read_sync_point(ServiceNowSyncPointKeys.ARTICLES_EXPIRY)) or {}).get(
            ServiceNowSyncPointKeys.LAST_SYNC_TIME
        )
        if last == today:
            return
        if last:
            expired: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE,
                f"{ServiceNowFields.WORKFLOW_STATE}={ServiceNowFields.PUBLISHED}"
                f"^{ServiceNowFields.VALID_TO}>={last}^{ServiceNowFields.VALID_TO}<{today}",
                [ServiceNowFields.NUMBER],
            ):
                expired.update(r.number for r in rows if r.number)
            await self._delete_articles(expired)
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.ARTICLES_EXPIRY, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: today}
        )

    async def _apply_audited_deletes(self) -> None:
        """Delete the articles whose deletion sys_audit_delete recorded since the last run, without
        waiting for the reconcile. Only an admin can read that table: on a 403 this pass does
        nothing and the reconcile stays the only way. Deleted categories need no pass: every run
        lists every category id (see _sync_categories)."""
        checkpoint = await self._read_checkpoint(self.article_sync_point, ServiceNowSyncPointKeys.AUDIT_DELETE)
        if checkpoint:
            numbers: set[str] = set()
            try:
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_AUDIT_DELETE,
                    f"{ServiceNowFields.TABLENAME}={ServiceNowTables.KB_KNOWLEDGE}"
                    f"^{ServiceNowFields.SYS_UPDATED_ON}>{self._delta_since(checkpoint)}",
                    [ServiceNowFields.DISPLAY_VALUE],
                ):
                    # display_value is "<number> v<version>"
                    numbers.update(str(r.display_value).rsplit(" v", 1)[0] for r in rows if r.display_value)
                    checkpoint = rows[-1].sys_updated_on
            except ServiceNowAPIError as e:
                if e.status_code == HttpStatusCode.FORBIDDEN.value:
                    self.logger.debug("This account cannot read sys_audit_delete; the reconcile removes deleted articles")
                    return
                raise
            # A deleted old version of an article that still has a current one deletes nothing.
            await self._delete_articles((await self._current_versions(sorted(numbers), [ServiceNowFields.NUMBER]))[1])
        await self.article_sync_point.update_sync_point(
            ServiceNowSyncPointKeys.AUDIT_DELETE, {ServiceNowSyncPointKeys.LAST_SYNC_TIME: checkpoint or self._utc_now()}
        )

    async def _process_single_article(
        self, article_data: TableAPIRecord, attachments_data: list[AttachmentMetadata],
        feedback: Sequence[TableAPIRecord] = (),
    ) -> list[tuple[Record, list[Permission]]]:
        """
        The article's record and its attachments' records, each with its grants, to write.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord
            attachments_data: its attachments, listed for the whole page (_fetch_attachments_for_articles)
            feedback: its kb_feedback rows, listed for the whole page (_feedback)

        Raises:
            Exception: when the article's grants could not be read; the caller retries it next
                run instead of writing it without them.
        """
        article_record = self._transform_to_article_webpage_record(article_data, feedback, attachments_data)
        if not article_record:
            self.logger.warning(f"Failed to transform article {article_data.sys_id}")
            return []

        inherit, access_rule, permissions = await self._article_access(article_data)
        if not permissions and (not inherit or access_rule == AccessRule.RESTRICTED):
            self._no_reader += 1
        article_record.inherit_permissions = inherit
        article_record.access_rule = access_rule
        # Every write replaces the stored grants: who the criteria and groups name changes
        # without the article changing, and an article that moved keeps no grant of its old place.
        article_record.rewrite_permissions = True
        records: list[tuple[Record, list[Permission]]] = [(article_record, permissions)]

        for key, att_data in self._attachment_keys(article_record.external_record_id, attachments_data):
            # An attachment has exactly its article's readers: it inherits from the article
            # (OPEN, no grants of its own), whichever way the article's own access is written.
            att_record = self._transform_to_attachment_file_record(
                att_data,
                parent_record_group_type=article_record.record_group_type,
                parent_external_record_group_id=article_record.external_record_group_id,
                article=article_record,
                key=key,
            )
            if att_record:
                att_record.rewrite_permissions = True
                if self._rekey_attachments:
                    # The same record under its new key: its citations and links still open.
                    stored = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id, att_data.sys_id
                    )
                    if stored is not None:
                        att_record.id = stored.id
                records.append((att_record, []))

        return records

    async def _fetch_attachments_for_articles(
        self, article_sys_ids: list[str] | None, listing: _Listing | None = None,
    ) -> dict[str, list[AttachmentMetadata]]:
        """
        The attachments and inline images of a page of articles, by article sys_id: one listing
        per IN chunk, not one per article; of every article version when ``article_sys_ids`` is
        None (one listing, for a full read). ``listing`` gets the counts of every chunk added up.

        Raises:
            ServiceNowAPIError: a failed listing is not "no attachments"; read that way, a full
                sync would unlink the articles' attachments.
        """
        attachments: dict[str, list[AttachmentMetadata]] = defaultdict(list)
        tables = f"{ServiceNowFields.TABLE_NAME}IN{ServiceNowTables.KB_KNOWLEDGE},{ServiceNowTables.INLINE_IMAGE}"
        queries = [tables] if article_sys_ids is None else [
            f"{tables}^{ServiceNowFields.TABLE_SYS_ID}IN{CommonStrings.COMMA.join(chunk)}"
            for chunk in self._chunk_ids(article_sys_ids)
        ]
        for query in queries:
            chunk_listing = _Listing()
            async for rows in self._iter_table(
                ServiceNowTables.SYS_ATTACHMENT,
                query,
                [
                    ServiceNowFields.SYS_ID,
                    ServiceNowFields.FILE_NAME,
                    ServiceNowFields.CONTENT_TYPE,
                    ServiceNowFields.SIZE_BYTES,
                    ServiceNowFields.HASH,
                    ServiceNowFields.TABLE_NAME,
                    ServiceNowFields.TABLE_SYS_ID,
                    ServiceNowFields.SYS_CREATED_ON,
                    ServiceNowFields.SYS_UPDATED_ON,
                ],
                page=ServiceNowDefaults.ID_PAGE_SIZE if article_sys_ids is None else ServiceNowDefaults.BATCH_SIZE,
                listing=chunk_listing,
            ):
                for record in rows:
                    attachments[record.table_sys_id].append(AttachmentMetadata(**record.model_dump()))
            if listing is not None:
                listing.total += chunk_listing.total
                listing.seen += chunk_listing.seen
        return attachments

    async def _fetch_kb_permissions_from_criteria(
        self, kb_sys_id: str
    ) -> Dict[str, List[str]]:
        """
        Fetch the user criteria ids of a knowledge base from its four mtom tables: Can Read,
        Cannot Read, Can Contribute and Cannot Contribute.

        Args:
            kb_sys_id: Knowledge base sys_id

        Returns:
            Dict with 'read', 'cannot_read', 'write' and 'cannot_write' lists of criteria sys_ids
        """
        criteria_map: dict[str, list[str]] = {key: [] for key, _ in _KB_MTOM}
        if self._kb_mtom is not None:
            return {key: list(ids) for key, ids in self._kb_mtom.get(kb_sys_id, criteria_map).items()}

        # A failed read raises: an empty Can Read list would remove every reader of the KB,
        # and an empty Cannot Read list would grant the KB to users it denies.
        for key, table in _KB_MTOM:
            listing = _Listing()
            async for rows in self._iter_table(
                table, f"{ServiceNowFields.KB_KNOWLEDGE_BASE}={kb_sys_id}", [ServiceNowFields.USER_CRITERIA],
                listing=listing, narrow=True,
            ):
                criteria_map[key].extend(
                    r.user_criteria for r in rows if getattr(r, ServiceNowFields.USER_CRITERIA, None)
                )
            if listing.partial:
                self._sync_read_incomplete = True
                raise _CriteriaHidden(f"{table} rows of knowledge base {kb_sys_id} are hidden from this account")

        return criteria_map

    async def _load_kb_criteria(self) -> None:
        """Every knowledge base's criteria ids, one listing per mtom table, and their user_criteria
        rows in one read, instead of five reads per knowledge base. A failed read leaves each
        knowledge base to read its own (_fetch_kb_permissions_from_criteria); a run stop raises."""
        try:
            by_kb: dict[str, dict[str, list[str]]] = defaultdict(lambda: {key: [] for key, _ in _KB_MTOM})
            for key, table in _KB_MTOM:
                async for rows in self._iter_table(
                    table, None, [ServiceNowFields.KB_KNOWLEDGE_BASE, ServiceNowFields.USER_CRITERIA],
                    page=ServiceNowDefaults.ID_PAGE_SIZE,
                ):
                    for r in rows:
                        if r.get(ServiceNowFields.KB_KNOWLEDGE_BASE) and r.get(ServiceNowFields.USER_CRITERIA):
                            by_kb[str(r.get(ServiceNowFields.KB_KNOWLEDGE_BASE))][key].append(
                                str(r.get(ServiceNowFields.USER_CRITERIA))
                            )
            self._kb_mtom = dict(by_kb)
            await self._fetch_user_criteria(sorted({c for kinds in by_kb.values() for ids in kinds.values() for c in ids}))
        except ServiceNowAPIError as e:
            _raise_if_run_stop(e)
            self.logger.warning(f"Could not read the knowledge bases' criteria at once; reading them one by one: {e}")
            self._kb_mtom = None

    async def _kb_criteria(self, kb_sys_id: str) -> dict[str, list[str]]:
        """A knowledge base's criteria ids (_fetch_kb_permissions_from_criteria), read once per
        run. A knowledge base with no Can Read and no Can Contribute criteria is readable by every
        user where the instance does not block that (see _resolve_block_no_criteria)."""
        if kb_sys_id not in self._kb_criteria_cache:
            read = await self._fetch_kb_permissions_from_criteria(kb_sys_id)
            criteria = {k: list(read.get(k) or []) for k in (
                ServiceNowDictKeys.READ, ServiceNowDictKeys.CANNOT_READ, ServiceNowDictKeys.WRITE, ServiceNowDictKeys.CANNOT_WRITE
            )}
            if not self.block_kb_without_criteria and not (
                criteria[ServiceNowDictKeys.READ] or criteria[ServiceNowDictKeys.WRITE]
            ):
                criteria[ServiceNowDictKeys.READ] = [_EVERYONE]
            self._kb_criteria_cache[kb_sys_id] = criteria
        return self._kb_criteria_cache[kb_sys_id]

    async def _process_criteria_permissions(
        self, criteria_ids: list[str], permission_type: PermissionType,
        deny_criteria_ids: list[str] | None = None,
    ) -> list[Permission]:
        """
        The grants for "matches any of ``criteria_ids`` and none of ``deny_criteria_ids``".

        The grant model has no deny and no AND. Without a deny and without a Match All over more
        than one field, each criterion becomes a grant per user, group, role, department, location
        and company it names (groups and roles keep up with their members), or an ORG grant when
        it matches everyone. Otherwise the users are computed here (allowed minus denied) and
        granted one by one. A failed read raises _CriteriaUnreadable, so the caller keeps the
        stored grants: unknown is neither allowed nor empty.
        """
        try:
            if not criteria_ids:
                return []

            # Deny rows first: unknown deny must never read as "no deny".
            deny_records = await self._fetch_deny_records(deny_criteria_ids or [])
            can_records = self._allow_rows(await self._fetch_user_criteria(criteria_ids))

            if not deny_records and not any(self._needs_user_resolution(r) for r in can_records):
                if any(self._is_everyone(r) for r in can_records):
                    return [Permission(entity_type=EntityType.ORG, type=permission_type)]
                # Users by email; groups, roles, departments, locations and companies are user groups.
                grants: list[Permission] = []
                for record in can_records:
                    grants += await self._users_to_permissions(
                        set(self._split_ids(getattr(record, ServiceNowFields.USER, None))), permission_type
                    )
                    grants += [
                        Permission(external_id=i, type=permission_type, entity_type=EntityType.GROUP)
                        for dim in self._CRITERIA_DIMENSIONS if dim != ServiceNowFields.USER
                        for i in self._split_ids(getattr(record, dim, None))
                    ]
                return grants

            users = await self._users_allowed_by(can_records, deny_records)
            return await self._users_to_permissions(users, permission_type)

        except _CriteriaUnreadable:
            raise
        except Exception as e:
            self.logger.error(f"Failed to process criteria permissions: {e}", exc_info=True)
            raise _CriteriaUnreadable(str(e)) from e

    async def _fetch_deny_records(self, deny_ids: list[str]) -> list[TableAPIRecord]:
        """Active Cannot Read / Cannot Contribute user_criteria rows. A failed read raises
        _CriteriaUnreadable. An unedited Guest User row that names no synced user changes nobody's
        access here, so it is left out (the KB can then stay one org grant)."""
        try:
            return [
                r for r in await self._fetch_user_criteria(deny_ids)
                if self._criteria_is_active(r) and not (
                    self._is_guest(r)
                    and not await self._users_matching_criteria(r, deny=True) & set(await self._emails_by_source_id())
                )
            ]
        except Exception as e:
            raise _CriteriaUnreadable(str(e)) from e

    def _is_guest(self, record: TableAPIRecord) -> bool:
        """The out-of-box Guest User script, unedited, with no field but the user: the script
        matches exactly the user named guest, so it is evaluated here (see _guest_ids). An edited
        script, or another field, takes the normal path."""
        return (
            " ".join(str(getattr(record, ServiceNowFields.SCRIPT, None) or "").split())
            == " ".join(ServiceNowDefaults.GUEST_USER_CRITERIA_SCRIPT.split())
            and set(self._dims(record)) <= {ServiceNowFields.USER}
        )

    async def _guest_ids(self) -> set[str]:
        """The sys_id of the user named guest (``GlideUser.getUser("guest")``), read once per run.
        Raises if the read fails."""
        if self._guest_cache is None:
            ids: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.SYS_USER, f"{ServiceNowFields.USER_NAME}={ServiceNowDefaults.GUEST_USER_NAME}",
                [ServiceNowFields.SYS_ID], narrow=True,
            ):
                ids.update(r.sys_id for r in rows if getattr(r, "sys_id", None))
            self._guest_cache = ids
        return self._guest_cache

    async def _users_allowed_by(self, can_records: list[TableAPIRecord], deny_records: list[TableAPIRecord]) -> set[str]:
        """Users matching any allow row minus users matching any deny row. A deny whose reach is
        unknown denies everyone it may cover; an allow that cannot be resolved raises."""
        denied: set[str] = set()
        for record in deny_records:
            try:
                users = None if self._deny_reach_unknown(record) else await self._users_matching_criteria(record, deny=True)
            except Exception as e:
                raise _CriteriaUnreadable(str(e)) from e
            if users is None:
                if not self._deny_reach_unknown(record):
                    self._note_criteria("partial", record.sys_id)
                # Whom a scripted, unreadable or partly hidden Cannot Read denies is unknown, so it may
                # deny anyone: nobody is allowed through criteria (privileged readers are added by the callers).
                self.logger.warning(f"Cannot Read / Cannot Contribute {record.sys_id} cannot be evaluated; denying everyone it may cover")
                return set()
            denied |= users
        allowed: set[str] = set()
        for record in can_records:
            allowed |= await self._users_matching_criteria(record) or set()
        return allowed - denied

    async def _effective_users(self, allow_ids: list[str], deny_ids: list[str]) -> set[str]:
        deny_records = await self._fetch_deny_records(deny_ids)
        can_records = self._allow_rows(await self._fetch_user_criteria(allow_ids))
        return await self._users_allowed_by(can_records, deny_records)

    async def _all_app_users(self) -> list[AppUser]:
        """The connector's users, read once per sync run (after the user sync) for groups and grants."""
        if self._app_users is None:
            # Raising: an empty list would write every group with no members.
            self._app_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id, raise_on_error=True
            )
        return self._app_users

    async def _emails_by_source_id(self) -> dict[str, str]:
        # One user listing per sync run, not a lookup per user or per article.
        if self._email_by_source_id is None:
            self._email_by_source_id = {
                u.source_user_id: u.email for u in await self._all_app_users() if u.source_user_id and u.email
            }
        return self._email_by_source_id

    async def _users_to_permissions(self, users: set[str], permission_type: PermissionType) -> list[Permission]:
        email_by_id = await self._emails_by_source_id()
        return [
            Permission(email=email_by_id[u], type=permission_type, entity_type=EntityType.USER)
            for u in sorted(users) if u in email_by_id
        ]

    async def _article_access(self, article_data: TableAPIRecord) -> tuple[bool, AccessRule, list[Permission]]:
        """(inherit, access rule, grants) of an article: who reads it is, as ServiceNow decides it,

            PRIV ∪ OG ∪ CONTRIB ∪ ((KR ∩ AR) − AD)

        PRIV: admins, knowledge admins, the KB owner and managers. OG: the members and manager of
        the article's active ownership group (they do not get the KB). KR: KB Can Read minus KB
        Cannot Read. AR: the article's Can Read (everyone when it has none). AD: its Cannot Read.
        CONTRIB: KB Can Contribute minus Cannot Contribute, held to AR and AD only when
        glide.knowman.apply_article_read_criteria is true. (ServiceNow docs, "Managing access to
        knowledge bases and knowledge articles":
        https://www.servicenow.com/docs/r/servicenow-platform/knowledge-management/user-access-knowledge.html)

        The KB's own grants are PRIV ∪ KR ∪ CONTRIB, so:
        - no criteria of its own: inherit, plus the ownership group (a union);
        - Can Read only: RESTRICTED (inherit and a grant): AR ∪ PRIV, and the contributors when the
          property is false. Intersected with the KB's readers this is the rule above;
        - Cannot Read, or Can Read and an ownership group: no inheritance, the users computed.
        Any unreadable rule raises, so the article is skipped and its stored grants stay.
        """
        own = self._split_ids(getattr(article_data, ServiceNowFields.CAN_READ_USER_CRITERIA, None))
        deny = self._split_ids(getattr(article_data, ServiceNowFields.CANNOT_READ_USER_CRITERIA, None))
        group = self._active_group(getattr(article_data, ServiceNowFields.OWNERSHIP_GROUP, None))
        kb_id = article_data.kb_knowledge_base
        if not own and not deny:
            return True, AccessRule.OPEN, await self._ownership_grants(group)
        if not deny and group is None:
            grants = await self._process_criteria_permissions(own, PermissionType.READ)
            grants += await self._users_to_permissions(await self._kb_privileged_users(kb_id), PermissionType.READ)
            if not self.apply_article_read_criteria:
                grants += await self._kb_contributor_grants(kb_id)
            return True, AccessRule.RESTRICTED, grants

        criteria = await self._kb_criteria(kb_id)
        held = deny if self.apply_article_read_criteria else []
        readers = await self._effective_users(criteria[ServiceNowDictKeys.READ], criteria[ServiceNowDictKeys.CANNOT_READ] + deny)
        contributors = await self._effective_users(
            criteria[ServiceNowDictKeys.WRITE], criteria[ServiceNowDictKeys.CANNOT_WRITE] + held
        )
        if own:
            article_readers = await self._effective_users(own, [])
            readers &= article_readers
            if self.apply_article_read_criteria:
                contributors &= article_readers
        users = readers | contributors | await self._kb_privileged_users(kb_id)
        if group is not None:
            members, _ = await self._members_of(ServiceNowFields.GROUP, [group.sys_id])
            users |= members | set(self._split_ids(group.manager))
        return False, AccessRule.OPEN, await self._users_to_permissions(users, PermissionType.READ)

    def _active_group(self, group_id: object) -> SysUserGroup | None:
        """The synced, active sys_user_group ``group_id``; None otherwise (an inactive group grants nothing)."""
        group = self._groups.get(str(group_id or ""))
        return group if group is not None and self._is_active(group) else None

    async def _ownership_grants(self, group: SysUserGroup | None) -> list[Permission]:
        """An article's ownership group reads it in every state, and so does the group's manager."""
        if group is None:
            return []
        return [
            Permission(external_id=group.sys_id, type=PermissionType.READ, entity_type=EntityType.GROUP),
            *await self._users_to_permissions(set(self._split_ids(group.manager)), PermissionType.READ),
        ]

    async def _kb_contributor_grants(self, kb_id: str) -> list[Permission]:
        """The KB's contributors (Can Contribute minus Cannot Contribute) as READ grants, per run."""
        if kb_id not in self._kb_contributor_cache:
            criteria = await self._kb_criteria(kb_id)
            self._kb_contributor_cache[kb_id] = await self._process_criteria_permissions(
                criteria[ServiceNowDictKeys.WRITE], PermissionType.READ,
                deny_criteria_ids=criteria[ServiceNowDictKeys.CANNOT_WRITE],
            )
        return self._kb_contributor_cache[kb_id]

    async def _kb_privileged_users(self, kb_id: str | None) -> set[str]:
        """Source ids of the admin users plus the KB's owner and managers (read with the KB in
        _sync_knowledge_bases, else here). Raises if unreadable, so nothing is cached and the
        article keeps its stored grants."""
        if self._admin_source_ids is None:
            self._admin_source_ids = await self._get_admin_users()
        people = set(self._admin_source_ids)
        if kb_id and kb_id not in self._kb_owner_cache:
            ids: set[str] = set()
            async for rows in self._iter_table(
                ServiceNowTables.KB_KNOWLEDGE_BASE,
                f"{ServiceNowFields.SYS_ID}={kb_id}",
                [ServiceNowFields.OWNER, ServiceNowFields.KB_MANAGERS],
            ):
                for row in rows:
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.OWNER, None)))
                    ids |= set(self._split_ids(getattr(row, ServiceNowFields.KB_MANAGERS, None)))
            self._kb_owner_cache[kb_id] = ids
        return people | self._kb_owner_cache.get(kb_id, set())

    _CRITERIA_DIMENSIONS = (
        ServiceNowFields.USER, ServiceNowFields.GROUP, ServiceNowFields.ROLE,
        ServiceNowFields.DEPARTMENT, ServiceNowFields.LOCATION, ServiceNowFields.COMPANY,
    )

    @staticmethod
    def _split_ids(value: object) -> list[str]:
        return [s.strip() for s in str(value or "").split(",") if s.strip()]

    @staticmethod
    def _criteria_is_active(record: TableAPIRecord) -> bool:
        return str(getattr(record, ServiceNowFields.ACTIVE, "true")).lower() != "false"

    def _dims(self, record: TableAPIRecord) -> list[str]:
        """The populated user, group, role, department, location and company fields."""
        return [d for d in self._CRITERIA_DIMENSIONS if self._split_ids(getattr(record, d, None))]

    @staticmethod
    def _match_all(record: TableAPIRecord) -> bool:
        return str(getattr(record, ServiceNowFields.MATCH_ALL, "false")).lower() == "true"

    @staticmethod
    def _unreadable(record: TableAPIRecord) -> bool:
        """A row this account could not read (see _fetch_user_criteria)."""
        return getattr(record, "unreadable", None) == "true"

    @staticmethod
    def _has_script(record: TableAPIRecord) -> bool:
        """ServiceNow runs a criterion's script whenever it sets ``answer``, whatever ``advanced``
        says. The field's default is a comment that sets nothing, so a script that is only
        comments and whitespace is no script; anything else counts as one."""
        return bool(_SCRIPT_COMMENTS.sub("", str(getattr(record, ServiceNowFields.SCRIPT, None) or "")).strip())

    def _is_has_role(self, record: TableAPIRecord) -> bool:
        """The out-of-box "Has atleast one role apart from snc_internal KB" criterion, unedited (its
        script, no field): the script is evaluated here (see _has_role_users). Any other is not."""
        return (
            record.sys_id == ServiceNowDefaults.HAS_ROLE_CRITERIA
            and not self._dims(record)
            and " ".join(str(getattr(record, ServiceNowFields.SCRIPT, None) or "").split())
            == " ".join(ServiceNowDefaults.HAS_ROLE_CRITERIA_SCRIPT.split())
        )

    def _has_unevaluated_script(self, record: TableAPIRecord) -> bool:
        return self._has_script(record) and not self._is_has_role(record) and not self._is_guest(record)

    def _has_unevaluable_condition(self, record: TableAPIRecord) -> bool:
        """A script, or a row that could not be read: conditions not evaluated here."""
        return self._unreadable(record) or self._has_script(record)

    def _is_everyone(self, record: TableAPIRecord) -> bool:
        """An active criterion with no field and no script matches every user (OOB "Any User")."""
        return self._criteria_is_active(record) and not self._has_unevaluable_condition(record) and not self._dims(record)

    def _deny_reach_unknown(self, record: TableAPIRecord) -> bool:
        """Whom a deny matches cannot be told. A script is OR'd with the fields (so it may reach
        anyone), except under Match All with fields, where it is AND'd: the field matches are then
        a superset of the users it denies."""
        if self._unreadable(record):
            return True
        return self._has_unevaluated_script(record) and not (self._match_all(record) and self._dims(record))

    def _allow_rows(self, records: list[TableAPIRecord]) -> list[TableAPIRecord]:
        """The active Can Read / Can Contribute rows that may grant. A condition not evaluated here
        counts as not met: under Match All the row grants nobody, under Match Any only its
        evaluated fields grant (they are all the evaluation below reads). A row that could not be
        read raises _CriteriaHidden: whom it grants is unknown, which is not "nobody"."""
        hidden = [r.sys_id for r in records if self._criteria_is_active(r) and self._unreadable(r)]
        if hidden:
            raise _CriteriaHidden(f"user criteria {', '.join(hidden)} could not be read")
        return [
            r for r in records
            if self._criteria_is_active(r)
            and not (self._match_all(r) and self._has_unevaluated_script(r))
        ]

    def _needs_user_resolution(self, record: TableAPIRecord) -> bool:
        return (
            (self._match_all(record) and len(self._dims(record)) > 1)
            or self._is_has_role(record) or self._is_guest(record)
        )

    async def _fetch_user_criteria(self, criteria_ids: list[str]) -> list[TableAPIRecord]:
        """user_criteria rows by sys_id, each read once per sync run. Raises on an API error.

        An id the listing does not return is deleted when ServiceNow counts no row for it, and
        then left out. Otherwise an ACL hid it: it comes back as an unreadable row, which grants
        nobody and, as a deny, denies everyone it may cover; the full sync unlinks nothing.
        """
        missing = [c for c in dict.fromkeys(criteria_ids) if c not in self._criteria_rows]
        fields = [
            ServiceNowFields.SYS_ID, ServiceNowFields.ACTIVE, ServiceNowFields.MATCH_ALL,
            ServiceNowFields.ADVANCED, ServiceNowFields.SCRIPT, *self._CRITERIA_DIMENSIONS,
        ]
        for chunk in self._chunk_ids(missing):
            async for page in self._iter_table(
                ServiceNowTables.USER_CRITERIA,
                f"{ServiceNowFields.SYS_ID}IN{CommonStrings.COMMA.join(chunk)}",
                fields,
                narrow=True,
            ):
                for r in page:
                    # A field ACL leaves the key out (an empty field it shows is ""): read without
                    # it, the row could pass for "Any User".
                    shown = r.model_dump()
                    hidden = any(f not in shown for f in fields)
                    if hidden:
                        self.logger.warning(f"User criteria {r.sys_id} has fields hidden from this account; it grants nobody and denies everyone")
                        self._note_criteria("hidden", r.sys_id)
                        self._sync_read_incomplete = True
                    self._criteria_rows[r.sys_id] = TableAPIRecord(
                        sys_id=r.sys_id, active=shown.get(ServiceNowFields.ACTIVE, "true"), unreadable="true"
                    ) if hidden else r
        for c in missing:
            row = self._criteria_rows.get(c)
            if row is not None and self._has_unevaluated_script(row):
                self._note_criteria("scripted", c)
            if c in self._criteria_rows:
                continue
            if await self._gone_at_source(ServiceNowTables.USER_CRITERIA, f"{ServiceNowFields.SYS_ID}={c}"):
                self._criteria_rows[c] = None  # deleted: not asked for again this run
            else:
                self.logger.warning(f"User criteria {c} is hidden from this account; it grants nobody and denies everyone")
                self._note_criteria("hidden", c)
                self._criteria_rows[c] = TableAPIRecord(sys_id=c, active="true", unreadable="true")
                self._sync_read_incomplete = True
        return [r for c in dict.fromkeys(criteria_ids) if (r := self._criteria_rows.get(c)) is not None]

    async def _with_child_groups(self, group_ids: list[str], listings: list[_Listing] | None = None) -> list[str]:
        """The groups and all their active descendants (sys_user_group.parent): ServiceNow group
        criteria match the members of child groups, and an inactive group matches nobody.
        Raises on any read error. ``listings`` gets how each read went."""
        # Not from a group known to be inactive: its active children would match through it. An
        # unknown one is kept, so a deny that names a hidden group still fails closed.
        group_ids = [g for g in group_ids if g not in self._groups or self._is_active(self._groups[g])]
        seen = set(group_ids)
        frontier = list(group_ids)
        while frontier:
            children: list[str] = []
            for chunk in self._chunk_ids(frontier):
                listing = _Listing()
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_USER_GROUP,
                    f"{ServiceNowFields.PARENT}IN{CommonStrings.COMMA.join(chunk)}^{ServiceNowFields.ACTIVE}=true",
                    [ServiceNowFields.SYS_ID],
                    listing=listing, narrow=True,
                ):
                    children.extend(r.sys_id for r in rows if getattr(r, "sys_id", None))
                if listings is not None:
                    listings.append(listing)
            frontier = [c for c in children if c not in seen]
            seen.update(frontier)
        return sorted(seen)

    async def _users_matching_criteria(self, record: TableAPIRecord, *, deny: bool = False) -> set[str] | None:
        """sys_ids of the active users one user_criteria row matches, read once per run. Raises if
        any read fails.

        Populated fields are OR'd, or AND'd under Match All; values inside one field are OR'd.
        A group includes its active child groups; departments, locations and companies match
        exactly. A role matches through sys_user_has_role, which already has a row for each role
        a user gets from a group or a containing role. No field and no script: every user. A
        script is not evaluated here (see _allow_rows and _deny_reach_unknown). As a deny, None
        when an ACL hid some of the rows read: whom it denies is then unknown.
        """
        key = (record.sys_id, deny)
        if key in self._criteria_users:
            return self._criteria_users[key]
        if self._is_everyone(record):
            self._criteria_users[key] = set(await self._emails_by_source_id())  # the same set for every article
            return self._criteria_users[key]
        if self._is_has_role(record):
            users = await self._has_role_users()
            # Unknown (the plugin state could not be read): nobody as an allow, everyone as a deny.
            self._criteria_users[key] = None if users is None and deny else (users or set())
            return self._criteria_users[key]
        partial = False
        per_dimension: list[set[str]] = []
        for dim in self._dims(record):
            ids = self._split_ids(getattr(record, dim, None))
            if dim == ServiceNowFields.USER:
                per_dimension.append(set(ids))
                continue
            users, hidden = await self._members_of(dim, ids)
            partial |= hidden
            per_dimension.append(users)
        if self._is_guest(record):
            per_dimension.append(await self._guest_ids())  # its script, OR'd or AND'd like a field
        if not per_dimension:
            matched: set[str] = set()  # a script and no field: not evaluated here, so nobody
        elif self._match_all(record):
            matched = set.intersection(*per_dimension)
        else:
            matched = set.union(*per_dimension)
        result = None if deny and partial else matched
        self._criteria_users[key] = result
        return result

    async def _members_of(self, dim: str, ids: list[str]) -> tuple[set[str], bool]:
        """The active users that the groups (with their active child groups), roles, departments,
        locations or companies ``ids`` hold, for the criteria field ``dim``, and whether an ACL hid
        some of the rows they come from. From this run's own listings when it made them (see
        _held), else read here. Raises if a read fails."""
        if dim in self._held:
            members, hidden = self._held[dim]
            return set().union(*(members.get(i, ()) for i in ids)), hidden
        active = f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true"
        table, filter_field, user_field, condition = {
            ServiceNowFields.GROUP: (
                ServiceNowTables.SYS_USER_GRMEMBER, ServiceNowFields.GROUP, ServiceNowFields.USER,
                f"{active}^{ServiceNowFields.GROUP}.{ServiceNowFields.ACTIVE}=true",
            ),
            ServiceNowFields.ROLE: (
                ServiceNowTables.SYS_USER_HAS_ROLE, ServiceNowFields.ROLE, ServiceNowFields.USER,
                f"{active}^{ServiceNowFields.STATE}={ServiceNowFields.ACTIVE}",
            ),
        }.get(dim) or (ServiceNowTables.SYS_USER, dim, ServiceNowFields.SYS_ID, f"{ServiceNowFields.ACTIVE}=true")
        listings: list[_Listing] = []
        if dim == ServiceNowFields.GROUP:
            ids = await self._with_child_groups(ids, listings)
        users: set[str] = set()
        for chunk in self._chunk_ids(ids):
            listing = _Listing()
            async for rows in self._iter_table(
                table, f"{filter_field}IN{CommonStrings.COMMA.join(chunk)}^{condition}", [user_field],
                page=ServiceNowDefaults.ID_PAGE_SIZE, listing=listing, narrow=True,
            ):
                users.update(str(getattr(r, user_field)) for r in rows if getattr(r, user_field, None))
            listings.append(listing)
        return users, any(listing.partial for listing in listings)

    async def _has_role_users(self) -> set[str] | None:
        """The active users the out-of-box "Has a role" criterion matches, per run. Its script: a
        user with at least one sys_user_has_role row (inherited rows count, whatever their state),
        or, while the plugin com.glide.explicit_roles is active, one with snc_internal and at least
        two rows. None when the plugin's state could not be read."""
        if self._has_role_users_cache is None:
            try:
                plugin = await self._table_get(
                    ServiceNowTables.V_PLUGIN,
                    sysparm_query=f"{ServiceNowFields.ID}={ServiceNowDefaults.EXPLICIT_ROLES_PLUGIN}",
                    sysparm_fields=ServiceNowFields.ACTIVE, sysparm_limit=ServiceNowDefaults.DEFAULT_LIMIT,
                    sysparm_no_count=None,
                )
            except ServiceNowAPIError as e:
                _raise_if_run_stop(e)
                self.logger.warning(f"Could not read whether {ServiceNowDefaults.EXPLICIT_ROLES_PLUGIN} is active: {e}")
                return None
            if plugin.total_count is None or (plugin.total_count and not plugin.result):
                return None  # hidden from this account
            explicit = any(str(r.get(ServiceNowFields.ACTIVE)) == ServiceNowFields.ACTIVE for r in plugin.result)
            roles = self._role_names  # this run's role listing, when it made one (see _sync_roles)
            if roles is None:
                roles = defaultdict(list)
                async for rows in self._iter_table(
                    ServiceNowTables.SYS_USER_HAS_ROLE, f"{ServiceNowFields.USER}.{ServiceNowFields.ACTIVE}=true",
                    [ServiceNowFields.USER, ServiceNowFields.ROLE_NAME], page=ServiceNowDefaults.ID_PAGE_SIZE,
                ):
                    for r in rows:
                        if r.get(ServiceNowFields.USER):
                            roles[str(r.get(ServiceNowFields.USER))].append(str(r.get(ServiceNowFields.ROLE_NAME) or ""))
            self._has_role_users_cache = {
                user for user, names in roles.items()
                if (ServiceNowRoles.SNC_INTERNAL in names and len(names) >= 2 if explicit else len(names) >= 1)
            }
        return self._has_role_users_cache

    async def _transform_to_app_user(
        self, user_data: TableAPIRecord
    ) -> Optional[AppUser]:
        """
        Transform ServiceNow user to AppUser entity.

        Args:
            user_data: ServiceNow sys_user TableAPIRecord

        Returns:
            AppUser: Transformed user entity or None if invalid
        """
        try:
            sys_id = user_data.sys_id
            email = (user_data.email or "").strip()
            user_name = user_data.user_name or ""
            first_name = user_data.first_name or ""
            last_name = user_data.last_name or ""

            if not sys_id or not email:
                return None

            # Build full name
            full_name = f"{first_name} {last_name}".strip()
            if not full_name:
                full_name = user_name or email

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if user_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(user_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if user_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(user_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            app_user = AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=sys_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=full_name,
                is_active=self._is_active(user_data),
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return app_user

        except Exception as e:
            self.logger.error(f"Error transforming user {getattr(user_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            self._sync_read_incomplete = True  # not written, so a full sync must not unlink it
            return None

    def _transform_to_user_group(
        self, group_data: SysUserGroup
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow group to AppUserGroup entity.

        Args:
            group_data: ServiceNow SysUserGroup Pydantic model

        Returns:
            AppUserGroup: Transformed user group entity or None if invalid
        """
        try:
            sys_id = group_data.sys_id
            name = group_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if group_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(group_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if group_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(group_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Create AppUserGroup (for user groups, not record groups)
            user_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=name,
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return user_group

        except Exception as e:
            self.logger.error(f"Error transforming group {getattr(group_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            self._sync_read_incomplete = True
            return None

    def _transform_to_organizational_group(
        self, entity_data: TableAPIRecord, prefix: str
    ) -> Optional[AppUserGroup]:
        """
        Transform ServiceNow organizational entity to AppUserGroup.

        This is a generic transform method for companies, departments and locations.

        Args:
            entity_data: ServiceNow entity TableAPIRecord (company, department, location)
            prefix: Name prefix (COMPANY_, DEPARTMENT_, LOCATION_)

        Returns:
            AppUserGroup: Transformed organizational group or None if invalid
        """
        try:
            sys_id = entity_data.sys_id
            name = entity_data.name or ""

            if not sys_id or not name:
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if entity_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(
                    entity_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT
                )
            if entity_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(
                    entity_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT
                )

            # Create AppUserGroup with prefix
            org_group = AppUserGroup(
                app_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                source_user_group_id=sys_id,
                name=f"{prefix}{name}",
                description=f"ServiceNow {prefix.rstrip('_')}: {name}",
                org_id=self.data_entities_processor.org_id,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return org_group

        except Exception as e:
            self.logger.error(
                f"Error transforming organizational entity {getattr(entity_data, 'sys_id', 'unknown')}: {e}",
                exc_info=True,
            )
            self._sync_read_incomplete = True
            return None

    def _transform_to_kb_record_group(
        self, kb_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow knowledge base to RecordGroup entity.

        Args:
            kb_data: ServiceNow kb_knowledge_base TableAPIRecord

        Returns:
            RecordGroup: Transformed KB as RecordGroup with type SERVICENOW or None if invalid
        """
        try:
            sys_id = kb_data.sys_id
            # An untitled KB is still written: its categories and articles hang under it.
            title = kb_data.title or sys_id

            if not sys_id:
                self.logger.warning("KB missing sys_id")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if kb_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(kb_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if kb_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(kb_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_BASE.format(
                    instance_url=self.instance_url,
                    portal=self.kb_portal_suffix,
                    sys_id=sys_id,
                )

            # Create RecordGroup for Knowledge Base
            kb_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=title,
                description=kb_data.description or "",
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOWKB,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

            return kb_record_group

        except Exception as e:
            self.logger.error(f"Error transforming KB {getattr(kb_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            self._sync_read_incomplete = True
            return None

    def _transform_to_category_record_group(
        self, category_data: TableAPIRecord
    ) -> Optional[RecordGroup]:
        """
        Transform ServiceNow kb_category to RecordGroup entity.

        Args:
            category_data: ServiceNow kb_category TableAPIRecord

        Returns:
            RecordGroup: Transformed category as RecordGroup with type SERVICENOW_CATEGORY or None if invalid
        """
        try:
            sys_id = category_data.sys_id
            # An unlabelled category is still written: its articles hang under it (see _category_ids).
            label = category_data.label or category_data.value or sys_id
            parent_sys_id = category_data.parent_id

            if not sys_id:
                self.logger.warning("Category missing sys_id")
                return None

            # Parse timestamps
            source_created_at = None
            source_updated_at = None
            if category_data.sys_created_on:
                source_created_at = datetime_to_epoch_ms(category_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
            if category_data.sys_updated_on:
                source_updated_at = datetime_to_epoch_ms(category_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

            # Construct web URL
            web_url = None
            if self.instance_url:
                web_url = ServiceNowURLPatterns.KB_CATEGORY.format(
                    instance_url=self.instance_url,
                    portal=self.kb_portal_suffix,
                    sys_id=sys_id,
                )

            # Create RecordGroup for Category
            category_record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=label,
                short_name=category_data.value or "",
                parent_external_group_id=parent_sys_id,
                external_group_id=sys_id,
                connector_name=Connectors.SERVICENOW,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SERVICENOW_CATEGORY,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                inherit_permissions=True,
            )

            return category_record_group

        except Exception as e:
            self.logger.error(f"Error transforming category {getattr(category_data, 'sys_id', 'unknown')}: {e}", exc_info=True)
            self._sync_read_incomplete = True
            return None

    def _apply_indexing_filter(self, record: Record, key: IndexingFilterKey) -> None:
        """Keep a record out of indexing when its indexing filter is off, or manual indexing is on.
        It is still synced, with its grants. A record indexed already stays indexed (the processor
        keeps COMPLETED), so the toggle applies to new and changed records."""
        if not self.indexing_filters.is_enabled(key):
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

    def _transform_to_article_webpage_record(
        self, article_data: TableAPIRecord, feedback: Sequence[TableAPIRecord] = (),
        attachments: Sequence[AttachmentMetadata] = (),
    ) -> Optional[WebpageRecord]:
        """
        Transform ServiceNow kb_knowledge article to WebpageRecord entity.

        Args:
            article_data: ServiceNow kb_knowledge TableAPIRecord
            feedback: the article's kb_feedback rows (any fields), for the revision
            attachments: this version's attachments and inline images, for the revision

        Returns:
            WebpageRecord: Transformed article or None if invalid
        """
        sys_id = article_data.sys_id
        images = {a.sys_id: a.hash or a.size_bytes for a in attachments}
        # The record is the article, not this version of it (see _sync_articles)
        number = getattr(article_data, ServiceNowFields.NUMBER, None) or ""
        short_description = article_data.short_description or number

        if not sys_id or not number:
            self.logger.warning(f"Article {sys_id} has no sys_id or number")
            return None

        # Parse timestamps
        source_created_at = None
        source_updated_at = None
        if article_data.sys_created_on:
            source_created_at = datetime_to_epoch_ms(article_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
        if article_data.sys_updated_on:
            source_updated_at = datetime_to_epoch_ms(article_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

        # Construct web URL
        web_url = None
        if self.instance_url:
            web_url = ServiceNowURLPatterns.KB_ARTICLE.format(
                instance_url=self.instance_url,
                portal=self.kb_portal_suffix,
                number=number,
            )

        # Extract category sys_id for external_record_group_id. Fall back to the KB when
        # the category is empty or this run did not sync it (see _sync_categories): an
        # article under a stub group named by its sys_id would reach nobody.
        kb_category_sys_id = article_data.kb_category
        if self._category_ids is not None and kb_category_sys_id not in self._category_ids:
            kb_category_sys_id = None
        external_record_group_id = None
        record_group_type = None

        # Try category first
        if kb_category_sys_id:
            external_record_group_id = kb_category_sys_id
            record_group_type = RecordGroupType.SERVICENOW_CATEGORY
        else:
            # Fallback to KB if no category
            kb_sys_id = article_data.kb_knowledge_base
            if kb_sys_id:
                external_record_group_id = kb_sys_id
                record_group_type = RecordGroupType.SERVICENOWKB
            else:
                # No category and no KB - skip this article
                self.logger.warning(f"Article {sys_id} has no category and no KB - skipping")
                return None

        # Create WebpageRecord for Article; its access is set by _process_single_article
        record_id = str(uuid.uuid4())
        article_record = WebpageRecord(
            id=record_id,
            external_record_id=number,
            # The processor re-queues a stored record for indexing only when this differs
            # from what it holds: a hash of what the article streams (_fetch_article_content).
            # The body names its images by sys_id, which a new version's copy changes, so the
            # version's own images count by their bytes; attachments are records of their own.
            external_revision_id=self._content_revision(
                _SYS_ID.sub(lambda m: images.get(m.group(0), m.group(0)), self._article_body(article_data)),
                sorted((row.sys_id, row.get(ServiceNowFields.SYS_UPDATED_ON)) for row in feedback),
            ),
            version=0,
            record_name=short_description,
            record_type=RecordType.WEBPAGE,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SERVICENOW,
            connector_id=self.connector_id,
            record_group_type=record_group_type,  # CATEGORY or KB
            external_record_group_id=external_record_group_id,  # Category or KB sys_id
            parent_external_record_id=None,
            weburl=web_url,
            mime_type=MimeTypes.BLOCKS.value,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
            preview_renderable=False,
        )
        self._apply_indexing_filter(article_record, IndexingFilterKey.KNOWLEDGE_BASE)
        return article_record

    def _transform_to_attachment_file_record(
        self,
        attachment_data: AttachmentMetadata,
        parent_record_group_type: Optional[RecordGroupType] = None,
        parent_external_record_group_id: Optional[str] = None,
        *,
        inherit_permissions: bool = True,
        article: WebpageRecord | None = None,
        key: str | None = None,
    ) -> Optional[FileRecord]:
        """
        Transform ServiceNow sys_attachment to FileRecord entity.

        Args:
            attachment_data: ServiceNow sys_attachment AttachmentMetadata model
            parent_record_group_type: The record group type from parent article (CATEGORY or KB)
            parent_external_record_group_id: The external record group ID from parent article
            inherit_permissions: True: the attachment takes its article's readers
            article: The parent article's record: the attachment hangs under its number
                and links to its page (a sys_attachment.do link downloads the file)
            key: what the record is stored under (see _attachment_keys); the sys_id without one

        Returns:
            FileRecord: Transformed attachment or None if invalid
        """
        sys_id = attachment_data.sys_id
        file_name = attachment_data.file_name

        if not sys_id or not file_name:
            self.logger.warning(f"Attachment {getattr(attachment_data, 'sys_id', None)} has no sys_id or file name")
            return None

        # Parse timestamps
        source_created_at = None
        source_updated_at = None
        if attachment_data.sys_created_on:
            source_created_at = datetime_to_epoch_ms(attachment_data.sys_created_on, ServiceNowDefaults.DATETIME_FORMAT)
        if attachment_data.sys_updated_on:
            source_updated_at = datetime_to_epoch_ms(attachment_data.sys_updated_on, ServiceNowDefaults.DATETIME_FORMAT)

        # ServiceNow sends "type;charset=..." and generic types for uploads it could not
        # type; the file name decides those. An unknown type still keeps the file.
        mime_type = (attachment_data.content_type or "").split(";")[0].strip().lower()
        if not mime_type or mime_type in ServiceNowDefaults.UNRELIABLE_MIME_TYPES:
            mime_type = mimetypes.guess_type(file_name, strict=False)[0] or MimeTypes.UNKNOWN.value

        # Parse file size
        file_size = None
        size_bytes = attachment_data.size_bytes
        if size_bytes:
            try:
                file_size = int(size_bytes)
            except (ValueError, TypeError):
                pass

        # Create FileRecord for Attachment
        attachment_record_id = str(uuid.uuid4())
        attachment_record = FileRecord(
            id=attachment_record_id,
            inherit_permissions=inherit_permissions,
            # A file's bytes never change, and a new version's copy keeps them (and their hash);
            # its sys_updated_on moves with no change. Without a hash, the copy is new.
            external_revision_id=attachment_data.hash
            or f"{attachment_data.size_bytes}:{attachment_data.sys_created_on}",
            org_id=self.data_entities_processor.org_id,
            record_name=file_name,
            record_type=RecordType.FILE,
            external_record_id=key or sys_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SERVICENOW,
            connector_id=self.connector_id,
            mime_type=mime_type,
            parent_external_record_id=article.external_record_id if article else None,
            parent_record_type=RecordType.WEBPAGE,
            record_group_type=parent_record_group_type,
            external_record_group_id=parent_external_record_group_id,
            weburl=article.weburl if article else None,
            is_file=True,
            size_in_bytes=file_size,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
            extension=file_name.rsplit(".", 1)[-1].lower() if "." in file_name else None,
        )
        self._apply_indexing_filter(attachment_record, IndexingFilterKey.ATTACHMENTS)
        return attachment_record



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
    ) -> "ServiceNowConnector":
        """
        Factory method to create and initialize the connector.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider
            config_service: Configuration service

        Returns:
            ServiceNowConnector: Initialized connector instance
        """
        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
