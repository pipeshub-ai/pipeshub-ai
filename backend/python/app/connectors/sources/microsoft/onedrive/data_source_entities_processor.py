import uuid
from dataclasses import dataclass
from typing import List, Tuple

from arango.database import TransactionDatabase

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, OriginTypes
from app.config.constants.service import config_node_constants
from app.connectors.sources.microsoft.onedrive.arango_service import ArangoService
from app.models.entities import (
    FileRecord,
    MailRecord,
    MessageRecord,
    Record,
    RecordGroup,
)
from app.models.permission import EntityType, Permission
from app.models.users import User, UserGroup
from app.services.messaging.interface.producer import IMessagingProducer
from app.services.messaging.kafka.config.kafka_config import KafkaProducerConfig
from app.services.messaging.messaging_factory import MessagingFactory
from app.utils.time_conversion import get_epoch_timestamp_in_ms


@dataclass
class RecordGroupWithPermissions:
    record_group: RecordGroup
    users: List[Tuple[User, Permission]]
    user_groups: List[Tuple[UserGroup, Permission]]
    anyone_with_link: bool = False
    anyone_same_org: bool = False
    anyone_same_domain: bool = False

@dataclass
class UserGroupWithMembers:
    user_group: UserGroup
    users: List[Tuple[User, Permission]]

class App:
    def __init__(self, app_name: str) -> None:
        self.app_name = app_name

    def get_app_name(self) -> str:
        return self.app_name

class AppGroup:
    def __init__(self, app_group_name: str, apps: List[App]) -> None:
        self.app_group_name = app_group_name
        self.apps = apps

    def get_app_group_name(self) -> str:
        return self.app_group_name


read_collections = [
    CollectionNames.RECORDS.value,
    CollectionNames.RECORD_GROUPS.value,
    CollectionNames.FILES.value,
    CollectionNames.MAILS.value,
    # CollectionNames.MESSAGES.value,
    CollectionNames.USERS.value,
    # CollectionNames.USER_GROUPS.value,
    CollectionNames.ORGS.value,
    # CollectionNames.DOMAINS.value,
    # CollectionNames.ANYONE.value,
    # CollectionNames.ANYONE_WITH_LINK.value,
    CollectionNames.PERMISSIONS.value,
    CollectionNames.IS_OF_TYPE.value,
    CollectionNames.BELONGS_TO_RECORD_GROUP.value,
    CollectionNames.BELONGS_TO.value,
    CollectionNames.RECORD_RELATIONS.value,
]

write_collections = [
    CollectionNames.RECORDS.value,
    CollectionNames.RECORD_GROUPS.value,
    CollectionNames.FILES.value,
    CollectionNames.MAILS.value,
    # CollectionNames.MESSAGES.value,
    CollectionNames.USERS.value,
    # CollectionNames.USER_GROUPS.value,
    CollectionNames.ORGS.value,
    # CollectionNames.DOMAINS.value,
    # CollectionNames.ANYONE.value,
    # CollectionNames.ANYONE_WITH_LINK.value,
    CollectionNames.PERMISSIONS.value,
    CollectionNames.IS_OF_TYPE.value,
    CollectionNames.BELONGS_TO_RECORD_GROUP.value,
    CollectionNames.BELONGS_TO.value,
    CollectionNames.RECORD_RELATIONS.value,
]

