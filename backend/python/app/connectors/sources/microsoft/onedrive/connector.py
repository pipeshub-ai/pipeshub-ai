import asyncio
import base64
import binascii
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from http import HTTPStatus
from logging import Logger
from typing import AsyncGenerator, Dict, List, NoReturn, Optional, Tuple, Any, override

from aiolimiter import AsyncLimiter
from azure.core.exceptions import ClientAuthenticationError
from azure.identity.aio import CertificateCredential, ClientSecretCredential
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph import GraphServiceClient
from msgraph.generated.drives.item.drive_item_request_builder import (
    DriveItemRequestBuilder,
)
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.subscription import Subscription
from msgraph.generated.users.item.drive.drive_request_builder import (
    DriveRequestBuilder as UserDriveRequestBuilder,
)
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import ActionItem, BaseConnector, ConnectionCheckResult
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
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterType,
    IndexingFilterKey,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.core.registry.types import FileContentValidationRule, ValidationRuleType
from app.connectors.sources.microsoft.common.apps import OneDriveApp
from app.connectors.sources.microsoft.common.msgraph_client import (
    MSGraphClient,
    RecordUpdate,
    map_msgraph_roles_to_permission_type,
)
from app.connectors.sources.microsoft.common.sharepoint_permission_scope import (
    ListUniquePermissions,
    SharePointAccessError,
    SharePointPermissionScopeReader,
    SharePointRestError,
    SharePointToken,
    drive_item_unique_id,
    etag_unique_id,
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
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationOutcome, NotificationSeverity, NotificationType
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.connectors.core.base.error.stream_errors import (
    not_found_at_source,
    to_stream_error,
)

def get_azure_error_payload(error: Exception) -> Dict[str, Any]:
    """Return Azure error payload JSON when present."""
    response = getattr(error, "response", None)
    if response is None:
        return {}
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def sanitize_azure_error(error: Exception) -> str:
    """Extract Azure `error_description` if available, else return full error."""
    payload = get_azure_error_payload(error)
    description = payload.get("error_description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    return str(error).strip()


def sanitize_graph_error(error: ODataError) -> str:
    """Return useful Graph error fields without SDK internals or request IDs."""
    graph_error = error.error
    error_code = graph_error.code.strip() if graph_error and graph_error.code else ""
    error_message = graph_error.message.strip() if graph_error and graph_error.message else ""
    status_code = error.response_status_code

    details = ": ".join(part for part in (error_code, error_message) if part)
    if status_code:
        return f"{details or 'Microsoft Graph request failed'} (HTTP {status_code})"
    return details or "Microsoft Graph request failed"


class DrivePageIncompleteError(Exception):
    """A change on this delta page could not be fully applied; the page must be read again."""


# How many runs a delta page is read again for a temporary failure before its
# unreadable items are skipped (keeping what is stored) so the rest can move on.
MAX_PAGE_ATTEMPTS = 5
# How a drive's stored records were written, kept per drive (``_drive_permissions_key``).
# "mode" is INHERITANCE once every record was written knowing whether it inherits,
# apart from the items listed in "recheck". No mode, as for a drive synced before
# a certificate was configured, means records carry a copy of their effective access.
PERMISSION_MODE_INHERITANCE = "inheritance"
PERMISSION_MODE_ITEM_ACL = "itemAcl"
# Items written without knowing whether they inherit are read again next run; past
# this many, the drive is rewritten in full instead.
MAX_RECHECK_ITEMS = 1000
# How long before a client certificate expires the instance card asks for a new one.
CERTIFICATE_EXPIRY_WARNING = timedelta(days=30)
CERTIFICATE_SETUP_DOCS_URL = "https://docs.pipeshub.com/connectors/microsoft-365/one-drive#switch-to-certificate-sign-in"
# What SharePoint last said to the connector's certificate, kept so a run that never asks keeps the card as it was.
SHAREPOINT_HEALTH_OK = "ok"
SHAREPOINT_HEALTH_REFUSED = "refused"
SHAREPOINT_HEALTH_FORBIDDEN = "forbidden"
# How often a connector still signing in with a client secret reminds its admin about certificates.
CERTIFICATE_REMINDER_INTERVAL_MS = 7 * 24 * 3600 * 1000
# How many users the setup form's connection check tries before it finds one with a OneDrive.
SHAREPOINT_CHECK_USERS = 10

# A 403 or 404 on one item's members or access won't clear up by trying again.
_PERMANENT_READ_STATUSES = frozenset({403, 404})


class GraphReadFailedError(Exception):
    """A Graph read needed to apply a change failed; ``permanent`` when retrying can't help."""

    def __init__(self, message: str, *, permanent: bool) -> None:
        super().__init__(message)
        self.permanent = permanent


def _read_failure(what: str, error: Exception) -> GraphReadFailedError:
    status = error.response_status_code if isinstance(error, ODataError) else None
    return GraphReadFailedError(f"could not read {what}: {error}", permanent=status in _PERMANENT_READ_STATUSES)


@dataclass
class _FolderWalk:
    """Items below a folder whose access could not be brought up to date."""

    unread_items: list[str] = field(default_factory=list)
    unlisted_folders: list[str] = field(default_factory=list)
    temporary: bool = False
    # Unique items whose inherit edge was dropped because their access couldn't be read.
    hidden_items: list[str] = field(default_factory=list[str])

    @property
    def complete(self) -> bool:
        return not self.unread_items and not self.unlisted_folders


def _drive_permissions_key(drive_id: str) -> str:
    return generate_record_sync_point_key("drivePermissions", "drives", drive_id)


@dataclass
class _DeltaCursor:
    """Where a pass over a drive's delta pages keeps its place."""

    key: str
    next_field: str
    held_field: str
    held_attempts_field: str
    # Where the pass's delta link is kept for the next run; None when it is thrown away.
    delta_field: str | None
    # The sync point as read at the start of the run.
    state: dict[str, Any]
    # Written with each page that commits.
    on_progress: dict[str, Any] = field(default_factory=dict[str, Any])

    def held_attempts(self, url: str) -> int:
        """Runs that already failed on this page, when it is the page the pass is held at."""
        if self.state.get(self.held_field) == url:
            return int(self.state.get(self.held_attempts_field) or 0)
        return 0


def _held_page_attempts(sync_point: Optional[dict[str, Any]], url: str) -> int:
    """Runs that already failed on this page, when it is the page the checkpoint is held at."""
    if sync_point and sync_point.get("heldPage") == url:
        return int(sync_point.get("heldPageAttempts") or 0)
    return 0


def _decode_pem(value: object, marker: str) -> str | None:
    """A PEM block as uploaded, or base64 of one; None when it is neither."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if not text.startswith("-----BEGIN"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8").strip()
        except (binascii.Error, UnicodeDecodeError):
            return None
    return text.replace("\r\n", "\n") if f"-----BEGIN {marker}" in text else None


def has_certificate_fields(auth_config: dict[str, Any]) -> bool:
    return any(isinstance(auth_config.get(key), str) and auth_config[key].strip() for key in ("certificate", "privateKey"))


def certificate_problem(auth_config: dict[str, Any]) -> str | None:
    """What is wrong with the uploaded certificate and key, in words for the user; None when they can be used.

    Checked here rather than left to Microsoft's sign-in, whose errors don't say
    which of the two uploads is wrong.
    """
    certificate_text = _decode_pem(auth_config.get("certificate"), "CERTIFICATE")
    if certificate_text is None:
        return "The client certificate is missing or is not a PEM certificate (BEGIN CERTIFICATE)."
    key = auth_config.get("privateKey")
    key_text = _decode_pem(key, "PRIVATE KEY")
    if key_text is None:
        any_pem = _decode_pem(key, "") or ""
        if "BEGIN RSA PRIVATE KEY" in any_pem:
            return ("The private key is in RSA (PKCS#1) format; upload it as PKCS#8: "
                    "openssl pkcs8 -topk8 -nocrypt -in privatekey.key -out privatekey-pkcs8.key")
        if "BEGIN ENCRYPTED PRIVATE KEY" in any_pem:
            return "The private key is encrypted; upload it unencrypted (openssl pkcs8 -topk8 -nocrypt)."
        return "The private key is missing or is not a PKCS#8 PEM key (BEGIN PRIVATE KEY)."
    try:
        certificate = x509.load_pem_x509_certificate(certificate_text.encode("utf-8"))
    except ValueError:
        return "The client certificate could not be read; upload the PEM file of the certificate registered on the app."
    try:
        private_key = serialization.load_pem_private_key(key_text.encode("utf-8"), password=None)
    except (ValueError, TypeError):
        return "The private key could not be read as an unencrypted PKCS#8 key."
    spki = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if certificate.public_key().public_bytes(*spki) != private_key.public_key().public_bytes(*spki):
        return "The private key does not belong to the client certificate; upload the key the certificate was made with."
    now = datetime.now(timezone.utc)
    if certificate.not_valid_after_utc <= now:
        return f"The client certificate expired on {certificate.not_valid_after_utc:%Y-%m-%d}; upload a current one."
    if certificate.not_valid_before_utc > now:
        return f"The client certificate is not valid until {certificate.not_valid_before_utc:%Y-%m-%d}."
    return None


def certificate_pem(auth_config: dict[str, Any]) -> bytes | None:
    """Private key and certificate as one PEM, as Azure's CertificateCredential reads them; None when unusable."""
    if certificate_problem(auth_config) is not None:
        return None
    certificate = _decode_pem(auth_config.get("certificate"), "CERTIFICATE")
    private_key = _decode_pem(auth_config.get("privateKey"), "PRIVATE KEY")
    return f"{private_key}\n{certificate}\n".encode("utf-8")


# Microsoft Entra ID sign-in errors that are about the tenant or application id, not the secret or certificate.
_TENANT_ERRORS = ("AADSTS90002", "AADSTS900021", "AADSTS900023")
_APPLICATION_ERRORS = ("AADSTS700016",)


def _sign_in_rejection(kind: str, reason: str) -> str:
    if reason.startswith(_TENANT_ERRORS):
        return f"Microsoft Entra ID doesn't know this Directory (Tenant) ID: {reason}"
    if reason.startswith(_APPLICATION_ERRORS):
        return f"Microsoft Entra ID doesn't know this Application (Client) ID in the tenant: {reason}"
    return f"Microsoft Entra ID rejected the {kind}: {reason}"


def certificate_expiry(auth_config: dict[str, Any]) -> datetime | None:
    """When the uploaded client certificate expires; None when there is none that can be read."""
    certificate_text = _decode_pem(auth_config.get("certificate"), "CERTIFICATE")
    if certificate_text is None:
        return None
    try:
        return x509.load_pem_x509_certificate(certificate_text.encode("utf-8")).not_valid_after_utc
    except ValueError:
        return None


def onedrive_config_action_items(auth_config: dict[str, Any], now: datetime | None = None) -> list[ActionItem]:
    """What the admin has to do about saved OneDrive auth settings."""
    now = now or datetime.now(timezone.utc)
    if not has_certificate_fields(auth_config):
        if not auth_config.get("clientSecret"):
            return []
        return [ActionItem(
            code="certificate_sign_in",
            title="Switch to certificate sign-in",
            message=(
                "Upload a client certificate and its private key, and grant the app the Sites.Read.All "
                "application permission of the SharePoint API with admin consent."
            ),
            action="authenticate",
            docs_url=CERTIFICATE_SETUP_DOCS_URL,
        )]
    problem = certificate_problem(auth_config)
    if problem:
        return [ActionItem(
            code="certificate_unusable",
            severity="error",
            title="The client certificate can't be used",
            message=problem,
            action="authenticate",
            docs_url=CERTIFICATE_SETUP_DOCS_URL,
        )]
    expires = certificate_expiry(auth_config)
    if expires is not None and expires - now <= CERTIFICATE_EXPIRY_WARNING:
        return [ActionItem(
            code="certificate_expiring",
            title="The client certificate expires soon",
            message=(
                f"The client certificate expires on {expires:%Y-%m-%d}. Register a new one on the app "
                "(Certificates & secrets) and upload it with its private key before then."
            ),
            action="authenticate",
            docs_url=CERTIFICATE_SETUP_DOCS_URL,
        )]
    return []


def graph_credential(auth_config: dict[str, Any]) -> ClientSecretCredential | CertificateCredential:
    """How the connector signs in to Microsoft Graph.

    The client secret when there is one, so a connector set up before certificates
    were supported signs in exactly as it did; otherwise the certificate.
    """
    tenant_id = auth_config.get("tenantId")
    client_id = auth_config.get("clientId")
    client_secret = auth_config.get("clientSecret")
    if not (tenant_id and client_id):
        raise ValueError("Incomplete OneDrive credentials. Ensure tenantId and clientId are configured.")
    if client_secret:
        return ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    pem = certificate_pem(auth_config)
    if pem is not None:
        return CertificateCredential(tenant_id=tenant_id, client_id=client_id, certificate_data=pem)
    if has_certificate_fields(auth_config):
        raise ValueError(f"Incomplete OneDrive credentials. {certificate_problem(auth_config)}")
    raise ValueError(
        "Incomplete OneDrive credentials. Configure a client secret, or a client certificate and its PKCS#8 private key."
    )


@dataclass
class OneDriveCredentials:
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False


class OneDriveUserStatus(str, Enum):
    AVAILABLE = "available"
    NOT_PROVISIONED = "not_provisioned"
    LOCKED = "locked"
    UNKNOWN_ERROR = "unknown_error"


# Title/message templates for each non-AVAILABLE OneDriveUserStatus, used by
# _notify_onedrive_user_failures. Kept next to the enum so a new status is a
# visible reminder to add its notification copy here too.
ONEDRIVE_USER_FAILURE_NOTIFICATIONS: Dict[OneDriveUserStatus, Tuple[str, str]] = {
    OneDriveUserStatus.NOT_PROVISIONED: (
        "OneDrive not provisioned for some users",
        "OneDrive is not provisioned or licensed for these users, so they were not synced: {emails}.",
    ),
    OneDriveUserStatus.LOCKED: (
        "OneDrive blocked for some users",
        "OneDrive is archived, or blocked for these users, so they were not synced: {emails}.",
    ),
    OneDriveUserStatus.UNKNOWN_ERROR: (
        "Some OneDrive users could not be synced",
        "OneDrive availability could not be checked for these users: {emails}. "
        "This may be a transient error; they will be picked up by a later sync if the issue resolves.",
    ),
}


@ConnectorBuilder("OneDrive")\
    .in_group("Microsoft 365")\
    .with_description("Sync files and folders from OneDrive")\
    .with_categories(["Storage"])\
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
                name="clientSecret",
                display_name="Client Secret",
                placeholder="Enter your Azure AD Client Secret",
                description=(
                    "The client secret this connector was set up with. New OneDrive connectors sign in with "
                    "a client certificate instead; upload one below and clear the secret to switch."
                ),
                field_type="PASSWORD",
                required=False,
                is_secret=True,
                saved_only=True,
            ),
            AuthField(
                name="tenantId",
                display_name="Directory (Tenant) ID",
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
                name="certificate",
                display_name="Client Certificate",
                placeholder="Click to upload certificate file (.crt, .cer, or .pem)",
                description=(
                    "The certificate registered on the app (Certificates & secrets), with its private key below. "
                    "Also grant the app the Sites.Read.All application permission of the SharePoint API (not "
                    "Microsoft Graph)."
                ),
                field_type="FILE",
                required=True,
                required_unless_saved="clientSecret",
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
                description="The unencrypted PKCS#8 private key of the client certificate.",
                field_type="FILE",
                required=True,
                required_unless_saved="clientSecret",
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
                ],
                is_secret=True,
            ),
        ])
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ONEDRIVE.value))
        .add_documentation_link(DocumentationLink(
            "Azure AD App Registration Setup",
            "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/microsoft-365/one-drive',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter files and folders by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter files and folders by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(CommonFields.file_extension_filter())
        .add_filter_field(FilterField(
            name="shared",
            display_name="Index Shared Items",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of shared items",
            default_value=True
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class OneDriveConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str,
        scope: str, created_by: str) -> None:
        super().__init__(OneDriveApp(connector_id), logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by)

        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider
            )
        # Initialize sync points
        self.drive_delta_sync_point = _create_sync_point(SyncDataPointType.RECORDS)
        self.user_sync_point = _create_sync_point(SyncDataPointType.USERS)
        self.user_group_sync_point = _create_sync_point(SyncDataPointType.GROUPS)
        self.connector_id = connector_id

        # Batch processing configuration
        self.batch_size = 100
        self.max_concurrent_batches = 1 # set to 1 for now to avoid write write conflicts for small number of records
        self.onedrive_users_synced = 0

        self.rate_limiter = AsyncLimiter(50, 1)  # 50 requests per second
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        self.client: GraphServiceClient | None = None
        self.msgraph_client: MSGraphClient | None = None
        self.scope_reader: SharePointPermissionScopeReader | None = None
        self._user_drive_ids: dict[str, str] = {}
        # Per drive, items written without knowing whether they inherit, until saved to be read again.
        # Kept across a run reset: a reindex running beside the sync may still be filling them.
        self._items_without_known_inheritance: dict[str, set[str]] = {}
        # Per drive, UniqueId -> item id of items written as inheriting on a full read's answer.
        self._inheriting_from_full_read: dict[str, dict[str, str]] = {}
        self._reset_permission_scopes()

    async def init(self) -> bool:
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")
        if not config:
            self.logger.error("OneDrive config not found")
            return False

        self.config = {"credentials": config}
        auth_config = config.get("auth", {})
        try:
            self.credential = graph_credential(auth_config)
        except ValueError as credentials_error:
            self.logger.error(f"❌ {credentials_error}")
            raise

        # Pre-initialize the credential to establish HTTP session
        # This prevents "HTTP transport has already been closed" errors
        try:
            await self.credential.get_token("https://graph.microsoft.com/.default")
            self.logger.info("✅ Credential initialized and HTTP session established")
        except Exception as token_error:
            self.logger.error(f"❌ Failed to initialize credential: {token_error}")
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title="OneDrive authentication failed",
                message=(
                    "Unable to authenticate using current credentials. Please verify the connector authentication credentials."
                ),
                payload={
                    "connector_id": self.connector_id,
                    "connector_name": self.connector_name.value,
                    "connector_scope": self.scope,
                },
                recipient_user_ids=[self.created_by],
            )
            raise ValueError(f"Failed to initialize OneDrive credential: {sanitize_azure_error(token_error)}")

        self.client = GraphServiceClient(self.credential, scopes=["https://graph.microsoft.com/.default"])
        self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)
        self.scope_reader = self._build_scope_reader(auth_config)
        return True

    def _build_scope_reader(self, auth_config: dict[str, Any]) -> SharePointPermissionScopeReader | None:
        """A reader of SharePoint unique permissions, when a usable certificate is configured."""
        if not has_certificate_fields(auth_config):
            self.logger.info(
                "No client certificate configured: every OneDrive item keeps the access list OneDrive reports "
                "for it, with no inheritance from its folder. Upload a certificate and grant the SharePoint "
                "Sites.Read.All application permission to have items inherit their folder's access."
            )
            return None
        pem = certificate_pem(auth_config)
        if pem is None:
            self.logger.warning(
                f"⚠️ {certificate_problem(auth_config)} Every OneDrive item keeps the access list OneDrive "
                "reports for it, with no inheritance from its folder."
            )
            return None
        tenant_id, client_id = auth_config["tenantId"], auth_config["clientId"]

        async def sharepoint_token(resource: str) -> SharePointToken:
            # A fresh credential per token: one kept across runs days apart can find its transport closed.
            async with CertificateCredential(tenant_id=tenant_id, client_id=client_id, certificate_data=pem) as credential:
                token = await credential.get_token(f"{resource}/.default")
            return SharePointToken(token=token.token, expires_on=token.expires_on)

        return SharePointPermissionScopeReader(sharepoint_token, self.rate_limiter)

    def _reset_permission_scopes(self) -> None:
        """Forget what SharePoint said about unique permissions, at the start of a run."""
        # Per drive, for this run; None when it couldn't be read.
        self._drive_scopes: dict[str, ListUniquePermissions | None] = {}
        # Drives that had a record written this run without knowing whether it inherits.
        self._item_acl_drives: set[str] = set()
        # Set once SharePoint refuses the app itself; no list is asked again this run.
        self._sharepoint_refusal: str | None = None
        self._unreadable_drives_logged: set[str] = set()
        # What SharePoint said to the certificate this run; None when it wasn't asked.
        self._sharepoint_health: str | None = None

    async def _get_drive_scope(self, drive_id: str) -> ListUniquePermissions | None:
        """Which items of a drive carry their own permissions; None when that can't be read."""
        if drive_id in self._drive_scopes:
            return self._drive_scopes[drive_id]
        if self.scope_reader is None or self._sharepoint_refusal is not None or self.client is None:
            return None
        scope = None
        try:
            query = DriveItemRequestBuilder.DriveItemRequestBuilderGetQueryParameters(select=["id", "sharePointIds"])
            async with self.rate_limiter:
                drive = await self.client.drives.by_drive_id(drive_id).get(
                    request_configuration=RequestConfiguration(query_parameters=query)
                )
            sharepoint_ids = getattr(drive, 'share_point_ids', None)
            list_id = getattr(sharepoint_ids, 'list_id', None)
            web_url = getattr(sharepoint_ids, 'site_url', None)
            if not (isinstance(list_id, str) and list_id and isinstance(web_url, str) and web_url):
                raise ValueError("the drive has no SharePoint list id or site URL")
            web_url = web_url.rstrip('/')
            list_url = f"{web_url}/_api/web/lists(guid'{list_id}')"
            item_count = await self.scope_reader.read_item_count(list_url)
            scope = ListUniquePermissions(self.scope_reader, web_url, list_url, item_count)
            self._sharepoint_health = SHAREPOINT_HEALTH_OK
        except SharePointAccessError as e:
            await self._note_sharepoint_refusal(e)
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not read which items of drive {drive_id} inherit their access; this run, its items "
                f"keep the access list OneDrive reports for each: {e}"
            )
            if isinstance(e, SharePointRestError) and e.status_code == HTTPStatus.FORBIDDEN:
                if self._sharepoint_health != SHAREPOINT_HEALTH_OK:
                    self._sharepoint_health = SHAREPOINT_HEALTH_FORBIDDEN
                await self._notify_sharepoint_permission_missing()
        self._drive_scopes[drive_id] = scope
        return scope

    async def _collect_action_items(self) -> None:
        """Set what needs the admin's attention after this run, which the runner stores with its outcome.

        What SharePoint said is kept from run to run: a run that didn't ask it leaves the card as it was.
        """
        try:
            auth_config = getattr(self, "config", {}).get("credentials", {}).get("auth", {})
            items = onedrive_config_action_items(auth_config)
            if self.scope_reader is None:
                self.last_action_items = items
                return
            key = generate_record_sync_point_key("permissionNotices", "connector", "sharepoint")
            health = self._sharepoint_health
            if health is None:
                health = (await self.drive_delta_sync_point.read_sync_point(key) or {}).get("health")
            else:
                await self.drive_delta_sync_point.update_sync_point(key, {"health": health})
            if health == SHAREPOINT_HEALTH_REFUSED:
                items.append(ActionItem(
                    code="sharepoint_refused",
                    severity="error",
                    title="SharePoint refused the app",
                    message=(
                        "Grant the app the Sites.Read.All application permission of the SharePoint API with admin "
                        "consent, and check that the certificate uploaded here is registered on the app and has "
                        "not expired."
                    ),
                    action="authenticate",
                    docs_url=CERTIFICATE_SETUP_DOCS_URL,
                ))
            elif health == SHAREPOINT_HEALTH_FORBIDDEN:
                items.append(ActionItem(
                    code="sharepoint_permission_missing",
                    severity="error",
                    title="Grant the SharePoint permission",
                    message=(
                        "Grant the app the Sites.Read.All application permission of the SharePoint API (not "
                        "Microsoft Graph) with admin consent."
                    ),
                    docs_url=CERTIFICATE_SETUP_DOCS_URL,
                ))
            self.last_action_items = items
        except Exception as e:
            self.logger.debug(f"Could not work out the connector's action items: {e}")

    async def _remind_about_certificate(self) -> None:
        """Remind the admin of a connector without a certificate what one would add, at most once a week.

        From any run, scheduled ones included, so the admin hears of it without opening the
        connector; the time it was last sent is kept in a sync point, so restarts don't repeat it.
        """
        key = generate_record_sync_point_key("permissionNotices", "connector", "certificate")
        try:
            auth_config = getattr(self, "config", {}).get("credentials", {}).get("auth", {})
            if self.scope_reader is not None or has_certificate_fields(auth_config):
                return
            last_sent = (await self.drive_delta_sync_point.read_sync_point(key) or {}).get("lastSentAt")
            now = get_epoch_timestamp_in_ms()
            if isinstance(last_sent, (int, float)) and now - last_sent < CERTIFICATE_REMINDER_INTERVAL_MS:
                return
            outcome = await self.notify_and_wait(
                type=NotificationType.CONNECTOR_INFO,
                severity=NotificationSeverity.INFO,
                title="OneDrive: switch to certificate sign-in",
                message=(
                    "Recommended: upload a client certificate and its private key on the connector, and grant the "
                    "app the Sites.Read.All application permission of the SharePoint API. The connector page links "
                    "to the steps."
                ),
                payload={
                    "connector_id": self.connector_id,
                    "connector_name": self.connector_name.value,
                    "connector_scope": self.scope,
                },
                recipient_user_ids=[self.created_by],
            )
            if outcome is NotificationOutcome.SENT:
                await self.drive_delta_sync_point.update_sync_point(key, {"lastSentAt": now})
        except Exception as e:
            self.logger.debug(f"The certificate reminder was not sent: {e}")

    async def _note_sharepoint_refusal(self, error: Exception) -> None:
        if self._sharepoint_refusal is not None:
            return
        self._sharepoint_refusal = str(error)
        self._sharepoint_health = SHAREPOINT_HEALTH_REFUSED
        self.logger.warning(
            f"⚠️ SharePoint refused the app (no Sites.Read.All grant in its token, or a certificate it doesn't "
            f"accept), so for the rest of this run OneDrive items "
            f"keep the access list OneDrive reports for each, with no inheritance from their folder: {error}"
        )
        await self.notify(
            type=NotificationType.CONNECTOR_WARNING,
            severity=NotificationSeverity.WARNING,
            title="OneDrive can't read inherited permissions",
            message=(
                "SharePoint refused the app. Grant it the Sites.Read.All application permission of the SharePoint "
                "API with admin consent, and check that the certificate uploaded to the connector is registered on "
                "the app and has not expired."
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
            },
            recipient_user_ids=[self.created_by],
        )

    async def _notify_sharepoint_permission_missing(self) -> None:
        await self.notify(
            type=NotificationType.CONNECTOR_WARNING,
            severity=NotificationSeverity.WARNING,
            title="OneDrive can't read inherited permissions",
            message=(
                "SharePoint denied the app access to OneDrive sites. Grant the app the Sites.Read.All application "
                "permission of the SharePoint API (not Microsoft Graph) with admin consent."
            ),
            payload={
                "connector_id": self.connector_id,
                "connector_name": self.connector_name.value,
                "connector_scope": self.scope,
            },
            recipient_user_ids=[self.created_by],
        )

    async def _inheritance(
        self, drive_id: str | None, unique_id: str | None, *, is_root: bool, item_id: str | None
    ) -> bool | None:
        """Whether SharePoint says an item takes its parent folder's access; None when that can't be told now.

        The root, and an item with no SharePoint id, never inherit: there is nothing to ask.
        """
        if is_root or not drive_id or unique_id is None:
            return False
        scope = await self._get_drive_scope(drive_id)
        if scope is None:
            return None
        try:
            inherits = not await scope.has_unique_permissions(unique_id)
        except SharePointAccessError as e:
            await self._note_sharepoint_refusal(e)
            self._drive_scopes[drive_id] = None
            return None
        except Exception as e:
            if not scope.unreadable:
                # One item SharePoint can't answer for (deleted since, say) keeps its own access list.
                self.logger.warning(f"⚠️ Could not read whether item {unique_id} of drive {drive_id} inherits its access: {e}")
            elif drive_id not in self._unreadable_drives_logged:
                self._unreadable_drives_logged.add(drive_id)
                self.logger.warning(
                    f"⚠️ Could not read which items of drive {drive_id} inherit their access; for the rest of "
                    f"this run, its items keep the access list OneDrive reports for each: {e}"
                )
            return None
        if inherits and item_id is not None and scope.read_in_full:
            self._inheriting_from_full_read.setdefault(drive_id, {})[unique_id] = item_id
        return inherits

    async def _inherits(
        self, drive_id: str | None, unique_id: str | None, *, is_root: bool, item_id: str | None
    ) -> bool:
        """``_inheritance``, with an unknown answer taken as "keeps its own access list" and queued to be asked again."""
        inherits = await self._inheritance(drive_id, unique_id, is_root=is_root, item_id=item_id)
        if inherits is None:
            self._queue_unknown_inheritance(drive_id, item_id)
            return False
        return inherits

    def _queue_unknown_inheritance(self, drive_id: str | None, item_id: str | None) -> None:
        if not (drive_id and item_id):
            return
        unknown = self._items_without_known_inheritance.setdefault(drive_id, set())
        # One past the limit is enough to tell the drive needs a full rewrite instead.
        if len(unknown) <= MAX_RECHECK_ITEMS:
            unknown.add(item_id)

    async def _inherits_parent_access(self, item: DriveItem) -> bool | None:
        """``_inheritance`` for a drive item: None when SharePoint couldn't say (it is then queued to ask again)."""
        parent = item.parent_reference
        drive_id = parent.drive_id if parent else None
        unique_id = drive_item_unique_id(item)
        is_root = getattr(item, 'root', None) is not None or not (parent and parent.id)
        inherits = await self._inheritance(drive_id, unique_id, is_root=is_root, item_id=item.id)
        if inherits is None:
            self._queue_unknown_inheritance(drive_id, item.id)
        return inherits

    async def _process_delta_item(
        self,
        item: DriveItem,
        *,
        hold_page_on_incomplete_walk: bool = True,
        unresolved_access: list[str] | None = None,
    ) -> Optional[RecordUpdate]:
        """
        Process a single delta item and detect changes.

        Returns:
            RecordUpdate object containing the record and change information
        """
        try:
            # Before the filters: OneDrive for Business sends deleted items without a
            # name or dates, which the filters would read as "doesn't match" and skip.
            if hasattr(item, 'deleted') and item.deleted is not None:
                self.logger.info(f"Item {item.id} has been deleted")
                return RecordUpdate(
                    record=None,
                    external_record_id=item.id,
                    is_new=False,
                    is_updated=False,
                    is_deleted=True,
                    metadata_changed=False,
                    content_changed=False,
                    permissions_changed=False
                )

            if not self._pass_date_filters(item):
                self.logger.debug(f"Skipping item {item.name} (ID: {item.id}) due to date filters.")
                return None

            if not self._pass_extension_filter(item):
                self.logger.debug(f"Skipping item {item.name} (ID: {item.id}) due to extention filters.")
                return None

            # Get existing record if any
            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, item.id
            )
            existing_file_record = None
            if existing_record:
                try:
                    existing_file_record = await self.data_entities_processor.get_file_record_by_id(existing_record.id)
                except GraphQueryError as read_error:
                    # Without the stored hashes a content change can't be seen, so read the page again.
                    if hold_page_on_incomplete_walk:
                        raise DrivePageIncompleteError(
                            f"stored file record of item {item.id} could not be read"
                        ) from read_error
                    raise


            # Detect changes
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            permissions_changed = False
            is_shared_folder = False
            is_shared_folder = (
                item.folder is not None and  # It's a folder
                hasattr(item, 'shared') and item.shared is not None  # Has shared property
            )

            if existing_record:
                # Check for metadata changes
                if (existing_record.external_revision_id != item.e_tag or
                    existing_record.record_name != item.name or
                    existing_record.updated_at != int(item.last_modified_date_time.timestamp() * 1000)):
                    metadata_changed = True
                    is_updated = True


                if existing_file_record:
                    # Check for content changes (different hash)
                    if item.file and hasattr(item.file, 'hashes'):
                        current_hash = item.file.hashes.quick_xor_hash if item.file.hashes else None
                        if existing_file_record.quick_xor_hash != current_hash:
                            content_changed = True
                            is_updated = True

            # Create/update file record
            signed_url = None
            if item.file is not None:
                signed_url = await self.msgraph_client.get_signed_url(
                    item.parent_reference.drive_id,
                    item.id,
                )

            is_shared = hasattr(item, 'shared') and item.shared is not None

            file_record = FileRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=item.name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.DRIVE,
                parent_record_type=RecordType.FILE,
                external_record_id=item.id,
                external_revision_id=item.e_tag,
                version=0 if is_new else existing_record.version + 1,
                origin=OriginTypes.CONNECTOR,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                created_at=int(item.created_date_time.timestamp() * 1000),
                updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                source_created_at=int(item.created_date_time.timestamp() * 1000),
                source_updated_at=int(item.last_modified_date_time.timestamp() * 1000),
                weburl=item.web_url,
                signed_url=signed_url,
                is_shared=is_shared,
                mime_type=item.file.mime_type if item.file else MimeTypes.FOLDER.value,
                parent_external_record_id=item.parent_reference.id if item.parent_reference else None,
                external_record_group_id=item.parent_reference.drive_id if item.parent_reference else None,
                size_in_bytes=item.size,
                is_file=item.folder is None,
                extension=item.name.split(".")[-1] if "." in item.name else None,
                path=item.parent_reference.path if item.parent_reference else None,
                etag=item.e_tag,
                ctag=item.c_tag,
                quick_xor_hash=item.file.hashes.quick_xor_hash if item.file and item.file.hashes else None,
                crc32_hash=item.file.hashes.crc32_hash if item.file and item.file.hashes else None,
                sha1_hash=item.file.hashes.sha1_hash if item.file and item.file.hashes else None,
                sha256_hash=item.file.hashes.sha256_hash if item.file and item.file.hashes else None,
            )
            if file_record.is_file and file_record.extension is None:
                return None

            permission_failure = None
            # An item that inherits has no grants of its own: Graph would list its parent's.
            inheritance = await self._inherits_parent_access(item)
            inherits = inheritance is True
            permission_result = []
            if not inherits:
                try:
                    permission_result = await self.msgraph_client.get_file_permission(
                        item.parent_reference.drive_id if item.parent_reference else None,
                        item.id,
                        raise_on_error=True,
                    )
                except Exception as read_error:
                    permission_failure = _read_failure(f"permissions of item {item.id}", read_error)
                    self.logger.warning(str(permission_failure))

            if permission_failure and existing_record is None:
                # A new file saved with no access stays that way until it next changes.
                if not permission_failure.permanent and hold_page_on_incomplete_walk:
                    raise DrivePageIncompleteError(f"access of new item {item.id} could not be read")
                if unresolved_access is not None:
                    unresolved_access.append(item.id)

            new_permissions = await self._convert_to_permissions(permission_result)

            if permission_failure is None:
                # Graph's list is the item's whole effective access and does not say which
                # grants were inherited. Only SharePoint's unique-permission flag does:
                # matching the parent is not inheritance, a later parent share would leak in.
                file_record.inherit_permissions = inherits
                file_record.rewrite_permissions = True
                permissions_changed = True
                if existing_record:
                    is_updated = True
            elif inheritance is False and self.scope_reader is not None:
                # SharePoint says the item no longer takes its folder's access, often to narrow it.
                # A stored inherit edge would keep the folder's audience; without it the stored
                # grants (none, if it inherited) stand until its own list can be read.
                file_record.inherit_permissions = False
                if existing_record:
                    metadata_changed = True
                    is_updated = True
                    if unresolved_access is not None and item.id:
                        unresolved_access.append(item.id)
            else:
                file_record.inherit_permissions = await self.data_entities_processor.inheritance_when_unreadable(
                    CollectionNames.RECORDS.value, existing_record.id if existing_record else None
                )

            if existing_record and existing_record.is_shared != is_shared_folder:
                metadata_changed = True
                is_updated = True
                if item.folder is not None:
                    # The walk runs only when the shared flag flips; saving the folder now
                    # would stop it from ever running again for the files it missed.
                    if permission_failure and not permission_failure.permanent and hold_page_on_incomplete_walk:
                        raise DrivePageIncompleteError(f"access of folder {item.id} could not be read")
                    walk = await self._update_folder_children_permissions(
                        drive_id=item.parent_reference.drive_id,
                        folder_id=item.id
                    )
                    if walk.temporary and hold_page_on_incomplete_walk:
                        raise DrivePageIncompleteError(f"access of some items inside folder {item.id} could not be read")
                    if unresolved_access is not None:
                        unresolved_access.extend(walk.hidden_items)
                    if permission_failure:
                        unresolved = [item.id, *walk.unread_items]
                        for unlisted_folder in walk.unlisted_folders:
                            unresolved.extend(r.external_record_id for r in await self._stored_descendants(unlisted_folder))
                        if unresolved_access is not None:
                            unresolved_access.extend(unresolved)
                        self.logger.error(
                            f"❌ The access of folder {item.id} could not be read; {unresolved} keep their stored access "
                            "and are read again on later runs until it can be read."
                        )
                    elif not walk.complete:
                        await self._settle_unread_children(walk, item.id, new_permissions, unshared=existing_record.is_shared)


            return RecordUpdate(
                record=file_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=permissions_changed,
                # old_permissions=existing_record.permissions if existing_record else None,
                new_permissions=new_permissions
            )

        except DrivePageIncompleteError:
            raise
        except Exception as ex:
            self.logger.error(f"❌ Error processing delta item {item.id}: {ex}", exc_info=True)
            # Whatever access it has stored is read again, so a rewrite of the drive can't skip it.
            self._queue_unknown_inheritance(item.parent_reference.drive_id if item.parent_reference else None, item.id)
            return None

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
                            type=map_msgraph_roles_to_permission_type(perm.roles),
                            entity_type=EntityType.USER
                        ))
                    if hasattr(perm.granted_to_v2, 'group') and perm.granted_to_v2.group:
                        group = perm.granted_to_v2.group
                        permissions.append(Permission(
                            external_id=group.id,
                            email=group.additional_data.get("email", None) if hasattr(group, 'additional_data') else None,
                            type=map_msgraph_roles_to_permission_type(perm.roles),
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
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.GROUP
                            ))
                        elif hasattr(identity, 'user') and identity.user:
                            user = identity.user
                            permissions.append(Permission(
                                external_id=user.id,
                                email=user.additional_data.get("email", None) if hasattr(user, 'additional_data') else None,
                                type=map_msgraph_roles_to_permission_type(perm.roles),
                                entity_type=EntityType.USER
                            ))

                # A link grants only the identities Graph lists on it. An organization or
                # anonymous link opens the item only to the people who redeem it, and Graph
                # does not list them, so they get no grant here: under-shared, never over-shared.

            except Exception as e:
                self.logger.error(f"❌ Error converting permission: {e}", exc_info=True)
                continue

        return permissions

    def _permissions_equal(self, old_perms: List[Permission], new_perms: List[Permission]) -> bool:
        """
        Compare two lists of permissions to detect changes.
        """
        if len(old_perms) != len(new_perms):
            return False

        # Create sets of permission tuples for comparison
        old_set = {(p.external_id, p.email, p.type, p.entity_type) for p in old_perms}
        new_set = {(p.external_id, p.email, p.type, p.entity_type) for p in new_perms}

        return old_set == new_set

    def _pass_date_filters(self, item: DriveItem) -> bool:
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

    async def _process_delta_items_generator(
        self,
        delta_items: List[dict],
        *,
        hold_page_on_incomplete_walk: bool = True,
        unresolved_access: list[str] | None = None,
    ) -> AsyncGenerator[Tuple[FileRecord, List[Permission], RecordUpdate], None]:
        """
        Process delta items and yield records with their permissions.
        This allows non-blocking processing of large datasets.

        Yields:
            Tuple of (FileRecord, List[Permission], RecordUpdate)
        """
        for item in delta_items:
            try:
                record_update = await self._process_delta_item(
                    item, hold_page_on_incomplete_walk=hold_page_on_incomplete_walk, unresolved_access=unresolved_access
                )

                if record_update:
                    if record_update.is_deleted:
                        # For deleted items, yield with empty permissions
                        yield (None, [], record_update)
                    elif record_update.record:
                        files_disabled = not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
                        shared_disabled = record_update.record.is_shared and not self.indexing_filters.is_enabled(IndexingFilterKey.SHARED, default=True)

                        if files_disabled or shared_disabled:
                            record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value


                        yield (record_update.record, record_update.new_permissions or [], record_update)

                # Allow other tasks to run
                await asyncio.sleep(0)

            except DrivePageIncompleteError:
                raise
            except Exception as e:
                self.logger.error(f"❌ Error processing item in generator: {e}", exc_info=True)
                continue

    async def _update_folder_children_permissions(
        self,
        drive_id: str,
        folder_id: str,
        inherited_permissions: Optional[List[Permission]] = None,
        walk: Optional[_FolderWalk] = None,
    ) -> _FolderWalk:
        """
        Recursively update permissions for all children of a folder.

        Args:
            drive_id: The drive ID
            folder_id: The folder ID whose children need permission updates
            inherited_permissions: The permissions to apply to children
            walk: Collects the items that could not be updated, across the recursion

        Returns:
            The items whose access could not be read or saved, and the folders whose
            children could not be listed.
        """
        walk = walk if walk is not None else _FolderWalk()
        try:
            children = await self.msgraph_client.list_folder_children(drive_id, folder_id, raise_on_error=True)
        except Exception as ex:
            failure = _read_failure(f"children of folder {folder_id}", ex)
            self.logger.error(f"Error updating folder children permissions: {failure}", exc_info=True)
            walk.unlisted_folders.append(folder_id)
            walk.temporary = walk.temporary or not failure.permanent
            return walk

        for child in children:
            try:
                child_inheritance = await self._inherits_parent_access(child)
                child_inherits = child_inheritance is True
                child_permissions = []
                if not child_inherits:
                    try:
                        child_permissions = await self.msgraph_client.get_file_permission(
                            drive_id,
                            child.id,
                            raise_on_error=True,
                        )
                    except Exception as read_error:
                        failure = _read_failure(f"permissions of child item {child.id}", read_error)
                        self.logger.warning(str(failure))
                        walk.unread_items.append(child.id)
                        walk.temporary = walk.temporary or not failure.permanent
                        child_permissions = None

                existing_child_record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, child.id
                )

                if child_permissions is None and child_inheritance is False and self.scope_reader and existing_child_record:
                    # A stored inherit edge on an item SharePoint calls unique would pass the folder's
                    # new sharing on to it; dropped, its stored grants alone stand until it can be read.
                    existing_child_record.inherit_permissions = False
                    await self.data_entities_processor.on_record_metadata_update(existing_child_record)
                    if child.id:
                        walk.hidden_items.append(child.id)

                if child_permissions is not None and existing_child_record:
                    converted_permissions = await self._convert_to_permissions(child_permissions)
                    existing_child_record.inherit_permissions = child_inherits
                    existing_child_record.rewrite_permissions = True
                    await self.data_entities_processor.on_updated_record_permissions(
                        record=existing_child_record,
                        permissions=converted_permissions
                    )
                    self.logger.info(f"Updated permissions for child item {child.id}")

                # If this child is also a folder, recurse
                if child.folder is not None:
                    await self._update_folder_children_permissions(drive_id=drive_id, folder_id=child.id, walk=walk)

            except Exception as child_ex:
                self.logger.error(f"Error updating child {child.id}: {child_ex}", exc_info=True)
                if child.id not in walk.unread_items:
                    walk.unread_items.append(child.id)
                walk.temporary = True

        return walk

    async def _settle_unread_children(
        self, walk: _FolderWalk, folder_id: str, folder_permissions: list[Permission], *, unshared: bool
    ) -> None:
        """Decide what items whose access couldn't be read keep once the folder is saved.

        After a share, their stored access is kept: it can only be narrower than the
        folder's. After an unshare, keeping it could leave the old share in place, and
        stored grants don't record whether they came from the folder, so those items
        are given the folder's current access instead. That drops any sharing of
        their own too, until the item changes or is reindexed.
        """
        records: list[Record] = []
        for external_id in walk.unread_items:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, external_id)
            if record:
                records.append(record)
        for unlisted_folder in walk.unlisted_folders:
            records.extend(await self._stored_descendants(unlisted_folder))
        # Whether these inherit wasn't read either; asked again, so none keeps an access it shouldn't.
        for record in records:
            self._queue_unknown_inheritance(record.external_record_group_id, record.external_record_id)

        if not unshared:
            self.logger.error(
                f"❌ Folder {folder_id} was shared, but the access of {walk.unread_items + walk.unlisted_folders} "
                "could not be read; they keep their stored access. Reindex them to pick up the new sharing."
            )
            return

        for record in records:
            record.inherit_permissions = False
            record.rewrite_permissions = True
            await self.data_entities_processor.on_updated_record_permissions(record, list(folder_permissions))
        self.logger.error(
            f"❌ Folder {folder_id} was unshared, but the access of {[r.external_record_id for r in records]} could not be "
            "read; they now have the folder's current access, which also drops any sharing of their own. "
            "Reindex them to restore that."
        )

    async def _stored_descendants(self, folder_id: str) -> list[Record]:
        # get_records_by_parent returns base Records, which carry no is_file, so every
        # child is looked up in turn; a file simply has no children.
        found: list[Record] = []
        seen = {folder_id}
        pending = [folder_id]
        while pending:
            children = await self.data_entities_processor.get_records_by_parent(self.connector_id, pending.pop())
            for child in children:
                if child.external_record_id in seen:
                    continue
                seen.add(child.external_record_id)
                found.append(child)
                pending.append(child.external_record_id)
        return found

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (new, updated, deleted).
        Publishes appropriate events based on the type of change.
        """
        try:
            if record_update.is_deleted:
                dbRecord = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id, record_update.external_record_id
                )
                if dbRecord:
                    await self.data_entities_processor.on_record_deleted(
                        record_id=dbRecord.id
                    )

            elif record_update.is_new:
                # Handle new record - this will be done through the normal flow
                self.logger.info(f"New record detected: {record_update.record.record_name}")

            elif record_update.is_updated:
                # Handle updates based on what changed
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
            self.logger.error(f"❌ Error handling record updates: {e}", exc_info=True)
            if record_update.record is not None:
                self._queue_unknown_inheritance(
                    record_update.record.external_record_group_id, record_update.record.external_record_id
                )

    async def _sync_user_groups(self) -> None:
        """
        Unified user group synchronization.
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
            if sync_point and sync_point.get('pendingGroupDeletes'):
                await self._retry_pending_group_deletes(sync_point['pendingGroupDeletes'], sync_point_key)

            # A run stopped mid-delta leaves only nextLink; resuming there keeps the
            # group deletions on the remaining pages, which a full sync would not see.
            delta_link = (sync_point.get('deltaLink') or sync_point.get('nextLink')) if sync_point else None

            if delta_link is None:
                self.logger.info("No sync point found, performing initial full sync...")

                # IMPORTANT: Get delta link BEFORE full sync to avoid missing changes
                # that occur during the sync
                delta_link = await self._get_initial_delta_link()

                all_groups_read = await self._perform_initial_full_sync()

                # The link is saved even when some groups could not be read: only a delta
                # from before the full sync reports groups deleted since. The marker makes
                # the next run read every group again before applying it.
                if delta_link:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key,
                        {
                            "nextLink": None,
                            "deltaLink": delta_link,
                            "fullSyncIncomplete": not all_groups_read,
                            "fullSyncAttempts": 0 if all_groups_read else 1,
                        }
                    )
                    self.logger.info("Initial sync completed and delta link saved for future syncs")
                else:
                    self.logger.warning("Initial sync completed but no delta link was obtained")
                if not all_groups_read:
                    self.logger.warning("Some groups could not be read; the next run will read every group again")
            elif sync_point.get('fullSyncIncomplete'):
                self.logger.info("Previous full group sync was incomplete, reading every group again...")
                all_groups_read = await self._perform_initial_full_sync()
                attempts = int(sync_point.get('fullSyncAttempts') or 1) + 1
                if all_groups_read:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": False, "fullSyncAttempts": 0}
                    )
                elif attempts >= MAX_PAGE_ATTEMPTS:
                    self.logger.error(
                        f"❌ Some groups still could not be read after {attempts} full group syncs; "
                        "they keep their stored members and are read again when they next change"
                    )
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": False, "fullSyncAttempts": 0}
                    )
                else:
                    await self.user_group_sync_point.update_sync_point(sync_point_key, {"fullSyncAttempts": attempts})
                # Run after the marker is settled: a group the delta gives up on sets it
                # again, and that must not be cleared by this full sync's outcome.
                await self._perform_delta_sync(delta_link, sync_point_key)
            else:
                self.logger.info("Sync point found, performing incremental delta sync...")
                await self._perform_delta_sync(delta_link, sync_point_key)

        except ODataError as e:
            self.logger.error(f"❌ Error in user group sync: {e}", exc_info=True)
            error_code = (e.error.code or "") if e.error else ""
            if error_code == "Authorization_RequestDenied" or e.response_status_code == 403:
                await self.notify(
                    type=NotificationType.CONNECTOR_AUTH_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title="User group sync failed",
                    message="Please check your authentication credentials are correct and has Group.Read.All API permission.",
                    payload={
                        "connector_id": self.connector_id,
                        "connector_name": self.connector_name.value,
                        "connector_scope": self.scope,
                    },
                    recipient_user_ids=[self.created_by],
                )
            raise
        except Exception as e:
            self.logger.error(f"❌ Error in user group sync: {e}", exc_info=True)
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


    async def _perform_initial_full_sync(self) -> bool:
        """
        Performs initial full sync using the standard /groups API.
        Gets current state of all groups and their members.

        Returns:
            True if every group and its members were read.
        """
        self.logger.info("Starting initial full user group synchronization")

        groups = await self.msgraph_client.get_all_user_groups()

        # Process all groups concurrently using asyncio.gather
        results = await asyncio.gather(
            *[self._process_single_group(group) for group in groups],
            return_exceptions=True
        )

        group_with_members = []
        all_groups_read = True
        for result in results:
            if isinstance(result, GraphReadFailedError) and result.permanent:
                self.logger.warning(f"Skipping group: {result}; it keeps its stored members")
            elif isinstance(result, Exception):
                self.logger.error(f"❌ Error processing group: {result}", exc_info=True)
                all_groups_read = False
            elif result is None:
                all_groups_read = False
            else:
                group_with_members.append(result)

        if group_with_members:
            await self.data_entities_processor.on_new_user_groups(group_with_members)

        self.logger.info(f"Initial full sync completed: processed {len(groups)} user groups")
        return all_groups_read

    async def _process_single_group(self, group) -> Optional[Tuple[AppUserGroup, List[AppUser]]]:
        """
        Processes a single group and returns a tuple of (user_group, app_users).
        Returns None if processing fails. A permanent member-read failure is raised
        instead, so the full sync can skip the group without counting it as incomplete.
        """
        try:
            app_users = await self._collect_group_users(group)
            return (self._to_app_user_group(group), app_users)

        except GraphReadFailedError as e:
            if e.permanent:
                raise
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error processing group {group.display_name}: {e}", exc_info=True)
            return None

    def _to_app_user_group(self, group: Group) -> AppUserGroup:
        return AppUserGroup(
            source_user_group_id=group.id,
            app_name=self.connector_name,
            connector_id=self.connector_id,
            name=group.display_name,
            description=group.description,
            source_created_at=group.created_date_time.timestamp() if group.created_date_time else get_epoch_timestamp_in_ms(),
        )

    async def _collect_group_users(self, group: Group) -> list[AppUser]:
        """
        Users in a group, including those of nested groups (one level deep).

        Supported member types:
        - User: Added directly
        - Group (nested): Fetch its users and add them (only one level deep)
        - Device, Service Principal, Org Contact: Ignored

        Raises GraphReadFailedError when the group's members, or a nested group's,
        can't be read: saving a partial list would replace the group's stored members.
        """
        try:
            members = await self.msgraph_client.get_group_members(group.id, raise_on_error=True)
        except Exception as e:
            raise _read_failure(f"members of group {group.id}", e) from e

        app_users = []
        for member in members:
            odata_type = getattr(member, 'odata_type', None) or (member.additional_data or {}).get('@odata.type', '')

            if '#microsoft.graph.user' in odata_type:
                app_user = self._create_app_user_from_member(member)
                if app_user:
                    app_users.append(app_user)
            elif '#microsoft.graph.group' in odata_type:
                app_users.extend(await self._get_users_from_nested_group(member))
            else:
                self.logger.debug(f"Skipping member type '{odata_type}' for member {member.id}")

        return app_users


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

            unapplied: list[str] = []
            failed_deletes: list[str] = []
            for group in groups:
                # Handle group DELETION
                if hasattr(group, 'additional_data') and group.additional_data and '@removed' in group.additional_data:
                    self.logger.info(f"[DELTA] 🗑️ REMOVE Group: {group.id}")
                    success = await self.handle_delete_group(group.id)
                    if not success:
                        self.logger.error(f"❌ Error handling group delete for {group.id}")
                        unapplied.append(group.id)
                        failed_deletes.append(group.id)
                    continue

                # Handle ADD/UPDATE
                self.logger.info(f"[DELTA] ✅ ADD/UPDATE Group: {getattr(group, 'display_name', 'N/A')} ({group.id})")
                saved = await self.handle_group_create(group)
                if saved is False:
                    self.logger.error(f"❌ Error handling group create for {group.id}")
                    unapplied.append(group.id)
                if saved:
                    # The saved list replaced the group's stored members and already leaves
                    # out anyone removed here, so there is nothing left to remove.
                    continue

                # Applied even when the member read above failed: a removal listed here needs
                # no other read, and is lost for good once the delta link moves past this page.
                member_changes = (group.additional_data or {}).get('members@delta', [])

                if member_changes:
                    self.logger.info(f"    -> [DELTA] 👥 Processing {len(member_changes)} member changes for group: {group.id}")

                for member_change in member_changes:
                    if not await self._process_member_change(group.id, member_change) and group.id not in unapplied:
                        unapplied.append(group.id)

            # Graph won't send this page again once the link moves past it, so a group
            # that couldn't be applied would lose its changes for good. A page is held
            # for a bounded number of runs so one broken group can't freeze group sync.
            if unapplied:
                stored = await self.user_group_sync_point.read_sync_point(sync_point_key)
                attempts = _held_page_attempts(stored, url) + 1
                if attempts < MAX_PAGE_ATTEMPTS:
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"heldPage": url, "heldPageAttempts": attempts}
                    )
                    self.logger.warning(
                        f"Groups {unapplied} could not be applied; this page will be read again next run "
                        f"(attempt {attempts} of {MAX_PAGE_ATTEMPTS})"
                    )
                    break
                self.logger.error(
                    f"❌ Groups {unapplied} still could not be applied after {attempts} attempts; "
                    "skipping them so group sync can continue. They keep their stored members, less any "
                    "removals listed on this page"
                )
                # The page moves on without these groups' member changes; a full group
                # sync reads their current members again, within its own attempt limit.
                if any(group_id not in failed_deletes for group_id in unapplied):
                    await self.user_group_sync_point.update_sync_point(
                        sync_point_key, {"fullSyncIncomplete": True, "fullSyncAttempts": 1}
                    )
                # Graph reports a deletion once. Queue it so every later run tries it again,
                # rather than leaving the deleted group's members with its access.
                still_failing = [g for g in failed_deletes if not await self.handle_delete_group(g)]
                if still_failing:
                    queued = sorted(set(stored.get('pendingGroupDeletes') or []) | set(still_failing))
                    await self.user_group_sync_point.update_sync_point(sync_point_key, {"pendingGroupDeletes": queued})
                    self.logger.error(
                        f"❌ Deleted groups {still_failing} could not be removed; their members keep the group's access "
                        "until a later run removes them"
                    )

            # Handle pagination and completion
            if result.get('next_link'):
                url = result.get('next_link')
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": url, "deltaLink": None, "heldPage": None, "heldPageAttempts": 0}
                )
            elif result.get('delta_link'):
                await self.user_group_sync_point.update_sync_point(
                    sync_point_key,
                    {"nextLink": None, "deltaLink": result.get('delta_link'), "heldPage": None, "heldPageAttempts": 0}
                )
                self.logger.info("Delta sync completed, delta link saved for next run")
                break
            else:
                self.logger.warning("Received response with neither next_link nor delta_link")
                break


    async def _retry_pending_group_deletes(self, pending: list[str], sync_point_key: str) -> None:
        remaining = [group_id for group_id in pending if not await self.handle_delete_group(group_id)]
        await self.user_group_sync_point.update_sync_point(sync_point_key, {"pendingGroupDeletes": remaining})
        if remaining:
            self.logger.error(f"❌ Deleted groups {remaining} still could not be removed; will try again next run")
        else:
            self.logger.info(f"Removed previously failed group deletions: {pending}")

    async def _process_member_change(self, group_id: str, member_change: dict) -> bool:
        """
        Processes a single member change from the delta response.

        Returns False when a removal could not be applied. Additions need no work
        here: the group's full member list is saved separately.
        """
        if '@removed' not in member_change:
            return True

        user_id = member_change.get('id')
        email = await self.msgraph_client.get_user_email(user_id)

        if not email:
            self.logger.error(f"❌ Could not look up member {user_id} to remove from group {group_id}")
            return False

        self.logger.info(f"    -> [DELTA] 👤⛔ REMOVING member: {email} ({user_id}) from group {group_id}")
        try:
            success = await self.data_entities_processor.on_user_group_member_removed(
                external_group_id=group_id,
                user_email=email,
                connector_id=self.connector_id
            )
        except Exception as e:
            # Reported as not applied rather than raised, so the rest of the page is
            # still applied and the caller holds the page for this group.
            self.logger.error(f"❌ Error removing member {email} from group {group_id}: {e}", exc_info=True)
            return False
        if not success:
            self.logger.error(f"❌ Error removing member {email} from group {group_id}")
        return bool(success)

    async def handle_group_create(self, group: Group) -> bool | None:
        """
        Handles the creation or update of a single user group.
        Fetches members and sends to data processor.

        Returns:
            True once the group's current members are saved. None when they can never
            be read (403/404): the group keeps its stored members and counts as handled.
            False if the change should be tried again later.
        """
        try:
            await self._save_group(group)
            return True

        except GraphReadFailedError as e:
            if e.permanent:
                self.logger.warning(f"Skipping group {group.id}: {e}; it keeps its stored members")
                return None
            self.logger.error(f"❌ Error handling group create for {group.id}: {e}")
            return False
        except Exception as e:
            self.logger.error(f"❌ Error handling group create for {getattr(group, 'id', 'unknown')}: {e}", exc_info=True)
            return False

    async def _save_group(self, group: Group) -> None:
        """Read a group's members and save it. Raises GraphReadFailedError if they can't be read."""
        app_users = await self._collect_group_users(group)
        await self.data_entities_processor.on_new_user_groups([(self._to_app_user_group(group), app_users)])
        self.logger.info(f"Processed group creation/update for: {group.display_name} with {len(app_users)} user members")


    async def _get_users_from_nested_group(self, nested_group) -> List[AppUser]:
        """
        Fetches users from a nested group (one level deep only).

        Args:
            nested_group: A group member object from Microsoft Graph API

        Returns:
            List of AppUser entities from the nested group

        Raises:
            GraphReadFailedError: if the nested group's members can't be read
        """
        nested_group_name = getattr(nested_group, 'display_name', nested_group.id)
        self.logger.info(f"Processing nested group member: {nested_group_name}")

        try:
            nested_members = await self.msgraph_client.get_group_members(nested_group.id, raise_on_error=True)
        except Exception as e:
            raise _read_failure(f"members of nested group {nested_group_name}", e) from e

        app_users = []
        for nested_member in nested_members:
            nested_odata_type = getattr(nested_member, 'odata_type', None) or (nested_member.additional_data or {}).get('@odata.type', '')

            if '#microsoft.graph.user' in nested_odata_type:
                app_user = self._create_app_user_from_member(nested_member)
                if app_user:
                    app_users.append(app_user)
            else:
                self.logger.debug(f"Skipping non-user member '{nested_odata_type}' in nested group {nested_group_name}")

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

    async def handle_delete_group(self, group_id: str) -> bool:
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

    async def _run_sync_with_yield(self, user_id: str) -> None:
        """
        Synchronizes drive contents using delta API with yielding for non-blocking operation.

        Args:
            user_id: The user identifier
        """
        try:
            self.logger.info(f"Starting sync for user {user_id}")

            # Get current sync state
            root_url = f"/users/{user_id}/drive/root/delta"
            sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "users", user_id)
            sync_point = await self.drive_delta_sync_point.read_sync_point(sync_point_key)
            pending_access: list[str] = []
            if sync_point and sync_point.get('pendingAccessReads'):
                pending_access = await self._retry_pending_access_reads(sync_point['pendingAccessReads'], sync_point_key)

            # Create RecordGroup if sync_point doesn't exist (first sync)
            if not sync_point:
                try:
                    # Get user drive information
                    drive = await self.msgraph_client.get_user_drive(user_id)
                    # Get user info (email and display name)
                    user_info = await self.msgraph_client.get_user_info(user_id)

                    if user_info:
                        # Create RecordGroup for the user's OneDrive
                        display_name = user_info.get('display_name')
                        user_email = user_info.get('email', user_id)

                        if display_name:
                            record_group_name = f"{display_name}'s OneDrive"
                        else:
                            record_group_name = f"OneDrive - {user_email}"

                        record_group = RecordGroup(
                            external_group_id=drive.id,
                            name=record_group_name,
                            group_type=RecordGroupType.DRIVE,
                            connector_name=self.connector_name,
                            connector_id=self.connector_id,
                            web_url=getattr(drive, 'web_url', None),
                            description=f"OneDrive for {display_name or user_email}",
                        )

                        owner_permission = Permission(
                            email=user_email,
                            type=PermissionType.OWNER,
                            entity_type=EntityType.USER
                        )

                        # Save the RecordGroup
                        await self.data_entities_processor.on_new_record_groups([(record_group, [owner_permission])])
                        self.logger.info(f"Created RecordGroup for user {user_id} with drive ID {drive.id}")
                    else:
                        self.logger.warning(f"Could not fetch user info for {user_id}, skipping RecordGroup creation")
                except Exception as e:
                    self.logger.error(f"Error creating RecordGroup for user {user_id}: {e}", exc_info=True)
                    # Continue with sync even if RecordGroup creation fails

            url = sync_point.get('deltaLink') or sync_point.get('nextLink') if sync_point else None
            enumerates_drive = not url
            if not url:
                url = ("{+baseurl}" + root_url)
            # Graph answers a delta link with the sharing-changes preference only if the link was made
            # with it (410 Resync required otherwise), so the preference follows the link.
            sharing_changes = bool(sync_point and sync_point.get("sharingChanges"))
            if enumerates_drive and self.scope_reader is not None:
                sharing_changes = True
                await self.drive_delta_sync_point.update_sync_point(sync_point_key, {"sharingChanges": True})

            drive_id = await self._user_drive_id(user_id)
            permission_state: dict[str, Any] = {}
            if drive_id:
                try:
                    permission_key = _drive_permissions_key(drive_id)
                    permission_state = await self.drive_delta_sync_point.read_sync_point(permission_key) or {}
                    if enumerates_drive and self.scope_reader is not None:
                        # A first sync reads every item, so with a certificate it leaves the drive in inheritance mode.
                        permission_state["enumerating"] = True
                        await self.drive_delta_sync_point.update_sync_point(permission_key, {"enumerating": True})
                except Exception as e:
                    # The drive still syncs; how its records were written is brought up to date on a later run.
                    self.logger.warning(f"⚠️ Could not read how user {user_id}'s OneDrive records were written: {e}")
                    drive_id = None

            try:
                if drive_id and permission_state.get("recheck") and self.scope_reader is not None:
                    try:
                        await self._recheck_item_access(drive_id, permission_state)
                    except Exception as e:
                        self.logger.warning(f"⚠️ Could not read again the access of queued items of OneDrive {drive_id}: {e}")

                pending_access = await self._sync_delta_pages(
                    user_id,
                    url,
                    _DeltaCursor(
                        key=sync_point_key,
                        next_field="nextLink",
                        held_field="heldPage",
                        held_attempts_field="heldPageAttempts",
                        delta_field="deltaLink",
                        state=sync_point or {},
                    ),
                    pending_access,
                    show_sharing_changes=sharing_changes,
                )

                if drive_id:
                    await self._verify_scanned_inheritance(drive_id)
                    await self._settle_permission_mode(user_id, drive_id, permission_state, pending_access)
            finally:
                if drive_id:
                    self._forget_drive_scope(drive_id)
                    try:
                        await self._save_items_without_known_inheritance(drive_id)
                    except Exception as e:
                        self.logger.error(f"❌ Could not queue items of OneDrive {drive_id} to be read again: {e}")

            self.logger.info(f"Completed delta sync for user {user_id}")

        except Exception as ex:
            self.logger.error(f"❌ Error in delta sync for user {user_id}: {ex}")
            raise

    async def _sync_delta_pages(
        self, user_id: str, url: str, cursor: "_DeltaCursor", pending_access: list[str], *, show_sharing_changes: bool = False
    ) -> list[str]:
        """Read a drive's delta pages from ``url`` and apply them; returns the items whose access is still to be read."""
        sync_point_key = generate_record_sync_point_key(RecordType.DRIVE.value, "users", user_id)
        batch_records = []
        batch_count = 0

        while True:
            # Fetch delta changes
            # An item that breaks inheritance changes only its sharing. While items are stored as
            # inheriting, delta must report that, or the item keeps its folder's audience.
            result = await self.msgraph_client.get_delta_response(url, show_sharing_changes=show_sharing_changes)
            # Graph can send an empty page with a nextLink, and the last page of a
            # no-change sync is empty too; both links still have to be followed or saved.
            drive_items = result.get('drive_items') or []

            # A page held for a temporary failure is replayed a bounded number of
            # times, so one unreadable file can't stop this drive from syncing.
            attempts_so_far = cursor.held_attempts(url)
            unresolved_access: list[str] = []
            page_items = self._process_delta_items_generator(
                drive_items,
                hold_page_on_incomplete_walk=attempts_so_far + 1 < MAX_PAGE_ATTEMPTS,
                unresolved_access=unresolved_access,
            )
            try:
                processed = [entry async for entry in page_items]
            except DrivePageIncompleteError:
                await self.drive_delta_sync_point.update_sync_point(
                    cursor.key,
                    sync_point_data={cursor.held_field: url, cursor.held_attempts_field: attempts_so_far + 1},
                )
                raise

            # Saved before any record: once one is stored, a replay no longer sees the
            # item as new, and a saved shared flag won't start the folder walk again.
            if unresolved_access:
                pending_access = sorted(set(pending_access) | set(unresolved_access))
                await self.drive_delta_sync_point.update_sync_point(
                    sync_point_key, sync_point_data={"pendingAccessReads": pending_access}
                )

            for file_record, permissions, record_update in processed:
                if record_update.is_deleted:
                    # Handle deletion immediately
                    await self._handle_record_updates(record_update)
                    continue

                # Handle updates
                if record_update.is_updated:
                    await self._handle_record_updates(record_update)
                    continue

                if file_record:
                    # Add to batch
                    batch_records.append((file_record, permissions))
                    batch_count += 1

                    # Process batch when it reaches the size limit
                    if batch_count >= self.batch_size:
                        await self.data_entities_processor.on_new_records(batch_records)
                        batch_records = []
                        batch_count = 0

                        # Allow other operations to proceed
                        await asyncio.sleep(0.1)

            # Process remaining records in batch
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)
                batch_records = []
                batch_count = 0

            # Update sync state with next_link
            next_link = result.get('next_link')
            if next_link:
                await self.drive_delta_sync_point.update_sync_point(
                    cursor.key,
                    sync_point_data={
                        cursor.next_field: next_link,
                        cursor.held_field: None,
                        cursor.held_attempts_field: 0,
                        **cursor.on_progress,
                        **({"pendingAccessReads": pending_access} if cursor.key == sync_point_key else {}),
                    }
                )
                url = next_link
            else:
                if cursor.delta_field is None:
                    # This pass's delta link is not kept: the drive's own link stays the one
                    # to resume from, so changes made while this pass ran are still seen.
                    await self.drive_delta_sync_point.update_sync_point(
                        cursor.key,
                        sync_point_data={cursor.next_field: None, cursor.held_field: None, cursor.held_attempts_field: 0},
                    )
                    break
                # No more pages - store deltaLink and clear nextLink
                delta_link = result.get('delta_link', None)
                if not delta_link:
                    # Saving None would make the next run start a fresh delta, which
                    # never reports files deleted since the stored link.
                    self.logger.warning(f"Delta page for user {user_id} had neither a next nor a delta link; keeping the saved checkpoint")
                    break
                await self.drive_delta_sync_point.update_sync_point(
                    cursor.key,
                    sync_point_data={
                        cursor.next_field: None,
                        cursor.delta_field: delta_link,
                        cursor.held_field: None,
                        cursor.held_attempts_field: 0,
                        **({"pendingAccessReads": pending_access} if cursor.key == sync_point_key else {}),
                    }
                )
                break
        return pending_access

    async def _user_drive_id(self, user_id: str) -> str | None:
        """The id of a user's OneDrive, which keys what is stored about its permissions."""
        drive_id = self._user_drive_ids.get(user_id)
        if drive_id or self.msgraph_client is None:
            return drive_id
        try:
            drive = await self.msgraph_client.get_user_drive(user_id)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not read the drive id of user {user_id}; its permission mode is left as stored: {e}")
            return None
        drive_id = getattr(drive, 'id', None)
        if isinstance(drive_id, str) and drive_id:
            self._user_drive_ids[user_id] = drive_id
            return drive_id
        return None

    async def _settle_permission_mode(
        self, user_id: str, drive_id: str, permission_state: dict[str, Any], pending_access: list[str]
    ) -> None:
        """After a drive's changes are applied, move it to inheritance mode when it can be.

        A drive whose stored records were written without SharePoint's answer (synced
        before a certificate was added) is enumerated once more, which rewrites every
        record knowing whether it inherits.
        """
        permission_key = _drive_permissions_key(drive_id)
        if permission_state.get("enumerating") and not permission_state.get("migrationNextLink"):
            # A first sync, or a rewrite whose last step failed, has read every item; those it
            # couldn't tell are queued for recheck.
            await self._adopt_sharing_changes_link(user_id, drive_id, pending_access)
            await self._mark_inheritance_mode(drive_id, permission_state)
            return
        if self.scope_reader is None or (
            permission_state.get("mode") == PERMISSION_MODE_INHERITANCE and not permission_state.get("migrationNextLink")
        ):
            return
        if await self._get_drive_scope(drive_id) is None:
            return

        self.logger.info(
            f"Rewriting the stored access of every item of user {user_id}'s OneDrive so that items inherit "
            "their folder's access; this happens once."
        )
        start = permission_state.get("migrationNextLink") or ("{+baseurl}" + f"/users/{user_id}/drive/root/delta")
        permission_state["enumerating"] = True
        await self.drive_delta_sync_point.update_sync_point(
            permission_key, {"enumerating": True, "migrationNextLink": start}
        )
        try:
            await self._sync_delta_pages(
                user_id,
                start,
                _DeltaCursor(
                    key=permission_key,
                    next_field="migrationNextLink",
                    held_field="migrationHeldPage",
                    held_attempts_field="migrationHeldPageAttempts",
                    delta_field="migrationDeltaLink",
                    state=permission_state,
                    # Restarting from scratch is for a cursor that stopped working, not for failures spread over a long rewrite.
                    on_progress={"migrationFailures": 0},
                ),
                pending_access,
                show_sharing_changes=True,
            )
        except Exception as e:
            stored = await self.drive_delta_sync_point.read_sync_point(permission_key) or {}
            failures = int(stored.get("migrationFailures") or 0) + 1
            restart = failures >= MAX_PAGE_ATTEMPTS
            self.logger.error(
                f"❌ Rewriting the stored access of user {user_id}'s OneDrive stopped; it "
                f"{'starts over' if restart else 'resumes'} next run: {e}"
            )
            update: dict[str, Any] = (
                {"migrationFailures": 0, "migrationNextLink": None, "enumerating": False}
                if restart else {"migrationFailures": failures}
            )
            await self.drive_delta_sync_point.update_sync_point(permission_key, update)
            permission_state.update(update)
            return
        await self._adopt_sharing_changes_link(user_id, drive_id, pending_access)
        await self._verify_scanned_inheritance(drive_id)
        await self._mark_inheritance_mode(drive_id, permission_state)

    async def _adopt_sharing_changes_link(self, user_id: str, drive_id: str, pending_access: list[str]) -> None:
        """Make the rewrite's delta link the drive's, once the old link has been read to the end.

        The rewrite asked for sharing changes; a link made without that can't be asked for
        them. The old link is read once more first, so what changed or was deleted before
        the rewrite started is not lost; what it reads again is applied twice, harmlessly.
        """
        user_key = generate_record_sync_point_key(RecordType.DRIVE.value, "users", user_id)
        permission_key = _drive_permissions_key(drive_id)
        new_link = (await self.drive_delta_sync_point.read_sync_point(permission_key) or {}).get("migrationDeltaLink")
        current = await self.drive_delta_sync_point.read_sync_point(user_key) or {}
        if not new_link or current.get("sharingChanges"):
            # The drive's own link already reports sharing changes: it stays, as it was read last.
            await self.drive_delta_sync_point.update_sync_point(permission_key, {"migrationDeltaLink": None})
            return
        old_link = current.get("deltaLink") or current.get("nextLink")
        if old_link:
            await self._sync_delta_pages(
                user_id,
                old_link,
                _DeltaCursor(
                    key=user_key, next_field="nextLink", held_field="heldPage",
                    held_attempts_field="heldPageAttempts", delta_field="deltaLink", state=current,
                ),
                pending_access,
            )
        await self.drive_delta_sync_point.update_sync_point(
            user_key, {"deltaLink": new_link, "nextLink": None, "sharingChanges": True}
        )
        await self.drive_delta_sync_point.update_sync_point(permission_key, {"migrationDeltaLink": None})

    async def _mark_inheritance_mode(self, drive_id: str, permission_state: dict[str, Any]) -> None:
        unknown = self._items_without_known_inheritance.get(drive_id, set())
        # Read again rather than taken from the start of the run: a reindex may have queued items since.
        stored = await self.drive_delta_sync_point.read_sync_point(_drive_permissions_key(drive_id)) or {}
        recheck = set(stored.get("recheck") or []) | unknown
        update: dict[str, Any] = {"enumerating": False, "migrationNextLink": None, "migrationFailures": 0}
        if len(recheck) <= MAX_RECHECK_ITEMS:
            update.update({"mode": PERMISSION_MODE_INHERITANCE, "recheck": sorted(recheck)})
        else:
            update.update({"mode": PERMISSION_MODE_ITEM_ACL, "recheck": []})
        await self.drive_delta_sync_point.update_sync_point(_drive_permissions_key(drive_id), update)
        permission_state.update(update)
        self._items_without_known_inheritance.pop(drive_id, None)
        if update["mode"] == PERMISSION_MODE_INHERITANCE:
            self.logger.info(f"✅ OneDrive {drive_id}: items now inherit their folder's access")

    async def _save_items_without_known_inheritance(self, drive_id: str) -> None:
        """Queue the items written without knowing whether they inherit, to be read again.

        Only for a drive in (or moving to) inheritance mode: elsewhere every record
        carries its effective access anyway. Too many to queue, and the drive falls
        back to that mode, to be enumerated again once SharePoint can be read.
        """
        unknown = self._items_without_known_inheritance.pop(drive_id, None)
        if not unknown:
            return
        key = _drive_permissions_key(drive_id)
        state = await self.drive_delta_sync_point.read_sync_point(key) or {}
        if not (state.get("mode") == PERMISSION_MODE_INHERITANCE or state.get("enumerating")):
            return
        recheck = set(state.get("recheck") or []) | unknown
        if len(recheck) <= MAX_RECHECK_ITEMS:
            update: dict[str, Any] = {"recheck": sorted(recheck)}
        else:
            self.logger.warning(
                f"⚠️ {len(recheck)} items of OneDrive {drive_id} were written without knowing whether they inherit; "
                "the drive is rewritten in full once SharePoint can be read again."
            )
            update = {"mode": PERMISSION_MODE_ITEM_ACL, "recheck": [], "enumerating": False, "migrationNextLink": None}
        await self.drive_delta_sync_point.update_sync_point(key, update)

    async def _recheck_item_access(self, drive_id: str, permission_state: dict[str, Any]) -> None:
        """Rewrite the access of items queued because it wasn't known whether they inherit."""
        queued = list(permission_state.get("recheck") or [])
        remaining = await self._reevaluate_item_access(drive_id, queued)
        key = _drive_permissions_key(drive_id)
        # Items queued by a reindex while these were read stay queued.
        stored = await self.drive_delta_sync_point.read_sync_point(key) or {}
        recheck = sorted((set(stored.get("recheck") or []) - set(queued)) | set(remaining))
        await self.drive_delta_sync_point.update_sync_point(key, {"recheck": recheck})
        permission_state["recheck"] = recheck

    async def _reevaluate_item_access(self, drive_id: str, item_ids: list[str]) -> list[str]:
        """Read again whether stored items inherit, and rewrite their access; returns those still unknown."""
        remaining: list[str] = []
        for item_id in item_ids:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, item_id)
            if not record:
                continue
            unique_id = etag_unique_id(record.external_revision_id)
            inherits = await self._inheritance(
                drive_id, unique_id, is_root=record.parent_external_record_id is None, item_id=None
            )
            if inherits is None:
                remaining.append(item_id)
                continue
            try:
                await self._write_known_access(record, drive_id, inherits=inherits)
            except Exception as ex:
                self.logger.warning(f"Access of item {item_id} still could not be read or saved: {ex}")
                remaining.append(item_id)
        return remaining

    async def _write_known_access(self, record: Record, drive_id: str, *, inherits: bool) -> None:
        """Rewrite a stored record's access once SharePoint has said whether it inherits."""
        grants: list[Permission] = []
        if not inherits:
            grants = await self._convert_to_permissions(
                await self.msgraph_client.get_file_permission(drive_id, record.external_record_id, raise_on_error=True)
            )
        record.inherit_permissions = inherits
        record.rewrite_permissions = True
        await self.data_entities_processor.on_updated_record_permissions(record, grants)

    async def _verify_scanned_inheritance(self, drive_id: str) -> None:
        """Read the drive's list again and correct items that broke inheritance after the first read.

        Answers from a full read can be minutes old by the time an item is written,
        and the delta link has moved past the change, so an item that became unique in
        between would otherwise keep its folder's access until it next changes.
        """
        decided = self._inheriting_from_full_read.pop(drive_id, {})
        if not decided:
            return
        scope = self._drive_scopes.get(drive_id)
        if scope is None:
            # SharePoint stopped answering after the full read: those answers are checked next run.
            for item_id in decided.values():
                self._queue_unknown_inheritance(drive_id, item_id)
            return
        try:
            changed = await scope.changed_to_unique_since_read(set(decided))
        except Exception as e:
            self.logger.warning(
                f"⚠️ Could not check OneDrive {drive_id} again for items that broke inheritance during this run; "
                f"they are read one by one: {e}"
            )
            for item_id in decided.values():
                self._queue_unknown_inheritance(drive_id, item_id)
            return
        if not changed:
            return
        # Asked one by one from here: the full read is what went stale.
        self._drive_scopes.pop(drive_id, None)
        changed_items = [decided[unique_id] for unique_id in changed]
        try:
            remaining = await self._reevaluate_item_access(drive_id, changed_items)
        except Exception as e:
            self.logger.warning(f"⚠️ Could not rewrite items of OneDrive {drive_id} that broke inheritance; queued: {e}")
            remaining = changed_items
        for item_id in remaining:
            self._queue_unknown_inheritance(drive_id, item_id)

    def _forget_drive_scope(self, drive_id: str) -> None:
        """Drop what SharePoint said about a drive; full-read answers not yet checked are queued instead."""
        self._drive_scopes.pop(drive_id, None)
        for item_id in self._inheriting_from_full_read.pop(drive_id, {}).values():
            self._queue_unknown_inheritance(drive_id, item_id)

    async def _retry_pending_access_reads(self, pending: list[str], sync_point_key: str) -> list[str]:
        """Read again the access of items saved while it couldn't be read; returns those still unread."""
        remaining = []
        for item_id in pending:
            record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, item_id)
            # Deleted, or its page never committed; a replay of that page queues it again.
            if not record:
                continue
            try:
                inherits = await self._inherits(
                    record.external_record_group_id,
                    etag_unique_id(record.external_revision_id),
                    is_root=record.parent_external_record_id is None,
                    item_id=item_id,
                )
                await self._write_known_access(record, record.external_record_group_id, inherits=inherits)
            except Exception as ex:
                self.logger.warning(f"Access of item {item_id} still could not be read or saved: {ex}")
                remaining.append(item_id)
        await self.drive_delta_sync_point.update_sync_point(sync_point_key, sync_point_data={"pendingAccessReads": remaining})
        if remaining:
            self.logger.error(f"❌ The access of {remaining} still could not be read; they are tried again next run")
        return remaining

    async def _process_users_in_batches(self, users: List[AppUser]) -> None:
        """
        Process users in concurrent batches for improved performance.

        Args:
            users: List of users to process
        """
        try:
            all_active_users = await self.data_entities_processor.get_all_active_users()
            active_user_emails = {active_user.email.lower() for active_user in all_active_users}

            # Filter users to sync (only active users)
            active_users = [
                user for user in users
                if user.email and user.email.lower() in active_user_emails
            ]

            self.logger.info(f"Found {len(active_users)} active users out of {len(users)} total users")

            users_to_sync = []
            failed_users: List[Tuple[str, OneDriveUserStatus]] = []
            for user in active_users:
                try:
                    onedrive_status = await self._user_has_onedrive(user.source_user_id)
                except Exception as e:
                    self.logger.error(f"❌ Error checking OneDrive for user {user.email}, skipping: {e}", exc_info=True)
                    failed_users.append((user.email, OneDriveUserStatus.UNKNOWN_ERROR))
                    continue

                if onedrive_status is OneDriveUserStatus.AVAILABLE:
                    users_to_sync.append(user)
                else:
                    failed_users.append((user.email, onedrive_status))
                    self.logger.info(
                        "Skipping user %s: OneDrive status is %s",
                        user.email,
                        onedrive_status.value,
                    )

            await self._notify_onedrive_user_failures(failed_users)

            self.logger.info(f"Processing {len(users_to_sync)} users with OneDrive out of {len(active_users)} active users")
            self.onedrive_users_synced = len(users_to_sync)
            if len(users_to_sync) == 0:
                await self.notify(
                    type=NotificationType.CONNECTOR_RECORD_SYNC_ERROR,
                    severity=NotificationSeverity.WARNING,
                    title="No OneDrive users synced",
                    message=(
                        "Ensure that your OneDrive users are invited to Pipeshub, and verify that your application has Files.Read.All API permission with admin consent."
                    ),
                    payload={
                        "connector_id": self.connector_id,
                        "connector_name": self.connector_name.value,
                        "connector_scope": self.scope,
                    },
                )

            # Process users in concurrent batches
            for i in range(0, len(users_to_sync), self.max_concurrent_batches):
                batch = users_to_sync[i:i + self.max_concurrent_batches]

                sync_tasks = [
                    self._run_sync_with_yield(user.source_user_id)
                    for user in batch
                ]

                await asyncio.gather(*sync_tasks, return_exceptions=True)
                await asyncio.sleep(1)

            self.logger.info("Completed processing all user batches")

        except Exception as e:
            self.logger.error(f"❌ Error processing users in batches: {e}")
            raise

    async def _notify_onedrive_user_failures(
        self,
        failed_users: List[Tuple[str, OneDriveUserStatus]],
    ) -> None:
        for status, (title, message_template) in ONEDRIVE_USER_FAILURE_NOTIFICATIONS.items():
            emails = [email for email, reason in failed_users if reason is status]
            if not emails:
                continue

            displayed_emails = ", ".join(emails[:5])
            if len(emails) > 5:
                displayed_emails = f"{displayed_emails}, and {len(emails) - 5} more"

            await self.notify(
                type=NotificationType.CONNECTOR_WARNING,
                severity=NotificationSeverity.WARNING,
                title=title,
                message=message_template.format(emails=displayed_emails),
                payload={
                    "connector_id": self.connector_id,
                    "connector_name": self.connector_name.value,
                    "connector_scope": self.scope,
                },
            )

    async def _user_has_onedrive(self, user_id: str) -> OneDriveUserStatus:
        """
        Check if a user has OneDrive provisioned.

        Args:
            user_id: The user identifier

        Returns:
            The user's OneDrive availability status.
        """
        try:
            drive = await self.msgraph_client.get_user_drive(user_id)
            drive_id = getattr(drive, 'id', None)
            if isinstance(drive_id, str) and drive_id:
                self._user_drive_ids[user_id] = drive_id
            return OneDriveUserStatus.AVAILABLE
        except ODataError as e:
            error_message = str(e).lower()
            error_code = (e.error.code or "").lower() if e.error else ""

            if (
                e.response_status_code == 404
                or error_code in {
                    "resourcenotfound",
                    "itemnotfound",
                    "request_resourcenotfound",
                }
                or "404" in error_message
            ):
                return OneDriveUserStatus.NOT_PROVISIONED

            if (
                e.response_status_code == 423
                or "resourcelocked" in error_message
            ):
                self.logger.warning(
                    f"⚠️ OneDrive for user {user_id} is locked or blocked (423 resourceLocked), skipping: {e}"
                )
                return OneDriveUserStatus.LOCKED
            raise
        except Exception as e:
            self.logger.error("❌ Error checking if user has OneDrive: %s", e)
            raise
    
    async def _sync_users(self) -> List[AppUser]:
        """
        Syncs OneDrive users.
        """
        try:
            users = await self.msgraph_client.get_all_users()
            await self.data_entities_processor.on_new_app_users(users)
            self.logger.info(f"✅ Successfully synced {len(users)} users")
            return users
        except ODataError as e:
            self.logger.error(f"❌ Error syncing OneDrive users: {e}", exc_info=True)
            error_code = (e.error.code or "") if e.error else ""
            if error_code == "Authorization_RequestDenied" or e.response_status_code == 403:
                await self.notify(
                    type=NotificationType.CONNECTOR_USER_SYNC_ERROR,
                    severity=NotificationSeverity.ERROR,
                    title="OneDrive users sync failed",
                    message=(
                        "Please check your authentication credentials are correct and has User.Read.All API permission."
                    ),
                    recipient_user_ids=[self.created_by],
                    payload={
                        "connector_id": self.connector_id,
                        "connector_name": self.connector_name.value,
                        "connector_scope": self.scope,
                    },
                )
            raise
        except Exception as e:
            self.logger.error(f"❌ Error syncing OneDrive users: {e}", exc_info=True)
            raise

    async def _handle_reindex_event(self, record_id: str) -> None:
        """
        Handle reindexing of a specific record.

        Args:
            record_id: The ID of the record to reindex
        """
        try:
            self.logger.info(f"Handling reindex event for record {record_id}")

            # Get the record from database
            record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, record_id
            )

            if not record:
                self.logger.warning(f"⚠️ Record {record_id} not found for reindexing")
                return

            # Get fresh data from OneDrive
            drive_id = record.external_record_group_id
            item_id = record.external_record_id

            # Get updated metadata
            async with self.msgraph_client.rate_limiter:
                item = await self.msgraph_client.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

            if item:
                # Process the updated item
                record_update = await self._process_delta_item(item)

                if record_update:
                    # Send for reindexing
                    await self.data_entities_processor.on_record_content_update(record_update.record)

                    # Update permissions if changed
                    if record_update.permissions_changed:
                        await self.data_entities_processor.on_updated_record_permissions(
                            record_update.record,
                            record_update.new_permissions
                        )

            self.logger.info(f"Completed reindex for record {record_id}")

        except Exception as e:
            self.logger.error(f"❌ Error handling reindex event: {e}")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handle webhook notifications from Microsoft Graph.

        Args:
            notification: The webhook notification payload
        """
        try:
            self.logger.info("Processing webhook notification")

            # Reinitialize credential if needed (webhooks might arrive after days of inactivity)
            await self._reinitialize_credential_if_needed()

            # Extract relevant information from notification
            resource = notification.get('resource', '')
            notification.get('changeType', '')

            # Parse the resource to get user and item IDs
            # Resource format: "users/{userId}/drive/root" or similar
            parts = resource.split('/')
            EXPECTED_PARTS = 2
            if len(parts) >= EXPECTED_PARTS and parts[0] == 'users':
                user_id = parts[1]

                # Run incremental sync for this user
                await self._run_sync_with_yield(user_id)

            self.logger.info("Webhook notification processed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error handling webhook notification: {e}")

    async def run_sync(self) -> None:
        """
        Main entry point for the OneDrive connector.
        Implements non-blocking sync with all requested features.
        """
        try:
            self.logger.info("Starting OneDrive connector sync")

            # Reinitialize credential to prevent "HTTP transport has already been closed" errors
            # This is necessary because the connector instance may be reused across multiple
            # scheduled runs that are days apart, causing the HTTP session to timeout
            await self._reinitialize_credential_if_needed()
            self.onedrive_users_synced = 0
            self._reset_permission_scopes()

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "onedrive", self.connector_id, self.logger
            )
            await self._remind_about_certificate()

            # Step 1: Sync users
            self.logger.info("Syncing users...")
            users = await self._sync_users()

            # Step 2: Sync user groups and their members
            self.logger.info("Syncing user groups...")
            await self._sync_user_groups()

            # Step 3: Process user drives with yielding for non-blocking operation
            self.logger.info("Syncing user drives...")
            await self._process_users_in_batches(users)

            self.logger.info("OneDrive connector sync completed successfully")
            if self.onedrive_users_synced > 0:
                await self.notify(
                    type=NotificationType.CONNECTOR_SUCCESS,
                    severity=NotificationSeverity.INFO,
                    title="OneDrive sync complete",
                    message="OneDrive sync completed successfully",
                    recipient_user_ids=[self.created_by],
                    payload={
                        "connector_id": self.connector_id,
                        "connector_name": self.connector_name.value,
                        "connector_scope": self.scope,
                    },
                )

        except Exception as e:
            self.logger.error(f"❌ Error in OneDrive connector run: {e}")
            raise
        finally:
            await self._collect_action_items()

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
            return
        except Exception as e:
            self.logger.warning(f"⚠️ Credential needs reinitialization: {e}")

        # Close old credential if it exists
        if hasattr(self, 'credential') and self.credential:
            try:
                await self.credential.close()
            except Exception:
                pass

        # Get credentials from config
        config = self.config.get("credentials", {})
        auth_config = config.get("auth", {})
        try:
            self.credential = graph_credential(auth_config)
        except ValueError as credentials_error:
            raise ValueError(f"Cannot reinitialize: credentials not found in config. {credentials_error}") from credentials_error

        try:
            # Pre-initialize to establish HTTP session
            await self.credential.get_token("https://graph.microsoft.com/.default")
        except Exception as reinit_error:
            self.logger.error(f"❌ Failed to reinitialize OneDrive credential: {reinit_error}")
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title="OneDrive authentication failed",
                message=(
                    "Unable to re-authenticate the OneDrive connector. "
                    f"The client {'secret' if auth_config.get('clientSecret') else 'certificate'} may have expired. "
                    "Please update the connector credentials."
                ),
                payload={
                    "connector_id": self.connector_id,
                    "connector_name": self.connector_name.value,
                    "connector_scope": self.scope,
                },
                recipient_user_ids=[self.created_by],
            )
            raise

        # Recreate Graph client with new credential
        self.client = GraphServiceClient(
            self.credential,
            scopes=["https://graph.microsoft.com/.default"]
        )
        self.msgraph_client = MSGraphClient(self.connector_name, self.connector_id, self.client, self.logger)

        self.logger.info("✅ Credential successfully reinitialized")

    async def run_incremental_sync(self) -> None:
        """
        Run incremental sync for a specific user or all users.
        Uses the stored delta token to get only changes since last sync.

        Args:
            user_id: Optional user ID to sync. If None, syncs all users.
        """
        try:
            self.logger.info("Starting incremental sync for all users")

            # Reinitialize credential to prevent session timeout issues
            await self._reinitialize_credential_if_needed()
            self._reset_permission_scopes()
            await self._remind_about_certificate()

            # Sync all active users
            users = await self.msgraph_client.get_all_users()
            await self._process_users_in_batches(users)

            self.logger.info("Incremental sync completed")

        except Exception as e:
            self.logger.error(f"❌ Error in incremental sync: {e}")
            raise
        finally:
            await self._collect_action_items()

    async def cleanup(self) -> None:
        """
        Cleanup resources when shutting down the connector.
        """
        try:
            self.logger.info("Cleaning up OneDrive connector resources")

            # Clear caches
            # self.processed_items.clear()
            # self.permission_cache.clear()

            # Close any open connections in the GraphServiceClient first
            if hasattr(self, 'client') and self.client:
                # GraphServiceClient doesn't have explicit close, but we can clear the reference
                self.client = None

            # Clear msgraph_client reference
            if hasattr(self, 'msgraph_client'):
                self.msgraph_client = None

            # Close the credential last to properly close the HTTP transport
            # This must be done after all API operations are complete
            if hasattr(self, 'credential') and self.credential:
                try:
                    await self.credential.close()
                    self.logger.info("✅ Credential HTTP transport closed successfully")
                except Exception as cred_error:
                    self.logger.warning(f"⚠️ Error closing credential (may already be closed): {cred_error}")
                finally:
                    self.credential = None

            self.logger.info("OneDrive connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during cleanup: {e}")

    async def _settle_reindexed_drives(self, drive_ids: set[str | None]) -> None:
        """Queue what a reindex wrote without knowing its inheritance, and drop the scopes it read."""
        for drive_id in drive_ids:
            if not drive_id:
                continue
            self._forget_drive_scope(drive_id)
            await self._save_items_without_known_inheritance(drive_id)

    async def reindex_records(self, records: List[Record]) -> None:
        """Reindex records."""
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} OneDrive records")

            if not self.msgraph_client:
                self.logger.error("MS Graph client not initialized. Call init() first.")
                raise Exception("MS Graph client not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
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

            await self._settle_reindexed_drives({record.external_record_group_id for record in records})
        except Exception as e:
            self.logger.error(f"Error during OneDrive reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[Tuple[Record, List[Permission]]]:
        """Fetch record from MS Graph and return data for reindexing if changed."""
        try:
            drive_id = record.external_record_group_id
            item_id = record.external_record_id

            if not drive_id or not item_id:
                self.logger.warning(f"Missing drive_id or item_id for record {record.id}")
                return None

            # Fetch fresh item from MS Graph
            async with self.msgraph_client.rate_limiter:
                item = await self.msgraph_client.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()

            if not item:
                self.logger.warning(f"Item {item_id} not found at source")
                return None

            # Use existing logic to detect changes and transform to FileRecord
            record_update = await self._process_delta_item(item)

            if not record_update or record_update.is_deleted:
                return None

            # Only return data if there's an actual update (metadata, content, or permissions)
            if record_update.is_updated:
                self.logger.info(f"Record {item_id} has changed at source. Updating.")
                # Ensure we keep the internal DB ID
                record_update.record.id = record.id
                return (record_update.record, record_update.new_permissions)

            return None

        except Exception as e:
            self.logger.error(f"Error checking OneDrive record {record.id} at source: {e}")
            return None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """OneDrive connector does not support dynamic filter options."""
        raise NotImplementedError("OneDrive connector does not support dynamic filter options")

    async def get_signed_url(self, record: Record) -> str:
        """
        Create a signed URL for a specific record.
        """
        try:
            # Reinitialize credential if needed (user might be accessing files after days of inactivity)
            await self._reinitialize_credential_if_needed()

            return await self.msgraph_client.get_signed_url(
                record.external_record_group_id,
                record.external_record_id,
                raise_on_error=True,
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"❌ Error creating signed URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream a record from OneDrive."""
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

    @classmethod
    @override
    def auth_save_problem(
        cls, auth_config: dict[str, Any], stored_auth_config: dict[str, Any] | None
    ) -> str | None:
        """New connectors sign in with a certificate; one already signing in with a client secret keeps it."""
        if (stored_auth_config or {}).get("clientSecret"):
            return None
        if auth_config.get("clientSecret"):
            return (
                "New OneDrive connectors sign in with a client certificate, not a client secret. Upload the "
                "certificate registered on the app (Certificates & secrets) and its private key."
            )
        if not has_certificate_fields(auth_config):
            return "Upload the client certificate registered on the app (Certificates & secrets) and its PKCS#8 private key."
        return certificate_problem(auth_config)

    @classmethod
    @override
    def config_action_items(cls, auth_config: dict[str, Any]) -> list[ActionItem] | None:
        return onedrive_config_action_items(auth_config)

    @classmethod
    async def _sharepoint_access_check(
        cls, graph_sign_in: ClientSecretCredential | CertificateCredential,
        certificate: ClientSecretCredential | CertificateCredential, logger: Logger,
    ) -> ConnectionCheckResult | None:
        """Read one user's OneDrive list over SharePoint REST with the certificate; None when it worked.

        Fails only on SharePoint's own refusal (401 or 403): without that permission every item
        would quietly keep its own access list. Anything that can't be checked lets the save go ahead.
        """
        client = GraphServiceClient(graph_sign_in, scopes=["https://graph.microsoft.com/.default"])
        site_url = list_id = None
        try:
            users = await client.users.get(request_configuration=RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=["id"], top=SHAREPOINT_CHECK_USERS
                )
            ))
            for user in (users.value if users and users.value else [])[:SHAREPOINT_CHECK_USERS]:
                if not user.id:
                    continue
                try:
                    drive = await client.users.by_user_id(user.id).drive.get(request_configuration=RequestConfiguration(
                        query_parameters=UserDriveRequestBuilder.DriveRequestBuilderGetQueryParameters(select=["id", "sharePointIds"])
                    ))
                except ODataError:
                    continue  # no OneDrive for this user
                ids = getattr(drive, "share_point_ids", None)
                site_url, list_id = getattr(ids, "site_url", None), getattr(ids, "list_id", None)
                if site_url and list_id:
                    break
        except Exception as e:
            logger.warning("OneDrive connection check could not find a OneDrive to check SharePoint with: %r", e)
            return ConnectionCheckResult(success=True, message="Signed in. SharePoint access could not be checked now; the first sync checks it.")
        if not (site_url and list_id):
            return ConnectionCheckResult(
                success=True, message="Signed in. No user has a OneDrive yet to check SharePoint access with; the first sync checks it."
            )

        async def sharepoint_token(resource: str) -> SharePointToken:
            token = await certificate.get_token(f"{resource}/.default")
            return SharePointToken(token=token.token, expires_on=token.expires_on)

        site_url = site_url.rstrip("/")
        reader = SharePointPermissionScopeReader(sharepoint_token, AsyncLimiter(5, 1))
        try:
            await reader.read_item_count(f"{site_url}/_api/web/lists(guid'{list_id}')")
        except SharePointRestError as e:
            if isinstance(e, SharePointAccessError) or e.status_code == HTTPStatus.FORBIDDEN:
                logger.info("OneDrive connection check: SharePoint refused the app: %s", e)
                return ConnectionCheckResult(
                    success=False,
                    message=(
                        "SharePoint refused the app. Grant it the Sites.Read.All application permission of the "
                        "SharePoint API (not Microsoft Graph) with admin consent; a new grant can take a few "
                        "minutes to apply."
                    ),
                )
            logger.warning("OneDrive connection check could not read SharePoint: %r", e)
            return ConnectionCheckResult(success=True, message="Signed in. SharePoint access could not be checked now; the first sync checks it.")
        return None

    @classmethod
    @override
    async def check_connection(cls, auth_config: dict[str, Any], logger: Logger) -> ConnectionCheckResult:
        """Sign in with the credentials on the setup form before they are saved.

        Fails only on an answer that settles it: credentials Microsoft rejects, or a
        certificate upload that can't be used. A sign-in that can't be tried (the
        network, say) lets the save go ahead, as it did before this check existed.
        """
        with_certificate = has_certificate_fields(auth_config)
        problem = certificate_problem(auth_config) if with_certificate else None
        if problem:
            if auth_config.get("clientSecret"):
                problem = f"{problem} Or remove the certificate and key to sign in with the client secret only."
            return ConnectionCheckResult(success=False, message=problem)
        try:
            graph_sign_in = graph_credential(auth_config)
        except ValueError as e:
            return ConnectionCheckResult(success=False, message=str(e).removeprefix("Incomplete OneDrive credentials. "))
        credentials = [("client secret" if auth_config.get("clientSecret") else "client certificate", graph_sign_in)]
        if with_certificate and auth_config.get("clientSecret"):
            credentials.append(("client certificate", CertificateCredential(
                tenant_id=auth_config["tenantId"], client_id=auth_config["clientId"],
                certificate_data=certificate_pem(auth_config),
            )))

        try:
            for kind, credential in credentials:
                try:
                    await credential.get_token("https://graph.microsoft.com/.default")
                except ClientAuthenticationError as e:
                    reason = (sanitize_azure_error(e).splitlines() or ["no reason given"])[0]
                    logger.info("OneDrive connection check: the %s was rejected: %s", kind, reason)
                    message = _sign_in_rejection(kind, reason)
                    if kind == "client certificate" and auth_config.get("clientSecret"):
                        message = f"{message} Or remove the certificate and key to sign in with the client secret only."
                    return ConnectionCheckResult(success=False, message=message)
                except Exception as e:
                    logger.warning("OneDrive connection check could not sign in with the %s: %r", kind, e)
                    return ConnectionCheckResult(
                        success=True, message=f"Saved without checking the {kind}: Microsoft Entra ID could not be reached."
                    )
            if with_certificate:
                certificate = next(credential for kind, credential in credentials if kind == "client certificate")
                refused = await cls._sharepoint_access_check(graph_sign_in, certificate, logger)
                if refused is not None:
                    return refused
        finally:
            for _, credential in credentials:
                try:
                    await credential.close()
                except Exception:
                    logger.debug("A connection-check credential did not close cleanly", exc_info=True)

        if with_certificate:
            return ConnectionCheckResult(
                success=True,
                message="Signed in, and SharePoint lets the app read which files and folders inherit their access.",
            )
        return ConnectionCheckResult(
            success=True,
            message=(
                "Signed in. Recommended: upload a client certificate and grant the app the SharePoint API's "
                "Sites.Read.All application permission."
            ),
        )

    async def test_connection_and_access(self) -> bool:
        """
        Probe the Microsoft Graph directory permissions used during sync:
          - User.Read.All  → GET /users?$top=1&$select=id
          - Group.Read.All → GET /groups?$top=1&$select=id

        Drive access is checked for each active user during file sync. Microsoft
        Graph can return the same 404 response for an unprovisioned drive and
        insufficient file access, so these cases cannot be distinguished here.

        Returns True only when both directory probes succeed.
        """
        self.logger.info("Testing connection and access to OneDrive")

        scope_probes = [
            ("User.Read.All",  self._probe_users_scope),
            ("Group.Read.All", self._probe_groups_scope),
        ]

        missing_scopes: List[str] = []
        for scope_name, probe in scope_probes:
            try:
                await probe()
                self.logger.info(f"✅ Permission verified: {scope_name}")
            except ODataError as e:
                error_code = (e.error.code or "") if e.error else ""
                if error_code == "Authorization_RequestDenied" or e.response_status_code == 403:
                    missing_scopes.append(scope_name)
                    self.logger.error(
                        f"❌ Missing required Microsoft Graph permission: {scope_name} "
                        f"(Azure error code: {error_code})"
                    )
                else:
                    self.logger.error(
                        f"❌ Unexpected Graph API error while probing {scope_name} "
                        f"(code={error_code}): {e}",
                        exc_info=True,
                    )
                    raise ConnectionError(
                        f"Microsoft Graph error while checking {scope_name}: "
                        f"{sanitize_graph_error(e)}"
                    ) from e
            except Exception as e:
                self.logger.error(
                    f"❌ Error testing {scope_name} permission: {e}",
                    exc_info=True,
                )
                raise ConnectionError(
                    f"Unable to check Microsoft Graph permission {scope_name}: "
                    f"{sanitize_azure_error(e)}"
                ) from e

        if missing_scopes:
            message = (
                f"OneDrive is missing the following Microsoft Graph permissions: "
                f"{', '.join(missing_scopes)}. "
                "Please grant these application permissions in Azure AD and provide admin consent."
            )
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title="OneDrive: missing API permissions",
                message=message,
                recipient_user_ids=[self.created_by],
                payload={
                    "connector_id": self.connector_id,
                    "connector_name": self.connector_name.value,
                    "connector_scope": self.scope,
                },
            )
            raise ConnectionError(message)

        return True

    async def _probe_users_scope(self) -> None:
        """Probe User.Read.All via GET /users?$top=1&$select=id."""
        await self.client.users.get(
            RequestConfiguration(
                query_parameters=UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    top=1, select=["id"]
                )
            )
        )

    async def _probe_groups_scope(self) -> None:
        """Probe Group.Read.All via GET /groups?$top=1&$select=id."""
        await self.client.groups.get(
            RequestConfiguration(
                query_parameters=GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters(
                    top=1, select=["id"]
                )
            )
        )
    @classmethod
    async def create_connector(cls, logger: Logger,
                               data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str,
                               scope: str, created_by: str, data_entities_processor, **kwargs) -> BaseConnector:
        return OneDriveConnector(logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by)


