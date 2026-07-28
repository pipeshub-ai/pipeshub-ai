import uuid
from typing import TYPE_CHECKING, Dict, List, Optional, Union

from app.config.constants.arangodb import (
    AppGroups,
    CollectionNames,
    ConnectorScopes,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.services.kafka_service import KafkaService
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]

MONGO_USER_GRAPH_KEY_LOOKUP_CHUNK_SIZE = 500

# KB folders use this mime type in the RECORDS doc (matches the legacy create_folder
# path). Note this differs from MimeTypes.FOLDER ("text/directory").
KB_FOLDER_MIME_TYPE = "application/vnd.folder"

class KnowledgeBaseService:
    """Data handler for knowledge base operations."""

    def __init__(
        self,
        logger,
        graph_provider: IGraphDBProvider,
        kafka_service : KafkaService,
        processor: "DataSourceEntitiesProcessor" = None,
        config_service=None,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.kafka_service = kafka_service
        # Shared entities processor used to route KB records/folders through the same
        # graph-write + Kafka path connectors use. Injected by the router from app.state.
        self.processor = processor
        # Needed to resolve the storage endpoint for upload signed-url routes.
        self.config_service = config_service

    async def _resolve_user_and_kb_access(
        self,
        kb_id: str,
        user_id: str,
        required_roles: Optional[List[str]] = None,
        permission_denied_reason: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str], Optional[Dict]]:
        """
        Resolve user + KB access in one place, minimising DB round-trips.

        Returns (user_key, user_role, error_dict).
        On success error_dict is None.
        On failure user_key and user_role are None and error_dict carries the
        structured response (success=False, code, reason).

        Authz outcome:
        - User has a role that satisfies required_roles → success.
        - User has a role but below required_roles → 403 (insufficient permission).
        - User has NO role on the KB → 404. Existence is hidden from callers with no
          relationship to the KB to prevent cross-tenant id enumeration (we do not
          distinguish "forbidden" from "not found" for them).

        Args:
            required_roles: roles that grant access (None = any role).
            permission_denied_reason: custom message when the user's role is
                insufficient (overrides the generic message).
        """
        user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
        if not user:
            self.logger.warning(f"⚠️ User not found: {user_id}")
            return None, None, {"success": False, "code": 404, "reason": f"User not found: {user_id}"}

        user_key = user.get("id") or user.get("_key")
        if not user_key:
            self.logger.error(
                f"❌ User record for {user_id} has no 'id' or '_key' field — "
                f"keys present: {list(user.keys())}"
            )
            return None, None, {"success": False, "code": 500, "reason": "Internal error: user record is malformed"}

        # Check permission first — if the user has any role, the KB must exist
        # (see invariant assumption in the docstring above).
        user_role = await self.graph_provider.get_user_kb_permission(kb_id, user_key)

        if not user_role:
            # No permission edge at all → 404 (do NOT reveal whether the KB exists;
            # this prevents cross-tenant id enumeration). A user who HAS a role but
            # below required_roles gets a 403 below — that only leaks KB existence to
            # legitimate members, which is fine.
            self.logger.warning(f"⚠️ User {user_key} has no access to KB {kb_id}")
            return None, None, {"success": False, "code": 404, "reason": "Knowledge base not found"}

        if required_roles and user_role not in required_roles:
            reason = permission_denied_reason or (
                "You do not have permission to perform this action on this knowledge base"
            )
            self.logger.warning(
                f"⚠️ User {user_key} role '{user_role}' insufficient for KB {kb_id} "
                f"(required: {', '.join(required_roles)})"
            )
            return None, None, {"success": False, "code": 403, "reason": reason}

        return user_key, user_role, None

    async def _resolve_user_ids_to_graph_keys(
        self,
        user_ids: List[str],
        requester_id: str,
    ) -> tuple[Optional[List[str]], Optional[Dict]]:
        """Resolve Mongo userIds to graph user _keys for KB permission graph ops."""
        if not user_ids:
            return [], None
        requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
        org_id = requester.get("orgId") if requester else None
        try:
            mapping = await self.graph_provider.get_graph_user_keys_by_mongo_user_ids(
                user_ids,
                org_id,
                chunk_size=MONGO_USER_GRAPH_KEY_LOOKUP_CHUNK_SIZE,
            )
            if not mapping:
                return None, {
                    "success": False,
                    "reason": f"Users not found in graph: {user_ids}",
                    "code": 400,
                }
            missing = [uid for uid in user_ids if uid not in mapping]
            if missing:
                return None, {
                    "success": False,
                    "reason": f"Users not found in graph: {missing}",
                    "code": 400,
                }
            return [mapping[uid] for uid in user_ids], None
        except ValueError as e:
            return None, {"success": False, "reason": str(e), "code": 400}

    async def _assert_no_folder_sibling_conflict(
        self,
        kb_id: str,
        parent_folder_id: Optional[str],
        folder_name: str,
        exclude_record_id: str,
    ) -> Optional[Dict]:
        """
        Check for folder name conflicts with siblings in the same parent.
        
        Returns error dict with 409 if conflict exists, None if no conflict.
        """
        existing_folder = await self.graph_provider.find_folder_by_name_in_parent(
            kb_id=kb_id,
            folder_name=folder_name,
            parent_folder_id=parent_folder_id,
            exclude_folder_id=exclude_record_id,
        )
        
        if existing_folder:
            location = "this folder" if parent_folder_id else "collection root"
            return {
                "success": False,
                "code": 409,
                "reason": f"Folder '{folder_name}' already exists in {location}",
            }
        return None

    async def _assert_no_file_sibling_conflict(
        self,
        kb_id: str,
        parent_folder_id: Optional[str],
        file_name: str,
        mime_type: str,
        exclude_record_id: str,
    ) -> Optional[Dict]:
        """
        Check for file name conflicts with siblings in the same parent.
        
        Returns error dict with 409 if conflict exists, None if no conflict.
        """
        existing_file = await self.graph_provider.find_file_by_name_in_parent(
            kb_id=kb_id,
            file_name=file_name,
            mime_type=mime_type,
            parent_folder_id=parent_folder_id,
            exclude_record_id=exclude_record_id,
        )
        
        if existing_file:
            location = "this folder" if parent_folder_id else "this location"
            return {
                "success": False,
                "code": 409,
                "reason": f"A file named '{file_name}' already exists in {location}",
            }
        return None

    async def create_knowledge_base(
        self,
        user_id: str,
        org_id: str,
        name: str
    ) -> Optional[Dict]:
        """Create a new knowledge base"""
        try:
            self.logger.info(f"🚀 Creating KB '{name}' for user {user_id} in org {org_id}")

            if not name or not name.strip():
                return {"success": False, "code": 400, "reason": "Knowledge base name is required"}
            name = name.strip()

            # Step 1: Look up user
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                self.logger.warning(f"⚠️ User not found: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }

            user_key = user.get('id') or user.get('_key')
            user_name = user.get('fullName') or f"{user.get('firstName', '')} {user.get('lastName', '')}".strip() or 'Unknown'
            self.logger.info(f"✅ Found user: {user_name} (key: {user_key})")

            timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid.uuid4())

            self.logger.info(f"📋 Generated KB ID: {kb_key}")

            # Step 3: Create transaction
            txn_id = None
            try:
                txn_id = await self.graph_provider.begin_transaction(
                    read=[],
                    write=[
                        CollectionNames.APPS.value,
                        CollectionNames.ORG_APP_RELATION.value,
                        CollectionNames.USER_APP_RELATION.value,
                        CollectionNames.PERMISSION.value,
                    ],
                )
                self.logger.info("🔄 Transaction created")
            except Exception as tx_error:
                self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Transaction creation failed: {str(tx_error)}"
                }

            kb_data = {
                "id": kb_key,
                # External user id (not the graph user_key) — matches every
                # other connector type's createdBy convention, which
                # connector_registry._can_access_connector compares against.
                "createdBy": user_id,
                "orgId": org_id,
                "name": name,
                "type": Connectors.KNOWLEDGE_BASE.value,
                "appGroup": AppGroups.LOCAL_STORAGE.value,
                "authType": "NONE",
                "scope": ConnectorScopes.PERSONAL.value,
                "isActive": True,
                "isAgentActive": True,
                "isConfigured": True,
                "isAuthenticated": True,
                "hideConnector": True,  # Excluded from main connector management UI
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": kb_key,
                "to_collection": CollectionNames.APPS.value,
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }

            # ORG_APP_RELATION is validated against basic_edge_schema in Arango
            # (additionalProperties: False — only _from/_to/createdAtTimestamp
            # allowed), so no entityType/updatedAtTimestamp here.
            org_app_edge = {
                "from_id": org_id,
                "from_collection": CollectionNames.ORGS.value,
                "to_id": kb_key,
                "to_collection": CollectionNames.APPS.value,
                "createdAtTimestamp": timestamp,
            }

            # USER_APP_RELATION is validated against user_app_relation_schema
            # in Arango, which requires syncState/lastSyncUpdate — same
            # convention as every other connector's user-app edge.
            user_app_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": kb_key,
                "to_collection": CollectionNames.APPS.value,
                "syncState": "NOT_STARTED",
                "lastSyncUpdate": timestamp,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            # Step 5: Execute database operations
            self.logger.info("💾 Executing database operations...")
            await self.graph_provider.batch_upsert_nodes(
                [kb_data],
                CollectionNames.APPS.value,
                transaction=txn_id,
            )
            await self.graph_provider.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSION.value,
                transaction=txn_id,
            )
            await self.graph_provider.batch_create_edges(
                [org_app_edge],
                CollectionNames.ORG_APP_RELATION.value,
                transaction=txn_id,
            )
            await self.graph_provider.batch_create_edges(
                [user_app_edge],
                CollectionNames.USER_APP_RELATION.value,
                transaction=txn_id,
            )
            await self.graph_provider.commit_transaction(txn_id)

            result = {"success": True}
            if result and result.get("success"):
                response = {
                    "id": kb_data["id"],
                    "name": kb_data["name"],
                    "createdAtTimestamp": kb_data["createdAtTimestamp"],
                    "updatedAtTimestamp": kb_data["updatedAtTimestamp"],
                    "success": True,
                    "userRole": "OWNER"
                }

                self.logger.info(f"✅ KB '{name}' created successfully: {kb_key}")
                return response

            else:
                return {
                    "success": False,
                    "code": 500,
                    "reason": "Failed to create knowledge base in database"
                }

        except Exception as e:
            self.logger.error(f"❌ KB creation failed for '{name}': {str(e)}")
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")

            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
    ) -> Optional[Dict]:
        """Get Knowledge base details"""
        try:
            self.logger.info(f"Getting knowledge base {kb_id} for user {user_id}")

            user_key, user_role, err = await self._resolve_user_and_kb_access(kb_id, user_id)
            if err:
                return err

            result = await self.graph_provider.get_knowledge_base(
                kb_id=kb_id,
                user_id=user_key,
            )

            if result:
                self.logger.info(f"Knowledge base retrieved successfully: {result}")
                return result
            else:
                self.logger.warning("Knowledge base not found after permission check")
                return {"success": False, "reason": "Knowledge base not found", "code": 404}

        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

    async def list_user_knowledge_bases(
        self,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Union[List[Dict],Dict]:
        """List knowledge bases with pagination and filtering"""
        try:
            self.logger.info(f" Listing knowledge bases for user {user_id}")

            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('id') or user.get('_key')

            # Calculate pagination
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "updatedAtTimestamp", "updatedAtTimestamp", "userRole"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            kbs, total_count, available_filters = await self.graph_provider.list_user_knowledge_bases(
                user_id=user_key,
                org_id=org_id,
                skip=skip,
                limit=limit,
                search=search,
                permissions=permissions,
                sort_by=sort_by,
                sort_order=sort_order,
            )

            if isinstance(kbs, dict) and kbs.get("success") is False:
                return kbs

            # Calculate pagination metadata
            total_pages = (total_count + limit - 1) // limit

            # Build applied filters
            applied_filters = {}
            if search:
                applied_filters["search"] = search
            if permissions:
                applied_filters["permissions"] = permissions
            if sort_by != "name":
                applied_filters["sort_by"] = sort_by
            if sort_order != "asc":
                applied_filters["sort_order"] = sort_order

            result = {
                "knowledgeBases": kbs,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                    "hasNext": page < total_pages,
                    "hasPrev": page > 1
                },
                "filters": {
                    "applied": applied_filters,
                    "available": available_filters
                }
            }


            self.logger.info(f"✅ Found {len(kbs)} knowledge bases (page {page}/{total_pages})")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to list knowledge bases: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def update_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        updates: Dict,
    ) -> Optional[Dict]:
        """Update knowledge base details"""
        try:
            self.logger.info(f"🚀 Updating knowledge base {kb_id}")
            timestamp = get_epoch_timestamp_in_ms()
            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id,
                required_roles=["OWNER", "WRITER", "ORGANIZER"],
            )
            if err:
                return err
            if "name" in updates:
                if not updates["name"] or not str(updates["name"]).strip():
                    return {"success": False, "code": 400, "reason": "Knowledge base name cannot be empty"}
                updates["name"] = str(updates["name"]).strip()
            updates["updatedAtTimestamp"] = timestamp
            # Update in database
            result = await self.graph_provider.update_knowledge_base(
                kb_id=kb_id,
                updates=updates
            )

            if result:
                self.logger.info("✅ Knowledge base updated successfully")
                return {
                    "success":True,
                    "reason":"Knowledge base updated successfully",
                    "code":200
                }
            else:
                self.logger.warning("⚠️ Failed to update knowledge base")
                return {
                    "success":False,
                    "reason":"Knowledge base not found",
                    "code":404
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to update knowledge base: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def delete_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
    ) -> Optional[Dict]:
        """Delete a knowledge base.

        A KB is an ``apps`` doc whose records carry ``connectorId == kb_id``, so we
        reuse the generic ``delete_connector_instance`` cascade (dynamic edge sweep —
        removes inheritPermissions and any future edge types, with empty
        recordGroups/roles/groups sets a no-op for a KB) and emit a single
        ``bulkDeleteRecords`` event for Qdrant vector cleanup, matching connector delete.
        """
        try:
            self.logger.info(f"🚀 Deleting knowledge base {kb_id}")
            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER"],
                permission_denied_reason="Only KB owners can delete knowledge bases",
            )
            if err:
                # _resolve_user_and_kb_access already logged the denial at warning level;
                # re-logging here at error would inflate error-rate metrics with auth events.
                return err

            self.logger.info(f"🔐 User {user_key} has OWNER permission - proceeding with deletion")

            result = await self.graph_provider.delete_connector_instance(
                connector_id=kb_id, org_id=org_id
            )

            if not result or not result.get("success"):
                self.logger.warning(f"⚠️ Failed to delete knowledge base {kb_id}")
                return {
                    "success": False,
                    "reason": (result or {}).get("error", "Failed to delete knowledge base"),
                    "code": 500,
                }

            # Single bulkDeleteRecords event drives Qdrant cleanup for all records at once.
            virtual_record_ids = result.get("virtual_record_ids", [])
            if virtual_record_ids:
                try:
                    await self.kafka_service.publish_event(
                        "record-events",
                        {
                            "eventType": EventTypes.BULK_DELETE_RECORDS.value,
                            "timestamp": get_epoch_timestamp_in_ms(),
                            "payload": {
                                "orgId": org_id,
                                "connectorId": kb_id,
                                "virtualRecordIds": virtual_record_ids,
                                "totalRecords": len(virtual_record_ids),
                            },
                        },
                    )
                except Exception as e:
                    self.logger.error(f"❌ Failed to publish bulkDeleteRecords for KB {kb_id}: {str(e)}")

            self.logger.info(f"✅ Knowledge base {kb_id} deleted successfully by user_key={user_key}")
            return {
                "success": True,
                "reason": "Knowledge base and all contents deleted successfully",
                "code": 200,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete knowledge base {kb_id}: {str(e)}")
            self.logger.error(f"❌ Error type: {type(e).__name__}")

            # Only log full traceback for unexpected errors
            if not isinstance(e, (ValueError, KeyError, PermissionError)):
                import traceback
                self.logger.error(f"❌ Traceback: {traceback.format_exc()}")

            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    def _build_kb_folder_record(
        self,
        kb_id: str,
        folder_id: str,
        name: str,
        org_id: str,
        parent_folder_id: Optional[str],
    ) -> FileRecord:
        """Build the FileRecord for a KB folder, matching the fields the direct-AQL
        create_folder path wrote (mime folder, COMPLETED status, UPLOAD/KB markers)."""
        ts = get_epoch_timestamp_in_ms()
        return FileRecord(
            id=folder_id,
            org_id=org_id,
            record_name=name,
            record_type=RecordType.FILE,
            external_record_id=folder_id,
            origin=OriginTypes.UPLOAD,
            connector_name=Connectors.KNOWLEDGE_BASE,
            connector_id=kb_id,
            external_record_group_id=kb_id,
            parent_external_record_id=parent_folder_id or None,
            version=0,
            mime_type=KB_FOLDER_MIME_TYPE,
            weburl=f"/kb/{kb_id}/folder/{folder_id}",
            is_file=False,
            extension=None,
            size_in_bytes=0,
            indexing_status=ProgressStatus.COMPLETED.value,
            extraction_status=ProgressStatus.COMPLETED.value,
            inherit_permissions=True,
            created_at=ts,
            updated_at=ts,
            source_created_at=ts,
            source_updated_at=ts,
        )

    async def create_folder_in_kb(
        self,
        kb_id: str,
        name: str,
        user_id: str,
        org_id: str,
    ) -> Optional[Dict]:
        """Create folder in KB root"""
        try:
            self.logger.info(f"🚀 Creating folder '{name}' in KB {kb_id} root")

            if not name or not name.strip():
                return {"success": False, "code": 400, "reason": "Folder name is required"}
            name = name.strip()

            # Validate user and permissions
            validation_result = await self.graph_provider._validate_folder_creation(kb_id, user_id)
            if not validation_result["valid"]:
                return validation_result

            # Check for name conflicts in KB root
            existing_folder = await self.graph_provider.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=name,
                parent_folder_id=None,  # KB root
            )

            if existing_folder:
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists in KB root"
                }

            # Create folder through the shared entities processor (same graph-write
            # path connectors use): records+files+isOfType, belongsTo→apps/<kbId>,
            # inheritPermissions. Folders are created COMPLETED and emit no event.
            folder_id = str(uuid.uuid4())
            folder_record = self._build_kb_folder_record(
                kb_id, folder_id, name, org_id, parent_folder_id=None
            )
            await self.processor.on_new_records([(folder_record, [])])

            return {
                "id": folder_id,
                "name": name,
                "webUrl": folder_record.weburl,
                "exists": False,
                "success": True,
            }

        except Exception as e:
            self.logger.error(f"❌ KB folder creation failed: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def create_nested_folder(
        self,
        kb_id: str,
        parent_folder_id: str,
        name: str,
        user_id: str,
        org_id: str,
    ) -> Optional[Dict]:
        """Create folder inside another folder"""
        try:
            self.logger.info(f"🚀 Creating nested folder '{name}' in folder {parent_folder_id}")

            if not name or not name.strip():
                return {"success": False, "code": 400, "reason": "Folder name is required"}
            name = name.strip()

            # Validate user and permissions
            validation_result = await self.graph_provider._validate_folder_creation(kb_id, user_id)
            if not validation_result["valid"]:
                return validation_result

            # Additional validation for parent folder
            folder_valid = await self.graph_provider.validate_folder_exists_in_kb(kb_id, parent_folder_id)
            if not folder_valid:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Parent folder {parent_folder_id} not found in KB {kb_id}"
                }

            # Check for name conflicts in parent location
            existing_folder = await self.graph_provider.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=name,
                parent_folder_id=parent_folder_id,
            )

            if existing_folder:
                location = "this folder" if parent_folder_id else "KB root"
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists in {location}"
                }

            # Create nested folder through the shared entities processor. The parent
            # linkage is a PARENT_CHILD edge from the parent folder's _key.
            folder_id = str(uuid.uuid4())
            folder_record = self._build_kb_folder_record(
                kb_id, folder_id, name, org_id, parent_folder_id=parent_folder_id
            )
            await self.processor.on_new_records([(folder_record, [])])

            return {
                "id": folder_id,
                "name": name,
                "webUrl": folder_record.weburl,
                "exists": False,
                "success": True,
            }

        except Exception as e:
            self.logger.error(f"❌ Nested folder creation failed: {str(e)}")
            return {"success": False, "code": 500, "reason": str(e)}

    async def get_folder_contents(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
    ) -> Dict:
        """Get contents of a folder"""
        try:
            self.logger.info(f"🔍 Getting contents of folder {folder_id} in KB {kb_id}")
            user_key, user_role, err = await self._resolve_user_and_kb_access(kb_id, user_id)
            if err:
                return err

            # Scope the folder to THIS KB before reading (the folder_id is caller-supplied;
            # without this a member of any KB could read another KB's folder contents).
            if not await self.graph_provider.validate_folder_in_kb(kb_id, folder_id):
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {"success": False, "code": 404, "reason": "Folder not found in knowledge base"}

            # Get folder contents
            result = await self.graph_provider.get_folder_contents(
                kb_id=kb_id,
                folder_id=folder_id,
            )

            if result:
                self.logger.info("✅ Folder contents retrieved successfully")
                return result
            else:
                self.logger.warning("⚠️ Folder not found")
                return {
                    "success": False,
                    "code": 404,
                    "reason": "Folder not found in knowledge base"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder contents: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def updateFolder(
        self,
        folder_id: str,
        kb_id: str,
        user_id: str,
        name: str
    ) -> Dict:
        try:
            self.logger.info(f"🚀 Updating folder {folder_id} in KB {kb_id}")
            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER", "WRITER"]
            )
            if err:
                return err

            # Validate the new name before touching the DB (avoids a 500 on None/empty).
            if not name or not name.strip():
                return {"success": False, "code": 400, "reason": "Folder name is required"}
            name = name.strip()

            # Validate that folder exists and belongs to the KB
            folder_exists = await self.graph_provider.validate_folder_in_kb(kb_id, folder_id)
            if not folder_exists:
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": 404
                }

            # Get current folder details to check if name actually changed
            current_folder = await self.graph_provider.get_document(folder_id, "records")
            if not current_folder:
                return {
                    "success": False,
                    "reason": "Folder not found",
                    "code": 404
                }
            
            current_name = current_folder.get("recordName", "")
            
            # Early exit if name hasn't changed
            if current_name.lower() == name.lower():
                self.logger.info(f"↩️ Folder name unchanged, skipping update")
                return {
                    "success": True,
                    "code": 200,
                    "reason": "Folder name unchanged"
                }

            # Resolve parent to check siblings in correct location
            parent_info = await self.graph_provider.get_record_parent_info(folder_id)
            parent_folder_id = parent_info.get("id") if parent_info and parent_info.get("type") == "record" else None
            
            # Check for name conflicts with sibling folders
            existing_folder = await self.graph_provider.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=name,
                parent_folder_id=parent_folder_id,
                exclude_folder_id=folder_id,
            )

            if existing_folder:
                location = "this folder" if parent_folder_id else "collection root"
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists in {location}"
                }

            # Rename through the shared processor: fetch the existing folder record so
            # all other fields are preserved, change the name, and route as a metadata
            # update (writes records.recordName + files.name, emits no reindex event).
            folder_record = await self.graph_provider.get_file_record_by_id(folder_id)
            if not folder_record:
                return {
                    "success": False,
                    "reason": "Folder not found",
                    "code": 404
                }
            folder_record.record_name = name
            folder_record.updated_at = get_epoch_timestamp_in_ms()
            await self.processor.on_record_metadata_update(folder_record)
            self.logger.info(f"✅ Folder updated successfully: {folder_id} by user {user_id}")
            return {
                "success": True,
                "code": 200,
                "reason": "Updated folder successfully"
            }
        except Exception as e:
            self.logger.error(f"Failed to update folder {folder_id} for knowledge base {kb_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str
    ) -> Dict:
        """Delete a folder """
        try:
            self.logger.info(f" Deleting folder {folder_id} in  knowledge base {kb_id}")
            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER", "WRITER"],
                permission_denied_reason="User lacks permission to delete folder",
            )
            if err:
                return err
            # Validate that folder exists and belongs to the KB
            folder_exists = await self.graph_provider.validate_folder_in_kb(kb_id, folder_id)
            if not folder_exists:
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": 404
                }

            # Delete through the unified recursive delete: the folder id is one root,
            # which cascades to remove the folder + all descendants (records/subfolders +
            # edges + files docs) and publishes a deleteRecord event per contained file,
            # so the router does not need to publish eventData for this path.
            await self.processor.on_records_deleted_cascade([folder_id], kb_id)
            self.logger.info(f"🎉 Folder {folder_id} and ALL contents deleted successfully by {user_id}")
            return {
                "success": True,
                "reason": "Folder and all contents deleted successfully",
                "code": 200,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def update_record(
        self,
        user_id: str,
        record_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        Update a record directly in KB root (not in any folder).
        Only OWNER and WRITER can update; READER and COMMENTER cannot.
        """
        try:
            self.logger.info(f"🚀 Updating record {record_id}")

            # Resolve KB context and check edit permission (OWNER/WRITER only)
            kb_context = await self.graph_provider._get_kb_context_for_record(record_id)
            if not kb_context:
                return {
                    "success": False,
                    "code": 404,
                    "reason": "Knowledge base context not found for record",
                }
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}",
                }
            user_key = user.get("id") or user.get("_key")
            user_role = await self.graph_provider.get_user_kb_permission(
                kb_context.get("kb_id"), user_key
            )
            if not user_role:
                # No role on the KB → hide existence (404), consistent with the read path.
                return {
                    "success": False,
                    "code": 404,
                    "reason": "Record not found",
                }
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "success": False,
                    "code": 403,
                    "reason": "User lacks permission to edit records",
                }

            # Check for file rename duplicate conflicts
            if "recordName" in updates:
                new_name = updates["recordName"]
                if not new_name or not str(new_name).strip():
                    return {"success": False, "code": 400, "reason": "Record name cannot be empty"}
                new_name = str(new_name).strip()
                updates["recordName"] = new_name

                # Get current record details
                current_record = await self.graph_provider.get_document(record_id, "records")
                if not current_record:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": "Record not found",
                    }
                
                # Get file metadata to check if this is a file (not a folder)
                file_doc = await self.graph_provider.get_document(record_id, "files")
                if file_doc and file_doc.get("isFile"):
                    # This is a file - check for duplicate file names
                    current_name = current_record.get("recordName", "")
                    
                    # Skip check if name unchanged
                    if current_name.lower() != new_name.lower():
                        # Resolve parent
                        parent_info = await self.graph_provider.get_record_parent_info(record_id)
                        parent_folder_id = parent_info.get("id") if parent_info and parent_info.get("type") == "record" else None
                        
                        # Check for sibling file with same name + mime
                        mime_type = file_doc.get("mimeType", "")
                        existing_file = await self.graph_provider.find_file_by_name_in_parent(
                            kb_id=kb_context.get("kb_id"),
                            file_name=new_name,
                            mime_type=mime_type,
                            parent_folder_id=parent_folder_id,
                            exclude_record_id=record_id,
                        )
                        
                        if existing_file:
                            location = "this folder" if parent_folder_id else "this location"
                            return {
                                "success": False,
                                "code": 409,
                                "reason": f"A file named '{new_name}' already exists in {location}",
                            }

            # Load the existing record so all fields are preserved on re-upsert.
            record = await self.graph_provider.get_file_record_by_id(record_id)
            if not record:
                return {"success": False, "code": 404, "reason": "Record not found"}

            timestamp = get_epoch_timestamp_in_ms()
            if "recordName" in updates:
                record.record_name = updates["recordName"]
            extra_keys = [k for k in updates.keys() if k != "recordName"]
            if extra_keys:
                self.logger.warning(f"update_record ignoring unmapped update keys: {extra_keys}")
            record.updated_at = timestamp

            if file_metadata is not None:
                # Content changed (new blob uploaded): bump revision so the record is
                # re-persisted, force reindex, and emit updateRecord (Qdrant refresh).
                record.source_updated_at = file_metadata.get("lastModified", timestamp)
                record.external_revision_id = str(timestamp)
                await self.processor.on_record_content_update(record)
            else:
                # Metadata-only (rename): persists records.recordName + files.name, no event.
                await self.processor.on_record_metadata_update(record)

            # Router enriches the response and publishes nothing (processor already did),
            # so return the shape it consumes without eventData.
            updated_doc = await self.graph_provider.get_document(record_id, "records")
            return {
                "success": True,
                "updatedRecord": updated_doc or {},
                "recordId": record_id,
                "timestamp": timestamp,
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to update KB record: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

    async def delete_records_in_kb(
        self,
        kb_id: str,
        record_ids: List[str],
        user_id: str,
    ) -> Optional[Dict]:
        """
        Delete multiple records from KB root (not in any folder)
        """
        try:
            self.logger.info(f"🚀 Bulk deleting {len(record_ids)} records from KB {kb_id} root")

            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER", "WRITER"]
            )
            if err:
                return err

            # Delete through the shared processor: recursively deletes each record + its
            # subtree, cascades all edges + type docs, publishes a deleteRecord per
            # indexed record (Qdrant cleanup). Returns the provider result for the response.
            result = await self.processor.on_records_deleted_cascade(record_ids, kb_id)
            if result and result.get("success"):
                result.pop("eventData", None)
                # Bulk-delete best practice: none of the requested ids matched (foreign /
                # non-existent / not in this KB) → 404. Partial success stays 200 with the
                # failed ids surfaced in failed_records.
                if result.get("total_requested", 0) > 0 and result.get("successfully_deleted", 0) == 0:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": "No matching records found in this knowledge base",
                        "failed_records": result.get("failed_records", []),
                    }
                result.setdefault("message", f"Deleted {result.get('successfully_deleted', 0)} record(s) from KB root")
                result.setdefault("deleteType", "kb_records")
                return result
            else:
                return result or {
                    "success": False,
                    "reason": "Failed to delete records",
                    "code": 500
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB records: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

    async def delete_records_in_folder(
        self,
        kb_id: str,
        folder_id: str,
        record_ids: List[str],
        user_id: str,
    ) -> Optional[Dict]:
        """
        Delete multiple records from a specific folder
        """
        try:
            self.logger.info(f"🚀 Bulk deleting {len(record_ids)} records from folder {folder_id} in KB {kb_id}")

            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER", "WRITER"]
            )
            if err:
                return err

            # Validate folder exists in KB
            folder_exists = await self.graph_provider.validate_folder_exists_in_kb(kb_id, folder_id)
            if not folder_exists:
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": 404
                }

            # Delete through the shared processor — same generic cascade as the KB-root
            # path (a folder is just a record). folder_id is no longer used to filter the
            # delete; records are scoped by the KB (connectorId == kb_id).
            result = await self.processor.on_records_deleted_cascade(record_ids, kb_id)
            if result and result.get("success"):
                result.pop("eventData", None)
                # Bulk-delete best practice: none of the requested ids matched → 404.
                # Partial success stays 200 with failed ids in failed_records.
                if result.get("total_requested", 0) > 0 and result.get("successfully_deleted", 0) == 0:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": "No matching records found in this folder",
                        "failed_records": result.get("failed_records", []),
                    }
                result.setdefault("message", f"Deleted {result.get('successfully_deleted', 0)} record(s) from folder")
                result.setdefault("deleteType", "folder_records")
                return result
            else:
                return result or {
                    "success": False,
                    "reason": "Failed to delete records in folder",
                    "code": 500
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder records: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],  # External user IDs
        team_ids: List[str],  # External team IDs
        role: str,
    ) -> Optional[Dict]:
        """Optimized version - single AQL query approach"""
        try:
            self.logger.info(f"🚀 Creating {role} permissions for {len(user_ids)} users and {len(team_ids)} teams on KB {kb_id}")

            # Step 1: Validate inputs early
            unique_users = list(dict.fromkeys(user_ids)) if user_ids else []
            unique_teams = list(dict.fromkeys(team_ids)) if team_ids else []

            if not unique_users and not unique_teams:
                return {"success": False, "reason": "No users or teams provided", "code": 400}

            # Role is required for users, but not for teams (teams don't have roles)
            if unique_users:
                valid_roles = ["OWNER", "ORGANIZER", "WRITER", "COMMENTER", "READER"]
                if not role or role not in valid_roles:
                    return {"success": False, "reason": f"Invalid role: {role}. Role is required for users.", "code": 400}

            self.logger.info(f"Looking up requester for create_kb_permissions: {requester_id}")
            requester_key, _, err = await self._resolve_user_and_kb_access(
                kb_id, requester_id, required_roles=["OWNER"],
                permission_denied_reason="Only KB owners can grant permissions",
            )
            if err:
                return err

            graph_user_ids, resolve_err = await self._resolve_user_ids_to_graph_keys(
                unique_users, requester_id
            )
            if resolve_err:
                return resolve_err

            # Step 2: Single AQL query to do everything at once
            # Pass role even if only teams (it will be ignored for teams)
            result = await self.graph_provider.create_kb_permissions(
                kb_id=kb_id,
                requester_id=requester_id,
                user_ids=graph_user_ids,
                team_ids=unique_teams,
                role=role if role else "READER"  # Default for teams (won't be used)
            )

            if result.get("success"):
                self.logger.info(f"✅ Permissions created: {result['grantedCount']} granted")
                return result
            else:
                self.logger.error(f"❌ Permission creation failed: {result.get('reason')}")
                return result

        except Exception as e:
            self.logger.error(f"❌ Failed to create KB permissions: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str
    ) -> Optional[Dict]:
        """Update permissions for users and teams on a knowledge base"""
        try:
            self.logger.info(f"🚀 Updating permission for {len(user_ids)} users and {len(team_ids)} teams on KB {kb_id} to {new_role}")

            # Check if at least one of user_ids or team_ids is provided
            if not user_ids and not team_ids:
                return {
                    "success": False,
                    "reason": "No users or teams provided for permission update",
                    "code": 400
                }

            # Teams don't have roles - they just have access or not
            # So we can only update user permissions, not team permissions
            if team_ids:
                return {
                    "success": False,
                    "reason": "Teams don't have roles. Only user permissions can be updated.",
                    "code": 400
                }

            self.logger.info(f"Looking up requester by requester: {requester_id}")
            requester_key, _, err = await self._resolve_user_and_kb_access(
                kb_id, requester_id, required_roles=["OWNER"],
                permission_denied_reason="Only KB owners can update permissions",
            )
            if err:
                return err

            graph_user_ids, resolve_err = await self._resolve_user_ids_to_graph_keys(
                user_ids, requester_id
            )
            if resolve_err:
                return resolve_err

            # Validate new role
            valid_roles = ["OWNER", "ORGANIZER", "WRITER", "COMMENTER", "READER"]
            if new_role not in valid_roles:
                return {
                    "success": False,
                    "reason": f"Invalid role. Must be one of: {', '.join(valid_roles)}",
                    "code": 400
                }

            # Get current permissions for all users and teams in a single batch query
            current_permissions = await self.graph_provider.get_kb_permissions(
                kb_id=kb_id,
                user_ids=graph_user_ids,
                team_ids=team_ids
            )

            # Get owner count for the KB
            total_owner_count = await self.graph_provider.count_kb_owners(kb_id=kb_id)

            # Filter out users/teams that don't have permissions (skip them instead of erroring)
            valid_user_ids = []
            skipped_users = []
            if graph_user_ids:
                for graph_user_id in graph_user_ids:
                    if graph_user_id in current_permissions["users"]:
                        valid_user_ids.append(graph_user_id)
                    else:
                        skipped_users.append(graph_user_id)
                        self.logger.warning(
                            f"⚠️ Skipping user {graph_user_id} - no permission found on KB {kb_id}"
                        )

            valid_team_ids = []
            skipped_teams = []
            if team_ids:
                for team_id in team_ids:
                    if team_id in current_permissions["teams"]:
                        valid_team_ids.append(team_id)
                    else:
                        skipped_teams.append(team_id)
                        self.logger.warning(f"⚠️ Skipping team {team_id} - no permission found on KB {kb_id}")

            # Check if we have any valid entities to update
            if not valid_user_ids and not valid_team_ids:
                return {
                    "success": False,
                    "reason": "No users or teams with existing permissions found to update",
                    "code": 404,
                    "skipped_users": skipped_users,
                    "skipped_teams": skipped_teams
                }

            # Fetch KB document to get creator information
            kb_doc = await self.graph_provider.get_document(
                document_key=kb_id,
                collection=CollectionNames.APPS.value
            )
            if not kb_doc:
                return {"success": False, "reason": "KB not found", "code": 404}

            creator_mongo_id = kb_doc.get("createdBy")
            creator_graph_key = None

            # Resolve creator's MongoDB ID to graph key
            if creator_mongo_id:
                requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
                org_id = requester.get("orgId") if requester else None
                if org_id:
                    creator_mapping = await self.graph_provider.get_graph_user_keys_by_mongo_user_ids(
                        [creator_mongo_id],
                        org_id,
                        chunk_size=1
                    )
                    creator_graph_key = creator_mapping.get(creator_mongo_id) if creator_mapping else None
                    if creator_graph_key:
                        self.logger.info(f"🔑 KB creator identified: {creator_graph_key} (Mongo ID: {creator_mongo_id})")

            owners_being_updated = []
            creator_being_updated = []

            for user_id in valid_user_ids:
                current_role = current_permissions["users"].get(user_id)
                if current_role == "OWNER":
                    owners_being_updated.append(user_id)
                    # Check if this owner is the creator
                    if user_id == creator_graph_key:
                        creator_being_updated.append(user_id)

            # Block changes to creator (unless requester IS the creator attempting self-modification)
            if creator_being_updated and requester_key != creator_graph_key:
                return {
                    "success": False,
                    "reason": "Cannot change permissions for the collection creator.",
                    "code": 403,
                    "creator": creator_being_updated
                }

            # Non-creator owners cannot touch another owner's role/access
            # But the creator CAN change other owners' permissions
            if requester_key != creator_graph_key:
                # Requester is NOT the creator - apply standard owner protection
                other_owners_being_updated = [
                    owner_user_id for owner_user_id in owners_being_updated
                    if owner_user_id != requester_key
                ]
                if other_owners_being_updated:
                    return {
                        "success": False,
                        "reason": "Cannot change permission for other owners.",
                        "code": 403
                    }

            # Bulk Operation Prevention: Cannot perform bulk operations on Owner permissions
            if len(valid_user_ids) > 1 and owners_being_updated:
                # Check if this is a bulk promotion to OWNER (all non-Owners being promoted)
                if new_role == "OWNER" and len(owners_being_updated) == 0:
                    # Bulk promotion to OWNER is allowed but should be logged
                    self.logger.info(f"⚠️ Bulk promotion of {len(valid_user_ids)} users to OWNER role on KB {kb_id}")
                else:
                    # Mixed operation or bulk update of existing Owners - reject
                    return {
                        "success": False,
                        "reason": "Cannot perform bulk operations on Owner permissions. Please update Owners one at a time.",
                        "code": 400
                    }

            # Single Owner Update Validation
            if len(owners_being_updated) == 1 and len(valid_user_ids) == 1:
                owner_user_id = owners_being_updated[0]
                if new_role != "OWNER":
                    # Owner is being downgraded - check minimum requirement using already fetched count
                    if total_owner_count <= 1:
                        return {
                            "success": False,
                            "reason": "Cannot remove all owners from the knowledge base. At least one owner must remain.",
                            "code": 400
                        }
                    self.logger.info(f"⚠️ Downgrading Owner {owner_user_id} to {new_role} on KB {kb_id} (remaining owners: {total_owner_count - 1})")
                # If new_role == "OWNER", it's a no-op (no change), which is fine

            # Update permissions using batch update method for valid entities only
            result = await self.graph_provider.update_kb_permission(
                kb_id=kb_id,
                requester_id=requester_key,
                user_ids=valid_user_ids,
                team_ids=valid_team_ids,
                new_role=new_role
            )

            if result:
                success_msg = f"✅ Permission updated successfully for {len(valid_user_ids)} users and {len(valid_team_ids)} teams"
                if skipped_users or skipped_teams:
                    success_msg += f" (skipped {len(skipped_users)} users and {len(skipped_teams)} teams without permissions)"
                self.logger.info(success_msg)

                return {
                    "success": True,
                    "userIds": valid_user_ids,
                    "teamIds": valid_team_ids,
                    "newRole": new_role,
                    "kbId": kb_id,
                }
            else:
                return {
                    "success": False,
                    "reason": "Failed to update permission",
                    "code": 500
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

    async def remove_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
    ) -> Optional[Dict]:
        """Remove permissions for users and teams from a knowledge base"""
        try:
            self.logger.info(f"🚀 Removing permission for {len(user_ids)} users and {len(team_ids)} teams from KB {kb_id}")

            # Check if at least one of user_ids or team_ids is provided
            if not user_ids and not team_ids:
                return {
                    "success": False,
                    "reason": "No users or teams provided for permission removal",
                    "code": 400
                }

            self.logger.info(f"Looking up requester by requester: {requester_id}")
            requester_key, _, err = await self._resolve_user_and_kb_access(
                kb_id, requester_id, required_roles=["OWNER"],
                permission_denied_reason="Only KB owners can remove permissions",
            )
            if err:
                return err

            graph_user_ids, resolve_err = await self._resolve_user_ids_to_graph_keys(
                user_ids, requester_id
            )
            if resolve_err:
                return resolve_err

            # Get current permissions for all users and teams in a single batch query
            current_permissions = await self.graph_provider.get_kb_permissions(
                kb_id=kb_id,
                user_ids=graph_user_ids,
                team_ids=team_ids
            )

            # Filter out users/teams that don't have permissions and check for owner removal
            valid_user_ids = []
            skipped_users = []
            owner_users_to_remove = []

            if graph_user_ids:
                for graph_user_id in graph_user_ids:
                    if graph_user_id in current_permissions["users"]:
                        current_role = current_permissions["users"][graph_user_id]
                        if current_role == "OWNER":
                            owner_users_to_remove.append(graph_user_id)
                        valid_user_ids.append(graph_user_id)
                    else:
                        skipped_users.append(graph_user_id)
                        self.logger.warning(
                            f"⚠️ Skipping user {graph_user_id} - no permission found on KB {kb_id}"
                        )

            valid_team_ids = []
            skipped_teams = []
            if team_ids:
                for team_id in team_ids:
                    if team_id in current_permissions["teams"]:
                        valid_team_ids.append(team_id)
                    else:
                        skipped_teams.append(team_id)
                        self.logger.warning(f"⚠️ Skipping team {team_id} - no permission found on KB {kb_id}")

            # Check if we have any valid entities to remove
            if not valid_user_ids and not valid_team_ids:
                return {
                    "success": False,
                    "reason": "No users or teams with existing permissions found to remove",
                    "code": 404,
                    "skipped_users": skipped_users,
                    "skipped_teams": skipped_teams
                }

            # Fetch KB document to get creator information
            kb_doc = await self.graph_provider.get_document(
                document_key=kb_id,
                collection=CollectionNames.APPS.value
            )
            if not kb_doc:
                return {"success": False, "reason": "KB not found", "code": 404}

            creator_mongo_id = kb_doc.get("createdBy")
            creator_graph_key = None

            # Resolve creator's MongoDB ID to graph key
            if creator_mongo_id:
                requester = await self.graph_provider.get_user_by_user_id(user_id=requester_id)
                org_id = requester.get("orgId") if requester else None
                if org_id:
                    creator_mapping = await self.graph_provider.get_graph_user_keys_by_mongo_user_ids(
                        [creator_mongo_id],
                        org_id,
                        chunk_size=1
                    )
                    creator_graph_key = creator_mapping.get(creator_mongo_id) if creator_mapping else None
                    if creator_graph_key:
                        self.logger.info(f"🔑 KB creator identified: {creator_graph_key} (Mongo ID: {creator_mongo_id})")

            # Check for owner removal restrictions
            if owner_users_to_remove:
                # Block removing creator entirely (even by themselves)
                if creator_graph_key in owner_users_to_remove:
                    return {
                        "success": False,
                        "reason": "Cannot remove the collection creator. The creator must always retain access.",
                        "code": 403,
                        "creator": creator_graph_key
                    }

                # Non-creator owners cannot remove another owner's access
                # But the creator CAN remove other owners
                if requester_key != creator_graph_key:
                    # Requester is NOT the creator - apply standard owner protection
                    other_owners_to_remove = [
                        owner_user_id for owner_user_id in owner_users_to_remove
                        if owner_user_id != requester_key
                    ]
                    if other_owners_to_remove:
                        return {
                            "success": False,
                            "reason": "Cannot remove other owners.",
                            "code": 403,
                            "owner_users": other_owners_to_remove
                        }

                # Count total owners in the KB
                owner_count = await self.graph_provider.count_kb_owners(kb_id)
                if owner_count <= len(owner_users_to_remove):
                    return {
                        "success": False,
                        "reason": "Cannot remove all owners from the knowledge base. At least one owner must remain.",
                        "code": 400,
                        "owner_users": owner_users_to_remove
                    }

            # Remove permissions using batch remove method for valid entities only
            result = await self.graph_provider.remove_kb_permission(
                kb_id=kb_id,
                user_ids=valid_user_ids,
                team_ids=valid_team_ids
            )

            if result:
                success_msg = f"✅ Permission removed successfully for {len(valid_user_ids)} users and {len(valid_team_ids)} teams"
                if skipped_users or skipped_teams:
                    success_msg += f" (skipped {len(skipped_users)} users and {len(skipped_teams)} teams without permissions)"
                self.logger.info(success_msg)


                return {
                    "success": True,
                    "userIds": valid_user_ids,
                    "teamIds": valid_team_ids,
                    "kbId": kb_id,
                }
            else:
                return {
                    "success": False,
                    "reason": "Failed to remove permissions",
                    "code": 500
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to remove KB permission: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }


    async def list_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
    ) -> Optional[Dict]:
        """List all permissions for a knowledge base"""
        try:
            self.logger.info(f"🔍 Listing permissions for KB {kb_id}")
            requester_key, _, err = await self._resolve_user_and_kb_access(
                kb_id, requester_id, required_roles=["OWNER"],
                permission_denied_reason="Only owners can view the knowledge base sharing list",
            )
            if err:
                return err

            # Get all permissions
            permissions = await self.graph_provider.list_kb_permissions(kb_id)

            self.logger.info(f"✅ Found {len(permissions)} permissions for KB {kb_id}")
            return {
                "success": True,
                "permissions": permissions,
                "kbId": kb_id,
                "totalCount": len(permissions),
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to list KB permissions: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": 500
            }

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
        source: str = "all",  # "all", "local", "connector"
    ) -> Dict:
        """
        List all records the user can access (from all KBs, folders, and direct connector permissions), with filters.
        """
        try:
            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('id') or user.get('_key')

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            sort_by = sort_by if sort_by in [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
            ] else "createdAtTimestamp"

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
                k: v for k, v in {
                    "search": search,
                    "recordTypes": record_types,
                    "origins": origins,
                    "connectors": connectors,
                    "indexingStatus": indexing_status,
                    "source": source if source != "all" else None,
                    "dateRange": {"from": date_from, "to": date_to} if date_from or date_to else None,
                }.items() if v
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
                }
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to list all records: {str(e)}")
            return {
                "records": [],
                "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
                "filters": {"applied": {}, "available": {}},
                "error": str(e),
            }

    async def list_kb_records(
        self,
        kb_id: str,
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
        """
        List all records in a specific KB (including all folders and direct records), with filters.
        The underlying provider filters results to only records the user has access to.
        Service-level permission enforcement is intentionally NOT applied here — doing so
        would change the historical API contract (empty list → 403) and break callers that
        rely on receiving an empty result when the user has no access to a particular KB.
        """
        try:
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('id') or user.get('_key')

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            sort_by = sort_by if sort_by in [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
            ] else "createdAtTimestamp"

            records, total_count, available_filters = await self.graph_provider.list_kb_records(
                kb_id=kb_id,
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

            total_pages = (total_count + limit - 1) // limit

            applied_filters = {
                k: v for k, v in {
                    "search": search,
                    "recordTypes": record_types,
                    "origins": origins,
                    "connectors": connectors,
                    "indexingStatus": indexing_status,
                    "dateRange": {"from": date_from, "to": date_to} if date_from or date_to else None,
                }.items() if v
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
                }
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to list KB records: {str(e)}")
            return {
                "records": [],
                "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
                "filters": {"applied": {}, "available": {}},
                "error": str(e),
            }

    async def get_kb_children(
        self,
        kb_id: str,
        user_id: str,
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
        """
        Get KB root contents with pagination and filters
        """
        try:
            self.logger.info(f"🔍 Getting KB {kb_id} children with pagination (page {page}, limit {limit})")

            user_key, user_role, err = await self._resolve_user_and_kb_access(kb_id, user_id)
            if err:
                return err

            # Calculate offset
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "created_at", "updated_at", "size", "type"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"

            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            # Get paginated contents
            result = await self.graph_provider.get_kb_children(
                kb_id=kb_id,
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
                return self._error_response(404, result.get("reason", "KB not found"))

            # Add pagination metadata
            total_items = result.get("totalCount", 0)
            total_pages = (total_items + limit - 1) // limit

            # Add user context
            result["userPermission"] = {
                "role": user_role,
                "canUpload": user_role in ["OWNER", "WRITER"],
                "canCreateFolders": user_role in ["OWNER", "WRITER"],
                "canEdit": user_role in ["OWNER", "WRITER"],
                "canDelete": user_role in ["OWNER", "WRITER"],
                "canManagePermissions": user_role in ["OWNER"]
            }

            result["pagination"] = {
                "page": page,
                "limit": limit,
                "totalItems": total_items,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1
            }

            result["filters"] = {
                "applied": {
                    k: v for k, v in {
                        "search": search,
                        "record_types": record_types,
                        "origins": origins,
                        "connectors": connectors,
                        "indexing_status": indexing_status,
                        "sort_by": sort_by,
                        "sort_order": sort_order,
                    }.items() if v is not None
                },
                "available": result.get("availableFilters", {})
            }

            self.logger.info(f"✅ KB children retrieved: {result['counts']['folders']} folders, {result['counts']['records']} records")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB children with pagination: {str(e)}")
            return self._error_response(500, str(e))

    async def get_folder_children(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
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
        """
        Get folder contents with pagination and filters
        """
        try:
            self.logger.info(f"🔍 Getting folder {folder_id} children with pagination (page {page}, limit {limit})")

            user_key, user_role, err = await self._resolve_user_and_kb_access(kb_id, user_id)
            if err:
                return err

            # Scope the folder to THIS KB before reading (the folder_id is caller-supplied;
            # without this a member of any KB could read another KB's folder contents).
            if not await self.graph_provider.validate_folder_in_kb(kb_id, folder_id):
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {"success": False, "code": 404, "reason": "Folder not found in knowledge base"}

            # Calculate offset
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "created_at", "updated_at", "size", "type"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"

            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            # Get paginated contents
            result = await self.graph_provider.get_folder_children(
                kb_id=kb_id,
                folder_id=folder_id,
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
                return self._error_response(404, result.get("reason", "Folder not found"))

            # Add pagination metadata
            total_items = result.get("totalCount", 0)
            total_pages = (total_items + limit - 1) // limit

            # Add user context
            result["userPermission"] = {
                "role": user_role,
                "canUpload": user_role in ["OWNER", "WRITER"],
                "canCreateFolders": user_role in ["OWNER", "WRITER"],
                "canEdit": user_role in ["OWNER", "WRITER"],
                "canDelete": user_role in ["OWNER", "WRITER"],
                "canManagePermissions": user_role in ["OWNER"]
            }

            result["pagination"] = {
                "page": page,
                "limit": limit,
                "totalItems": total_items,
                "totalPages": total_pages,
                "hasNext": page < total_pages,
                "hasPrev": page > 1
            }

            result["filters"] = {
                "applied": {
                    k: v for k, v in {
                        "search": search,
                        "record_types": record_types,
                        "origins": origins,
                        "connectors": connectors,
                        "indexing_status": indexing_status,
                        "sort_by": sort_by,
                        "sort_order": sort_order,
                    }.items() if v is not None
                },
                "available": result.get("availableFilters", {})
            }

            # # Add breadcrumb navigation
            # result["breadcrumbs"] = await self.graph_provider.get_breadcrumb_path(
            #     kb_id=kb_id,
            #     folder_id=folder_id
            # )

            self.logger.info(f"✅ Folder children retrieved: {result['counts']['folders']} folders, {result['counts']['records']} records")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder children with pagination: {str(e)}")
            return self._error_response(500, str(e))

    def _error_response(self, code: int, reason: str) -> Dict:
        """Create consistent error response"""
        return {
            "success": False,
            "code": code,
            "reason": reason
        }

    # Convenience methods that call the unified upload method
    async def upload_records_to_kb(self, kb_id: str, user_id: str, org_id: str, files: List[Dict]) -> Dict:
        """Upload to KB root"""
        return await self._upload_records(kb_id, user_id, org_id, files, parent_folder_id=None)

    async def upload_records_to_folder(self, kb_id: str, folder_id: str, user_id: str, org_id: str, files: List[Dict]) -> Dict:
        """Upload to specific folder"""
        return await self._upload_records(kb_id, user_id, org_id, files, parent_folder_id=folder_id)

    async def _get_storage_url(self) -> str:
        """Resolve the storage endpoint used to build upload signed-url routes."""
        try:
            endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
            return (endpoints or {}).get("storage", {}).get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
        except Exception as e:
            self.logger.warning(f"Failed to resolve storage endpoint, using default: {e}")
            return DefaultEndpoints.STORAGE_ENDPOINT.value

    def _build_kb_file_record(
        self,
        kb_id: str,
        record_dict: Dict,
        file_dict: Dict,
        parent_folder_id: Optional[str],
        storage_url: str,
    ) -> FileRecord:
        """Build a KB file FileRecord from the (record, fileRecord) dicts the client
        sends. `from_arango_record` already handles the KB-upload dict shape; we then
        set the KB anchoring fields the client doesn't send and the signed-url route
        the indexing consumer uses to fetch the blob."""
        frec = FileRecord.from_arango_record(
            arango_base_file_record=file_dict, arango_base_record=record_dict
        )
        frec.connector_id = kb_id
        frec.external_record_group_id = kb_id
        frec.parent_external_record_id = parent_folder_id or None
        if storage_url and frec.external_record_id:
            frec.fetch_signed_url = (
                f"{storage_url}/api/v1/document/internal/{frec.external_record_id}/download"
            )
        return frec

    async def _resolve_upload_folders(
        self, kb_id: str, org_id: str, analysis: Dict
    ) -> tuple[Dict[str, str], List[FileRecord]]:
        """Resolve the folder hierarchy inferred from file paths: reuse existing folders,
        mint FileRecords for new ones. Returns (hierarchy_path -> folder_id, new folder records).
        Mirrors the provider's _ensure_folders_exist but assigns ids instead of writing."""
        gp = self.graph_provider
        folder_map: Dict[str, str] = {}
        new_folder_records: List[FileRecord] = []
        upload_parent_folder_id = analysis.get("parent_folder_id")
        for hierarchy_path in analysis["sorted_folder_paths"]:
            info = analysis["folder_hierarchy"][hierarchy_path]
            folder_name = info["name"]
            parent_hierarchy_path = info["parent_path"]
            if parent_hierarchy_path:
                parent_folder_id = folder_map.get(parent_hierarchy_path)
                if parent_folder_id is None:
                    raise ValueError(f"Parent folder resolution failed for path: {parent_hierarchy_path}")
            else:
                parent_folder_id = upload_parent_folder_id
            existing = await gp.find_folder_by_name_in_parent(
                kb_id=kb_id, folder_name=folder_name, parent_folder_id=parent_folder_id
            )
            if existing:
                folder_map[hierarchy_path] = existing.get("_key") or existing.get("id", "")
            else:
                new_id = str(uuid.uuid4())
                folder_map[hierarchy_path] = new_id
                new_folder_records.append(
                    self._build_kb_folder_record(kb_id, new_id, folder_name, org_id, parent_folder_id)
                )
        return folder_map, new_folder_records

    async def _build_upload_file_records(
        self, kb_id: str, files: List[Dict], analysis: Dict, storage_url: str
    ) -> tuple[List[FileRecord], List[Dict], List[str]]:
        """Build file FileRecords, deduplicating by name per destination folder (mirrors
        the provider's _create_files_batch skip logic). Returns (records, skipped, failed)."""
        gp = self.graph_provider
        upload_parent_folder_id = analysis.get("parent_folder_id")
        file_records: List[FileRecord] = []
        skipped_files: List[Dict] = []
        failed_files: List[str] = []

        # Group files by destination parent (None => KB root) for per-parent dedup.
        by_parent: Dict[Optional[str], List[Dict]] = {}
        for index, file_data in enumerate(files):
            dest = analysis["file_destinations"].get(index, {})
            if dest.get("type") == "folder":
                parent_id = dest.get("folder_id")
                if not parent_id:
                    failed_files.append(file_data.get("filePath", ""))
                    continue
            else:
                parent_id = upload_parent_folder_id
            by_parent.setdefault(parent_id, []).append(file_data)

        for parent_id, group in by_parent.items():
            existing_name_mime = await gp._fetch_existing_file_names_in_parent(
                kb_id=kb_id, parent_folder_id=parent_id
            )
            seen: set = set()
            for file_data in group:
                file_rec = file_data.get("fileRecord") or {}
                record = file_data.get("record") or {}
                file_name = gp._normalize_name(file_rec.get("name") or record.get("recordName")) or ""
                mime_type = file_rec.get("mimeType")
                key = (file_name.lower(), str(mime_type or ""))
                variants = gp._normalized_name_variants_lower(file_name)
                conflict = any((v, str(mime_type or "")) in existing_name_mime for v in variants) or key in seen
                if conflict:
                    skipped_files.append({
                        "filePath": file_data.get("filePath", ""),
                        "name": file_name,
                        "reason": "DUPLICATE_NAME",
                    })
                    continue
                seen.add(key)
                file_rec["name"] = file_name
                if "recordName" not in record:
                    record["recordName"] = file_name
                file_records.append(
                    self._build_kb_file_record(kb_id, record, file_rec, parent_id, storage_url)
                )
        return file_records, skipped_files, failed_files

    async def _upload_records(
        self, kb_id: str, user_id: str, org_id: str, files: List[Dict], parent_folder_id: Optional[str]
    ) -> Dict:
        """Create uploaded files (and any inferred folders) through the shared processor.

        Reuses the provider's read-only helpers for validation, path→folder analysis
        and duplicate-name detection, then issues a single on_new_records call (folders
        first so PARENT_CHILD parents exist, then files) — one transaction. Folders are
        created COMPLETED with no event; files emit newRecord (with signedUrlRoute) for
        indexing. The processor publishes the events, so the router publishes nothing.
        """
        gp = self.graph_provider
        upload_type = "folder" if parent_folder_id else "KB root"
        try:
            validation = await gp._validate_upload_context(
                kb_id=kb_id, user_id=user_id, org_id=org_id, parent_folder_id=parent_folder_id
            )
            if not validation.get("valid"):
                return validation

            analysis = gp._analyze_upload_structure(files, validation)

            folder_map, new_folder_records = await self._resolve_upload_folders(kb_id, org_id, analysis)
            gp._populate_file_destinations(analysis, folder_map)

            storage_url = await self._get_storage_url()
            file_records, skipped_files, failed_files = await self._build_upload_file_records(
                kb_id, files, analysis, storage_url
            )

            entities = [(fr, []) for fr in new_folder_records] + [(fr, []) for fr in file_records]
            if entities:
                await self.processor.on_new_records(entities)

            result = {
                "total_created": len(file_records),
                "folders_created": len(new_folder_records),
                "failed_files": failed_files,
                "skipped_files": skipped_files,
            }
            return {
                "success": True,
                "message": gp._generate_upload_message(result, upload_type),
                "totalCreated": len(file_records),
                "foldersCreated": len(new_folder_records),
                "createdFolders": [{"id": fr.id} for fr in new_folder_records],
                "failedFiles": failed_files,
                "skippedFiles": skipped_files,
                "kbId": kb_id,
                "parentFolderId": parent_folder_id,
            }
        except Exception as e:
            self.logger.error(f"❌ Upload records failed: {str(e)}", exc_info=True)
            return {"success": False, "reason": str(e), "code": 500}

    async def validate_folder_for_upload(self, kb_id: str, folder_id: str, user_id: str, org_id: str) -> Dict:
        """Validate that a folder exists and belongs to the KB before upload."""
        return await self.graph_provider.validate_folder_for_upload(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            org_id=org_id,
        )

    async def move_record(
        self,
        kb_id: str,
        record_id: str,
        new_parent_id: Optional[str],
        user_id: str,
    ) -> Dict:
        """
        Move a record (file or folder) to a new location within the same KB.

        Args:
            kb_id:          The knowledge base ID.
            record_id:      The record to move (_key in RECORDS collection).
            new_parent_id:  Target folder ID, or None to move to KB root.
            user_id:        The requesting user's external userId.

        Returns:
            Dict with success/failure details.
        """
        # Normalise: treat empty string the same as None (= move to KB root)
        new_parent_id = new_parent_id or None
        txn_id: Optional[str] = None

        try:
            destination = "KB root" if new_parent_id is None else f"folder {new_parent_id}"
            self.logger.info(f"🚀 Moving record {record_id} → {destination} in KB {kb_id}")

            # ── 1. Resolve user + KB access ──────────────────────────────────
            user_key, user_role, err = await self._resolve_user_and_kb_access(
                kb_id, user_id, required_roles=["OWNER", "WRITER"]
            )
            if err:
                return err

            self.logger.info(f"User role: {user_role}")

            # ── 3. Verify record belongs to this KB ──────────────────────────
            kb_context = await self.graph_provider._get_kb_context_for_record(record_id)
            if not kb_context or kb_context.get("kb_id") != kb_id:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record {record_id} not found in KB {kb_id}",
                }
            self.logger.info(f"KB context: {kb_context}")

            # ── 4. Get current parent via RECORD_RELATIONS ───────────────────
            parent_info = await self.graph_provider.get_record_parent_info(record_id)
            self.logger.info(f"Parent info: {parent_info}")
            # parent_info = {"parentId": str, "parentType": "record"|"recordGroup", "edgeKey": str} | None
            # None  →  record is already at KB root (no PARENT_CHILD edge exists)
            current_parent_id = parent_info.get("id") if parent_info else None
            self.logger.info(f"Current parent id: {current_parent_id}")
            self.logger.info(
                f"📍 Record {record_id} current parent: {current_parent_id or 'KB root'}"
            )

            # ── 5. No-op check ───────────────────────────────────────────────
            if new_parent_id == current_parent_id:
                self.logger.info(f"↩️  Record {record_id} already at {destination}, skipping")
                return {
                    "success": True,
                    "message": "Record is already in the requested location",
                    "recordId": record_id,
                    "newParentId": new_parent_id,
                }

            # ── 6. Validate target folder (if not moving to root) ────────────
            if new_parent_id is not None:
                folder_valid = await self.graph_provider.validate_folder_in_kb(kb_id, new_parent_id)
                if not folder_valid:
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"Target folder {new_parent_id} not found in KB {kb_id}",
                    }

                # Circular-reference guard (only relevant when moving a folder)
                if new_parent_id == record_id:
                    return {"success": False, "code": 400, "reason": "Cannot move a folder into itself"}

                record_is_folder = await self.graph_provider.is_record_folder(record_id)
                if record_is_folder:
                    is_circular = await self.graph_provider.is_record_descendant_of(
                        record_id=new_parent_id,
                        ancestor_id=record_id,
                    )
                    if is_circular:
                        return {
                            "success": False,
                            "code": 400,
                            "reason": "Cannot move a folder into one of its own sub-folders (circular reference)",
                        }

            # ── 6.5. Check for destination sibling name conflicts ────────────
            # Load the record's name and determine if it's a folder or file
            moving_record = await self.graph_provider.get_document(record_id, "records")
            if not moving_record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record {record_id} not found",
                }
            
            record_name = moving_record.get("recordName", "")
            is_folder = await self.graph_provider.is_record_folder(record_id)
            
            if is_folder:
                # Check for folder name conflict in destination
                conflict_err = await self._assert_no_folder_sibling_conflict(
                    kb_id=kb_id,
                    parent_folder_id=new_parent_id,
                    folder_name=record_name,
                    exclude_record_id=record_id,
                )
                if conflict_err:
                    return conflict_err
            else:
                # Check for file name conflict in destination
                file_doc = await self.graph_provider.get_document(record_id, "files")
                if file_doc and file_doc.get("isFile"):
                    mime_type = file_doc.get("mimeType", "")
                    conflict_err = await self._assert_no_file_sibling_conflict(
                        kb_id=kb_id,
                        parent_folder_id=new_parent_id,
                        file_name=record_name,
                        mime_type=mime_type,
                        exclude_record_id=record_id,
                    )
                    if conflict_err:
                        return conflict_err

            # ── 7. Move through the shared processor ─────────────────────────
            # Validation above stays here. The processor re-points the PARENT_CHILD
            # edge by _key, refreshes the apps anchor, updates externalParentId via
            # the re-upserted record, and emits no reindex event for a pure move.
            record = await self.graph_provider.get_file_record_by_id(record_id)
            if not record:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Record {record_id} not found",
                }
            old_external_id = record.external_record_id
            record.parent_external_record_id = new_parent_id  # None => KB root (no edge)
            await self.processor.on_records_moved([(old_external_id, record, [])])

            self.logger.info(f"✅ Record {record_id} moved → {destination}")
            return {
                "success": True,
                "recordId": record_id,
                "kbId": kb_id,
                "newParentId": new_parent_id,
                "previousParentId": current_parent_id,
            }

        except Exception as e:
            self.logger.error(
                f"❌ move_record failed: record={record_id} target={new_parent_id!r} "
                f"kb={kb_id} — {str(e)}",
                exc_info=True,
            )
            if txn_id is not None:
                try:
                    await self.graph_provider.rollback_transaction(txn_id)
                    self.logger.info("🔄 Transaction rolled back")
                except Exception as rb_err:
                    self.logger.warning(f"Rollback failed: {rb_err}")
            return {"success": False, "code": 500, "reason": str(e)}