class DataSourceEntitiesProcessor:
    def __init__(self, logger, app: App, arango_service: ArangoService, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.app = app
        self.arango_service: ArangoService = arango_service
        self.config_service: ConfigurationService = config_service
        self.org_id = ""

    async def initialize(self) -> None:
        producer_config = await self.config_service.get_config(
            config_node_constants.KAFKA.value
        )
        kafka_producer_config = KafkaProducerConfig(
            bootstrap_servers=producer_config.get("bootstrap_servers"),
            client_id=producer_config.get("client_id", "connectors"),
        )
        self.messaging_producer: IMessagingProducer = MessagingFactory.create_producer(
            broker_type="kafka",
            logger=self.logger,
            config=kafka_producer_config,
        )
        await self.messaging_producer.initialize()
        orgs = await self.arango_service.get_all_orgs()
        orgs = await self.arango_service.get_all_orgs()
        if not orgs:
            raise Exception("No organizations found in the database. Cannot initialize DataSourceEntitiesProcessor.")
        self.org_id = orgs[0]["_key"]


    async def _handle_parent_record(self, record: Record, transaction: TransactionDatabase) -> None:
        if record.parent_external_record_id:
            parent_record = await self.arango_service.get_record_by_external_id(connector_name=record.connector_name,
                                                                                external_id=record.parent_external_record_id, transaction=transaction)

            if parent_record is None:
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
                    mime_type="application/vnd.folder",

                )
                await self.arango_service.batch_upsert_nodes(
                    [parent_record.to_arango_base_record()], collection=CollectionNames.RECORDS.value, transaction=transaction
                )

            if parent_record and isinstance(parent_record, Record):
                # Create a edge between the record and the parent record if it doesn't exist
                parent_record_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{parent_record.id}",
                    "_to": f"{CollectionNames.RECORDS.value}/{record.id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    "lastUpdatedTimestampAtSource": get_epoch_timestamp_in_ms(),
                }
                await self.arango_service.batch_create_edges(
                    [parent_record_edge], collection=CollectionNames.RECORD_RELATIONS.value, transaction=transaction
                )

    async def _handle_record_group(self, record: Record, transaction: TransactionDatabase) -> None:
        record_group = await self.arango_service.get_record_group_by_external_id(connector_name=record.connector_name,
                                                                                  external_id=record.external_record_group_id, transaction=transaction)

        if record_group is None:
            # Create a new record group
            record_group = RecordGroup(
                external_group_id=record.external_record_group_id,
                name=record.external_record_group_id,
                group_type=record.record_group_type,
                connector_name=record.connector_name,
            )
            await self.arango_service.batch_upsert_nodes(
                [record_group.to_arango_base_record_group()], collection=CollectionNames.RECORD_GROUPS.value, transaction=transaction
            )
            # Todo: Create a edge between the record group and the App

        if record_group:
            # Create a edge between the record and the record group if it doesn't exist
            record_group_edge = {
                "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group.id}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "entityType": "GROUP",
            }
            await self.arango_service.batch_create_edges(
                [record_group_edge], collection=CollectionNames.BELONGS_TO.value, transaction=transaction
            )

    async def _handle_new_record(self, record: Record, transaction: TransactionDatabase) -> None:
        is_of_type_record = None
        record.org_id = self.org_id
        if isinstance(record, FileRecord):
            is_of_type_record = {
                "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                "_to": f"{CollectionNames.FILES.value}/{record.id}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_base_record()], collection=CollectionNames.RECORDS.value, transaction=transaction
            )
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_file_record()], collection=CollectionNames.FILES.value, transaction=transaction
            )
            await self.arango_service.batch_create_edges(
                [is_of_type_record], collection=CollectionNames.IS_OF_TYPE.value, transaction=transaction
            )
        if isinstance(record, MailRecord):
            is_of_type_record = {
                "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                "_to": f"{CollectionNames.MAILS.value}/{record.id}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_base_record()], collection=CollectionNames.RECORDS.value, transaction=transaction
            )
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_mail_record()], collection=CollectionNames.MAILS.value, transaction=transaction
            )
            await self.arango_service.batch_create_edges(
                [is_of_type_record], collection=CollectionNames.IS_OF_TYPE.value, transaction=transaction
            )
        if isinstance(record, MessageRecord):
            is_of_type_record = {
                "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                "_to": f"{CollectionNames.MESSAGES.value}/{record.id}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_base_record()], collection=CollectionNames.RECORDS.value, transaction=transaction
            )
            await self.arango_service.batch_upsert_nodes(
                [record.to_arango_message_record()], collection=CollectionNames.MESSAGES.value, transaction=transaction
            )
            await self.arango_service.batch_create_edges(
                [is_of_type_record], collection=CollectionNames.IS_OF_TYPE.value, transaction=transaction
            )


    async def _handle_record_permissions(self, record: Record, permissions: List[Permission], transaction: TransactionDatabase) -> None:
        record_permissions = []

        for permission in permissions:
            from_collection = f"{CollectionNames.RECORDS.value}/{record.id}"
            to_collection = None
            if permission.entity_type == EntityType.USER.value:
                user = None
                if permission.email:
                    user = await self.arango_service.get_user_by_email(permission.email, transaction=transaction)

                if user:
                    to_collection = f"{CollectionNames.USERS.value}/{user.id}"
            # elif permission.entity_type == EntityType.GROUP.value:
            #     if permission.external_id:
            #         user_group = await self.arango_service.get_user_group_by_external_id(permission.external_id)
            #     else:
            #         user_group = await self.arango_service.get_user_group_by_email(permission.email)

            #     if user_group:
            #         to_collection = f"{CollectionNames.USER_GROUPS.value}/{user_group.id}"

            # if permission.entity_type == EntityType.ORG.value:
            #     org = await self.arango_service.get_org_by_external_id(permission.external_id)
            #     if org:
            #         to_collection = f"{CollectionNames.ORGS.value}/{org.id}"

            # if permission.entity_type == EntityType.DOMAIN.value:
            #     domain = await self.arango_service.get_domain_by_external_id(permission.external_id)
            #     if domain:
            #         to_collection = f"{CollectionNames.DOMAINS.value}/{domain.id}"

            # if permission.entity_type == EntityType.ANYONE.value:
            #     to_collection = f"{CollectionNames.ANYONE.value}"

            # if permission.entity_type == EntityType.ANYONE_WITH_LINK.value:
            #     to_collection = f"{CollectionNames.ANYONE_WITH_LINK.value}"

            if to_collection:
                record_permissions.append(permission.to_arango_permission(from_collection, to_collection))

        if record_permissions:
            await self.arango_service.batch_create_edges(
                record_permissions, collection=CollectionNames.PERMISSIONS.value, transaction=transaction
            )


    async def _process_record(self, record: Record, permissions: List[Permission], transaction: TransactionDatabase) -> None:
        existing_record = await self.arango_service.get_record_by_external_id(connector_name=record.connector_name,
                                                                                    external_id=record.external_record_id, transaction=transaction)

        if existing_record is None:
            await self._handle_new_record(record, transaction)
        else:
            record.id = existing_record.id
            await self._handle_updated_record(record, existing_record, transaction)

        # Create a edge between the record and the parent record if it doesn't exist and if parent_record_id is provided
        await self._handle_parent_record(record, transaction)

        # Create a edge between the record and the record group if it doesn't exist and if record_group_id is provided
        await self._handle_record_group(record, transaction)

        # Create a edge between the base record and the specific record if it doesn't exist - isOfType - File, Mail, Message

        await self._handle_record_permissions(record, permissions, transaction)
        #Todo: Check if record is updated, permissions are updated or content is updated
        #if existing_record:


        # Create record if it doesn't exist
        # Record download function
        # Create a permission edge between the record and the app with sync status if it doesn't exist

        if existing_record is None:
            await self.messaging_producer.send_message(
                "record-events",
                {"eventType": "newRecord", "timestamp": get_epoch_timestamp_in_ms(), "payload": record.to_kafka_record()},
                key=record.id
            )

    async def on_new_records(self, records_with_permissions: List[Tuple[Record, List[Permission]]]) -> None:
        try:
            transaction = self.arango_service.db.begin_transaction(
                    read=read_collections,
                    write=write_collections,
            )
            # Create a transaction
            for record, permissions in records_with_permissions:
                await self._process_record(record, permissions, transaction)

            transaction.commit_transaction()
        except Exception as e:
            self.logger.error(f"Error in on_new_records: {str(e)}")
            transaction.abort_transaction()
            raise e

    async def _handle_updated_record(self, record: Record, existing_record: Record, transaction: TransactionDatabase) -> None:
        pass

    async def on_updated_record_permissions(self, record: Record, permissions: List[Permission]) -> None:
        pass

    async def on_record_content_update(self, record: Record) -> None:
        pass

    async def on_record_metadata_update(self, record: Record) -> None:
        pass

    async def on_record_deleted(self, record_id: str) -> None:
        await self.arango_service.delete_record(record_id)
        pass


    async def on_new_record_groups(self, record_groups: List[RecordGroup], permissions: List[Permission]) -> None:
        # Create a transaction
        transaction = self.arango_service.db.begin_transaction(
                    read=read_collections,
                    write=write_collections,
            )
        for record_group in record_groups:
            self.logger.info(f"Processing record group: {record_group}")
            # Create record group if it doesn't exist
            # Create a permission edge between the record group and the org if it doesn't exist
            # Create a permission edge between the record group and the user if it doesn't exist
            # Create a permission edge between the record group and the user group if it doesn't exist
            # Create a permission edge between the record group and the org if it doesn't exist
            # Create a edge between the record group and the app with sync status if it doesn't exist
            await self.arango_service.batch_upsert_nodes(
                [record_group.to_arango_base_record_group()], collection=CollectionNames.RECORD_GROUPS.value, transaction=transaction
            )
        transaction.commit_transaction()

        # Commit the transaction

    async def on_new_users(self, users: List[User]) -> None:
        # Create a transaction
        transaction = self.arango_service.db.begin_transaction(
                    read=read_collections,
                    write=write_collections,
            )

        # Get all users from the database(Active and Inactive)
        existing_users = await self.arango_service.get_users(self.org_id, active=False)

        for user in users:
            self.logger.info(f"Processing user: {user}")

            existing_user_emails = {existing_user.get("email") for existing_user in existing_users}
            if user.email not in existing_user_emails:
                user_record = user.to_arango_base_record()
                user_record["isActive"] = False
                user_record["_key"] = str(uuid.uuid4())
                user_record["orgId"] = self.org_id
                await self.arango_service.batch_upsert_nodes(
                    [user_record],
                    collection=CollectionNames.USERS.value, transaction=transaction
                )

                #  # Create a edge between the user and the org if it doesn't exist
                #  user_org_relation = {
                #     "_from": f"{CollectionNames.USERS.value}/{user.id}",
                #     "_to": f"{CollectionNames.ORGS.value}/{self.org_id}",
                #     "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                #     "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                #     "entityType": "USER",
                #  }
                #  await self.arango_service.batch_create_edges(
                #     [user_org_relation], collection=CollectionNames.BELONGS_TO.value, transaction=transaction
                #  )

                # Create a edge between the user and the app with sync status if it doesn't exist
                # user_app_relation = {
                #     "_from": f"{CollectionNames.USERS.value}/{user.id}",
                #     "_to": f"{CollectionNames.APPS.value}/{self.app.id}",
                #     "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                #     "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                #     "entityType": "USER",
                #  }



        # Commit the transaction
        transaction.commit_transaction()

    async def on_new_user_groups(self, user_groups: List[UserGroup], permissions: List[Permission]) -> None:
        # Create a transaction

        for user_group in user_groups:
            self.logger.info(f"Processing user group: {user_group}")
            # Create user group if it doesn't exist
            # Create a edge between the user and user group

        # Commit the transaction


    async def on_new_app(self, app: App) -> None:
        pass

    async def on_new_app_group(self, app_group: AppGroup) -> None:
        pass


    async def get_all_active_users(self) -> List[User]:
        users = await self.arango_service.get_users(self.org_id, active=True)
        return [User.from_arango_user(user) for user in users]
