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
                    mime_type=MimeTypes.FOLDER,
                )
                await tx_store.batch_upsert_records([parent_record])

            if parent_record and isinstance(parent_record, Record):
                # Create a edge between the record and the parent record if it doesn't exist
                tx_store.create_record_relation(parent_record.id, record.id, "PARENT_CHILD")

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

        await tx_store.batch_upsert_records([record])


    async def _handle_record_permissions(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> None:
        record_permissions = []

        for permission in permissions:
            from_collection = f"{CollectionNames.RECORDS.value}/{record.id}"
            to_collection = None
            if permission.entity_type == EntityType.USER.value:
                user = None
                if permission.email:
                    user = await tx_store.get_user_by_email(permission.email)
                if user:
                    to_collection = f"{CollectionNames.USERS.value}/{user.id}"
            # elif permission.entity_type == EntityType.GROUP.value:
            #     if permission.external_id:
            #         user_group = await self.data_store.get_user_group_by_external_id(permission.external_id)
            #     else:
            #         user_group = await self.data_store.get_user_group_by_email(permission.email)

            #     if user_group:
            #         to_collection = f"{CollectionNames.GROUPS.value}/{user_group.id}"

            # if permission.entity_type == EntityType.ORG.value:
            #     org = await self.data_store.get_org_by_external_id(permission.external_id)
            #     if org:
            #         to_collection = f"{CollectionNames.ORGS.value}/{org.id}"

            # if permission.entity_type == EntityType.DOMAIN.value:
            #     domain = await self.data_store.get_domain_by_external_id(permission.external_id)
            #     if domain:
            #         to_collection = f"{CollectionNames.DOMAINS.value}/{domain.id}"

            # if permission.entity_type == EntityType.ANYONE.value:
            #     to_collection = f"{CollectionNames.ANYONE.value}"

            # if permission.entity_type == EntityType.ANYONE_WITH_LINK.value:
            #     to_collection = f"{CollectionNames.ANYONE_WITH_LINK.value}"

            if to_collection:
                record_permissions.append(permission.to_arango_permission(from_collection, to_collection))

        if record_permissions:
            await tx_store.batch_create_edges(
                record_permissions, collection=CollectionNames.PERMISSIONS.value
            )


    async def _process_record(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> Optional[Record]:
        existing_record = await tx_store.get_record_by_external_id(connector_name=record.connector_name,
                                                                   external_id=record.external_record_id)

        if existing_record is None:
            await self._handle_new_record(record, tx_store)
        else:
            record.id = existing_record.id
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

        return None

    async def on_new_records(self, records_with_permissions: List[Tuple[Record, List[Permission]]]) -> None:
        try:
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

    async def _handle_updated_record(self, record: Record, existing_record: Record, tx_store: TransactionStore) -> None:
        pass

    async def on_updated_record_permissions(self, record: Record, permissions: List[Permission], tx_store: TransactionStore) -> None:
        pass

    async def on_record_content_update(self, record: Record, tx_store: TransactionStore) -> None:
        pass

    async def on_record_metadata_update(self, record: Record, tx_store: TransactionStore) -> None:
        pass

    async def on_record_deleted(self, record_id: str) -> None:
        async with self.data_store_provider.transaction() as tx_store:
            await tx_store.delete_record_by_key(record_id)

    async def on_new_record_groups(self, record_groups: List[Tuple[RecordGroup, List[Permission]]]) -> None:
        try:
            async with self.data_store_provider.transaction() as tx_store:
                for record_group, _permissions in record_groups:
                    record_group.org_id = self.org_id

                    self.logger.info(f"Processing record group: {record_group}")
                    existing_record_group = await tx_store.get_record_group_by_external_id(connector_name=record_group.connector_name,
                                                                                           external_id=record_group.external_group_id)
                    if existing_record_group is None:
                        record_group.id = str(uuid.uuid4())
                        # Create a permission edge between the record group and the org if it doesn't exist
                        # Create a permission edge between the record group and the user if it doesn't exist
                        # Create a permission edge between the record group and the user group if it doesn't exist
                        # Create a permission edge between the record group and the org if it doesn't exist
                        # Create a edge between the record group and the app with sync status if it doesn't exist
                    else:
                        record_group.id = existing_record_group.id

                    await tx_store.batch_upsert_record_groups([record_group])
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

    async def on_new_user_groups(self, user_groups: List[AppUserGroup], permissions: List[Permission]) -> None:
        try:
            async with self.data_store_provider.transaction():

                for user_group in user_groups:
                    self.logger.info(f"Processing user group: {user_group}")
                    # Create user group if it doesn't exist
                    # Create a edge between the user and user group
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

