import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, MimeTypes, OriginTypes
from app.config.constants.service import config_node_constants
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.core.interfaces.connector.apps import App, AppGroup
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    Record,
    RecordGroup,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.models.permission import EntityType, Permission, PermissionType

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
            self.org_id = orgs[0]["_key"]

    async def _handle_parent_record(self, record: Record, tx_store: TransactionStore) -> None:
        if record.parent_external_record_id:
            parent_record = await tx_store.get_record_by_external_id(connector_name=record.connector_name,
                                                                     external_id=record.parent_external_record_id)

            if parent_record is None and record.parent_record_type is RecordType.FILE and record.record_type is RecordType.FILE:
                # Create a new parent record
                parent_record = FileRecord(
                    org_id=self.org_id,
                    external_record_id=record.parent_external_record_id,
                    record_name=record.parent_external_record_id,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=record.connector_name,
                    record_type=record.parent_record_type,
                    record_group_type=record.record_group_type,
                    version=0,
                    is_file=False,
                    extension=None,
                    mime_type=MimeTypes.FOLDER.value,
                )
                await tx_store.batch_upsert_records([parent_record])

            if parent_record and isinstance(parent_record, Record):
                if (record.record_type == RecordType.FILE and
                    record.parent_external_record_id and
                    record.parent_record_type == RecordType.MAIL):
                    relation_type = 'ATTACHMENT'
                else:
                    relation_type = 'PARENT_CHILD'
                await tx_store.create_record_relation(parent_record.id, record.id, relation_type)

    async def _handle_record_group(self, record: Record, tx_store: TransactionStore) -> None:
        record_group = await tx_store.get_record_group_by_external_id(connector_name=record.connector_name,
                                                                      external_id=record.external_record_group_id)

        if record_group is None:
            # Create a new record group
            record_group = RecordGroup(
                external_group_id=record.external_record_group_id,
                name=record.external_record_group_id,
                group_type=record.record_group_type,
                connector_name=record.connector_name,
            )
            await tx_store.batch_upsert_record_groups([record_group])
            # Todo: Create a edge between the record group and the App

        if record_group:
            # Create a edge between the record and the record group if it doesn't exist
            await tx_store.create_record_group_relation(record.id, record_group.id)

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
                from_collection = None
                to_collection = f"{CollectionNames.RECORDS.value}/{record.id}"
                
                if permission.entity_type == EntityType.USER.value:
                    user = None
                    if permission.email:
                        user = await tx_store.get_user_by_email(permission.email)

                        # If user doesn't exist (external user), create them as inactive
                        if not user and permission.email:
                            user = await self._create_external_user(permission.email, record.connector_name, tx_store)
                    if user:
                        from_collection = f"{CollectionNames.USERS.value}/{user.id}"
                elif permission.entity_type == EntityType.GROUP.value:
                    if permission.external_id:
                        user_group = await tx_store.get_user_group_by_external_id(external_id=permission.external_id, connector_name=record.connector_name)
                    # else:
                    #     user_group = await tx_store.get_user_group_by_email(email=permission.email, connector_name=record.connector_name)

                    if user_group:
                        from_collection = f"{CollectionNames.GROUPS.value}/{user_group.id}"

                # if permission.entity_type == EntityType.ORG.value:
                #     org = await self.data_store.get_org_by_external_id(permission.external_id)
                #     if org:
                #         from_collection = f"{CollectionNames.ORGS.value}/{org.id}"

                # if permission.entity_type == EntityType.DOMAIN.value:
                #     domain = await self.data_store.get_domain_by_external_id(permission.external_id)
                #     if domain:
                #         from_collection = f"{CollectionNames.DOMAINS.value}/{domain.id}"

                # if permission.entity_type == EntityType.ANYONE.value:
                #     from_collection = f"{CollectionNames.ANYONE.value}"

                # if permission.entity_type == EntityType.ANYONE_WITH_LINK.value:
                #     from_collection = f"{CollectionNames.ANYONE_WITH_LINK.value}"

                if from_collection:
                    record_permissions.append(permission.to_arango_permission(from_collection, to_collection))

            if record_permissions:
                await tx_store.batch_create_edges(
                    record_permissions, collection=CollectionNames.PERMISSION.value
                )
        except Exception as e:
            self.logger.error("Failed to create permission edge: %s", e)
    
    async def _create_external_user(self, email: str, connector_name: str, tx_store) -> AppUser:
        """Create an external user record."""
        external_source_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, email))

        # Create external user record
        external_user = AppUser(
            app_name=connector_name,
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

    async def on_updated_record_permissions(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> None:
        self.logger.info(f"Starting permission update for record: {record.record_name} ({record.id})")
    

        record_node_id = f"{CollectionNames.RECORDS.value}/{record.id}"

        try:
            # Step 1: Delete all existing permission edges that point TO this record.
            deleted_count = await tx_store.delete_edges_to(
                to_key=record_node_id,
                collection=CollectionNames.PERMISSION.value
            )
            self.logger.info(f"Deleted {deleted_count} old permission edge(s) for record: {record.id}")

            # Step 2: Add the new permissions by reusing the existing helper method.
            if permissions:
                self.logger.info(f"Adding {len(permissions)} new permission edge(s) for record: {record.id}")
                await self._handle_record_permissions(record, permissions, tx_store)
            else:
                self.logger.info(f"No new permissions to add for record: {record.id}")

            self.logger.info(f"Successfully updated permissions for record: {record.id}")

        except Exception as e:
            self.logger.error(f"Failed to update permissions for record {record.id}: {e}", exc_info=True)
            raise

    async def _process_record(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> Optional[Record]:
        existing_record = await tx_store.get_record_by_external_id(connector_name=record.connector_name,
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
            self.logger.info(f"on_new_records for: {records_with_permissions}")
            records_to_publish = []

            async with self.data_store_provider.transaction() as tx_store:
                for record, permissions in records_with_permissions:
                    processed_record = await self._process_record(record, permissions, tx_store)
                    
                    if processed_record:
                        records_to_publish.append(processed_record)

            if records_to_publish:
                for record in records_to_publish:
                    await self.messaging_producer.send_message(
                            "record-events",
                            {"eventType": "newRecord", "timestamp": get_epoch_timestamp_in_ms(), "payload": record.to_kafka_record()},
                            key=record.id
                        )
        except Exception as e:
            self.logger.error(f"Transaction on_new_records failed: {str(e)}")
            raise e


    async def on_record_content_update(self, record: Record, tx_store: TransactionStore) -> None:
        processed_record = await self._process_record(record, [], tx_store)
        await self.messaging_producer.send_message(
                "record-events",
                {"eventType": "updateRecord", "timestamp": get_epoch_timestamp_in_ms(), "payload": processed_record.to_kafka_record()},
                key=record.id
            )
        
    async def on_record_metadata_update(self, record: Record, tx_store: TransactionStore) -> None:
        pass

    async def on_record_deleted(self, record_id: str) -> None:
        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_record_by_key(record_id)
            print("should be deleted by now !! ")
    
    
    #- Create a permission edge between the record group and the org if it doesn't exist
    #- Create a permission edge between the record group and the user if it doesn't exist
    #  Create a permission edge between the record group and the user group if it doesn't exist
    #! Create a edge between the record group and the app with sync status if it doesn't exist
    #  Cleanup this code    
    async def on_new_record_groups(self, record_groups: List[Tuple[RecordGroup, List[Permission]]]) -> None:
        try:
            async with self.data_store_provider.transaction() as tx_store:
                for record_group, permissions in record_groups:
                    record_group.org_id = self.org_id

                    self.logger.info(f"Processing record group: {record_group.name}")
                    existing_record_group = await tx_store.get_record_group_by_external_id(
                        connector_name=record_group.connector_name,
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

                    # 1. Upsert the record group document
                    await tx_store.batch_upsert_record_groups([record_group])

                    # 2. Create the BELONGS_TO edge for the organization
                    org_relation = {
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{record_group.id}",
                        "_to": f"{CollectionNames.ORGS.value}/{self.org_id}",
                        "createdAtTimestamp": record_group.created_at,
                        "updatedAtTimestamp": record_group.updated_at,
                        "entityType": "ORGANIZATION",
                    }
                    self.logger.info(f"Creating BELONGS_TO edge for RecordGroup {record_group.id} to Org {self.org_id}")
                    await tx_store.batch_create_edges(
                        [org_relation], collection=CollectionNames.BELONGS_TO.value
                    )

                    # 3. Handle User and Group Permissions (from the passed 'permissions' list)
                    if not permissions:
                        continue

                    record_group_permissions = []
                    to_collection = f"{CollectionNames.RECORD_GROUPS.value}/{record_group.id}"

                    for permission in permissions:
                        from_collection = None
                        
                        if permission.entity_type == EntityType.USER:
                            user = None
                            if permission.email:
                                user = await tx_store.get_user_by_email(permission.email)
                            
                            if user:
                                from_collection = f"{CollectionNames.USERS.value}/{user.id}"
                            else:
                                self.logger.warning(f"Could not find user with email {permission.email} for RecordGroup permission.")
                        
                        elif permission.entity_type == EntityType.GROUP:
                            user_group = None
                            if permission.external_id:
                                user_group = await tx_store.get_user_group_by_external_id(
                                    connector_name=record_group.connector_name,
                                    external_id=permission.external_id
                                )
                            
                            if user_group:
                                from_collection = f"{CollectionNames.GROUPS.value}/{user_group.id}"
                            else:
                                self.logger.warning(f"Could not find group with external_id {permission.external_id} for RecordGroup permission.")
                        
                        # (The ORG case is no longer needed here as it's handled by BELONGS_TO)

                        if from_collection:
                            record_group_permissions.append(
                                permission.to_arango_permission(from_collection, to_collection)
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

    async def on_new_app_users(self, users: List[AppUser]) -> None:
        try:
            async with self.data_store_provider.transaction() as tx_store:

                # Get all users from the database(Active and Inactive)
                existing_users = await tx_store.get_users(self.org_id, active=False)
                existing_user_emails = {existing_user.get("email") for existing_user in existing_users if existing_user is not None}
                for user in users:
                    self.logger.info(f"Processing user: {user}")

                    if user.email not in existing_user_emails:
                        await tx_store.batch_upsert_app_users([user])


        except Exception as e:
            self.logger.error(f"Transaction on_new_users failed: {str(e)}")
            raise e

    async def on_new_user_groups(self, user_groups: List[Tuple[AppUserGroup, List[Permission]]]) -> None:
        """
        Processes new user groups, upserts them, and creates permission edges.
        This follows the logic of 'on_new_record_groups'.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                for user_group, permissions in user_groups:
                    # Set the org_id on the object, as it's needed for the doc
                    user_group.org_id = self.org_id

                    self.logger.info(f"Processing user group: {user_group.name}")
                    
                    # Check if the user group already exists in the DB
                    existing_user_group = await tx_store.get_user_group_by_external_id(
                        connector_name=user_group.app_name,
                        external_id=user_group.source_user_group_id
                    )
                    print("Got an existing UG!!: ",existing_user_group)
                    
                    if existing_user_group is None:
                        # The ID is already set by default_factory, but we log
                        self.logger.info(f"Creating new user group with id: {user_group.id}")
                    else:
                        # Overwrite the new UUID with the existing one
                        user_group.id = existing_user_group.id
                        self.logger.info(f"Updating existing user group with id: {user_group.id}")
                        user_group.updated_at = get_epoch_timestamp_in_ms()

                    # 1. Upsert the user group document
                    # (This uses batch_upsert_user_groups and the to_arango... method)
                    await tx_store.batch_upsert_user_groups([user_group])

                    # 3. Handle User Permissions (from the passed 'permissions' list)
                    if not permissions:
                        continue

                    user_group_permissions = []
                    # Set the 'to' side of the edge to be this user group
                    to_collection = f"{CollectionNames.GROUPS.value}/{user_group.id}"

                    for permission in permissions:
                        from_collection = None
                        
                        if permission.entity_type == EntityType.USER:
                            user = None
                            if permission.email:
                                # Find the user's internal DB ID
                                user = await tx_store.get_user_by_email(permission.email)
                            
                            if user:
                                # Set the 'from' side of the edge to be the user
                                from_collection = f"{CollectionNames.USERS.value}/{user.id}"
                            else:
                                self.logger.warning(f"Could not find user with email {permission.email} for UserGroup permission.")
                        
                        # (Other entity_type cases like GROUP could be added here if needed)

                        if from_collection:
                            # (Assuming Permission class has this method)
                            user_group_permissions.append(
                                permission.to_arango_permission(from_collection, to_collection)
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

    async def on_new_app(self, app: App) -> None:
        pass

    async def on_new_app_group(self, app_group: AppGroup) -> None:
        pass


    async def get_all_active_users(self) -> List[User]:
        async with self.data_store_provider.transaction() as tx_store:
            users = await tx_store.get_users(self.org_id, active=True)

            return [User.from_arango_user(user) for user in users if user is not None]


    async def on_user_group_member_removed(
        self, 
        external_group_id: str, 
        user_email: str, 
        connector_name: str
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
                    connector_name=connector_name,
                    external_id=external_group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot remove member from group: "
                        f"Group with external ID {external_group_id} not found in database"
                    )
                    return False
                
                # 3. Construct the edge keys
                from_key = f"{CollectionNames.USERS.value}/{user.id}"
                to_key = f"{CollectionNames.GROUPS.value}/{user_group.id}"
                
                # 4. Delete the permission edge
                edge_deleted = await tx_store.delete_edge(
                    from_key=from_key,
                    to_key=to_key,
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
        connector_name: str
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
                    connector_name=connector_name,
                    external_id=external_group_id
                )
                if not user_group:
                    self.logger.warning(
                        f"Cannot add member to group: "
                        f"Group with external ID {external_group_id} not found in database"
                    )
                    return False
                
                # 3. Check if permission edge already exists
                from_key = f"{CollectionNames.USERS.value}/{user.id}"
                to_key = f"{CollectionNames.GROUPS.value}/{user_group.id}"
                
                # TODO: Implement a method to check if edge exists
                existing_edge = await tx_store.get_edge(from_key, to_key, CollectionNames.PERMISSION.value)
                if existing_edge:
                    self.logger.info(f"Permission edge already exists between {user_email} and group {user_group.name}")
                    return False
                
                # 4. Create the permission object
                permission = Permission(
                    external_id=user.id,  
                    email=user_email,
                    type=permission_type,
                    entity_type=EntityType.USER
                )
                
                # 5. Create new permission edge since it doesn't exist
                permission_edge = permission.to_arango_permission(from_key, to_key)
                
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
        connector_name: str
    ) -> bool:
        """
        Delete a user group and all its associated edges from the database.
        
        Args:
            external_group_id: The external ID of the group from the source system
            connector_name: The name of the connector (e.g., 'DROPBOX')
        
        Returns:
            bool: True if the group was successfully deleted, False otherwise
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                # 1. Look up the user group by external ID
                user_group = await tx_store.get_user_group_by_external_id(
                    connector_name=connector_name,
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

                #x Delete the node and edges
                await tx_store.delete_nodes_and_edges([group_internal_id], CollectionNames.GROUPS.value)
                
                # 2. Delete all edges connected to this group
                # group_collection_id = f"{CollectionNames.GROUPS.value}/{group_internal_id}"
                # print("!!!!!!!!!!!!!!!! group_collection_id", group_collection_id)
                # await tx_store.delete_edges_to(group_collection_id, CollectionNames.PERMISSION.value)
                # await tx_store.delete_edges_from(group_collection_id, CollectionNames.BELONGS_TO.value)
                
                # # 3. Delete the group node itself
                # await tx_store.delete_nodes([group_internal_id], CollectionNames.GROUPS.value)
                
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
    
    async def _delete_group_organization_edges(self, tx_store, group_internal_id: str) -> None:
        """Delete BELONGS_TO edges between group and organization."""
        try:
            group_collection_id = f"{CollectionNames.GROUPS.value}/{group_internal_id}"
            org_collection_id = f"{CollectionNames.ORGS.value}/{self.org_id}"
            
            # Delete the BELONGS_TO edge from group to organization
            edge_deleted = await tx_store.delete_edge(
                from_key=group_collection_id,
                to_key=org_collection_id,
                collection=CollectionNames.BELONGS_TO.value
            )
            
            if edge_deleted:
                self.logger.info(f"Deleted BELONGS_TO edge from group {group_internal_id} to org {self.org_id}")
            else:
                self.logger.debug(f"No BELONGS_TO edge found from group {group_internal_id} to org")
                
        except Exception as e:
            self.logger.error(f"Error deleting organization edges for group {group_internal_id}: {e}")