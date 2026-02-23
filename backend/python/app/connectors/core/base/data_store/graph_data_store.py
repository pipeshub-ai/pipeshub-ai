from contextlib import asynccontextmanager
from logging import Logger
from typing import AsyncContextManager, Dict, List, Optional

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_store.data_store import (
    DataStoreProvider,
    TransactionStore,
)
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
    Person,
    Record,
    RecordGroup,
    User,
)
from app.models.permission import Permission
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]

class GraphTransactionStore(TransactionStore):
    """
    Graph database transaction-aware data store using IGraphDBProvider.
    """

    def __init__(self, graph_provider: IGraphDBProvider, txn: str) -> None:
        self.graph_provider = graph_provider
        self.txn = txn  # Transaction ID (string) for HTTP provider
        self.logger = graph_provider.logger

    async def batch_upsert_nodes(self, nodes: List[Dict], collection: str) -> bool | None:
        return await self.graph_provider.batch_upsert_nodes(nodes, collection, transaction=self.txn)

    async def get_record_by_path(self, connector_id: str, path: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_path(connector_id, path, transaction=self.txn)

    async def get_record_by_key(self, key: str) -> Optional[Record]:
        return await self.graph_provider.get_document(key, CollectionNames.RECORDS.value, transaction=self.txn)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_external_id(connector_id, external_id, transaction=self.txn)

    async def get_record_by_external_revision_id(self, connector_id: str, external_revision_id: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_external_revision_id(connector_id, external_revision_id, transaction=self.txn)

    async def get_records_by_status(self, org_id: str, connector_id: str, status_filters: List[str], limit: Optional[int] = None, offset: int = 0) -> List[Record]:
        """Get records by status. Returns properly typed Record instances."""
        return await self.graph_provider.get_records_by_status(org_id, connector_id, status_filters, limit, offset, transaction=self.txn)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        return await self.graph_provider.get_record_group_by_external_id(connector_id, external_id, transaction=self.txn)

    async def get_file_record_by_id(self, id: str) -> Optional[FileRecord]:
        return await self.graph_provider.get_file_record_by_id(id, transaction=self.txn)

    async def get_record_group_by_id(self, id: str) -> Optional[RecordGroup]:
        return await self.graph_provider.get_record_group_by_id(id, transaction=self.txn)

    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        """
        Create BELONGS_TO edge from child record group to parent record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_record_groups_relation(child_id, parent_id, transaction=self.txn)

    async def get_user_by_email(self, email: str) -> Optional[User]:
        return await self.graph_provider.get_user_by_email(email, transaction=self.txn)

    async def get_user_by_source_id(self, source_user_id: str, connector_id: str) -> Optional[User]:
        return await self.graph_provider.get_user_by_source_id(source_user_id, connector_id, transaction=self.txn)

    async def get_app_user_by_email(self, email: str, connector_id: str) -> Optional[AppUser]:
        return await self.graph_provider.get_app_user_by_email(email, connector_id, transaction=self.txn)

    async def get_record_owner_source_user_email(self, record_id: str) -> Optional[str]:
        return await self.graph_provider.get_record_owner_source_user_email(record_id, transaction=self.txn)

    async def get_user_by_user_id(self, user_id: str) -> Optional[User]:
        return await self.graph_provider.get_user_by_user_id(user_id)

    async def delete_record_by_key(self, key: str) -> None:
        # Delete the record node from the records collection
        return await self.graph_provider.delete_nodes([key], CollectionNames.RECORDS.value, transaction=self.txn)

    async def delete_record_by_external_id(self, connector_id: str, external_id: str, user_id: str) -> None:
        return await self.graph_provider.delete_record_by_external_id(connector_id, external_id, user_id)

    async def remove_user_access_to_record(self, connector_id: str, external_id: str, user_id: str) -> None:
        return await self.graph_provider.remove_user_access_to_record(connector_id, external_id, user_id)

    async def delete_record_group_by_external_id(self, connector_id: str, external_id: str) -> None:
        return await self.graph_provider.delete_record_group_by_external_id(connector_id, external_id, transaction=self.txn)

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edge(from_id, from_collection, to_id, to_collection, collection, transaction=self.txn)

    async def delete_nodes(self, keys: List[str], collection: str) -> None:
        return await self.graph_provider.delete_nodes(keys, collection, transaction=self.txn)

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edges_from(from_id, from_collection, collection, transaction=self.txn)

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edges_to(to_id, to_collection, collection, transaction=self.txn)

    async def delete_parent_child_edge_to_record(self, record_id: str, collection: str = CollectionNames.RECORD_RELATIONS.value) -> int:
        """Delete PARENT_CHILD edges pointing to a specific target record"""
        return await self.graph_provider.delete_parent_child_edge_to_record(record_id, collection=collection, transaction=self.txn)

    async def delete_edges_to_groups(self, from_id: str, from_collection: str, collection: str) -> None:
        return await self.graph_provider.delete_edges_to_groups(from_id, from_collection, collection, transaction=self.txn)

    async def delete_edges_between_collections(self, from_id: str, from_collection: str, edge_collection: str, to_collection: str) -> None:
        return await self.graph_provider.delete_edges_between_collections(from_id, from_collection, edge_collection, to_collection, transaction=self.txn)

    async def delete_edges_by_relationship_types(
        self,
        from_id: str,
        from_collection: str,
        collection: str,
        relationship_types: List[str]
    ) -> int:
        """Delete edges by relationship types from a record."""
        return await self.graph_provider.delete_edges_by_relationship_types(
            from_id, from_collection, collection, relationship_types, transaction=self.txn
        )

    async def delete_nodes_and_edges(self, keys: List[str], collection: str) -> None:
        return await self.graph_provider.delete_nodes_and_edges(keys, collection, graph_name="knowledgeGraph", transaction=self.txn)

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[AppUserGroup]:
        return await self.graph_provider.get_user_group_by_external_id(connector_id, external_id, transaction=self.txn)

    async def delete_user_group_by_id(self, group_id: str) -> None:
        return await self.graph_provider.delete_nodes_and_edges([group_id],CollectionNames.GROUPS.value,graph_name="knowledgeGraph",transaction=self.txn)

    async def get_app_role_by_external_id(self, connector_id: str, external_id: str) -> Optional[AppRole]:
        return await self.graph_provider.get_app_role_by_external_id(connector_id, external_id, transaction=self.txn)

    async def get_users(self, org_id: str, active: bool = True) -> List[User]:
        users_dict = await self.graph_provider.get_users(org_id, active)
        return [User.from_arango_user(user_dict) for user_dict in users_dict if user_dict is not None]

    async def get_app_users(self, org_id: str, connector_id: str) -> List[AppUser]:
        app_users_dict = await self.graph_provider.get_app_users(org_id, connector_id)
        return [AppUser.from_arango_user(user_dict) for user_dict in app_users_dict if user_dict is not None]

    async def get_user_groups(self, connector_id: str, org_id: str) -> List[AppUserGroup]:
        return await self.graph_provider.get_user_groups(connector_id, org_id, transaction=self.txn)

    async def batch_upsert_people(self, people: List[Person]) -> None:
        return await self.graph_provider.batch_upsert_people(people, transaction=self.txn)

    async def create_user_group_hierarchy(
        self,
        child_external_id: str,
        parent_external_id: str,
        connector_id: str
    ) -> bool:
        """Create BELONGS_TO edge between child and parent user groups"""
        try:
            # Lookup both groups
            child_group = await self.get_user_group_by_external_id(connector_id, child_external_id)
            if not child_group:
                self.logger.warning(
                    f"Child user group not found: {child_external_id} (connector: {connector_id})"
                )
                return False

            parent_group = await self.get_user_group_by_external_id(connector_id, parent_external_id)
            if not parent_group:
                self.logger.warning(
                    f"Parent user group not found: {parent_external_id} (connector: {connector_id})"
                )
                return False

            # Create BELONGS_TO edge
            edge = {
                "from_id": child_group.id,
                "from_collection": CollectionNames.GROUPS.value,
                "to_id": parent_group.id,
                "to_collection": CollectionNames.GROUPS.value,
                "entityType": "GROUP",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.graph_provider.batch_create_edges(
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
        connector_id: str
    ) -> bool:
        """Create BELONGS_TO edge from user to group using source IDs"""
        try:
            # Lookup user by sourceUserId
            user = await self.get_user_by_source_id(user_source_id, connector_id)
            if not user:
                self.logger.warning(
                    f"User not found: {user_source_id} (connector: {connector_id})"
                )
                return False

            # Lookup group
            group = await self.get_user_group_by_external_id(connector_id, group_external_id)
            if not group:
                self.logger.warning(
                    f"User group not found: {group_external_id} (connector: {connector_id})"
                )
                return False

            # Create BELONGS_TO edge
            edge = {
                "from_id": user.id,
                "from_collection": CollectionNames.USERS.value,
                "to_id": group.id,
                "to_collection": CollectionNames.GROUPS.value,
                "entityType": "GROUP",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.graph_provider.batch_create_edges(
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

    async def get_first_user_with_permission_to_node(self, node_id: str, node_collection: str) -> Optional[User]:
        return await self.graph_provider.get_first_user_with_permission_to_node(node_id, node_collection, transaction=self.txn)

    async def get_users_with_permission_to_node(self, node_id: str, node_collection: str) -> List[User]:
        return await self.graph_provider.get_users_with_permission_to_node(node_id, node_collection, transaction=self.txn)

    async def get_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str) -> Optional[Dict]:
        return await self.graph_provider.get_edge(from_id, from_collection, to_id, to_collection, collection, transaction=self.txn)

    async def get_record_by_conversation_index(self, connector_id: str, conversation_index: str, thread_id: str, org_id: str, user_id: str) -> Optional[Record]:
        return await self.graph_provider.get_record_by_conversation_index(connector_id, conversation_index, thread_id, org_id, user_id, transaction=self.txn)

    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> Optional[Record]:
        """Get record by Jira issue key (e.g., PROJ-123) by searching weburl pattern."""
        return await self.graph_provider.get_record_by_issue_key(connector_id, issue_key, transaction=self.txn)

    async def get_record_by_weburl(self, weburl: str, org_id: Optional[str] = None) -> Optional[Record]:
        """Get record by weburl (exact match)."""
        return await self.graph_provider.get_record_by_weburl(weburl, org_id, transaction=self.txn)

    async def get_records_by_parent(
        self,
        connector_id: str,
        parent_external_record_id: str,
        record_type: Optional[str] = None
    ) -> List[Record]:
        """Get all child records for a parent record by parent_external_record_id. Optionally filter by record_type."""
        return await self.graph_provider.get_records_by_parent(
            connector_id, parent_external_record_id, record_type, transaction=self.txn
        )

    async def get_record_path(self, record_id: str) -> Optional[str]:
        """Get full hierarchical path for a record by traversing parent-child edges."""
        return await self.graph_provider.get_record_path(record_id, transaction=self.txn)

    async def batch_upsert_records(self, records: List[Record]) -> None:
        """
        Batch upsert records (base + specific type + IS_OF_TYPE edge).

        Delegates to graph_provider for the full record upsert logic.
        """
        return await self.graph_provider.batch_upsert_records(records, transaction=self.txn)

    async def batch_upsert_record_groups(self, record_groups: List[RecordGroup]) -> None:
        """
        Batch upsert record groups.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_record_groups(record_groups, transaction=self.txn)

    async def batch_upsert_record_permissions(self, record_id: str, permissions: List[Permission]) -> None:
        return await self.graph_provider.batch_upsert_record_permissions(record_id, permissions, transaction=self.txn)

    async def batch_create_user_app_edges(self, edges: List[Dict]) -> int:
        return await self.graph_provider.batch_create_user_app_edges(edges)

    async def batch_upsert_user_groups(self, user_groups: List[AppUserGroup]) -> None:
        """
        Batch upsert user groups.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_user_groups(user_groups, transaction=self.txn)

    async def batch_upsert_app_roles(self, app_roles: List[AppRole]) -> None:
        """
        Batch upsert app roles.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_app_roles(app_roles, transaction=self.txn)

    async def batch_upsert_app_users(self, users: List[AppUser]) -> None:
        """
        Batch upsert app users.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.batch_upsert_app_users(users, transaction=self.txn)

    async def batch_upsert_orgs(self, orgs: List[Org]) -> None:
        return await self.graph_provider.batch_upsert_orgs(orgs, transaction=self.txn)

    async def batch_upsert_domains(self, domains: List[Domain]) -> None:
        return await self.graph_provider.batch_upsert_domains(domains, transaction=self.txn)

    async def batch_upsert_anyone(self, anyone: List[Anyone]) -> None:
        return await self.graph_provider.batch_upsert_anyone(anyone, transaction=self.txn)

    async def batch_upsert_anyone_with_link(self, anyone_with_link: List[AnyoneWithLink]) -> None:
        return await self.graph_provider.batch_upsert_anyone_with_link(anyone_with_link, transaction=self.txn)

    async def batch_upsert_anyone_same_org(self, anyone_same_org: List[AnyoneSameOrg]) -> None:
        return await self.graph_provider.batch_upsert_anyone_same_org(anyone_same_org, transaction=self.txn)

    async def commit(self) -> None:
        """
        Commit the transaction.

        With HTTP provider: Makes async HTTP call (PUT /_api/transaction/{txn_id})
        With SDK provider: Wrapped in executor for backward compatibility
        """
        self.logger.debug(f"💾 Committing transaction {self.txn}...")
        await self.graph_provider.commit_transaction(self.txn)
        self.logger.debug(f"✅ Transaction {self.txn} committed")

    async def rollback(self) -> None:
        """
        Rollback the transaction.

        With HTTP provider: Makes async HTTP call (DELETE /_api/transaction/{txn_id})
        With SDK provider: Wrapped in executor for backward compatibility
        """
        self.logger.debug(f"🔄 Rolling back transaction {self.txn}...")
        await self.graph_provider.rollback_transaction(self.txn)
        self.logger.debug(f"✅ Transaction {self.txn} rolled back")

    async def create_record_relation(
        self,
        from_record_id: str,
        to_record_id: str,
        relation_type: str
    ) -> None:
        """
        Create a relation edge between two records.

        Delegates to graph_provider for implementation.

        Args:
            from_record_id: Source record ID
            to_record_id: Target record ID
            relation_type: Type of relation (e.g., "BLOCKS", "CLONES", etc.)
        """
        return await self.graph_provider.create_record_relation(
            from_record_id, to_record_id, relation_type, transaction=self.txn
        )
    async def create_record_group_relation(self, record_id: str, record_group_id: str) -> None:
        """
        Create BELONGS_TO edge from record to record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_record_group_relation(record_id, record_group_id, transaction=self.txn)

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        """
        Create INHERIT_PERMISSIONS edge from record to record group.

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.create_inherit_permissions_relation_record_group(
            record_id, record_group_id, transaction=self.txn
        )

    async def delete_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        """
        Delete INHERIT_PERMISSIONS edge from record to record group.
        Called when a record's inherit_permissions is False, to remove a previously created edge (e.g. from placeholder).

        Delegates to graph_provider for implementation.
        """
        return await self.graph_provider.delete_inherit_permissions_relation_record_group(
            record_id, record_group_id, transaction=self.txn
        )

    async def create_inherit_permissions_relation_record(self, child_record_id: str, parent_record_id: str) -> None:
        record_edge = {
                    "from_id": child_record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": parent_record_id,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
        await self.graph_provider.batch_create_edges(
            [record_edge], collection=CollectionNames.INHERIT_PERMISSIONS.value, transaction=self.txn
        )
    async def get_sync_point(self, sync_point_key: str) -> Optional[Dict]:
        return await self.graph_provider.get_sync_point(sync_point_key, CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def get_all_orgs(self) -> List[Org]:
        return await self.graph_provider.get_all_orgs()

    async def create_user_groups(self, user_groups: List[AppUserGroup]) -> None:
        return await self.graph_provider.batch_upsert_nodes([user_group.to_arango_base_user_group() for user_group in user_groups],
                    collection=CollectionNames.GROUPS.value, transaction=self.txn)

    async def create_users(self, users: List[AppUser]) -> None:
        return await self.graph_provider.batch_upsert_nodes([user.to_arango_base_user() for user in users],
                    collection=CollectionNames.USERS.value, transaction=self.txn)

    async def create_orgs(self, orgs: List[Org]) -> None:
        return await self.graph_provider.batch_upsert_nodes([org.to_arango_base_org() for org in orgs],
                    collection=CollectionNames.ORGS.value, transaction=self.txn)

    async def create_domains(self, domains: List[Domain]) -> None:
        return await self.graph_provider.batch_upsert_nodes([domain.to_arango_base_domain() for domain in domains],
                    collection=CollectionNames.DOMAINS.value, transaction=self.txn)

    async def create_anyone(self, anyone: List[Anyone]) -> None:
        return await self.graph_provider.batch_upsert_nodes([anyone_item.to_arango_base_anyone() for anyone_item in anyone],
                    collection=CollectionNames.ANYONE.value, transaction=self.txn)

    async def create_anyone_with_link(self, anyone_with_link: List[AnyoneWithLink]) -> None:
        return await self.graph_provider.batch_upsert_nodes([anyone_with_link_item.to_arango_base_anyone_with_link() for anyone_with_link_item in anyone_with_link],
                    collection=CollectionNames.ANYONE_WITH_LINK.value, transaction=self.txn)

    async def create_anyone_same_org(self, anyone_same_org: List[AnyoneSameOrg]) -> None:
        return await self.graph_provider.batch_upsert_nodes([anyone_same_org_item.to_arango_base_anyone_same_org() for anyone_same_org_item in anyone_same_org],
                    collection=CollectionNames.ANYONE_SAME_ORG.value, transaction=self.txn)

    async def create_sync_point(self, sync_point_key: str, sync_point_data: Dict) -> None:
        return await self.graph_provider.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def delete_sync_point(self, sync_point_key: str) -> None:
        return await self.graph_provider.remove_sync_point([sync_point_key],
                    collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)
    async def read_sync_point(self, sync_point_key: str) -> None:
        return await self.graph_provider.get_sync_point(sync_point_key, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def update_sync_point(self, sync_point_key: str, sync_point_data: Dict) -> None:
        return await self.graph_provider.upsert_sync_point(sync_point_key, sync_point_data, collection=CollectionNames.SYNC_POINTS.value, transaction=self.txn)

    async def batch_upsert_record_group_permissions(
        self, record_group_id: str, permissions: List[Permission], connector_id: str
    ) -> None:
        """
        Batch upsert permissions for a record group.

        Creates permission edges from users/groups to the record group.
        Looks up users by email and groups by external_id, then creates
        the appropriate permission edges.

        Args:
            record_group_id: Internal ID (_key) of the record group
            permissions: List of Permission objects
            connector_id: Connector ID for scoped group lookups
        """
        if not permissions:
            return

        permission_edges = []
        to_id = record_group_id
        to_collection = CollectionNames.RECORD_GROUPS.value

        for permission in permissions:
            from_id = None
            from_collection = None

            if permission.entity_type.value == "USER":
                # Lookup user by email
                user = None
                if permission.email:
                    user = await self.get_user_by_email(permission.email)

                if user:
                    from_id = user.id
                    from_collection = CollectionNames.USERS.value
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
                        connector_id, permission.external_id
                    )

                if user_group:
                    from_id = user_group.id
                    from_collection = CollectionNames.GROUPS.value
                else:
                    self.logger.warning(
                        f"Group not found for external_id: {permission.external_id}"
                    )
                    continue

            if from_id and from_collection:
                permission_edges.append(
                    permission.to_arango_permission(from_id, from_collection, to_id, to_collection)
                )

        if permission_edges:
            await self.graph_provider.batch_create_edges(
                permission_edges,
                collection=CollectionNames.PERMISSION.value,
                transaction=self.txn
            )

    async def batch_create_edges(self, edges: List[Dict], collection: str) -> None:
        return await self.graph_provider.batch_create_edges(edges, collection=collection, transaction=self.txn)

    async def batch_create_entity_relations(self, edges: List[Dict]) -> None:
        """Batch create entity relation edges with edgeType in UPSERT match condition."""
        return await self.graph_provider.batch_create_entity_relations(edges, transaction=self.txn)

    async def get_edges_to_node(self, node_id: str, edge_collection: str) -> List[Dict]:
        """Get all edges pointing to a specific node"""
        return await self.graph_provider.get_edges_to_node(node_id, edge_collection, transaction=self.txn)

    async def get_edges_from_node(self, from_node_id: str, edge_collection: str) -> List[Dict]:
        """Get all edges originating from a specific node"""
        return await self.graph_provider.get_edges_from_node(from_node_id, edge_collection, transaction=self.txn)

    async def get_related_node_field(
        self, node_id: str, edge_collection: str, target_collection: str,
        field: str, direction: str = "outbound"
    ) -> List:
        """Get specific field values from related nodes"""
        return await self.graph_provider.get_related_node_field(
            node_id, edge_collection, target_collection, field, direction, transaction=self.txn
        )

    async def delete_records_and_relations(self, record_key: str, hard_delete: bool = False) -> None:
        """Delete a record and all its relations"""
        return await self.graph_provider.delete_records_and_relations(record_key, hard_delete, transaction=self.txn)

    async def process_file_permissions(self, org_id: str, file_key: str, permissions: List[Dict]) -> None:
        """Process file permissions"""
        return await self.graph_provider.process_file_permissions(org_id, file_key, permissions, transaction=self.txn)

    async def get_nodes_by_field_in(
        self, collection: str, field: str, values: List, return_fields: List[str] = None
    ) -> List[Dict]:
        """Get nodes where field value is in list"""
        return await self.graph_provider.get_nodes_by_field_in(
            collection, field, values, return_fields, transaction=self.txn
        )

    async def remove_nodes_by_field(self, collection: str, field: str, value) -> int:
        """Remove nodes matching field value"""
        return await self.graph_provider.remove_nodes_by_field(
            collection, field, value, transaction=self.txn
        )

    async def get_nodes_by_filters(
        self,
        collection: str,
        filters: Dict,
        return_fields: Optional[List[str]] = None
    ) -> List[Dict]:
        """Get nodes from a collection matching multiple field filters."""
        return await self.graph_provider.get_nodes_by_filters(
            collection=collection,
            filters=filters,
            return_fields=return_fields,
            transaction=self.txn
        )


class GraphDataStore(DataStoreProvider):
    """
    Graph database data store using IGraphDBProvider.

    """

    def __init__(self, logger: Logger, graph_provider: IGraphDBProvider) -> None:
        self.logger = logger
        self.graph_provider = graph_provider

    @asynccontextmanager
    async def transaction(self) -> AsyncContextManager["TransactionStore"]:
        """
        Create a graph database transaction store context manager.

        With HTTP provider (ArangoHTTPProvider):
        - begin_transaction() returns transaction ID (string) - fully async
        - All operations pass txn_id in HTTP headers
        - commit/rollback are fully async HTTP calls

        """
        # Begin transaction - returns transaction ID (str) for HTTP provider
        self.logger.debug("🔄 Beginning transaction...")
        txn = await self.graph_provider.begin_transaction(
            read=read_collections,
            write=write_collections
        )
        self.logger.debug(f"✅ Transaction started with ID: {txn}")

        tx_store = GraphTransactionStore(self.graph_provider, txn)

        try:
            yield tx_store
        except Exception as e:
            self.logger.error(f"❌ Transaction error, rolling back: {str(e)}")
            await tx_store.rollback()
            raise
        else:
            await tx_store.commit()

    async def execute_in_transaction(self, func, *args, **kwargs) -> None:
        """Execute function within graph database transaction"""
        async with self.transaction() as tx_store:
            return await func(tx_store, *args, **kwargs)

