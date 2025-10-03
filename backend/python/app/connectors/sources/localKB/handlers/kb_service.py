import asyncio
import uuid
from typing import Dict, List, Optional, Union

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.services.kafka_service import KafkaService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KnowledgeBaseService :
    """ Data handler for knowledge base opertaions   """

    def __init__(
        self,
        logger,
        arango_service : BaseArangoService,
        kafka_service : KafkaService
    ) -> None:
        self.logger = logger
        self.arango_service = arango_service
        self.kafka_service = kafka_service


    async def create_knowledge_base(
        self,
        user_id: str,
        org_id: str,
        name: str
    ) -> Optional[Dict]:
        """Create a new knowledge base"""
        try:
            self.logger.info(f"🚀 Creating KB '{name}' for user {user_id} in org {org_id}")

            # Step 1: Look up user
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)
            if not user:
                self.logger.warning(f"⚠️ User not found: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }

            user_key = user.get('_key')
            user_name = user.get('fullName') or f"{user.get('firstName', '')} {user.get('lastName', '')}".strip() or 'Unknown'
            self.logger.info(f"✅ Found user: {user_name} (key: {user_key})")

            # Step 2: Generate data
            timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid.uuid4())

            self.logger.info(f"📋 Generated KB ID: {kb_key}")

            # Step 3: Create transaction
            transaction = None
            try:
                transaction = self.arango_service.db.begin_transaction(
                    write=[
                        CollectionNames.RECORD_GROUPS.value,
                        CollectionNames.RECORDS.value,
                        CollectionNames.FILES.value,
                        CollectionNames.IS_OF_TYPE.value,
                        CollectionNames.BELONGS_TO.value,
                        CollectionNames.PERMISSIONS_TO_KB.value,
                    ]
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
                "_key": kb_key,
                "createdBy": user_key,
                "orgId": org_id,
                "groupName": name,
                "groupType": Connectors.KNOWLEDGE_BASE.value,
                "connectorName": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            }

            permission_edge = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_key}",
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
                "lastUpdatedTimestampAtSource": timestamp,
            }


            # Step 5: Execute database operations
            self.logger.info("💾 Executing database operations...")
            result = await self.arango_service.create_knowledge_base(
                kb_data=kb_data,
                # root_folder_data=root_folder_data,
                permission_edge=permission_edge,
                # folder_edge=folder_edge,
                transaction=transaction
            )

            await asyncio.to_thread(lambda: transaction.commit_transaction())

            if result and result.get("success"):
                response = {
                    "id": kb_data["_key"],
                    "name": kb_data["groupName"],
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
            self.logger.error(f"❌ Error type: {type(e).__name__}")
            if hasattr(transaction, 'abort_transaction'):
                    await asyncio.to_thread(lambda: transaction.abort_transaction())

            # Only log full traceback for unexpected errors (not business logic errors)
            if not isinstance(e, (ValueError, KeyError)):
                import traceback
                self.logger.error(f"❌ Traceback: {traceback.format_exc()}")

            return {
                "success": False,
                "code": 500,
                "reason": str(e)
            }

    async def get_knowledge_base(
        self,
        kb_id:str,
        user_id:str
    )-> Optional[Dict]:
        """Get Knowledge base details"""
        try:
            self.logger.info(f"Getting knowledge base {kb_id} for user {user_id}")
            self.logger.info(f"🔍 Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')
            # validates permissions and gets kb for the user
            user_role = await self.arango_service.get_user_kb_permission(
                kb_id=kb_id,
                user_id=user_key
            )
            if not user_role:
                self.logger.warning(f"⚠️ User {user_key} has no access to KB {kb_id}")
                response = {
                    "success": False,
                    "reason": "User has no permission for KnowledgeBase",
                    "code": "403"
                }
                return response

            result = await self.arango_service.get_knowledge_base(
                kb_id = kb_id,
                user_id = user_key
            )

            if result:
                self.logger.info(f"Knowledge base retrieved successfullu: {result}")
                return result
            else :
                self.logger.warning("Knowledge base not found")
                return {
                    "success":False,
                    "reason":"Knowledge base not found",
                    "code":"404"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code":"500"
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
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')

            # Calculate pagination
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "updatedAtTimestamp", "updatedAtTimestamp", "userRole"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"
            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            kbs, total_count, available_filters = await self.arango_service.list_user_knowledge_bases(
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
            # Check user permissions
            self.logger.info(f"🔍 Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')

            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER","ORGANIZER","FILEORGANIZER"]:
                self.logger.warning(f"⚠️ User {user_id} lacks permission to update KB {kb_id}")
                response = {
                    "success": False,
                    "reason": "User has no permission for KnowledgeBase",
                    "code": "403"
                }
                return response
            updates["updatedAtTimestamp"] = timestamp
            # Update in database
            result = await self.arango_service.update_knowledge_base(
                kb_id=kb_id,
                updates=updates
            )

            if result:
                self.logger.info("✅ Knowledge base updated successfully")
                return {
                    "success":True,
                    "reason":"Knowledge base updated successfully",
                    "code":"200"
                }
            else:
                self.logger.warning("⚠️ Failed to update knowledge base")
                return {
                    "success":False,
                    "reason":"Knowledge base not found",
                    "code":"404"
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
    ) -> Optional[Dict]:
        """Delete a knowledge base"""
        try:
            self.logger.info(f"🚀 Deleting knowledge base {kb_id}")
            self.logger.info(f"🔍 Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')
            user_name = user.get('fullName') or f"{user.get('firstName', '')} {user.get('lastName', '')}".strip() or 'Unknown'
            self.logger.info(f"✅ Found user: {user_name} (key: {user_key})")

            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if user_role != "OWNER":
                self.logger.warning(f"⚠️ User {user_name} lacks permission to delete KB {kb_id}")
                return {
                    "success": False,
                    "reason": "Only KB owners can delete knowledge bases",
                    "code": 403
                }

            self.logger.info(f"🔐 User {user_name} has OWNER permission - proceeding with deletion")

            # Delete in database with transaction
            result = await self.arango_service.delete_knowledge_base(
                kb_id=kb_id
           )

            if result:
                self.logger.info(f"✅ Knowledge base {kb_id} deleted successfully by {user_name}")
                return {
                    "success": True,
                    "reason": "Knowledge base and all contents deleted successfully",
                    "code": 200
                }
            else:
                self.logger.warning(f"⚠️ Failed to delete knowledge base {kb_id}")
                return {
                    "success": False,
                    "reason": "Failed to delete knowledge base",
                    "code": 500
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

            # Validate user and permissions
            validation_result = await self.arango_service._validate_folder_creation(kb_id, user_id)
            if not validation_result["valid"]:
                return validation_result

            # Check for name conflicts in KB root
            existing_folder = await self.arango_service.find_folder_by_name_in_parent(
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

            # Create folder using unified method
            result = await self.arango_service.create_folder(
                kb_id=kb_id,
                folder_name=name,
                org_id=org_id,
                parent_folder_id=None,  # KB root
            )

            if result and result.get("success"):
                return result
            else:
                return {"success": False, "code": 500, "reason": "Failed to create folder"}

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

            # Validate user and permissions
            validation_result = await self.arango_service._validate_folder_creation(kb_id, user_id)
            if not validation_result["valid"]:
                return validation_result

            # Additional validation for parent folder
            folder_valid = await self.arango_service.validate_folder_exists_in_kb(kb_id, parent_folder_id)
            if not folder_valid:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"Parent folder {parent_folder_id} not found in KB {kb_id}"
                }

            # Check for name conflicts in KB root
            existing_folder = await self.arango_service.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=name,
                parent_folder_id=parent_folder_id,
            )

            if existing_folder:
                return {
                    "success": False,
                    "code": 409,
                    "reason": f"Folder '{name}' already exists in KB root"
                }

            # Create folder using unified method
            result = await self.arango_service.create_folder(
                kb_id=kb_id,
                folder_name=name,
                org_id=org_id,
                parent_folder_id=parent_folder_id,  # Nested folder
            )

            if result and result.get("success"):
                return result
            else:
                return {"success": False, "code": 500, "reason": "Failed to create folder"}

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
            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')
            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if not user_role:
                self.logger.warning(f"⚠️ User {user_key} lacks access to KB {kb_id}")
                response = {
                    "success": False,
                    "reason": "User has no permission for KnowledgeBase",
                    "code": "403"
                }
                return response

            # Get folder contents
            result = await self.arango_service.get_folder_contents(
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
                    "code": 400,
                    "reason": "Failed to get folder contents, or folder not found "
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
            # timestamp = get_epoch_timestamp_in_ms()
            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')
            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                self.logger.warning(f"⚠️ User {user_key} lacks permission to update folder in KB {kb_id}")
                response = {
                    "success": False,
                    "reason": "User has no permission for KnowledgeBase",
                    "code": "403"
                }
                return response

             # Validate that folder exists and belongs to the KB
            folder_exists = await self.arango_service.validate_folder_in_kb(kb_id, folder_id)
            if not folder_exists:
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": "404"
                }

            updates = {
                "name": name
                # "updatedAtTimestamp": timestamp
            }

            # Check for name conflicts in KB root
            existing_folder = await self.arango_service.find_folder_by_name_in_parent(
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


            # Update in database
            result = await self.arango_service.update_folder(
                folder_id=folder_id,
                updates=updates
            )

            if result:
                self.logger.info(f"✅ Folder updated successfully: {folder_id} by user {user_id}")
                return {
                    "success": True,
                    "code": 200,
                    "reason": "Updated folder successfully"
                 }
            else:
                self.logger.warning(f"⚠️ Failed to update folder {folder_id}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": "Failed to update the folder"
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
            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')

            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if user_role != "OWNER":
                self.logger.warning(f"⚠️ User {user_key} lacks permission to add records in KB {kb_id}")
                return {
                    "success": False,
                    "reason": "User lacks permission to add records",
                    "code": "403"
                }
            # Validate that folder exists and belongs to the KB
            folder_exists = await self.arango_service.validate_folder_in_kb(kb_id, folder_id)
            if not folder_exists:
                self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": "404"
                }

            # Delete in database
            result = await self.arango_service.delete_folder(
                kb_id=kb_id,
                folder_id=folder_id
            )

            if result:
                self.logger.info(f"🎉 Folder {folder_id} and ALL contents deleted successfully by {user_id}")
                return {
                    "success": True,
                    "reason": "Folder and all contents deleted successfully",
                    "code": 200,
                }
            else:
                self.logger.warning("⚠️ Failed to delete folder")
                return {
                    "success": False,
                    "code": 500,
                    "reason": "Failed to delete folder"
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
        Update a record directly in KB root (not in any folder)
        """
        try:
            self.logger.info(f"🚀 Updating record {record_id}")

            # Call update method
            result = await self.arango_service.update_record(
                record_id=record_id,
                user_id=user_id,
                updates=updates,
                file_metadata=file_metadata
            )

            if result and result.get("success"):
                return result
            else:
                return result or {
                    "success": False,
                    "reason": "Failed to update record",
                    "code": 500
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

            # Step 1: Validate user and permissions
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }

            user_key = user.get('_key')
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)

            if user_role not in ["OWNER", "WRITER", "FILEORGANIZER"]:
                return {
                    "success": False,
                    "reason": "User lacks permission to delete records",
                    "code": 403
                }

            # Step 2: Call bulk deletion method (folder_id=None for KB root)
            result = await self.arango_service.delete_records(
                record_ids=record_ids,
                kb_id=kb_id,
                folder_id=None  # KB root records
            )

            if result and result.get("success"):
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

            # Step 1: Validate user and permissions
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)
            if not user:
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }

            user_key = user.get('_key')
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)

            if user_role not in ["OWNER", "WRITER", "FILEORGANIZER"]:
                return {
                    "success": False,
                    "reason": "User lacks permission to delete records",
                    "code": 403
                }

            # Step 2: Validate folder exists in KB
            folder_exists = await self.arango_service.validate_folder_exists_in_kb(kb_id, folder_id)
            if not folder_exists:
                return {
                    "success": False,
                    "reason": "Folder not found in knowledge base",
                    "code": 404
                }

        # Step 3 Call bulk deletion method (folder_id=None for KB root)
            result = await self.arango_service.delete_records(
                record_ids=record_ids,
                kb_id=kb_id,
                folder_id=folder_id
            )

            if result and result.get("success"):
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
            valid_roles = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]
            if role not in valid_roles:
                return {"success": False, "reason": f"Invalid role: {role}", "code": 400}

            unique_users = list(set(user_ids))
            unique_teams = list(set(team_ids))
            if not unique_users and not unique_teams:
                return {"success": False, "reason": "No users or teams provided", "code": 400}

            # Step 2: Single AQL query to do everything at once
            result = await self.arango_service.create_kb_permissions(
                kb_id=kb_id,
                requester_id=requester_id,
                user_ids=unique_users,
                team_ids=unique_teams,
                role=role
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
                    "code": "400"
                }

            self.logger.info(f"Looking up requester by requester: {requester_id}")
            requester = await self.arango_service.get_user_by_user_id(user_id=requester_id)

            if not requester:
                self.logger.warning(f"⚠️ User not found for user_id: {requester_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}"
                }
            requester_key = requester.get('_key')

            # Validate requester has permission to update permissions
            requester_role = await self.arango_service.get_user_kb_permission(kb_id, requester_key)
            if requester_role not in ["OWNER"]:
                return {
                    "success": False,
                    "reason": "Only KB owners can update permissions",
                    "code": "403"
                }

            # Validate new role
            valid_roles = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]
            if new_role not in valid_roles:
                return {
                    "success": False,
                    "reason": f"Invalid role. Must be one of: {', '.join(valid_roles)}",
                    "code": "400"
                }

            # Get current permissions for all users and teams in a single batch query
            current_permissions = await self.arango_service.get_kb_permissions(
                kb_id=kb_id,
                user_ids=user_ids,
                team_ids=team_ids
            )

            # Filter out users/teams that don't have permissions (skip them instead of erroring)
            valid_user_ids = []
            skipped_users = []
            if user_ids:
                for user_id in user_ids:
                    if user_id in current_permissions["users"]:
                        valid_user_ids.append(user_id)
                    else:
                        skipped_users.append(user_id)
                        self.logger.warning(f"⚠️ Skipping user {user_id} - no permission found on KB {kb_id}")

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
                    "code": "404",
                    "skipped_users": skipped_users,
                    "skipped_teams": skipped_teams
                }

            # Update permissions using batch update method for valid entities only
            result = await self.arango_service.update_kb_permission(
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
                    "code": "500"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": "500"
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
                    "code": "400"
                }

            self.logger.info(f"Looking up requester by requester: {requester_id}")
            requester = await self.arango_service.get_user_by_user_id(user_id=requester_id)

            if not requester:
                self.logger.warning(f"⚠️ User not found for user_id: {requester_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}"
                }
            requester_key = requester.get('_key')

            # Validate requester has permission to remove permissions
            requester_role = await self.arango_service.get_user_kb_permission(kb_id, requester_key)
            if requester_role not in ["OWNER"]:
                return {
                    "success": False,
                    "reason": "Only KB owners can remove permissions",
                    "code": "403"
                }

            # Get current permissions for all users and teams in a single batch query
            current_permissions = await self.arango_service.get_kb_permissions(
                kb_id=kb_id,
                user_ids=user_ids,
                team_ids=team_ids
            )

            # Filter out users/teams that don't have permissions and check for owner removal
            valid_user_ids = []
            skipped_users = []
            owner_users_to_remove = []

            if user_ids:
                for user_id in user_ids:
                    if user_id in current_permissions["users"]:
                        current_role = current_permissions["users"][user_id]
                        if current_role == "OWNER":
                            owner_users_to_remove.append(user_id)
                        valid_user_ids.append(user_id)
                    else:
                        skipped_users.append(user_id)
                        self.logger.warning(f"⚠️ Skipping user {user_id} - no permission found on KB {kb_id}")

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
                    "code": "404",
                    "skipped_users": skipped_users,
                    "skipped_teams": skipped_teams
                }

            # Check for owner removal restrictions
            if owner_users_to_remove:
                # Count total owners in the KB
                owner_count = await self.arango_service.count_kb_owners(kb_id)
                if owner_count <= len(owner_users_to_remove):
                    return {
                        "success": False,
                        "reason": "Cannot remove all owners from the knowledge base. At least one owner must remain.",
                        "code": "400",
                        "owner_users": owner_users_to_remove
                    }

            # Remove permissions using batch remove method for valid entities only
            result = await self.arango_service.remove_kb_permission(
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
                    "code": "500"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to remove KB permission: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": "500"
            }


    async def list_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
    ) -> Optional[Dict]:
        """List all permissions for a knowledge base"""
        try:
            self.logger.info(f"🔍 Listing permissions for KB {kb_id}")
            self.logger.info(f"Looking up requester by requester: {requester_id}")
            requester = await self.arango_service.get_user_by_user_id(user_id=requester_id)

            if not requester:
                self.logger.warning(f"⚠️ User not found for user_id: {requester_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {requester_id}"
                }
            requester_key = requester.get('_key')

            # Validate requester has access to the KB
            requester_role = await self.arango_service.get_user_kb_permission(kb_id, requester_key)
            if not requester_role:
                return {
                    "success": False,
                    "reason": "User does not have access to this knowledge base",
                    "code": "403"
                }

            # Get all permissions
            permissions = await self.arango_service.list_kb_permissions(kb_id)

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
                "code": "500"
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
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            sort_by = sort_by if sort_by in [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
            ] else "createdAtTimestamp"

            records, total_count, available_filters = await self.arango_service.list_all_records(
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
        """
        try:
            self.logger.info(f"Looking up user by user_id: {user_id}")
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)

            if not user:
                self.logger.warning(f"⚠️ User not found for user_id: {user_id}")
                return {
                    "success": False,
                    "code": 404,
                    "reason": f"User not found for user_id: {user_id}"
                }
            user_key = user.get('_key')

            skip = (page - 1) * limit
            sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
            sort_by = sort_by if sort_by in [
                "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
            ] else "createdAtTimestamp"

            records, total_count, available_filters = await self.arango_service.list_kb_records(
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

            # Validate user
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")

            user_key = user.get('_key')

            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if not user_role:
                return self._error_response(403, "User has no permission for KnowledgeBase")

            # Calculate offset
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "created_at", "updated_at", "size", "type"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"

            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            # Get paginated contents
            result = await self.arango_service.get_kb_children(
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
                "canEdit": user_role in ["OWNER", "WRITER", "FILEORGANIZER"],
                "canDelete": user_role in ["OWNER"]
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

            # Validate user
            user = await self.arango_service.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._error_response(404, f"User not found: {user_id}")

            user_key = user.get('_key')

            # Check user permissions
            user_role = await self.arango_service.get_user_kb_permission(kb_id, user_key)
            if not user_role:
                return self._error_response(403, "User has no permission for KnowledgeBase")

            # Calculate offset
            skip = (page - 1) * limit

            # Validate sort parameters
            valid_sort_fields = ["name", "created_at", "updated_at", "size", "type"]
            if sort_by not in valid_sort_fields:
                sort_by = "name"

            if sort_order.lower() not in ["asc", "desc"]:
                sort_order = "asc"

            # Get paginated contents
            result = await self.arango_service.get_folder_children(
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
                "canEdit": user_role in ["OWNER", "WRITER", "FILEORGANIZER"],
                "canDelete": user_role in ["OWNER"]
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
            # result["breadcrumbs"] = await self.arango_service.get_breadcrumb_path(
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

    # Convenience methods that call the unified method
    async def upload_records_to_kb(self, kb_id: str, user_id: str, org_id: str, files: List[Dict]) -> Dict:
        """Upload to KB root"""
        return await self.arango_service.upload_records(kb_id, user_id, org_id, files, parent_folder_id=None)

    async def upload_records_to_folder(self, kb_id: str, folder_id: str, user_id: str, org_id: str, files: List[Dict]) -> Dict:
        """Upload to specific folder"""
        return await self.arango_service.upload_records(kb_id, user_id, org_id, files, parent_folder_id=folder_id)