# Additional helper class for managing OneDrive subscriptions
class OneDriveSubscriptionManager:
    """
    Manages webhook subscriptions for OneDrive change notifications.
    """

    def __init__(self, msgraph_client: MSGraphClient, logger: Logger) -> None:
        self.client = msgraph_client
        self.logger = logger
        self.subscriptions: Dict[str, str] = {}  # user_id -> subscription_id mapping

    async def create_subscription(self, user_id: str, notification_url: str) -> Optional[str]:
        """
        Create a subscription for a user's OneDrive.

        Args:
            user_id: The user ID to create subscription for
            notification_url: The webhook URL to receive notifications

        Returns:
            Subscription ID if successful, None otherwise
        """
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription = Subscription(
                change_type="updated",
                notification_url=notification_url,
                resource=f"users/{user_id}/drive/root",
                expiration_date_time=expiration_datetime,
                client_state="OneDriveConnector"  # Optional: for security validation
            )

            async with self.client.rate_limiter:
                result = await self.client.client.subscriptions.post(subscription)

            if result and result.id:
                self.subscriptions[user_id] = result.id
                self.logger.info(f"Created subscription {result.id} for user {user_id}")
                return result.id

            return None

        except Exception as e:
            self.logger.error(f"❌ Error creating subscription for user {user_id}: {e}")
            return None

    async def renew_subscription(self, subscription_id: str) -> bool:
        """
        Renew an existing subscription before it expires.

        Args:
            subscription_id: The subscription ID to renew

        Returns:
            True if successful, False otherwise
        """
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription_update = Subscription(
                expiration_date_time=expiration_datetime
            )

            async with self.client.rate_limiter:
                await self.client.client.subscriptions.by_subscription_id(subscription_id).patch(subscription_update)

            self.logger.info(f"Renewed subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error renewing subscription {subscription_id}: {e}")
            return False

    async def delete_subscription(self, subscription_id: str) -> bool:
        """
        Delete a subscription.

        Args:
            subscription_id: The subscription ID to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.client.rate_limiter:
                await self.client.client.subscriptions.by_subscription_id(subscription_id).delete()

            # Remove from tracking
            user_id = next((k for k, v in self.subscriptions.items() if v == subscription_id), None)
            if user_id:
                del self.subscriptions[user_id]

            self.logger.info(f"Deleted subscription {subscription_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting subscription {subscription_id}: {e}")
            return False

    async def renew_all_subscriptions(self) -> None:
        """
        Renew all active subscriptions.
        Should be called periodically (e.g., every 2 days) to prevent expiration.
        """
        try:
            self.logger.info(f"Renewing {len(self.subscriptions)} subscriptions")

            for user_id, subscription_id in self.subscriptions.items():
                await self.renew_subscription(subscription_id)
                # Small delay to avoid rate limiting
                await asyncio.sleep(0.5)

            self.logger.info("Completed subscription renewal")

        except Exception as e:
            self.logger.error(f"❌ Error renewing subscriptions: {e}")

    async def cleanup_subscriptions(self) -> None:
        """
        Clean up all subscriptions during shutdown.
        """
        try:
            self.logger.info("Cleaning up subscriptions")

            for subscription_id in list(self.subscriptions.values()):
                await self.delete_subscription(subscription_id)

            self.subscriptions.clear()
            self.logger.info("Subscription cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during subscription cleanup: {e}")
