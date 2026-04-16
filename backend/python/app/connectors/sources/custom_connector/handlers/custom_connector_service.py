import uuid
from typing import Dict, List, Optional, Tuple, Union

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.services.kafka_service import KafkaService
from app.models.entities import (
    AppRole,
    AppUserGroup,
    Record,
    RecordType,
)
from app.models.permission import Permission
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

VALID_ROLES = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]

# Parent record types for which a child FILE creates an ATTACHMENT edge
# (matches DataSourceEntitiesProcessor.ATTACHMENT_CONTAINER_TYPES). For all
# other (parent_type, child_type) combinations we fall back to PARENT_CHILD.
ATTACHMENT_CONTAINER_TYPES = {
    RecordType.MAIL.value,
    RecordType.GROUP_MAIL.value,
    RecordType.WEBPAGE.value,
    RecordType.CONFLUENCE_PAGE.value,
    RecordType.CONFLUENCE_BLOGPOST.value,
    RecordType.SHAREPOINT_PAGE.value,
    RecordType.PROJECT.value,
    RecordType.LINK.value,
    RecordType.TICKET.value,
    RecordType.DEAL.value,
    RecordType.CASE.value,
    RecordType.TASK.value,
}

read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]


class CustomConnectorService:
    """Generic service for custom connector instances — SDK surface.

    Covers:
      - RecordGroup CRUD (instances / collections)
      - Folder CRUD
      - Record CRUD for ANY RecordType (file, mail, ticket, webpage, message, ...)
          · create_record / create_records — pass any Record subclass (FileRecord,
            MailRecord, TicketRecord, WebpageRecord, MessageRecord, etc.).
            Caller controls parent_type ("folder" | "record" | "recordGroup")
      - Record-to-record relations (PARENT_CHILD / LINKED_TO / ATTACHMENT / SIBLING / BLOCKS / ...)
      - Permission management on any node (recordGroup OR record)
          · entities: USER, GROUP, ROLE (generic SDK)
          · TEAM entities supported but KB-specific (kept for KB compatibility)
      - Group CRUD (create_group, list_groups, update_group, delete_group,
        add_group_members, remove_group_members)
      - Role CRUD (create_role, list_roles, update_role, delete_role,
        add_role_members, remove_role_members)

    Permission gating:
      - Record/RG writes: OWNER | WRITER on the parent instance
      - Record relations: WRITER+ on the instance of BOTH endpoints
      - Group/role admin (add/remove members, update, delete): OWNER on the group/role
      - Adding permissions (add_permissions_to_node): OWNER on the target node

    KnowledgeBaseService is a thin wrapper around this service, passing
    connector_type=KNOWLEDGE_BASE and is_restricted=True.
    """

    def __init__(
        self,
        logger,
        graph_provider: IGraphDBProvider,
        kafka_service: KafkaService,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.kafka_service = kafka_service

    # ==================== Instance CRUD ====================

    async def create_record_group(
        self,
        user_id: str,
        org_id: str,
        name: str,
        group_type: str,
        connector_id: str,
        external_group_id: Optional[str] = None,
        parent_external_group_id: Optional[str] = None,
        is_restricted: bool = False,
        inherit_permissions: bool = False,
    ) -> Optional[Dict]:
        """Create a recordGroup node + PERMISSION (OWNER) + BELONGS_TO edges in the graph DB.

        `connector_type` is derived from the parent app document's `type` field.

        Hierarchy + permission flags:
          - `external_group_id`: caller-supplied id from the external system (required).
          - `parent_external_group_id`: if provided AND it resolves to another RG of the
            same connector, this new RG is linked to that parent via:
              * PARENT_CHILD edge (always when parent resolves)
              * INHERIT_PERMISSIONS edge (only if `inherit_permissions=True`) — points
                to parent RG (NOT app)
          - If no `parent_external_group_id` and `inherit_permissions=True`:
              * INHERIT_PERMISSIONS edge → parent app (original behavior, used by KB)
          - `is_restricted` (bool): stored on the document as `isRestricted`. No edge
            behavior tied to it — reserved for future use. KB always sets True.
        """
        txn_id = None
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }

            # Validate the parent connector (app) exists — avoids orphan BELONGS_TO edges.
            # Derive connector_type from the app doc so callers don't have to pass it.
            app_doc = await self.graph_provider.get_document(
                connector_id, CollectionNames.APPS.value
            )
            if not app_doc:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Connector instance (app) not found: {connector_id}",
                }
            connector_type = (app_doc.get("type") or app_doc.get("name") or "").upper()
            if not connector_type:
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"App document {connector_id} is missing a 'type' field",
                }

            self.logger.info(
                f"Creating record group '{name}' (type={connector_type}) "
                f"for user {user_id} in org {org_id}"
            )

            user_key = user.get("id") or user.get("_key")
            timestamp = get_epoch_timestamp_in_ms()
            instance_key = str(uuid.uuid4())
            # When caller doesn't supply an external id (e.g. KB), default to the
            # internal key so the recordGroup document always has a non-null externalGroupId.
            if not external_group_id:
                external_group_id = instance_key

            # Resolve parent recordGroup (if caller provided one) BEFORE opening the
            # transaction — it's a read-only lookup and any failure should short-circuit.
            parent_rg_internal_id: Optional[str] = None
            if parent_external_group_id:
                parent_rg = await self.graph_provider.get_record_group_by_external_id(
                    connector_id=connector_id, external_id=parent_external_group_id
                )
                if parent_rg is None:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": (
                            f"Parent recordGroup with external_group_id "
                            f"'{parent_external_group_id}' not found in connector {connector_id}"
                        ),
                    }
                # Pydantic RecordGroup vs dict — handle both
                parent_rg_internal_id = (
                    getattr(parent_rg, "id", None)
                    or (parent_rg.get("id") if isinstance(parent_rg, dict) else None)
                    or (parent_rg.get("_key") if isinstance(parent_rg, dict) else None)
                )

            txn_id = await self.graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.RECORD_GROUPS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.PERMISSION.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                ],
            )

            instance_data = {
                "id": instance_key,
                "createdBy": user_id,  # MongoDB userId — matches the APPS collection convention
                "orgId": org_id,
                "groupName": name,
                "groupType": group_type,
                "connectorName": connector_type,
                "connectorId": connector_id,
                "externalGroupId": external_group_id,
                "parentExternalGroupId": parent_external_group_id,
                "isRestricted": is_restricted,
                "inheritPermissions": inherit_permissions,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": instance_key,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }

            belongs_to_app_edge = {
                "from_id": instance_key,
                "from_collection": CollectionNames.RECORD_GROUPS.value,
                "to_id": connector_id,
                "to_collection": CollectionNames.APPS.value,
                "entityType": group_type,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            # RG → ORG BELONGS_TO edge (matches DataSourceEntitiesProcessor behavior)
            belongs_to_org_edge = {
                "from_id": instance_key,
                "from_collection": CollectionNames.RECORD_GROUPS.value,
                "to_id": org_id,
                "to_collection": CollectionNames.ORGS.value,
                "entityType": "ORGANIZATION",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            await self.graph_provider.batch_upsert_nodes(
                [instance_data],
                CollectionNames.RECORD_GROUPS.value,
                transaction=txn_id,
            )
            await self.graph_provider.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSION.value,
                transaction=txn_id,
            )
            await self.graph_provider.batch_create_edges(
                [belongs_to_app_edge, belongs_to_org_edge],
                CollectionNames.BELONGS_TO.value,
                transaction=txn_id,
            )
            # PARENT_CHILD edge to parent recordGroup (when a parent was resolved)
            if parent_rg_internal_id:
                parent_child_edge = {
                    "from_id": parent_rg_internal_id,
                    "from_collection": CollectionNames.RECORD_GROUPS.value,
                    "to_id": instance_key,
                    "to_collection": CollectionNames.RECORD_GROUPS.value,
                    "relationshipType": RecordRelations.PARENT_CHILD.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.graph_provider.batch_create_edges(
                    [parent_child_edge],
                    CollectionNames.RECORD_RELATIONS.value,
                    transaction=txn_id,
                )

            # INHERIT_PERMISSIONS edge — target depends on whether a parent RG exists.
            # With parent RG resolved → inherit from parent RG (NOT app).
            # Without parent RG → inherit from the connector app (original behavior).
            if inherit_permissions:
                if parent_rg_internal_id:
                    inherit_target_id = parent_rg_internal_id
                    inherit_target_collection = CollectionNames.RECORD_GROUPS.value
                else:
                    inherit_target_id = connector_id
                    inherit_target_collection = CollectionNames.APPS.value
                inherit_permissions_edge = {
                    "from_id": instance_key,
                    "from_collection": CollectionNames.RECORD_GROUPS.value,
                    "to_id": inherit_target_id,
                    "to_collection": inherit_target_collection,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.graph_provider.batch_create_edges(
                    [inherit_permissions_edge],
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    transaction=txn_id,
                )
            await self.graph_provider.commit_transaction(txn_id)
            txn_id = None

            self.logger.info(f"Record group '{name}' created: {instance_key}")
            return {
                "id": instance_data["id"],
                "name": instance_data["groupName"],
                "createdAtTimestamp": instance_data["createdAtTimestamp"],
                "updatedAtTimestamp": instance_data["updatedAtTimestamp"],
                "success": True,
                "userRole": "OWNER",
            }

        except Exception as e:
            self.logger.error(f"Record group creation failed for '{name}': {str(e)}")
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def get_record_group(
        self,
        record_group_id: str,
        user_id: str,
        group_type: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get record group (e.g. KB / Collection) details with permission check.

        `group_type` is accepted for backwards compatibility but is no longer
        required — the provider matches by record_group_id alone.
        """
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=record_group_id, user_id=user_key
            )
            if not user_role:
                return {
                    "success": False,
                    "reason": "User has no permission for this record group",
                    "code": "403",
                }

            result = await self.graph_provider.get_record_group(
                record_group_id=record_group_id,
                user_id=user_key,
                group_type=group_type,
            )

            if result:
                return result
            return {
                "success": False,
                "reason": "Record group not found",
                "code": "404",
            }

        except Exception as e:
            self.logger.error(f"Failed to get record group: {str(e)}")
            return {"success": False, "reason": str(e), "code": "500"}

    async def list_user_record_groups(
        self,
        user_id: str,
        org_id: str,
        group_type: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        connector_id: Optional[str] = None,
    ) -> Union[List[Dict], Dict]:
        """List user's record groups (e.g. KBs / Collections) with pagination and filtering.

        `connector_id` (optional) restricts results to record groups belonging to a
        specific app instance — pushed down to the provider query.
        """
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            skip = (page - 1) * limit
            valid_sort_fields = ["name", "updatedAtTimestamp", "createdAtTimestamp", "userRole"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            record_groups, total_count, available_filters = (
                await self.graph_provider.list_user_record_groups(
                    user_id=user_key,
                    org_id=org_id,
                    group_type=group_type,
                    skip=skip,
                    limit=limit,
                    search=search,
                    permissions=permissions,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    connector_id=connector_id,
                )
            )

            if isinstance(record_groups, dict) and record_groups.get("success") is False:
                return record_groups

            total_pages = (total_count + limit - 1) // limit
            applied_filters = {}
            if search:
                applied_filters["search"] = search
            if permissions:
                applied_filters["permissions"] = permissions
            if sort_by != "name":
                applied_filters["sort_by"] = sort_by
            if sort_order != "asc":
                applied_filters["sort_order"] = sort_order
            if connector_id:
                applied_filters["connector_id"] = connector_id

            return {
                "recordGroups": record_groups,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1,
                },
                "filters": {
                    "applied": applied_filters,
                    "available": available_filters,
                },
            }

        except Exception as e:
            self.logger.error(f"Failed to list record groups: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def update_record_group(
        self,
        record_group_id: str,
        user_id: str,
        updates: Dict,
    ) -> Optional[Dict]:
        """Update record group (e.g. KB / Collection) details."""
        try:
            timestamp = get_epoch_timestamp_in_ms()
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=record_group_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "reason": "User has no permission to update this record group",
                    "code": "403",
                }

            updates["updatedAtTimestamp"] = timestamp
            result = await self.graph_provider.update_record_group(
                record_group_id=record_group_id, updates=updates
            )

            if result:
                return {"success": True, "reason": "Record group updated", "code": "200"}
            return {"success": False, "reason": "Record group not found", "code": "404"}

        except Exception as e:
            self.logger.error(f"Failed to update record group: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def delete_record_group(
        self,
        record_group_id: str,
        user_id: str,
    ) -> Optional[Dict]:
        """Delete a record group (e.g. KB / Collection) and all its nested content.

        Cascades to records, files, folders, and edges belonging to this record
        group. Does NOT touch the parent apps-row connector instance — whole
        connector deletion is handled separately by the connectors DELETE
        endpoint (see event_service._handle_delete).
        """
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=record_group_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "reason": "Insufficient permissions to delete record group",
                    "code": 403,
                }

            result = await self.graph_provider.delete_record_group(
                record_group_id=record_group_id,
            )

            if result and result.get("success"):
                return {
                    "success": True,
                    "reason": "Record group and all contents deleted",
                    "code": 200,
                    "eventData": result.get("eventData"),
                }
            return {"success": False, "reason": "Failed to delete record group", "code": 500}

        except Exception as e:
            self.logger.error(f"Failed to delete record group {record_group_id}: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    # ==================== Folder Operations ====================

    async def create_folder(
        self,
        instance_id: str,
        name: str,
        user_id: str,
        org_id: str,
        is_restricted: bool = False,
        inherit_permissions: bool = True,
        parent_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create folder in connector instance (root or nested).

        - `is_restricted`: stored on the folder record document only. Reserved for future use.
        - `inherit_permissions`: when True (default), creates INHERIT_PERMISSIONS edge from
          this folder → its immediate parent (another folder if nested, else the recordGroup).
        - `parent_id`: folder hierarchy link. Independent of the two flags above — always
          creates the PARENT_CHILD edge when set.
        """
        try:
            location = "root" if parent_id is None else f"folder {parent_id}"
            self.logger.info(f"Creating folder '{name}' in instance {instance_id} at {location}")

            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions to create folder. Role: {user_role}",
                }

            if parent_id is not None:
                folder_valid = await self.graph_provider.validate_folder_exists_in_record_group(
                    instance_id, parent_id
                )
                if not folder_valid:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Parent folder {parent_id} not found in instance {instance_id}",
                    }

            existing_folder = await self.graph_provider.find_folder_by_name_in_record_group(
                record_group_id=instance_id,
                folder_name=name,
                parent_folder_id=parent_id,
            )
            if existing_folder:
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists in {location}",
                }

            result = await self.graph_provider.create_generic_folder(
                instance_id=instance_id,
                folder_name=name,
                org_id=org_id,
                is_restricted=is_restricted,
                inherit_permissions=inherit_permissions,
                parent_folder_id=parent_id,
            )

            if result and result.get("success"):
                return result
            return {"success": False, "code": 500, "reason": "Failed to create folder"}

        except Exception as e:
            self.logger.error(f"Folder creation failed: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def get_folder_contents(
        self,
        instance_id: str,
        folder_id: str,
        user_id: str,
    ) -> Dict:
        """Get contents of a folder."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if not user_role:
                return {
                    "success": False,
                    "reason": "User has no permission for this connector instance",
                    "code": "403",
                }

            result = await self.graph_provider.get_folder_contents(
                kb_id=instance_id, folder_id=folder_id
            )
            if result:
                return result
            return {
                "success": False,
                "code": 400,
                "reason": "Failed to get folder contents, or folder not found",
            }

        except Exception as e:
            self.logger.error(f"Failed to get folder contents: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def update_folder(
        self,
        instance_id: str,
        folder_id: str,
        user_id: str,
        name: str,
    ) -> Dict:
        """Update folder name."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "reason": "User has no permission to update folder",
                    "code": "403",
                }

            folder_exists = await self.graph_provider.validate_folder_exists_in_record_group(
                instance_id, folder_id
            )
            if not folder_exists:
                return {
                    "success": False,
                    "reason": "Folder not found in connector instance",
                    "code": "404",
                }

            existing_folder = await self.graph_provider.find_folder_by_name_in_record_group(
                record_group_id=instance_id,
                folder_name=name,
                parent_folder_id=None,
            )
            if existing_folder:
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists",
                }

            result = await self.graph_provider.update_folder(
                folder_id=folder_id, updates={"name": name}
            )

            if result:
                return {"success": True, "code": 200, "reason": "Folder updated"}
            return {"success": False, "code": 500, "reason": "Failed to update folder"}

        except Exception as e:
            self.logger.error(f"Failed to update folder: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def delete_folder(
        self,
        instance_id: str,
        folder_id: str,
        user_id: str,
    ) -> Dict:
        """Delete a folder and all its contents."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "reason": "User lacks permission to delete folder",
                    "code": "403",
                }

            folder_exists = await self.graph_provider.validate_folder_exists_in_record_group(
                instance_id, folder_id
            )
            if not folder_exists:
                return {
                    "success": False,
                    "reason": "Folder not found in connector instance",
                    "code": "404",
                }

            result = await self.graph_provider.delete_generic_folder(
                instance_id=instance_id, folder_id=folder_id
            )

            if result and result.get("success"):
                return {
                    "success": True,
                    "reason": "Folder and all contents deleted",
                    "code": 200,
                    "eventData": result.get("eventData"),
                }
            return {"success": False, "code": 500, "reason": "Failed to delete folder"}

        except Exception as e:
            self.logger.error(f"Failed to delete folder: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    # ==================== Record Operations ====================

    async def create_record(
        self,
        instance_id: str,
        record: Record,
        user_id: str,
        permissions: Optional[List[Permission]] = None,
    ) -> Dict:
        """Generic record creation for any RecordType (FILE/MAIL/TICKET/WEBPAGE/MESSAGE/...).

        - instance_id: target recordGroup (connector instance) id
        - record: a Record subclass instance with all required base fields populated.
                  Per-record fields read by this method:
                    · `inherit_permissions` (bool, default True) — when True, an
                      INHERIT_PERMISSIONS edge is created from this record to its
                      immediate parent (resolved parent record, else recordGroup).
                    · `parent_external_record_id` + `parent_record_type` — when the
                      external id resolves inside this instance, a parent edge is
                      created (ATTACHMENT if child is FILE and parent type is in
                      ATTACHMENT_CONTAINER_TYPES, else PARENT_CHILD).
                    · `is_restricted` — stored as-is on the record document; no
                      edge behavior is tied to it (reserved for future use).
        - permissions: optional list of Permission objects (USER/GROUP/ROLE entities)

        Under the hood: validates permission, then in one transaction:
          upserts the record (base + type-specific + IS_OF_TYPE) → BELONGS_TO edge →
          optional PARENT_CHILD/ATTACHMENT edge → optional INHERIT_PERMISSIONS edge →
          PERMISSION edges. Publishes 'newRecord' Kafka event after commit.
        """
        return await self.create_records(
            instance_id=instance_id,
            records_with_permissions=[(record, permissions or [])],
            user_id=user_id,
        )

    async def create_records(
        self,
        instance_id: str,
        records_with_permissions: List[Tuple[Record, List[Permission]]],
        user_id: str,
    ) -> Dict:
        """Batched generic record creation. Preferred for bulk SDK ingest.

        All records are created in a single transaction — on failure, nothing is written.
        Each record drives its own parent linkage and permission inheritance via fields
        on the Record object (see `create_record` docstring).
        """
        txn_id: Optional[str] = None
        if not records_with_permissions:
            return {"success": False, "code": 400, "reason": "No records provided"}

        try:
            # Permission gate — requester must be WRITER+ on instance
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return self._error_response(403, f"Insufficient permissions on instance. Role: {user_role}")

            # Fetch instance metadata (org_id, connector_id, connector_name)
            instance_doc = await self.graph_provider.get_document(
                instance_id, CollectionNames.RECORD_GROUPS.value
            )
            if not instance_doc:
                return self._error_response(404, f"Instance {instance_id} not found")
            instance_org_id = instance_doc.get("orgId", "")
            instance_connector_id = instance_doc.get("connectorId", "")

            # Resolve any per-record parent references via parent_external_record_id
            # BEFORE opening the transaction (separate read query). Returns map of
            # external_record_id → internal record id (only entries that actually
            # exist within this instance's connector).
            parent_external_ids = {
                r.parent_external_record_id
                for r, _ in records_with_permissions
                if getattr(r, "parent_external_record_id", None)
            }
            resolved_parents: Dict[str, str] = {}
            for ext_id in parent_external_ids:
                parent_record = await self.graph_provider.get_record_by_external_id(
                    connector_id=instance_connector_id, external_id=ext_id
                )
                if parent_record is None:
                    self.logger.warning(
                        f"Parent record with external_id '{ext_id}' not found in connector "
                        f"{instance_connector_id} — child record will be created without a parent edge"
                    )
                    continue
                # Pydantic Record vs dict — handle both shapes
                pid = (
                    getattr(parent_record, "id", None)
                    or (parent_record.get("id") if isinstance(parent_record, dict) else None)
                    or (parent_record.get("_key") if isinstance(parent_record, dict) else None)
                )
                if pid:
                    resolved_parents[ext_id] = pid

            txn_id = await self.graph_provider.begin_transaction(
                read=[],
                write=[
                    # base + type-specific record collections (all entries from RECORD_TYPE_COLLECTION_MAPPING)
                    CollectionNames.RECORDS.value,
                    CollectionNames.FILES.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.WEBPAGES.value,
                    CollectionNames.TICKETS.value,
                    CollectionNames.COMMENTS.value,
                    CollectionNames.LINKS.value,
                    CollectionNames.PROJECTS.value,
                    CollectionNames.PRODUCTS.value,
                    CollectionNames.DEALS.value,
                    CollectionNames.PULLREQUESTS.value,
                    CollectionNames.MEETINGS.value,
                    # edge collections
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    CollectionNames.PERMISSION.value,
                ],
            )

            # Fill defaults the service owns and collect records for batch upsert.
            # NOTE: `is_restricted` and `inherit_permissions` come from the Record
            # object (set by the caller / router auto-fill) — do NOT override them.
            timestamp = get_epoch_timestamp_in_ms()
            records_list: List[Record] = []
            for record, _perms in records_with_permissions:
                if not record.org_id:
                    record.org_id = instance_org_id
                if not record.connector_id:
                    record.connector_id = instance_connector_id
                record.record_group_id = instance_id
                record.external_record_group_id = record.external_record_group_id or instance_id
                records_list.append(record)

            # 1) Batch upsert — creates base + type-specific + IS_OF_TYPE for all records
            await self.graph_provider.batch_upsert_records(records_list, transaction=txn_id)

            created_ids: List[str] = []
            for record, permissions in records_with_permissions:
                record_id = record.id
                created_ids.append(record_id)

                # 2) BELONGS_TO edge: record → instance
                await self.graph_provider.create_record_group_relation(
                    record_id=record_id, record_group_id=instance_id, transaction=txn_id
                )

                # 3) Parent edge — PARENT_CHILD or ATTACHMENT — driven by Record fields.
                # Resolved via parent_external_record_id; if it didn't resolve, no edge.
                parent_internal_id: Optional[str] = None
                ext_parent_id = getattr(record, "parent_external_record_id", None)
                if ext_parent_id and ext_parent_id in resolved_parents:
                    parent_internal_id = resolved_parents[ext_parent_id]
                    parent_type_val = getattr(record, "parent_record_type", None)
                    parent_type_str = (
                        parent_type_val.value if hasattr(parent_type_val, "value")
                        else (parent_type_val or "")
                    )
                    child_type_val = getattr(record, "record_type", None)
                    child_type_str = (
                        child_type_val.value if hasattr(child_type_val, "value")
                        else (child_type_val or "")
                    )
                    is_attachment = (
                        child_type_str == RecordType.FILE.value
                        and parent_type_str in ATTACHMENT_CONTAINER_TYPES
                    )
                    relation_type = (
                        RecordRelations.ATTACHMENT.value
                        if is_attachment
                        else RecordRelations.PARENT_CHILD.value
                    )
                    await self.graph_provider.create_record_relation(
                        from_record_id=parent_internal_id,
                        to_record_id=record_id,
                        relation_type=relation_type,
                        transaction=txn_id,
                    )

                # 4) INHERIT_PERMISSIONS edge — driven by record.inherit_permissions.
                # Target is the immediate parent: resolved parent record if any,
                # otherwise the recordGroup itself.
                if getattr(record, "inherit_permissions", True):
                    if parent_internal_id:
                        inherit_target_id = parent_internal_id
                        inherit_target_collection = CollectionNames.RECORDS.value
                    else:
                        inherit_target_id = instance_id
                        inherit_target_collection = CollectionNames.RECORD_GROUPS.value
                    inherit_edge = {
                        "from_id": record_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": inherit_target_id,
                        "to_collection": inherit_target_collection,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    await self.graph_provider.batch_create_edges(
                        [inherit_edge],
                        CollectionNames.INHERIT_PERMISSIONS.value,
                        transaction=txn_id,
                    )

                # 5) PERMISSION edges for each Permission in the list
                if permissions:
                    await self._add_permissions_to_record(
                        record_id=record_id,
                        permissions=permissions,
                        transaction=txn_id,
                    )

            await self.graph_provider.commit_transaction(txn_id)
            txn_id = None

            # 6) Publish 'newRecord' Kafka events (after successful commit).
            # Skip for records the caller marked as non-indexable (matches the
            # DataSourceEntitiesProcessor behavior used by Jira / Gmail / Slack).
            auto_index_off = ProgressStatus.AUTO_INDEX_OFF.value if hasattr(ProgressStatus, "AUTO_INDEX_OFF") else "AUTO_INDEX_OFF"
            for record in records_list:
                if getattr(record, "is_internal", False):
                    self.logger.debug(f"Skipping Kafka newRecord for internal record {record.id}")
                    continue
                if getattr(record, "indexing_status", None) == auto_index_off:
                    self.logger.debug(f"Skipping Kafka newRecord for AUTO_INDEX_OFF record {record.id}")
                    continue
                try:
                    await self.kafka_service.publish_event(
                        "record-events",
                        {
                            "eventType": "newRecord",
                            "timestamp": get_epoch_timestamp_in_ms(),
                            "payload": record.to_kafka_record(),
                        },
                    )
                except Exception as kafka_err:
                    # Kafka failure doesn't undo the commit; log and continue
                    self.logger.error(f"Failed to publish newRecord event for {record.id}: {str(kafka_err)}")

            return {
                "success": True,
                "created": len(created_ids),
                "recordIds": created_ids,
                "instanceId": instance_id,
            }

        except Exception as e:
            self.logger.error(f"create_records failed: {str(e)}", exc_info=True)
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return self._error_response(500, str(e))

    async def _add_permissions_to_record(
        self,
        record_id: str,
        permissions: List[Permission],
        transaction: Optional[str] = None,
    ) -> None:
        """Resolve Permission objects (by email/external_id) and create PERMISSION edges on the record.

        At record creation time the creator is implicitly authorized to grant permissions,
        so we bypass `create_node_permissions`' OWNER gate and create edges directly via
        batch_create_edges (same pattern as create_record_group).
        """
        from app.models.permission import EntityType, PermissionType

        # Map PermissionType enum -> role string stored on the edge
        role_map = {
            PermissionType.OWNER: "OWNER",
            PermissionType.WRITE: "WRITER",
            PermissionType.READ: "READER",
            PermissionType.COMMENT: "COMMENTER",
            PermissionType.OTHER: "READER",
        }

        timestamp = get_epoch_timestamp_in_ms()
        edges: List[Dict] = []

        async def _resolve_user_key(perm: Permission) -> Optional[str]:
            if not perm.email:
                return None
            user = await self.graph_provider.get_user_by_email(perm.email)
            if not user:
                self.logger.warning(f"Permission: user not found for email {perm.email}")
                return None
            # get_user_by_email returns a Pydantic User; get_user_by_user_id returns a dict.
            # Support both shapes.
            if isinstance(user, dict):
                return user.get("id") or user.get("_key")
            return getattr(user, "id", None) or getattr(user, "_key", None)

        async def _validate_node_exists(entity_id: str, collection: str, entity_label: str) -> bool:
            """Verify the from-entity exists before creating a dangling PERMISSION edge."""
            doc = await self.graph_provider.get_document(entity_id, collection)
            if not doc:
                self.logger.warning(
                    f"Permission: {entity_label} not found for id {entity_id} in {collection}"
                )
                return False
            return True

        for perm in permissions:
            role_str = role_map.get(perm.type, "READER")
            from_id: Optional[str] = None
            from_collection: Optional[str] = None
            perm_type: Optional[str] = None
            include_role = True

            if perm.entity_type == EntityType.USER:
                from_id = await _resolve_user_key(perm)
                from_collection = CollectionNames.USERS.value
                perm_type = "USER"
            elif perm.entity_type == EntityType.GROUP:
                if perm.external_id and await _validate_node_exists(
                    perm.external_id, CollectionNames.GROUPS.value, "group"
                ):
                    from_id = perm.external_id
                    from_collection = CollectionNames.GROUPS.value
                    perm_type = "GROUP"
            elif perm.entity_type == EntityType.ROLE:
                if perm.external_id and await _validate_node_exists(
                    perm.external_id, CollectionNames.ROLES.value, "role"
                ):
                    from_id = perm.external_id
                    from_collection = CollectionNames.ROLES.value
                    perm_type = "ROLE"
            elif perm.entity_type == EntityType.TEAM:
                if perm.external_id and await _validate_node_exists(
                    perm.external_id, CollectionNames.TEAMS.value, "team"
                ):
                    from_id = perm.external_id
                    from_collection = CollectionNames.TEAMS.value
                    perm_type = "TEAM"
                    include_role = False
            else:
                # DOMAIN / ORG / ANYONE / ANYONE_WITH_LINK not handled here; skip.
                self.logger.warning(
                    f"Skipping permission on record {record_id}: entity_type "
                    f"'{perm.entity_type}' is not supported by CustomConnectorService "
                    "(only USER, GROUP, ROLE, TEAM)"
                )
                continue

            if not from_id:
                continue

            edge = {
                "from_id": from_id,
                "from_collection": from_collection,
                "to_id": record_id,
                "to_collection": CollectionNames.RECORDS.value,
                "externalPermissionId": "",
                "type": perm_type,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }
            if include_role:
                edge["role"] = role_str
            edges.append(edge)

        if edges:
            await self.graph_provider.batch_create_edges(
                edges, CollectionNames.PERMISSION.value, transaction=transaction
            )

    # --- Existing FILE-only upload path (kept for KB compat) ---

    async def upload_files(
        self,
        instance_id: str,
        files: List[Dict],
        user_id: str,
        org_id: str,
        is_restricted: bool,
        parent_id: Optional[str] = None,
    ) -> Dict:
        """Upload files to connector instance root or a folder.

        Legacy path — delegates to graph_provider.create_files_in_record_group.
        KB callers use this. New SDK code should prefer create_records with Record subclass instances.
        """
        return await self.graph_provider.create_files_in_record_group(
            record_group_id=instance_id,
            user_id=user_id,
            org_id=org_id,
            files=files,
            is_restricted=is_restricted,
            parent_folder_id=parent_id,
        )

    async def update_record(
        self,
        record_id: str,
        user_id: str,
        instance_id: Optional[str] = None,
        updates: Dict = None,
        file_metadata: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Update a record. `instance_id` is auto-derived from the record if not supplied."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            # Derive parent recordGroup from the record itself when caller didn't
            # pass it (e.g. the custom-connector router's `/records/{id}` route).
            if not instance_id:
                kb_context = await self.graph_provider.get_kb_context_for_record(record_id)
                if not kb_context or not kb_context.get("kb_id"):
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Record {record_id} not found",
                    }
                instance_id = kb_context["kb_id"]

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": "User lacks permission to edit records",
                }

            result = await self.graph_provider.update_record(
                record_id=record_id,
                user_id=user_id,
                updates=updates,
                file_metadata=file_metadata,
            )

            if result and result.get("success"):
                return result
            return result or {"success": False, "reason": "Failed to update record", "code": 500}

        except Exception as e:
            self.logger.error(f"Failed to update record: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def delete_records(
        self,
        record_ids: List[str],
        instance_id: str,
        user_id: str,
        parent_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Delete multiple records from instance root or a folder."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "reason": "User lacks permission to delete records",
                    "code": 403,
                }

            if parent_id:
                folder_exists = await self.graph_provider.validate_folder_exists_in_record_group(
                    instance_id, parent_id
                )
                if not folder_exists:
                    return {
                        "success": False,
                        "reason": "Folder not found in connector instance",
                        "code": 404,
                    }

            result = await self.graph_provider.delete_records(
                record_ids=record_ids,
                kb_id=instance_id,
                folder_id=parent_id,
            )

            if result and result.get("success"):
                return result
            return result or {"success": False, "reason": "Failed to delete records", "code": 500}

        except Exception as e:
            self.logger.error(f"Failed to delete records: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def move_record(
        self,
        record_id: str,
        instance_id: str,
        new_parent_id: Optional[str],
        user_id: str,
    ) -> Dict:
        """Move a record to a new location within the same connector instance."""
        new_parent_id = new_parent_id or None
        txn_id: Optional[str] = None

        try:
            destination = "instance root" if new_parent_id is None else f"folder {new_parent_id}"
            self.logger.info(
                f"Moving record {record_id} -> {destination} in instance {instance_id}"
            )

            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if user_role not in ["OWNER", "WRITER"]:
                return self._error_response(
                    403, f"Insufficient permissions. Role: {user_role}"
                )

            kb_context = await self.graph_provider.get_kb_context_for_record(record_id)
            if not kb_context or kb_context.get("kb_id") != instance_id:
                return self._error_response(
                    404, f"Record {record_id} not found in instance {instance_id}"
                )

            parent_info = await self.graph_provider.get_record_parent_info(record_id)
            current_parent_id = parent_info.get("id") if parent_info else ""

            if new_parent_id == current_parent_id:
                return {
                    "success": True,
                    "message": "Record is already in the requested location",
                    "recordId": record_id,
                    "newParentId": new_parent_id,
                }

            if new_parent_id is not None:
                folder_valid = await self.graph_provider.validate_folder_exists_in_record_group(
                    instance_id, new_parent_id
                )
                if not folder_valid:
                    return self._error_response(
                        404,
                        f"Target folder {new_parent_id} not found in instance {instance_id}",
                    )

                if new_parent_id == record_id:
                    return self._error_response(400, "Cannot move a folder into itself")

                record_is_folder = await self.graph_provider.is_record_folder(record_id)
                if record_is_folder:
                    is_circular = await self.graph_provider.is_record_descendant_of(
                        record_id=new_parent_id, ancestor_id=record_id
                    )
                    if is_circular:
                        return self._error_response(
                            400, "Cannot move a folder into one of its own sub-folders"
                        )

            txn_id = await self.graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                ],
            )

            if parent_info is not None:
                deleted = await self.graph_provider.delete_parent_child_edge_to_record(
                    record_id=record_id, transaction=txn_id
                )
                if not deleted:
                    raise RuntimeError(
                        f"Failed to delete PARENT_CHILD edge for record {record_id}"
                    )

            if new_parent_id is not None:
                created = await self.graph_provider.create_parent_child_edge(
                    parent_id=new_parent_id, child_id=record_id, transaction=txn_id
                )
                if not created:
                    raise RuntimeError(
                        f"Failed to create PARENT_CHILD edge {new_parent_id} -> {record_id}"
                    )

            updated = await self.graph_provider.update_record_external_parent_id(
                record_id=record_id, new_parent_id=new_parent_id, transaction=txn_id
            )
            if not updated:
                raise RuntimeError(
                    f"Failed to update externalParentId for record {record_id}"
                )

            # Update INHERIT_PERMISSIONS edge if record is restricted
            record_doc = await self.graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value, transaction=txn_id
            )
            if record_doc and record_doc.get("isRestricted"):
                await self.graph_provider.delete_edges_from(
                    record_id, CollectionNames.RECORDS.value,
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    transaction=txn_id,
                )
                if new_parent_id is not None:
                    inherit_target_id = new_parent_id
                    inherit_target_collection = CollectionNames.RECORDS.value
                else:
                    inherit_target_id = instance_id
                    inherit_target_collection = CollectionNames.RECORD_GROUPS.value
                timestamp = get_epoch_timestamp_in_ms()
                inherit_edge = {
                    "from_id": record_id,
                    "from_collection": CollectionNames.RECORDS.value,
                    "to_id": inherit_target_id,
                    "to_collection": inherit_target_collection,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                await self.graph_provider.batch_create_edges(
                    [inherit_edge],
                    CollectionNames.INHERIT_PERMISSIONS.value,
                    transaction=txn_id,
                )

            await self.graph_provider.commit_transaction(txn_id)
            txn_id = None

            return {
                "success": True,
                "recordId": record_id,
                "instanceId": instance_id,
                "newParentId": new_parent_id,
                "previousParentId": current_parent_id,
            }

        except Exception as e:
            self.logger.error(f"move_record failed: {str(e)}", exc_info=True)
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return self._error_response(500, str(e))

    async def stream_record(
        self,
        record_id: str,
        user_id: str,
        org_id: str,
    ) -> Dict:
        """Validate access and return file metadata required for streaming.

        Permission enforcement and the actual byte streaming are performed at
        the router/controller layer using the metadata returned here.
        """
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            kb_context = await self.graph_provider.get_kb_context_for_record(record_id)
            if not kb_context:
                return self._error_response(404, "Record not found or not in any connector instance")

            instance_id = kb_context.get("kb_id")
            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if not user_role:
                return self._error_response(403, "User does not have access to this record")

            record = await self.graph_provider.get_record_by_id(record_id)
            if not record:
                return self._error_response(404, f"Record {record_id} not found")

            file_record = await self.graph_provider.get_file_record_by_id(record_id)

            return {
                "success": True,
                "recordId": record_id,
                "userId": user_id,
                "orgId": org_id,
                "instanceId": instance_id,
                "userRole": user_role,
                "record": record,
                "fileRecord": file_record,
            }

        except Exception as e:
            self.logger.error(f"stream_record failed for {record_id}: {str(e)}")
            return self._error_response(500, str(e))

    # ==================== Permission Operations ====================

    async def add_permissions_to_node(
        self,
        node_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str,
        group_ids: Optional[List[str]] = None,
        role_ids: Optional[List[str]] = None,
        node_collection: str = "recordGroups",
    ) -> Optional[Dict]:
        """Create permissions on a node for users/groups/roles/teams.

        Works on any node (recordGroup or record, via node_collection).
        Entity types: user_ids (USER), group_ids (GROUP), role_ids (ROLE) — all generic SDK;
        team_ids (TEAM) — KB-specific, kept for backwards compat.
        Requires OWNER role on the target node.
        """
        try:
            unique_users = list(set(user_ids)) if user_ids else []
            unique_groups = list(set(group_ids)) if group_ids else []
            unique_roles = list(set(role_ids)) if role_ids else []
            unique_teams = list(set(team_ids)) if team_ids else []

            if not unique_users and not unique_groups and not unique_roles and not unique_teams:
                return {"success": False, "reason": "No entities provided", "code": 400}

            if unique_users or unique_groups or unique_roles:
                if not role or role not in VALID_ROLES:
                    return {
                        "success": False,
                        "reason": f"Invalid role: {role}. Must be one of: {', '.join(VALID_ROLES)}",
                        "code": 400,
                    }

            # Permission gate: requester must be OWNER on the node
            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}",
                }
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=node_id, user_id=requester_key
            )
            if requester_role != "OWNER":
                return {
                    "success": False,
                    "reason": "Only owners can grant permissions",
                    "code": 403,
                }

            result = await self.graph_provider.create_node_permissions(
                node_id=node_id,
                # Provider re-looks up the user by business userId, not internal key
                requester_id=requester_id,
                user_ids=unique_users,
                team_ids=unique_teams,
                role=role if role else "READER",
                group_ids=unique_groups,
                role_ids=unique_roles,
                node_collection=node_collection,
            )

            return result

        except Exception as e:
            self.logger.error(f"Failed to create permissions: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def update_node_permissions(
        self,
        node_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str,
        group_ids: Optional[List[str]] = None,
        role_ids: Optional[List[str]] = None,
        node_collection: str = "recordGroups",
    ) -> Optional[Dict]:
        """Update permissions for users/groups/roles/teams on a node.

        Teams don't have roles — silently skipped. USER/GROUP/ROLE can all be updated.
        """
        try:
            group_ids = group_ids or []
            role_ids = role_ids or []
            if not user_ids and not team_ids and not group_ids and not role_ids:
                return {
                    "success": False,
                    "reason": "No entities provided",
                    "code": "400",
                }

            if team_ids and not (user_ids or group_ids or role_ids):
                return {
                    "success": False,
                    "reason": "Teams don't have roles. Pass user/group/role IDs.",
                    "code": "400",
                }

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}",
                }
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=node_id, user_id=requester_key
            )
            if requester_role not in ["OWNER"]:
                return {
                    "success": False,
                    "reason": "Only owners can update permissions",
                    "code": "403",
                }

            if new_role not in VALID_ROLES:
                return {
                    "success": False,
                    "reason": f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}",
                    "code": "400",
                }

            current_permissions = await self.graph_provider.get_node_permissions(
                node_id=node_id,
                user_ids=user_ids,
                team_ids=team_ids,
                group_ids=group_ids,
                role_ids=role_ids,
                node_collection=node_collection,
            )

            total_owner_count = await self.graph_provider.count_node_owners(node_id=node_id)

            def _filter_valid(ids, bucket: str):
                valid, skipped = [], []
                for eid in ids or []:
                    if eid in current_permissions.get(bucket, {}):
                        valid.append(eid)
                    else:
                        skipped.append(eid)
                return valid, skipped

            valid_user_ids, skipped_users = _filter_valid(user_ids, "users")
            valid_group_ids, skipped_groups = _filter_valid(group_ids, "groups")
            valid_role_ids, skipped_roles = _filter_valid(role_ids, "roles")
            valid_team_ids, skipped_teams = _filter_valid(team_ids, "teams")

            if not valid_user_ids and not valid_group_ids and not valid_role_ids and not valid_team_ids:
                return {
                    "success": False,
                    "reason": "No entities with existing permissions found to update",
                    "code": "404",
                    "skipped_users": skipped_users,
                    "skipped_groups": skipped_groups,
                    "skipped_roles": skipped_roles,
                    "skipped_teams": skipped_teams,
                }

            owners_being_updated = [
                uid for uid in valid_user_ids
                if current_permissions["users"].get(uid) == "OWNER"
            ]

            if len(valid_user_ids) > 1 and owners_being_updated:
                if not (new_role == "OWNER" and len(owners_being_updated) == 0):
                    return {
                        "success": False,
                        "reason": "Cannot perform bulk operations on Owner permissions.",
                        "code": "400",
                    }

            if len(owners_being_updated) == 1 and len(valid_user_ids) == 1:
                if new_role != "OWNER" and total_owner_count <= 1:
                    return {
                        "success": False,
                        "reason": "Cannot remove all owners. At least one owner must remain.",
                        "code": "400",
                    }

            result = await self.graph_provider.update_node_permission(
                node_id=node_id,
                requester_id=requester_key,
                user_ids=valid_user_ids,
                team_ids=valid_team_ids,
                new_role=new_role,
                group_ids=valid_group_ids,
                role_ids=valid_role_ids,
                node_collection=node_collection,
            )

            if result:
                return {
                    "success": True,
                    "userIds": valid_user_ids,
                    "groupIds": valid_group_ids,
                    "roleIds": valid_role_ids,
                    "teamIds": valid_team_ids,
                    "newRole": new_role,
                    "nodeId": node_id,
                }
            return {"success": False, "reason": "Failed to update permission", "code": "500"}

        except Exception as e:
            self.logger.error(f"Failed to update permission: {str(e)}")
            return {"success": False, "reason": str(e), "code": "500"}

    async def remove_node_permissions(
        self,
        node_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        group_ids: Optional[List[str]] = None,
        role_ids: Optional[List[str]] = None,
        node_collection: str = "recordGroups",
    ) -> Optional[Dict]:
        """Remove permissions for users/groups/roles/teams from a node."""
        try:
            group_ids = group_ids or []
            role_ids = role_ids or []
            if not user_ids and not team_ids and not group_ids and not role_ids:
                return {
                    "success": False,
                    "reason": "No entities provided",
                    "code": "400",
                }

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}",
                }
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=node_id, user_id=requester_key
            )
            if requester_role not in ["OWNER"]:
                return {
                    "success": False,
                    "reason": "Only owners can remove permissions",
                    "code": "403",
                }

            current_permissions = await self.graph_provider.get_node_permissions(
                node_id=node_id,
                user_ids=user_ids,
                team_ids=team_ids,
                group_ids=group_ids,
                role_ids=role_ids,
                node_collection=node_collection,
            )

            def _filter_valid(ids, bucket: str):
                valid, skipped = [], []
                for eid in ids or []:
                    if eid in current_permissions.get(bucket, {}):
                        valid.append(eid)
                    else:
                        skipped.append(eid)
                return valid, skipped

            valid_user_ids, skipped_users = _filter_valid(user_ids, "users")
            valid_group_ids, skipped_groups = _filter_valid(group_ids, "groups")
            valid_role_ids, skipped_roles = _filter_valid(role_ids, "roles")
            valid_team_ids, skipped_teams = _filter_valid(team_ids, "teams")

            owner_users_to_remove = [
                uid for uid in valid_user_ids
                if current_permissions["users"].get(uid) == "OWNER"
            ]

            if not valid_user_ids and not valid_group_ids and not valid_role_ids and not valid_team_ids:
                return {
                    "success": False,
                    "reason": "No entities with existing permissions found to remove",
                    "code": "404",
                    "skipped_users": skipped_users,
                    "skipped_groups": skipped_groups,
                    "skipped_roles": skipped_roles,
                    "skipped_teams": skipped_teams,
                }

            if owner_users_to_remove:
                owner_count = await self.graph_provider.count_node_owners(node_id)
                if owner_count <= len(owner_users_to_remove):
                    return {
                        "success": False,
                        "reason": "Cannot remove all owners. At least one owner must remain.",
                        "code": "400",
                        "owner_users": owner_users_to_remove,
                    }

            result = await self.graph_provider.remove_node_permission(
                node_id=node_id,
                user_ids=valid_user_ids,
                team_ids=valid_team_ids,
                group_ids=valid_group_ids,
                role_ids=valid_role_ids,
                node_collection=node_collection,
            )

            if result:
                return {
                    "success": True,
                    "userIds": valid_user_ids,
                    "groupIds": valid_group_ids,
                    "roleIds": valid_role_ids,
                    "teamIds": valid_team_ids,
                    "nodeId": node_id,
                }
            return {"success": False, "reason": "Failed to remove permissions", "code": "500"}

        except Exception as e:
            self.logger.error(f"Failed to remove permission: {str(e)}")
            return {"success": False, "reason": str(e), "code": "500"}

    async def list_node_permissions(
        self,
        node_id: str,
        requester_id: str,
        node_collection: str = "recordGroups",
    ) -> Optional[Dict]:
        """List all permissions for a node (recordGroup or record)."""
        try:
            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}",
                }
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=node_id, user_id=requester_key
            )
            if not requester_role:
                return {
                    "success": False,
                    "reason": "User does not have access to this node",
                    "code": "403",
                }

            permissions = await self.graph_provider.list_node_permissions(
                node_id, node_collection=node_collection
            )
            return {
                "success": True,
                "permissions": permissions,
                "nodeId": node_id,
                "totalCount": len(permissions),
            }

        except Exception as e:
            self.logger.error(f"Failed to list permissions: {str(e)}")
            return {"success": False, "reason": str(e), "code": "500"}

    # ==================== Group Operations ====================

    async def create_group(
        self,
        user_id: str,
        org_id: str,
        name: str,
        connector_id: str,
        source_group_id: str,
        app_name: str,
        description: Optional[str] = None,
    ) -> Dict:
        """Create a group + OWNER PERMISSION edge user→group.

        The group lives in the `groups` collection; the creator auto-gets OWNER membership
        so subsequent add/remove/update/delete operations have a permission gate.
        """
        txn_id: Optional[str] = None
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            timestamp = get_epoch_timestamp_in_ms()
            group_key = str(uuid.uuid4())

            group = AppUserGroup(
                id=group_key,
                app_name=Connectors(app_name),
                connector_id=connector_id,
                source_user_group_id=source_group_id,
                name=name,
                description=description,
                org_id=org_id,
                created_at=timestamp,
                updated_at=timestamp,
            )

            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": group_key,
                "to_collection": CollectionNames.GROUPS.value,
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }

            txn_id = await self.graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.GROUPS.value,
                    CollectionNames.PERMISSION.value,
                ],
            )
            await self.graph_provider.batch_upsert_user_groups([group], transaction=txn_id)
            await self.graph_provider.batch_create_edges(
                [permission_edge], CollectionNames.PERMISSION.value, transaction=txn_id
            )
            await self.graph_provider.commit_transaction(txn_id)
            txn_id = None

            return {
                "success": True,
                "id": group_key,
                "name": name,
                "description": description,
                "sourceGroupId": source_group_id,
                "connectorId": connector_id,
                "appName": app_name,
                "orgId": org_id,
                "userRole": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
        except Exception as e:
            self.logger.error(f"create_group failed: {str(e)}", exc_info=True)
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return self._error_response(500, str(e))

    async def get_group(self, group_id: str, user_id: str) -> Dict:
        """Get group details. Requester must have any PERMISSION edge on the group."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=group_id, user_id=user_key
            )
            if not user_role:
                return self._error_response(403, "User does not have access to this group")

            group_doc = await self.graph_provider.get_document(
                group_id, CollectionNames.GROUPS.value
            )
            if not group_doc:
                return self._error_response(404, f"Group {group_id} not found")

            return {
                "success": True,
                "id": group_doc.get("_key") or group_doc.get("id"),
                "name": group_doc.get("name"),
                "description": group_doc.get("description"),
                "externalGroupId": group_doc.get("externalGroupId"),
                "connectorId": group_doc.get("connectorId"),
                "connectorName": group_doc.get("connectorName"),
                "orgId": group_doc.get("orgId"),
                "userRole": user_role,
                "createdAtTimestamp": group_doc.get("createdAtTimestamp"),
                "updatedAtTimestamp": group_doc.get("updatedAtTimestamp"),
            }
        except Exception as e:
            self.logger.error(f"get_group failed: {str(e)}")
            return self._error_response(500, str(e))

    async def list_groups(
        self,
        user_id: str,
        org_id: str,
        connector_id: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Dict:
        """List groups the user is a member of (paginated + searchable)."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            skip = (max(page, 1) - 1) * limit
            items, total = await self.graph_provider.list_user_groups(
                user_id=user_key,
                org_id=org_id,
                connector_id=connector_id,
                skip=skip,
                limit=limit,
                search=search,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            total_pages = (total + limit - 1) // limit if limit else 0
            return {
                "success": True,
                "groups": items,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total,
                    "totalPages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1,
                },
            }
        except Exception as e:
            self.logger.error(f"list_groups failed: {str(e)}")
            return self._error_response(500, str(e))

    async def update_group(self, group_id: str, user_id: str, updates: Dict) -> Dict:
        """Update group fields. Requester must be OWNER on the group."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=group_id, user_id=user_key
            )
            if user_role != "OWNER":
                return self._error_response(403, "Only group owners can update the group")

            # Whitelist allowed update keys — do not allow mutating connector_id/externalGroupId/_key
            allowed = {"name", "description"}
            clean_updates: Dict = {k: v for k, v in (updates or {}).items() if k in allowed}
            if not clean_updates:
                return self._error_response(400, "No valid fields to update (allowed: name, description)")
            clean_updates["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()

            await self.graph_provider.batch_upsert_nodes(
                [{"_key": group_id, **clean_updates}],
                CollectionNames.GROUPS.value,
            )

            return {"success": True, "id": group_id, "updated": list(clean_updates.keys())}
        except Exception as e:
            self.logger.error(f"update_group failed: {str(e)}")
            return self._error_response(500, str(e))

    async def delete_group(self, group_id: str, user_id: str) -> Dict:
        """Delete a group and all its PERMISSION edges. Requester must be OWNER."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=group_id, user_id=user_key
            )
            if user_role != "OWNER":
                return self._error_response(403, "Only group owners can delete the group")

            deleted = await self.graph_provider.delete_group_cascade(group_id)
            if not deleted:
                return self._error_response(404, f"Group {group_id} not found")
            return {"success": True, "id": group_id}
        except Exception as e:
            self.logger.error(f"delete_group failed: {str(e)}")
            return self._error_response(500, str(e))

    async def add_group_members(
        self,
        group_id: str,
        user_ids: List[str],
        requester_id: str,
        role: str = "READER",
    ) -> Dict:
        """Add users to a group (create PERMISSION edges USER→GROUP). Requester must be OWNER."""
        try:
            if not user_ids:
                return self._error_response(400, "No user_ids provided")
            if role not in VALID_ROLES:
                return self._error_response(
                    400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"
                )

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return self._error_response(404, f"User not found: {requester_id}")
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=group_id, user_id=requester_key
            )
            if requester_role != "OWNER":
                return self._error_response(403, "Only group owners can add members")

            # Filter out users already in the group
            existing = await self.graph_provider.get_node_permissions(
                node_id=group_id,
                user_ids=user_ids,
                team_ids=[],
                node_collection=CollectionNames.GROUPS.value,
            )
            existing_user_ids = set(existing.get("users", {}).keys())
            new_ids = [uid for uid in user_ids if uid not in existing_user_ids]
            if not new_ids:
                return {
                    "success": True,
                    "addedCount": 0,
                    "added": [],
                    "skipped": user_ids,
                }

            timestamp = get_epoch_timestamp_in_ms()
            edges = [
                {
                    "from_id": uid,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": group_id,
                    "to_collection": CollectionNames.GROUPS.value,
                    "externalPermissionId": "",
                    "type": "USER",
                    "role": role,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastUpdatedTimestampAtSource": timestamp,
                }
                for uid in new_ids
            ]
            await self.graph_provider.batch_create_edges(
                edges, CollectionNames.PERMISSION.value
            )
            return {
                "success": True,
                "addedCount": len(new_ids),
                "added": new_ids,
                "skipped": [uid for uid in user_ids if uid in existing_user_ids],
                "role": role,
                "groupId": group_id,
            }
        except Exception as e:
            self.logger.error(f"add_group_members failed: {str(e)}")
            return self._error_response(500, str(e))

    async def remove_group_members(
        self,
        group_id: str,
        user_ids: List[str],
        requester_id: str,
    ) -> Dict:
        """Remove users from a group. Requester must be OWNER."""
        try:
            if not user_ids:
                return self._error_response(400, "No user_ids provided")

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return self._error_response(404, f"User not found: {requester_id}")
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=group_id, user_id=requester_key
            )
            if requester_role != "OWNER":
                return self._error_response(403, "Only group owners can remove members")

            deleted = await self.graph_provider.remove_users_from_group_edges(
                group_id=group_id, user_ids=user_ids
            )
            if not deleted:
                return self._error_response(
                    404, "No matching membership edges found to remove"
                )
            return {"success": True, "groupId": group_id, "removed": user_ids}
        except Exception as e:
            self.logger.error(f"remove_group_members failed: {str(e)}")
            return self._error_response(500, str(e))

    # ==================== Role Operations ====================

    async def create_role(
        self,
        user_id: str,
        org_id: str,
        name: str,
        connector_id: str,
        source_role_id: str,
        app_name: str,
        description: Optional[str] = None,
        parent_role_id: Optional[str] = None,
    ) -> Dict:
        """Create a role + OWNER PERMISSION edge user→role.

        Symmetric to create_group. The creator auto-gets OWNER so subsequent add/remove/
        update/delete operations have a permission gate.
        """
        txn_id: Optional[str] = None
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            timestamp = get_epoch_timestamp_in_ms()
            role_key = str(uuid.uuid4())

            role_obj = AppRole(
                id=role_key,
                app_name=Connectors(app_name),
                connector_id=connector_id,
                source_role_id=source_role_id,
                name=name,
                org_id=org_id,
                parent_role_id=parent_role_id,
                created_at=timestamp,
                updated_at=timestamp,
            )

            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": role_key,
                "to_collection": CollectionNames.ROLES.value,
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }

            txn_id = await self.graph_provider.begin_transaction(
                read=[],
                write=[
                    CollectionNames.ROLES.value,
                    CollectionNames.PERMISSION.value,
                ],
            )
            await self.graph_provider.batch_upsert_app_roles([role_obj], transaction=txn_id)
            await self.graph_provider.batch_create_edges(
                [permission_edge], CollectionNames.PERMISSION.value, transaction=txn_id
            )
            # Optionally persist description on the role doc since AppRole doesn't model it
            if description:
                await self.graph_provider.batch_upsert_nodes(
                    [{"_key": role_key, "description": description}],
                    CollectionNames.ROLES.value,
                    transaction=txn_id,
                )
            await self.graph_provider.commit_transaction(txn_id)
            txn_id = None

            return {
                "success": True,
                "id": role_key,
                "name": name,
                "description": description,
                "sourceRoleId": source_role_id,
                "connectorId": connector_id,
                "appName": app_name,
                "orgId": org_id,
                "parentRoleId": parent_role_id,
                "userRole": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }
        except Exception as e:
            self.logger.error(f"create_role failed: {str(e)}", exc_info=True)
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return self._error_response(500, str(e))

    async def get_role(self, role_id: str, user_id: str) -> Dict:
        """Get role details. Requester must have any PERMISSION edge on the role."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=role_id, user_id=user_key
            )
            if not user_role:
                return self._error_response(403, "User does not have access to this role")

            role_doc = await self.graph_provider.get_document(
                role_id, CollectionNames.ROLES.value
            )
            if not role_doc:
                return self._error_response(404, f"Role {role_id} not found")

            return {
                "success": True,
                "id": role_doc.get("_key") or role_doc.get("id"),
                "name": role_doc.get("name"),
                "description": role_doc.get("description"),
                "externalRoleId": role_doc.get("externalRoleId"),
                "connectorId": role_doc.get("connectorId"),
                "connectorName": role_doc.get("connectorName"),
                "orgId": role_doc.get("orgId"),
                "parentRoleId": role_doc.get("parentRoleId"),
                "userRole": user_role,
                "createdAtTimestamp": role_doc.get("createdAtTimestamp"),
                "updatedAtTimestamp": role_doc.get("updatedAtTimestamp"),
            }
        except Exception as e:
            self.logger.error(f"get_role failed: {str(e)}")
            return self._error_response(500, str(e))

    async def list_roles(
        self,
        user_id: str,
        org_id: str,
        connector_id: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Dict:
        """List roles assigned to the user (paginated + searchable)."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            skip = (max(page, 1) - 1) * limit
            items, total = await self.graph_provider.list_user_roles(
                user_id=user_key,
                org_id=org_id,
                connector_id=connector_id,
                skip=skip,
                limit=limit,
                search=search,
                sort_by=sort_by,
                sort_order=sort_order,
            )
            total_pages = (total + limit - 1) // limit if limit else 0
            return {
                "success": True,
                "roles": items,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total,
                    "totalPages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1,
                },
            }
        except Exception as e:
            self.logger.error(f"list_roles failed: {str(e)}")
            return self._error_response(500, str(e))

    async def update_role(self, role_id: str, user_id: str, updates: Dict) -> Dict:
        """Update role fields. Requester must be OWNER on the role."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=role_id, user_id=user_key
            )
            if user_role != "OWNER":
                return self._error_response(403, "Only role owners can update the role")

            allowed = {"name", "description", "parentRoleId"}
            clean_updates: Dict = {k: v for k, v in (updates or {}).items() if k in allowed}
            if not clean_updates:
                return self._error_response(
                    400, "No valid fields to update (allowed: name, description, parentRoleId)"
                )
            clean_updates["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()

            await self.graph_provider.batch_upsert_nodes(
                [{"_key": role_id, **clean_updates}],
                CollectionNames.ROLES.value,
            )

            return {"success": True, "id": role_id, "updated": list(clean_updates.keys())}
        except Exception as e:
            self.logger.error(f"update_role failed: {str(e)}")
            return self._error_response(500, str(e))

    async def delete_role(self, role_id: str, user_id: str) -> Dict:
        """Delete a role and all its PERMISSION edges. Requester must be OWNER."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")
            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=role_id, user_id=user_key
            )
            if user_role != "OWNER":
                return self._error_response(403, "Only role owners can delete the role")

            deleted = await self.graph_provider.delete_role_cascade(role_id)
            if not deleted:
                return self._error_response(404, f"Role {role_id} not found")
            return {"success": True, "id": role_id}
        except Exception as e:
            self.logger.error(f"delete_role failed: {str(e)}")
            return self._error_response(500, str(e))

    async def add_role_members(
        self,
        role_id: str,
        user_ids: List[str],
        requester_id: str,
        membership_role: str = "READER",
    ) -> Dict:
        """Assign a role to users (create PERMISSION edges USER→ROLE). Requester must be OWNER on the role."""
        try:
            if not user_ids:
                return self._error_response(400, "No user_ids provided")
            if membership_role not in VALID_ROLES:
                return self._error_response(
                    400, f"Invalid role. Must be one of: {', '.join(VALID_ROLES)}"
                )

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return self._error_response(404, f"User not found: {requester_id}")
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=role_id, user_id=requester_key
            )
            if requester_role != "OWNER":
                return self._error_response(403, "Only role owners can assign the role")

            # Filter out users already assigned
            existing = await self.graph_provider.get_node_permissions(
                node_id=role_id,
                user_ids=user_ids,
                team_ids=[],
                node_collection=CollectionNames.ROLES.value,
            )
            existing_user_ids = set(existing.get("users", {}).keys())
            new_ids = [uid for uid in user_ids if uid not in existing_user_ids]
            if not new_ids:
                return {
                    "success": True,
                    "assignedCount": 0,
                    "assigned": [],
                    "skipped": user_ids,
                }

            timestamp = get_epoch_timestamp_in_ms()
            edges = [
                {
                    "from_id": uid,
                    "from_collection": CollectionNames.USERS.value,
                    "to_id": role_id,
                    "to_collection": CollectionNames.ROLES.value,
                    "externalPermissionId": "",
                    "type": "USER",
                    "role": membership_role,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                    "lastUpdatedTimestampAtSource": timestamp,
                }
                for uid in new_ids
            ]
            await self.graph_provider.batch_create_edges(
                edges, CollectionNames.PERMISSION.value
            )
            return {
                "success": True,
                "assignedCount": len(new_ids),
                "assigned": new_ids,
                "skipped": [uid for uid in user_ids if uid in existing_user_ids],
                "role": membership_role,
                "roleId": role_id,
            }
        except Exception as e:
            self.logger.error(f"add_role_members failed: {str(e)}")
            return self._error_response(500, str(e))

    async def remove_role_members(
        self,
        role_id: str,
        user_ids: List[str],
        requester_id: str,
    ) -> Dict:
        """Unassign a role from users. Requester must be OWNER."""
        try:
            if not user_ids:
                return self._error_response(400, "No user_ids provided")

            requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
            if not requester:
                return self._error_response(404, f"User not found: {requester_id}")
            requester_key = requester.get("id") or requester.get("_key")

            requester_role = await self.graph_provider.get_user_node_permission(
                node_id=role_id, user_id=requester_key
            )
            if requester_role != "OWNER":
                return self._error_response(403, "Only role owners can unassign the role")

            deleted = await self.graph_provider.unassign_role_from_users_edges(
                role_id=role_id, user_ids=user_ids
            )
            if not deleted:
                return self._error_response(
                    404, "No matching role assignments found to remove"
                )
            return {"success": True, "roleId": role_id, "unassigned": user_ids}
        except Exception as e:
            self.logger.error(f"remove_role_members failed: {str(e)}")
            return self._error_response(500, str(e))

    # ==================== Listing & Children Operations ====================

    async def list_all_records(
        self,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
        source: str = "all",
    ) -> Dict:
        """List all records the user can access."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            valid_sort = [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp",
                "recordType", "origin", "indexingStatus",
            ]
            sort_by = sort_by if sort_by in valid_sort else "createdAtTimestamp"

            records, total_count, available_filters = await self.graph_provider.list_all_records(
                user_id=user_key,
                org_id=org_id,
                skip=skip,
                limit=limit,
                search=search,
                record_types=record_types,
                origins=origins,
                connectors=connectors,
                indexing_status=indexing_status,
                permissions=permissions,
                date_from=date_from,
                date_to=date_to,
                sort_by=sort_by,
                sort_order=sort_order,
                source=source,
            )

            total_pages = (total_count + limit - 1) // limit
            applied_filters = {
                k: v
                for k, v in {
                    "search": search,
                    "recordTypes": record_types,
                    "origins": origins,
                    "connectors": connectors,
                    "indexingStatus": indexing_status,
                    "source": source if source != "all" else None,
                    "dateRange": (
                        {"from": date_from, "to": date_to}
                        if date_from or date_to
                        else None
                    ),
                }.items()
                if v
            }

            return {
                "records": records,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                },
                "filters": {
                    "applied": applied_filters,
                    "available": available_filters,
                },
            }
        except Exception as e:
            self.logger.error(f"Failed to list all records: {str(e)}")
            return {
                "records": [],
                "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
                "filters": {"applied": {}, "available": {}},
                "error": str(e),
            }

    async def list_record_group_records(
        self,
        record_group_id: str,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
    ) -> Dict:
        """List all records in a record group (e.g. KB / Collection)."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            valid_sort = [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp",
                "recordType", "origin", "indexingStatus",
            ]
            sort_by = sort_by if sort_by in valid_sort else "createdAtTimestamp"

            records, total_count, available_filters = (
                await self.graph_provider.list_record_group_records(
                    record_group_id=record_group_id,
                    user_id=user_key,
                    org_id=org_id,
                    skip=skip,
                    limit=limit,
                    search=search,
                    record_types=record_types,
                    origins=origins,
                    connectors=connectors,
                    indexing_status=indexing_status,
                    date_from=date_from,
                    date_to=date_to,
                    sort_by=sort_by,
                    sort_order=sort_order,
                )
            )

            total_pages = (total_count + limit - 1) // limit
            applied_filters = {
                k: v
                for k, v in {
                    "search": search,
                    "recordTypes": record_types,
                    "origins": origins,
                    "connectors": connectors,
                    "indexingStatus": indexing_status,
                    "dateRange": (
                        {"from": date_from, "to": date_to}
                        if date_from or date_to
                        else None
                    ),
                }.items()
                if v
            }

            return {
                "records": records,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                },
                "filters": {
                    "applied": applied_filters,
                    "available": available_filters,
                },
            }
        except Exception as e:
            self.logger.error(f"Failed to list record group records: {str(e)}")
            return {
                "records": [],
                "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
                "filters": {"applied": {}, "available": {}},
                "error": str(e),
            }

    async def get_node_children(
        self,
        instance_id: str,
        user_id: str,
        node_id: Optional[str] = None,
        node_type: str = "recordGroup",
        page: int = 1,
        limit: int = 20,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Dict:
        """Get children of a node (instance root or folder) with pagination."""
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")

            user_key = user.get("id") or user.get("_key")

            user_role = await self.graph_provider.get_user_node_permission(
                node_id=instance_id, user_id=user_key
            )
            if not user_role:
                return self._error_response(403, "User has no permission for this connector instance")

            skip = (page - 1) * limit
            valid_sort_fields = ["name", "created_at", "updated_at", "size", "type"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            actual_node_id = node_id if node_id else instance_id
            actual_node_type = node_type if node_id else "recordGroup"

            result = await self.graph_provider.get_node_children(
                node_id=actual_node_id,
                node_type=actual_node_type,
                skip=skip,
                limit=limit,
                level=level,
                search=search,
                record_types=record_types,
                origins=origins,
                connectors=connectors,
                indexing_status=indexing_status,
                sort_by=sort_by,
                sort_order=sort_order,
            )

            if not result.get("success"):
                return self._error_response(404, result.get("reason", "Node not found"))

            total_items = result.get("totalCount", 0)
            total_pages = (total_items + limit - 1) // limit

            result["userPermission"] = {
                "role": user_role,
                "canUpload": user_role in ["OWNER", "WRITER"],
                "canCreateFolders": user_role in ["OWNER", "WRITER"],
                "canEdit": user_role in ["OWNER", "WRITER"],
                "canDelete": user_role in ["OWNER"],
                "canManagePermissions": user_role in ["OWNER"],
            }

            result["pagination"] = {
                "page": page,
                "limit": limit,
                "totalItems": total_items,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1,
            }

            result["filters"] = {
                "applied": {
                    k: v
                    for k, v in {
                        "search": search,
                        "record_types": record_types,
                        "origins": origins,
                        "connectors": connectors,
                        "indexing_status": indexing_status,
                        "sort_by": sort_by,
                        "sort_order": sort_order,
                    }.items()
                    if v is not None
                },
                "available": result.get("availableFilters", {}),
            }

            return result

        except Exception as e:
            self.logger.error(f"Failed to get node children: {str(e)}")
            return self._error_response(500, str(e))

    # ==================== Helpers ====================

    def _error_response(self, code: int, reason: str) -> Dict:
        """Create consistent error response."""
        return {"success": False, "code": code, "reason": reason}
