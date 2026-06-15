from abc import ABC, abstractmethod
import asyncio
from logging import Logger
from typing import Any, Dict, List, Optional

from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.constants import INTERNAL_CONNECTOR_GROUP_NAME
from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.models.entities import AppUser, AppUserGroup, Record
from app.models.permission import EntityType, Permission, PermissionType
from app.services.notification.types import NotificationSeverity, NotificationType, NotificationOrigin, NotificationRecipientRole
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.services.notification.notification_service import NotificationService
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DEFAULT_CONNECTOR_NOTIFICATION_LINK = "workspace/connectors/"
INITIAL_NOTIFICATION_BACKOFF = 86400 * 1000 # 24 hrs in ms
MAX_NOTIFICATION_BACKOFF = 604800 * 1000 # 7 days in ms


class BaseConnector(ABC):
    """Base abstract class for all connectors"""
    logger: Logger
    data_entities_processor: DataSourceEntitiesProcessor
    data_store_provider: DataStoreProvider
    config_service: ConfigurationService
    app: App
    connector_name: Connectors
    connector_id: str
    scope: str
    created_by: str
    creator_email: Optional[str]
    _notification_service: NotificationService | None
    _notification_cache: dict[str, tuple[float, float]] = {}

    def __init__(
        self,
        app: App,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str
    ) -> None:
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.app = app
        self.connector_name = app.get_app_name()
        self.data_store_provider = data_store_provider
        self.config_service = config_service
        self.connector_id = connector_id
        self.scope = scope
        self.created_by = created_by
        self.creator_email = None
        # Cached GROUP permission for the pseudo "ConnectorGroup" (see
        # ensure_connector_group_permission). Populated lazily by personal-scope
        # connectors that route all record-group/record access through a single
        # shared internal group instead of a direct user grant.
        self._connector_group_permission: Optional[Permission] = None
        self._notification_service = None
        self._background_tasks: set[asyncio.Task] = set()

    @abstractmethod
    async def init(self) -> bool:
        pass

    @abstractmethod
    def test_connection_and_access(self) -> bool:
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    def get_signed_url(self, record: Record) -> Optional[str]:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        NotImplementedError("This method is not supported by the subclass")

    @abstractmethod
    def run_sync(self) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def run_incremental_sync(self) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    def handle_webhook_notification(self, notification: Dict) -> None:
        NotImplementedError("This method is not supported")

    @abstractmethod
    async def cleanup(self) -> None:
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    async def reindex_records(self, record_results: List[Record]) -> None:
        NotImplementedError("This method should be implemented by the subclass")

    @classmethod
    @abstractmethod
    async def create_connector(cls, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService, connector_id: str) -> "BaseConnector":
        NotImplementedError("This method should be implemented by the subclass")

    @abstractmethod
    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for a specific filter field.

        Args:
            filter_key: The filter field name (e.g., "space_keys", "page_ids")
            page: Current page number for offset-based pagination
            limit: Number of items per page for pagination
            search: Optional search query to filter options
            cursor: Optional cursor for cursor-based pagination (API-specific)

        Returns:
            FilterOptionsResponse object with options and pagination metadata
        """
        raise NotImplementedError("This method should be implemented by the subclass")

    def get_app(self) -> App:
        return self.app

    def get_app_group(self) -> AppGroup:
        return self.app.get_app_group()

    def get_app_name(self) -> str:
        return self.app.get_app_name()

    def get_app_group_name(self) -> str:
        return self.app.get_app_group_name()

    def get_connector_id(self) -> str:
        return self.connector_id

    async def _load_creator_email(self) -> None:
        """
        Load and cache the creator's email for personal scope connectors.

        This is useful for connectors that need to create permissions for the creator.
        Call this in init() if needed (typically for personal scope connectors).
        """
        if self.scope == ConnectorScope.PERSONAL.value and self.created_by:
            try:
                async with self.data_store_provider.transaction() as tx_store:
                    user = await tx_store.get_user_by_user_id(self.created_by)
                    if user and user.get("email"):
                        self.creator_email = user.get("email")
                        self.logger.debug(f"Cached creator email: {self.creator_email}")
            except Exception as e:
                self.logger.warning(f"Could not get user for created_by {self.created_by}: {e}")

    def _connector_group_external_id(self) -> str:
        """Stable per-connector source id for the pseudo ConnectorGroup.

        Prefix kept aligned with the historical GitLab Personal scheme
        (``internal-{connector_id}``) so any existing rows / edges already
        keyed by that external id continue to resolve through the unified
        helper without a one-off migration.
        """
        return f"internal-{self.connector_id}"

    async def ensure_connector_group_permission(self) -> Optional[Permission]:
        """Upsert the creator's ``USER_APP_RELATION`` edge and a pseudo
        ``AppUserGroup`` named ``ConnectorGroup`` for this connector, then
        return a ``GROUP`` permission that points at the group.

        The group is *internal* — it does not exist in the upstream source. It is
        used by personal-scope connectors so that record-group/record access is
        granted to a single shared group whose only member is the connector creator,
        instead of being granted directly to the creator. New users that should
        access the data later can be added as members of this group without
        rewriting per-record ACLs.

        Two edges are written, in this order:

        1. ``users/<creator> -> apps/<connector_id>`` (``USER_APP_RELATION``):
           created by ``on_new_app_users``. Required by AQL traversals that walk
           ``OUTBOUND user USER_APP_RELATION`` to discover which connectors a
           user can reach (see ``arango_http_provider`` permission lookups).
           Without this edge, records synced under the ConnectorGroup are
           still unreachable from the App side of the graph for the creator.
        2. ``users/<creator> -> groups/<ConnectorGroup>`` (``PERMISSION``):
           created by ``on_new_user_groups``. This is the edge that backs the
           returned ``GROUP`` permission and is what ``_handle_record_permissions``
           resolves when stamping ACLs on record groups / records.

        - ``app_name`` / ``connector_id`` scope the group to this connector instance.
        - ``name`` is the shared display name (``"ConnectorGroup"``).
        - ``source_user_group_id`` is deterministic per connector
          (``internal-{connector_id}``, see ``_connector_group_external_id``),
          so the group is upserted in place across sync runs and
          ``Permission(GROUP, external_id=...)`` resolves back to the same node.

        Returns ``None`` (and does nothing) when ``self.creator_email`` is unset —
        without an identifiable creator there is no useful member to add and the
        group would be unreachable for any human user. ``USER_APP_RELATION``
        failures are logged and swallowed (the GROUP edge is the load-bearing
        one for permissions); only a failed group upsert returns ``None``.

        The resulting permission is cached on ``self._connector_group_permission``,
        so repeat calls within the same sync are no-ops.
        """
        if self._connector_group_permission is not None:
            return self._connector_group_permission

        if not self.creator_email:
            self.logger.warning(
                "Cannot create ConnectorGroup for connector %s: no creator email resolved",
                self.connector_id,
            )
            return None

        source_group_id = self._connector_group_external_id()
        org_id = self.data_entities_processor.org_id

        # Normalize ``connector_name`` to the ``Connectors`` enum: a few
        # subclasses store the string ``.value`` instead of the enum on
        # ``self.connector_name``. Pydantic would coerce on the way in, but
        # being explicit here also gives us a safe ``.value`` for the
        # description string below.
        app_name_enum = (
            self.connector_name
            if isinstance(self.connector_name, Connectors)
            else Connectors(self.connector_name)
        )

        pseudo_group = AppUserGroup(
            app_name=app_name_enum,
            connector_id=self.connector_id,
            source_user_group_id=source_group_id,
            name=INTERNAL_CONNECTOR_GROUP_NAME,
            org_id=org_id,
            description=(
                f"Internal group of users granted access to this {app_name_enum.value} "
                "connector instance"
            ),
        )

        # Lightweight AppUser shell — on_new_user_groups looks members up by
        # email against the PipesHub users collection (see
        # data_source_entities_processor.on_new_user_groups), so the
        # source_user_id only needs to be unique within this connector.
        creator_member = AppUser(
            app_name=app_name_enum,
            connector_id=self.connector_id,
            source_user_id=f"creator_{self.created_by or self.creator_email}",
            email=self.creator_email,
            full_name=self.creator_email,
            org_id=org_id,
            is_active=True,
        )

        # Step 1: ensure the USER -> APP edge (``USER_APP_RELATION``) exists.
        # ``on_new_app_users`` upserts the platform user row (no-op for an
        # existing creator) and writes the ``users/<key> -> apps/<connector_id>``
        # edge that downstream AQL traversals (e.g.
        # ``OUTBOUND user USER_APP_RELATION``) rely on to discover which
        # connectors a user can reach. Without this edge, records synced
        # under the ConnectorGroup are still unreachable from the App side
        # of the graph for the creator.
        #
        # Logged-and-swallowed: a missing App node (rare — the App is created
        # at connector registration on the Node.js side) should not block the
        # group upsert. Permissions still work via the GROUP edge below.
        try:
            await self.data_entities_processor.on_new_app_users([creator_member])
        except Exception as e:
            self.logger.warning(
                "USER_APP_RELATION upsert failed for connector %s creator %s: %s. "
                "Continuing with ConnectorGroup upsert.",
                self.connector_id,
                self.creator_email,
                e,
            )

        # Step 2: upsert the ConnectorGroup and the USER -> GROUP membership
        # edge. This is the edge that backs the GROUP permission returned
        # below and is consumed by ``_handle_record_permissions``.
        try:
            await self.data_entities_processor.on_new_user_groups(
                [(pseudo_group, [creator_member])]
            )
        except Exception as e:
            self.logger.error(
                "Failed to upsert ConnectorGroup for connector %s: %s",
                self.connector_id,
                e,
                exc_info=True,
            )
            return None

        self._connector_group_permission = Permission(
            entity_type=EntityType.GROUP,
            external_id=source_group_id,
            type=PermissionType.READ,
        )
        self.logger.info(
            "ConnectorGroup ensured for connector %s (members: %s)",
            self.connector_id,
            self.creator_email,
        )
        return self._connector_group_permission

    async def notify(
        self,
        type: NotificationType,
        severity: NotificationSeverity,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        recipient_user_ids: list[str] | None = None,
        recipient_roles: list[NotificationRecipientRole] | None = None,
    ) -> None:
        """Fire-and-forget: publish a user-visible connector notification to the broker."""
        svc = self._notification_service
        if not svc or not self.created_by:
            self.logger.debug(
                "notify skipped: no notification service or created_by "
                "associated with connector: %s, connector id: %s \n "
                "Info: self.created_by: %s, self.notification_service: %s",
                self.connector_name,
                self.connector_id,
                self.created_by,
                bool(self._notification_service),
            )
            return
        org_id = getattr(self.data_entities_processor, "org_id", None) or ""

        if self._suppress_notification(title, message, severity):
            return
        
        connector_type = self.connector_name.value if isinstance(self.connector_name, Connectors) else self.connector_name
        if payload and "redirect_link" in payload:
            redirect_link = payload["redirect_link"]
        else:
            redirect_link = DEFAULT_CONNECTOR_NOTIFICATION_LINK + f"{self.scope}/?connectorType={connector_type}"

        if not recipient_user_ids and not recipient_roles:
            recipient_user_ids = [self.created_by]

        async def _run() -> None:
            await svc.publish_notification(
                org_id=str(org_id),
                origin=NotificationOrigin.CONNECTOR,
                type=type,
                severity=severity,
                title=title,
                message=message,
                payload=payload,
                redirect_link=redirect_link,
                recipient_user_ids=recipient_user_ids,
                recipient_roles=recipient_roles,
            )

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_run())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        except RuntimeError:
            # No running loop (e.g. sync tests) — skip scheduling
            self.logger.debug("notify skipped: no running asyncio loop for connector: %s, connector id: %s", self.connector_name, self.connector_id)

    def _suppress_notification(self, title: str, message: str, severity: NotificationSeverity) -> bool:

        if severity in [NotificationSeverity.INFO, NotificationSeverity.SUCCESS]:
            return False

        key = f"{self.connector_id}:{title}:{message}"
        now = get_epoch_timestamp_in_ms()

        if key in self._notification_cache:
            next_allowed_time, backoff = self._notification_cache[key]
            if now < next_allowed_time:
                self.logger.debug(
                    "notification suppressed: \"%s\" already sent within backoff period for connector: %s, connector id: %s",
                    title,
                    self.connector_name,
                    self.connector_id
                )
                return True
            else:
                if now - next_allowed_time > MAX_NOTIFICATION_BACKOFF: # Backoff expired, reset to initial backoff
                    self._notification_cache[key] = (now + INITIAL_NOTIFICATION_BACKOFF, INITIAL_NOTIFICATION_BACKOFF)
                else:
                    new_next_allowed_time = now + backoff
                    new_backoff = min(backoff * 2, MAX_NOTIFICATION_BACKOFF)
                    self._notification_cache[key] = (new_next_allowed_time, new_backoff)
        else:
            self._notification_cache[key] = (now + INITIAL_NOTIFICATION_BACKOFF, INITIAL_NOTIFICATION_BACKOFF)

        return False

