import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    MimeTypes,
    OriginTypes,
    RecordRelations,
)
from app.config.constants.service import config_node_constants
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    MailRecord,
    Record,
    RecordGroup,
    RecordType,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms

ARANGO_NODE_ID_PARTS = 2 # ArangoDB node IDs are in format "collection/id"

# Permission hierarchy for comparing and upgrading permissions
# Higher number = higher permission level
PERMISSION_HIERARCHY = {
    "READER": 1,
    "COMMENTER": 2,
    "WRITER": 3,
    "OWNER": 4,
}

@dataclass
class RecordGroupWithPermissions:
    record_group: RecordGroup
    users: List[Tuple[AppUser, Permission]]
    user_groups: List[Tuple[AppUserGroup, Permission]]
    anyone_with_link: bool = False
    anyone_same_org: bool = False
    anyone_same_domain: bool = False

@dataclass
class UserGroupWithMembers:
    user_group: AppUserGroup
    users: List[Tuple[AppUser, Permission]]

class DataSourceEntitiesProcessor:
    ATTACHMENT_CONTAINER_TYPES = [
        RecordType.MAIL,
        RecordType.WEBPAGE,
        RecordType.CONFLUENCE_PAGE,
        RecordType.CONFLUENCE_BLOGPOST,
        RecordType.SHAREPOINT_PAGE,
    ]
    def __init__(self, logger, data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.data_store_provider: DataStoreProvider = data_store_provider
        self.config_service: ConfigurationService = config_service
        self.org_id = ""

    async def initialize(self) -> None:
        producer_config = await self.config_service.get_config(
            config_node_constants.KAFKA.value
        )

        # Ensure bootstrap_servers is a list
        bootstrap_servers = producer_config.get("brokers") or producer_config.get("bootstrap_servers")
        if isinstance(bootstrap_servers, str):
            bootstrap_servers = [server.strip() for server in bootstrap_servers.split(",")]

        kafka_producer_config = KafkaProducerConfig(
            bootstrap_servers=bootstrap_servers,
            client_id=producer_config.get("client_id", "connectors"),
        )
        self.messaging_producer: IMessagingProducer = MessagingFactory.create_producer(
            broker_type="kafka",
            logger=self.logger,
            config=kafka_producer_config,
        )
        await self.messaging_producer.initialize()
        async with self.data_store_provider.transaction() as tx_store:
            orgs = await tx_store.get_all_orgs()
            if not orgs:
                raise Exception("No organizations found in the database. Cannot initialize DataSourceEntitiesProcessor.")
            # Use backward-compatible field access
            self.org_id = orgs[0].get("id", orgs[0].get("_key"))

    def _create_placeholder_parent_record(
        self,
        parent_external_id: str,
        parent_record_type: RecordType,
        record: Record,
    ) -> Record:
        """
        Create a placeholder parent record based on the parent record type.

        Args:
            parent_external_id: External ID of the parent record
            parent_record_type: Type of the parent record
            record: The child record (for context like connector info)

        Returns:
            A placeholder Record instance of the appropriate type
        """
        base_params = {
            "org_id": self.org_id,
            "external_record_id": parent_external_id,
            "record_name": parent_external_id,  # Will be updated when real parent is synced
            "origin": OriginTypes.CONNECTOR.value,
            "connector_name": record.connector_name,
            "connector_id": record.connector_id,
            "record_type": parent_record_type,
            "record_group_type": record.record_group_type,
            "version": 0,
            "mime_type": MimeTypes.UNKNOWN.value,
        }

        # Map RecordType to appropriate Record class
        if parent_record_type == RecordType.FILE:
            file_params = {k: v for k, v in base_params.items() if k != "mime_type"}
            return FileRecord(
                **file_params,
                is_file=False,
                extension=None,
                mime_type=MimeTypes.FOLDER.value,
            )
        elif parent_record_type in [RecordType.WEBPAGE, RecordType.CONFLUENCE_PAGE,
                                     RecordType.CONFLUENCE_BLOGPOST, RecordType.SHAREPOINT_PAGE]:
            # All webpage-like types use WebpageRecord
            return WebpageRecord(**base_params)
        elif parent_record_type == RecordType.MAIL:
            return MailRecord(**base_params)
        elif parent_record_type == RecordType.TICKET:
            return TicketRecord(**base_params)
        elif parent_record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
            return CommentRecord(
                **base_params,
                author_source_id="",  # Will be updated when real parent is synced
            )
        else:
            raise ValueError(
                f"Unsupported parent record type: {parent_record_type.value}. for _handle_parent_record"
            )

    async def _handle_parent_record(self, record: Record, tx_store: TransactionStore) -> None:
        if record.parent_external_record_id:
            parent_record = await tx_store.get_record_by_external_id(
                connector_id=record.connector_id,
                external_id=record.parent_external_record_id
            )

            # Create placeholder parent record if not found (generic for all record types)
            if parent_record is None and record.parent_record_type:
                parent_record = self._create_placeholder_parent_record(
                    parent_external_id=record.parent_external_record_id,
                    parent_record_type=record.parent_record_type,
                    record=record,
                )
                await tx_store.batch_upsert_records([parent_record])

            if parent_record and isinstance(parent_record, Record):
                if (record.record_type == RecordType.FILE and record.parent_external_record_id and
                    record.parent_record_type in self.ATTACHMENT_CONTAINER_TYPES):
                    relation_type = RecordRelations.ATTACHMENT.value
                else:
                    relation_type = RecordRelations.PARENT_CHILD.value
                await tx_store.create_record_relation(parent_record.id, record.id, relation_type)

    async def _handle_record_group(self, record: Record, tx_store: TransactionStore) -> None:
        record_group = await tx_store.get_record_group_by_external_id(connector_id=record.connector_id,
                                                                      external_id=record.external_record_group_id)

        if record_group is None:
            # Create a new record group
            record_group = RecordGroup(
                external_group_id=record.external_record_group_id,
                name=record.external_record_group_id,
                group_type=record.record_group_type,
                connector_name=record.connector_name,
                connector_id=record.connector_id,
            )
            await tx_store.batch_upsert_record_groups([record_group])
            # Todo: Create a edge between the record group and the App

        if record_group:
            # Create a edge between the record and the record group if it doesn't exist
            await tx_store.create_record_group_relation(record.id, record_group.id)

            if record.inherit_permissions:
                await tx_store.create_inherit_permissions_relation_record_group(record.id, record_group.id)

    async def _handle_new_record(self, record: Record, tx_store: TransactionStore) -> None:
        # Set org_id for the record
        record.org_id = self.org_id
        self.logger.info("Upserting new record: %s", record.record_name)
        await tx_store.batch_upsert_records([record])

    async def _handle_updated_record(self, record: Record, existing_record: Record, tx_store: TransactionStore) -> None:
        # Set org_id for the record
        record.org_id = self.org_id
        self.logger.info("Updating existing record: %s, version %d -> %d",
                         record.record_name, existing_record.version, record.version)
        await tx_store.batch_upsert_records([record])

    async def _handle_record_permissions(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> None:
        record_permissions = []

        try:
            for permission in permissions:
                # Permission edges: Entity (User/Group) → Record
                to_id = record.id
                to_collection = CollectionNames.RECORDS.value
                from_id = None
                from_collection = None

                if permission.entity_type == EntityType.USER.value:
                    user = None
                    if permission.email:
                        user = await tx_store.get_user_by_email(permission.email)

                        # If user doesn't exist (external user), create them as inactive
                        if not user and permission.email:
                            user = await self._create_external_user(permission.email, record.connector_id, record.connector_name, tx_store)

                    if user:
                        from_id = user.id
                        from_collection = CollectionNames.USERS.value

                elif permission.entity_type == EntityType.GROUP.value:
                    user_group = None
                    if permission.external_id:
                        # Look up group by external_id
                        user_group = await tx_store.get_user_group_by_external_id(
                            connector_id=record.connector_id,
                            external_id=permission.external_id
                        )

                    if user_group:
                        from_id = user_group.id
                        from_collection = CollectionNames.GROUPS.value
                    else:
                        self.logger.warning(f"User group with external ID {permission.external_id} not found in database")
                        continue
                elif permission.entity_type == EntityType.ROLE.value:
                    user_role = None
                    if permission.external_id:
                        user_role = await tx_store.get_app_role_by_external_id(external_id=permission.external_id, connector_id=record.connector_id)
                    if user_role:
                        from_id = user_role.id
                        from_collection = CollectionNames.ROLES.value
                    else:
                        self.logger.warning(f"User role with external ID {permission.external_id} for {record.connector_name} and connector_id {record.connector_id} not found in database")
                        continue
                elif permission.entity_type == EntityType.ORG.value:
                    from_id = self.org_id
                    from_collection = CollectionNames.ORGS.value

                # elif permission.entity_type == EntityType.DOMAIN.value:
                #     domain = await tx_store.get_domain_by_external_id(permission.external_id)
                #     if domain:
                #         from_id = domain.id
                #         from_collection = CollectionNames.DOMAINS.value

                # elif permission.entity_type == EntityType.ANYONE.value:
                #     from_id = None  # Anyone doesn't have an ID
                #     from_collection = CollectionNames.ANYONE.value

                # elif permission.entity_type == EntityType.ANYONE_WITH_LINK.value:
                #     from_id = None  # Anyone with link doesn't have an ID
                #     from_collection = CollectionNames.ANYONE_WITH_LINK.value

                if from_id and from_collection:
                    record_permissions.append(permission.to_arango_permission(from_id, from_collection, to_id, to_collection))

            if record_permissions:
                await tx_store.batch_create_edges(
                    record_permissions, collection=CollectionNames.PERMISSION.value
                )
        except Exception as e:
            self.logger.error("Failed to create permission edge: %s", e)

    async def _create_external_user(self, email: str, connector_id: str, connector_name: str, tx_store) -> AppUser:
        """Create an external user record."""
        external_source_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))

        # Create external user record
        external_user = AppUser(
            app_name=connector_name,
            connector_id=connector_id,
            source_user_id=external_source_id,
            email=email,
            full_name=email.split('@')[0],
            is_active=False
        )

        # Save the external user
        await tx_store.batch_upsert_app_users([external_user])

        # Fetch the created user to get the ID
        user = await tx_store.get_user_by_email(email)

        self.logger.info(f"Created external user record for: {email}")
        return user

    async def on_updated_record_permissions(self, record: Record, permissions: List[Permission]) -> None:
        self.logger.info(f"Starting permission update for record: {record.record_name} ({record.id})")



        try:
            async with self.data_store_provider.transaction() as tx_store:
                # Step 1: Delete all existing permission edges that point TO this record.
                deleted_count = await tx_store.delete_edges_to(
                    to_id=record.id,
                    to_collection=CollectionNames.RECORDS.value,
                    collection=CollectionNames.PERMISSION.value
                )
                self.logger.info(f"Deleted {deleted_count} old permission edge(s) for record: {record.id}")

                # Step 2: Add the new permissions by reusing the existing helper method.
                if permissions:
                    self.logger.info(f"Adding {len(permissions)} new permission edge(s) for record: {record.id}")
                    await self._handle_record_permissions(record, permissions, tx_store)
                # if record comes with inherit permissions true create inherit permissions edge else check if inherit permissions edge exists and delete it
                if record.inherit_permissions:
                    record_group = await tx_store.get_record_group_by_external_id(connector_id=record.connector_id,
                                                                      external_id=record.external_record_group_id)

                    if record_group:
                        await tx_store.create_inherit_permissions_relation_record_group(record.id, record_group.id)

                if not record.inherit_permissions:
                    record_group = await tx_store.get_record_group_by_external_id(connector_id=record.connector_id,
                                                                      external_id=record.external_record_group_id)
                    if record_group:
                        # Delete the INHERIT_PERMISSIONS edge
                        await tx_store.delete_edge(
                            from_id=record.id,
                            from_collection=CollectionNames.RECORDS.value,
                            to_id=record_group.id,
                            to_collection=CollectionNames.RECORD_GROUPS.value,
                            collection=CollectionNames.INHERIT_PERMISSIONS.value
                        )
                else:
                    self.logger.info(f"No new permissions to add for record: {record.id}")

                self.logger.info(f"Successfully updated permissions for record: {record.id}")

        except Exception as e:
            self.logger.error(f"Failed to update permissions for record {record.id}: {e}", exc_info=True)
            raise

    async def _process_record(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> Optional[Record]:
        existing_record = await tx_store.get_record_by_external_id(connector_id=record.connector_id,
                                                                   external_id=record.external_record_id)

        if existing_record is None:
            self.logger.info("New record: %s", record)
            await self._handle_new_record(record, tx_store)
        else:
            record.id = existing_record.id
            # pass
            #check if revision Id is same as existing record
            if record.external_revision_id != existing_record.external_revision_id:
                await self._handle_updated_record(record, existing_record, tx_store)

        # Create a edge between the record and the parent record if it doesn't exist and if parent_record_id is provided
        await self._handle_parent_record(record, tx_store)

        # Create a edge between the record and the record group if it doesn't exist and if record_group_id is provided
        await self._handle_record_group(record, tx_store)

        # Create a edge between the base record and the specific record if it doesn't exist - isOfType - File, Mail, Message

        await self._handle_record_permissions(record, permissions, tx_store)
        #Todo: Check if record is updated, permissions are updated or content is updated
        #if existing_record:


        # Create record if it doesn't exist
        # Record download function
        # Create a permission edge between the record and the app with sync status if it doesn't exist
        if existing_record is None:
            return record

        return record

    async def on_new_records(self, records_with_permissions: List[Tuple[Record, List[Permission]]]) -> None:
        try:
            if not records_with_permissions:
                self.logger.warning("on_new_records received an empty list; skipping processing.")
                return

            records_to_publish = []

            async with self.data_store_provider.transaction() as tx_store:
                for record, permissions in records_with_permissions:
                    processed_record = await self._process_record(record, permissions, tx_store)

                    if processed_record:
                        records_to_publish.append(processed_record)

            if records_to_publish:
                for record in records_to_publish:
                    # Skip publishing indexing events for records with AUTO_INDEX_OFF status
                    if hasattr(record, 'indexing_status') and record.indexing_status == IndexingStatus.AUTO_INDEX_OFF.value:
                        self.logger.debug(
                            f"Skipping automatic indexing event for record {record.id} "
                            f"with AUTO_INDEX_OFF status"
                        )
                        continue

                    await self.messaging_producer.send_message(
                            "record-events",
                            {"eventType": "newRecord", "timestamp": get_epoch_timestamp_in_ms(), "payload": record.to_kafka_record()},
                            key=record.id
                        )
        except Exception as e:
            self.logger.error(f"Transaction on_new_records failed: {str(e)}")
            raise e


    async def on_record_content_update(self, record: Record) -> None:
        async with self.data_store_provider.transaction() as tx_store:
            processed_record = await self._process_record(record, [], tx_store)

            # Skip publishing update events for records with AUTO_INDEX_OFF status
            if hasattr(processed_record, 'indexing_status') and processed_record.indexing_status == IndexingStatus.AUTO_INDEX_OFF.value:
                self.logger.debug(
                    f"Skipping content update event for record {record.id} with AUTO_INDEX_OFF status"
                )
                return

            await self.messaging_producer.send_message(
                "record-events",
                {"eventType": "updateRecord", "timestamp": get_epoch_timestamp_in_ms(), "payload": processed_record.to_kafka_record()},
                key=record.id
            )

    async def on_record_metadata_update(self, record: Record) -> None:
        async with self.data_store_provider.transaction() as tx_store:
            existing_record = await tx_store.get_record_by_external_id(connector_id=record.connector_id,
                                                                   external_id=record.external_record_id)
            await self._handle_updated_record(record, existing_record, tx_store)

    async def on_record_deleted(self, record_id: str) -> None:
        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_record_by_key(record_id)

    async def reindex_existing_records(self, records: List[Record]) -> None:
        """
        Publish reindex events for existing records without DB operations.
        Used for reindexing functionality where records already exist in DB.
        This method publishes reindexRecord events to trigger re-indexing in the indexing service.

        Args:
            records: List of properly typed Record instances (FileRecord, MailRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            for record in records:
                payload = record.to_kafka_record()

                await self.messaging_producer.send_message(
                    "record-events",
                    {
                        "eventType": "reindexRecord",
                        "timestamp": get_epoch_timestamp_in_ms(),
                        "payload": payload
                    },
                    key=record.id
                )

            self.logger.info(f"Published reindex events for {len(records)} records")
        except Exception as e:
            self.logger.error(f"Failed to publish reindex events: {str(e)}")
            raise e

    async def on_new_record_groups(self, record_groups: List[Tuple[RecordGroup, List[Permission]]]) -> None:
        try:
            if not record_groups:
                self.logger.warning("on_new_record_groups received an empty list; skipping processing.")
                return

            async with self.data_store_provider.transaction() as tx_store:
                for record_group, permissions in record_groups:
                    record_group.org_id = self.org_id

                    self.logger.info(f"Processing record group: {record_group.name}")
                    existing_record_group = await tx_store.get_record_group_by_external_id(
                        connector_id=record_group.connector_id,
                        external_id=record_group.external_group_id
                    )

                    if existing_record_group is None:
                        record_group.id = str(uuid.uuid4())
                        self.logger.info(f"Creating new record group with id: {record_group.id}")
                    else:
                        record_group.id = existing_record_group.id
                        self.logger.info(f"Updating existing record group with id: {record_group.id}")
                        # Ensure update timestamp is fresh for the edge
                        record_group.updated_at = get_epoch_timestamp_in_ms()

                        # To Delete the previously existing edges to record group and create new permissions
                        await tx_store.delete_edges_to(
                            to_id=record_group.id,
                            to_collection=CollectionNames.RECORD_GROUPS.value,
                            collection=CollectionNames.PERMISSION.value
                        )

                    # 1. Upsert the record group document
                    await tx_store.batch_upsert_record_groups([record_group])

                    # 2. Create the BELONGS_TO edge for the organization
                    org_relation = {
                        "from_id": record_group.id,
                        "from_collection": CollectionNames.RECORD_GROUPS.value,
                        "to_id": self.org_id,
                        "to_collection": CollectionNames.ORGS.value,
                        "createdAtTimestamp": record_group.created_at,
                        "updatedAtTimestamp": record_group.updated_at,
                        "entityType": "ORGANIZATION",
                    }
                    self.logger.info(f"Creating BELONGS_TO edge for RecordGroup {record_group.id} to Org {self.org_id}")
                    await tx_store.batch_create_edges(
                        [org_relation], collection=CollectionNames.BELONGS_TO.value
                    )

                    # 3. Handle User and Group Permissions (from the passed 'permissions' list)
                    if record_group.parent_external_group_id:
                        parent_record_group = await tx_store.get_record_group_by_external_id(
                            connector_id=record_group.connector_id,
                            external_id=record_group.parent_external_group_id
                        )

                        if parent_record_group:
                            self.logger.info(f"Creating BELONGS_TO edge for RecordGroup '{record_group.name}' to parent '{parent_record_group.name}'")

                            # Define the edge document from child to parent RecordGroup
                            parent_relation = {
                                "from_id": record_group.id,
                                "from_collection": CollectionNames.RECORD_GROUPS.value,
                                "to_id": parent_record_group.id,
                                "to_collection": CollectionNames.RECORD_GROUPS.value,
                                "createdAtTimestamp": record_group.created_at,
                                "updatedAtTimestamp": record_group.updated_at,
                                "entityType": "KB",
                            }

                            # Create the edge using the same batch method
                            await tx_store.batch_create_edges(
                                [parent_relation], collection=CollectionNames.BELONGS_TO.value
                            )

                            if record_group.inherit_permissions:
                                inherit_relation = parent_relation.copy()
                                inherit_relation.pop("entityType", None)

                                await tx_store.batch_create_edges(
                                    [inherit_relation], collection=CollectionNames.INHERIT_PERMISSIONS.value
                                )
                            #if inherit records is false we need to remove the edge aswell
                        else:
                            self.logger.warning(
                                f"Could not find parent record group with external_id "
                                f"'{record_group.parent_external_group_id}' for child '{record_group.name}'"
                            )

                    # 4. Handle User and Group Permissions (from the passed 'permissions' list)
                    if not permissions:
                        continue

                    record_group_permissions = []
                    to_id = record_group.id
                    to_collection = CollectionNames.RECORD_GROUPS.value

                    for permission in permissions:
                        from_id = None
                        from_collection = None

                        if permission.entity_type == EntityType.USER:
                            user = None
                            if permission.email:
                                user = await tx_store.get_user_by_email(permission.email)

                            if user:
                                from_id = user.id
                                from_collection = CollectionNames.USERS.value
                            else:
                                self.logger.warning(f"Could not find user with email {permission.email} for RecordGroup permission.")

                        elif permission.entity_type == EntityType.GROUP:
                            user_group = None
                            if permission.external_id:
                                user_group = await tx_store.get_user_group_by_external_id(
                                    connector_id=record_group.connector_id,
                                    external_id=permission.external_id
                                )

                            if user_group:
                                from_id = user_group.id
                                from_collection = CollectionNames.GROUPS.value
                            else:
                                self.logger.warning(f"Could not find group with external_id {permission.external_id} for RecordGroup permission.")

                        elif permission.entity_type == EntityType.ROLE:
                            user_role = None
                            if permission.external_id:
                                user_role = await tx_store.get_app_role_by_external_id(
                                    connector_id=record_group.connector_id,
                                    external_id=permission.external_id
                                )

                            if user_role:
                                from_id = user_role.id
                                from_collection = CollectionNames.ROLES.value
                            else:
                                self.logger.warning(f"Could not find role with external_id {permission.external_id} for RecordGroup permission.")
                        # (The ORG case is no longer needed here as it's handled by BELONGS_TO)
                        # Update adding ORG permission to allow fetching of records via record groups
                        elif permission.entity_type == EntityType.ORG:
                            from_id = self.org_id
                            from_collection = CollectionNames.ORGS.value

                        if from_id and from_collection:
                            record_group_permissions.append(
                                permission.to_arango_permission(from_id, from_collection, to_id, to_collection)
                            )

                    # Batch create (upsert) all permission edges for this record group
                    if record_group_permissions:
                        self.logger.info(f"Creating/updating {len(record_group_permissions)} PERMISSION edges for RecordGroup {record_group.id}")
                        await tx_store.batch_create_edges(
                            record_group_permissions, collection=CollectionNames.PERMISSION.value
                        )

                    if record_group.parent_record_group_id:
                        await tx_store.create_record_groups_relation(record_group.id, record_group.parent_record_group_id)

        except Exception as e:
            self.logger.error(f"Transaction on_new_record_groups failed: {str(e)}")
            raise e

    async def update_record_group_name(self, folder_id: str, new_name: str, old_name: str = None, connector_id: str = None) -> None:
        """Update the name of an existing record group in the database."""
        try:
            async with self.data_store_provider.transaction() as tx_store:
                existing_group = await tx_store.get_record_group_by_external_id(
                    connector_id=connector_id,
                    external_id=folder_id
                )

                if not existing_group:
                    self.logger.warning(
                        f"Cannot rename record group: Group with external ID {folder_id} not found in database"
                    )
                    return

                existing_group.name = new_name
                existing_group.updated_at = get_epoch_timestamp_in_ms()

                await tx_store.batch_upsert_record_groups([existing_group])

                self.logger.info(
                    f"Successfully renamed record group {folder_id} from '{old_name}' to '{new_name}' "
                    f"(internal_id: {existing_group.id})"
                )

        except Exception as e:
            self.logger.error(f"Failed to update record group name for {folder_id}: {e}", exc_info=True)
            raise

    async def on_new_app_users(self, users: List[AppUser]) -> None:
        try:
            if not users:
                self.logger.warning("on_new_app_users received an empty list; skipping processing.")
                return

            async with self.data_store_provider.transaction() as tx_store:
                await tx_store.batch_upsert_app_users(users)

        except Exception as e:
            self.logger.error(f"Transaction on_new_users failed: {str(e)}")
            raise e

    async def on_new_user_groups(self, user_groups: List[Tuple[AppUserGroup, List[AppUser]]]) -> None:
        """
        Processes new user groups, upserts them, and creates permission edges.
        This follows the logic of 'on_new_record_groups'.
        """
        try:
            if not user_groups:
                self.logger.warning("on_new_user_groups received an empty list; skipping processing.")
                return

            async with self.data_store_provider.transaction() as tx_store:
                for user_group, members in user_groups:
                    # Set the org_id on the object, as it's needed for the doc
                    user_group.org_id = self.org_id

                    self.logger.info(f"Processing user group: {user_group.name} with id {user_group.id}")
                    self.logger.info(f"Processing user group permissions: {members}")

                    # Check if the user group already exists in the DB
                    existing_user_group = await tx_store.get_user_group_by_external_id(
                        connector_id=user_group.connector_id,
                        external_id=user_group.source_user_group_id
                    )

                    if existing_user_group is None:
                        # The ID is already set by default_factory, but we log
                        self.logger.info(f"Creating new user group with id: {user_group.id}")
                    else:
                        # Overwrite the new UUID with the existing one
                        user_group.id = existing_user_group.id
                        self.logger.info(f"Updating existing user group with id: {user_group.id}")
                        user_group.updated_at = get_epoch_timestamp_in_ms()

                        # To Delete the previously existing edges to user group and create new permissions
                        await tx_store.delete_edges_to(
                            to_id=user_group.id,
                            to_collection=CollectionNames.GROUPS.value,
                            collection=CollectionNames.PERMISSION.value
                        )

                    # 1. Upsert the user group document
                    # (This uses batch_upsert_user_groups and the to_arango... method)
                    await tx_store.batch_upsert_user_groups([user_group])


                    user_group_permissions = []
                    # Set the 'to' side of the edge to be this user group
                    to_id = user_group.id
                    to_collection = CollectionNames.GROUPS.value

                    for member in members:
                        user = None
                        if member.email:
                            # Find the user's internal DB ID
                            user = await tx_store.get_user_by_email(member.email)

                        if not user:
                            self.logger.warning(f"Could not find user with email {member.email} for UserGroup permission.")
                            continue

                        permission = Permission(
                            external_id=member.id,
                            email=member.email,
                            type=PermissionType.READ,
                            entity_type=EntityType.USER
                        )
                        from_id = user.id
                        from_collection = CollectionNames.USERS.value

                        user_group_permissions.append(
                            permission.to_arango_permission(from_id, from_collection, to_id, to_collection)
                        )

                    # Batch create (upsert) all permission edges for this user group
                    if user_group_permissions:
                        self.logger.info(f"Creating/updating {len(user_group_permissions)} PERMISSION edges for UserGroup {user_group.id}")
                        await tx_store.batch_create_edges(
                            user_group_permissions, collection=CollectionNames.PERMISSION.value
                        )

        except Exception as e:
            self.logger.error(f"Transaction on_new_user_groups failed: {str(e)}")
            raise e

    async def on_new_app_roles(self, roles: List[Tuple[AppRole, List[AppUser]]]) -> None:
        """
        Processes new app roles, upserts them, and creates permission edges
        from users to these roles.
        """
        try:
            if not roles:
                self.logger.warning("on_new_app_roles received an empty list; skipping processing.")
                return

            async with self.data_store_provider.transaction() as tx_store:
                for role, members in roles:
                    # Set the org_id on the object, as it's needed for the doc
                    role.org_id = self.org_id

                    self.logger.info(f"Processing app role: {role.name}")
                    self.logger.info(f"Processing role members: {members}")

                    # Check if the app role already exists in the DB
                    existing_app_role = await tx_store.get_app_role_by_external_id(
                        connector_id=role.connector_id,
                        external_id=role.source_role_id
                    )

                    if existing_app_role is None:
                        # The ID is already set by default_factory, but we log
                        self.logger.info(f"Creating new app role with id: {role.id}")
                    else:
                        # Overwrite the new UUID with the existing one
                        role.id = existing_app_role.id
                        self.logger.info(f"Updating existing app role with id: {role.id}")
                        role.updated_at = get_epoch_timestamp_in_ms()

                        # To Delete the previously existing edges to app role and create new permissions
                        await tx_store.delete_edges_to(
                            to_id=role.id,
                            to_collection=CollectionNames.ROLES.value,
                            collection=CollectionNames.PERMISSION.value
                        )

                    # 1. Upsert the app role document
                    await tx_store.batch_upsert_app_roles([role])


                    role_permissions = []
                    # Set the 'to' side of the edge to be this role
                    to_id = role.id
                    to_collection = CollectionNames.ROLES.value

                    for member in members:
                        user = None
                        if member.email:
                            # Find the user's internal DB ID
                            user = await tx_store.get_user_by_email(member.email)

                        if not user:
                            self.logger.warning(f"Could not find user with email {member.email} for AppRole permission.")
                            continue

                        permission = Permission(
                            external_id=member.id,
                            email=member.email,
                            type=PermissionType.READ,
                            entity_type=EntityType.USER
                        )
                        from_id = user.id
                        from_collection = CollectionNames.USERS.value

                        role_permissions.append(
                            permission.to_arango_permission(from_id, from_collection, to_id, to_collection)
                        )

                    # Batch create (upsert) all permission edges for this role
                    if role_permissions:
                        self.logger.info(f"Creating/updating {len(role_permissions)} PERMISSION edges for AppRole {role.id}")
                        await tx_store.batch_create_edges(
                            role_permissions, collection=CollectionNames.PERMISSION.value
                        )

        except Exception as e:
            self.logger.error(f"Transaction on_new_app_roles failed: {str(e)}")
            raise e

    async def on_new_app(self, app: App) -> None:
        pass

    async def on_new_app_group(self, app_group: AppGroup) -> None:
        pass


    async def get_all_active_users(self) -> List[User]:
        async with self.data_store_provider.transaction() as tx_store:
            return await tx_store.get_users(self.org_id, active=True)

    async def get_all_app_users(self, connector_id: str) -> List[AppUser]:
        async with self.data_store_provider.transaction() as tx_store:
            return await tx_store.get_app_users(self.org_id, connector_id)

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        async with self.data_store_provider.transaction() as tx_store:
            record = await tx_store.get_record_by_external_id(connector_id=connector_id, external_id=external_record_id)
            return record

    async def on_user_group_member_removed(
        self,
        external_group_id: str,
        user_email: str,
        connector_id: str
    ) -> bool:

        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user by email
                user = await tx_store.get_user_by_email(user_email)
                if not user:
                    self.logger.warning(
                        f"Cannot remove member from group {external_group_id}: "
                        f"User with email {user_email} not found in database"
                    )
                    return False

                # 2. Look up the user group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_id=connector_id,
                    external_id=external_group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot remove member from group: "
                        f"Group with external ID {external_group_id} not found in database"
                    )
                    return False

                # 3. Delete the permission edge
                edge_deleted = await tx_store.delete_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=user_group.id,
                    to_collection=CollectionNames.GROUPS.value,
                    collection=CollectionNames.PERMISSION.value
                )

                if edge_deleted:
                    self.logger.info(
                        f"Successfully removed user {user_email} from group {user_group.name} "
                        f"(external_id: {external_group_id})"
                    )
                    return True
                else:
                    self.logger.warning(
                        f"No permission edge found between user {user_email} "
                        f"and group {user_group.name} (external_id: {external_group_id})"
                    )
                    return False

        except Exception as e:
            self.logger.error(
                f"Failed to remove user {user_email} from group {external_group_id}: {str(e)}",
                exc_info=True
            )
            return False

    async def on_user_group_member_added(
        self,
        external_group_id: str,
        user_email: str,
        permission_type: PermissionType,
        connector_id: str
    ) -> bool:
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user by email
                user = await tx_store.get_user_by_email(user_email)
                if not user:
                    self.logger.warning(
                        f"Cannot add member to group {external_group_id}: "
                        f"User with email {user_email} not found in database"
                    )
                    return False

                # 2. Look up the user group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_id=connector_id,
                    external_id=external_group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot add member to group: "
                        f"Group with external ID {external_group_id} not found in database"
                    )
                    return False

                # 3. Check if permission edge already exists
                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=user_group.id,
                    to_collection=CollectionNames.GROUPS.value,
                    collection=CollectionNames.PERMISSION.value
                )
                if existing_edge:
                    self.logger.info(f"Permission edge already exists between {user_email} and group {user_group.name}")
                    return False

                # 4. Create the permission object (external_id is not used when storing in arango)
                permission = Permission(
                    external_id=user.id,
                    email=user_email,
                    type=permission_type,
                    entity_type=EntityType.GROUP
                )

                # 5. Create new permission edge since it doesn't exist
                permission_edge = permission.to_arango_permission(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=user_group.id,
                    to_collection=CollectionNames.GROUPS.value
                )

                await tx_store.batch_create_edges(
                    [permission_edge],
                    collection=CollectionNames.PERMISSION.value
                )

                self.logger.info(
                    f"Successfully added user {user_email} to group {user_group.name} "
                    f"(external_id: {external_group_id}) with permission {permission_type}"
                )
                return True

        except Exception as e:
            self.logger.error(
                f"Failed to add user {user_email} to group {external_group_id}: {str(e)}",
                exc_info=True
            )
            return False

    async def on_user_group_deleted(
        self,
        external_group_id: str,
        connector_id: str
    ) -> bool:
        """
        Delete a user group and all its associated edges from the database.

        Args:
            external_group_id: The external ID of the group from the source system
            connector_id: The ID of the connector (e.g., 'DROPBOX')

        Returns:
            bool: True if the group was successfully deleted, False otherwise
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_id=connector_id,
                    external_id=external_group_id
                )

                if not user_group:
                    self.logger.warning(
                        f"Cannot delete group: Group with external ID {external_group_id} not found in database"
                    )
                    return False

                group_internal_id = user_group.id
                group_name = user_group.name

                self.logger.info(f"Deleting user group: {group_name} (internal_id: {group_internal_id})")

                #Delete the node and edges
                await tx_store.delete_nodes_and_edges([group_internal_id], CollectionNames.GROUPS.value)

                self.logger.info(
                    f"Successfully deleted user group {group_name} "
                    f"(external_id: {external_group_id}, internal_id: {group_internal_id}) "
                    f"and all associated edges"
                )
                return True

        except Exception as e:
            self.logger.error(
                f"Failed to delete user group {external_group_id}: {str(e)}",
                exc_info=True
            )
            return False

    async def delete_user_group_by_id(self, group_id: str) -> None:
        """
        Delete a user group by its internal ID, including all associated edges.

        Args:
            group_id: The internal ID of the user group to delete
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                await tx_store.delete_user_group_by_id(group_id)
                self.logger.info(f"Successfully deleted user group with ID: {group_id}")
        except Exception as e:
            self.logger.error(f"Failed to delete user group {group_id}: {str(e)}",exc_info=True)
            raise

    async def migrate_group_permissions_to_user(
        self,
        group_id: str,
        user_email: str,
        connector_id: str,
        tx_store: Optional[TransactionStore] = None
    ) -> None:
        """
        Migrate all permissions from a group to a user.

        This is a generic method that can be used by any connector to transfer
        permissions from a group to a user. It handles:
        - Getting all permission edges from the group
        - Checking for existing user permissions (duplicates)
        - Upgrading permissions when needed (e.g., READER → WRITER)
        - Batch creating new permission edges

        Args:
            group_id: The internal ID of the group to migrate permissions from
            user_email: Email of the user to migrate permissions to
            connector_id: Connector ID for logging
            tx_store: Optional transaction store to participate in caller's transaction.
                     If not provided, a new transaction will be created.
        """
        # If no transaction provided, create one and recursively call with it
        if tx_store is None:
            async with self.data_store_provider.transaction() as tx_store:
                return await self.migrate_group_permissions_to_user(
                    group_id, user_email, connector_id, tx_store
                )

        # Get the user object
        user = await tx_store.get_user_by_email(user_email)
        if not user:
            self.logger.warning(
                f"User {user_email} not found in users collection, "
                f"cannot migrate permissions. Skipping."
            )
            return

        # Get all permission edges FROM the group
        group_node_id = f"{CollectionNames.GROUPS.value}/{group_id}"
        permission_edges = await tx_store.get_edges_from_node(
            from_node_id=group_node_id,
            edge_collection=CollectionNames.PERMISSION.value
        )

        if not permission_edges:
            self.logger.debug(f"No permissions found for group {group_id}")
            return

        migrated_count = 0
        skipped_count = 0
        new_permission_edges = []

        # Process each permission edge
        for edge in permission_edges:
            try:
                target_node_id = edge.get("_to")
                if not target_node_id:
                    continue

                # Extract target ID and collection from _to
                target_parts = target_node_id.split("/", 1)
                if len(target_parts) != ARANGO_NODE_ID_PARTS:
                    continue

                target_collection, target_id = target_parts

                # Get permission type from edge
                role_str = edge.get("role", "READER")
                try:
                    permission_type = PermissionType(role_str)
                except ValueError:
                    permission_type = PermissionType.READ  # Default fallback

                # Check if user already has permission to this target
                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=target_id,
                    to_collection=target_collection,
                    collection=CollectionNames.PERMISSION.value
                )

                if existing_edge:
                    # User already has permission, check if we need to upgrade it
                    existing_role = existing_edge.get("role", "READER")
                    existing_role_level = PERMISSION_HIERARCHY.get(existing_role, 0)
                    new_role_level = PERMISSION_HIERARCHY.get(permission_type.value, 0)

                    if new_role_level > existing_role_level:
                        # Delete old edge and create new one with upgraded permission
                        await tx_store.delete_edge(
                            from_id=user.id,
                            from_collection=CollectionNames.USERS.value,
                            to_id=target_id,
                            to_collection=target_collection,
                            collection=CollectionNames.PERMISSION.value
                        )

                        # Create new edge with upgraded permission
                        permission = Permission(
                            email=user_email,
                            type=permission_type,
                            entity_type=EntityType.USER
                        )
                        edge_data = permission.to_arango_permission(
                            from_id=user.id,
                            from_collection=CollectionNames.USERS.value,
                            to_id=target_id,
                            to_collection=target_collection
                        )
                        new_permission_edges.append(edge_data)
                        migrated_count += 1
                        self.logger.debug(
                            f"Upgraded permission for user {user_email} to {target_node_id} "
                            f"(from {existing_role} to {permission_type.value})"
                        )
                    else:
                        skipped_count += 1
                        self.logger.debug(
                            f"User {user_email} already has permission to {target_node_id} "
                            f"(existing: {existing_role}, group: {permission_type.value}), skipping"
                        )
                else:
                    # Create new permission edge for user (batch create later)
                    permission = Permission(
                        email=user_email,
                        type=permission_type,
                        entity_type=EntityType.USER
                    )

                    edge_data = permission.to_arango_permission(
                        from_id=user.id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=target_id,
                        to_collection=target_collection
                    )

                    new_permission_edges.append(edge_data)
                    migrated_count += 1

            except Exception as e:
                self.logger.warning(
                    f"Failed to process permission edge {edge.get('_key', 'unknown')} "
                    f"for user {user_email}: {e}",
                    exc_info=True
                )
                continue

        # Batch create all new permission edges
        if new_permission_edges:
            await tx_store.batch_create_edges(
                new_permission_edges,
                collection=CollectionNames.PERMISSION.value
            )

        if migrated_count > 0 or skipped_count > 0:
            self.logger.info(
                f"✅ Permission migration complete for user {user_email}: "
                f"migrated {migrated_count}, skipped {skipped_count} duplicates"
            )

    async def migrate_group_to_user_by_external_id(
        self,
        group_external_id: str,
        user_email: str,
        connector_id: str
    ) -> None:
        """
        Migrate permissions from a group to a user and delete the group.
        This is a convenience method that handles the entire flow atomically.

        This method:
        1. Finds the group by external ID
        2. Migrates all permissions from group to user
        3. Deletes the group
        All in a single transaction.

        Args:
            group_external_id: External ID of the group to migrate from
            user_email: Email of the user to migrate permissions to
            connector_id: Connector ID
        """
        async with self.data_store_provider.transaction() as tx_store:
            # Find the group by external ID
            group = await tx_store.get_user_group_by_external_id(
                connector_id=connector_id,
                external_id=group_external_id
            )

            if not group:
                self.logger.debug(
                    f"Group with external ID {group_external_id} not found for connector {connector_id}"
                )
                return

            self.logger.info(
                f"Migrating group '{group.name}' ({group.id}) to user '{user_email}'"
            )

            # Migrate permissions (using the same transaction)
            await self.migrate_group_permissions_to_user(
                group_id=group.id,
                user_email=user_email,
                connector_id=connector_id,
                tx_store=tx_store
            )

            # Delete the group (this will also delete all its edges)
            await tx_store.delete_user_group_by_id(group.id)

            self.logger.info(f"✅ Completed migration and deleted group '{group.name}'")

    async def on_app_role_deleted(
        self,
        external_role_id: str,
        connector_id: str
    ) -> bool:
        """
        Delete an app role and all its associated edges from the database.

        Args:
            external_role_id: The external ID of the role from the source system
            connector_id: The instance ID of the connector

        Returns:
            bool: True if the role was successfully deleted, False otherwise
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the app role by external ID
                app_role = await tx_store.get_app_role_by_external_id(
                    connector_id=connector_id,
                    external_id=external_role_id
                )

                if not app_role:
                    self.logger.warning(
                        f"Cannot delete role: Role with external ID {external_role_id} not found in database"
                    )
                    return False

                role_internal_id = app_role.id
                role_name = app_role.name

                self.logger.info(f"Deleting app role: {role_name} (internal_id: {role_internal_id})")

                # Delete the node and all associated edges
                await tx_store.delete_nodes_and_edges([role_internal_id], CollectionNames.ROLES.value)

                self.logger.info(
                    f"Successfully deleted app role {role_name} "
                    f"(external_id: {external_role_id}, internal_id: {role_internal_id}) "
                    f"and all associated edges"
                )
                return True

        except Exception as e:
            self.logger.error(
                f"Failed to delete app role {external_role_id}: {str(e)}",
                exc_info=True
            )
            return False

    async def on_record_group_deleted(
        self,
        external_group_id: str,
        connector_id: str
    ) -> bool:
        """
        Delete a record group and all its associated edges from the database.

        Args:
            external_group_id: The external ID of the group from the source system.
            connector_id: The ID of the connector (e.g., 'DROPBOX').

        Returns:
            bool: True if the group was successfully deleted, False otherwise.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Find the record group by its external ID
                record_group = await tx_store.get_record_group_by_external_id(
                    connector_id=connector_id,
                    external_id=external_group_id
                )

                if not record_group:
                    self.logger.warning(
                        f"Cannot delete record group: Group with external ID {external_group_id} not found."
                    )
                    return False

                record_group_internal_id = record_group.id
                record_group_name = record_group.name

                self.logger.info(
                    f"Deleting record group: '{record_group_name}' (internal_id: {record_group_internal_id})"
                )

                # 2. Atomically delete the group node and all its connected edges
                await tx_store.delete_nodes_and_edges(
                    [record_group_internal_id], CollectionNames.RECORD_GROUPS.value
                )

                self.logger.info(
                    f"Successfully deleted record group '{record_group_name}' "
                    f"(external_id: {external_group_id}) and its edges."
                )
                return True

        except Exception as e:
            self.logger.error(
                f"Failed to delete record group with external ID {external_group_id}: {str(e)}",
                exc_info=True
            )
            return False


    async def _delete_group_organization_edges(self, tx_store, group_internal_id: str) -> None:
        """Delete BELONGS_TO edges between group and organization."""
        try:
            # Delete the BELONGS_TO edge from group to organization
            edge_deleted = await tx_store.delete_edge(
                from_id=group_internal_id,
                from_collection=CollectionNames.GROUPS.value,
                to_id=self.org_id,
                to_collection=CollectionNames.ORGS.value,
                collection=CollectionNames.BELONGS_TO.value
            )

            if edge_deleted:
                self.logger.info(f"Deleted BELONGS_TO edge from group {group_internal_id} to org {self.org_id}")
            else:
                self.logger.debug(f"No BELONGS_TO edge found from group {group_internal_id} to org")

        except Exception as e:
            self.logger.error(f"Error deleting organization edges for group {group_internal_id}: {e}")

    #IMPORTANT: DO NOT USE THIS METHOD
    #TODO: When an user is delelted from a connetor we need to delete the userAppRelation b/w the app and user
    # async def on_user_removed(
    #     self,
    #     user_email: str,
    #     connector_name: str
    # ) -> bool:
    #     """
    #     Delete a user and all its associated edges from the database.

    #     Args:
    #         user_email: The email of the user to be removed
    #         connector_name: The name of the connector (e.g., 'DROPBOX')

    #     Returns:
    #         bool: True if the user was successfully deleted, False otherwise
    #     """
    #     try:
    #         async with self.data_store_provider.transaction() as tx_store:
    #             # 1. Look up the user by email
    #             user = await tx_store.get_user_by_email(user_email)

    #             if not user:
    #                 self.logger.warning(
    #                     f"Cannot delete user: User with email {user_email} not found in database"
    #                 )
    #                 return False

    #             if user.is_active:
    #                 self.logger.warning(
    #                     f"Cannot delete user: User with email {user_email} is still active"
    #                 )
    #                 return False

    #             user_internal_id = user.id
    #             user_name = user.full_name

    #             self.logger.info(f"Deleting user: {user_name} ({user_email}, internal_id: {user_internal_id})")

    #             # Delete the node and edges
    #             await tx_store.delete_nodes_and_edges([user_internal_id], CollectionNames.USERS.value)

    #             self.logger.info(
    #                 f"Successfully deleted user {user_name} "
    #                 f"(email: {user_email}, internal_id: {user_internal_id}) "
    #                 f"and all associated edges"
    #             )
    #             return True

    #     except Exception as e:
    #         self.logger.error(
    #             f"Failed to delete user {user_email}: {str(e)}",
    #             exc_info=True
    #         )
    #         return False
