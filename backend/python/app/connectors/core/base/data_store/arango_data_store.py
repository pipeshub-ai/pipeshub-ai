from contextlib import asynccontextmanager
from logging import Logger
from typing import AsyncContextManager, Dict, List, Optional

from arango.database import TransactionDatabase

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.models.entities import (
    Anyone,
    AnyoneSameOrg,
    AnyoneWithLink,
    AppRole,
    AppUser,
    AppUserGroup,
    Domain,
    FileRecord,
    Org,
    Record,
    RecordGroup,
    RecordType,
    User,
)
from app.models.permission import Permission
from app.utils.time_conversion import get_epoch_timestamp_in_ms

read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]

class ArangoTransactionStore(TransactionStore):
    """ArangoDB transaction-aware data store"""

    def __init__(self, arango_service: BaseArangoService, txn: TransactionDatabase) -> None:
        self.arango_service = arango_service
        self.txn = txn
        self.logger = arango_service.logger

    async def batch_upsert_nodes(self, nodes: List[Dict], collection: str) -> bool | None:
        return await self.arango_service.batch_upsert_nodes(nodes, collection, transaction=self.txn)

    async def get_record_by_path(self, connector_name: Connectors, path: str) -> Optional[Record]:
        return await self.arango_service.get_record_by_path(connector_name, path, transaction=self.txn)

    async def get_record_by_key(self, key: str) -> Optional[Record]:
        return await self.arango_service.get_record_by_id(key, transaction=self.txn)

    async def get_record_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[Record]:
        return await self.arango_service.get_record_by_external_id(connector_name, external_id, transaction=self.txn)

    async def get_record_group_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[RecordGroup]:
        return await self.arango_service.get_record_group_by_external_id(connector_name, external_id, transaction=self.txn)

    async def get_file_record_by_id(self, id: str) -> Optional[FileRecord]:
        return await self.arango_service.get_file_record_by_id(id, transaction=self.txn)

    async def get_record_group_by_id(self, id: str) -> Optional[RecordGroup]:
        return await self.arango_service.get_record_group_by_id(id, transaction=self.txn)

    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        await self.arango_service.batch_create_edges([{
            "_from": f"{CollectionNames.RECORD_GROUPS.value}/{child_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{parent_id}",
            "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }], collection=CollectionNames.BELONGS_TO.value, transaction=self.txn)

    async def get_or_create_app_by_name(self, app_name: str, org_id: str) -> Optional[Dict]:
        return await self.arango_service.get_or_create_app_by_name(app_name, org_id)

    async def get_user_by_email(self, email: str) -> Optional[User]:
        return await self.arango_service.get_user_by_email(email, transaction=self.txn)

    async def get_user_by_source_id(self, source_user_id: str, connector_name: Connectors) -> Optional[User]:
        return await self.arango_service.get_user_by_source_id(source_user_id, connector_name, transaction=self.txn)

    async def get_app_user_by_email(self, email: str, app_name: Connectors) -> Optional[AppUser]:
        return await self.arango_service.get_app_user_by_email(email, app_name, transaction=self.txn)

    async def get_record_owner_source_user_email(self, record_id: str) -> Optional[str]:
        return await self.arango_service.get_record_owner_source_user_email(record_id, transaction=self.txn)

    async def get_user_by_user_id(self, user_id: str) -> Optional[User]:
        return await self.arango_service.get_user_by_user_id(user_id)

    async def delete_record_by_key(self, key: str) -> None:
        return await self.arango_service.delete_record(key, transaction=self.txn)

    async def delete_record_by_external_id(self, connector_name: Connectors, external_id: str, user_id: str) -> None:
        return await self.arango_service.delete_record_by_external_id(connector_name, external_id, user_id)

    async def remove_user_access_to_record(self, connector_name: Connectors, external_id: str, user_id: str) -> None:
        return await self.arango_service.remove_user_access_to_record(connector_name, external_id, user_id)

    async def delete_record_group_by_external_id(self, connector_name: Connectors, external_id: str) -> None:
        return await self.arango_service.delete_record_group_by_external_id(connector_name, external_id, transaction=self.txn)

    async def delete_edge(self, from_key: str, to_key: str, collection: str) -> None:
        return await self.arango_service.delete_edge(from_key, to_key, collection, transaction=self.txn)

    async def delete_nodes(self, keys: List[str], collection: str) -> None:
        return await self.arango_service.delete_nodes(keys, collection, transaction=self.txn)

    async def delete_edges_from(self, from_key: str, collection: str) -> None:
        return await self.arango_service.delete_edges_from(from_key, collection, transaction=self.txn)

    async def delete_edges_to(self, to_key: str, collection: str) -> None:
        return await self.arango_service.delete_edges_to(to_key, collection, transaction=self.txn)

    async def delete_edges_to_groups(self, from_key: str, collection: str) -> None:
        return await self.arango_service.delete_edges_to_groups(from_key, collection, transaction=self.txn)

    async def delete_edges_between_collections(self, from_key: str, edge_collection: str, to_collection: str) -> None:
        return await self.arango_service.delete_edges_between_collections(from_key, edge_collection, to_collection, transaction=self.txn)

    async def delete_nodes_and_edges(self, keys: List[str], collection: str) -> None:
        return await self.arango_service.delete_nodes_and_edges(keys, collection, graph_name="knowledgeGraph", transaction=self.txn)

    async def get_user_group_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[AppUserGroup]:
        return await self.arango_service.get_user_group_by_external_id(connector_name, external_id, transaction=self.txn)

    async def get_app_role_by_external_id(self, connector_name: Connectors, external_id: str) -> Optional[AppRole]:
        return await self.arango_service.get_app_role_by_external_id(connector_name, external_id, transaction=self.txn)

    async def get_users(self, org_id: str, active: bool = True) -> List[User]:
        return await self.arango_service.get_users(org_id, active)

    async def get_app_users(self, org_id: str, app_name: str) -> List[AppUser]:
        return await self.arango_service.get_app_users(org_id, app_name)

    async def get_user_groups(self, app_name: Connectors, org_id: str) -> List[AppUserGroup]:
        return await self.arango_service.get_user_groups(app_name, org_id, transaction=self.txn)

    async def create_user_group_hierarchy(
        self,
        child_external_id: str,
        parent_external_id: str,
        connector_name: Connectors
    ) -> bool:
        """Create BELONGS_TO edge between child and parent user groups"""
        try:
            # Lookup both groups
            child_group = await self.get_user_group_by_external_id(connector_name, child_external_id)
            if not child_group:
                self.logger.warning(
                    f"Child user group not found: {child_external_id} (connector: {connector_name.value})"
                )
                return False

            parent_group = await self.get_user_group_by_external_id(connector_name, parent_external_id)
            if not parent_group:
                self.logger.warning(
                    f"Parent user group not found: {parent_external_id} (connector: {connector_name.value})"
                )
                return False

            # Create BELONGS_TO edge
            edge = {
                "_from": f"{CollectionNames.GROUPS.value}/{child_group.id}",
                "_to": f"{CollectionNames.GROUPS.value}/{parent_group.id}",
                "entityType": "GROUP",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.arango_service.batch_create_edges(
                [edge],
                collection=CollectionNames.BELONGS_TO.value,
                transaction=self.txn
            )

            self.logger.debug(f"Created user group hierarchy: {child_group.name} -> {parent_group.name}")
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to create user group hierarchy ({child_external_id} -> {parent_external_id}): {str(e)}",
                exc_info=True
            )
            return False

    async def create_user_group_membership(
        self,
        user_source_id: str,
        group_external_id: str,
        connector_name: Connectors
    ) -> bool:
        """Create BELONGS_TO edge from user to group using source IDs"""
        try:
            # Lookup user by sourceUserId
            user = await self.get_user_by_source_id(user_source_id, connector_name)
            if not user:
                self.logger.warning(
                    f"User not found: {user_source_id} (connector: {connector_name.value})"
                )
                return False

            # Lookup group
            group = await self.get_user_group_by_external_id(connector_name, group_external_id)
            if not group:
                self.logger.warning(
                    f"User group not found: {group_external_id} (connector: {connector_name.value})"
                )
                return False

            # Create BELONGS_TO edge
            edge = {
                "_from": f"{CollectionNames.USERS.value}/{user.id}",
                "_to": f"{CollectionNames.GROUPS.value}/{group.id}",
                "entityType": "GROUP",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.arango_service.batch_create_edges(
                [edge],
                collection=CollectionNames.BELONGS_TO.value,
                transaction=self.txn
            )

            self.logger.debug(f"Created user group membership: {user.email} -> {group.name}")
            return True

        except Exception as e:
            self.logger.error(
                f"Failed to create user group membership ({user_source_id} -> {group_external_id}): {str(e)}",
                exc_info=True
            )
            return False

    async def get_first_user_with_permission_to_node(self, node_key: str) -> Optional[str]:
        return await self.arango_service.get_first_user_with_permission_to_node(node_key, transaction=self.txn)

    async def get_users_with_permission_to_node(self, node_key: str) -> List[str]:
        return await self.arango_service.get_users_with_permission_to_node(node_key, transaction=self.txn)

    async def get_edge(self, from_key: str, to_key: str, collection: str) -> Optional[Dict]:
        return await self.arango_service.get_edge(from_key, to_key, collection, transaction=self.txn)

    async def get_record_by_conversation_index(self, connector_name: Connectors, conversation_index: str, thread_id: str, org_id: str, user_id: str) -> Optional[Record]:
        return await self.arango_service.get_record_by_conversation_index(connector_name, conversation_index, thread_id, org_id, user_id, transaction=self.txn)


    async def batch_upsert_records(self, records: List[Record]) -> None:

        record_ids = [r.id for r in records]
        duplicates = [x for x in record_ids if record_ids.count(x) > 1]
        if duplicates:
            self.logger.warning(f"DUPLICATE RECORD IDS IN BATCH: {duplicates}")

        try:
            for record in records:
                # Define record type configurations
                record_type_config = {
                    RecordType.FILE: {
                        "collection": CollectionNames.FILES.value,
                    },
                    RecordType.MAIL: {
                        "collection": CollectionNames.MAILS.value,
                    },
                    # RecordType.MESSAGE.value: {
                    #     "collection": CollectionNames.MESSAGES.value,
                    # },
                    RecordType.WEBPAGE: {
                        "collection": CollectionNames.WEBPAGES.value,
                    },
                    RecordType.TICKET: {
                        "collection": CollectionNames.TICKETS.value,
                    },
                }

                # Get the configuration for the current record type
                record_type = record.record_type
                if record_type not in record_type_config:
                    self.logger.error(f"❌ Unsupported record type: {record_type}")
                    continue

                config = record_type_config[record_type]

                # Create the IS_OF_TYPE edge
                is_of_type_record = {
                    "_from": f"{CollectionNames.RECORDS.value}/{record.id}",
                    "_to": f"{config['collection']}/{record.id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }

                # Upsert base record
                await self.arango_service.batch_upsert_nodes([record.to_arango_base_record()], collection=CollectionNames.RECORDS.value, transaction=self.txn)
                # Upsert specific record type if it has a specific method
                await self.arango_service.batch_upsert_nodes([record.to_arango_record()], collection=config["collection"], transaction=self.txn)
                # Create IS_OF_TYPE edge
                await self.arango_service.batch_create_edges([is_of_type_record], collection=CollectionNames.IS_OF_TYPE.value, transaction=self.txn)

            self.logger.info(" Successfully upserted records ")
            return True
        except Exception as e:
            self.logger.error("❌ Batch upsert failed: %s", str(e))

            return False

    async def batch_upsert_record_groups(self, record_groups: List[RecordGroup]) -> None:
        return await self.arango_service.batch_upsert_nodes(
                [record_group.to_arango_base_record_group() for record_group in record_groups],
                collection=CollectionNames.RECORD_GROUPS.value,
                transaction=self.txn
            )

    async def batch_upsert_record_permissions(self, record_id: str, permissions: List[Permission]) -> None:
        return await self.arango_service.batch_upsert_record_permissions(record_id, permissions, transaction=self.txn)

    async def batch_create_user_app_edges(self, edges: List[Dict]) -> int:
        return await self.arango_service.batch_create_user_app_edges(edges)

    async def batch_upsert_user_groups(self, user_groups: List[AppUserGroup]) -> None:
        return await self.arango_service.batch_upsert_nodes(
                            [user_group.to_arango_base_user_group() for user_group in user_groups],
                            collection=CollectionNames.GROUPS.value,
                            transaction=self.txn
                        )

    async def batch_upsert_app_roles(self, app_roles: List[AppRole]) -> None:

        return await self.arango_service.batch_upsert_nodes(
                            [app_role.to_arango_base_role() for app_role in app_roles],
                            collection=CollectionNames.ROLES.value,
                            transaction=self.txn
                        )

    async def batch_upsert_app_users(self, users: List[AppUser]) -> None:
        orgs = await self.arango_service.get_all_orgs()
        if not orgs:
            raise Exception("No organizations found in the database. Cannot initialize DataSourceEntitiesProcessor.")
        org_id = orgs[0]["_key"]
        app_name = users[0].app_name.value

        app = await self.arango_service.get_or_create_app_by_name(app_name, org_id)
        if not app:
            raise Exception(f"Failed to get/create app: {app_name}")

        app_id = app["_id"]

        for user in users:
            user_record = await self.arango_service.get_user_by_email(user.email)

            if not user_record:
                await self.arango_service.batch_upsert_nodes(
                    [{**user.to_arango_base_user(), "orgId": org_id, "isActive": False}],
                    collection=CollectionNames.USERS.value,
                    transaction=self.txn
                )

                user_record = await self.arango_service.get_user_by_email(user.email, transaction=self.txn)

                # Create a edge between the user and the org if it doesn't exist
                user_org_relation = {
                    "_from": f"{CollectionNames.USERS.value}/{user.id}",
                    "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                    "createdAtTimestamp": user.created_at,
                    "updatedAtTimestamp": user.updated_at,
                    "entityType": "ORGANIZATION",
                }
                await self.arango_service.batch_create_edges(
                    [user_org_relation], collection=CollectionNames.BELONGS_TO.value, transaction=self.txn
                )

            user_key = user_record.id
            user_app_relation = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": app_id,
                "sourceUserId": user.source_user_id,
                "syncState": "NOT_STARTED",
                "lastSyncUpdate": get_epoch_timestamp_in_ms(),
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.arango_service.batch_create_edges(
                [user_app_relation],
                collection=CollectionNames.USER_APP_RELATION.value,
                transaction=self.txn
            )

    async def batch_upsert_orgs(self, orgs: List[Org]) -> None:
        return await self.arango_service.batch_upsert_orgs(orgs, transaction=self.txn)

    async def batch_upsert_domains(self, domains: List[Domain]) -> None:
        return await self.arango_service.batch_upsert_domains(domains, transaction=self.txn)

    async def batch_upsert_anyone(self, anyone: List[Anyone]) -> None:
        return await self.arango_service.batch_upsert_anyone(anyone, transaction=self.txn)

    async def batch_upsert_anyone_with_link(self, anyone_with_link: List[AnyoneWithLink]) -> None:
        return await self.arango_service.batch_upsert_anyone_with_link(anyone_with_link, transaction=self.txn)

    async def batch_upsert_anyone_same_org(self, anyone_same_org: List[AnyoneSameOrg]) -> None:
        return await self.arango_service.batch_upsert_anyone_same_org(anyone_same_org, transaction=self.txn)

    async def commit(self) -> None:
        """Commit the ArangoDB transaction"""
        self.txn.commit_transaction()

    async def rollback(self) -> None:
        """Rollback the ArangoDB transaction"""
        self.txn.abort_transaction()

    async def create_record_relation(self, from_record_id: str, to_record_id: str, relation_type: str) -> None:
        record_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{from_record_id}",
                    "_to": f"{CollectionNames.RECORDS.value}/{to_record_id}",
                    "relationshipType": relation_type,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }

        await self.arango_service.batch_create_edges(
            [record_edge], collection=CollectionNames.RECORD_RELATIONS.value, transaction=self.txn
        )
    async def create_record_group_relation(self, record_id: str, record_group_id: str) -> None:
        record_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
        await self.arango_service.batch_create_edges(
            [record_edge], collection=CollectionNames.BELONGS_TO.value, transaction=self.txn
        )

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        record_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
        await self.arango_service.batch_create_edges(
            [record_edge], collection=CollectionNames.INHERIT_PERMISSIONS.value, transaction=self.txn
        )
    async def create_inherit_permissions_relation_record(self, child_record_id: str, parent_record_id: str) -> None:
        record_edge = {
                    "_from": f"{CollectionNames.RECORDS.value}/{child_record_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{parent_record_id}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
        await self.arango_service.batch_create_edges(
            [record_edge], collection=CollectionNames.INHERIT_PERMISSIONS.value, transaction=self.txn
        )
    async def get_sync_point(self, sync_point_key: str) -> Optional[Dict]:
        return await self.arango_service.get_sync_point(sync_point_key, CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def get_all_orgs(self) -> List[Org]:
        return await self.arango_service.get_all_orgs()

    async def create_record_permissions(self, record_id: str, permissions: List[Permission]) -> None:
        return await self.arango_service.batch_create_edges([permission.to_arango_permission() for permission in permissions],
                    collection=CollectionNames.PERMISSIONS.value, transaction=self.txn)

    async def create_record_group_permissions(self, record_group_id: str, permissions: List[Permission]) -> None:
        return await self.arango_service.batch_create_edges([permission.to_arango_permission() for permission in permissions],
                    collection=CollectionNames.PERMISSIONS.value, transaction=self.txn)

    async def create_user_groups(self, user_groups: List[AppUserGroup]) -> None:
        return await self.arango_service.batch_upsert_nodes([user_group.to_arango_base_user_group() for user_group in user_groups],
                    collection=CollectionNames.GROUPS.value, transaction=self.txn)

    async def create_users(self, users: List[AppUser]) -> None:
        return await self.arango_service.batch_upsert_nodes([user.to_arango_base_user() for user in users],
                    collection=CollectionNames.USERS.value, transaction=self.txn)

    async def create_orgs(self, orgs: List[Org]) -> None:
        return await self.arango_service.batch_upsert_nodes([org.to_arango_base_org() for org in orgs],
                    collection=CollectionNames.ORGS.value, transaction=self.txn)

    async def create_domains(self, domains: List[Domain]) -> None:
        return await self.arango_service.batch_upsert_nodes([domain.to_arango_base_domain() for domain in domains],
                    collection=CollectionNames.DOMAINS.value, transaction=self.txn)

    async def create_anyone(self, anyone: List[Anyone]) -> None:
        return await self.arango_service.batch_upsert_nodes([anyone.to_arango_base_anyone() for anyone in anyone],
                    collection=CollectionNames.ANYONE.value, transaction=self.txn)

    async def create_anyone_with_link(self, anyone_with_link: List[AnyoneWithLink]) -> None:
        return await self.arango_service.batch_upsert_nodes([anyone_with_link.to_arango_base_anyone_with_link() for anyone_with_link in anyone_with_link],
                    collection=CollectionNames.ANYONE_WITH_LINK.value, transaction=self.txn)

    async def create_anyone_same_org(self, anyone_same_org: List[AnyoneSameOrg]) -> None:
        return await self.arango_service.batch_upsert_nodes([anyone_same_org.to_arango_base_anyone_same_org() for anyone_same_org in anyone_same_org],
                    collection=CollectionNames.ANYONE_SAME_ORG.value, transaction=self.txn)

    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict) -> None:
        return await self.arango_service.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def delete_sync_point(self, sync_point_key: str) -> None:
        return await self.arango_service.remove_sync_point([sync_point_key],
                    collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)
    async def read_sync_point(self, sync_point_key: str) -> None:
        return await self.arango_service.get_sync_point(sync_point_key, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict) -> None:
        return await self.arango_service.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def batch_upsert_record_group_permissions(
        self, record_group_id: str, permissions: List[Permission], connector_name: Connectors
    ) -> None:
        """
        Batch upsert permissions for a record group.

        Creates permission edges from users/groups to the record group.
        Looks up users by email and groups by external_id, then creates
        the appropriate permission edges.

        Args:
            record_group_id: Internal ID (_key) of the record group
            permissions: List of Permission objects
            connector_name: Connector enum for scoped group lookups
        """
        if not permissions:
            return

        permission_edges = []
        to_collection = f"{CollectionNames.RECORD_GROUPS.value}/{record_group_id}"

        for permission in permissions:
            from_collection = None

            if permission.entity_type.value == "USER":
                # Lookup user by email
                user = None
                if permission.email:
                    user = await self.get_user_by_email(permission.email)

                if user:
                    from_collection = f"{CollectionNames.USERS.value}/{user.id}"
                else:
                    self.logger.warning(
                        f"User not found for email: {permission.email}"
                    )
                    continue

            elif permission.entity_type.value == "GROUP":
                # Lookup group by external_id using the provided connector_name
                user_group = None
                if permission.external_id:
                    user_group = await self.get_user_group_by_external_id(
                        connector_name, permission.external_id
                    )

                if user_group:
                    from_collection = f"{CollectionNames.GROUPS.value}/{user_group.id}"
                else:
                    self.logger.warning(
                        f"Group not found for external_id: {permission.external_id}"
                    )
                    continue

            if from_collection:
                permission_edges.append(
                    permission.to_arango_permission(from_collection, to_collection)
                )

        if permission_edges:
            await self.arango_service.batch_create_edges(
                permission_edges,
                collection=CollectionNames.PERMISSION.value,
                transaction=self.txn
            )

    async def batch_create_edges(self, edges: List[Dict], collection: str) -> None:
        return await self.arango_service.batch_create_edges(edges, collection=collection, transaction=self.txn)

class ArangoDataStore(DataStoreProvider):
    """ArangoDB data store"""
    def __init__(self, logger: Logger, arango_service: BaseArangoService) -> None:
        self.arango_service = arango_service

    @asynccontextmanager
    async def transaction(self) -> AsyncContextManager["TransactionStore"]:
        """
        Create an ArangoDB transaction store context manager.
        """
        db = self.arango_service.db

        # Begin transaction
        txn = db.begin_transaction(
            read=read_collections,
            write=write_collections
        )

        tx_store = ArangoTransactionStore(self.arango_service, txn)

        try:
            yield tx_store
        except Exception:
            await tx_store.rollback()
            raise
        else:
            await tx_store.commit()

    async def execute_in_transaction(self, func, *args, **kwargs) -> None:
        """Execute function within ArangoDB transaction"""
        async with self.transaction() as tx_store:
            return func(tx_store, *args, **kwargs)
