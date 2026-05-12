import asyncio
import json
import mimetypes
import httpx
import re
import base64

from logging import Logger
from typing import Any, AsyncGenerator, Callable, DefaultDict, Dict, List, Optional, Set, Tuple, Type
from urllib.parse import quote
from uuid import uuid4
from html_to_markdown import convert as html_to_markdown  # type: ignore[import-untyped]

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.utils.oauth_config import fetch_oauth_config_by_id
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
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
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCollection,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.salesforce.common.apps import SalesforceApp
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    DealRecord,
    FileRecord,
    Org,
    Person,
    ProductRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from collections import defaultdict

from app.models.blocks import (
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceConfig,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.utils.html_utils import embed_html_images_as_base64
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import epoch_ms_to_iso, get_epoch_timestamp_in_ms, parse_timestamp


class RecordUpdate(BaseModel):
    """Track updates to a record (e.g. Salesforce file)."""

    model_config = ConfigDict(extra="ignore")

    record: Optional[Any] = None
    is_new: bool = False
    is_updated: bool = False
    is_deleted: bool = False
    metadata_changed: bool = False
    content_changed: bool = False
    permissions_changed: bool = False
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None


DEFAULT_API_VERSION = "59.0"
STREAM_CHUNK_SIZE = 8192              # bytes per chunk when streaming file content
FILE_STREAM_TIMEOUT_TOTAL_S = 300     # seconds — generous for large file downloads
FILE_STREAM_CONNECT_TIMEOUT_S = 10    # seconds


class MessageSegment(BaseModel):
    """A single segment from a Salesforce Chatter messageSegments array."""

    model_config = ConfigDict(extra="ignore")

    type: Optional[str] = None
    text: Optional[str] = None
    htmlTag: Optional[str] = None
    altText: Optional[str] = None
    url: Optional[str] = None
    reference: Optional[Dict[str, Any]] = None


class ChatterUser(BaseModel):
    """Salesforce user reference as it appears in Chatter actor/user fields."""

    model_config = ConfigDict(extra="ignore")

    name: Optional[str] = None
    displayName: Optional[str] = None
    id: Optional[str] = None


class TrackedChange(BaseModel):
    """A single field-change entry from capabilities.trackedChanges.changes."""

    model_config = ConfigDict(extra="ignore")

    fieldName: Optional[str] = None
    oldValue: Optional[Any] = None
    newValue: Optional[Any] = None


class ChatterFile(BaseModel):
    """A file attachment item from capabilities.files.items or capabilities.content."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    title: Optional[str] = None
    contentType: Optional[str] = None
    contentSize: Optional[int] = None


class ChatterBody(BaseModel):
    """The body object on a Chatter feed element or comment."""

    model_config = ConfigDict(extra="ignore")

    text: Optional[str] = None
    isRichText: Optional[bool] = None
    messageSegments: Optional[List[MessageSegment]] = None


class ChatterComment(BaseModel):
    """A comment or reply item from capabilities.comments.page.items."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    user: Optional[ChatterUser] = None
    body: Optional[ChatterBody] = None
    parent: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None


class ChatterElement(BaseModel):
    """A top-level Chatter feed element from the feedElements API."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    type: Optional[str] = None
    feedElementType: Optional[str] = None
    createdDate: Optional[str] = None
    actor: Optional[ChatterUser] = None
    body: Optional[ChatterBody] = None
    header: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Salesforce API row shapes
# ---------------------------------------------------------------------------

class SalesforceUser(BaseModel):
    """A row from the Salesforce User object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Email: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Title: Optional[str] = None
    Phone: Optional[str] = None
    MobilePhone: Optional[str] = None
    IsActive: Optional[bool] = None
    UserRoleId: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


class SalesforceRole(BaseModel):
    """A row from the Salesforce UserRole object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    ParentRoleId: Optional[str] = None
    SystemModstamp: Optional[str] = None


class SalesforceGroup(BaseModel):
    """A row from the Salesforce Group object (Public Group or Queue)."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    Type: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


class SalesforceOpportunity(BaseModel):
    """A row from the Salesforce Opportunity object."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    Id: Optional[str] = None
    Name: Optional[str] = None
    AccountId: Optional[str] = None
    Account: Optional[Dict[str, Any]] = None
    StageName: Optional[str] = None
    Amount: Optional[float] = None
    ExpectedRevenue: Optional[float] = None
    CloseDate: Optional[str] = None
    Probability: Optional[float] = None
    Type: Optional[str] = None
    OwnerId: Optional[str] = None
    IsWon: Optional[bool] = None
    IsClosed: Optional[bool] = None
    IsDeleted: Optional[bool] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None
    Opportunities: Optional[Dict[str, Any]] = None
    latest_comment_epoch: Optional[int] = Field(None, alias="_latest_comment_epoch")


class SalesforceProduct(BaseModel):
    """A row from the Salesforce Product2 object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    ProductCode: Optional[str] = None
    Family: Optional[str] = None
    IsActive: Optional[bool] = None
    StockKeepingUnit: Optional[str] = None
    SystemModstamp: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


class SalesforceCase(BaseModel):
    """A row from the Salesforce Case object."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    Id: Optional[str] = None
    CaseNumber: Optional[str] = None
    Subject: Optional[str] = None
    Status: Optional[str] = None
    Priority: Optional[str] = None
    Type: Optional[str] = None
    AccountId: Optional[str] = None
    Owner: Optional[Dict[str, Any]] = None
    Contact: Optional[Dict[str, Any]] = None
    CreatedBy: Optional[Dict[str, Any]] = None
    IsDeleted: Optional[bool] = None
    SystemModstamp: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None
    latest_comment_epoch: Optional[int] = Field(None, alias="_latest_comment_epoch")


class SalesforceTask(BaseModel):
    """A row from the Salesforce Task object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Subject: Optional[str] = None
    Status: Optional[str] = None
    Priority: Optional[str] = None
    TaskSubtype: Optional[str] = None
    WhatId: Optional[str] = None
    What: Optional[Dict[str, Any]] = None
    Owner: Optional[Dict[str, Any]] = None
    CreatedBy: Optional[Dict[str, Any]] = None
    ActivityDate: Optional[str] = None
    SystemModstamp: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


class SalesforceContentVersion(BaseModel):
    """A row from the Salesforce ContentVersion object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    ContentDocumentId: Optional[str] = None
    PathOnClient: Optional[str] = None
    Title: Optional[str] = None
    ContentSize: Optional[int] = None
    FileExtension: Optional[str] = None
    Checksum: Optional[str] = None
    LastModifiedDate: Optional[str] = None
    CreatedDate: Optional[str] = None


class SalesforceContact(BaseModel):
    """A row from the Salesforce Contact object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Email: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Phone: Optional[str] = None
    Title: Optional[str] = None
    Department: Optional[str] = None
    LeadSource: Optional[str] = None
    Description: Optional[str] = None
    Account: Optional[Dict[str, Any]] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None

class SalesforceAccount(BaseModel):
    """A row from the Salesforce Account object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    Name: Optional[str] = None
    Type: Optional[str] = None
    Rating: Optional[str] = None
    Website: Optional[str] = None
    Industry: Optional[str] = None
    Ownership: Optional[str] = None
    Phone: Optional[str] = None
    DunsNumber: Optional[str] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None
    Opportunities: Optional[Dict[str, Any]] = None


class SalesforceLead(BaseModel):
    """A row from the Salesforce Lead object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    FirstName: Optional[str] = None
    LastName: Optional[str] = None
    Email: Optional[str] = None
    Phone: Optional[str] = None
    Company: Optional[str] = None
    Title: Optional[str] = None
    Status: Optional[str] = None
    Rating: Optional[str] = None
    Industry: Optional[str] = None
    LeadSource: Optional[str] = None
    AnnualRevenue: Optional[float] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None
    ConvertedDate: Optional[str] = None
    ConvertedContactId: Optional[str] = None


class SalesforceLineItem(BaseModel):
    """A row from the Salesforce OpportunityLineItem object."""

    model_config = ConfigDict(extra="ignore")

    Id: Optional[str] = None
    OpportunityId: Optional[str] = None
    Product2: Optional[Dict[str, Any]] = None
    UnitPrice: Optional[float] = None
    Quantity: Optional[float] = None
    TotalPrice: Optional[float] = None
    IsDeleted: Optional[bool] = None
    CreatedDate: Optional[str] = None
    LastModifiedDate: Optional[str] = None


# Sync point keys for incremental sync (time-based)
USERS_SYNC_POINT_KEY = "users"
ROLES_SYNC_POINT_KEY = "roles"
USER_GROUPS_SYNC_POINT_KEY = "user_groups"
CONTACTS_SYNC_POINT_KEY = "contacts"
LEADS_SYNC_POINT_KEY = "leads"
PRODUCTS_SYNC_POINT_KEY = "products"
SOLD_IN_SYNC_POINT_KEY = "sold_in"
DEALS_SYNC_POINT_KEY = "deals"
CASES_SYNC_POINT_KEY = "cases"
TASKS_SYNC_POINT_KEY = "tasks"
FILES_SYNC_POINT_KEY = "files"
ACCOUNTS_SYNC_POINT_KEY = "accounts"
DISCUSSIONS_SYNC_POINT_KEY = "discussions"

# Permission hierarchy for comparing and upgrading permissions
# Higher number = higher permission level
PERMISSION_HIERARCHY = {
    "READER": 1,
    "COMMENTER": 2,
    "WRITER": 3,
    "OWNER": 4,
}


def _parse_salesforce_timestamp(value: Optional[Any]) -> Optional[int]:
    """Parse a Salesforce date string (normalizes +0000 to +00:00) to epoch ms. Returns None if invalid or missing."""
    if value is None or not isinstance(value, str):
        return None
    try:
        s = value.replace("+0000", "+00:00")
        return parse_timestamp(s)
    except Exception:
        return None


def _accumulate_latest_feed_epoch(
    feed_records: List[Dict[str, Any]],
    latest_feed_epoch: Dict[str, int],
) -> None:
    """Fold a page of `(ParentId, CreatedDate)` rows into the running
    parent-id -> latest-epoch-ms map (per-parent max wins)."""
    for f in feed_records:
        parent_id = f.get("ParentId")
        created_date = f.get("CreatedDate")
        if not parent_id or not created_date:
            continue
        epoch = _parse_salesforce_timestamp(created_date)
        if epoch is None:
            continue
        if parent_id not in latest_feed_epoch or epoch > latest_feed_epoch[parent_id]:
            latest_feed_epoch[parent_id] = epoch


# Salesforce IDs are exactly 15 or 18 alphanumeric characters.
_SF_ID_RE = re.compile(r'^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$')


def _sanitize_soql_id(value: str) -> str:
    """
    Validate a Salesforce record ID before it is interpolated into a SOQL string.

    Salesforce IDs are exactly 15 or 18 alphanumeric characters.  Any value
    that does not match this pattern cannot be a legitimate ID and is rejected
    to prevent SOQL injection.

    Raises:
        ValueError: if *value* is empty or does not match the expected format.
    """
    if not value or not _SF_ID_RE.match(value):
        raise ValueError(f"Invalid Salesforce ID for SOQL interpolation: {value!r}")
    return value


def _sanitize_soql_ids_batch(ids: List[str], logger: Logger, context: str = "") -> List[str]:
    """
    Validate a batch of Salesforce record IDs for SOQL interpolation.

    Each ID is run through `_sanitize_soql_id`; invalid values are dropped and
    logged as warnings.  Returns the subset of *ids* that passed validation.

    Args:
        ids:     The raw IDs to validate.
        logger:  Logger used to emit per-invalid-ID warnings.
        context: Optional human-readable label included in warning messages
                 (e.g. "opportunity account lookup") to aid debugging.
    """
    safe: List[str] = []
    label = f" ({context})" if context else ""
    for value in ids:
        try:
            safe.append(_sanitize_soql_id(value))
        except ValueError:
            logger.warning("Skipping invalid Salesforce ID%s: %r", label, value)
    return safe


def _compose_soql_where(*conditions: str) -> str:
    """Compose 0–N condition strings into a single WHERE ... AND ... clause.

    Empty or whitespace-only conditions are ignored. Returns an empty string
    when no conditions are provided (caller appends ORDER BY / nothing directly).
    """
    non_empty = [c.strip() for c in conditions if c and c.strip()]
    return ("WHERE " + " AND ".join(non_empty)) if non_empty else ""


# Mirrors IN_CLAUSE_BATCH_SIZE / _EDGE_CHECK_BATCH used elsewhere in this module.
_DEFAULT_IN_LOOKUP_BATCH_SIZE = 500


async def _get_nodes_by_field_in_batched(
    tx_store: Any,
    collection: str,
    field: str,
    values: List[Any],
    return_fields: Optional[List[str]] = None,
    batch_size: int = _DEFAULT_IN_LOOKUP_BATCH_SIZE,
) -> List[Dict[str, Any]]:
    """Shard a `get_nodes_by_field_in` lookup into fixed-size chunks.

    A single AQL ``FILTER doc.<field> IN @values`` over a multi-thousand-element
    array degrades into a hash-join over a huge constant — slow, and not what
    ``IN`` is optimised for in ArangoDB. The SOQL paths already chunk
    ``IN (...)`` lookups via `_sanitize_soql_ids_batch`; this is the equivalent
    for the Arango-side lookup.

    Returns an empty list when *values* is empty so callers can drop their own
    ``if X else []`` guard.
    """
    if not values:
        return []
    merged: List[Dict[str, Any]] = []
    for i in range(0, len(values), batch_size):
        chunk = values[i : i + batch_size]
        if not chunk:
            continue
        page = await tx_store.get_nodes_by_field_in(
            collection=collection,
            field=field,
            values=chunk,
            return_fields=return_fields,
        )
        if page:
            merged.extend(page)
    return merged


def _date_bound_conditions(
    column: str,
    after_ms: Optional[int],
    before_ms: Optional[int],
) -> List[str]:
    """Build SOQL date-range conditions for a column from optional epoch bounds.

    Maps the framework's datetime filter shape onto SOQL:
    - is_after  → (start, None)   → ["{column} >= start"]
    - is_before → (None, end)     → ["{column} <= end"]
    - is_between→ (start, end)    → ["{column} >= start", "{column} <= end"]

    Returns an empty list when both bounds are None (no filter active).
    """
    conditions: List[str] = []
    if after_ms:
        conditions.append(f"{column} >= {epoch_ms_to_iso(after_ms)}")
    if before_ms:
        conditions.append(f"{column} <= {epoch_ms_to_iso(before_ms)}")
    return conditions


def _ts_in_bounds(
    ts: Optional[int],
    after_ms: Optional[int],
    before_ms: Optional[int],
) -> bool:
    """Python equivalent of `_date_bound_conditions` for in-memory filtering.

    Returns True when `ts` satisfies the bounds. A missing ts (None) fails the
    filter only if at least one bound is set; with no bounds the predicate is a
    no-op and returns True.
    """
    if after_ms is None and before_ms is None:
        return True
    if ts is None:
        return False
    if after_ms is not None and ts < after_ms:
        return False
    if before_ms is not None and ts > before_ms:
        return False
    return True


@ConnectorBuilder("Salesforce")\
    .in_group("Salesforce")\
    .with_description("Sync content from your Salesforce instance")\
    .with_categories(["CRM", "Sales"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Salesforce",
            authorize_url="https://login.salesforce.com/services/oauth2/authorize",
            token_url="https://login.salesforce.com/services/oauth2/token",
            redirect_uri="connectors/oauth/callback/Salesforce",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "api",
                    "refresh_token",
                    "offline_access"
                ],
                agent=[]
            ),
            fields=[
                AuthField(
                    name="instance_url",
                    display_name="Salesforce Instance URL",
                    placeholder="https://login.salesforce.com",
                    description="The base URL of your Salesforce instance",
                    field_type="TEXT",
                    max_length=2048
                ),
                CommonFields.client_id("Salesforce Connected App"),
                CommonFields.client_secret("Salesforce Connected App")
            ],
            icon_path=IconPaths.connector_icon(Connectors.SALESFORCE.value),
            app_group="Salesforce",
            app_description="OAuth application for accessing Salesforce API",
            app_categories=["CRM", "Sales"],
            token_access_type="offline"
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.SALESFORCE.value))
        .add_documentation_link(DocumentationLink(
            "Salesforce OAuth Setup",
            "https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_web_server_flow.htm",
            "setup"
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter content by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter content by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class SalesforceConnector(BaseConnector):
    """
    Connector for synchronizing data from a Salesforce instance.
    Syncs roles, users, and permissions.
    """
    salesforce_instance_url: str

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str = "",
        created_by: str = "",
    ) -> None:
        super().__init__(
            SalesforceApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        self.connector_name = Connectors.SALESFORCE
        self.connector_id = connector_id

        # Sync point for incremental user sync (time-based)
        self.user_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.USERS,
            data_store_provider=self.data_store_provider,
        )
        # Sync point for incremental sync of roles, groups, contacts, leads, products, opportunities, cases, tasks
        self.records_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider,
        )

        # Data source and configuration
        self.data_source: Optional[SalesforceDataSource] = None
        self.api_version = DEFAULT_API_VERSION

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

        # Shared HTTP client; created in init(), closed in cleanup().
        # Re-using a single client avoids per-call TCP connection overhead.
        self._http_client: Optional[httpx.AsyncClient] = None

    async def _get_api_version(self) -> str:
        """Get API version from config or use default."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if config and config.get("apiVersion"):
                return str(config.get("apiVersion"))
        except Exception:
            pass
        return self.api_version

    async def _soql_query_paginated(
        self,
        api_version: str,
        q: str,
        queryAll: bool = False,
    ) -> AsyncGenerator[List[Dict[str, Any]], None]:
        """Stream a paginated SOQL query, yielding one Salesforce response page per yield.

        Each yielded value is the `records` list for a single Salesforce page (typically
        ~200 rows by API default). Callers iterate with `async for page in ...` and process
        each page independently — no full result set is ever held in memory by this helper.

        Raises:
            RuntimeError: on any Salesforce API failure — initial query or any pagination page.
                The generator stops; callers must not advance sync-point cursors when this
                generator raises.
        """
        if not self.data_source:
            raise RuntimeError("Salesforce data source is not initialized")

        if queryAll:
            response = await self.data_source.soql_query_all(api_version=api_version, q=q)
        else:
            response = await self.data_source.soql_query(api_version=api_version, q=q)

        if not response.success:
            self.logger.error("SOQL query failed: %s. Query: %s", response.error, q)
            raise RuntimeError(f"SOQL query failed: {response.error}")

        yield list(response.data.get("records") or [])

        while not response.data.get("done", True):
            next_url = response.data.get("nextRecordsUrl")
            if not next_url:
                break
            response = await self.data_source.soql_query_next(next_url=next_url)
            if not response.success:
                self.logger.error(
                    "SOQL pagination failed at %s: %s.", next_url, response.error,
                )
                raise RuntimeError(f"SOQL pagination failed: {response.error}")
            yield list(response.data.get("records") or [])

    @staticmethod
    async def _typed_pages(
        raw_pages: AsyncGenerator[List[Dict[str, Any]], None],
        model_cls: Type[BaseModel],
    ) -> AsyncGenerator[List[Any], None]:
        """Wrap a raw-dict page generator into a typed-record page generator.

        Used at orchestrator call sites that read directly from `_soql_query_paginated`
        (no `_get_updated_X` helper in between) but feed a `_sync_X` consumer that
        expects already-validated Pydantic instances.
        """
        async for page in raw_pages:
            yield [model_cls.model_validate(r) for r in page]

    async def _fetch_first_record(
        self,
        api_version: str,
        soql: str,
        queryAll: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Drain a paginated SOQL stream and return the first record (or None).

        Convenience wrapper for "fetch one record by id" patterns that don't need
        to hold the full result page in scope.  Raises if the underlying SOQL fails.
        """
        async for page in self._soql_query_paginated(
            api_version=api_version, q=soql, queryAll=queryAll,
        ):
            if page:
                return page[0]
        return None

    @staticmethod
    async def _filter_pages_by_last_modified(
        typed_pages: AsyncGenerator[List[Any], None],
        updated_after_ms: Optional[int],
        updated_before_ms: Optional[int],
    ) -> AsyncGenerator[List[Any], None]:
        """Apply the `_ts_in_bounds` filter on `LastModifiedDate` to each page in the
        stream.  Used after `_get_updated_deal` / `_get_updated_case` to drop rows
        resurfaced by the FeedItem/FeedComment OR-branches that fall outside the
        configured updated_at window."""
        async for page in typed_pages:
            kept = [
                row for row in page
                if _ts_in_bounds(
                    _parse_salesforce_timestamp(row.LastModifiedDate),
                    updated_after_ms,
                    updated_before_ms,
                )
            ]
            if kept:
                yield kept

    async def init(self) -> bool:
        """
        Initializes the Salesforce client using credentials from the config service.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Salesforce configuration not found.")
                return False

            credentials_config = config.get("credentials", {})
            auth_config = config.get("auth", {})
            oauth_config_id = auth_config.get("oauthConfigId")

            # Fetch OAuth config
            oauth_config = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=Connectors.SALESFORCE.value,
                config_service=self.config_service,
                logger=self.logger
            )

            # Extract access token and instance URL
            auth_config_data = oauth_config.get("config")
            access_token = credentials_config.get("access_token")
            refresh_token = credentials_config.get("refresh_token")
            instance_url = auth_config_data.get("instance_url")

            if not access_token:
                self.logger.error("Salesforce access token not found in configuration.")
                return False
            if not instance_url:
                self.logger.error("Salesforce instance URL not found in configuration.")
                return False

            # Initialize Salesforce client
            self.api_version = config.get("apiVersion", DEFAULT_API_VERSION)
            salesforce_config = SalesforceConfig(
                instance_url=instance_url,
                access_token=access_token,
                api_version=self.api_version,
                refresh_token=refresh_token
            )

            try:
                client = SalesforceClient.build_with_config(salesforce_config)
            except ValueError as e:
                self.logger.error(f"Failed to initialize Salesforce client: {e}", exc_info=True)
                return False

            self.data_source = SalesforceDataSource(client)
            self.salesforce_instance_url = instance_url

            self._http_client = httpx.AsyncClient()

            self.logger.info("Salesforce client initialized successfully.")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Salesforce client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """
        Tests the connection to Salesforce by attempting to query the API.

        Returns:
            True if connection test was successful, False otherwise
        """
        if not self.data_source:
            self.logger.error("Salesforce data source not initialized")
            return False

        try:
            api_version = await self._get_api_version()
            response = await self.data_source.limits(version=api_version)
            if response.success:
                self.logger.info("Salesforce connection test successful.")
                return True
            else:
                self.logger.error(f"Salesforce connection test failed: {response.error}")
                return False

        except Exception as e:
            self.logger.error(f"Salesforce connection test failed: {e}", exc_info=True)
            return False

    async def _reinitialize_token_if_needed(self) -> bool:
        """
        Call a basic API using the data source; if the response is 401 Unauthorized,
        perform token refresh via TokenRefreshService and re-initialize the connector.

        Returns:
            True if no reinit was needed (API succeeded) or reinit succeeded;
            False if data source is not initialized, or 401 was received but refresh failed.
        """
        self.logger.info("Reinitializing Salesforce token if needed...")

        if not self.data_source:
            self.logger.warning("Salesforce data source not initialized; cannot reinitialize token.")
            return False

        try:
            api_version = await self._get_api_version()
            response = await self.data_source.limits(version=api_version)
        except Exception as e:
            self.logger.warning(f"Salesforce limits API call failed: {e}")
            return False

        if response.success:
            self.logger.debug(f"Salesforce access token still active for connector {self.connector_id}")
            return True

        # Check for 401 Unauthorized (error is e.g. "HTTP 401")
        if not response.error or "401" not in response.error:
            return False

        self.logger.debug(f"Salesforce API returned 401 for connector {self.connector_id}; attempting token refresh.")
        refresh_service = startup_service.get_token_refresh_service()
        if not refresh_service:
            self.logger.error("Token refresh service not available; cannot reinitialize token.")
            return False

        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Connector config not found; cannot refresh token.")
                return False
            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error("No refresh token in connector config; cannot refresh.")
                return False
            connector_type = (
                self.connector_name.value
                if hasattr(self.connector_name, "value")
                else str(self.connector_name)
            )
            await refresh_service._perform_token_refresh(
                self.connector_id, connector_type, refresh_token
            )
        except Exception as e:
            self.logger.error(f"Token refresh failed for connector {self.connector_id}: {e}", exc_info=True)
            return False

        # Re-initialize the connector so the data source uses the new token
        try:
            return await self.init()
        except Exception as e:
            self.logger.error(f"Re-initialization after token refresh failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL for accessing a Salesforce record.

        For FILE records: returns the shepherd version download URL in the format
        {instance_url}/sfc/servlet.shepherd/version/download/{content_version_id}
        where content_version_id is taken from record.external_revision_id.

        For other records: returns the web URL {instance_url}/{external_record_id}.

        Args:
            record: The record to get URL for

        Returns:
            The download/web URL of the record or None
        """
        if not record.external_record_id:
            return None
        if record.record_type == RecordType.FILE and record.external_revision_id:
            base = (self.salesforce_instance_url or "").rstrip("/")
            return f"{base}/sfc/servlet.shepherd/version/download/{record.external_revision_id}"
        return f"{self.salesforce_instance_url}/{record.external_record_id}"

    async def _get_access_token(self) -> Optional[str]:
        """Fetch current access token from connector config (for authenticated API calls)."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                return None
            return config.get("credentials", {}).get("access_token")
        except Exception as e:
            self.logger.error(f"Failed to get access token: {e}")
            return None

# ============================= STREAMINGS CONTENT FROM SALESFORCE =============================

    async def _stream_salesforce_file_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream file content from Salesforce ContentVersion VersionData API.

        Uses record.external_revision_id (ContentVersion Id) and Bearer token auth.
        Same REST path as data_source.s_object_blob_retrieve(record_id, "ContentVersion", version, "VersionData"),
        but we stream with aiohttp here because the data source's _execute_request parses responses as JSON;
        VersionData is binary, and we need chunked streaming for create_stream_record_response.
        """
        if not record.external_revision_id:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="File record has no version id (external_revision_id)"
            )
        access_token = await self._get_access_token()
        if not access_token:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not authenticated"
            )
        api_version = await self._get_api_version()
        base = (self.salesforce_instance_url or "").rstrip("/")
        path = f"/services/data/v{api_version}/sobjects/ContentVersion/{record.external_revision_id}/VersionData"
        url = f"{base}{path}"
        timeout = httpx.Timeout(FILE_STREAM_TIMEOUT_TOTAL_S, connect=FILE_STREAM_CONNECT_TIMEOUT_S)
        http_client = self._http_client or httpx.AsyncClient()
        try:
            async with http_client.stream(
                "GET",
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                follow_redirects=True,
                timeout=timeout,
            ) as response:
                if response.status_code != HttpStatusCode.SUCCESS.value:
                    body = (await response.aread()).decode(errors="replace")
                    self.logger.error(
                        f"Salesforce VersionData request failed: {response.status_code} {body[:500]}"
                    )
                    raise HTTPException(
                        status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                        detail=f"Failed to fetch file content: {response.status_code}"
                    )
                async for chunk in response.aiter_bytes(STREAM_CHUNK_SIZE):
                    yield chunk
        except httpx.HTTPError as e:
            self.logger.error(f"Error streaming Salesforce file {record.id}: {e}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to fetch file content: {str(e)}"
            )

    async def _message_segments_to_html(self, segments: List[MessageSegment]) -> str:
        """
        Convert Chatter messageSegments to HTML, fetching InlineImage segments
        as base64 data URIs so the content is self-contained.
        """
        html_parts = []

        for seg in segments:
            seg_type = seg.type

            if seg_type == "MarkupBegin":
                html_tag = seg.htmlTag or "span"
                html_parts.append(f"<{html_tag}>")

            elif seg_type == "MarkupEnd":
                html_tag = seg.htmlTag or "span"
                # MarkupEnd text often contains the newline, include it
                html_parts.append(f"</{html_tag}>")

            elif seg_type == "Text":
                text = seg.text or ""
                html_parts.append(text)

            elif seg_type == "InlineImage":
                alt = seg.altText or ""
                file_url = seg.url or ""  # e.g. /services/data/v59.0/connect/files/{id}/content?versionNumber=1
                image_url = f"{self.salesforce_instance_url}{file_url}"
                b64_data_uri = await self._fetch_file_as_base64_uri(image_url)
                if b64_data_uri:
                    html_parts.append(f'<img alt="{alt}" src="{b64_data_uri}" />')
                else:
                    html_parts.append(f'<img alt="{alt}" src="" />')

            elif seg_type == "EntityLink":
                text = seg.text or ""
                ref = seg.reference or {}
                url = ref.get("url") or ""
                html_parts.append(f'<a href="{url}">{text}</a>')

            elif seg_type in ("FieldChange", "FieldChangeName", "FieldChangeValue"):
                text = seg.text or ""
                html_parts.append(text)

            else:
                # Fallback: just use text if present
                text = seg.text or ""
                html_parts.append(text)

        return "".join(html_parts)

    # Inline images larger than this are skipped to protect heap memory.
    _MAX_INLINE_IMAGE_BYTES: int = 10 * 1024 * 1024  # 10 MB

    async def _fetch_file_as_base64_uri(self, full_url: str) -> Optional[str]:
        """
        Fetch a Salesforce file by its URL and return a base64 data URI.
        - file-asset-public URLs: fetched without auth (CDN-hosted public assets)
        - connect/files URLs: fetched with Bearer token (API-protected files)

        Files larger than _MAX_INLINE_IMAGE_BYTES are skipped (returns None).
        """
        if not full_url or not self.data_source:
            return None

        try:
            is_public_asset = "file-asset-public" in full_url
            is_api_file = "/connect/files/" in full_url or "/services/data/" in full_url

            headers = {}
            if is_api_file and not is_public_asset:
                access_token = await self._get_access_token()
                if not access_token:
                    return None
                headers["Authorization"] = f"Bearer {access_token}"

            http_client = self._http_client or httpx.AsyncClient()
            response = await http_client.get(full_url, headers=headers, follow_redirects=True)

            # If public fetch 404s, retry once with an auth token
            if response.status_code == 404 and is_public_asset:
                access_token = await self._get_access_token()
                if not access_token:
                    return None
                response = await http_client.get(
                    full_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    follow_redirects=True,
                )

            if response.status_code != 200:
                return None

            cl = response.headers.get("Content-Length")
            if cl and int(cl) > self._MAX_INLINE_IMAGE_BYTES:
                self.logger.warning(
                    "Skipping oversized inline image (%s bytes): %s", cl, full_url
                )
                return None

            raw = response.content
            content_type = response.headers.get("Content-Type", "")

            # Determine mime type: prefer Content-Type header, fallback to magic bytes
            if "image/jpeg" in content_type or "image/jpg" in content_type:
                mime = "image/jpeg"
            elif "image/gif" in content_type:
                mime = "image/gif"
            elif "image/webp" in content_type:
                mime = "image/webp"
            elif "image/png" in content_type:
                mime = "image/png"
            elif raw[:2] == b'\xff\xd8':
                mime = "image/jpeg"
            elif raw[:4] == b'\x89PNG':
                mime = "image/png"
            elif raw[:3] == b'GIF':
                mime = "image/gif"
            else:
                mime = "image/png"

            b64 = base64.b64encode(raw).decode("utf-8")
            return f"data:{mime};base64,{b64}"

        except Exception as e:
            self.logger.error(f"Error fetching file as base64 URI: {e}", exc_info=True)
            return None
    
    async def _fetch_and_build_discussion_block_groups(
        self,
        parent_id: str,
        start_index: int,
        weburl: Optional[str] = None,
    ) -> List[BlockGroup]:
        """
        Fetch Chatter feed via REST API and build discussion BlockGroups.
        Combines fetching and building into a single function.
        """
        if not self.data_source:
            return []

        api_version = await self._get_api_version()

        # --- Fetch all pages ---
        all_elements: List[ChatterElement] = []

        resp = await self.data_source.record_feed_elements(
            record_group_id=parent_id,
            version=api_version,
        )
        if not resp.success or not resp.data:
            return []

        all_elements.extend(
            [ChatterElement.model_validate(e) for e in (resp.data.get("elements") or [])]
        )

        next_page_url = resp.data.get("nextPageUrl")
        while next_page_url:
            paged_resp = await self.data_source._execute_request(
                method="GET",
                path=next_page_url,
                params=None,
                body=None,
                content_type="application/json",
            )
            if not paged_resp.success or not paged_resp.data:
                break
            all_elements.extend(
                [ChatterElement.model_validate(e) for e in (paged_resp.data.get("elements") or [])]
            )
            next_page_url = paged_resp.data.get("nextPageUrl")

        # --- Helpers ---

        def _author_from_user(user: Optional[ChatterUser]) -> str:
            if not user:
                return ""
            return (user.name or user.displayName or "").strip()

        def _get_tracked_changes(element: ChatterElement) -> List[TrackedChange]:
            caps = element.capabilities or {}
            tc = caps.get("trackedChanges") or {}
            return [TrackedChange.model_validate(c) for c in (tc.get("changes") or [])]

        def _get_comments(element: Any) -> List[ChatterComment]:
            caps = element.capabilities or {}
            comments_cap = caps.get("comments") or {}
            page = comments_cap.get("page") or {}
            return [ChatterComment.model_validate(c) for c in (page.get("items") or [])]

        def _get_feed_files(element: ChatterElement) -> List[ChatterFile]:
            caps = element.capabilities or {}
            files_cap = caps.get("files") or {}
            return [ChatterFile.model_validate(f) for f in (files_cap.get("items") or [])]

        def _tracked_changes_to_text(changes: List[TrackedChange]) -> str:
            lines = []
            for tc in changes:
                fn = tc.fieldName or ""
                ov = str(tc.oldValue) if tc.oldValue is not None else ""
                nv = str(tc.newValue) if tc.newValue is not None else ""
                lines.append(f"{fn}: {ov} → {nv}")
            return "\n".join(lines)

        def _get_call_log_post_data(element: ChatterElement) -> Optional[str]:
            """
            Build the markdown content for a CallLogPost feed element.

            Salesforce stores call log content in capabilities.enhancedLink:
              - title:       the subject of the logged call (may be null — fall back
                             to the first EntityLink text in header.messageSegments)
              - description: the full call notes body

            Returns a formatted markdown string, or None if this element is not a
            CallLogPost or has no usable content.
            """
            if element.type != "CallLogPost":
                return None

            caps = element.capabilities or {}
            enhanced_link = caps.get("enhancedLink") or {}

            # Title: prefer enhancedLink.title, fall back to first EntityLink in header
            title: str = (enhanced_link.get("title") or "").strip()
            if not title:
                header = element.header or {}
                for seg in header.get("messageSegments") or []:
                    if seg.get("type") == "EntityLink":
                        title = (seg.get("text") or "").strip()
                        break

            description: str = (enhanced_link.get("description") or "").strip()

            if not title and not description:
                return None

            parts = []
            if title:
                parts.append(f"**{title}**")
            if description:
                # description is plain text with \r\n line endings — normalise to markdown
                parts.append(html_to_markdown(description.replace("\r\n", "\n")))

            return "\n\n".join(parts)

        def _get_comment_files(comment: ChatterComment) -> List[ChatterFile]:
            """
            Extract all file attachments from a comment or reply.
            Prefers capabilities.files.items (multiple files), falls back to
            capabilities.content (single file, older-style attachment).
            """
            caps = comment.capabilities or {}

            file_items = (caps.get("files") or {}).get("items") or []
            if file_items:
                return [ChatterFile.model_validate(f) for f in file_items]

            # Single-file fallback — normalise to a list for uniform handling
            content = caps.get("content")
            if content and isinstance(content, dict) and content.get("id"):
                return [ChatterFile.model_validate(content)]

            return []

        async def _process_body(body: Optional[ChatterBody]) -> str:
            """
            Convert a Chatter body dict to markdown.
            Prefers messageSegments (to capture InlineImages) over plain text.
            """
            if not body:
                return ""

            segments = body.messageSegments or []
            is_rich = body.isRichText or False

            if segments and is_rich:
                # Build HTML from segments (images become base64 data URIs)
                html = await self._message_segments_to_html(segments)
                return html_to_markdown(html) if html else ""
            else:
                # Plain text fallback
                plain = (body.text or "").strip()
                if plain:
                    converted = await self._process_html_images(plain)
                    return html_to_markdown(converted) if converted else ""
                return ""

        def _prepend_ts(data: str, ts: Optional[str]) -> str:
            if ts:
                return f"*{ts}*\n\n{data}" if data else f"*{ts}*"
            return data

        def _is_task_comment(comment: ChatterComment) -> bool:
            comment_parent = comment.parent or {}
            comment_parent_id = comment_parent.get("id") or ""
            # If the comment belongs to a different record than what we're processing, skip it
            return comment_parent_id.startswith("00T") and comment_parent_id != parent_id

        async def _resolve_child_records(external_ids: List[str]) -> List[ChildRecord]:
            """Look up each external_id and build a proper ChildRecord."""
            children = []
            for ext_id in external_ids:
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id,
                        ext_id,
                    )
                    if record:
                        children.append(
                            ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=record.id,
                                child_name=record.record_name,
                            )
                        )
                except Exception:
                    pass  # skip unresolvable attachments
            return children

        def _flatten_elements(elements: List[ChatterElement]) -> List[ChatterElement]:
            flat = []
            for el in elements:
                if el.feedElementType == "Bundle":
                    bundle_caps = el.capabilities or {}
                    bundle = bundle_caps.get("bundle") or {}
                    page = bundle.get("page") or {}
                    sub_elements = [
                        ChatterElement.model_validate(e) for e in (page.get("elements") or [])
                    ]
                    flat.extend(_flatten_elements(sub_elements))
                else:
                    flat.append(el)
            return flat

        flat_elements = _flatten_elements(all_elements)
        flat_elements.sort(key=lambda e: e.createdDate or "", reverse=True)

        # --- Build BlockGroups ---
        block_groups: List[BlockGroup] = []
        idx = start_index

        async def _process_comment(comment: ChatterComment, thread_index: int) -> None:
            """
            Build a BlockGroup for a comment/reply, resolve all its file attachments
            (supports both single and multiple files via _get_comment_files), then
            recurse into any nested replies so no level of the thread is missed.
            Comments on CallLogPosts are treated identically to any other comment —
            only the parent post itself gets the special call log formatting.
            """
            nonlocal idx

            comment_author = _author_from_user(comment.user)
            comment_id = comment.id or ""
            comment_data = await _process_body(comment.body)

            # Resolve all file attachments on this comment/reply
            comment_files = _get_comment_files(comment)
            comment_ext_ids = [f"{f.id}-{parent_id}" for f in comment_files if f.id]
            comment_children = await _resolve_child_records(comment_ext_ids)

            comment_bg = BlockGroup(
                id=str(uuid4()),
                index=idx,
                parent_index=thread_index,
                name=f"Comment by {comment_author}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                description=f"Comment by {comment_author}",
                source_group_id=comment_id,
                data=comment_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                children_records=comment_children if comment_children else None,
            )
            block_groups.append(comment_bg)

            # Recurse into nested replies on this comment
            for reply in _get_comments(comment):
                idx += 1
                await _process_comment(reply, thread_index)

        for element in flat_elements:
            el_id = element.id or ""
            author = _author_from_user(element.actor)
            el_timestamp = element.createdDate

            # Thread BlockGroup
            thread_index = idx
            thread_name = f"Chatter: {author}"
            thread_bg = BlockGroup(
                id=str(uuid4()),
                index=thread_index,
                parent_index=0,
                name=thread_name,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT_THREAD,
                description=f"Discussion thread - {thread_name}",
                source_group_id=f"{parent_id}_discussion_{el_id}",
                weburl=weburl,
                requires_processing=False,
            )
            block_groups.append(thread_bg)
            idx += 1

            # Post body
            # Priority order:
            #   1. CallLogPost  → title + description from capabilities.enhancedLink
            #   2. TrackedChange (field edits with no body text) → formatted field diffs
            #   3. Normal post  → body segments / plain text converted to markdown
            body_obj = element.body
            tracked = _get_tracked_changes(element)

            call_log_data = _get_call_log_post_data(element)
            if call_log_data is not None:
                # CallLogPost: use enhancedLink title + description only
                post_data = call_log_data
            elif not ((body_obj.text or "").strip() if body_obj else "") and tracked:
                post_data = _tracked_changes_to_text(tracked)
            else:
                post_data = await _process_body(body_obj)

            post_data = _prepend_ts(post_data, el_timestamp)

            # Post-level file attachments
            feed_files = _get_feed_files(element)
            post_ext_ids = [f"{f.id}-{parent_id}" for f in feed_files if f.id]
            attachment_children = await _resolve_child_records(post_ext_ids)

            post_bg = BlockGroup(
                id=str(uuid4()),
                index=idx,
                parent_index=thread_index,
                name=f"Post by {author}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                description=f"Post by {author}",
                source_group_id=el_id,
                data=post_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                children_records=attachment_children if attachment_children else None,
            )
            block_groups.append(post_bg)
            idx += 1

            # Comments / replies — _process_comment handles multi-file attachments
            # and recurses into nested replies. CallLogPost replies are treated the
            # same as any other comment (no special formatting needed there).
            for comment in _get_comments(element):
                await _process_comment(comment, thread_index)
                idx += 1

        return block_groups

    async def _get_record_linked_file_child_records(
        self,
        api_version: str,
        record_salesforce_id: str,
    ) -> List[ChildRecord]:
        """
        Retrieves files attached to a record using ContentDocumentLink associations in Salesforce.
        """
        if not record_salesforce_id or not self.data_source:
            return []

        try:
            safe_record_id = _sanitize_soql_id(record_salesforce_id)
        except ValueError:
            self.logger.warning(
                "Skipping linked-file lookup: invalid Salesforce ID %r", record_salesforce_id
            )
            return []

        soql = (
            "SELECT ContentDocumentId, LinkedEntityId, ContentDocument.Title, "
            "ContentDocument.FileExtension, ContentDocument.LatestPublishedVersionId, "
            "ContentDocument.CreatedDate FROM ContentDocumentLink "
            f"WHERE LinkedEntityId = '{safe_record_id}'"
        )

        # Per-record child lookup: a SOQL failure here must not abort the parent
        # record's processing — log and return what we have so far. This is the
        # narrow exception to the "raise on SOQL failure" contract.
        child_records: List[ChildRecord] = []
        try:
            async for page in self._soql_query_paginated(api_version=api_version, q=soql):
                for row in page:
                    content_document_id = row.get("ContentDocumentId")
                    linked_entity_id = row.get("LinkedEntityId")
                    if not content_document_id or not linked_entity_id:
                        continue
                    external_record_id = f"{content_document_id}-{linked_entity_id}"
                    try:
                        record = await self.data_entities_processor.get_record_by_external_id(
                            self.connector_id, external_record_id,
                        )
                        if record:
                            child_records.append(
                                ChildRecord(
                                    child_type=ChildType.RECORD,
                                    child_id=record.id,
                                    child_name=record.record_name,
                                )
                            )
                    except Exception as e:
                        self.logger.error(f"Error resolving child record: {e}", exc_info=True)
        except RuntimeError as e:
            self.logger.warning(
                "Linked-file SOQL failed for record %s: %s", record_salesforce_id, e
            )
            return []

        return child_records

    async def _get_opportunity_related_child_records(
        self,
        api_version: str,
        opportunity_id: str,
    ) -> List[ChildRecord]:
        """
        Fetch Tasks, OpportunityLineItems (products), and Cases related to an Opportunity
        via a single composite request, resolve each to Arango record id, and return as ChildRecords.
        """
        if not opportunity_id or not self.data_source:
            return []
        try:
            safe_id = _sanitize_soql_id(opportunity_id)
        except ValueError:
            self.logger.warning(
                "Skipping opportunity child records: invalid Salesforce ID %r", opportunity_id
            )
            return []
        q_tasks = f"SELECT Id, Subject, Status FROM Task WHERE WhatId='{safe_id}'"
        q_products = (
            f"SELECT Id, Product2.Id, Product2.Name, Quantity, UnitPrice "
            f"FROM OpportunityLineItem WHERE OpportunityId='{safe_id}'"
        )
        q_cases = (
            f"SELECT Id, CaseNumber, Status FROM Case "
            f"WHERE AccountId IN (SELECT AccountId FROM Opportunity WHERE Id='{safe_id}')"
        )
        base = f"/services/data/v{api_version}/query/"
        composite_request = [
            {"method": "GET", "referenceId": "RelatedTasks", "url": f"{base}?q={quote(q_tasks)}"},
            {"method": "GET", "referenceId": "RelatedProducts", "url": f"{base}?q={quote(q_products)}"},
            {"method": "GET", "referenceId": "RelatedCases", "url": f"{base}?q={quote(q_cases)}"},
        ]
        response = await self.data_source.composite(
            version=api_version,
            data={"compositeRequest": composite_request},
        )
        if not response.success or not response.data:
            self.logger.error(f"Composite request for opportunity {opportunity_id} failed: {response.error}")
            return []
        composite_response = response.data.get("compositeResponse") or []
        child_records: List[ChildRecord] = []
        for item in composite_response:
            if item.get("httpStatusCode") != 200:
                continue
            body = item.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    continue
            records_list = (body or {}).get("records") or []
            ref_id = item.get("referenceId", "")
            for row in records_list:
                external_id = None
                name = None
                if ref_id == "RelatedTasks":
                    external_id = row.get("Id")
                    name = row.get("Subject") or (f"Task {external_id}" if external_id else None)
                elif ref_id == "RelatedProducts":
                    product2 = row.get("Product2") or {}
                    external_id = product2.get("Id")
                    name = product2.get("Name") or (f"Product {external_id}" if external_id else None)
                elif ref_id == "RelatedCases":
                    external_id = row.get("Id")
                    name = row.get("CaseNumber") or (f"Case {external_id}" if external_id else None)
                if not external_id:
                    continue
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id,
                        external_id,
                    )
                    if record:
                        child_records.append(
                            ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=record.id,
                                child_name=record.record_name or name,
                            )
                        )
                except Exception as e:
                    self.logger.error(f"Could not resolve related record for external_id={external_id}: {e}")
        return child_records

    def _set_block_group_children(self, block_groups: List[BlockGroup]) -> None:
        """Wire parent_index -> children for all block groups (description, emails, discussion threads)."""
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)
        for bg in block_groups:
            if bg.index in blockgroup_children_map:
                bg.children = BlockGroupChildren.from_indices(
                    block_group_indices=sorted(blockgroup_children_map[bg.index])
                )
    
    async def _process_html_images(self, html_content: str) -> str:
        """Embed Salesforce public-asset images as inline base64 data URIs."""
        return await embed_html_images_as_base64(
            html_content,
            self._http_client,
            self.logger,
            # Only inline images hosted on Salesforce public file-asset URLs.
            url_filter=lambda src: bool(re.search(r"/file-asset-public/[^?]+", src)),
        )

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """
        Stream record content.

        For FILE records: streams file bytes from Salesforce ContentVersion (like Dropbox),
        using create_stream_record_response with filename and mime_type.

        For PRODUCT, DEAL, CASE, TASK: streams BlocksContainer JSON.
        """

        await self._reinitialize_token_if_needed()
        if not self.data_source:
            self.logger.error("Salesforce data source not initialized")
            return

        try:
            if record.record_type == RecordType.FILE:
                return create_stream_record_response(
                    self._stream_salesforce_file_content(record),
                    filename=record.record_name,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}",
                )
            if record.record_type == RecordType.PRODUCT:
                content_bytes = await self._process_product_record(record)
            elif record.record_type == RecordType.DEAL:
                content_bytes = await self._process_deal_record(record)
            elif record.record_type == RecordType.CASE:
                content_bytes = await self._process_case_record(record)
            elif record.record_type == RecordType.TASK:
                content_bytes = await self._process_task_record(record)
            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")
            

            return StreamingResponse(
                iter([content_bytes]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                }
            )

        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}", exc_info=True)
            raise

    async def _process_product_record(self, record: ProductRecord) -> bytes:
        """
        Fetch product description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        product_id = record.external_record_id
        api_version = await self._get_api_version()

        try:
            safe_product_id = _sanitize_soql_id(product_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce product ID: %r", product_id)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid record ID"
            )
        soql_query = (
            f"SELECT Id, Name, Description, ProductCode, Family FROM Product2 WHERE Id = '{safe_product_id}'"
        )
        try:
            product_data = await self._fetch_first_record(
                api_version=api_version, soql=soql_query,
            )
        except Exception as e:
            self.logger.warning("Failed to fetch product %s: %s", product_id, e)
            product_data = None
        if product_data is None:
            description_content = f"# {record.record_name or 'Product'}"
        else:
            description_raw = product_data.get("Description") or ""
            description_content = (
                description_raw
                if description_raw
                else f"# {record.record_name or 'Product'}\n\nNo description available."
            )


        weburl = None
        if self.salesforce_instance_url and product_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{product_id}")
            except Exception:
                pass

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Product Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Salesforce Product description for {record.record_name or 'Product'}",
            source_group_id=f"{product_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
        )

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=[description_block_group]
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_deal_record(self, record: Record) -> bytes:
        """
        Fetch deal (Opportunity) description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        opportunity_id = record.external_record_id
        api_version = await self._get_api_version()

        try:
            safe_opportunity_id = _sanitize_soql_id(opportunity_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce opportunity ID: %r", opportunity_id)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid record ID"
            )
        soql_query = (
            f"SELECT Id, Description FROM Opportunity WHERE Id = '{safe_opportunity_id}'"
        )
        try:
            opportunity_data = await self._fetch_first_record(
                api_version=api_version, soql=soql_query,
            )
        except Exception as e:
            self.logger.error("Failed to fetch opportunity %s: %s", opportunity_id, e)
            opportunity_data = None
        if opportunity_data is None:
            description_content = f"# {record.record_name or 'Deal'}"
        else:
            description_raw = opportunity_data.get("Description") or ""
            description_content = (
                description_raw
                if description_raw
                else f"# {record.record_name or 'Deal'}\n\nNo description available."
            )

        weburl = None
        if self.salesforce_instance_url and opportunity_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{opportunity_id}")
            except Exception:
                pass

        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, opportunity_id
        )
        related_children = await self._get_opportunity_related_child_records(
            api_version, opportunity_id
        )
        all_children = list(description_linked_files) + list(related_children)
        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Deal Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Deal description for {record.record_name or 'Deal'}",
            source_group_id=f"{opportunity_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=all_children if all_children else None,
        )
        block_groups: List[BlockGroup] = [description_block_group]

        # structure_discussion = await self._fetch_structure_discussion(opportunity_id)
        discussion_groups = await self._fetch_and_build_discussion_block_groups(
            parent_id=opportunity_id,
            start_index=len(block_groups),
            weburl=str(weburl) if weburl is not None else None,
        )
        block_groups.extend(discussion_groups)
        self._set_block_group_children(block_groups)

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=block_groups,
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_case_record(self, record: Record) -> bytes:
        """
        Fetch case description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        case_id = record.external_record_id
        api_version = await self._get_api_version()

        try:
            safe_case_id = _sanitize_soql_id(case_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce case ID: %r", case_id)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid record ID"
            )
        soql_query = (
            f"SELECT Id, Subject, Description FROM Case WHERE Id = '{safe_case_id}'"
        )
        try:
            case_data = await self._fetch_first_record(
                api_version=api_version, soql=soql_query,
            )
        except Exception as e:
            self.logger.warning("Failed to fetch case %s: %s", case_id, e)
            case_data = None
        if case_data is None:
            description_content = f"# {record.record_name or 'Case'}"
        else:
            subject = case_data.get("Subject") or ""
            description_raw = case_data.get("Description") or ""
            if subject and description_raw:
                description_content = f"# {subject}\n\n{description_raw}"
            elif subject:
                description_content = f"# {subject}\n\nNo description available."
            elif description_raw:
                description_content = description_raw
            else:
                description_content = f"# {record.record_name or 'Case'}\n\nNo description available."

        weburl = None
        if self.salesforce_instance_url and case_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{case_id}")
            except Exception:
                pass

        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, case_id
        )
        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Case Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Case description for {record.record_name or 'Case'}",
            source_group_id=f"{case_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=(
                description_linked_files if description_linked_files else None
            ),
        )
        block_groups = [description_block_group]

        # structure_discussion = await self._fetch_structure_discussion(case_id)
        discussion_groups = await self._fetch_and_build_discussion_block_groups(
            parent_id=case_id,
            start_index=len(block_groups),
            weburl=str(weburl) if weburl is not None else None,
        )
        block_groups.extend(discussion_groups)
        self._set_block_group_children(block_groups)

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=block_groups,
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_task_record(self, record: Record) -> bytes:
        """
        Fetch task subject and description using external_record_id and return content as BlocksContainer bytes.
        """
        self.logger.debug("_process_task_record start for record_id=%s, external_record_id=%s", record.id, record.external_record_id)
        if not self.data_source:
            self.logger.error("_process_task_record: data_source not initialized for record %s", record.id)
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        task_id = record.external_record_id
        api_version = await self._get_api_version()

        try:
            safe_task_id = _sanitize_soql_id(task_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce task ID: %r", task_id)
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Invalid record ID"
            )
        soql_query = (
            f"SELECT Id, Subject, Description FROM Task WHERE Id = '{safe_task_id}'"
        )
        email_query = (
            f"SELECT Id, Subject, HtmlBody, TextBody, HasAttachment "
            f"FROM EmailMessage WHERE ActivityId = '{safe_task_id}' LIMIT 1"
        )
        try:
            task_data, email_data = await asyncio.gather(
                self._fetch_first_record(api_version=api_version, soql=soql_query),
                self._fetch_first_record(api_version=api_version, soql=email_query),
            )
        except Exception as e:
            self.logger.warning(
                "_process_task_record: Failed to fetch task %s: %s", task_id, e,
            )
            task_data = None
            email_data = None

        if task_data is None:
            description_content = f"# {record.record_name or 'Task'}"
        else:
            subject = task_data.get("Subject") or ""
            description_raw = task_data.get("Description") or ""
            if subject and description_raw:
                description_content = f"# {subject}\n\n{description_raw}"
            elif subject:
                description_content = f"# {subject}\n\nNo description available."
            elif description_raw:
                description_content = description_raw
            else:
                description_content = f"# {record.record_name or 'Task'}\n\nNo description available."

        weburl = None
        if self.salesforce_instance_url and task_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{task_id}")
            except Exception as e:
                self.logger.error(f"Weburl build failed: {e}", exc_info=True)
        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, task_id
        )

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Task Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Task description for {record.record_name or 'Task'}",
            source_group_id=f"{task_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=(
                description_linked_files if description_linked_files else None
            ),
        )
        block_groups = [description_block_group]

        if email_data:
            # 2. Extract metadata
            email_message_id = email_data.get("Id") or ""
            from_addr = email_data.get("FromAddress") or "Unknown Sender"
            to_addr = email_data.get("ToAddress") or "Unknown Recipient"
            subject = email_data.get("Subject") or "(No Subject)"
            
            # 3. Process the Body
            html_body = email_data.get("HtmlBody") or ""
            if html_body:
                # Convert images to base64 and then to Markdown
                processed_html = await self._process_html_images(html_body)
                body_md = html_to_markdown(processed_html)
            else:
                # Fallback to TextBody if HtmlBody is missing
                body_md = email_data.get("TextBody") or "No body content available."
            full_email_context = "\n\n".join([
                f"# {subject}",
                f"**From:** {from_addr}",
                f"**To:** {to_addr}",
                "---",
                body_md
            ])

            # 5. Create the BlockGroup using the full context
            email_bg = BlockGroup(
                id=str(uuid4()),
                index=1,
                parent_index=0,
                name=subject,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Task Email: {subject}",
                source_group_id=f"{task_id}_email_{email_message_id}",
                data=full_email_context,  # PASSING THE WHOLE CONTEXT HERE
                format=DataFormat.MARKDOWN,
                requires_processing=True
            )
            block_groups.append(email_bg)
        
        self._set_block_group_children(block_groups)
        
        try:
            blocks_container = BlocksContainer(
                blocks=[],
                block_groups=block_groups,
            )
            out = blocks_container.model_dump_json(indent=2).encode("utf-8")
            self.logger.debug("Process task record done for record_id=%s, output_bytes=%d", record.id, len(out))
            return out
        except Exception as e:
            self.logger.error(f"BlocksContainer build/serialize failed for record_id={record.id}: {e}", exc_info=True)
            raise

# --------------- Data Sync Methods ---------------

    async def _iter_record_access(
        self,
        user_id: str,
        user_email: str,
        record_ids: List[str],
        api_version: str,
    ) -> AsyncGenerator[Tuple[str, str, str], None]:
        """
        Yields (record_id, user_email, access_level) tuples for a given user,
        streaming results as each composite batch resolves.
        """
        unique_ids = list(set(rid for rid in record_ids if rid))
        if not unique_ids or not user_id:
            return

        try:
            safe_user_id = _sanitize_soql_id(user_id)
        except ValueError:
            self.logger.warning("Invalid Salesforce user ID in access check: %r", user_id)
            return

        SOQL_BATCH_SIZE = 200
        COMPOSITE_BATCH_SIZE = 25

        base_query_url = f"/services/data/v{api_version}/query/"

        sub_requests = []
        for i in range(0, len(unique_ids), SOQL_BATCH_SIZE):
            batch_ids = unique_ids[i : i + SOQL_BATCH_SIZE]
            safe_ids = _sanitize_soql_ids_batch(batch_ids, self.logger, "access check")
            if not safe_ids:
                continue
            ids_str = "','".join(safe_ids)
            query = (
                f"SELECT RecordId, MaxAccessLevel FROM UserRecordAccess "
                f"WHERE UserId='{safe_user_id}' "
                f"AND RecordId IN ('{ids_str}')"
            )
            sub_requests.append({
                "method": "GET",
                "url": f"{base_query_url}?q={quote(query)}",
                "referenceId": f"access_batch_{i}"
            })

        for i in range(0, len(sub_requests), COMPOSITE_BATCH_SIZE):
            batch = sub_requests[i : i + COMPOSITE_BATCH_SIZE]
            try:
                response = await self.data_source.composite(
                    version=api_version,
                    data={"compositeRequest": batch}
                )
                if not response.success or not response.data:
                    self.logger.error(f"Composite access check failed: {response.error}")
                    continue

                for item in response.data.get("compositeResponse") or []:
                    if item.get("httpStatusCode") != 200:
                        continue

                    for row in (item.get("body") or {}).get("records") or []:
                        rec_id = row.get("RecordId")
                        max_access = row.get("MaxAccessLevel")

                        if max_access in ("All", "Transfer"):
                            access_level = "OWNER"
                        elif max_access in ("Edit", "Delete"):
                            access_level = "WRITER"
                        elif max_access == "Read":
                            access_level = "READER"
                        else:
                            continue  # Skip OTHERS entirely — no yield, no DB write

                        yield rec_id, user_email, access_level

            except Exception as e:
                self.logger.error(
                    f"Error processing access batch {i} for user {user_email}: {e}",
                    exc_info=True
                )

    async def run_sync(self) -> None:
            """
            Runs a full synchronization from the Salesforce instance.
            Syncs roles first (with hierarchy), then users with role permissions.
            """
            await self._reinitialize_token_if_needed()
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            try:
                self.logger.info("Starting Salesforce full sync.")

                self.sync_filters, self.indexing_filters = await load_connector_filters(
                    self.config_service, "salesforce", self.connector_id, self.logger
                )

                # Each datetime filter from the framework is a (start, end) tuple where
                # exactly which side is populated depends on the operator:
                #   is_after  → (start, None)
                #   is_before → (None, end)
                #   is_between→ (start, end)
                # Reading both endpoints here keeps the rest of the sync agnostic to
                # which operator the user picked.
                _created_f = self.sync_filters.get(SyncFilterKey.CREATED) if self.sync_filters else None
                _modified_f = self.sync_filters.get(SyncFilterKey.MODIFIED) if self.sync_filters else None
                opp_case_created_after_ms: Optional[int] = (
                    _created_f.get_datetime_start() if _created_f and not _created_f.is_empty() else None
                )
                opp_case_created_before_ms: Optional[int] = (
                    _created_f.get_datetime_end() if _created_f and not _created_f.is_empty() else None
                )
                opp_case_updated_after_ms: Optional[int] = (
                    _modified_f.get_datetime_start() if _modified_f and not _modified_f.is_empty() else None
                )
                opp_case_updated_before_ms: Optional[int] = (
                    _modified_f.get_datetime_end() if _modified_f and not _modified_f.is_empty() else None
                )
                if opp_case_created_after_ms or opp_case_created_before_ms:
                    self.logger.info(
                        "Date filter active — created_at in [%s, %s] ms (Opportunities + Cases)",
                        opp_case_created_after_ms,
                        opp_case_created_before_ms,
                    )
                if opp_case_updated_after_ms or opp_case_updated_before_ms:
                    self.logger.info(
                        "Date filter active — updated_at in [%s, %s] ms (Opportunities + Cases)",
                        opp_case_updated_after_ms,
                        opp_case_updated_before_ms,
                    )

                api_version = await self._get_api_version()

                # Step 1: Incremental sync for users
                self.logger.info("Syncing users (incremental)...")
                user_sync_point = await self.user_sync_point.read_sync_point(USERS_SYNC_POINT_KEY)
                last_sync_ts_ms = user_sync_point.get("lastSyncTimestamp")
                base_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, MobilePhone, Title, CreatedDate, LastModifiedDate, UserRoleId FROM User"
                )
                if last_sync_ts_ms:
                    soql_datetime = epoch_ms_to_iso(last_sync_ts_ms)
                    soql_query = f"{base_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Incremental user sync: fetching users modified since %s", soql_datetime)
                else:
                    soql_query = f"{base_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Full user sync: no previous sync point, fetching all users")
                await self._sync_users(
                    self._soql_query_paginated(api_version=api_version, q=soql_query),
                )
                await self.user_sync_point.update_sync_point(
                    USERS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 2: Incremental sync for roles
                self.logger.info("Syncing roles and role permissions (incremental)...")
                roles_sync_point = await self.records_sync_point.read_sync_point(ROLES_SYNC_POINT_KEY)
                roles_last_ts_ms = roles_sync_point.get("lastSyncTimestamp")
                base_roles_soql = "SELECT Id, Name, ParentRoleId, DeveloperName, SystemModstamp FROM UserRole"
                base_user_to_role_soql = "SELECT Id, FirstName, LastName, Email, Phone, MobilePhone, Title, CreatedDate, LastModifiedDate, UserRoleId FROM User WHERE UserRoleId != null"
                if roles_last_ts_ms:
                    soql_datetime = epoch_ms_to_iso(roles_last_ts_ms)
                    soql_query = f"{base_roles_soql} WHERE SystemModstamp >= {soql_datetime} ORDER BY SystemModstamp ASC"
                    soql_user_to_role_query = f"{base_user_to_role_soql} AND LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Incremental roles sync: fetching since %s", soql_datetime)
                else:
                    soql_query = f"{base_roles_soql} ORDER BY SystemModstamp ASC"
                    soql_user_to_role_query = f"{base_user_to_role_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Full roles sync: no previous sync point")
                await self._sync_roles(
                    self._soql_query_paginated(api_version=api_version, q=soql_query),
                    self._soql_query_paginated(api_version=api_version, q=soql_user_to_role_query),
                )
                await self.records_sync_point.update_sync_point(
                    ROLES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 3: Full sync for Public Groups and Queues
                self.logger.info("Syncing Public Groups and Queues (full)...")
                base_groups_soql = "SELECT Id, Name, DeveloperName, Type, CreatedDate, LastModifiedDate FROM Group WHERE Type IN ('Regular', 'Queue')"
                await self._sync_user_groups(
                    api_version=api_version,
                    group_records_pages=self._soql_query_paginated(
                        api_version=api_version, q=base_groups_soql,
                    ),
                )

                # Step 4: Incremental sync for Accounts
                self.logger.info("Syncing Accounts and sales prospect edges (incremental)...")
                accounts_sync_point = await self.records_sync_point.read_sync_point(ACCOUNTS_SYNC_POINT_KEY)
                accounts_last_ts_ms = accounts_sync_point.get("lastSyncTimestamp")
                soql_accounts_query = (
                    "SELECT Name, Website, Industry, Ownership, Phone, DunsNumber, Owner.Name, Type, Rating, "
                    "Id, CreatedDate, LastModifiedDate, SystemModstamp, "
                    "(SELECT Id, CloseDate, IsWon, IsClosed FROM Opportunities ORDER BY CloseDate ASC) "
                    "FROM Account"
                )
                if accounts_last_ts_ms:
                    soql_datetime = epoch_ms_to_iso(accounts_last_ts_ms)
                    self.logger.debug("Incremental account sync: fetching accounts changed since %s", soql_datetime)
                    account_pages = self._get_updated_account(
                        api_version=api_version,
                        soql_datetime=soql_datetime,
                        soql_accounts_query=soql_accounts_query,
                    )
                else:
                    self.logger.debug("Full account sync: no previous sync point")
                    account_pages = self._typed_pages(
                        self._soql_query_paginated(api_version=api_version, q=soql_accounts_query),
                        SalesforceAccount,
                    )
                newly_synced_account_ids = await self._sync_accounts(account_pages)
                await self.records_sync_point.update_sync_point(
                    ACCOUNTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 5: Incremental sync for Contacts
                self.logger.info("Syncing Contacts and sales contact edges (incremental)...")
                contacts_sync_point = await self.records_sync_point.read_sync_point(CONTACTS_SYNC_POINT_KEY)
                contacts_last_ts_ms = contacts_sync_point.get("lastSyncTimestamp")
                base_contacts_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, AccountId, Account.Name, Title, Department, "
                    "Description, LeadSource, CreatedDate, LastModifiedDate FROM Contact"
                )
                if contacts_last_ts_ms:
                    soql_datetime = epoch_ms_to_iso(contacts_last_ts_ms)
                    soql_contacts_query = f"{base_contacts_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Incremental contacts sync: fetching since %s", soql_datetime)
                else:
                    soql_contacts_query = f"{base_contacts_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Full contacts sync: no previous sync point")
                await self._sync_contacts(
                    self._soql_query_paginated(api_version=api_version, q=soql_contacts_query),
                )
                await self.records_sync_point.update_sync_point(
                    CONTACTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 6: Incremental sync for Leads
                self.logger.info("Syncing Leads and sales lead edges (incremental)...")
                leads_sync_point = await self.records_sync_point.read_sync_point(LEADS_SYNC_POINT_KEY)
                leads_last_ts_ms = leads_sync_point.get("lastSyncTimestamp")
                base_leads_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, Company, Title, Status, "
                    "Rating, Industry, LeadSource, AnnualRevenue, CreatedDate, LastModifiedDate, ConvertedDate, "
                    "ConvertedContactId FROM Lead"
                )
                if leads_last_ts_ms:
                    soql_datetime = epoch_ms_to_iso(leads_last_ts_ms)
                    soql_leads_query = f"{base_leads_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Incremental leads sync: fetching since %s", soql_datetime)
                else:
                    soql_leads_query = f"{base_leads_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Full leads sync: no previous sync point")
                await self._sync_leads(
                    self._soql_query_paginated(api_version=api_version, q=soql_leads_query),
                )
                await self.records_sync_point.update_sync_point(
                    LEADS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 7: Incremental sync for Products
                # 7.1 Ensure product record group exists (org -> record group BELONGS_TO + org -> record group PERMISSION)
                product_record_group = RecordGroup(
                    name="Products",
                    external_group_id= self.data_entities_processor.org_id + "-product",
                    group_type=RecordGroupType.PRODUCT,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                )
                # Platform permission: org can read the product record group (enables org-level access to product records)
                org_permission = Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                )
                await self.data_entities_processor.on_new_record_groups([(product_record_group, [org_permission])])
                self.logger.debug("Product record group ensured.")

                self.logger.info("Syncing Products (incremental)...")
                products_sync_point = await self.records_sync_point.read_sync_point(PRODUCTS_SYNC_POINT_KEY)
                products_last_ts_ms = products_sync_point.get("lastSyncTimestamp")
                base_products_soql = (
                    "SELECT Id, Name, ProductCode, Family, IsActive, "
                    "StockKeepingUnit, CreatedDate, LastModifiedDate FROM Product2"
                )
                await self._sync_products(
                    self._get_updated_product(api_version, products_last_ts_ms, base_products_soql),
                )
                await self.records_sync_point.update_sync_point(
                    PRODUCTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 8: Incremental sync for Opportunities as deals, record groups per account, dealOf/dealInfo/permission edges
                self.logger.info("Syncing Opportunities (deals) (incremental)...")
                opportunities_sync_point = await self.records_sync_point.read_sync_point(DEALS_SYNC_POINT_KEY)
                opportunities_last_ts_ms = opportunities_sync_point.get("lastSyncTimestamp")
                base_opportunities_soql = (
                    "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, ExpectedRevenue, CloseDate, "
                    "Probability, Type, Description, OwnerId, Owner.Name, IsWon, IsClosed, "
                    "CreatedDate, LastModifiedDate FROM Opportunity"
                )
                _opp_extra_where: list[str] = _date_bound_conditions(
                    "CreatedDate", opp_case_created_after_ms, opp_case_created_before_ms
                )
                opp_pages = self._get_updated_deal(
                    api_version,
                    opportunities_last_ts_ms,
                    base_opportunities_soql,
                    extra_where_conditions=_opp_extra_where,
                    updated_after_ms=opp_case_updated_after_ms,
                    updated_before_ms=opp_case_updated_before_ms,
                )
                # The FeedItem/FeedComment OR-branches in the incremental path can resurface
                # parents whose LastModifiedDate is outside the configured updated_at window.
                # Apply the per-page filter here so it runs regardless of the sync-point branch.
                if opp_case_updated_after_ms or opp_case_updated_before_ms:
                    opp_pages = self._filter_pages_by_last_modified(
                        opp_pages, opp_case_updated_after_ms, opp_case_updated_before_ms,
                    )
                newly_synced_opp_ids = await self._sync_opportunities(opp_pages)
                await self.records_sync_point.update_sync_point(
                    DEALS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 8.5: Sync soldIn edges (Product -> Deal) from OpportunityLineItems
                self.logger.info("Syncing soldIn edges (Product -> Deal) from OpportunityLineItems...")
                products_sync_point = await self.records_sync_point.read_sync_point(SOLD_IN_SYNC_POINT_KEY)
                products_last_ts_ms = products_sync_point.get("lastSyncTimestamp")
                base_sold_in_soql = (
                    "SELECT Id, OpportunityId, Product2.Id, Product2.Name, UnitPrice, Quantity, "
                    "CreatedDate, LastModifiedDate, IsDeleted FROM OpportunityLineItem"
                )
                if products_last_ts_ms:
                    soql_datetime = epoch_ms_to_iso(products_last_ts_ms)
                    soql_sold_in_query = f"{base_sold_in_soql} WHERE LastModifiedDate > {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Incremental OpportunityLineItem sync: fetching since %s", soql_datetime)
                else:
                    soql_sold_in_query = f"{base_sold_in_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.debug("Full OpportunityLineItem sync: no product sync point filter")
                await self._sync_sold_in_edges(
                    self._soql_query_paginated(
                        api_version=api_version, q=soql_sold_in_query, queryAll=True,
                    ),
                    api_version,
                )
                await self.records_sync_point.update_sync_point(
                    SOLD_IN_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 9: Incremental sync for Cases
                # 9.1 make a record group for cases with no account id
                org_permission = Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                )
                record_group = RecordGroup(
                    name="Unlinked Cases",
                    external_group_id="UNASSIGNED-CASE",
                    group_type=RecordGroupType.CASE,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                )
                await self.data_entities_processor.on_new_record_groups([(record_group, [org_permission])])
                self.logger.debug(f"Ensured case record group for cases with no account id.")

                # 9.2 sync cases
                self.logger.info("Syncing Cases (incremental)...")
                cases_sync_point = await self.records_sync_point.read_sync_point(CASES_SYNC_POINT_KEY)
                cases_last_ts_ms = cases_sync_point.get("lastSyncTimestamp")
                base_cases_soql = (
                    "SELECT Id, CaseNumber, Subject, Status, Priority, Type, OwnerId, Owner.Email, "
                    "Owner.Name, AccountId, Contact.Email, Contact.Name, CreatedBy.Email, "
                    "CreatedBy.Name, CreatedDate, LastModifiedDate, SystemModstamp FROM Case"
                )
                _case_extra_where: list[str] = _date_bound_conditions(
                    "CreatedDate", opp_case_created_after_ms, opp_case_created_before_ms
                )
                case_pages = self._get_updated_case(
                    api_version,
                    cases_last_ts_ms,
                    base_cases_soql,
                    extra_where_conditions=_case_extra_where,
                    updated_after_ms=opp_case_updated_after_ms,
                    updated_before_ms=opp_case_updated_before_ms,
                )
                if opp_case_updated_after_ms or opp_case_updated_before_ms:
                    case_pages = self._filter_pages_by_last_modified(
                        case_pages, opp_case_updated_after_ms, opp_case_updated_before_ms,
                    )
                newly_synced_case_ids = await self._sync_cases(case_pages)
                await self.records_sync_point.update_sync_point(
                    CASES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Collect parent IDs that became eligible for the first time in this cycle.
                # Used by the task and file sync steps to backfill pre-existing children whose
                # LastModifiedDate predates the sync point cursor.
                newly_synced_parent_ids: Set[str] = (
                    newly_synced_account_ids
                    | newly_synced_opp_ids
                    | newly_synced_case_ids
                )
                if newly_synced_parent_ids:
                    self.logger.info(
                        "Newly eligible parents this cycle: %d "
                        "(%d accounts, %d opportunities, %d cases) — "
                        "will backfill their pre-existing tasks and files",
                        len(newly_synced_parent_ids),
                        len(newly_synced_account_ids),
                        len(newly_synced_opp_ids),
                        len(newly_synced_case_ids),
                    )

                # Step 10: Sync Tasks
                # 10.1 make a record group for tasks with no account id
                org_permission = Permission(
                    entity_type=EntityType.ORG,
                    type=PermissionType.READ,
                )
                record_group = RecordGroup(
                    name="Unlinked Tasks",
                    external_group_id="UNASSIGNED-TASK",
                    group_type=RecordGroupType.TASK,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                )
                await self.data_entities_processor.on_new_record_groups([(record_group, [org_permission])])
                self.logger.debug(f"Ensured task record group for tasks with no account id.")

                self.logger.info("Syncing Tasks (incremental)...")
                base_tasks_soql = (
                    "SELECT Id, Subject, Status, Priority, ActivityDate, Description, WhoId, Who.Email, WhatId, What.Name, What.Type, OwnerId, TaskSubtype, "
                    "Owner.Name, Owner.Email, CreatedBy.Name, CreatedBy.Email, CreatedDate, LastModifiedDate, SystemModstamp FROM Task"
                )
                tasks_sync_point = await self.records_sync_point.read_sync_point(TASKS_SYNC_POINT_KEY)
                tasks_last_ts_ms = tasks_sync_point.get("lastSyncTimestamp")
                await self._sync_tasks(
                    self._get_updated_task(
                        api_version,
                        tasks_last_ts_ms,
                        base_tasks_soql,
                        newly_synced_parent_ids=newly_synced_parent_ids,
                    ),
                    created_after_ms=opp_case_created_after_ms,
                    created_before_ms=opp_case_created_before_ms,
                    updated_after_ms=opp_case_updated_after_ms,
                    updated_before_ms=opp_case_updated_before_ms,
                )
                await self.records_sync_point.update_sync_point(
                    TASKS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 11: Sync Files
                self.logger.info("Syncing Files (incremental)...")
                files_sync_point = await self.records_sync_point.read_sync_point(FILES_SYNC_POINT_KEY)
                files_last_ts_ms = files_sync_point.get("lastSyncTimestamp")
                await self._sync_files(
                    api_version=api_version,
                    file_records_pages=self._get_updated_file(
                        api_version,
                        files_last_ts_ms,
                        newly_synced_parent_ids=newly_synced_parent_ids,
                    ),
                    created_after_ms=opp_case_created_after_ms,
                    created_before_ms=opp_case_created_before_ms,
                    updated_after_ms=opp_case_updated_after_ms,
                    updated_before_ms=opp_case_updated_before_ms,
                )
                await self.records_sync_point.update_sync_point(
                    FILES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 12: Sync permissions edges
                self.logger.info("Syncing permissions edges...")
                await self._sync_permissions_edges(api_version=api_version)

                self.logger.info("Salesforce full sync completed.")

            except Exception as ex:
                self.logger.error(f"Error in Salesforce connector run: {ex}", exc_info=True)
                raise

    async def get_updated_record_ids(
            self,
            since_timestamp_ms: int,
            record_types: Optional[List[str]] = None,
        ) -> Set[str]:
            """
            Get record IDs that have updated discussions, comments, or call logs since a timestamp.
            This queries:
            1. FeedItems (Chatter posts) created/modified since the cutoff
            2. FeedComments (replies to Chatter) created since the cutoff
            3. Tasks (Call logs) created/modified since the cutoff
            """
            if not self.data_source:
                self.logger.error("get_updated_record_ids: data source not initialized")
                return set()

            api_version = await self._get_api_version()
            cutoff_datetime = epoch_ms_to_iso(since_timestamp_ms)
            updated_record_ids: Set[str] = set()

            # ── Query 1: FeedItems created or modified since cutoff ──────────────
            # FeedItems track Chatter posts, tracked field changes, content posts, etc.
            # ParentId points to the parent record (Opportunity, Account, Case, etc.)
            feeditem_soql = (
                "SELECT ParentId, Parent.Type "
                "FROM FeedItem "
                f"WHERE (CreatedDate >= {cutoff_datetime} OR LastModifiedDate >= {cutoff_datetime})"
            )

            if record_types:
                # Filter by parent object type (e.g. only Opportunities and Cases)
                type_list = ", ".join(f"'{t}'" for t in record_types)
                feeditem_soql += f" AND Parent.Type IN ({type_list})"

            try:
                async for page in self._soql_query_paginated(
                    api_version=api_version, q=feeditem_soql,
                ):
                    for row in page:
                        parent_id = row.get("ParentId")
                        if parent_id:
                            updated_record_ids.add(parent_id)
                self.logger.info(
                    f"Found {len(updated_record_ids)} records with updated FeedItems since {cutoff_datetime}"
                )
            except Exception as e:
                self.logger.error(f"Error querying FeedItems: {e}", exc_info=True)

            # ── Query 2: FeedComments created since cutoff ────────────────────────
            # FeedComments are replies to FeedItems. We need to join back to FeedItem
            # to get the ParentId (the actual record ID).
            # Note: FeedComment.FeedItemId -> FeedItem.Id -> FeedItem.ParentId
            feedcomment_soql = (
                "SELECT FeedItemId "
                "FROM FeedComment "
                f"WHERE CreatedDate >= {cutoff_datetime}"
            )

            try:
                feed_item_ids: List[str] = []
                async for page in self._soql_query_paginated(
                    api_version=api_version, q=feedcomment_soql,
                ):
                    for row in page:
                        fid = row.get("FeedItemId")
                        if fid:
                            feed_item_ids.append(fid)

                if feed_item_ids:
                    # Batch lookup of FeedItem.ParentId for these FeedItems.
                    # Salesforce allows up to ~4000 items in an IN clause; batch at
                    # 500 for safety (same as the existing pattern).
                    BATCH_SIZE = 500
                    for i in range(0, len(feed_item_ids), BATCH_SIZE):
                        batch = feed_item_ids[i : i + BATCH_SIZE]
                        in_clause = ", ".join(f"'{fid}'" for fid in batch)

                        lookup_soql = (
                            f"SELECT ParentId, Parent.Type FROM FeedItem WHERE Id IN ({in_clause})"
                        )
                        if record_types:
                            type_list = ", ".join(f"'{t}'" for t in record_types)
                            lookup_soql += f" AND Parent.Type IN ({type_list})"

                        async for lookup_page in self._soql_query_paginated(
                            api_version=api_version, q=lookup_soql,
                        ):
                            for row in lookup_page:
                                parent_id = row.get("ParentId")
                                if parent_id:
                                    updated_record_ids.add(parent_id)

                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including FeedComment updates"
                )
            except Exception as e:
                self.logger.error(f"Error querying FeedComments: {e}", exc_info=True)

            # ── Query 3: Tasks (Call logs) created or modified since cutoff ──────
            # TaskSubtype = 'Call' identifies call logs
            # WhatId points to the parent record (Opportunity, Case, Account, etc.)
            task_soql = (
                "SELECT WhatId, What.Type "
                "FROM Task "
                f"WHERE TaskSubtype = 'Call' "
                f"AND (CreatedDate >= {cutoff_datetime} OR LastModifiedDate >= {cutoff_datetime})"
            )

            if record_types:
                type_list = ", ".join(f"'{t}'" for t in record_types)
                task_soql += f" AND What.Type IN ({type_list})"

            try:
                async for page in self._soql_query_paginated(
                    api_version=api_version, q=task_soql,
                ):
                    for row in page:
                        what_id = row.get("WhatId")
                        if what_id:
                            updated_record_ids.add(what_id)
                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including Call logs"
                )
            except Exception as e:
                self.logger.error(f"Error querying Tasks: {e}", exc_info=True)

            # ── Query 4: Comments on Call logs ────────────────────────────────────
            # Call logs can have FeedComments too — we need to find Tasks that have
            # new comments, then add their WhatId to the set.
            # Strategy: Query FeedComments where ParentId (the Task.Id) is a Call Task,
            # then look up the Task to get WhatId.
            call_comment_soql = (
                "SELECT FeedItemId "
                "FROM FeedComment "
                f"WHERE CreatedDate >= {cutoff_datetime}"
            )

            try:
                feed_item_ids_for_calls: List[str] = []
                async for page in self._soql_query_paginated(
                    api_version=api_version, q=call_comment_soql,
                ):
                    for row in page:
                        fid = row.get("FeedItemId")
                        if fid:
                            feed_item_ids_for_calls.append(fid)

                if feed_item_ids_for_calls:
                    BATCH_SIZE = 500
                    for i in range(0, len(feed_item_ids_for_calls), BATCH_SIZE):
                        batch = feed_item_ids_for_calls[i : i + BATCH_SIZE]
                        in_clause = ", ".join(f"'{fid}'" for fid in batch)

                        # Find FeedItems whose Parent is a Task with TaskSubtype = 'Call'
                        lookup_soql = (
                            f"SELECT ParentId, Parent.Type FROM FeedItem WHERE Id IN ({in_clause}) "
                            f"AND Parent.Type = 'Task'"
                        )

                        task_ids: List[str] = []
                        async for lookup_page in self._soql_query_paginated(
                            api_version=api_version, q=lookup_soql,
                        ):
                            for row in lookup_page:
                                pid = row.get("ParentId")
                                if pid:
                                    task_ids.append(pid)

                        if task_ids:
                            # Fetch the WhatId for these Tasks (filtering TaskSubtype = Call)
                            task_in_clause = ", ".join(f"'{tid}'" for tid in task_ids)
                            task_lookup_soql = (
                                f"SELECT WhatId, What.Type FROM Task "
                                f"WHERE Id IN ({task_in_clause}) AND TaskSubtype = 'Call'"
                            )
                            if record_types:
                                type_list = ", ".join(f"'{t}'" for t in record_types)
                                task_lookup_soql += f" AND What.Type IN ({type_list})"

                            async for task_page in self._soql_query_paginated(
                                api_version=api_version, q=task_lookup_soql,
                            ):
                                for row in task_page:
                                    what_id = row.get("WhatId")
                                    if what_id:
                                        updated_record_ids.add(what_id)

                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including Call log comments"
                )
            except Exception as e:
                self.logger.error(f"Error querying Call log comments: {e}", exc_info=True)

            self.logger.info(
                f"get_updated_record_ids complete: {len(updated_record_ids)} unique record IDs "
                f"with updated activity since {cutoff_datetime}"
            )
            return updated_record_ids

    def user_to_app_user(self, user: SalesforceUser) -> AppUser:
        return AppUser(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_id=user.Id,
            email=user.Email or "",
            first_name=user.FirstName or "",
            last_name=user.LastName or "",
            full_name=f"{user.FirstName or ''} {user.LastName or ''}".strip(),
            org_id=self.data_entities_processor.org_id,
            title=user.Title,
            source_created_at=_parse_salesforce_timestamp(user.CreatedDate) or None,
            source_updated_at=_parse_salesforce_timestamp(user.LastModifiedDate) or None,
        )

    def role_to_app_role(self, role: SalesforceRole) -> AppRole:
        return AppRole(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_role_id=role.Id,
            name=role.Name or "",
            org_id=self.data_entities_processor.org_id,
            parent_role_id=role.ParentRoleId,
            source_updated_at=_parse_salesforce_timestamp(role.SystemModstamp),
        )

    async def _sync_users(
        self, user_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
    ) -> None:
        """Sync users from Salesforce, committing each Salesforce page in its own
        transaction.  Per-page commit semantics: a crash mid-stream leaves earlier
        pages persisted; the sync point is only advanced if every page completes."""
        try:
            total = 0
            async for page in user_records_pages:
                if not page:
                    continue
                parsed_users = [SalesforceUser.model_validate(r) for r in page]
                app_users = [self.user_to_app_user(u) for u in parsed_users]
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total += len(app_users)
            self.logger.info("Finished syncing %d Salesforce users", total)
        except Exception as e:
            self.logger.error(f"Error syncing users: {e}", exc_info=True)
            raise

    async def _sync_roles(
        self,
        role_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
        user_role_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
    ) -> None:
        """Sync roles with their member users.  The user-role stream is drained first
        into a map (bounded by user count, the smaller of the two); roles then stream
        through with per-page commits."""
        try:
            self.logger.info("Starting Salesforce role sync...")
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            # Drain user-role records into the lookup map first.
            role_to_app_users: Dict[str, List[AppUser]] = {}
            async for page in user_role_records_pages:
                for r in page:
                    user = SalesforceUser.model_validate(r)
                    role_id = user.UserRoleId
                    if not role_id:
                        continue
                    role_to_app_users.setdefault(role_id, []).append(
                        self.user_to_app_user(user),
                    )

            total_roles = 0
            async for page in role_records_pages:
                if not page:
                    continue
                roles_batch: List[Tuple[AppRole, List[AppUser]]] = []
                for r in page:
                    role = SalesforceRole.model_validate(r)
                    members = role_to_app_users.get(role.Id, [])
                    roles_batch.append((self.role_to_app_role(role), members))
                if roles_batch:
                    await self.data_entities_processor.on_new_app_roles(roles_batch)
                    total_roles += len(roles_batch)

            self.logger.info("Finished syncing %d Salesforce roles", total_roles)

        except Exception as e:
            self.logger.error(f"Error syncing roles: {e}", exc_info=True)
            raise

    async def _sync_user_groups(
        self,
        api_version: str,
        group_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
    ) -> None:
        """Sync Public Groups and Queues with flattened membership hierarchy.

        The flattened-membership map is fetched once before the page loop (it is the
        cross-page lookup); each page of group records then commits independently.
        """
        try:
            self.logger.info("Starting Salesforce Public Groups and Queues sync...")

            flattened_user_memberships = await self._flatten_group_members(api_version)
            if not flattened_user_memberships:
                self.logger.warning("No flattened group memberships found")
                # Still drain the input so the producer can release resources.
                async for _ in group_records_pages:
                    pass
                return

            org_id = self.data_entities_processor.org_id
            total_groups = 0

            async for page in group_records_pages:
                user_groups_batch: List[Tuple[AppUserGroup, List[AppUser]]] = []
                for r in page:
                    group = SalesforceGroup.model_validate(r)
                    group_id = group.Id
                    if not group_id:
                        continue
                    user_data_set = flattened_user_memberships.get(group_id, set())
                    if not user_data_set:
                        self.logger.debug(
                            "Group %s (%s) has no users", group.Name or "", group_id,
                        )
                        continue
                    app_user_group = AppUserGroup(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_group_id=group_id,
                        name=group.Name or "",
                        org_id=org_id,
                        description=f"Salesforce {group.Type or ''}",
                        source_created_at=_parse_salesforce_timestamp(group.CreatedDate),
                        source_updated_at=_parse_salesforce_timestamp(group.LastModifiedDate),
                    )
                    app_users = [
                        AppUser(
                            app_name=self.connector_name,
                            connector_id=self.connector_id,
                            source_user_id=user_id,
                            email=email,
                            full_name="",
                            org_id=org_id,
                        )
                        for user_id, email in user_data_set
                    ]
                    if app_users:
                        user_groups_batch.append((app_user_group, app_users))

                if user_groups_batch:
                    await self.data_entities_processor.on_new_user_groups(user_groups_batch)
                    total_groups += len(user_groups_batch)

            self.logger.info(
                "Finished syncing %d Salesforce Public Groups/Queues", total_groups,
            )

        except Exception as e:
            self.logger.error(f"Error syncing user groups: {e}", exc_info=True)
            raise

    def _parse_opportunities(
        self, acc: SalesforceAccount
    ) -> Tuple[Optional[int], bool]:
        """
        Parse Opportunities subquery on a SalesforceAccount (already sorted by CloseDate ASC).
        Returns (end_time_ms, active_customer):
        - end_time_ms: first won opportunity CloseDate (epoch ms), or None.
        - active_customer: True if any opportunity has IsClosed == False.
        """
        opportunities = acc.Opportunities or {}
        records = opportunities.get("records") or []
        end_time_ms: Optional[int] = None
        active_customer = False
        for opp in records:
            if opp.get("IsClosed") is False:
                active_customer = True
            if end_time_ms is None and opp.get("IsWon") is True:
                close_date = opp.get("CloseDate")
                parsed = _parse_salesforce_timestamp(close_date)
                if parsed is not None:
                    end_time_ms = parsed
        return (end_time_ms, active_customer)

    async def _get_account_ids_for_opportunities(self, opportunity_ids: set) -> Dict[str, str]:
        """
        Bulk-fetch AccountId for a set of Opportunity IDs.
        Returns a mapping of opportunity_id -> account_id.
        """
        return await self._get_account_ids_for_object(
            object_name="Opportunity",
            ids=opportunity_ids,
            log_context="opportunity account lookup",
        )

    async def _get_account_ids_for_cases(self, case_ids: set) -> Dict[str, str]:
        """
        Bulk-fetch AccountId for a set of Case IDs.
        Returns a mapping of case_id -> account_id.
        """
        return await self._get_account_ids_for_object(
            object_name="Case",
            ids=case_ids,
            log_context="case account lookup",
        )

    async def _get_account_ids_for_object(
        self,
        object_name: str,
        ids: set,
        log_context: str,
    ) -> Dict[str, str]:
        """
        Generic bulk-fetch of AccountId for a set of IDs of a given Salesforce object.

        Issues one SOQL query per 500-ID batch; batches are streamed sequentially and
        each page is processed page-by-page to keep memory bounded.

        Returns a mapping of object_id -> account_id (entries with null AccountId
        are omitted).
        """
        if not ids:
            return {}
        api_version = await self._get_api_version()
        _BATCH = 500
        id_list = list(ids)

        queries: List[str] = []
        for i in range(0, len(id_list), _BATCH):
            safe_ids = _sanitize_soql_ids_batch(
                id_list[i : i + _BATCH], self.logger, log_context,
            )
            if not safe_ids:
                continue
            ids_clause = "','".join(safe_ids)
            queries.append(
                f"SELECT Id, AccountId FROM {object_name} WHERE Id IN ('{ids_clause}')"
            )

        result: Dict[str, str] = {}
        for q in queries:
            async for page in self._soql_query_paginated(api_version=api_version, q=q):
                for rec in page:
                    if rec.get("Id") and rec.get("AccountId"):
                        result[rec["Id"]] = rec["AccountId"]
        return result

    def _build_product_record(self, product: SalesforceProduct, list_price: Optional[float] = None) -> ProductRecord:
        """
        Build a ProductRecord from a raw Salesforce Product2 API row.

        Expected keys: Id, Name, ProductCode, Family, IsActive,
        StockKeepingUnit, CreatedDate, LastModifiedDate.
        list_price is optionally provided from a PricebookEntry lookup.
        """
        product_id = product.Id
        weburl = HttpUrl(f"{self.salesforce_instance_url}/{product_id}")
        product_record = ProductRecord(
            record_name=product.Name or "",
            record_type=RecordType.PRODUCT,
            record_group_type=RecordGroupType.PRODUCT,
            external_record_id=product_id,
            external_revision_id=product.SystemModstamp,
            external_record_group_id=self.data_entities_processor.org_id + "-product",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            product_code=product.ProductCode,
            product_family=product.Family,
            is_active=product.IsActive,
            sku=product.StockKeepingUnit,
            list_price=list_price,
            source_created_at=_parse_salesforce_timestamp(product.CreatedDate),
            source_updated_at=_parse_salesforce_timestamp(product.LastModifiedDate),
            preview_renderable=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return product_record

    def _build_deal_record(self, opp: SalesforceOpportunity) -> DealRecord:
        """
        Build a DealRecord from a raw Salesforce Opportunity API row.

        Expected keys: Id, Name, AccountId, Account (nested), StageName, Amount, ExpectedRevenue,
        CloseDate, Probability, Type, OwnerId, IsWon, IsClosed, CreatedDate, LastModifiedDate.
        """
        opp_id = opp.Id
        account_id = "UNASSIGNED-DEAL"
        if opp.AccountId and (opp.Account or {}).get("Name"):
            account_id = opp.AccountId
        last_modified_epoch = _parse_salesforce_timestamp(opp.LastModifiedDate)
        latest_comment_epoch: Optional[int] = opp.latest_comment_epoch
        effective_revision_epoch: Optional[int] = max(
            filter(lambda x: x is not None, [last_modified_epoch, latest_comment_epoch]),
            default=None,
        )
        weburl = (
            str(HttpUrl(f"{self.salesforce_instance_url}/{opp_id}"))
            if self.salesforce_instance_url and opp_id
            else None
        )
        deal_record = DealRecord(
            record_name=opp.Name or "",
            record_type=RecordType.DEAL,
            record_group_type=RecordGroupType.DEAL,
            external_record_id=opp_id,
            external_record_group_id=account_id,
            version=1,
            external_revision_id=str(effective_revision_epoch) if effective_revision_epoch is not None else None,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            org_id=self.data_entities_processor.org_id,
            name=opp.Name or "",
            amount=float(opp.Amount) if opp.Amount is not None else None,
            expected_revenue=float(opp.ExpectedRevenue) if opp.ExpectedRevenue is not None else None,
            expected_close_date=str(opp.CloseDate) if opp.CloseDate else None,
            conversion_probability=float(opp.Probability) if opp.Probability is not None else None,
            type=opp.Type,
            owner_id=opp.OwnerId,
            is_won=opp.IsWon,
            is_closed=opp.IsClosed,
            created_date=str(opp.CreatedDate) if opp.CreatedDate else None,
            close_date=str(opp.CloseDate) if opp.CloseDate else None,
            source_created_at=_parse_salesforce_timestamp(opp.CreatedDate),
            source_updated_at=last_modified_epoch,
            inherit_permissions=False,
            preview_renderable=False,
            weburl=weburl,
        )
        return deal_record

    def _build_case_record(self, case_row: SalesforceCase) -> TicketRecord:
        """
        Build a TicketRecord (CASE) from a raw Salesforce Case API row.

        Expected keys: Id, Status, Priority, Type, Owner (nested), Contact (nested),
        CreatedBy (nested), AccountId, SystemModstamp, CreatedDate, LastModifiedDate.
        """
        case_id = case_row.Id

        case_number = case_row.CaseNumber
        case_name = case_row.Subject or f"Case {case_number}"
        account = case_row.AccountId or None
        external_record_group_id = account if account else "UNASSIGNED-CASE"
        weburl = None
        if self.salesforce_instance_url and case_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{case_id}")
            except Exception:
                pass
        owner = case_row.Owner or {}
        reporter = case_row.Contact or {}
        created_by = case_row.CreatedBy or {}
        system_modstamp_epoch = _parse_salesforce_timestamp(case_row.SystemModstamp)
        latest_feeditem_epoch: Optional[int] = case_row.latest_comment_epoch
        effective_revision_epoch: Optional[int] = max(
            filter(lambda x: x is not None, [system_modstamp_epoch, latest_feeditem_epoch]),
            default=None,
        )
        case_record = TicketRecord(
            record_name=case_name,
            record_type=RecordType.CASE,
            record_group_type=RecordGroupType.CASE,
            external_record_id=case_id,
            external_record_group_id=external_record_group_id,
            external_revision_id=str(effective_revision_epoch) if effective_revision_epoch is not None else None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            status=case_row.Status,
            priority=case_row.Priority,
            type=case_row.Type,
            assignee=owner.get("Name"),
            assignee_email=owner.get("Email"),
            reporter_name=reporter.get("Name"),
            reporter_email=reporter.get("Email"),
            creator_name=created_by.get("Name"),
            creator_email=created_by.get("Email"),
            source_created_at=_parse_salesforce_timestamp(case_row.CreatedDate),
            source_updated_at=_parse_salesforce_timestamp(case_row.LastModifiedDate),
            inherit_permissions=False,
            preview_renderable=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return case_record

    def _build_task_record(
        self,
        task_row: SalesforceTask,
        opp_account_map: Optional[Dict[str, str]] = None,
        case_account_map: Optional[Dict[str, str]] = None,
    ) -> TicketRecord:
        """
        Build a TicketRecord (TASK) from a raw Salesforce Task API row.

        Expected keys: Id, Subject, Status, Priority, TaskSubtype, WhatId, What (nested),
        Owner (nested), CreatedBy (nested), SystemModstamp, ActivityDate, CreatedDate,
        LastModifiedDate.

        opp_account_map and case_account_map are pre-fetched bulk lookups
        (opportunity_id -> account_id, case_id -> account_id) supplied by the
        caller to avoid per-task Salesforce API calls (N+1 problem).
        """
        task_id = task_row.Id
        what_id = task_row.WhatId or None
        what_obj = task_row.What
        what_type = (what_obj.get("Type") if what_obj else None) or None
        _WHAT_TYPE_TO_RECORD_TYPE: Dict[str, RecordType] = {
            "Opportunity": RecordType.DEAL,
            "Case": RecordType.CASE,
            "Product2": RecordType.PRODUCT,
        }
        parent_id = None
        parent_record_type: Optional[RecordType] = None
        if what_type in ("Opportunity", "Case", "Product2"):
            parent_id = what_id
            parent_record_type = _WHAT_TYPE_TO_RECORD_TYPE.get(what_type)
            external_record_group_id = "UNASSIGNED-TASK"
            if what_type == "Product2":
                external_record_group_id = self.data_entities_processor.org_id + "-product"
            elif what_type == "Opportunity":
                account_id = (opp_account_map or {}).get(what_id) if what_id else None
                if account_id:
                    external_record_group_id = account_id
                else:
                    external_record_group_id = "UNASSIGNED-DEAL"
            elif what_type == "Case":
                account_id = (case_account_map or {}).get(what_id) if what_id else None
                if account_id:
                    external_record_group_id = account_id
                else:
                    external_record_group_id = "UNASSIGNED-CASE"
        elif what_type == "Account":
            external_record_group_id = what_id
        else:
            external_record_group_id = "UNASSIGNED-TASK"
        weburl = None
        if self.salesforce_instance_url and task_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{task_id}")
            except Exception:
                pass
        subject = task_row.Subject or ""
        record_name = subject if subject else f"Task {task_id}"
        owner = task_row.Owner or {}
        created_by = task_row.CreatedBy or {}
        task_record = TicketRecord(
            record_name=record_name,
            record_type=RecordType.TASK,
            record_group_type=RecordGroupType.TASK,
            external_record_id=task_id,
            external_record_group_id=external_record_group_id,
            parent_external_record_id=parent_id,
            parent_record_type=parent_record_type,
            external_revision_id=task_row.SystemModstamp or None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            status=task_row.Status,
            priority=task_row.Priority,
            type=task_row.TaskSubtype,
            assignee=owner.get("Name"),
            assignee_email=owner.get("Email"),
            creator_name=created_by.get("Name"),
            creator_email=created_by.get("Email"),
            due_date_timestamp=_parse_salesforce_timestamp(task_row.ActivityDate),
            source_created_at=_parse_salesforce_timestamp(task_row.CreatedDate),
            source_updated_at=_parse_salesforce_timestamp(task_row.LastModifiedDate),
            preview_renderable=False,
            inherit_permissions=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return task_record

    def _build_file_record(
        self,
        meta: Optional[SalesforceContentVersion],
        ext_id: str,
        parent_id: Optional[str] = None,
        external_record_group_id: Optional[str] = None,
        parent_record_type: Optional[RecordType] = None,
    ) -> Optional[FileRecord]:
        """
        Build a FileRecord from a Salesforce ContentVersion metadata model.

        Args:
            meta: ContentVersion row (fields: Id, ContentDocumentId, PathOnClient, Title,
                  ContentSize, FileExtension, Checksum, LastModifiedDate, CreatedDate).
            ext_id: The ContentVersionId used as external_record_id.
            parent_id: Optional external record ID of the linked parent (Opportunity/Case/Task).
            external_record_group_id: Optional external group ID for the record group.
            parent_record_type: Optional RecordType of the parent record.

        Returns FileRecord, or None if meta is missing required fields.
        """
        if meta is None or not meta.Id:
            return None
        doc_id = meta.ContentDocumentId or ext_id
        mime_type = mimetypes.guess_type(meta.PathOnClient or "")[0] or MimeTypes.UNKNOWN.value
        cv_id = meta.Id
        weburl = HttpUrl(f"{self.salesforce_instance_url}/{doc_id}")
        file_record = FileRecord(
            id=str(uuid4()),
            record_name=meta.PathOnClient or meta.Title or "Unknown",
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.SALESFORCE_FILE,
            external_record_id=ext_id,
            external_revision_id=cv_id,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            weburl=str(weburl) if weburl is not None else None,
            size_in_bytes=meta.ContentSize,
            extension=meta.FileExtension,
            md5_hash=meta.Checksum,
            mime_type=mime_type,
            is_file=True,
            source_updated_at=_parse_salesforce_timestamp(meta.LastModifiedDate),
            source_created_at=_parse_salesforce_timestamp(meta.CreatedDate),
            external_record_group_id=external_record_group_id,
            parent_external_record_id=parent_id,
            parent_record_type=parent_record_type,
        )
        return file_record

    async def _fetch_salesforce_record_if_updated(
        self,
        external_id: str,
        record_type: str,
        since_timestamp_ms: int,
        api_version: str,
    ) -> Optional[Record]:
        """
        Query Salesforce for a single record by external ID and return a fully-built
        Record if it was modified after since_timestamp_ms, or None otherwise.

        For each supported type the method:
        1. Converts since_timestamp_ms to a SOQL datetime string.
        2. Runs a typed SOQL query filtered by Id (or ContentDocumentId for FILE)
           and LastModifiedDate >= soql_datetime.
        3. If the result has no rows returns None.
        4. Builds and returns the typed record using the appropriate _build_* method.
        """
        soql_datetime = epoch_ms_to_iso(since_timestamp_ms)

        try:
            if record_type == RecordType.DEAL:
                safe_id = _sanitize_soql_id(external_id)
                soql = (
                    "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, ExpectedRevenue, CloseDate, "
                    "Probability, Type, Description, OwnerId, Owner.Name, IsWon, IsClosed, "
                    f"CreatedDate, LastModifiedDate FROM Opportunity "
                    f"WHERE Id = '{safe_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                row = await self._fetch_first_record(api_version=api_version, soql=soql)
                if row is None:
                    return None
                return self._build_deal_record(SalesforceOpportunity.model_validate(row))

            elif record_type == RecordType.CASE:
                safe_id = _sanitize_soql_id(external_id)
                soql = (
                    "SELECT Id, CaseNumber, Subject, Status, Priority, Type, OwnerId, Owner.Email, "
                    "Owner.Name, AccountId, Contact.Email, Contact.Name, CreatedBy.Email, "
                    "CreatedBy.Name, CreatedDate, LastModifiedDate, SystemModstamp FROM Case "
                    f"WHERE Id = '{safe_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                row = await self._fetch_first_record(api_version=api_version, soql=soql)
                if row is None:
                    return None
                return self._build_case_record(SalesforceCase.model_validate(row))

            elif record_type == RecordType.TASK:
                safe_id = _sanitize_soql_id(external_id)
                soql = (
                    "SELECT Id, Subject, Status, Priority, ActivityDate, Description, WhoId, Who.Email, "
                    "WhatId, What.Name, What.Type, OwnerId, TaskSubtype, "
                    "Owner.Name, Owner.Email, CreatedBy.Name, CreatedBy.Email, "
                    f"CreatedDate, LastModifiedDate, SystemModstamp FROM Task "
                    f"WHERE Id = '{safe_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                row = await self._fetch_first_record(api_version=api_version, soql=soql)
                if row is None:
                    return None
                return self._build_task_record(SalesforceTask.model_validate(row))

            elif record_type == RecordType.FILE:
                # external_record_id is "{doc_id}-{linked_entity_id}" (linked) or just doc_id (unlinked)
                doc_id = external_id.split("-")[0]
                safe_doc_id = _sanitize_soql_id(doc_id)
                soql = (
                    "SELECT Id, ContentDocumentId, Title, PathOnClient, ContentSize, "
                    "FileExtension, FileType, LastModifiedDate, CreatedDate, Checksum "
                    f"FROM ContentVersion WHERE IsLatest = true "
                    f"AND ContentDocumentId = '{safe_doc_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                row = await self._fetch_first_record(api_version=api_version, soql=soql)
                if row is None:
                    return None
                return self._build_file_record(
                    SalesforceContentVersion.model_validate(row),
                    ext_id=external_id,
                )

            elif record_type == RecordType.PRODUCT:
                safe_id = _sanitize_soql_id(external_id)
                soql = (
                    "SELECT Id, Name, ProductCode, Family, IsActive, "
                    "StockKeepingUnit, CreatedDate, LastModifiedDate FROM Product2 "
                    f"WHERE Id = '{safe_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                row = await self._fetch_first_record(api_version=api_version, soql=soql)
                if row is None:
                    return None
                return self._build_product_record(SalesforceProduct.model_validate(row))
            else:
                self.logger.warning(
                    "Unsupported record type for staleness check: %s (external_id=%s)",
                    record_type,
                    external_id,
                )
                return None

        except ValueError as e:
            self.logger.warning(
                "Invalid Salesforce ID for staleness check, skipping record %s (%s): %s",
                external_id,
                record_type,
                e,
            )
            return None
        except Exception as e:
            self.logger.error(
                "Error checking if record %s (%s) is updated: %s",
                external_id,
                record_type,
                e,
                exc_info=True,
            )
            return None

    async def _get_updated_deal(
        self,
        api_version: str,
        opportunities_last_ts_ms: Optional[int],
        base_opportunities_soql: str,
        extra_where_conditions: Optional[List[str]] = None,
        updated_after_ms: Optional[int] = None,
        updated_before_ms: Optional[int] = None,
    ) -> AsyncGenerator[List[SalesforceOpportunity], None]:
        """Stream Opportunity records for incremental sync.

        When a sync point timestamp is available, yields the deduplicated union of:
        - Opportunities whose LastModifiedDate >= cutoff (direct edits)
        - Opportunities that have new Chatter posts (FeedItem) since the cutoff
        - Opportunities that have new Chatter replies (FeedComment) since the cutoff

        The two `*_dates` queries (small payload — `(ParentId, CreatedDate)` only) are
        drained up-front into the `latest_feed_epoch` map so the three record-bearing
        queries can stream through a running `seen` set.

        extra_where_conditions: additional SOQL condition strings (no WHERE keyword) ANDed
        into every query — used to push user-configured date filters (e.g. CreatedDate >=)
        down to Salesforce rather than filtering in Python.

        updated_after_ms / updated_before_ms: when set and no sync point exists (full scan),
        AND LastModifiedDate >= / <= <date> into the SOQL so Salesforce filters server-side
        instead of downloading all records and discarding them in Python.  Not applied to
        the FeedItem/FeedComment OR-branches in the incremental path because those branches
        intentionally resurface old parents that have new Chatter activity.
        """
        extra = extra_where_conditions or []

        if opportunities_last_ts_ms:
            soql_datetime = epoch_ms_to_iso(opportunities_last_ts_ms)

            soql_modified = (
                f"{base_opportunities_soql} "
                f"{_compose_soql_where(f'LastModifiedDate >= {soql_datetime}', *extra)} "
                f"ORDER BY LastModifiedDate ASC"
            )
            soql_feeditems = (
                f"{base_opportunities_soql} "
                f"{_compose_soql_where(f'Id IN (SELECT ParentId FROM FeedItem WHERE CreatedDate >= {soql_datetime})', 'IsDeleted = false', *extra)}"
            )
            soql_feedcomments = (
                f"{base_opportunities_soql} "
                f"{_compose_soql_where(f'Id IN (SELECT ParentId FROM FeedComment WHERE CreatedDate >= {soql_datetime})', 'IsDeleted = false', *extra)}"
            )
            soql_item_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedItem "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )
            soql_comment_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedComment "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )

            # Drain the two small *_dates queries fully (payload is just ParentId + CreatedDate
            # so memory is bounded by |distinct ParentId|, not record size).
            latest_feed_epoch: Dict[str, int] = {}
            async for date_page in self._soql_query_paginated(
                api_version=api_version, q=soql_item_dates,
            ):
                _accumulate_latest_feed_epoch(date_page, latest_feed_epoch)
            async for date_page in self._soql_query_paginated(
                api_version=api_version, q=soql_comment_dates,
            ):
                _accumulate_latest_feed_epoch(date_page, latest_feed_epoch)

            # Stream the three record-bearing queries through a running `seen` set.
            seen: Set[str] = set()
            total_modified = total_feeditems = total_feedcomments = 0

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_modified,
            ):
                total_modified += len(page)
                opps = self._dedupe_and_enrich_opps(page, seen, latest_feed_epoch)
                if opps:
                    yield opps

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_feeditems,
            ):
                total_feeditems += len(page)
                opps = self._dedupe_and_enrich_opps(page, seen, latest_feed_epoch)
                if opps:
                    yield opps

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_feedcomments,
            ):
                total_feedcomments += len(page)
                opps = self._dedupe_and_enrich_opps(page, seen, latest_feed_epoch)
                if opps:
                    yield opps

            self.logger.info(
                "Incremental opportunities: %d modified, %d from FeedItem, "
                "%d from FeedComment, %d unique after dedupe",
                total_modified, total_feeditems, total_feedcomments, len(seen),
            )
            return

        full_extra = list(extra) + _date_bound_conditions(
            "LastModifiedDate", updated_after_ms, updated_before_ms
        )
        soql_full = (
            f"{base_opportunities_soql} "
            f"{_compose_soql_where(*full_extra)} "
            f"ORDER BY LastModifiedDate ASC"
        ).strip()
        self.logger.debug("Full deals sync: no previous sync point")
        async for page in self._soql_query_paginated(api_version=api_version, q=soql_full):
            yield [SalesforceOpportunity.model_validate(r) for r in page]

    @staticmethod
    def _dedupe_and_enrich_opps(
        page: List[Dict[str, Any]],
        seen: Set[str],
        latest_feed_epoch: Dict[str, int],
    ) -> List[SalesforceOpportunity]:
        """Drop duplicates against `seen`, fold `_latest_comment_epoch` into each row,
        and return the page's typed survivors."""
        survivors: List[SalesforceOpportunity] = []
        for row in page:
            opp_id = row.get("Id")
            if not opp_id or opp_id in seen:
                continue
            seen.add(opp_id)
            row["_latest_comment_epoch"] = latest_feed_epoch.get(opp_id)
            survivors.append(SalesforceOpportunity.model_validate(row))
        return survivors

    async def _get_updated_product(
        self,
        api_version: str,
        products_last_ts_ms: Optional[int],
        base_products_soql: str,
    ) -> AsyncGenerator[List[SalesforceProduct], None]:
        """Stream Product2 records for incremental sync, one Salesforce page per yield.

        Returns products modified since the last sync point, or all products
        when no sync point is available.
        """
        if products_last_ts_ms:
            soql_datetime = epoch_ms_to_iso(products_last_ts_ms)
            soql = f"{base_products_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Incremental products sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_products_soql} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Full products sync: no previous sync point")

        async for page in self._soql_query_paginated(api_version=api_version, q=soql):
            yield [SalesforceProduct.model_validate(r) for r in page]

    async def _get_updated_case(
        self,
        api_version: str,
        cases_last_ts_ms: Optional[int],
        base_cases_soql: str,
        extra_where_conditions: Optional[List[str]] = None,
        updated_after_ms: Optional[int] = None,
        updated_before_ms: Optional[int] = None,
    ) -> AsyncGenerator[List[SalesforceCase], None]:
        """Stream Case records for incremental sync.  Same shape as `_get_updated_deal`:
        the small `*_dates` queries are drained up-front into `latest_feed_epoch`, then
        the three record-bearing queries stream through a running `seen` set.

        extra_where_conditions: additional SOQL condition strings (no WHERE keyword) ANDed
        into every query — used to push user-configured date filters (e.g. CreatedDate >=)
        down to Salesforce rather than filtering in Python.

        updated_after_ms / updated_before_ms: when set and no sync point exists (full scan),
        AND LastModifiedDate >= / <= <date> into the SOQL so Salesforce filters server-side
        instead of downloading all records and discarding them in Python.  Not applied to
        the FeedItem/FeedComment OR-branches in the incremental path because those branches
        intentionally resurface old parents that have new Chatter activity.
        """
        extra = extra_where_conditions or []

        if cases_last_ts_ms:
            soql_datetime = epoch_ms_to_iso(cases_last_ts_ms)

            soql_modified = (
                f"{base_cases_soql} "
                f"{_compose_soql_where(f'LastModifiedDate >= {soql_datetime}', *extra)} "
                f"ORDER BY LastModifiedDate ASC"
            )
            soql_feeditems = (
                f"{base_cases_soql} "
                f"{_compose_soql_where(f'Id IN (SELECT ParentId FROM FeedItem WHERE CreatedDate >= {soql_datetime})', 'IsDeleted = false', *extra)}"
            )
            soql_feedcomments = (
                f"{base_cases_soql} "
                f"{_compose_soql_where(f'Id IN (SELECT ParentId FROM FeedComment WHERE CreatedDate >= {soql_datetime})', 'IsDeleted = false', *extra)}"
            )
            soql_item_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedItem "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )
            soql_comment_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedComment "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )

            latest_feed_epoch: Dict[str, int] = {}
            async for date_page in self._soql_query_paginated(
                api_version=api_version, q=soql_item_dates,
            ):
                _accumulate_latest_feed_epoch(date_page, latest_feed_epoch)
            async for date_page in self._soql_query_paginated(
                api_version=api_version, q=soql_comment_dates,
            ):
                _accumulate_latest_feed_epoch(date_page, latest_feed_epoch)

            seen: Set[str] = set()
            total_modified = total_feeditems = total_feedcomments = 0

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_modified,
            ):
                total_modified += len(page)
                cases = self._dedupe_and_enrich_cases(page, seen, latest_feed_epoch)
                if cases:
                    yield cases

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_feeditems,
            ):
                total_feeditems += len(page)
                cases = self._dedupe_and_enrich_cases(page, seen, latest_feed_epoch)
                if cases:
                    yield cases

            async for page in self._soql_query_paginated(
                api_version=api_version, q=soql_feedcomments,
            ):
                total_feedcomments += len(page)
                cases = self._dedupe_and_enrich_cases(page, seen, latest_feed_epoch)
                if cases:
                    yield cases

            self.logger.info(
                "Incremental cases: %d modified, %d from FeedItem, "
                "%d from FeedComment, %d unique after dedupe",
                total_modified, total_feeditems, total_feedcomments, len(seen),
            )
            return

        full_extra = list(extra) + _date_bound_conditions(
            "LastModifiedDate", updated_after_ms, updated_before_ms
        )
        soql_full = (
            f"{base_cases_soql} "
            f"{_compose_soql_where(*full_extra)} "
            f"ORDER BY LastModifiedDate ASC"
        ).strip()
        self.logger.debug("Full cases sync: no previous sync point")
        async for page in self._soql_query_paginated(api_version=api_version, q=soql_full):
            yield [SalesforceCase.model_validate(r) for r in page]

    @staticmethod
    def _dedupe_and_enrich_cases(
        page: List[Dict[str, Any]],
        seen: Set[str],
        latest_feed_epoch: Dict[str, int],
    ) -> List[SalesforceCase]:
        """Drop duplicates against `seen`, fold `_latest_comment_epoch` into each row,
        and return the page's typed survivors."""
        survivors: List[SalesforceCase] = []
        for row in page:
            case_id = row.get("Id")
            if not case_id or case_id in seen:
                continue
            seen.add(case_id)
            row["_latest_comment_epoch"] = latest_feed_epoch.get(case_id)
            survivors.append(SalesforceCase.model_validate(row))
        return survivors

    async def _get_updated_task(
        self,
        api_version: str,
        tasks_last_ts_ms: Optional[int],
        base_tasks_soql: str,
        newly_synced_parent_ids: Optional[Set[str]] = None,
    ) -> AsyncGenerator[List[SalesforceTask], None]:
        """Stream Task records for incremental sync.

        Pass 1 — timestamp filter: yields tasks modified since the last sync point (or all
        tasks when no sync point is available), one Salesforce page per yield.

        Pass 2 — parent backfill (only on incremental runs): for each ID in
        `newly_synced_parent_ids`, yields tasks whose WhatId matches, regardless of
        LastModifiedDate, deduplicated against Pass 1 via a running `seen_ids` set.
        """
        if tasks_last_ts_ms:
            soql_datetime = epoch_ms_to_iso(tasks_last_ts_ms)
            soql = f"{base_tasks_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Incremental tasks sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_tasks_soql} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Full tasks sync: no previous sync point")

        seen_ids: Set[str] = set()
        pass1_count = 0

        async for page in self._soql_query_paginated(api_version=api_version, q=soql):
            survivors: List[SalesforceTask] = []
            for r in page:
                task = SalesforceTask.model_validate(r)
                if task.Id and task.Id not in seen_ids:
                    seen_ids.add(task.Id)
                    survivors.append(task)
            pass1_count += len(survivors)
            if survivors:
                yield survivors

        # Supplemental pass: only needed on incremental runs — on a full scan every task is
        # already returned, so the backfill would add nothing.
        if not (tasks_last_ts_ms and newly_synced_parent_ids):
            self.logger.debug("Tasks pass 1: %d unique", pass1_count)
            return

        parent_ids = list(newly_synced_parent_ids)
        _BATCH = 500
        queries: List[str] = []
        for i in range(0, len(parent_ids), _BATCH):
            safe_ids = _sanitize_soql_ids_batch(
                parent_ids[i : i + _BATCH], self.logger, "task backfill",
            )
            if not safe_ids:
                continue
            ids_clause = "','".join(safe_ids)
            queries.append(
                f"{base_tasks_soql} WHERE WhatId IN ('{ids_clause}') "
                f"ORDER BY LastModifiedDate ASC"
            )

        supplemental_count = 0
        for q in queries:
            async for page in self._soql_query_paginated(api_version=api_version, q=q):
                survivors = []
                for r in page:
                    task = SalesforceTask.model_validate(r)
                    if task.Id and task.Id not in seen_ids:
                        seen_ids.add(task.Id)
                        survivors.append(task)
                supplemental_count += len(survivors)
                if survivors:
                    yield survivors

        self.logger.info(
            "Tasks: %d from pass 1, %d backfilled from %d newly eligible parents",
            pass1_count, supplemental_count, len(newly_synced_parent_ids),
        )

    async def _get_updated_file(
        self,
        api_version: str,
        files_last_ts_ms: Optional[int],
        newly_synced_parent_ids: Optional[Set[str]] = None,
    ) -> AsyncGenerator[List[SalesforceContentVersion], None]:
        """Stream ContentVersion records for incremental sync.

        Pass 1 — timestamp filter: yields the latest version of files modified since the
        last sync point (or all latest-version files when no sync point is available),
        one Salesforce page per yield.

        Pass 2 — parent backfill (only on incremental runs): for each ID in
        `newly_synced_parent_ids`, walks ContentDocumentLink (direct + email-message
        chain) and yields the resulting ContentVersions in pages, deduplicated against
        Pass 1 via a running `seen_doc_ids` set.
        """
        base_files_soql = (
            "SELECT Id, ContentDocumentId, Title, PathOnClient, ContentSize, "
            "FileExtension, FileType, LastModifiedDate, CreatedDate, Checksum "
            "FROM ContentVersion WHERE IsLatest = true"
        )

        if files_last_ts_ms:
            soql_datetime = epoch_ms_to_iso(files_last_ts_ms)
            soql = f"{base_files_soql} AND LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Incremental files sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_files_soql} ORDER BY LastModifiedDate ASC"
            self.logger.debug("Full files sync: no previous sync point")

        # Track ContentDocumentIds seen in Pass 1 so Pass 2 can dedupe against
        # the file backfill set. Pass 1 itself yields every row from Salesforce
        # (matching the legacy "return everything" behaviour); per-record dedup
        # against the graph store is the consumer's job.
        seen_doc_ids: Set[str] = set()
        pass1_count = 0

        async for page in self._soql_query_paginated(api_version=api_version, q=soql):
            cvs: List[SalesforceContentVersion] = [
                SalesforceContentVersion.model_validate(r) for r in page
            ]
            for cv in cvs:
                if cv.ContentDocumentId:
                    seen_doc_ids.add(cv.ContentDocumentId)
            pass1_count += len(cvs)
            if cvs:
                yield cvs

        # Supplemental pass: only needed on incremental runs.  On a full scan every
        # ContentVersion is already included so the backfill would add nothing.
        if not (files_last_ts_ms and newly_synced_parent_ids):
            self.logger.debug("Files pass 1: %d unique ContentDocumentIds", pass1_count)
            return

        _BATCH = 500
        parent_ids = list(newly_synced_parent_ids)

        def _build_in_queries(
            source_ids: List[str],
            build_query: Callable[[str], str],
            context: str,
        ) -> List[str]:
            """Chunk *source_ids* into _BATCH-sized IN-clauses and feed each clause to
            *build_query* to produce the final SOQL string.  Invalid IDs are dropped
            via `_sanitize_soql_ids_batch`."""
            queries: List[str] = []
            for i in range(0, len(source_ids), _BATCH):
                safe_ids = _sanitize_soql_ids_batch(
                    source_ids[i : i + _BATCH], self.logger, context,
                )
                if not safe_ids:
                    continue
                queries.append(build_query("','".join(safe_ids)))
            return queries

        # Step 1a: files directly linked to newly eligible parents
        # (ContentDocumentLink.LinkedEntityId ∈ {Opp, Case, Account IDs}).
        # Stream new ContentDocumentIds into a single bounded set; flush to Step 2 in
        # batches to keep memory bounded by the size of one SOQL IN-batch.
        new_doc_ids_buffer: List[str] = []
        direct_link_count = 0
        backfill_count = 0

        async def _flush_doc_buffer_through_step2() -> AsyncGenerator[List[SalesforceContentVersion], None]:
            """Drain `new_doc_ids_buffer` in IN-batches, fetching each ContentVersion
            page from Step 2 and yielding it.  Clears the buffer in place."""
            nonlocal backfill_count
            if not new_doc_ids_buffer:
                return
            step2_queries = _build_in_queries(
                new_doc_ids_buffer,
                lambda ids: (
                    f"{base_files_soql} "
                    f"AND ContentDocumentId IN ('{ids}') "
                    f"ORDER BY LastModifiedDate ASC"
                ),
                "file backfill step 2",
            )
            new_doc_ids_buffer.clear()
            for q in step2_queries:
                async for cv_page in self._soql_query_paginated(api_version=api_version, q=q):
                    cvs: List[SalesforceContentVersion] = []
                    for r in cv_page:
                        cv = SalesforceContentVersion.model_validate(r)
                        if cv.ContentDocumentId:
                            cvs.append(cv)
                    if cvs:
                        backfill_count += len(cvs)
                        yield cvs

        step1a_queries = _build_in_queries(
            parent_ids,
            lambda ids: (
                "SELECT ContentDocumentId FROM ContentDocumentLink "
                f"WHERE LinkedEntityId IN ('{ids}')"
            ),
            "file backfill step 1a",
        )
        for q in step1a_queries:
            async for link_page in self._soql_query_paginated(api_version=api_version, q=q):
                for row in link_page:
                    doc_id = row.get("ContentDocumentId")
                    if doc_id and doc_id not in seen_doc_ids:
                        seen_doc_ids.add(doc_id)
                        new_doc_ids_buffer.append(doc_id)
                        direct_link_count += 1
                        if len(new_doc_ids_buffer) >= _BATCH:
                            async for cv_page in _flush_doc_buffer_through_step2():
                                yield cv_page

        # Step 1b: files linked via EmailMessage -> Task -> newly eligible parent.
        # Chain: ContentDocumentLink.LinkedEntityId = EmailMessage.Id,
        #        EmailMessage.ActivityId = Task.Id, Task.WhatId IN newly_synced_parent_ids.
        # Walk the chain in reverse, draining each hop into a bounded ID buffer that
        # flushes when it hits _BATCH so we never accumulate more than one IN-batch worth.
        step1b_task_queries = _build_in_queries(
            parent_ids,
            lambda ids: f"SELECT Id FROM Task WHERE WhatId IN ('{ids}')",
            "file backfill step 1b task",
        )

        task_ids_buffer: List[str] = []
        email_message_ids_buffer: List[str] = []
        email_chain_doc_count = 0
        total_task_ids = 0

        async def _drain_email_buffer_to_doc_buffer() -> AsyncGenerator[List[SalesforceContentVersion], None]:
            """Resolve `email_message_ids_buffer` -> ContentDocumentIds and append to
            `new_doc_ids_buffer`, flushing through Step 2 whenever full.  Clears the
            email buffer in place."""
            nonlocal email_chain_doc_count
            if not email_message_ids_buffer:
                return
            step1b_link_queries = _build_in_queries(
                email_message_ids_buffer,
                lambda ids: (
                    "SELECT ContentDocumentId FROM ContentDocumentLink "
                    f"WHERE LinkedEntityId IN ('{ids}')"
                ),
                "file backfill step 1b link",
            )
            email_message_ids_buffer.clear()
            for q in step1b_link_queries:
                async for link_page in self._soql_query_paginated(api_version=api_version, q=q):
                    for row in link_page:
                        doc_id = row.get("ContentDocumentId")
                        if doc_id and doc_id not in seen_doc_ids:
                            seen_doc_ids.add(doc_id)
                            new_doc_ids_buffer.append(doc_id)
                            email_chain_doc_count += 1
                            if len(new_doc_ids_buffer) >= _BATCH:
                                async for cv_page in _flush_doc_buffer_through_step2():
                                    yield cv_page

        async def _drain_task_buffer_to_email_buffer() -> AsyncGenerator[List[SalesforceContentVersion], None]:
            """Resolve `task_ids_buffer` -> EmailMessage IDs and append to
            `email_message_ids_buffer`, draining the email buffer whenever full.
            Clears the task buffer in place."""
            if not task_ids_buffer:
                return
            step1b_email_queries = _build_in_queries(
                task_ids_buffer,
                lambda ids: (
                    f"SELECT Id FROM EmailMessage WHERE ActivityId IN ('{ids}')"
                ),
                "file backfill step 1b email",
            )
            task_ids_buffer.clear()
            for q in step1b_email_queries:
                async for em_page in self._soql_query_paginated(api_version=api_version, q=q):
                    for row in em_page:
                        em_id = row.get("Id")
                        if em_id:
                            email_message_ids_buffer.append(em_id)
                            if len(email_message_ids_buffer) >= _BATCH:
                                async for cv_page in _drain_email_buffer_to_doc_buffer():
                                    yield cv_page

        for q in step1b_task_queries:
            async for task_page in self._soql_query_paginated(api_version=api_version, q=q):
                for row in task_page:
                    if row.get("Id"):
                        task_ids_buffer.append(row["Id"])
                        total_task_ids += 1
                        if len(task_ids_buffer) >= _BATCH:
                            async for cv_page in _drain_task_buffer_to_email_buffer():
                                yield cv_page

        # Final flush: drain partial buffers (left over after the streaming pages above).
        async for cv_page in _drain_task_buffer_to_email_buffer():
            yield cv_page
        async for cv_page in _drain_email_buffer_to_doc_buffer():
            yield cv_page
        async for cv_page in _flush_doc_buffer_through_step2():
            yield cv_page

        self.logger.info(
            "Supplemental file backfill: %d new ContentDocumentIds "
            "(%d via direct link, %d via email-message chain, %d tasks resolved) "
            "from %d newly eligible parents — emitted %d ContentVersions",
            direct_link_count + email_chain_doc_count,
            direct_link_count,
            email_chain_doc_count,
            total_task_ids,
            len(newly_synced_parent_ids),
            backfill_count,
        )

    async def _get_updated_account(
        self, api_version: str, soql_datetime: str, soql_accounts_query: str
    ) -> AsyncGenerator[List[SalesforceAccount], None]:
        """Stream Account records for incremental sync.

        Step 1 collects changed Account IDs (small payload — just IDs) from two sources:
        accounts modified directly, plus accounts whose Opportunities changed.  Step 2
        fetches the full Account records in IN-batches of 500 and yields each Salesforce
        page as it arrives.
        """
        ids_from_accounts: Set[str] = set()

        # Step 1a: Accounts changed directly.
        query_a = f"SELECT Id FROM Account WHERE SystemModstamp >= {soql_datetime}"
        async for page in self._soql_query_paginated(api_version=api_version, q=query_a):
            for rec in page:
                rec_id = rec.get("Id")
                if rec_id:
                    ids_from_accounts.add(rec_id)

        # Step 1b: Accounts with changed Opportunities.
        query_b = f"SELECT AccountId FROM Opportunity WHERE SystemModstamp >= {soql_datetime}"
        async for page in self._soql_query_paginated(api_version=api_version, q=query_b):
            for rec in page:
                aid = rec.get("AccountId")
                if aid:
                    ids_from_accounts.add(aid)

        if not ids_from_accounts:
            self.logger.debug("Incremental account sync: no changed account IDs")
            return

        # Step 2: Fetch full records in IN-batches (Salesforce limit 500).
        id_list = list(ids_from_accounts)
        batch_size = 500
        total_emitted = 0
        for i in range(0, len(id_list), batch_size):
            safe_ids = _sanitize_soql_ids_batch(
                id_list[i : i + batch_size], self.logger, "incremental account sync",
            )
            if not safe_ids:
                continue
            in_clause = "','".join(safe_ids)
            main_query = f"{soql_accounts_query} WHERE Id IN ('{in_clause}')"
            async for page in self._soql_query_paginated(api_version=api_version, q=main_query):
                accs = [SalesforceAccount.model_validate(r) for r in page]
                if accs:
                    total_emitted += len(accs)
                    yield accs

        self.logger.info(
            "Incremental account sync: fetched %d accounts for %d changed IDs",
            total_emitted, len(ids_from_accounts),
        )

    async def _sync_accounts(
        self, account_records_pages: AsyncGenerator[List[SalesforceAccount], None],
    ) -> Set[str]:
        """Sync Salesforce accounts page-by-page.

        Per page: build org nodes + record groups, look up existing-org keys for the
        page (delete stale prospect/customer/dealOf edges), then upsert orgs and create
        new edges in a single per-page transaction.  Cross-page state is the running
        `external_org_key_by_name` map (so brand-new accounts upserted in earlier pages
        are recognized as existing in later pages) and `newly_synced_account_ids`.

        Per-page commit semantics: a crash mid-stream leaves earlier pages persisted;
        the sync point is only advanced if every page completes.

        Returns the set of Salesforce Account IDs created for the first time across all
        pages — used by the caller to trigger supplemental task/file backfills.
        """
        try:
            newly_synced_account_ids: Set[str] = set()
            org_id = self.data_entities_processor.org_id

            # One-time setup: snapshot existing external orgs into a name -> _key map.
            # Updated in place each page so that newly upserted orgs are recognized as
            # existing on subsequent pages (correct delete-then-recreate behaviour).
            async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                all_orgs = await tx_store.get_all_orgs()
            external_org_key_by_name: Dict[str, str] = {
                o["name"]: o["_key"]
                for o in (all_orgs or [])
                if o.get("isExternal") is True
            }

            total_orgs = 0
            total_record_groups = 0

            async for page in account_records_pages:
                orgs_with_edges: List[Tuple[Org, RecordGroup, Dict, Optional[Dict]]] = []
                record_groups_with_perms: List[Tuple[RecordGroup, List[Permission]]] = []

                for acc in page:
                    try:
                        account_id = acc.Id
                        if not account_id:
                            continue
                        acc_type = acc.Type
                        rating = acc.Rating
                        created_date = acc.CreatedDate
                        account_name = acc.Name or ""
                        start_time_ms = _parse_salesforce_timestamp(created_date)
                        end_time_ms, active_customer = self._parse_opportunities(acc)

                        org_node = Org(
                            name=account_name,
                            is_external=True,
                            website=acc.Website,
                            industry=acc.Industry,
                            ownership_type=acc.Ownership,
                            phone=acc.Phone,
                            duns_id=acc.DunsNumber,
                            source_created_at=start_time_ms,
                            source_updated_at=_parse_salesforce_timestamp(acc.LastModifiedDate),
                        )
                        record_group = RecordGroup(
                            name=account_name or account_id,
                            external_group_id=account_id,
                            group_type=RecordGroupType.SALESFORCE_ORG,
                            connector_name=self.connector_name,
                            connector_id=self.connector_id,
                            source_created_at=start_time_ms,
                            source_updated_at=_parse_salesforce_timestamp(acc.LastModifiedDate),
                        )
                        record_groups_with_perms.append((record_group, []))
                        updated_time_ms = _parse_salesforce_timestamp(acc.LastModifiedDate)
                        prospect_edge = {
                            "to_id": org_node.id,
                            "rating": rating,
                            "type": acc_type,
                            "externalId": account_id,
                            "startTime": start_time_ms,
                            "endTime": end_time_ms,
                            "createdAtTimestamp": start_time_ms,
                            "updatedAtTimestamp": updated_time_ms,
                        }
                        customer_edge: Optional[Dict] = None
                        if end_time_ms is not None:
                            customer_edge = {
                                "to_id": org_node.id,
                                "rating": rating,
                                "type": acc_type,
                                "activeCustomer": active_customer,
                                "externalId": account_id,
                                "since": end_time_ms,
                                "createdAtTimestamp": start_time_ms,
                                "updatedAtTimestamp": updated_time_ms,
                            }
                        orgs_with_edges.append((org_node, record_group, prospect_edge, customer_edge))
                    except Exception as e:
                        self.logger.warning(f"Failed to process account {acc.Id}: {e}")
                        continue

                if not orgs_with_edges:
                    continue

                if record_groups_with_perms:
                    await self.data_entities_processor.on_new_record_groups(
                        record_groups_with_perms,
                    )
                    total_record_groups += len(record_groups_with_perms)

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_orgs = await tx_store.get_all_orgs(is_external=True)
                    external_org_key_by_name = {
                        o["name"]: o.get("id", o.get("_key"))
                        for o in all_orgs
                    }
                    delete_tasks = []
                    for org, rg, _, _ in orgs_with_edges:
                        existing_key = external_org_key_by_name.get(org.name)
                        if existing_key is not None:
                            org.id = existing_key
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=existing_key,
                                to_collection=CollectionNames.ORGS.value,
                                collection=CollectionNames.PROSPECT.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=existing_key,
                                to_collection=CollectionNames.ORGS.value,
                                collection=CollectionNames.CUSTOMER.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_from(
                                rg.id,
                                CollectionNames.RECORD_GROUPS.value,
                                CollectionNames.DEAL_OF.value,
                            ))
                        else:
                            if rg.external_group_id:
                                newly_synced_account_ids.add(rg.external_group_id)
                    if delete_tasks:
                        for _del in delete_tasks:
                            await _del

                    await tx_store.batch_upsert_orgs(
                        [org.to_arango_org() for org, _, _, _ in orgs_with_edges]
                    )

                    # Mark these orgs as "existing" for subsequent pages.
                    for org, _, _, _ in orgs_with_edges:
                        external_org_key_by_name[org.name] = org.id

                    prospect_edges = []
                    customer_edges = []
                    dealof_edges = []
                    for org, rg, prospect_edge_attrs, customer_edge_attrs in orgs_with_edges:
                        prospect_edges.append({
                            **prospect_edge_attrs,
                            "to_id": org.id,
                            "to_collection": CollectionNames.ORGS.value,
                            "from_id": org_id,
                            "from_collection": CollectionNames.ORGS.value,
                        })
                        if customer_edge_attrs:
                            customer_edges.append({
                                **customer_edge_attrs,
                                "to_id": org.id,
                                "to_collection": CollectionNames.ORGS.value,
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                            })
                        dealof_edges.append({
                            "from_id": rg.id,
                            "from_collection": CollectionNames.RECORD_GROUPS.value,
                            "to_id": org.id,
                            "to_collection": CollectionNames.ORGS.value,
                            "createdAtTimestamp": prospect_edge_attrs.get("createdAtTimestamp"),
                            "updatedAtTimestamp": prospect_edge_attrs.get("updatedAtTimestamp"),
                        })

                    await tx_store.batch_create_edges(
                        prospect_edges, collection=CollectionNames.PROSPECT.value,
                    )
                    if customer_edges:
                        await tx_store.batch_create_edges(
                            customer_edges, collection=CollectionNames.CUSTOMER.value,
                        )
                    if dealof_edges:
                        await tx_store.batch_create_edges(
                            dealof_edges, collection=CollectionNames.DEAL_OF.value,
                        )

                total_orgs += len(orgs_with_edges)

            self.logger.info(
                "Synced %d account orgs (%d record groups), %d newly created",
                total_orgs, total_record_groups, len(newly_synced_account_ids),
            )
            return newly_synced_account_ids

        except Exception as e:
            self.logger.error(f"Error syncing accounts and prospect/customer: {e}", exc_info=True)
            raise

    async def _sync_contacts(
        self, contact_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
    ) -> None:
        """Sync Salesforce Contacts page-by-page.  Each page opens its own transaction:
        looks up existing People + Orgs, deletes stale contact/memberOf edges, upserts
        the changed people, and recreates the edges.  Cross-page state is just two
        running counters."""
        try:
            org_id = self.data_entities_processor.org_id
            total_changed = 0
            total_unchanged = 0

            async for raw_page in contact_records_pages:
                page = [SalesforceContact.model_validate(r) for r in raw_page]
                contact_with_edges: List[Tuple[Person, Dict, Optional[Dict]]] = []

                for contact in page:
                    try:
                        contact_id = contact.Id
                        if not contact_id:
                            continue
                        email = contact.Email
                        if not email:
                            self.logger.warning(
                                "Skipping contact %s: email not available (required for Person node)",
                                contact_id,
                            )
                            continue

                        person = Person(
                            email=email,
                            first_name=contact.FirstName,
                            last_name=contact.LastName,
                            phone=contact.Phone,
                            created_at=_parse_salesforce_timestamp(contact.CreatedDate),
                            updated_at=_parse_salesforce_timestamp(contact.LastModifiedDate),
                        )
                        since_ms = _parse_salesforce_timestamp(contact.CreatedDate)
                        contact_updated_ms = _parse_salesforce_timestamp(contact.LastModifiedDate)

                        contact_edge = {
                            "to_id": person.id,
                            "description": contact.Description,
                            "leadSource": contact.LeadSource,
                            "since": since_ms,
                            "externalId": contact_id,
                            "createdAtTimestamp": since_ms,
                            "updatedAtTimestamp": contact_updated_ms,
                        }
                        member_of_edge: Optional[Dict] = None
                        account_obj = contact.Account or {}
                        account_name = account_obj.get("Name")
                        if account_name:
                            member_of_edge = {
                                "from_id": person.id,
                                "accountName": account_name,
                                "title": contact.Title,
                                "department": contact.Department,
                                "createdAtTimestamp": since_ms,
                                "updatedAtTimestamp": contact_updated_ms,
                            }
                        contact_with_edges.append((person, contact_edge, member_of_edge))
                    except Exception as e:
                        self.logger.warning(f"Failed to process contact {contact.Id}: {e}")
                        continue

                if not contact_with_edges:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_emails = [p.email for p, _, _ in contact_with_edges]
                    all_account_names = list({
                        moe.get("accountName")
                        for _, sce, moe in contact_with_edges
                        if sce and moe and moe.get("accountName")
                    })

                    people_coro = tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.PEOPLE.value,
                        field="email",
                        values=all_emails,
                    )
                    orgs_coro = (
                        tx_store.get_nodes_by_field_in(
                            collection=CollectionNames.ORGS.value,
                            field="name",
                            values=all_account_names,
                        )
                        if all_account_names
                        else asyncio.gather()
                    )
                    existing_people_result, existing_orgs_result = await asyncio.gather(
                        people_coro, orgs_coro
                    )
                    email_map = {node.get("email"): node for node in (existing_people_result or [])}
                    org_name_map = {node.get("name"): node for node in (existing_orgs_result or [])}

                    unchanged_emails: set = set()
                    delete_tasks = []
                    for person, contact_edge, _ in contact_with_edges:
                        node = email_map.get(person.email)
                        if node:
                            person.id = node.get("id") or node.get("_key")
                            stored_updated = node.get("updatedAtTimestamp")
                            incoming_updated = contact_edge.get("updatedAtTimestamp") if contact_edge else None
                            if (
                                stored_updated is not None
                                and incoming_updated is not None
                                and stored_updated == incoming_updated
                            ):
                                unchanged_emails.add(person.email)
                                continue
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=person.id,
                                to_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.CONTACT.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_from(
                                from_id=person.id,
                                from_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.MEMBER_OF.value,
                            ))
                    if delete_tasks:
                        for _del in delete_tasks:
                            await _del

                    changed_contacts = [
                        (p, sce, moe)
                        for p, sce, moe in contact_with_edges
                        if p.email not in unchanged_emails
                    ]
                    if changed_contacts:
                        await tx_store.batch_upsert_people([p for p, _, _ in changed_contacts])

                    contact_edges = []
                    member_of_edges = []
                    for person, contact_edge, member_of_edge in changed_contacts:
                        if contact_edge:
                            contact_edge["to_id"] = person.id
                            contact_edges.append({
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                                "to_collection": CollectionNames.PEOPLE.value,
                                **contact_edge,
                            })
                        if member_of_edge and contact_edge:
                            account_name = member_of_edge.pop("accountName", None)
                            if account_name:
                                org_node = org_name_map.get(account_name)
                                if org_node:
                                    member_of_edges.append({
                                        "from_id": person.id,
                                        "from_collection": CollectionNames.PEOPLE.value,
                                        "to_id": org_node.get("id") or org_node.get("_key"),
                                        "to_collection": CollectionNames.ORGS.value,
                                        **{k: v for k, v in member_of_edge.items()},
                                    })

                    if contact_edges:
                        await tx_store.batch_create_edges(
                            contact_edges, collection=CollectionNames.CONTACT.value,
                        )
                    if member_of_edges:
                        await tx_store.batch_create_edges(
                            member_of_edges, collection=CollectionNames.MEMBER_OF.value,
                        )

                total_changed += len(changed_contacts)
                total_unchanged += len(unchanged_emails)

            self.logger.info(
                "Synced %d contact person nodes and contact edges (%d unchanged, skipped)",
                total_changed, total_unchanged,
            )

        except Exception as e:
            self.logger.error(f"Error syncing contacts and contact edges: {e}", exc_info=True)
            raise

    async def _sync_leads(
        self, lead_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
    ) -> None:
        """Sync Salesforce Leads page-by-page.  Each page opens its own transaction:
        looks up existing People, deletes stale lead edges, upserts the people, and
        recreates the edges.

        lead edge fields: Company, Title, Status, Rating, Industry, LeadSource,
        AnnualRevenue, ExternalId, startTime (CreatedDate), endTime (ConvertedDate).
        """
        try:
            org_id = self.data_entities_processor.org_id
            total = 0

            async for raw_page in lead_records_pages:
                page = [SalesforceLead.model_validate(r) for r in raw_page]
                lead_with_edges: List[Tuple[Person, Dict]] = []
                for lead in page:
                    try:
                        lead_id = lead.Id
                        if not lead_id:
                            continue
                        is_converted = bool(lead.ConvertedContactId)
                        start_time_ms = _parse_salesforce_timestamp(lead.CreatedDate)
                        end_time_ms = (
                            _parse_salesforce_timestamp(lead.ConvertedDate)
                            if is_converted else None
                        )
                        edge_attrs = {
                            "company": lead.Company,
                            "title": lead.Title,
                            "status": lead.Status,
                            "rating": lead.Rating,
                            "industry": lead.Industry,
                            "leadSource": lead.LeadSource,
                            "annualRevenue": lead.AnnualRevenue,
                            "externalId": lead_id,
                            "startTime": start_time_ms,
                            "endTime": end_time_ms,
                            "createdAtTimestamp": start_time_ms,
                            "updatedAtTimestamp": _parse_salesforce_timestamp(lead.LastModifiedDate),
                        }
                        lead_email = lead.Email
                        if not lead_email:
                            self.logger.warning(
                                f"Skipping lead {lead_id}: missing email required for Person node",
                            )
                            continue
                        person = Person(
                            email=lead_email,
                            first_name=lead.FirstName,
                            last_name=lead.LastName,
                            phone=lead.Phone,
                            created_at=_parse_salesforce_timestamp(lead.CreatedDate),
                            updated_at=_parse_salesforce_timestamp(lead.LastModifiedDate),
                        )
                        lead_with_edges.append((person, edge_attrs))
                    except Exception as e:
                        self.logger.warning(f"Failed to process lead {lead.Id}: {e}")
                        continue

                if not lead_with_edges:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_emails = [p.email for p, _ in lead_with_edges]
                    existing_people = await tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.PEOPLE.value,
                        field="email",
                        values=all_emails,
                    )
                    email_map = {node.get("email"): node for node in (existing_people or [])}

                    ids_to_delete = []
                    for person, _ in lead_with_edges:
                        node = email_map.get(person.email)
                        if node:
                            person.id = node.get("id") or node.get("_key")
                            ids_to_delete.append(person.id)

                    if ids_to_delete:
                        await asyncio.gather(*[
                            tx_store.delete_edges_to(
                                to_id=pid,
                                to_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.LEAD.value,
                            )
                            for pid in ids_to_delete
                        ])

                    await tx_store.batch_upsert_people([p for p, _ in lead_with_edges])

                    edges = [
                        {
                            "from_id": org_id,
                            "from_collection": CollectionNames.ORGS.value,
                            "to_id": p.id,
                            "to_collection": CollectionNames.PEOPLE.value,
                            **e,
                        }
                        for p, e in lead_with_edges
                    ]
                    await tx_store.batch_create_edges(edges, collection=CollectionNames.LEAD.value)

                total += len(lead_with_edges)

            self.logger.info("Synced %d lead person nodes and lead edges", total)

        except Exception as e:
            self.logger.error(f"Error syncing leads and lead edges: {e}", exc_info=True)
            raise

    async def _fetch_standard_pricebook_prices(
        self, api_version: str, product_ids: List[str]
    ) -> Dict[str, float]:
        """Batch-fetch UnitPrice from the Standard Pricebook for the given Product2 Ids.

        Returns a dict mapping Product2Id -> UnitPrice.  Bounded by the size of
        `product_ids` (one Salesforce page worth), so safe to materialize.
        """
        if not product_ids:
            return {}
        try:
            safe_ids = _sanitize_soql_ids_batch(
                product_ids, self.logger, "standard pricebook prices",
            )
            if not safe_ids:
                return {}
            id_list = ", ".join(f"'{pid}'" for pid in safe_ids)
            soql = (
                f"SELECT Product2Id, UnitPrice FROM PricebookEntry "
                f"WHERE Product2Id IN ({id_list}) AND Pricebook2.IsStandard = true AND IsActive = true"
            )
            price_map: Dict[str, float] = {}
            async for page in self._soql_query_paginated(api_version=api_version, q=soql):
                for row in page:
                    pid = row.get("Product2Id")
                    price = row.get("UnitPrice")
                    if pid and price is not None:
                        price_map[pid] = price
            return price_map
        except Exception as exc:
            self.logger.warning("Failed to fetch standard pricebook prices: %s", exc)
            return {}

    async def _sync_products(
        self, product_records_pages: AsyncGenerator[List[SalesforceProduct], None],
    ) -> None:
        """Sync Salesforce Product2 records page-by-page.  Each page batch-fetches its
        own standard pricebook prices and existing-record set, then commits."""
        try:
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            api_version = await self._get_api_version()
            total = 0
            total_new = 0
            total_updated = 0

            async for page in product_records_pages:
                page_product_ids = [p.Id for p in page if p.Id]
                if not page_product_ids:
                    continue

                price_map = await self._fetch_standard_pricebook_prices(
                    api_version, page_product_ids,
                )

                records: List[Record] = []
                for product in page:
                    try:
                        if not product.Id:
                            continue
                        record = self._build_product_record(
                            product, list_price=price_map.get(product.Id),
                        )
                        if self.indexing_filters and self.indexing_filters.is_enabled(
                            IndexingFilterKey.ENABLE_MANUAL_SYNC,
                        ):
                            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                        records.append(record)
                    except Exception as e:
                        self.logger.warning(f"Failed to process product {product.Id}: {e}")
                        continue

                if not records:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    existing_nodes = await tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=[r.external_record_id for r in records if r.external_record_id],
                    )
                existing_ext_ids = {
                    node.get("externalRecordId")
                    for node in (existing_nodes or [])
                    if node.get("externalRecordId") and node.get("connectorId") == self.connector_id
                }
                new_records = [r for r in records if r.external_record_id not in existing_ext_ids]
                update_records = [r for r in records if r.external_record_id in existing_ext_ids]

                if new_records:
                    await self.data_entities_processor.on_new_records(
                        [(r, []) for r in new_records],
                    )
                for record in update_records:
                    await self.data_entities_processor.on_record_content_update(record)

                total += len(records)
                total_new += len(new_records)
                total_updated += len(update_records)

            self.logger.info(
                "Synced %d product records (%d new, %d updated).",
                total, total_new, total_updated,
            )

        except Exception as e:
            self.logger.error(f"Error syncing products: {e}", exc_info=True)
            raise

    async def _sync_opportunities(
        self,
        opportunity_records_pages: AsyncGenerator[List[SalesforceOpportunity], None],
    ) -> Set[str]:
        """Sync Salesforce Opportunities page-by-page.

        One-time setup: ensure the "Unlinked Deals" record group exists.

        Per page: build deal records + deal_info edges, look up existing records,
        partition into new/updated, persist, and create deal_info edges in a per-page
        transaction.  Cross-page state is `newly_synced_opp_ids` which is returned
        at the end for the caller's task/file backfill.
        """
        try:
            org_permission = Permission(
                entity_type=EntityType.ORG, type=PermissionType.READ,
            )
            record_group = RecordGroup(
                name="Unlinked Deals",
                external_group_id="UNASSIGNED-DEAL",
                group_type=RecordGroupType.DEAL,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
            )
            await self.data_entities_processor.on_new_record_groups(
                [(record_group, [org_permission])],
            )
            self.logger.debug("Ensured unassigned deal record group.")

            org_id = self.data_entities_processor.org_id
            newly_synced_opp_ids: Set[str] = set()
            total = 0
            total_new = 0
            total_updated = 0

            async for page in opportunity_records_pages:
                opportunity_with_edges: List[Tuple[Record, Dict]] = []
                records: List[Record] = []

                for opp in page:
                    if not opp.Id:
                        continue
                    record = self._build_deal_record(opp)
                    if self.indexing_filters and self.indexing_filters.is_enabled(
                        IndexingFilterKey.ENABLE_MANUAL_SYNC,
                    ):
                        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    records.append(record)
                    deal_info_edge = {
                        "stage": opp.StageName or "",
                        "createdAtTimestamp": _parse_salesforce_timestamp(opp.CreatedDate),
                        "updatedAtTimestamp": _parse_salesforce_timestamp(opp.LastModifiedDate),
                    }
                    opportunity_with_edges.append((record, deal_info_edge))

                if not records:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    opp_existing_nodes = await tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=[r.external_record_id for r in records if r.external_record_id],
                    )
                opp_existing_ext_ids = {
                    node.get("externalRecordId")
                    for node in (opp_existing_nodes or [])
                    if node.get("externalRecordId") and node.get("connectorId") == self.connector_id
                }
                new_opp_records = [r for r in records if r.external_record_id not in opp_existing_ext_ids]
                update_opp_records = [r for r in records if r.external_record_id in opp_existing_ext_ids]

                if new_opp_records:
                    await self.data_entities_processor.on_new_records(
                        [(r, []) for r in new_opp_records],
                    )
                for record in update_opp_records:
                    await self.data_entities_processor.on_record_content_update(record)

                newly_synced_opp_ids.update(
                    r.external_record_id for r in new_opp_records if r.external_record_id
                )

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    existing_records_list = await asyncio.gather(*[
                        tx_store.get_record_by_external_id(
                            connector_id=record.connector_id,
                            external_id=record.external_record_id,
                        )
                        for record, _ in opportunity_with_edges
                    ])
                    ext_id_to_existing = {
                        existing.external_record_id: existing
                        for existing in existing_records_list
                        if existing is not None
                    }

                    delete_tasks = []
                    for record, _ in opportunity_with_edges:
                        existing = ext_id_to_existing.get(record.external_record_id)
                        if existing is not None:
                            record.id = existing.id
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=record.id,
                                to_collection=CollectionNames.RECORDS.value,
                                collection=CollectionNames.DEAL_INFO.value,
                            ))
                    if delete_tasks:
                        for _del in delete_tasks:
                            await _del

                    deal_info_edges = []
                    for record, deal_info_edge in opportunity_with_edges:
                        existing = ext_id_to_existing.get(record.external_record_id)
                        if existing is None:
                            continue
                        if deal_info_edge:
                            deal_info_edges.append({
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                                "to_id": record.id,
                                "to_collection": CollectionNames.RECORDS.value,
                                **deal_info_edge,
                            })
                    if deal_info_edges:
                        await tx_store.batch_create_edges(
                            deal_info_edges, collection=CollectionNames.DEAL_INFO.value,
                        )

                total += len(records)
                total_new += len(new_opp_records)
                total_updated += len(update_opp_records)

            self.logger.info(
                "Synced %d deal records (%d new, %d updated).",
                total, total_new, total_updated,
            )
            return newly_synced_opp_ids

        except Exception as e:
            self.logger.error(f"Error syncing opportunities: {e}", exc_info=True)
            raise

    async def _sync_sold_in_edges(
        self,
        line_item_records_pages: AsyncGenerator[List[Dict[str, Any]], None],
        api_version: str,
    ) -> None:
        """Sync soldIn edges (Product -> Deal) from OpportunityLineItem records.

        Page-streaming structure: each page of incoming line items contributes its
        (OpportunityId, Product2Id) pairs into a running set; the SOQL fan-out + edge
        write happens once at the end (after the input stream completes) because
        soldIn edges aggregate quantities/prices across ALL line items per pair, so
        they can only be written when every page has been seen.
        """
        try:
            unique_pairs: Set[Tuple[str, str]] = set()
            page_count = 0
            async for raw_page in line_item_records_pages:
                page_count += 1
                for raw_sold_in in raw_page:
                    sold_in = SalesforceLineItem.model_validate(raw_sold_in)
                    product2 = sold_in.Product2 or {}
                    product2_id = product2.get("Id")
                    opp_id = sold_in.OpportunityId
                    if product2_id and opp_id:
                        unique_pairs.add((opp_id, product2_id))

            if not unique_pairs:
                self.logger.info(
                    "No valid (OpportunityId, Product2Id) pairs found in %d line-item pages",
                    page_count,
                )
                return

            SOQL_IN_BATCH = 500
            unique_opp_ids = sorted({oid for oid, _ in unique_pairs})
            unique_product2_ids = sorted({pid for _, pid in unique_pairs})

            # Stream the per-batch fan-out queries through the running aggregation
            # rather than buffering raw rows.  queryAll=True captures soft-deleted
            # line items as well.
            items_by_pair: DefaultDict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
            queries: List[str] = []
            for i in range(0, len(unique_opp_ids), SOQL_IN_BATCH):
                safe_ids = _sanitize_soql_ids_batch(
                    unique_opp_ids[i : i + SOQL_IN_BATCH], self.logger, "soldIn bulk query",
                )
                if not safe_ids:
                    continue
                opp_ids_clause = "','".join(safe_ids)
                queries.append(
                    "SELECT Id, OpportunityId, Product2Id, Quantity, UnitPrice, TotalPrice, "
                    "IsDeleted, CreatedDate, LastModifiedDate "
                    f"FROM OpportunityLineItem WHERE OpportunityId IN ('{opp_ids_clause}')"
                )

            for q in queries:
                async for page in self._soql_query_paginated(
                    api_version=api_version, q=q, queryAll=True,
                ):
                    for item in page:
                        pair_key = (item.get("OpportunityId"), item.get("Product2Id"))
                        if pair_key in unique_pairs:
                            items_by_pair[pair_key].append(item)

            async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                product_nodes = await tx_store.get_nodes_by_field_in(
                    collection=CollectionNames.RECORDS.value,
                    field="externalRecordId",
                    values=unique_product2_ids,
                )
                deal_nodes = await tx_store.get_nodes_by_field_in(
                    collection=CollectionNames.RECORDS.value,
                    field="externalRecordId",
                    values=unique_opp_ids,
                )
            product_internal_id_map: Dict[str, str] = {
                node.get("externalRecordId"): (node.get("id") or node.get("_key"))
                for node in (product_nodes or [])
                if node.get("externalRecordId") and (node.get("id") or node.get("_key"))
                and node.get("connectorId") == self.connector_id
            }
            deal_internal_id_map: Dict[str, str] = {
                node.get("externalRecordId"): (node.get("id") or node.get("_key"))
                for node in (deal_nodes or [])
                if node.get("externalRecordId") and (node.get("id") or node.get("_key"))
                and node.get("connectorId") == self.connector_id
            }

            # Build deal_groups using pre-fetched internal IDs (no per-item DB calls)
            # {opp_external_id: (deal_internal_id, {product_internal_id: [raw line rows]})}
            deal_groups: Dict[str, Tuple[str, DefaultDict[str, List[Dict[str, Any]]]]] = {}

            for (opp_id, product2_id), items in items_by_pair.items():
                if not items:
                    continue
                product_internal_id = product_internal_id_map.get(product2_id)
                if not product_internal_id:
                    self.logger.debug("No DB record for product2_id=%s, skipping", product2_id)
                    continue
                deal_internal_id = deal_internal_id_map.get(opp_id)
                if not deal_internal_id:
                    self.logger.debug("No DB record for opp_id=%s, skipping", opp_id)
                    continue
                if opp_id not in deal_groups:
                    deal_groups[opp_id] = (deal_internal_id, defaultdict(list))
                _, by_product = deal_groups[opp_id]
                for item in items:
                    by_product[product_internal_id].append(
                        {
                            "quantity": float(item["Quantity"]) if item.get("Quantity") is not None else None,
                            "unitPrice": float(item["UnitPrice"]) if item.get("UnitPrice") is not None else None,
                            "totalPrice": float(item["TotalPrice"]) if item.get("TotalPrice") is not None else None,
                            "isDeleted": item.get("IsDeleted", False),
                            "sourceCreatedAtTimestamp": _parse_salesforce_timestamp(item.get("CreatedDate")),
                            "sourceUpdatedAtTimestamp": _parse_salesforce_timestamp(item.get("LastModifiedDate")),
                        }
                    )

            # Build final edge list; to_id is embedded so the delete step can reuse it
            final_sold_in_items: List[Tuple[str, List[Dict[str, Any]]]] = []
            for deal_internal_id, by_product in deal_groups.values():
                if not by_product:
                    continue
                edges_list: List[Dict[str, Any]] = []
                for product_id, raw_lines in by_product.items():
                    created_timestamps = [
                        row["sourceCreatedAtTimestamp"]
                        for row in raw_lines
                        if row.get("sourceCreatedAtTimestamp") is not None
                    ]
                    updated_timestamps = [
                        row["sourceUpdatedAtTimestamp"]
                        for row in raw_lines
                        if row.get("sourceUpdatedAtTimestamp") is not None
                    ]
                    min_created_ts = min(created_timestamps) if created_timestamps else None
                    max_updated_ts = max(updated_timestamps) if updated_timestamps else None
                    quantities     = [row["quantity"]          for row in raw_lines]
                    unit_prices    = [row["unitPrice"]         for row in raw_lines]
                    total_prices   = [row["totalPrice"]        for row in raw_lines]
                    is_deleted_flags = [bool(row["isDeleted"]) for row in raw_lines]
                    # Parallel arrays must stay in sync; log loudly if they diverge.
                    lengths = {len(quantities), len(unit_prices), len(total_prices), len(is_deleted_flags)}
                    if len(lengths) != 1:
                        self.logger.error(
                            "soldIn parallel-array length mismatch for product=%s deal=%s: "
                            "quantities=%d unitPrices=%d totalPrices=%d isDeletedFlags=%d — skipping edge",
                            product_id, deal_internal_id,
                            len(quantities), len(unit_prices), len(total_prices), len(is_deleted_flags),
                        )
                        continue
                    edge_data: Dict[str, Any] = {
                        "from_id": product_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": deal_internal_id,
                        "to_collection": CollectionNames.RECORDS.value,
                        "quantities": quantities,
                        "unitPrices": unit_prices,
                        "totalPrices": total_prices,
                        "isDeletedFlags": is_deleted_flags,
                    }
                    if min_created_ts is not None:
                        edge_data["createdAtTimestamp"] = min_created_ts
                    if max_updated_ts is not None:
                        edge_data["updatedAtTimestamp"] = max_updated_ts
                        edge_data["sourceUpdatedAtTimestamp"] = max_updated_ts
                    edges_list.append(edge_data)
                final_sold_in_items.append((deal_internal_id, edges_list))

            if final_sold_in_items:
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    delete_tasks = [
                        tx_store.delete_edge(
                            from_id=edge.get("from_id"),
                            from_collection=edge.get("from_collection"),
                            to_id=edge.get("to_id"),
                            to_collection=CollectionNames.RECORDS.value,
                            collection=CollectionNames.SOLD_IN.value,
                        )
                        for _, edges_list in final_sold_in_items
                        for edge in edges_list
                    ]
                    if delete_tasks:
                        for _del in delete_tasks:
                            await _del

                    all_sold_in_edges = [
                        edge
                        for _, edges_list in final_sold_in_items
                        for edge in edges_list
                    ]
                    if all_sold_in_edges:
                        await tx_store.batch_create_edges(
                            all_sold_in_edges,
                            collection=CollectionNames.SOLD_IN.value,
                        )

            self.logger.info(
                "Synced soldIn edges for %d unique deals (total %d edges)",
                len(final_sold_in_items),
                sum(len(edges) for _, edges in final_sold_in_items),
            )
        except Exception as e:
            self.logger.error(f"Error syncing soldIn edges: {e}", exc_info=True)
            raise

    async def _sync_cases(
        self, case_records_pages: AsyncGenerator[List[SalesforceCase], None],
    ) -> Set[str]:
        """Sync Salesforce Cases page-by-page as ticket records.

        Per page: build records, batch-fetch existing externalRecordIds for this page,
        partition into new/updated, persist, and continue.  Returns the running set
        of newly-created Case IDs across all pages for the caller's task/file backfill.
        """
        try:
            newly_synced_case_ids: Set[str] = set()
            total = 0
            total_new = 0
            total_updated = 0

            async for page in case_records_pages:
                records: List[Record] = []
                for case_row in page:
                    try:
                        if not case_row.Id:
                            continue
                        record = self._build_case_record(case_row)
                        if self.indexing_filters and self.indexing_filters.is_enabled(
                            IndexingFilterKey.ENABLE_MANUAL_SYNC,
                        ):
                            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                        records.append(record)
                    except Exception as e:
                        self.logger.warning(f"Failed to process case {case_row.Id}: {e}")
                        continue

                if not records:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    case_existing_nodes = await tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=[r.external_record_id for r in records if r.external_record_id],
                    )
                case_existing_ext_ids = {
                    node.get("externalRecordId")
                    for node in (case_existing_nodes or [])
                    if node.get("externalRecordId") and node.get("connectorId") == self.connector_id
                }
                new_case_records = [r for r in records if r.external_record_id not in case_existing_ext_ids]
                update_case_records = [r for r in records if r.external_record_id in case_existing_ext_ids]
                if new_case_records:
                    await self.data_entities_processor.on_new_records(
                        [(r, []) for r in new_case_records],
                    )
                for record in update_case_records:
                    await self.data_entities_processor.on_record_content_update(record)

                newly_synced_case_ids.update(
                    r.external_record_id for r in new_case_records if r.external_record_id
                )
                total += len(records)
                total_new += len(new_case_records)
                total_updated += len(update_case_records)

            self.logger.info(
                "Synced %d case records (%d new, %d updated).",
                total, total_new, total_updated,
            )
            return newly_synced_case_ids

        except Exception as e:
            self.logger.error(f"Error syncing cases: {e}", exc_info=True)
            raise

    async def _sync_tasks(
        self,
        task_records_pages: AsyncGenerator[List[SalesforceTask], None],
        created_after_ms: Optional[int] = None,
        updated_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None,
        updated_before_ms: Optional[int] = None,
    ) -> None:
        """Sync Salesforce Tasks page-by-page as ticket records.

        Per page: scope DB lookups to this page's WhatIds, parent-entity filter +
        date filter, batch-fetch parent Account IDs (concurrent SOQL fan-out bounded
        per page), build records, partition new/updated, persist.

        Date filters take a (after_ms, before_ms) pair per column to support all of
        the framework's datetime operators (is_after, is_before, is_between, last_N_days).
        """
        def _task_passes_date_filter(task: SalesforceTask) -> bool:
            if not _ts_in_bounds(
                _parse_salesforce_timestamp(task.CreatedDate),
                created_after_ms,
                created_before_ms,
            ):
                return False
            if not _ts_in_bounds(
                _parse_salesforce_timestamp(task.LastModifiedDate),
                updated_after_ms,
                updated_before_ms,
            ):
                return False
            return True

        try:
            total = 0
            total_new = 0
            total_updated = 0

            async for task_records in task_records_pages:
                if not task_records:
                    continue

                non_account_what_ids: set[str] = set()
                account_what_ids: set[str] = set()
                for _task in task_records:
                    if not _task.WhatId:
                        continue
                    if (_task.What or {}).get("Type") == "Account":
                        account_what_ids.add(_task.WhatId)
                    else:
                        non_account_what_ids.add(_task.WhatId)

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    _all_candidate_nodes = await _get_nodes_by_field_in_batched(
                        tx_store,
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=list(non_account_what_ids),
                    )
                    candidate_nodes = [
                        n for n in (_all_candidate_nodes or [])
                        if n.get("connectorId") == self.connector_id
                    ]
                    _all_account_group_nodes = await _get_nodes_by_field_in_batched(
                        tx_store,
                        collection=CollectionNames.RECORD_GROUPS.value,
                        field="externalGroupId",
                        values=list(account_what_ids),
                        return_fields=["externalGroupId", "groupType", "connectorId"],
                    )
                    account_group_nodes = [
                        n for n in (_all_account_group_nodes or [])
                        if n.get("connectorId") == self.connector_id
                    ]

                    async def _has_belongs_to_edge(node: dict) -> bool:
                        internal_id = node.get("id") or node.get("_key")
                        if not internal_id:
                            return False
                        edges = await tx_store.get_edges_from_node(
                            from_node_id=f"{CollectionNames.RECORDS.value}/{internal_id}",
                            edge_collection=CollectionNames.BELONGS_TO.value,
                        )
                        return bool(edges)

                    _EDGE_CHECK_BATCH = 500
                    synced_nodes = []
                    for _i in range(0, len(candidate_nodes), _EDGE_CHECK_BATCH):
                        _batch = candidate_nodes[_i : _i + _EDGE_CHECK_BATCH]
                        _flags = await asyncio.gather(*[_has_belongs_to_edge(n) for n in _batch])
                        synced_nodes.extend(n for n, ok in zip(_batch, _flags) if ok)

                synced_external_ids: set[str] = {
                    node["externalRecordId"]
                    for node in synced_nodes
                    if node.get("externalRecordId")
                }
                synced_account_ids: set[str] = {
                    node["externalGroupId"]
                    for node in (account_group_nodes or [])
                    if node.get("externalGroupId")
                    and node.get("groupType") == RecordGroupType.SALESFORCE_ORG.value
                }

                before_count = len(task_records)
                kept_tasks = [
                    task for task in task_records
                    if (
                        # Unassigned tasks (no WhatId): include only when they pass date filters.
                        (not task.WhatId and _task_passes_date_filter(task))
                        # Opportunity/Case/Task-parented tasks: date filters are applied at the
                        # parent level (only Opportunities/Cases within the date window are
                        # synced). Once a parent is synced, all its child tasks are included
                        # unconditionally.
                        or (
                            task.WhatId
                            and (task.What or {}).get("Type") != "Account"
                            and task.WhatId in synced_external_ids
                        )
                        # Account parents: Accounts have no date-scoped sync of their own, so
                        # date filters are applied directly to the task.
                        or (
                            task.WhatId
                            and (task.What or {}).get("Type") == "Account"
                            and task.WhatId in synced_account_ids
                            and _task_passes_date_filter(task)
                        )
                    )
                ]
                self.logger.debug(
                    "Parent-entity filter: kept %d/%d tasks", len(kept_tasks), before_count,
                )
                if not kept_tasks:
                    continue

                opp_ids = {
                    t.WhatId for t in kept_tasks
                    if t.WhatId and (t.What or {}).get("Type") == "Opportunity"
                }
                case_ids = {
                    t.WhatId for t in kept_tasks
                    if t.WhatId and (t.What or {}).get("Type") == "Case"
                }
                opp_account_map, case_account_map = await asyncio.gather(
                    self._get_account_ids_for_opportunities(opp_ids),
                    self._get_account_ids_for_cases(case_ids),
                )

                records: List[Record] = []
                for task_row in kept_tasks:
                    try:
                        if not task_row.Id:
                            continue
                        record = self._build_task_record(task_row, opp_account_map, case_account_map)
                        if self.indexing_filters and self.indexing_filters.is_enabled(
                            IndexingFilterKey.ENABLE_MANUAL_SYNC,
                        ):
                            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                        records.append(record)
                    except Exception as e:
                        self.logger.warning(f"Failed to process task {task_row.Id}: {e}")
                        continue

                if not records:
                    continue

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    task_existing_nodes = await _get_nodes_by_field_in_batched(
                        tx_store,
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=[r.external_record_id for r in records if r.external_record_id],
                    )
                task_existing_by_ext_id: Dict[str, Dict] = {
                    node.get("externalRecordId"): node
                    for node in (task_existing_nodes or [])
                    if node.get("externalRecordId") and node.get("connectorId") == self.connector_id
                }
                new_task_records = [r for r in records if r.external_record_id not in task_existing_by_ext_id]
                update_task_records = [r for r in records if r.external_record_id in task_existing_by_ext_id]

                if new_task_records:
                    await self.data_entities_processor.on_new_records(
                        [(r, []) for r in new_task_records],
                    )
                for record in update_task_records:
                    existing_node = task_existing_by_ext_id.get(record.external_record_id)
                    existing_internal_id = existing_node.get("id") or existing_node.get("_key") if existing_node else None
                    existing_rg_ext_id = existing_node.get("externalGroupId") if existing_node else None
                    if existing_internal_id:
                        record.id = existing_internal_id
                    if existing_rg_ext_id and existing_rg_ext_id != record.external_record_group_id:
                        async with self.data_store_provider.transaction() as tx_store:
                            await tx_store.delete_edges_from(
                                record.id,
                                CollectionNames.RECORDS.value,
                                CollectionNames.BELONGS_TO.value,
                            )
                    await self.data_entities_processor.on_record_content_update(record)

                total += len(records)
                total_new += len(new_task_records)
                total_updated += len(update_task_records)

            self.logger.info(
                "Synced %d task records (%d new, %d updated).",
                total, total_new, total_updated,
            )

        except Exception as e:
            self.logger.error(f"Error syncing tasks: {e}", exc_info=True)
            raise

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (content changed, metadata changed).
        """
        try:
            if record_update.is_deleted and record_update.external_record_id:
                await self.data_entities_processor.on_record_deleted(record_id=record_update.external_record_id)
            elif record_update.is_updated and record_update.record:
                if record_update.content_changed:
                    self.logger.debug(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)
                if record_update.metadata_changed:
                    self.logger.debug(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def _sync_files(
        self,
        api_version: str,
        file_records_pages: AsyncGenerator[List[SalesforceContentVersion], None],
        created_after_ms: Optional[int] = None,
        updated_after_ms: Optional[int] = None,
        created_before_ms: Optional[int] = None,
        updated_before_ms: Optional[int] = None,
    ) -> None:
        """Sync Salesforce Files page-by-page.

        Per page: build a per-page `files_by_doc_id` map, drain the
        ContentDocumentLink lookups sequentially (one IN-batch at a time),
        resolve EmailMessage->Task, scope DB lookups to this page's linked IDs,
        run the parent-entity filter, build records, and persist new/updated
        records.

        Files whose parent (LinkedEntityId) is an Account are included only when the
        Account has already been synced by this connector AND the file itself passes
        the configured date filters.
        """
        IN_CLAUSE_BATCH_SIZE = 500
        PARENT_TYPES = {"Opportunity", "Task", "Case", "Account"}
        _LINKED_TYPE_TO_RECORD_TYPE: Dict[str, RecordType] = {
            "Opportunity": RecordType.DEAL,
            "Case": RecordType.CASE,
            "Task": RecordType.TASK,
        }

        def _file_passes_date_filter(meta: Optional[SalesforceContentVersion]) -> bool:
            if meta is None:
                return True
            if not _ts_in_bounds(
                _parse_salesforce_timestamp(meta.CreatedDate),
                created_after_ms,
                created_before_ms,
            ):
                return False
            if not _ts_in_bounds(
                _parse_salesforce_timestamp(meta.LastModifiedDate),
                updated_after_ms,
                updated_before_ms,
            ):
                return False
            return True

        try:
            files_record_group = RecordGroup(
                name="Salesforce Files",
                external_group_id=f"{self.data_entities_processor.org_id}-files",
                group_type=RecordGroupType.SALESFORCE_FILE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                is_internal=True,
            )
            await self.data_entities_processor.on_new_record_groups([
                (files_record_group, [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)])
            ])

            org_id = self.data_entities_processor.org_id
            total_new = 0
            total_updated = 0

            async for file_records in file_records_pages:
                if not file_records:
                    continue

                files_by_doc_id: Dict[str, SalesforceContentVersion] = {}
                for cv in file_records:
                    if cv.ContentDocumentId:
                        files_by_doc_id[cv.ContentDocumentId] = cv
                unique_doc_ids = list(files_by_doc_id.keys())
                if not unique_doc_ids:
                    continue

                # Concurrent fan-out for ContentDocumentLink lookups, bounded per page.
                link_queries: List[str] = []
                for i in range(0, len(unique_doc_ids), IN_CLAUSE_BATCH_SIZE):
                    safe_doc_ids = _sanitize_soql_ids_batch(
                        unique_doc_ids[i : i + IN_CLAUSE_BATCH_SIZE],
                        self.logger, "sync files link query",
                    )
                    if not safe_doc_ids:
                        continue
                    in_list = ", ".join(f"'{d_id}'" for d_id in safe_doc_ids)
                    link_queries.append(
                        "SELECT ContentDocumentId, LinkedEntityId, LinkedEntity.Type "
                        f"FROM ContentDocumentLink WHERE ContentDocumentId IN ({in_list})"
                    )

                links_map: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
                docs_with_any_links: Set[str] = set()
                email_message_ids: Set[str] = set()
                for q in link_queries:
                    async for link_page in self._soql_query_paginated(
                        api_version=api_version, q=q,
                    ):
                        for row in link_page:
                            doc_id = row.get("ContentDocumentId")
                            if not doc_id:
                                continue
                            links_map[doc_id].append(row)
                            docs_with_any_links.add(doc_id)
                            linked_entity_type = (row.get("LinkedEntity") or {}).get("Type")
                            linked_entity_id = row.get("LinkedEntityId")
                            if linked_entity_type == "EmailMessage" and linked_entity_id:
                                email_message_ids.add(linked_entity_id)

                # Resolve EmailMessage -> Task IDs (sequential drain).
                email_to_task_map: Dict[str, str] = {}
                if email_message_ids:
                    email_message_ids_list = list(email_message_ids)
                    email_queries: List[str] = []
                    for i in range(0, len(email_message_ids_list), IN_CLAUSE_BATCH_SIZE):
                        safe_ids = _sanitize_soql_ids_batch(
                            email_message_ids_list[i : i + IN_CLAUSE_BATCH_SIZE],
                            self.logger, "sync files email message lookup",
                        )
                        if not safe_ids:
                            continue
                        ids_clause = "','".join(safe_ids)
                        email_queries.append(
                            f"SELECT Id, ActivityId FROM EmailMessage WHERE Id IN ('{ids_clause}')"
                        )
                    for q in email_queries:
                        async for em_page in self._soql_query_paginated(
                            api_version=api_version, q=q,
                        ):
                            for row in em_page:
                                if row.get("ActivityId"):
                                    email_to_task_map[row["Id"]] = row["ActivityId"]

                non_account_linked_ids: Set[str] = set()
                account_linked_ids: Set[str] = set()
                for _links in links_map.values():
                    for _link in _links:
                        _lid = _link.get("LinkedEntityId")
                        _ltype = (_link.get("LinkedEntity") or {}).get("Type")
                        if not _lid:
                            continue
                        # EmailMessage must be resolved before the PARENT_TYPES guard —
                        # "EmailMessage" is not in PARENT_TYPES, so checking that first
                        # would skip the resolution and the task ID would never be added.
                        if _ltype == "EmailMessage":
                            _task_id = email_to_task_map.get(_lid)
                            if _task_id:
                                non_account_linked_ids.add(_task_id)
                        elif _ltype not in PARENT_TYPES:
                            continue
                        elif _ltype == "Account":
                            account_linked_ids.add(_lid)
                        else:
                            non_account_linked_ids.add(_lid)

                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    _all_candidate_nodes = await _get_nodes_by_field_in_batched(
                        tx_store,
                        collection=CollectionNames.RECORDS.value,
                        field="externalRecordId",
                        values=list(non_account_linked_ids),
                    )
                    _candidate_nodes = [
                        n for n in (_all_candidate_nodes or [])
                        if n.get("connectorId") == self.connector_id
                    ]
                    _all_account_group_nodes = await _get_nodes_by_field_in_batched(
                        tx_store,
                        collection=CollectionNames.RECORD_GROUPS.value,
                        field="externalGroupId",
                        values=list(account_linked_ids),
                        return_fields=["externalGroupId", "groupType", "connectorId"],
                    )
                    _account_group_nodes = [
                        n for n in (_all_account_group_nodes or [])
                        if n.get("connectorId") == self.connector_id
                    ]

                    async def _has_belongs_to_edge(node: dict) -> bool:
                        internal_id = node.get("id") or node.get("_key")
                        if not internal_id:
                            return False
                        edges = await tx_store.get_edges_from_node(
                            from_node_id=f"{CollectionNames.RECORDS.value}/{internal_id}",
                            edge_collection=CollectionNames.BELONGS_TO.value,
                        )
                        return bool(edges)

                    _EDGE_CHECK_BATCH = 500
                    _synced_nodes = []
                    for _i in range(0, len(_candidate_nodes), _EDGE_CHECK_BATCH):
                        _batch = _candidate_nodes[_i : _i + _EDGE_CHECK_BATCH]
                        _flags = await asyncio.gather(*[_has_belongs_to_edge(n) for n in _batch])
                        _synced_nodes.extend(n for n, ok in zip(_batch, _flags) if ok)

                synced_external_ids: Set[str] = {
                    node["externalRecordId"]
                    for node in _synced_nodes
                    if node.get("externalRecordId") and node.get("connectorId") == self.connector_id
                }
                synced_account_ids: Set[str] = {
                    node["externalGroupId"]
                    for node in (_account_group_nodes or [])
                    if node.get("externalGroupId")
                    and node.get("groupType") == RecordGroupType.SALESFORCE_ORG.value
                    and node.get("connectorId") == self.connector_id
                }

                all_records: List[Tuple[FileRecord, str]] = []
                for doc_id in docs_with_any_links:
                    for link in links_map[doc_id]:
                        linked_entity_id = link.get("LinkedEntityId")
                        linked_entity_type = (link.get("LinkedEntity") or {}).get("Type")
                        if linked_entity_type == "EmailMessage":
                            task_id = email_to_task_map.get(linked_entity_id)
                            if not task_id:
                                continue
                            linked_entity_id = task_id
                            linked_entity_type = "Task"
                        if linked_entity_type not in PARENT_TYPES:
                            continue
                        if linked_entity_type == "Account":
                            if linked_entity_id not in synced_account_ids:
                                continue
                            if not _file_passes_date_filter(files_by_doc_id.get(doc_id)):
                                continue
                        elif linked_entity_id not in synced_external_ids:
                            continue

                        file_parent_record_type: Optional[RecordType] = None
                        if linked_entity_type == "Account":
                            parent_id = None
                            external_record_group_id = linked_entity_id
                        else:
                            parent_id = linked_entity_id
                            external_record_group_id = f"{org_id}-files"
                            file_parent_record_type = _LINKED_TYPE_TO_RECORD_TYPE.get(linked_entity_type)

                        external_record_id = f"{doc_id}-{linked_entity_id}"
                        meta = files_by_doc_id.get(doc_id)
                        rec = self._build_file_record(
                            meta, external_record_id, parent_id,
                            external_record_group_id, file_parent_record_type,
                        )
                        if rec:
                            all_records.append((rec, external_record_id))

                for doc_id in (set(files_by_doc_id.keys()) - docs_with_any_links):
                    meta = files_by_doc_id.get(doc_id)
                    rec = self._build_file_record(
                        meta, doc_id,
                        external_record_group_id=f"{org_id}-files",
                    )
                    if self.indexing_filters and self.indexing_filters.is_enabled(
                        IndexingFilterKey.ENABLE_MANUAL_SYNC,
                    ):
                        rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    if rec:
                        all_records.append((rec, doc_id))

                new_records_batch: List[Tuple[FileRecord, List[Permission]]] = []
                page_updated = 0
                for rec, ext_id in all_records:
                    if not rec:
                        continue
                    existing = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id, ext_id,
                    )
                    if existing is None:
                        new_records_batch.append((rec, []))
                        continue

                    content_changed = (getattr(existing, "md5_hash", None) != rec.md5_hash)
                    metadata_changed = (
                        getattr(existing, "record_name", None) != rec.record_name
                        or getattr(existing, "external_revision_id", None) != rec.external_revision_id
                        or getattr(existing, "source_updated_at", None) != rec.source_updated_at
                        or getattr(existing, "size_in_bytes", None) != rec.size_in_bytes
                        or getattr(existing, "extension", None) != rec.extension
                        or getattr(existing, "mime_type", None) != rec.mime_type
                        or getattr(existing, "weburl", None) != rec.weburl
                    )
                    if content_changed or metadata_changed:
                        rec.id = existing.id
                        rec.version = getattr(existing, "version", 0) + 1
                        record_update = RecordUpdate(
                            record=rec,
                            is_new=False,
                            is_updated=True,
                            is_deleted=False,
                            metadata_changed=metadata_changed,
                            content_changed=content_changed,
                            permissions_changed=False,
                            external_record_id=ext_id,
                        )
                        await self._handle_record_updates(record_update)
                        page_updated += 1

                for i in range(0, len(new_records_batch), 100):
                    await self.data_entities_processor.on_new_records(
                        new_records_batch[i : i + 100],
                    )

                total_new += len(new_records_batch)
                total_updated += page_updated

            self.logger.info(
                "Synced Salesforce files: %d new, %d updated", total_new, total_updated,
            )

        except Exception as e:
            self.logger.error(f"Failed to sync Salesforce files: {str(e)}", exc_info=True)
            raise

    async def _sync_permissions_edges(self, api_version: str) -> None:
        """
        Sync all Salesforce permission edges in three phases:
        1. Pre-fetch records, record-groups, and DB users in one transaction.
        2. Collect (user, record, access_level) tuples from Salesforce concurrently
           (API calls only — no DB writes during collection).
        3. Replace all stale permission edges with fresh ones in a single batch
           transaction: O(records + groups) deletes + 2 batch_create_edges calls.
        """
        # 1. Fetch all active Salesforce users (streamed; bounded by user count
        #    which is the natural cardinality of this query — capping pages here
        #    has no further benefit).
        soql_query = "SELECT Id, Email FROM User WHERE IsActive = true"
        sf_users: List[Dict[str, str]] = []
        async for page in self._soql_query_paginated(api_version=api_version, q=soql_query):
            for u in page:
                if u.get("Id") and u.get("Email"):
                    sf_users.append(u)

        # 2. Fetch records, record groups, and matching DB users in one transaction
        async with self.data_store_provider.transaction() as tx_store:
            salesforce_records = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value,
                field="recordType",
                values=[RecordType.DEAL.value, RecordType.TASK.value, RecordType.CASE.value, RecordType.PRODUCT.value],
            )
            salesforce_records = [r for r in salesforce_records if r.get("connectorId") == self.connector_id]

            salesforce_record_groups = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORD_GROUPS.value,
                field="groupType",
                values=[RecordGroupType.SALESFORCE_ORG.value],
            )
            salesforce_record_groups = [r for r in salesforce_record_groups if r.get("connectorId") == self.connector_id]

            # Pre-fetch all DB user nodes whose email matches a Salesforce user
            sf_user_emails = [u.get("Email") for u in sf_users]
            db_users = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.USERS.value,
                field="email",
                values=sf_user_emails,
            )

        # 3. Build O(1) lookup maps to avoid per-record DB round-trips
        ext_id_to_record_id: Dict[str, str] = {
            r.get("externalRecordId"): (r.get("id") or r.get("_key"))
            for r in salesforce_records
            if r.get("externalRecordId") and (r.get("id") or r.get("_key"))
        }
        ext_id_to_rg_id: Dict[str, str] = {
            r.get("externalGroupId"): (r.get("id") or r.get("_key"))
            for r in salesforce_record_groups
            if r.get("externalGroupId") and (r.get("id") or r.get("_key"))
        }
        email_to_user_id: Dict[str, str] = {
            u.get("email"): (u.get("id") or u.get("_key"))
            for u in (db_users or [])
            if u.get("email") and (u.get("id") or u.get("_key"))
        }

        salesforce_external_ids = list(ext_id_to_record_id.keys())
        salesforce_rg_external_ids = list(ext_id_to_rg_id.keys())

        if not salesforce_external_ids:
            self.logger.info("No Salesforce records found to sync permissions for.")
            return

        self.logger.info(
            "Syncing permissions for %d users across %d records and %d record groups.",
            len(sf_users), len(salesforce_external_ids), len(salesforce_rg_external_ids),
        )

        # 4. Collect all permission tuples from Salesforce API concurrently (no DB writes)
        MAX_CONCURRENT_USERS = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)
        tasks = [
            self._sync_permissions_for_user(
                user=user,
                salesforce_external_ids=salesforce_external_ids,
                salesforce_record_group_external_ids=salesforce_rg_external_ids,
                api_version=api_version,
                semaphore=semaphore,
            )
            for user in sf_users
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_permission_tuples: List[Tuple[str, str, str, bool]] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                self.logger.error(
                    "Failed to collect permissions for user %s: %s",
                    sf_users[i].get("Email"), result,
                )
            else:
                all_permission_tuples.extend(result)  # type: ignore[arg-type]

        self.logger.info("Collected %d permission tuples total.", len(all_permission_tuples))

        # 5. Single batch transaction: delete stale edges then create fresh ones.
        #    Deleting by target record/group (not by user) is O(records + groups) instead
        #    of O(users × records).  SALESFORCE_ORG record groups carry no ORG-level
        #    permission edges, so wiping all PERMISSION edges to them is safe.
        all_record_internal_ids = list(ext_id_to_record_id.values())
        all_rg_internal_ids = list(ext_id_to_rg_id.values())

        async with self.data_store_provider.transaction() as tx_store:
            delete_tasks = [
                tx_store.delete_edges_to(
                    rid,
                    CollectionNames.RECORDS.value,
                    CollectionNames.PERMISSION.value,
                )
                for rid in all_record_internal_ids
            ] + [
                tx_store.delete_edges_to(
                    rgid,
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.PERMISSION.value,
                )
                for rgid in all_rg_internal_ids
            ]
            if delete_tasks:
                for _del in delete_tasks:
                    await _del

            record_edges: List[Dict] = []
            rg_edges: List[Dict] = []
            for email, ext_id, access_level, is_group in all_permission_tuples:
                user_internal_id = email_to_user_id.get(email)
                if not user_internal_id:
                    self.logger.debug("Skipping permission: no DB user for email %s", email)
                    continue
                try:
                    perm_type = PermissionType(access_level)
                except ValueError:
                    self.logger.warning(
                        "Unknown access level %r for user %s, skipping", access_level, email
                    )
                    continue

                permission = Permission(email=email, type=perm_type, entity_type=EntityType.USER)
                if is_group:
                    target_id = ext_id_to_rg_id.get(ext_id)
                    if not target_id:
                        continue
                    rg_edges.append(permission.to_arango_permission(
                        from_id=user_internal_id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=target_id,
                        to_collection=CollectionNames.RECORD_GROUPS.value,
                    ))
                else:
                    target_id = ext_id_to_record_id.get(ext_id)
                    if not target_id:
                        continue
                    record_edges.append(permission.to_arango_permission(
                        from_id=user_internal_id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=target_id,
                        to_collection=CollectionNames.RECORDS.value,
                    ))

            if record_edges:
                await tx_store.batch_create_edges(record_edges, collection=CollectionNames.PERMISSION.value)
            if rg_edges:
                await tx_store.batch_create_edges(rg_edges, collection=CollectionNames.PERMISSION.value)

        self.logger.info(
            "Permission sync complete: %d record edges and %d record group edges written.",
            len(record_edges), len(rg_edges),
        )

    async def _sync_permissions_for_user(
        self,
        user: Dict[str, str],
        salesforce_external_ids: List[str],
        salesforce_record_group_external_ids: List[str],
        api_version: str,
        semaphore: asyncio.Semaphore,
    ) -> List[Tuple[str, str, str, bool]]:
        """
        Collect all permission tuples for one user via the Salesforce UserRecordAccess
        API.  Returns a list of (email, external_id, access_level, is_record_group)
        tuples.  No database writes are performed here — the caller batches them.
        """
        user_id = user.get("Id")
        user_email = user.get("Email")

        if not user_id or not user_email:
            return []

        collected: List[Tuple[str, str, str, bool]] = []
        async with semaphore:
            async for rec_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_external_ids,
                api_version=api_version,
            ):
                collected.append((email, rec_id, access_level, False))

            async for rg_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_record_group_external_ids,
                api_version=api_version,
            ):
                collected.append((email, rg_id, access_level, True))

        return collected

    async def salesforce_record_group_permissions_sync(
        self,
        connector_id: str,
        record_group_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a Salesforce org record group.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                record_group = await tx_store.get_record_group_by_external_id(
                    connector_id=connector_id,
                    external_id=record_group_external_id,
                )
                if not record_group:
                    self.logger.warning(
                        f"Record group with external id {record_group_external_id} not found in database"
                    )
                    return

                if record_group.group_type != RecordGroupType.SALESFORCE_ORG:
                    return

                user = await tx_store.get_user_by_email(users_email)
                if not user:
                    self.logger.warning(f"User with email {users_email} not found in database")
                    return

                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record_group.id,
                    to_collection=CollectionNames.RECORD_GROUPS.value,
                    collection=CollectionNames.PERMISSION.value,
                )

                required_level = PERMISSION_HIERARCHY.get(access_level.value, 0)
                if existing_edge:
                    existing_role = existing_edge.get("role", "READER")
                    existing_level = PERMISSION_HIERARCHY.get(existing_role, 0)
                    if existing_level == required_level:
                        return
                    await tx_store.delete_edge(
                        from_id=user.id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=record_group.id,
                        to_collection=CollectionNames.RECORD_GROUPS.value,
                        collection=CollectionNames.PERMISSION.value,
                    )

                permission = Permission(
                    email=users_email,
                    type=access_level,
                    entity_type=EntityType.USER,
                )
                edge_data = permission.to_arango_permission(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record_group.id,
                    to_collection=CollectionNames.RECORD_GROUPS.value,
                )
                await tx_store.batch_create_edges([edge_data], collection=CollectionNames.PERMISSION.value)
        except Exception as e:
            self.logger.error(f"Failed to sync record group permissions: {str(e)}", exc_info=True)
            raise

    async def salesforce_permissions_sync(
        self,
        connector_id: str,
        record_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a record.
        If they already have that level or higher, return. Otherwise create or upgrade the permission edge.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                record = await tx_store.get_record_by_external_id(
                    connector_id=connector_id,
                    external_id=record_external_id,
                )
                if not record:
                    self.logger.warning(f"Record with external id {record_external_id} not found in database")
                    return

                user = await tx_store.get_user_by_email(users_email)
                if not user:
                    self.logger.warning(f"User with email {users_email} not found in database")
                    return

                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record.id,
                    to_collection=CollectionNames.RECORDS.value,
                    collection=CollectionNames.PERMISSION.value,
                )

                required_level = PERMISSION_HIERARCHY.get(access_level.value, 0)
                if existing_edge:
                    existing_role = existing_edge.get("role", "READER")
                    existing_level = PERMISSION_HIERARCHY.get(existing_role, 0)
                    if existing_level == required_level:
                        self.logger.debug(
                            f"User {users_email} already has sufficient permission on record {record_external_id} "
                            f"(existing: {existing_role}, required: {access_level.value})"
                        )
                        return
                    await tx_store.delete_edge(
                        from_id=user.id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=record.id,
                        to_collection=CollectionNames.RECORDS.value,
                        collection=CollectionNames.PERMISSION.value,
                    )

                permission = Permission(
                    email=users_email,
                    type=access_level,
                    entity_type=EntityType.USER,
                )
                edge_data = permission.to_arango_permission(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record.id,
                    to_collection=CollectionNames.RECORDS.value,
                )
                await tx_store.batch_create_edges([edge_data], collection=CollectionNames.PERMISSION.value)
                self.logger.info(
                    f"Created/updated permission for {users_email} on record {record_external_id} to {access_level.value}"
                )
        except Exception as e:
            self.logger.error(f"Failed to sync permissions: {str(e)}", exc_info=True)
            raise

    def _flatten_single_group_members(
        self,
        group_id: str,
        group_to_members: Dict[str, List[str]],
        visited: set,
        current_path: set,
    ) -> set:
        """
        Recursively flatten a single group's members to find all actual User IDs.
        Returns:
            Set of User IDs that belong to this group (directly or indirectly)
        """
        if group_id in current_path:
            self.logger.warning(f"Circular reference detected for group {group_id}")
            return set()
        if group_id in visited:
            return set()

        visited.add(group_id)
        current_path.add(group_id)

        user_ids = set()
        immediate_members = group_to_members.get(group_id, [])

        for member_id in immediate_members:
            if member_id in group_to_members:
                nested_user_ids = self._flatten_single_group_members(
                    member_id, group_to_members, visited, current_path.copy()
                )
                user_ids.update(nested_user_ids)
            else:
                user_ids.add(member_id)

        current_path.remove(group_id)
        return user_ids

    async def _flatten_group_members(self, api_version: str) -> Dict[str, Set[tuple]]:
        """
        Fetch all groups and their members, then flatten nested groups to actual User IDs with emails.
        Returns:
            Map of group ID -> set of dicts with {'user_id': str, 'email': str}
        """
        try:
            soql_query = """
                SELECT Id, Name, 
                    (SELECT Id, UserOrGroupId, UserOrGroup.Email 
                    FROM GroupMembers) 
                FROM Group 
                WHERE Type IN ('Regular', 'Queue')
            """
            group_to_members: Dict[str, List[str]] = {}
            user_id_to_email: Dict[str, str] = {}

            async for page in self._soql_query_paginated(api_version=api_version, q=soql_query):
                for group in page:
                    group_id = group.get("Id")
                    if not group_id:
                        continue
                    records = group.get("GroupMembers", {}).get("records", [])
                    member_ids: List[str] = []
                    for member in records:
                        user_or_group_id = member.get("UserOrGroupId")
                        if not user_or_group_id:
                            continue
                        member_ids.append(user_or_group_id)
                        user_or_group = member.get("UserOrGroup")
                        if user_or_group and user_or_group.get("Email"):
                            user_id_to_email[user_or_group_id] = user_or_group.get("Email")
                    group_to_members[group_id] = member_ids

            if not group_to_members:
                self.logger.warning("No groups found")
                return {}
            
            # Flatten all groups to get actual user IDs
            flattened_result: Dict[str, Set[tuple]] = {}
            
            for group_id in group_to_members:
                visited: set = set()
                current_path: set = set()
                user_ids = self._flatten_single_group_members(
                    group_id, group_to_members, visited, current_path
                )
                
                # Convert user IDs to set of (user_id, email) tuples
                user_set = set()
                for user_id in user_ids:
                    email = user_id_to_email.get(user_id, "")
                    if email:  # Only include users with emails
                        user_set.add((user_id, email))
                
                flattened_result[group_id] = user_set
            
            return flattened_result
            
        except Exception as e:
            self.logger.error(f"Error flattening group members: {e}", exc_info=True)
            return {}

    async def run_incremental_sync(self) -> None:
        """
        Run an incremental sync of all Salesforce data.

        This delegates to ``run_sync``, which is already fully incremental:
        every one of the 12 data steps reads its own named sync-point timestamp
        and only fetches records changed since the last successful run.  The
        first call after a connector install (or a sync-point reset) therefore
        performs a full bootstrap; subsequent scheduled calls are lightweight.
        """
        try:
            self.logger.info("Starting Salesforce incremental sync.")
            await self.run_sync()
            self.logger.info("Salesforce incremental sync completed.")

        except Exception as ex:
            self.logger.error(f"Error in Salesforce incremental sync: {ex}", exc_info=True)
            raise

    def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handles webhook notifications from Salesforce.

        Args:
            notification: The webhook notification payload
        """
        self.logger.info("Salesforce webhook received.")
        asyncio.create_task(self.run_incremental_sync())

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up Salesforce connector resources.")
        self.data_source = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records from Salesforce.

        For each record, checks whether Salesforce has a newer version by comparing
        the stored external_revision_id (epoch ms string for DEAL/CASE) against the
        record's current LastModifiedDate. Records that have been updated are rebuilt
        and sent to on_record_content_update; unchanged records are batched and sent
        to reindex_existing_records.
        """
        try:
            await self._reinitialize_token_if_needed()
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Salesforce records")

            if not self.data_source:
                raise Exception("Salesforce client not initialized. Call init() first.")

            api_version = await self._get_api_version()
            to_reindex: List[Record] = []

            for record in records:
                # external_revision_id is stored as str(epoch_ms) for DEAL and CASE.
                # For TASK/PRODUCT it is a SystemModstamp string; for FILE a ContentVersionId.
                # Non-numeric values fall back to since_ms=0 → always re-fetch and rebuild.
                since_ms: Optional[int] = None
                if record.external_revision_id:
                    try:
                        since_ms = int(record.external_revision_id)
                    except (ValueError, TypeError):
                        pass

                updated_record = await self._fetch_salesforce_record_if_updated(
                    record.external_record_id, record.record_type, since_ms or 0, api_version
                )

                if updated_record:
                    await self.data_entities_processor.on_record_content_update(updated_record)
                else:
                    to_reindex.append(record)

            if to_reindex:
                await self.data_entities_processor.reindex_existing_records(to_reindex)

            self.logger.info(
                "Reindex complete: %s updated, %s reindexed as-is",
                len(records) - len(to_reindex),
                len(to_reindex),
            )

        except Exception as e:
            self.logger.error(f"Error during Salesforce reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> None:
        """Salesforce connector does not support dynamic filter options yet."""
        raise NotImplementedError("Salesforce connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        **kwargs: Any,
    ) -> "BaseConnector":
        """
        Factory method to create a Salesforce connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials
            connector_id: The connector instance ID
            scope: Connector scope (passed by ConnectorFactory)
            created_by: User id who created the connector (passed by ConnectorFactory)

        Returns:
            Initialized SalesforceConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return SalesforceConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )