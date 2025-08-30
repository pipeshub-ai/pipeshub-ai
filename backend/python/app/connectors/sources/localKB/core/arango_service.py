"""ArangoDB service for interacting with the database"""
import asyncio
import uuid
from typing import Dict, List, Optional, Tuple

from arango import ArangoClient
from arango.database import TransactionDatabase

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
)
from app.config.constants.service import (
    DefaultEndpoints,
    config_node_constants,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.services.kafka_service import KafkaService
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class KnowledgeBaseArangoService(BaseArangoService):
    """ArangoDB service class for interacting with the database"""

    def __init__(
        self,
        logger,
        arango_client: ArangoClient,
        kafka_service: KafkaService,
        config_service: ConfigurationService,
    ) -> None:
        # Call parent class constructor to initialize shared attributes
        super().__init__(logger, arango_client, config_service,kafka_service)
        self.kafka_service = kafka_service
        self.config_service = config_service
        self.logger = logger


    async def _create_new_record_event_payload(self, record_doc: Dict, file_doc: Dict, storage_url: str) -> Dict:
        """
        Creates  NewRecordEvent to Kafka,
        """
        try:
            record_id = record_doc["_key"]
            self.logger.info(f"🚀 Preparing NewRecordEvent for record_id: {record_id}")

            signed_url_route = (
                f"{storage_url}/api/v1/document/internal/{record_doc['externalRecordId']}/download"
            )
            timestamp = get_epoch_timestamp_in_ms()

            # Construct the payload matching the Node.js NewRecordEvent interface
            payload = {
                "orgId": record_doc.get("orgId"),
                "recordId": record_id,
                "recordName": record_doc.get("recordName"),
                "recordType": record_doc.get("recordType"),
                "version": record_doc.get("version", 1),
                "signedUrlRoute": signed_url_route,
                "origin": record_doc.get("origin"),
                "extension": file_doc.get("extension", ""),
                "mimeType": file_doc.get("mimeType", ""),
                "createdAtTimestamp": str(record_doc.get("createdAtTimestamp",timestamp)),
                "updatedAtTimestamp": str(record_doc.get("updatedAtTimestamp",timestamp)),
                "sourceCreatedAtTimestamp": str(record_doc.get("sourceCreatedAtTimestamp",record_doc.get("createdAtTimestamp", timestamp))),
            }

            return payload
        except Exception:
            self.logger.error(
                f"❌ Failed to publish NewRecordEvent for record_id: {record_doc.get('_key', 'N/A')}",
                exc_info=True
            )
            return {}

    async def _publish_upload_events(self, kb_id: str, result: Dict) -> None:
        """
        Enhanced event publishing with better error handling
        """
        try:
            self.logger.info(f"This is the result passed to publish record events {result}")
            # Get the full data of created files directly from the transaction result
            created_files_data = result.get("created_files_data", [])

            if not created_files_data:
                self.logger.info("No new records were created, skipping event publishing.")
                return

            self.logger.info(f"🚀 Publishing creation events for {len(created_files_data)} new records.")

            # Get storage endpoint
            try:
                endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
                self.logger.info(f"This the the endpoint {endpoints}")
                storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)
            except Exception as config_error:
                self.logger.error(f"❌ Failed to get storage config: {str(config_error)}")
                storage_url = "http://localhost:3000"  # Fallback

            # Create events with enhanced error handling
            successful_events = 0
            failed_events = 0

            for file_data in created_files_data:
                try:
                    record_doc = file_data.get("record")
                    file_doc = file_data.get("fileRecord")

                    if record_doc and file_doc:
                        # Create payload with error handling
                        create_payload = await self._create_new_record_event_payload(
                            record_doc, file_doc, storage_url
                        )

                        if create_payload:  # Only publish if payload creation succeeded
                            await self._publish_record_event("newRecord", create_payload)
                            successful_events += 1
                        else:
                            self.logger.warning(f"⚠️ Skipping event for record {record_doc.get('_key')} - payload creation failed")
                            failed_events += 1
                    else:
                        self.logger.warning(f"⚠️ Incomplete file data found, cannot publish event: {file_data}")
                        failed_events += 1

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish event for record: {str(event_error)}")
                    failed_events += 1

            self.logger.info(f"📊 Event publishing summary: {successful_events} successful, {failed_events} failed")

        except Exception as e:
            self.logger.error(f"❌ Critical error in event publishing for KB {kb_id}: {str(e)}", exc_info=True)


    async def _create_update_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict] = None
    ) -> Dict:
        """Create update record event payload matching Node.js format"""
        try:
            endpoints = await self.config_service.get_config(
                    config_node_constants.ENDPOINTS.value
                )
            storage_url = endpoints.get("storage").get("endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value)

            signed_url_route = f"{storage_url}/api/v1/document/internal/{record['externalRecordId']}/download"

            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            return {
                "orgId": record.get("orgId"),
                "recordId": record.get("_key"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "signedUrlRoute": signed_url_route,
                "updatedAtTimestamp": str(record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms())),
                "sourceLastModifiedTimestamp": str(record.get("sourceLastModifiedTimestamp", record.get("updatedAtTimestamp", get_epoch_timestamp_in_ms()))),
                "virtualRecordId": record.get("virtualRecordId"),
                "summaryDocumentId": record.get("summaryDocumentId"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create update record event payload: {str(e)}")
            return {}

    async def _create_deleted_record_event_payload(
        self,
        record: Dict,
        file_record: Optional[Dict] = None
    ) -> Dict:
        """Create deleted record event payload matching Node.js format"""
        try:
            # Get extension and mimeType from file record
            extension = ""
            mime_type = ""
            if file_record:
                extension = file_record.get("extension", "")
                mime_type = file_record.get("mimeType", "")

            return {
                "orgId": record.get("orgId"),
                "recordId": record.get("_key"),
                "version": record.get("version", 1),
                "extension": extension,
                "mimeType": mime_type,
                "summaryDocumentId": record.get("summaryDocumentId"),
                "virtualRecordId": record.get("virtualRecordId"),
            }
        except Exception as e:
            self.logger.error(f"❌ Failed to create deleted record event payload: {str(e)}")
            return {}

    async def _publish_record_event(self, event_type: str, payload: Dict) -> None:
        """Publish record event to Kafka"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            event = {
                "eventType": event_type,
                "timestamp": timestamp,
                "payload": payload
            }

            await self.kafka_service.publish_event("record-events", event)
            self.logger.info(f"✅ Published {event_type} event for record {payload.get('recordId')}")

        except Exception as e:
            self.logger.error(f"❌ Failed to publish {event_type} event: {str(e)}")

    def _validation_error(self, code: int, reason: str) -> Dict:
        """Helper to create validation error response"""
        return {"valid": False, "success": False, "code": code, "reason": reason}

    def _analyze_upload_structure(self, files: List[Dict], validation_result: Dict) -> Dict:
        """
        Updated structure analysis - creates folder hierarchy map based on file paths
        but uses names for validation
        """
        folder_hierarchy = {}  # path -> {name: str, parent_path: str, level: int}
        file_destinations = {}  # file_index -> {type: "root"|"folder", folder_name: str|None, folder_hierarchy_path: str|None}

        for index, file_data in enumerate(files):
            file_path = file_data["filePath"]

            if "/" in file_path:
                # File is in a subfolder - analyze the hierarchy
                path_parts = file_path.split("/")
                folder_parts = path_parts[:-1]

                # Build folder hierarchy
                current_path = ""
                for i, folder_name in enumerate(folder_parts):
                    parent_path = current_path if current_path else None
                    current_path = f"{current_path}/{folder_name}" if current_path else folder_name

                    if current_path not in folder_hierarchy:
                        folder_hierarchy[current_path] = {
                            "name": folder_name,
                            "parent_path": parent_path,
                            "level": i + 1
                        }

                # File goes to the deepest folder
                file_destinations[index] = {
                    "type": "folder",
                    "folder_name": folder_parts[-1],  # Only the immediate parent folder name
                    "folder_hierarchy_path": current_path,  # Full hierarchy path for creation
                }
            else:
                # File goes to upload target (KB root or parent folder)
                file_destinations[index] = {
                    "type": "root",
                    "folder_name": None,
                    "folder_hierarchy_path": None,
                }

        # Sort folders by level (create parents first)
        sorted_folder_paths = sorted(folder_hierarchy.keys(), key=lambda x: folder_hierarchy[x]["level"])

        # Add parent folder context to the analysis
        parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            parent_folder_id = validation_result["parent_folder"].get("_key") if validation_result.get("parent_folder") else None

        return {
            "folder_hierarchy": folder_hierarchy,
            "sorted_folder_paths": sorted_folder_paths,
            "file_destinations": file_destinations,
            "upload_target": validation_result["upload_target"],
            "parent_folder_id": parent_folder_id,
            "summary": {
                "total_folders": len(folder_hierarchy),
                "root_files": len([d for d in file_destinations.values() if d["type"] == "root"]),
                "folder_files": len([d for d in file_destinations.values() if d["type"] == "folder"])
            }
        }

    async def _ensure_folders_exist(
        self,
        kb_id: str,
        org_id: str,
        folder_analysis: Dict,
        validation_result: Dict,
        transaction,
        timestamp: int
    ) -> Dict[str, str]:
        """
        Updated folder creation - uses name-based validation instead of path
        """
        folder_map = {}  # hierarchy_path -> folder_id
        upload_parent_folder_id = None
        if validation_result["upload_target"] == "folder":
            upload_parent_folder_id = validation_result["parent_folder"].get("_key") if validation_result.get("parent_folder") else None

        for hierarchy_path in folder_analysis["sorted_folder_paths"]:
            folder_info = folder_analysis["folder_hierarchy"][hierarchy_path]
            folder_name = folder_info["name"]
            parent_hierarchy_path = folder_info["parent_path"]

            # Determine parent folder ID
            parent_folder_id = None
            if parent_hierarchy_path:
                # Has a parent folder in the hierarchy
                parent_folder_id = folder_map.get(parent_hierarchy_path)
                if parent_folder_id is None:
                    self.logger.error(f"❌ Parent folder not found in map for path: {parent_hierarchy_path}")
                    raise Exception(f"Parent folder creation failed for path: {parent_hierarchy_path}")
            elif upload_parent_folder_id:
                # First level folder under the upload target folder
                parent_folder_id = upload_parent_folder_id
            # else: parent_folder_id remains None (KB root)

            # Check if folder already exists using name-based lookup
            existing_folder = await self.find_folder_by_name_in_parent(
                kb_id=kb_id,
                folder_name=folder_name,
                parent_folder_id=parent_folder_id
            )

            if existing_folder:
                folder_map[hierarchy_path] = existing_folder["_key"]
                self.logger.debug(f"✅ Folder exists: {folder_name} in parent {parent_folder_id or 'KB root'}")
            else:
                # Create new folder
                folder = await self.create_folder(
                    kb_id=kb_id,
                    org_id=org_id,
                    folder_name=folder_name,
                    parent_folder_id=parent_folder_id
                )
                folder_id = folder['id']
                if folder_id:
                    folder_map[hierarchy_path] = folder_id
                    self.logger.info(f"✅ Created folder: {folder_name} -> {folder_id} in parent {parent_folder_id or 'KB root'}")
                else:
                    raise Exception(f"Failed to create folder: {folder_name}")

        return folder_map

    async def _create_folder(
        self,
        kb_id: str,
        org_id: str,
        folder_path: str,
        folder_map: Dict[str, str],
        validation_result: Dict,
        transaction,
        timestamp: int
    ) -> str:
        """Unified folder creation logic"""
        folder_id = str(uuid.uuid4())
        folder_name = folder_path.split("/")[-1]

        # Determine parent folder ID
        parent_folder_id = None
        if "/" in folder_path:
            parent_path = "/".join(folder_path.split("/")[:-1])
            parent_folder_id = folder_map.get(parent_path)

        # If no parent in map, check if we're uploading to a specific folder
        if not parent_folder_id and validation_result["upload_target"] == "folder":
            # For first-level subfolders of the upload target
            upload_parent_path = validation_result["parent_path"]
            if folder_path.startswith(f"{upload_parent_path}/") and folder_path.count("/") == upload_parent_path.count("/") + 1:
                parent_folder_id = validation_result["parent_folder"]["_key"]

        # Create folder document
        folder_data = {
            "_key": folder_id,
            "orgId": org_id,
            "recordGroupId": kb_id,
            "name": folder_name,
            "isFile": False,
            "extension": None,
            "mimeType": "application/vnd.folder",
            "sizeInBytes": 0,
            "webUrl": f"/kb/{kb_id}/folder/{folder_id}"
        }

        # Create folder
        await self.batch_upsert_nodes([folder_data], CollectionNames.FILES.value, transaction)

        # Create relationships
        edges_to_create = []

        # KB relationship (always needed)
        edges_to_create.append({
            "_from": f"{CollectionNames.FILES.value}/{folder_id}",
            "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
            "entityType": Connectors.KNOWLEDGE_BASE.value,
            "createdAtTimestamp": timestamp,
            "updatedAtTimestamp": timestamp,
        })

        # Parent-child relationship (if has parent)
        if parent_folder_id:
            edges_to_create.append({
                "_from": f"{CollectionNames.FILES.value}/{parent_folder_id}",
                "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                "relationshipType": "PARENT_CHILD",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })
        else:
            # record relations edge between folder and kb
            edges_to_create.append({
                    "_from": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                    "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

        # Create edges
        for edge in edges_to_create:
            collection = (
                CollectionNames.BELONGS_TO.value
                if edge.get("entityType") == Connectors.KNOWLEDGE_BASE.value
                else CollectionNames.RECORD_RELATIONS.value
            )
            await self.batch_create_edges([edge], collection, transaction)

        return folder_id

    def _populate_file_destinations(self, folder_analysis: Dict, folder_map: Dict[str, str]) -> None:
        """
        Update file destinations with resolved folder IDs using hierarchy paths
        """
        for index, destination in folder_analysis["file_destinations"].items():
            if destination["type"] == "folder":
                hierarchy_path = destination["folder_hierarchy_path"]
                if hierarchy_path in folder_map:
                    destination["folder_id"] = folder_map[hierarchy_path]
                else:
                    self.logger.error(f"❌ Folder ID not found in map for hierarchy path: {hierarchy_path}")
                    # Don't set folder_id, which will cause the file to be added to failed_files

    # ========== UNIFIED RECORD CREATION ==========

    async def _create_records(
        self,
        kb_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        transaction,
        timestamp: int
    ) -> Dict:
        total_created = 0
        failed_files = []

        # Group files by destination
        root_files = []
        folder_files = {}  # folder_id -> files
        created_files_data = []
        for index, file_data in enumerate(files):
            destination = folder_analysis["file_destinations"][index]

            if destination["type"] == "root":
                # File goes to root (KB root or parent folder)
                parent_folder_id = folder_analysis.get("parent_folder_id")  # Use the validated parent folder ID
                root_files.append((file_data, parent_folder_id))
            else:
                # File goes to subfolder
                folder_id = destination["folder_id"]
                if folder_id:
                    if folder_id not in folder_files:
                        folder_files[folder_id] = []
                    folder_files[folder_id].append(file_data)
                else:
                    self.logger.error(f"❌ No folder ID found for file: {file_data['filePath']}")
                    failed_files.append(file_data["filePath"])

        # Create root files
        if root_files:
            try:
                # Separate by target (KB root vs parent folder)
                kb_root_files = [f for f, folder_id in root_files if folder_id is None]
                parent_folder_files = {}

                for file_data, folder_id in root_files:
                    if folder_id is not None:
                        if folder_id not in parent_folder_files:
                            parent_folder_files[folder_id] = []
                        parent_folder_files[folder_id].append(file_data)

                # Create KB root files
                if kb_root_files:
                    successful_files = await self._create_files_in_kb_root(
                        kb_id=kb_id,
                        files=kb_root_files,
                        transaction=transaction,
                        timestamp=timestamp
                    )
                    created_files_data.extend(successful_files)
                    total_created += len(successful_files)
                    self.logger.info(f"✅ Created {len(successful_files)} files in KB root")

                # Create parent folder files
                for folder_id, folder_file_list in parent_folder_files.items():
                    successful_files = await self._create_files_in_folder(
                        kb_id=kb_id,
                        folder_id=folder_id,
                        files=folder_file_list,
                        transaction=transaction,
                        timestamp=timestamp
                    )
                    created_files_data.extend(successful_files)
                    total_created += len(successful_files)
                    self.logger.info(f"✅ Created {len(successful_files)} files in parent folder")

            except Exception as e:
                self.logger.error(f"❌ Failed to create root files: {str(e)}")
                failed_files.extend([f[0]["filePath"] for f in root_files])

        # Create subfolder files
        for folder_id, folder_file_list in folder_files.items():
            try:
                successful_files = await self._create_files_in_folder(
                    kb_id=kb_id,
                    folder_id=folder_id,
                    files=folder_file_list,
                    transaction=transaction,
                    timestamp=timestamp
                )
                created_files_data.extend(successful_files)
                total_created += len(successful_files)
                self.logger.info(f"✅ Created {len(successful_files)} files in subfolder {folder_id}")

            except Exception as e:
                self.logger.error(f"❌ Failed to create files in subfolder {folder_id}: {str(e)}")
                failed_files.extend([f["filePath"] for f in folder_file_list])

        return {"total_created": total_created, "failed_files": failed_files,"created_files_data": created_files_data}

    # ========== SHARED RECORD CREATION HELPERS ==========

    async def _create_files_in_kb_root(self, kb_id: str, files: List[Dict], transaction, timestamp: int) -> int:
        """Create files directly in KB root"""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=None,  # No parent = KB root
            transaction=transaction,
            timestamp=timestamp
        )

    async def _create_files_in_folder(self, kb_id: str, folder_id: str, files: List[Dict], transaction, timestamp: int) -> int:
        """Create files in a specific folder"""
        return await self._create_files_batch(
            kb_id=kb_id,
            files=files,
            parent_folder_id=folder_id,
            transaction=transaction,
            timestamp=timestamp
        )

    async def _create_files_batch(
        self,
        kb_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str],
        transaction,
        timestamp: int
    ) -> List[Dict]:
        """
        Updated batch file creation with proper conflict handling
        Skips files with name conflicts instead of creating duplicates
        """
        if not files:
            return []

        valid_files = []
        skipped_files = []

        # Step 1: Filter out files with name conflicts
        for file_data in files:
            file_name = file_data["fileRecord"]["name"]

            # Check for name conflicts using the updated validation
            conflict_result = await self._check_name_conflict_in_parent(
                kb_id=kb_id,
                parent_folder_id=parent_folder_id,
                item_name=file_name,
                transaction=transaction
            )

            if conflict_result["has_conflict"]:
                conflicts = conflict_result["conflicts"]
                conflict_names = [c["name"] for c in conflicts]
                self.logger.warning(f"⚠️ Skipping file due to name conflict: '{file_name}' conflicts with {conflict_names}")
                skipped_files.append({
                    "file_name": file_name,
                    "reason": f"Name conflict with existing items: {conflict_names}",
                    "conflicts": conflicts
                })
            else:
                valid_files.append(file_data)

        # Log skipping summary
        if skipped_files:
            self.logger.info(f"📋 Skipped {len(skipped_files)} files due to name conflicts, processing {len(valid_files)} files")

        # If no valid files, return early
        if not valid_files:
            self.logger.warning("⚠️ No valid files to create after conflict filtering")
            return []

        # Step 2: Extract records and file records from valid files only
        records = [f["record"] for f in valid_files]
        file_records = [f["fileRecord"] for f in valid_files]

        # Step 3: Create records and file records
        await self.batch_upsert_nodes(records, CollectionNames.RECORDS.value, transaction)
        await self.batch_upsert_nodes(file_records, CollectionNames.FILES.value, transaction)

        # Step 4: Create relationships for valid files only
        edges_to_create = []

        for file_data in valid_files:
            record_id = file_data["record"]["_key"]
            file_id = file_data["fileRecord"]["_key"]

            # Parent -> Record relationship (if has parent)
            if parent_folder_id:
                edges_to_create.append({
                    "_from": f"files/{parent_folder_id}",
                    "_to": f"records/{record_id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })
            else:
                # Record -> KB relationship (KB root)
                edges_to_create.append({
                    "_from": f"recordGroups/{kb_id}",
                    "_to": f"records/{record_id}",
                    "relationshipType": "PARENT_CHILD",
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                })

            # Record -> File relationship
            edges_to_create.append({
                "_from": f"records/{record_id}",
                "_to": f"files/{file_id}",
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })

            # Record -> KB relationship (belongs to KB)
            edges_to_create.append({
                "_from": f"records/{record_id}",
                "_to": f"recordGroups/{kb_id}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": timestamp,
                "updatedAtTimestamp": timestamp,
            })

        # Step 5: Batch create edges by type
        parent_child_edges = [e for e in edges_to_create if e.get("relationshipType") == "PARENT_CHILD"]
        is_of_type_edges = [e for e in edges_to_create if e["_to"].startswith("files/") and not e.get("relationshipType")]
        belongs_to_kb_edges = [e for e in edges_to_create if e["_to"].startswith("recordGroups/")]

        if parent_child_edges:
            await self.batch_create_edges(parent_child_edges, CollectionNames.RECORD_RELATIONS.value, transaction)
        if is_of_type_edges:
            await self.batch_create_edges(is_of_type_edges, CollectionNames.IS_OF_TYPE.value, transaction)
        if belongs_to_kb_edges:
            await self.batch_create_edges(belongs_to_kb_edges, CollectionNames.BELONGS_TO.value, transaction)

        # Step 6: Store skipped files for reporting (optional)
        if hasattr(self, '_current_upload_skipped_files'):
            self._current_upload_skipped_files.extend(skipped_files)

        self.logger.info(f"✅ Successfully created {len(valid_files)} files, skipped {len(skipped_files)} due to conflicts")

        return valid_files

    # ========== HELPER METHODS ==========

    async def _validate_folder_creation(self, kb_id: str, user_id: str) -> Dict:
        """Shared validation logic for folder creation"""
        try:
            # Get user
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return {"valid": False, "success": False, "code": 404, "reason": f"User not found: {user_id}"}

            user_key = user.get('_key')

            # Check permissions
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return {
                    "valid": False,
                    "success": False,
                    "code": 403,
                    "reason": f"Insufficient permissions. Role: {user_role}"
                }

            return {
                "valid": True,
                "user": user,
                "user_key": user_key,
                "user_role": user_role
            }

        except Exception as e:
            return {"valid": False, "success": False, "code": 500, "reason": str(e)}

    def _generate_upload_message(self, result: Dict, upload_type: str) -> str:
        """Generate success message"""
        total_created = result["total_created"]
        folders_created = result["folders_created"]
        failed_files = len(result.get("failed_files", []))

        message = f"Successfully uploaded {total_created} file{'s' if total_created != 1 else ''} to {upload_type}"

        if folders_created > 0:
            message += f" with {folders_created} new subfolder{'s' if folders_created != 1 else ''} created"

        if failed_files > 0:
            message += f". {failed_files} file{'s' if failed_files != 1 else ''} failed to upload"

        return message + "."


    async def _execute_upload_transaction(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        folder_analysis: Dict,
        validation_result: Dict
    ) -> Dict:
        """Unified transaction execution for all upload scenarios"""
        try:
            # Start transaction
            transaction = self.db.begin_transaction(
                write=[
                    CollectionNames.FILES.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.BELONGS_TO.value,
                ]
            )

            try:
                timestamp = get_epoch_timestamp_in_ms()

                # Step 1: Ensure all needed folders exist
                folder_map = await self._ensure_folders_exist(
                    kb_id=kb_id,
                    org_id=org_id,
                    folder_analysis=folder_analysis,
                    validation_result=validation_result,
                    transaction=transaction,
                    timestamp=timestamp
                )

                # Step 2: Update file destinations with folder IDs
                self._populate_file_destinations(folder_analysis, folder_map)

                # Step 3: Create all records and relationships
                creation_result = await self._create_records(
                    kb_id=kb_id,
                    files=files,
                    folder_analysis=folder_analysis,
                    transaction=transaction,
                    timestamp=timestamp
                )

                if creation_result["total_created"] > 0 or len(folder_map) > 0:
                    # Step 4: Commit transaction BEFORE event publishing
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
                    self.logger.info("✅ Upload transaction committed successfully")
                    # Step 5: Publish events AFTER successful commit
                    try:
                        await self._publish_upload_events(kb_id, {
                            "created_files_data": creation_result["created_files_data"],
                            "total_created": creation_result["total_created"]
                        })
                        self.logger.info(f"✅ Published events for {creation_result['total_created']} records")
                    except Exception as event_error:
                        self.logger.error(f"❌ Event publishing failed (records still created): {str(event_error)}")
                        # Don't fail the main operation - records were successfully created

                    return {
                        "success": True,
                        "total_created": creation_result["total_created"],
                        "folders_created": len(folder_map),
                        "created_folders": [
                            {"id": folder_id}
                            for folder_id in folder_map.values()
                        ],
                        "failed_files": creation_result["failed_files"],
                        "created_files_data": creation_result["created_files_data"]
                    }
                else:
                    # Nothing was created - abort transaction
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Transaction aborted - no items to create")
                    return {
                        "success": True,
                        "total_created": 0,
                        "folders_created": 0,
                        "created_folders": [],
                        "failed_files": creation_result["failed_files"],
                        "created_files_data": []
                    }

            except Exception as e:
                if transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Failed to abort transaction: {str(abort_error)}")

                self.logger.error(f"❌ Upload transaction failed: {str(e)}")
                return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}


        except Exception as e:
            return {"success": False, "reason": f"Transaction failed: {str(e)}", "code": 500}



    async def _validate_upload_context(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        parent_folder_id: Optional[str] = None
    ) -> Dict:
        """Unified validation for all upload scenarios"""
        try:
            # Get user
            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                return self._validation_error(404, f"User not found: {user_id}")

            user_key = user.get('_key')

            # Check KB permissions
            user_role = await self.get_user_kb_permission(kb_id, user_key)
            if user_role not in ["OWNER", "WRITER"]:
                return self._validation_error(403, f"Insufficient permissions. Role: {user_role}")

            # Validate folder if specified
            parent_folder = None
            parent_path = "/"  # Default for KB root

            if parent_folder_id:
                # Validate folder exists and belongs to KB
                folder_valid = await self.validate_folder_exists_in_kb(kb_id, parent_folder_id)
                if not folder_valid:
                    return self._validation_error(404, f"Folder {parent_folder_id} not found in KB {kb_id}")

                # Get parent folder details
                parent_folder = await self.get_document(parent_folder_id, CollectionNames.FILES.value)
                if not parent_folder:
                    return self._validation_error(404, f"Parent folder {parent_folder_id} not found")

                parent_path = parent_folder.get("path", "/")

            return {
                "valid": True,
                "user": user,
                "user_key": user_key,
                "user_role": user_role,
                "parent_folder": parent_folder,
                "parent_path": parent_path,
                "upload_target": "folder" if parent_folder_id else "kb_root"
            }

        except Exception as e:
            return self._validation_error(500, f"Validation failed: {str(e)}")

    async def get_user_by_user_id(self, user_id: str) -> Optional[Dict]:
        """Get user by user ID"""
        try:
            query = f"""
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user.userId == @user_id
                    RETURN user
            """
            cursor = self.db.aql.execute(query, bind_vars={"user_id": user_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            self.logger.error(f"Error getting user by user ID: {str(e)}")
            return None

    async def get_document(self, document_key: str, collection: str) -> Optional[Dict]:
        """Get a document by its key"""
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc._key == @document_key
                RETURN doc
            """
            cursor = self.db.aql.execute(
                query,
                bind_vars={"document_key": document_key, "@collection": collection},
            )
            result = list(cursor)
            return result[0] if result else None
        except Exception as e:
            self.logger.error("❌ Error getting document: %s", str(e))
            return None

    async def batch_upsert_nodes(
        self,
        nodes: List[Dict],
        collection: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool | None:
        """Batch upsert multiple nodes using Python-Arango SDK methods"""
        try:
            self.logger.info("🚀 Batch upserting nodes: %s", collection)

            batch_query = """
            FOR node IN @nodes
                UPSERT { _key: node._key }
                INSERT node
                UPDATE node
                IN @@collection
                RETURN NEW
            """

            bind_vars = {"nodes": nodes, "@collection": collection}

            db = transaction if transaction else self.db

            cursor = db.aql.execute(batch_query, bind_vars=bind_vars)
            results = list(cursor)
            self.logger.info(
                "✅ Successfully upserted %d nodes in collection '%s'.",
                len(results),
                collection,
            )
            return True

        except Exception as e:
            self.logger.error("❌ Batch upsert failed: %s", str(e))
            if transaction:
                raise
            return False

    async def batch_create_edges(
        self,
        edges: List[Dict],
        collection: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool | None:
        """Batch create PARENT_CHILD relationships"""
        try:
            self.logger.info("🚀 Batch creating edges: %s", collection)

            batch_query = """
            FOR edge IN @edges
                UPSERT { _from: edge._from, _to: edge._to }
                INSERT edge
                UPDATE edge
                IN @@collection
                RETURN NEW
            """
            bind_vars = {"edges": edges, "@collection": collection}

            db = transaction if transaction else self.db

            cursor = db.aql.execute(batch_query, bind_vars=bind_vars)
            results = list(cursor)
            self.logger.info(
                "✅ Successfully created %d edges in collection '%s'.",
                len(results),
                collection,
            )
            return True
        except Exception as e:
            self.logger.error("❌ Batch edge creation failed: %s", str(e))
            return False

    async def create_knowledge_base(
        self,
        kb_data:Dict,
        permission_edge:Dict,
        transaction:Optional[TransactionDatabase]=None
    )-> Dict:
        """Create knowledge base with permissions"""
        try:
            kb_name = kb_data.get('groupName', 'Unknown')
            self.logger.info(f"🚀 Creating knowledge base: '{kb_name}' in ArangoDB")

            # KB record group creation
            await self.batch_upsert_nodes(
                [kb_data], CollectionNames.RECORD_GROUPS.value,transaction=transaction
            )

            # user KB permission edge
            await self.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSIONS_TO_KB.value,transaction=transaction
            )

            self.logger.info(f"✅ Knowledge base created successfully: {kb_data['_key']}")
            return {
                "id": kb_data["_key"],
                "name": kb_data["groupName"],
                "success": True
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to create knowledge base: {str(e)}")
            raise

    async def get_user_kb_permission(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """Validate user knowledge permission"""
        try:
            self.logger.info(f"🔍 Checking permissions for user {user_id} on KB {kb_id}")
            db = transaction if transaction else self.db

            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._from == CONCAT('users/', @user_id)
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                RETURN perm
            """

            cursor = db.aql.execute(
                query,
                bind_vars={
                    "kb_id": kb_id,
                    "user_id": user_id,
                    "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                },
            )

            permission = next(cursor, None)

            if permission:
                role = permission.get("role")
                self.logger.info(f"✅ Found permission: user {user_id} has role '{role}' on KB {kb_id}")
                return role
            else:
                self.logger.warning(f"⚠️ No permission found for user {user_id} on KB {kb_id}")

                # Debug: Let's see what permissions exist for this KB
                debug_query = """
                FOR perm IN @@permissions_collection
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    RETURN {
                        from: perm._from,
                        role: perm.role,
                        type: perm.type
                    }
                """
                debug_cursor = db.aql.execute(
                    debug_query,
                    bind_vars={
                        "kb_id": kb_id,
                        "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                    },
                )
                existing_perms = list(debug_cursor)
                self.logger.info(f"🔍 Debug - All permissions for KB {kb_id}: {existing_perms}")

                return None

        except Exception as e:
            self.logger.error(f"❌ Failed to validate knowledge base permission for user {user_id}: {str(e)}")
            raise

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[Dict]:
        """Get knowledge base with user permissions"""
        try:
            db = transaction if transaction else self.db

            #  Get the KB and folders
            query = """
            FOR kb IN @@recordGroups_collection
                FILTER kb._key == @kb_id
                // Get the user's role for this KB
                LET user_perm = FIRST(
                    FOR perm IN @@permissions_collection
                        FILTER perm._from == @user_from
                        FILTER perm._to == kb._id
                        RETURN perm
                )
                LET user_role = user_perm ? user_perm.role : null

                // Get folders
                LET folders = (
                    FOR edge IN @@kb_to_folder_edges
                        FILTER edge._to == kb._id
                        // Make sure the _from is actually a file document
                        FILTER STARTS_WITH(edge._from, 'files/')
                        LET folder = DOCUMENT(edge._from)
                        FILTER folder != null AND folder.isFile == false
                        RETURN {
                            id: folder._key,
                            name: folder.name,
                            createdAtTimestamp: edge.createdAtTimestamp,
                            updatedAtTimestamp: edge.updatedAtTimestamp,
                            path: folder.path,
                            webUrl: folder.webUrl,
                            mimeType: folder.mimeType,
                            sizeInBytes: folder.sizeInBytes
                        }
                )
                RETURN {
                    id: kb._key,
                    name: kb.groupName,
                    createdAtTimestamp: kb.createdAtTimestamp,
                    updatedAtTimestamp: kb.updatedAtTimestamp,
                    createdBy: kb.createdBy,
                    userRole: user_role,
                    folders: folders
                }
            """
            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "user_from": f"users/{user_id}",
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                "@kb_to_folder_edges": CollectionNames.BELONGS_TO.value,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            })
            result = next(cursor, None)
            if result:
                self.logger.info("✅ Knowledge base retrieved successfully")
                return result
            else:
                self.logger.warning("⚠️ Knowledge base not found")
                return None
        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base: {str(e)}")
            raise

    async def get_knowledge_base_by_id(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """Get knowledge base by ID"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR kb IN @@recordGroups_collection
                FILTER kb._key == @kb_id
                FILTER kb.groupType == @kb_type
                FILTER kb.connectorName == @kb_connector
                RETURN kb
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
            })

            return next(cursor, None)

        except Exception as e:
            self.logger.error(f"❌ Failed to get knowledge base by ID: {str(e)}")
            return None

    async def list_user_knowledge_bases(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[TransactionDatabase] = None,
    ) -> Tuple[List[Dict], int, Dict]:
        """List knowledge bases with pagination, search, and filtering"""
        try:
            db = transaction if transaction else self.db

            # Build filter conditions
            filter_conditions = []

            # Search filter
            if search:
                filter_conditions.append("LIKE(LOWER(kb.groupName), LOWER(@search_term))")

            # Permission filter
            if permissions:
                filter_conditions.append("perm.role IN @permissions")

            # Build WHERE clause
            additional_filters = ""
            if filter_conditions:
                additional_filters = "AND " + " AND ".join(filter_conditions)

            # Sort field mapping
            sort_field_map = {
                "name": "kb.groupName",
                "createdAtTimestamp": "kb.createdAtTimestamp",
                "updatedAtTimestamp": "kb.updatedAtTimestamp",
                "userRole": "perm.role"
            }
            sort_field = sort_field_map.get(sort_by, "kb.groupName")
            sort_direction = sort_order.upper()

            # Main query with pagination
            main_query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._from == @user_from
                LET kb = DOCUMENT(perm._to)
                FILTER kb != null
                FILTER kb.orgId == @org_id
                FILTER kb.groupType == @kb_type
                FILTER kb.connectorName == @kb_connector
                {additional_filters}
                // Get all the folders for this KB
                LET folders = (
                    FOR edge IN @@belongs_to_kb
                        FILTER edge._to == kb._id
                        LET folder = DOCUMENT(edge._from)
                        FILTER folder != null && folder.isFile == false
                        RETURN {{
                            id: folder._key,
                            name: folder.name,
                            createdAtTimestamp: edge.createdAtTimestamp,
                            path: folder.path,
                            webUrl: folder.webUrl
                        }}
                )
                SORT {sort_field} {sort_direction}
                LIMIT @skip, @limit
                RETURN {{
                    id: kb._key,
                    name: kb.groupName,
                    createdAtTimestamp: kb.createdAtTimestamp,
                    updatedAtTimestamp: kb.updatedAtTimestamp,
                    createdBy: kb.createdBy,
                    userRole: perm.role,
                    folders: folders
                }}
            """

            # Count query
            count_query = f"""
            FOR perm IN @@count_permissions_collection
                FILTER perm._from == @count_user_from
                LET kb = DOCUMENT(perm._to)
                FILTER kb != null
                FILTER kb.orgId == @count_org_id
                FILTER kb.groupType == @count_kb_type
                FILTER kb.connectorName == @count_kb_connector
                {additional_filters.replace('@search_term', '@count_search_term').replace('@permissions', '@count_permissions') if additional_filters else ''}
                COLLECT WITH COUNT INTO total
                RETURN total
            """

            # Available filters query
            filters_query = """
            FOR perm IN @@filters_permissions_collection
                FILTER perm._from == @filters_user_from
                LET kb = DOCUMENT(perm._to)
                FILTER kb != null
                FILTER kb.orgId == @filters_org_id
                FILTER kb.groupType == @filters_kb_type
                FILTER kb.connectorName == @filters_kb_connector
                RETURN {
                    permission: perm.role,
                    kb_name: kb.groupName
                }
            """

            # Bind variables for main query
            main_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "skip": skip,
                "limit": limit,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            }

            # Add search term if provided
            if search:
                main_bind_vars["search_term"] = f"%{search}%"

            # Add permissions filter if provided
            if permissions:
                main_bind_vars["permissions"] = permissions

            # Bind variables for count query
            count_bind_vars = {
                "count_user_from": f"users/{user_id}",
                "count_org_id": org_id,
                "count_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "count_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "@count_permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            # Add search term if provided for count query
            if search:
                count_bind_vars["count_search_term"] = f"%{search}%"

            # Add permissions filter if provided for count query
            if permissions:
                count_bind_vars["count_permissions"] = permissions

            # Bind variables for filters query
            filters_bind_vars = {
                "filters_user_from": f"users/{user_id}",
                "filters_org_id": org_id,
                "filters_kb_type": Connectors.KNOWLEDGE_BASE.value,
                "filters_kb_connector": Connectors.KNOWLEDGE_BASE.value,
                "@filters_permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            # Execute queries
            kbs_cursor = db.aql.execute(main_query, bind_vars=main_bind_vars)
            count_cursor = db.aql.execute(count_query, bind_vars=count_bind_vars)
            filters_cursor = db.aql.execute(filters_query, bind_vars=filters_bind_vars)

            # Get results
            kbs = list(kbs_cursor)
            total_count = next(count_cursor, 0)
            filter_data = list(filters_cursor)

            # Build available filters
            available_permissions = list(set(item["permission"] for item in filter_data))

            available_filters = {
                "permissions": available_permissions,
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
            }

            self.logger.info(f"✅ Found {len(kbs)} knowledge bases out of {total_count} total")
            return kbs, total_count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list knowledge bases with pagination: {str(e)}")
            return [], 0, {
                "permissions": [],
                "sortFields": ["name", "createdAtTimestamp", "updatedAtTimestamp", "userRole"],
                "sortOrders": ["asc", "desc"]
            }

    async def update_knowledge_base(
        self,
        kb_id: str,
        updates: Dict,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """Update knowledge base"""
        try:
            self.logger.info(f"🚀 Updating knowledge base {kb_id}")

            db = transaction if transaction else self.db

            query = """
            FOR kb IN @@kb_collection
                FILTER kb._key == @kb_id
                UPDATE kb WITH @updates IN @@kb_collection
                RETURN NEW
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "updates": updates,
                "@kb_collection": CollectionNames.RECORD_GROUPS.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.info("✅ Knowledge base updated successfully")
                return True
            else:
                self.logger.warning("⚠️ Knowledge base not found")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update knowledge base: {str(e)}")
            raise

    async def find_folder_by_name_in_parent(
        self,
        kb_id: str,
        folder_name: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Find a folder by name within a specific parent (KB root or folder)
        """
        try:
            db = transaction if transaction else self.db

            if parent_folder_id:
                # Look for folder in specific parent folder
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET folder = DOCUMENT(edge._to)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    FILTER folder.recordGroupId == @kb_id
                    FILTER LOWER(folder.name) == LOWER(@folder_name)
                    RETURN folder
                """

                cursor = db.aql.execute(query, bind_vars={
                    "parent_from": f"files/{parent_folder_id}",
                    "folder_name": folder_name,
                    "kb_id": kb_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })
            else:
                # Look for folder in KB root
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @kb_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET folder = DOCUMENT(edge._to)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    FILTER folder.recordGroupId == @kb_id
                    FILTER LOWER(folder.name) == LOWER(@folder_name)
                    RETURN folder
                """

                cursor = db.aql.execute(query, bind_vars={
                    "kb_from": f"recordGroups/{kb_id}",
                    "folder_name": folder_name,
                    "kb_id": kb_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })

            result = next(cursor, None)

            if result:
                self.logger.debug(f"✅ Found folder '{folder_name}' in parent")
            else:
                self.logger.debug(f"❌ Folder '{folder_name}' not found in parent")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to find folder by name: {str(e)}")
            return None

    async def navigate_to_folder_by_path(
        self,
        kb_id: str,
        folder_path: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Navigate to a folder using folder names in the path
        Example: "/folder1/subfolder2" -> Find folder1 in KB root, then subfolder2 in folder1
        """
        try:
            if not folder_path or folder_path == "/":
                return None

            # Remove leading slash and split path
            path_parts = folder_path.strip("/").split("/")
            current_parent_id = None  # Start from KB root

            for folder_name in path_parts:
                if not folder_name:  # Skip empty parts
                    continue

                folder = await self.find_folder_by_name_in_parent(
                    kb_id=kb_id,
                    folder_name=folder_name,
                    parent_folder_id=current_parent_id,
                    transaction=transaction
                )

                if not folder:
                    self.logger.debug(f"❌ Folder '{folder_name}' not found in path '{folder_path}'")
                    return None

                current_parent_id = folder["_key"]

            # Return the final folder if we successfully navigated the entire path
            return await self.get_file_record_by_id(current_parent_id, transaction)

        except Exception as e:
            self.logger.error(f"❌ Failed to navigate to folder by path: {str(e)}")
            return None

    async def create_folder(
        self,
        kb_id: str,
        folder_name: str,
        org_id: str,
        parent_folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Create folder using name-based validation
        """
        try:
            folder_id = str(uuid.uuid4())
            timestamp = get_epoch_timestamp_in_ms()

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                transaction = self.db.begin_transaction(
                    write=[
                        CollectionNames.FILES.value,
                        CollectionNames.BELONGS_TO.value,
                        CollectionNames.RECORD_RELATIONS.value,
                    ]
                )

            location = "KB root" if parent_folder_id is None else f"folder {parent_folder_id}"
            self.logger.info(f"🚀 Creating folder '{folder_name}' in {location}")

            try:
                # Step 1: Validate parent folder exists (if nested)
                if parent_folder_id:
                    parent_folder = await self.get_file_record_by_id(parent_folder_id, transaction)
                    if not parent_folder:
                        raise ValueError(f"Parent folder {parent_folder_id} not found")
                    if parent_folder.get("isFile") is not False:
                        raise ValueError(f"Parent {parent_folder_id} is not a folder")
                    if parent_folder.get("recordGroupId") != kb_id:
                        raise ValueError(f"Parent folder does not belong to KB {kb_id}")

                    self.logger.info(f"✅ Validated parent folder: {parent_folder.get('name')}")

                # Step 2: Check for name conflicts in the target location
                existing_folder = await self.find_folder_by_name_in_parent(
                    kb_id=kb_id,
                    folder_name=folder_name,
                    parent_folder_id=parent_folder_id,
                    transaction=transaction
                )

                if existing_folder:
                    self.logger.warning(f"⚠️ Name conflict: '{folder_name}' already exists in {location}")
                    return {
                        "folderId": existing_folder["_key"],
                        "name": existing_folder["name"],
                        "webUrl": existing_folder.get("webUrl", ""),
                        "parent_folder_id": parent_folder_id,
                        "exists": True,
                        "success": True
                    }

                # Step 3: Create folder document (without path)
                folder_data = {
                    "_key": folder_id,
                    "orgId": org_id,
                    "recordGroupId": kb_id,
                    "name": folder_name,
                    "isFile": False,
                    "extension": None,
                    "mimeType": "application/vnd.folder",
                    "sizeInBytes": 0,
                    "webUrl": f"/kb/{kb_id}/folder/{folder_id}"
                }

                # Step 4: Create folder in database
                await self.batch_upsert_nodes([folder_data], CollectionNames.FILES.value, transaction)

                # Step 5: Create relationships
                edges_to_create = []

                # Always create KB relationship (for easy querying)
                kb_relationship_edge = {
                    "_from": f"{CollectionNames.FILES.value}/{folder_id}",
                    "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                    "entityType": Connectors.KNOWLEDGE_BASE.value,
                    "createdAtTimestamp": timestamp,
                    "updatedAtTimestamp": timestamp,
                }
                edges_to_create.append((kb_relationship_edge, CollectionNames.BELONGS_TO.value))

                # Create parent-child relationship
                if parent_folder_id:
                    # Nested folder: Parent Folder -> Child Folder
                    parent_child_edge = {
                        "_from": f"{CollectionNames.FILES.value}/{parent_folder_id}",
                        "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                        "relationshipType": "PARENT_CHILD",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    edges_to_create.append((parent_child_edge, CollectionNames.RECORD_RELATIONS.value))
                else:
                    # Root folder: KB -> Folder
                    kb_parent_edge = {
                        "_from": f"{CollectionNames.RECORD_GROUPS.value}/{kb_id}",
                        "_to": f"{CollectionNames.FILES.value}/{folder_id}",
                        "relationshipType": "PARENT_CHILD",
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                    }
                    edges_to_create.append((kb_parent_edge, CollectionNames.RECORD_RELATIONS.value))

                # Step 6: Create all edges
                for edge_data, collection in edges_to_create:
                    await self.batch_create_edges([edge_data], collection, transaction)

                # Step 7: Commit transaction
                if should_commit:
                    await asyncio.to_thread(lambda: transaction.commit_transaction())

                self.logger.info(f"✅ Folder '{folder_name}' created successfully")
                return {
                    "id": folder_id,
                    "name": folder_name,
                    "webUrl": folder_data["webUrl"],
                    "exists": False,
                    "success": True
                }

            except Exception as inner_error:
                if should_commit:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted after error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise inner_error

        except Exception as e:
            self.logger.error(f"❌ Failed to create folder '{folder_name}': {str(e)}")
            raise

    async def update_folder(
        self,
        folder_id:str,
        updates:Dict,
        transaction: Optional[TransactionDatabase]= None
    )-> bool:
        """ Update folder """
        try:
            self.logger.info(f"🚀 Updating folder {folder_id}")

            db = transaction if transaction else self.db

            query = """
            FOR folder IN @@folder_collection
                FILTER folder._key == @folder_id
                UPDATE folder WITH @updates IN @@folder_collection
                RETURN NEW
            """

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "updates": updates,
                "@folder_collection": CollectionNames.FILES.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.info("✅ Folder updated successfully")
                return True
            else:
                self.logger.warning("⚠️ Folder not found")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to update Folder : {str(e)}")
            raise

    async def get_record_by_id(
        self,
        record_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """Get a record by ID with permission validation"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR record IN @@records_collection
                FILTER record._key == @record_id
                FILTER record.isDeleted != true
                // Get associated file record
                LET fileEdge = FIRST(
                    FOR isEdge IN @@is_of_type
                        FILTER isEdge._from == record._id
                        RETURN isEdge
                )
                LET fileRecord = fileEdge ? DOCUMENT(fileEdge._to) : null
                RETURN {
                    _key: record._key,
                    _id: record._id,
                    orgId: record.orgId,
                    recordName: record.recordName,
                    externalRecordId: record.externalRecordId,
                    recordType: record.recordType,
                    origin: record.origin,
                    version: record.version,
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    isDeleted: record.isDeleted,
                    isLatestVersion: record.isLatestVersion,
                    webUrl: record.webUrl,
                    fileRecord: fileRecord ? {
                        _key: fileRecord._key,
                        name: fileRecord.name,
                        extension: fileRecord.extension,
                        mimeType: fileRecord.mimeType,
                        sizeInBytes: fileRecord.sizeInBytes,
                        webUrl: fileRecord.webUrl
                    } : null
                }
            """

            cursor = db.aql.execute(query, bind_vars={
                "record_id": record_id,
                "@records_collection": CollectionNames.RECORDS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
            })

            return next(cursor, None)

        except Exception as e:
            self.logger.error(f"❌ Failed to get record by ID: {str(e)}")
            return None

    async def validate_folder_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Validate that a folder exists, is valid, and belongs to a specific knowledge base.
        """
        try:
            db = transaction if transaction else self.db

            query = """
            LET folder = DOCUMENT(@@files_collection, @folder_id)
            LET folder_valid = folder != null AND folder.isFile == false
            LET relationship = folder_valid ? FIRST(
                FOR edge IN @@belongs_to_collection
                    FILTER edge._from == @folder_from
                    FILTER edge._to == @kb_to
                    FILTER edge.entityType == @entity_type
                    RETURN 1
            ) : null
            RETURN folder_valid AND relationship != null
            """

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "folder_from": f"files/{folder_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "entity_type": Connectors.KNOWLEDGE_BASE.value,
                "@files_collection": CollectionNames.FILES.value,
                "@belongs_to_collection": CollectionNames.BELONGS_TO.value,
            })

            result = next(cursor, False)

            if not result:
                self.logger.warning(f"⚠️ Folder {folder_id} validation failed for KB {kb_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder in KB: {str(e)}")
            return False

    async def validate_record_in_folder(self, folder_id: str, record_id: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Check if a record is a child of a folder via PARENT_CHILD edge"""
        try:
            db = transaction if transaction else self.db
            query = """
            FOR edge IN @@record_relations
                FILTER edge._from == @folder_from
                FILTER edge._to == @record_to
                FILTER edge.relationshipType == "PARENT_CHILD"
                RETURN edge
            """
            cursor = db.aql.execute(query, bind_vars={
                "folder_from": f"files/{folder_id}",
                "record_to": f"records/{record_id}",
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            })
            result = next(cursor, None)
            return result is not None
        except Exception as e:
            self.logger.error(f"❌ Failed to validate record {record_id} in folder {folder_id}: {str(e)}")
            return False

    async def validate_record_in_kb(self, kb_id: str, record_id: str, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Check if a record belongs to a KB via BELONGS_TO_KB edge"""
        try:
            db = transaction if transaction else self.db
            query = """
            FOR edge IN @@belongs_to_kb
                FILTER edge._from == @record_from
                FILTER edge._to == @kb_to
                RETURN edge
            """
            cursor = db.aql.execute(query, bind_vars={
                "record_from": f"records/{record_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            })
            result = next(cursor, None)
            return result is not None
        except Exception as e:
            self.logger.error(f"❌ Failed to validate record {record_id} in KB {kb_id}: {str(e)}")
            return False

    async def get_file_record_by_id(self, file_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[Dict]:
        try:
            db = transaction if transaction else self.db
            query = """
            FOR file IN @@files
                FILTER file._key == @file_id
                RETURN file
            """
            cursor = db.aql.execute(query, bind_vars={
                "file_id": file_id,
                "@files": CollectionNames.FILES.value,
            })
            return next(cursor, None)
        except Exception as e:
            self.logger.error(f"❌ Failed to fetch file record {file_id}: {str(e)}")
            return None

    async def update_record(
        self,
        record_id: str,
        user_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Update a record by ID with automatic KB and permission detection
        This method automatically:
        1. Validates user exists
        2. Gets the record and finds its KB via belongs_to_kb edge
        3. Checks user permissions on that KB
        4. Determines if record is in a folder or KB root
        5. Updates the record directly (no redundant validation)
        Args:
            record_id: The record to update
            user_id: External user ID doing the update
            updates: Dictionary of fields to update
            file_metadata: Optional file metadata for file uploads
            transaction: Optional existing transaction
        """
        try:
            self.logger.info(f"🚀 Updating record {record_id} by user {user_id}")
            self.logger.info(f"Record updates: {updates}")
            self.logger.info(f"File metadata: {file_metadata}")

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                        ]
                    )
                    self.logger.info("🔄 Transaction created for record update")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return {
                        "success": False,
                        "code": 500,
                        "reason": f"Transaction creation failed: {str(tx_error)}"
                    }

            try:
                # Step 1: Get user by external user ID
                user = await self.get_user_by_user_id(user_id=user_id)
                if not user:
                    self.logger.error(f"❌ User not found: {user_id}")
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return {
                        "success": False,
                        "code": 404,
                        "reason": f"User not found: {user_id}"
                    }

                user_key = user.get('_key')
                self.logger.info(f"✅ Found user: {user_key}")

                # Step 2: Get record, KB context, permissions, and file record in one query
                context_query = """
                LET record = DOCUMENT("records", @record_id)
                FILTER record != null
                FILTER record.isDeleted != true
                // Find KB via belongs_to_kb edge
                LET kb_edge = FIRST(
                    FOR edge IN @@belongs_to_kb
                        FILTER edge._from == record._id
                        FILTER STARTS_WITH(edge._to, 'recordGroups/')
                        RETURN edge
                )
                LET kb = kb_edge ? DOCUMENT(kb_edge._to) : null
                // Check if record is in a folder via PARENT_CHILD relationship
                LET folder_edge = FIRST(
                    FOR fld_edge IN @@record_relations
                        FILTER fld_edge._to == record._id
                        FILTER fld_edge.relationshipType == "PARENT_CHILD"
                        FILTER STARTS_WITH(fld_edge._from, 'files/')
                        RETURN fld_edge
                )
                LET parent_folder = folder_edge ? DOCUMENT(folder_edge._from) : null
                // Check user permissions on the KB
                LET user_permission = kb ? FIRST(
                    FOR perm IN @@permissions_to_kb
                        FILTER perm._from == CONCAT('users/', @user_key)
                        FILTER perm._to == kb._id
                        FILTER perm.type == "USER"
                        RETURN perm.role
                ) : null
                // Get associated file record
                LET file_record = FIRST(
                    FOR isEdge IN @@is_of_type
                        FILTER isEdge._from == record._id
                        LET fileRecord = DOCUMENT(isEdge._to)
                        FILTER fileRecord != null
                        RETURN fileRecord
                )
                RETURN {
                    record_exists: record != null,
                    record: record,
                    kb_exists: kb != null,
                    kb: kb,
                    user_permission: user_permission,
                    has_permission: user_permission != null AND user_permission IN ["OWNER", "WRITER"],
                    parent_folder: parent_folder,
                    folder_id: parent_folder ? parent_folder._key : null,
                    file_record: file_record,
                    validation_passed: record != null AND kb != null AND user_permission != null AND user_permission IN ["OWNER", "WRITER"]
                }
                """

                cursor = transaction.aql.execute(context_query, bind_vars={
                    "record_id": record_id,
                    "user_key": user_key,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                context = next(cursor, {})

                # Step 3: Validate context and permissions
                if not context.get("validation_passed"):
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())

                    if not context.get("record_exists"):
                        error_reason = f"Record {record_id} not found or deleted"
                        error_code = 404
                    elif not context.get("kb_exists"):
                        error_reason = f"Knowledge base not found for record {record_id}"
                        error_code = 500
                    elif not context.get("has_permission"):
                        user_perm = context.get("user_permission")
                        if user_perm is None:
                            error_reason = f"User {user_id} has no access to the knowledge base containing record {record_id}"
                            error_code = 403
                        else:
                            error_reason = f"User {user_id} has insufficient permissions ({user_perm}) to update record {record_id}. Required: OWNER or WRITER"
                            error_code = 403
                    else:
                        error_reason = "Unknown validation error"
                        error_code = 500

                    self.logger.error(f"❌ Validation failed: {error_reason}")
                    return {
                        "success": False,
                        "code": error_code,
                        "reason": error_reason
                    }

                # Step 4: Extract validated context
                current_record = context["record"]
                current_file_record = context.get("file_record")
                kb = context["kb"]
                kb_id = kb["_key"]
                folder_id = context.get("folder_id")
                user_permission = context["user_permission"]

                self.logger.info("✅ Context validated:")
                self.logger.info(f"   📚 KB: {kb_id} ({kb.get('groupName', 'Unknown')})")
                self.logger.info(f"   📁 Folder: {folder_id or 'KB Root'}")
                self.logger.info(f"   🔐 Permission: {user_permission}")

                # Step 5: Prepare update data (no redundant validation needed)
                timestamp = get_epoch_timestamp_in_ms()
                version = (current_record.get("version", 0)) + 1
                processed_updates = {
                    **updates,
                    "version": version,
                    "updatedAtTimestamp": timestamp
                }

                # Handle file upload case
                if file_metadata:
                    sourceLastModifiedTimestamp = file_metadata.get("lastModified", timestamp)
                    processed_updates.update({
                        "sourceCreatedAtTimestamp": sourceLastModifiedTimestamp,
                        "sourceLastModifiedTimestamp": sourceLastModifiedTimestamp,
                    })

                    # Set record name from file if provided
                    original_name = file_metadata.get("originalname", "")
                    if original_name and "." in original_name:
                        last_dot = original_name.rfind(".")
                        if last_dot > 0:
                            processed_updates["recordName"] = original_name[:last_dot]

                # Step 6: Update the record directly
                record_update_query = """
                FOR record IN @@records_collection
                    FILTER record._key == @record_id
                    UPDATE record WITH @updates IN @@records_collection
                    RETURN NEW
                """

                cursor = transaction.aql.execute(record_update_query, bind_vars={
                    "record_id": record_id,
                    "updates": processed_updates,
                    "@records_collection": CollectionNames.RECORDS.value,
                })

                updated_record = next(cursor, None)

                if not updated_record:
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return {
                        "success": False,
                        "code": 500,
                        "reason": f"Failed to update record {record_id}"
                    }

                # Step 7: Update file record if file metadata provided
                file_updated = False
                updated_file = current_file_record

                if file_metadata and current_file_record:
                    file_updates = {}

                    if "originalname" in file_metadata:
                        file_updates["name"] = file_metadata["originalname"]

                    if "size" in file_metadata:
                        file_updates["sizeInBytes"] = file_metadata["size"]

                    if file_updates:
                        file_update_query = """
                        FOR file IN @@files_collection
                            FILTER file._key == @file_key
                            UPDATE file WITH @file_updates IN @@files_collection
                            RETURN NEW
                        """

                        try:
                            file_cursor = transaction.aql.execute(file_update_query, bind_vars={
                                "file_key": current_file_record["_key"],
                                "file_updates": file_updates,
                                "@files_collection": CollectionNames.FILES.value,
                            })

                            updated_file = next(file_cursor, None)
                            if updated_file:
                                file_updated = True
                                self.logger.info(f"✅ File metadata updated for record {record_id}")

                        except Exception as file_error:
                            self.logger.error(f"❌ Failed to update file metadata: {str(file_error)}")
                            # Continue without failing the entire operation

                # Step 8: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing record update transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return {
                            "success": False,
                            "code": 500,
                            "reason": f"Transaction commit failed: {str(commit_error)}"
                        }

                # Step 9: Publish update event (after successful commit)
                try:
                    update_payload = await self._create_update_record_event_payload(
                        updated_record, updated_file
                    )
                    if update_payload:
                        await self._publish_record_event("updateRecord", update_payload)
                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish update event: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"✅ Record {record_id} updated successfully with auto-detected context")

                return {
                    "success": True,
                    "updatedRecord": updated_record,
                    "updatedFile": updated_file,
                    "fileUpdated": file_updated,
                    "recordId": record_id,
                    "timestamp": timestamp,
                    "location": "folder" if folder_id else "kb_root",
                    "folderId": folder_id,
                    "kb": kb,
                    "userPermission": user_permission,
                }

            except Exception as db_error:
                self.logger.error(f"❌ Database error during record update: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                return {
                    "success": False,
                    "code": 500,
                    "reason": f"Database error: {str(db_error)}"
                }

        except Exception as e:
            self.logger.error(f"❌ Failed to update record {record_id}: {str(e)}")
            return {
                "success": False,
                "code": 500,
                "reason": f"Service error: {str(e)}"
            }

    async def delete_records(
        self,
        record_ids: List[str],
        kb_id: str,
        folder_id: Optional[str] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Delete multiple records and publish delete events for each
        Returns details about successfully deleted records for event publishing
        """
        try:
            if not record_ids:
                return {
                    "success": True,
                    "deleted_records": [],
                    "failed_records": [],
                    "total_requested": 0,
                    "successfully_deleted": 0,
                    "failed_count": 0
                }

            self.logger.info(f"🚀 Bulk deleting {len(record_ids)} records from {'folder ' + folder_id if folder_id else 'KB root'}")

            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORDS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.BELONGS_TO.value,
                        ]
                    )
                    self.logger.info("🔄 Transaction created for bulk record deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return {
                        "success": False,
                        "reason": f"Transaction creation failed: {str(tx_error)}"
                    }

            try:
                # Step 1: Get complete record details for validation and event publishing
                self.logger.info("🔍 Step 1: Getting record details for event publishing...")

                validation_query = """
                LET records_with_details = (
                    FOR rid IN @record_ids
                        LET record = DOCUMENT("records", rid)
                        LET record_exists = record != null
                        LET record_not_deleted = record_exists ? record.isDeleted != true : false
                        // Check KB relationship
                        LET kb_relationship = record_exists ? FIRST(
                            FOR edge IN @@belongs_to_kb
                                FILTER edge._from == CONCAT('records/', rid)
                                FILTER edge._to == CONCAT('recordGroups/', @kb_id)
                                RETURN edge
                        ) : null
                        // Check folder relationship if folder_id provided
                        LET folder_relationship = @folder_id ? (
                            record_exists ? FIRST(
                                FOR edge_rel IN @@record_relations
                                    FILTER edge_rel._to == CONCAT('records/', rid)
                                    FILTER edge_rel._from == CONCAT('files/', @folder_id)
                                    FILTER edge_rel.relationshipType == "PARENT_CHILD"
                                    RETURN edge_rel
                            ) : null
                        ) : true
                        // Get associated file record
                        LET file_record = record_exists ? FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == CONCAT('records/', rid)
                                LET fileRec = DOCUMENT(isEdge._to)
                                FILTER fileRec != null
                                RETURN fileRec
                        ) : null
                        LET is_valid = record_exists AND record_not_deleted AND kb_relationship != null AND folder_relationship != null
                        RETURN {
                            record_id: rid,
                            record: record,
                            file_record: file_record,
                            is_valid: is_valid,
                            validation_errors: is_valid ? [] : [
                                !record_exists ? "Record not found" : null,
                                record_exists AND !record_not_deleted ? "Record already deleted" : null,
                                kb_relationship == null ? "Not in specified KB" : null,
                                folder_relationship == null ? "Not in specified folder" : null
                            ]
                        }
                )
                LET valid_records = records_with_details[* FILTER CURRENT.is_valid]
                LET invalid_records = records_with_details[* FILTER !CURRENT.is_valid]
                RETURN {
                    valid_records: valid_records,
                    invalid_records: invalid_records,
                    total_valid: LENGTH(valid_records),
                    total_invalid: LENGTH(invalid_records)
                }
                """

                cursor = transaction.aql.execute(validation_query, bind_vars={
                    "record_ids": record_ids,
                    "kb_id": kb_id,
                    "folder_id": folder_id,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                validation_result = next(cursor, {})

                valid_records = validation_result.get("valid_records", [])
                invalid_records = validation_result.get("invalid_records", [])

                self.logger.info(f"📋 Validation complete: {len(valid_records)} valid, {len(invalid_records)} invalid")

                deleted_records = []
                failed_records = []

                # Add invalid records to failed list
                for invalid_record in invalid_records:
                    errors = [err for err in invalid_record["validation_errors"] if err]
                    failed_records.append({
                        "record_id": invalid_record["record_id"],
                        "reason": ", ".join(errors) if errors else "Record validation failed"
                    })

                # If no valid records found, return early
                if not valid_records:
                    if should_commit:
                        try:
                            await asyncio.to_thread(lambda: transaction.commit_transaction())
                            self.logger.info("✅ Transaction committed (no records to delete)")
                        except Exception as commit_error:
                            self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                            try:
                                await asyncio.to_thread(lambda: transaction.abort_transaction())
                            except Exception as abort_error:
                                self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")

                    self.logger.info("✅ No valid records found to delete")
                    return {
                        "success": True,
                        "deleted_records": [],
                        "failed_records": failed_records,
                        "total_requested": len(record_ids),
                        "successfully_deleted": 0,
                        "failed_count": len(failed_records),
                        "files_deleted": 0,
                        "location": "folder" if folder_id else "kb_root",
                        "folder_id": folder_id,
                        "kb_id": kb_id,
                        "message": "Records already deleted or not found"
                    }

                # Store records for event publishing before deletion
                records_for_events = []
                for valid_record in valid_records:
                    records_for_events.append({
                        "record": valid_record["record"],
                        "file_record": valid_record["file_record"]
                    })

                # Step 2: Delete edges, file records, and records
                self.logger.info("🗑️ Step 2: Deleting records and associated data...")

                valid_record_ids = [r["record_id"] for r in valid_records]
                file_record_ids = [r["file_record"]["_key"] for r in valid_records if r["file_record"]]

                # Delete edges
                if valid_record_ids:
                    # Delete all edges related to these records
                    edges_cleanup_query = """
                    FOR record_id IN @record_ids
                        // Delete record relations edges
                        FOR rec_rel_edge IN @@record_relations
                            FILTER rec_rel_edge._from == CONCAT('records/', record_id) OR rec_rel_edge._to == CONCAT('records/', record_id)
                            REMOVE rec_rel_edge IN @@record_relations
                        // Delete is_of_type edges
                        FOR iot_edge IN @@is_of_type
                            FILTER iot_edge._from == CONCAT('records/', record_id)
                            REMOVE iot_edge IN @@is_of_type
                        // Delete belongs_to_kb edges
                        FOR btk_edge IN @@belongs_to_kb
                            FILTER btk_edge._from == CONCAT('records/', record_id)
                            REMOVE btk_edge IN @@belongs_to_kb
                    """
                    transaction.aql.execute(edges_cleanup_query, bind_vars={
                        "record_ids": valid_record_ids,
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    })
                    self.logger.info(f"✅ Deleted edges for {len(valid_record_ids)} records")

                # Delete file records
                if file_record_ids:
                    file_records_delete_query = """
                    FOR file_key IN @file_keys
                        REMOVE file_key IN @@files_collection
                    """
                    transaction.aql.execute(file_records_delete_query, bind_vars={
                        "file_keys": file_record_ids,
                        "@files_collection": CollectionNames.FILES.value,
                    })
                    self.logger.info(f"✅ Deleted {len(file_record_ids)} file records")

                # Delete records and track successful deletions
                if valid_record_ids:
                    records_delete_query = """
                    FOR record_key IN @record_keys
                        LET record_doc = DOCUMENT(@@records_collection, record_key)
                        FILTER record_doc != null
                        REMOVE record_doc IN @@records_collection
                        RETURN OLD
                    """

                    cursor = transaction.aql.execute(records_delete_query, bind_vars={
                        "record_keys": valid_record_ids,
                        "@records_collection": CollectionNames.RECORDS.value,
                    })

                    actually_deleted = list(cursor)
                    deleted_records = [
                        {"record_id": record["_key"], "name": record.get("recordName", "Unknown")}
                        for record in actually_deleted
                    ]

                    self.logger.info(f"✅ Deleted {len(deleted_records)} records")

                # Step 3: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing bulk deletion transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return {
                            "success": False,
                            "reason": f"Transaction commit failed: {str(commit_error)}"
                        }

                # Step 4: Publish delete events for successfully deleted records
                try:
                    delete_event_tasks = []
                    for record_data in records_for_events:
                        if any(d["record_id"] == record_data["record"]["_key"] for d in deleted_records):
                            # Only publish events for actually deleted records
                            delete_payload = await self._create_deleted_record_event_payload(
                                record_data["record"], record_data["file_record"]
                            )
                            if delete_payload:
                                delete_event_tasks.append(
                                    self._publish_record_event("deleteRecord", delete_payload)
                                )

                    if delete_event_tasks:
                        await asyncio.gather(*delete_event_tasks, return_exceptions=True)
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish delete events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                success_count = len(deleted_records)
                failed_count = len(failed_records)

                self.logger.info(f"🎉 Bulk deletion completed: {success_count} deleted, {failed_count} failed")

                return {
                    "success": True,
                    "deleted_records": deleted_records,
                    "failed_records": failed_records,
                    "total_requested": len(record_ids),
                    "successfully_deleted": success_count,
                    "failed_count": failed_count,
                    "files_deleted": len(file_record_ids),
                    "location": "folder" if folder_id else "kb_root",
                    "folder_id": folder_id,
                    "kb_id": kb_id
                }

            except Exception as db_error:
                self.logger.error(f"❌ Database error during bulk deletion: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed bulk record deletion: {str(e)}")
            return {
                "success": False,
                "reason": f"Service error: {str(e)}"
            }

    async def validate_users_exist(
        self,
        user_ids: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> List[str]:
        """Validate which users exist in the database"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR user_id IN @user_ids
                LET user = FIRST(
                    FOR user IN @@users_collection
                        FILTER user._key == user_id
                        RETURN user._key
                )
                FILTER user != null
                RETURN user
            """

            cursor = db.aql.execute(query, bind_vars={
                "user_ids": user_ids,
                "@users_collection": CollectionNames.USERS.value,
            })

            return list(cursor)

        except Exception as e:
            self.logger.error(f"❌ Failed to validate users exist: {str(e)}")
            return []

    async def get_existing_kb_permissions(
        self,
        kb_id: str,
        user_ids: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict[str, str]:
        """Get existing permissions for specified users on a KB"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR user_id IN @user_ids
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', user_id)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    RETURN {
                        user_id: user_id,
                        role: perm.role
                    }
            """

            cursor = db.aql.execute(query, bind_vars={
                "user_ids": user_ids,
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            })

            result = {}
            for perm in cursor:
                result[perm["user_id"]] = perm["role"]

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get existing KB permissions: {str(e)}")
            return {}

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str
    ) -> Dict:
        """Create kb permissions for users and teams - Optimized version"""
        try:
            timestamp = get_epoch_timestamp_in_ms()

            # Single comprehensive query to validate, check existing permissions, and prepare data
            main_query = """
            // Get requester info and validate ownership in one go
            LET requester_info = FIRST(
                FOR user IN @@users_collection
                FILTER user.userId == @requester_id
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', user._key)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER perm.type == "USER"
                    FILTER perm.role == "OWNER"
                RETURN {user_key: user._key, is_owner: true}
            )

            // Quick KB existence check
            LET kb_exists = LENGTH(FOR kb IN @@recordGroups_collection FILTER kb._key == @kb_id LIMIT 1 RETURN 1) > 0

            // Process all users and their current permissions in one pass
            LET user_operations = (
                FOR user_id IN @user_ids
                    LET user = FIRST(FOR u IN @@users_collection FILTER u._key == user_id RETURN u)
                    LET current_perm = user ? FIRST(
                        FOR perm IN @@permissions_collection
                        FILTER perm._from == CONCAT('users/', user._key)
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER perm.type == "USER"
                        RETURN perm
                    ) : null

                    FILTER user != null  // Skip non-existent users

                    LET operation = current_perm == null ? "insert" :
                                (current_perm.role != @role ? "update" : "skip")

                    RETURN {
                        user_id: user_id,
                        user_key: user._key,
                        userId: user.userId,
                        name: user.fullName,
                        operation: operation,
                        current_role: current_perm ? current_perm.role : null,
                        perm_key: current_perm ? current_perm._key : null
                    }
            )

            // Process all teams and their current permissions in one pass
            LET team_operations = (
                FOR team_id IN @team_ids
                    LET team = FIRST(FOR t IN @@teams_collection FILTER t._key == team_id RETURN t)
                    LET current_perm = team ? FIRST(
                        FOR perm IN @@permissions_collection
                        FILTER perm._from == CONCAT('teams/', team._key)
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER perm.type == "TEAM"
                        RETURN perm
                    ) : null

                    FILTER team != null  // Skip non-existent teams

                    LET operation = current_perm == null ? "insert" :
                                (current_perm.role != @role ? "update" : "skip")

                    RETURN {
                        team_id: team_id,
                        team_key: team._key,
                        name: team.name,
                        operation: operation,
                        current_role: current_perm ? current_perm.role : null,
                        perm_key: current_perm ? current_perm._key : null
                    }
            )

            RETURN {
                is_valid: requester_info != null AND kb_exists,
                requester_found: requester_info != null,
                kb_exists: kb_exists,
                user_operations: user_operations,
                team_operations: team_operations,
                users_to_insert: user_operations[* FILTER CURRENT.operation == "insert"],
                users_to_update: user_operations[* FILTER CURRENT.operation == "update"],
                users_skipped: user_operations[* FILTER CURRENT.operation == "skip"],
                teams_to_insert: team_operations[* FILTER CURRENT.operation == "insert"],
                teams_to_update: team_operations[* FILTER CURRENT.operation == "update"],
                teams_skipped: team_operations[* FILTER CURRENT.operation == "skip"]
            }
            """

            cursor = self.db.aql.execute(main_query, bind_vars={
                "kb_id": kb_id,
                "requester_id": requester_id,
                "user_ids": user_ids,
                "team_ids": team_ids,
                "role": role,
                "@users_collection": CollectionNames.USERS.value,
                "@teams_collection": CollectionNames.TEAMS.value,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
                "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value,
            })

            result = next(cursor, {})

            # Fast validation
            if not result.get("is_valid"):
                if not result.get("requester_found"):
                    return {"success": False, "reason": "Requester not found or not owner", "code": 403}
                if not result.get("kb_exists"):
                    return {"success": False, "reason": "Knowledge base not found", "code": 404}

            users_to_insert = result.get("users_to_insert", [])
            users_skipped = result.get("users_skipped", [])
            teams_to_insert = result.get("teams_to_insert", [])
            teams_skipped = result.get("teams_skipped", [])

            # Prepare batch operations
            operations = []

            # Batch insert new permissions
            if users_to_insert or teams_to_insert:
                insert_docs = []

                for user_data in users_to_insert:
                    insert_docs.append({
                        "_from": f"users/{user_data['user_key']}",
                        "_to": f"recordGroups/{kb_id}",
                        "externalPermissionId": "",
                        "type": "USER",
                        "role": role,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "lastUpdatedTimestampAtSource": timestamp,
                    })

                for team_data in teams_to_insert:
                    insert_docs.append({
                        "_from": f"teams/{team_data['team_key']}",
                        "_to": f"recordGroups/{kb_id}",
                        "externalPermissionId": "",
                        "type": "TEAM",
                        "role": role,
                        "createdAtTimestamp": timestamp,
                        "updatedAtTimestamp": timestamp,
                        "lastUpdatedTimestampAtSource": timestamp,
                    })

                if insert_docs:
                    operations.append((
                        "FOR doc IN @docs INSERT doc INTO @@permissions_collection",
                        {"docs": insert_docs, "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value}
                    ))

            # Execute all operations in sequence (could be made parallel if needed)
            for query, bind_vars in operations:
                self.db.aql.execute(query, bind_vars=bind_vars)

            # Build optimized response
            granted_count = len(users_to_insert) + len(teams_to_insert)

            final_result = {
                "success": True,
                "grantedCount": granted_count,
                "grantedUsers": [u["user_id"] for u in users_to_insert],
                "grantedTeams": [t["team_id"] for t in teams_to_insert],
                "role": role,
                "kbId": kb_id,
                "details": {
                    "granted": {
                        "users": [{"user_key": u["user_id"], "userId": u["userId"], "name": u["name"]} for u in users_to_insert],
                        "teams": [{"team_key": t["team_id"], "name": t["name"]} for t in teams_to_insert]
                    },
                    "skipped": {
                        "users": [{"user_key": u["user_id"], "userId": u["userId"], "name": u["name"], "role": u["current_role"]} for u in users_skipped],
                        "teams": [{"team_key": t["team_id"], "name": t["name"], "role": t["current_role"]} for t in teams_skipped]
                    }
                }
            }

            self.logger.info(f"Optimized batch operation: {granted_count} granted, {len(users_skipped + teams_skipped)} skipped")
            return final_result

        except Exception as e:
            self.logger.error(f"Failed optimized batch operation: {str(e)}")
            return {"success": False, "reason": f"Database error: {str(e)}", "code": 500}

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str
    ) -> Optional[Dict]:
        """Optimistically update permissions for users and teams on a knowledge base"""
        try:
            self.logger.info(f"🚀 Optimistic update: {len(user_ids or [])} users and {len(team_ids or [])} teams on KB {kb_id} to {new_role}")

            # Quick validation of inputs
            if not user_ids and not team_ids:
                return {"success": False, "reason": "No users or teams provided", "code": "400"}

            # Validate new role
            valid_roles = ["OWNER", "ORGANIZER", "FILEORGANIZER", "WRITER", "COMMENTER", "READER"]
            if new_role not in valid_roles:
                return {
                    "success": False,
                    "reason": f"Invalid role. Must be one of: {', '.join(valid_roles)}",
                    "code": "400"
                }

            # Single atomic operation: check requester permission + get current permissions + update
            bind_vars = {
                "kb_id": kb_id,
                "requester_id": requester_id,
                "new_role": new_role,
                "timestamp": get_epoch_timestamp_in_ms(),
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            # Build conditions for targets
            target_conditions = []
            if user_ids:
                target_conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            if team_ids:
                target_conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

            # Atomic query that does everything in one go
            atomic_query = f"""
            LET requester_perm = FIRST(
                FOR perm IN @@permissions_collection
                    FILTER perm._from == CONCAT('users/', @requester_id)
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER perm.type == 'USER'
                    RETURN perm.role
            )

            LET current_perms = (
                FOR perm IN @@permissions_collection
                    FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                    FILTER ({' OR '.join(target_conditions)})
                    RETURN {{
                        _key: perm._key,
                        id: SPLIT(perm._from, '/')[1],
                        type: perm.type,
                        current_role: perm.role,
                        _from: perm._from
                    }}
            )

            LET validation_result = (
                requester_perm != "OWNER" ? {{error: "Only KB owners can update permissions", code: "403"}} :
                null
            )

            LET updated_perms = (
                validation_result == null ? (
                    FOR perm IN @@permissions_collection
                        FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                        FILTER ({' OR '.join(target_conditions)})
                        UPDATE perm WITH {{
                            role: @new_role,
                            updatedAtTimestamp: @timestamp,
                            lastUpdatedTimestampAtSource: @timestamp
                        }} IN @@permissions_collection
                        RETURN {{
                            _key: NEW._key,
                            id: SPLIT(NEW._from, '/')[1],
                            type: NEW.type,
                            old_role: OLD.role,
                            new_role: NEW.role
                        }}
                ) : []
            )
            RETURN {{
                validation_error: validation_result,
                current_permissions: current_perms,
                updated_permissions: updated_perms,
                requester_role: requester_perm
            }}
            """

            cursor = self.db.aql.execute(atomic_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Query execution failed", "code": "500"}

            # Log the raw result for debugging
            self.logger.info(f"🔍 Update query result: {result}")

            # Check for validation errors
            if result["validation_error"]:
                error = result["validation_error"]
                return {"success": False, "reason": error["error"], "code": error["code"]}

            updated_permissions = result["updated_permissions"]

            # Count updates by type
            updated_users = sum(1 for perm in updated_permissions if perm["type"] == "USER")
            updated_teams = sum(1 for perm in updated_permissions if perm["type"] == "TEAM")

            # Build detailed response
            updates_by_type = {"users": {}, "teams": {}}
            for perm in updated_permissions:
                if perm["type"] == "USER":
                    updates_by_type["users"][perm["id"]] = {
                        "old_role": perm["old_role"],
                        "new_role": perm["new_role"]
                    }
                elif perm["type"] == "TEAM":
                    updates_by_type["teams"][perm["id"]] = {
                        "old_role": perm["old_role"],
                        "new_role": perm["new_role"]
                    }

            self.logger.info(f"✅ Optimistically updated {len(updated_permissions)} permissions for KB {kb_id}")

            return {
                "success": True,
                "kb_id": kb_id,
                "new_role": new_role,
                "updated_permissions": len(updated_permissions),
                "updated_users": updated_users,
                "updated_teams": updated_teams,
                "updates_detail": updates_by_type,
                "requester_role": result["requester_role"]
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to update KB permission optimistically: {str(e)}")
            return {
                "success": False,
                "reason": str(e),
                "code": "500"
            }

    async def remove_kb_permission(
        self,
        kb_id: str,
        user_ids: List[str],
        team_ids: List[str],
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """Remove permissions for multiple users and teams from a KB (internal method)"""
        try:
            db = transaction if transaction else self.db

            # Build conditions for batch removal
            conditions = []
            bind_vars = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

            if not conditions:
                return False

            query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(conditions)})
                REMOVE perm IN @@permissions_collection
                RETURN {{
                    _key: OLD._key,
                    _from: OLD._from,
                    type: OLD.type,
                    role: OLD.role
                }}
            """

            cursor = db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)

            if results:
                removed_users = sum(1 for perm in results if perm["type"] == "USER")
                removed_teams = sum(1 for perm in results if perm["type"] == "TEAM")
                self.logger.info(f"✅ Removed {len(results)} permissions from KB {kb_id} ({removed_users} users, {removed_teams} teams)")
                return True
            else:
                self.logger.warning(f"⚠️ No permissions found to remove from KB {kb_id}")
                return False

        except Exception as e:
            self.logger.error(f"❌ Failed to remove KB permissions: {str(e)}")
            return False


    async def count_kb_owners(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> int:
        """Count the number of owners for a knowledge base"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER perm.role == 'OWNER'
                COLLECT WITH COUNT INTO owner_count
                RETURN owner_count
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            })

            count = next(cursor, 0)
            self.logger.info(f"📊 KB {kb_id} has {count} owners")
            return count

        except Exception as e:
            self.logger.error(f"❌ Failed to count KB owners: {str(e)}")
            return 0

    async def get_kb_permissions(
        self,
        kb_id: str,
        user_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict[str, Dict[str, str]]:
        """Get current roles for multiple users and teams on a knowledge base in a single query"""
        try:
            db = transaction if transaction else self.db

            # Build conditions for batch query
            conditions = []
            bind_vars = {
                "kb_id": kb_id,
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER')")
                bind_vars["user_froms"] = [f"users/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"teams/{team_id}" for team_id in team_ids]

            if not conditions:
                return {"users": {}, "teams": {}}

            query = f"""
            FOR perm IN @@permissions_collection
                FILTER perm._to == CONCAT('recordGroups/', @kb_id)
                FILTER ({' OR '.join(conditions)})
                RETURN {{
                    id: SPLIT(perm._from, '/')[1],
                    type: perm.type,
                    role: perm.role
                }}
            """

            cursor = db.aql.execute(query, bind_vars=bind_vars)
            permissions = list(cursor)

            # Organize results by type
            result = {"users": {}, "teams": {}}

            for perm in permissions:
                if perm["type"] == "USER":
                    result["users"][perm["id"]] = perm["role"]
                elif perm["type"] == "TEAM":
                    result["teams"][perm["id"]] = perm["role"]

            self.logger.info(f"✅ Retrieved {len(permissions)} permissions for KB {kb_id}")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB permissions batch: {str(e)}")
            raise

    async def list_kb_permissions(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> List[Dict]:
        """List all permissions for a KB with user details"""
        try:
            db = transaction if transaction else self.db

            query = """
            FOR perm IN @@permissions_collection
                FILTER perm._to == @kb_to
                LET entity = DOCUMENT(perm._from)
                FILTER entity != null
                RETURN {
                    id: entity._key,
                    name: entity.fullName || entity.name || entity.userName,
                    userId: entity.userId,
                    email: entity.email,
                    role: perm.role,
                    type: perm.type,
                    createdAtTimestamp: perm.createdAtTimestamp,
                    updatedAtTimestamp: perm.updatedAtTimestamp
                }
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_to": f"recordGroups/{kb_id}",
                "@permissions_collection": CollectionNames.PERMISSIONS_TO_KB.value,
            })

            return list(cursor)

        except Exception as e:
            self.logger.error(f"❌ Failed to list KB permissions: {str(e)}")
            return []

    async def list_all_records(
        self,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        permissions: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        source: str,
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records the user can access directly via belongs_to_kb edges.
        Returns (records, total_count, available_filters)
        """
        try:
            self.logger.info(f"🔍 Listing all records for user {user_id}, source: {source}")

            # Determine what data sources to include
            include_kb_records = source in ['all', 'local']
            include_connector_records = source in ['all', 'connector']

            # Build filter conditions function
            def build_record_filters(include_filter_vars: bool = True) -> str:
                conditions = []
                if search and include_filter_vars:
                    conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                if record_types and include_filter_vars:
                    conditions.append("record.recordType IN @record_types")
                if origins and include_filter_vars:
                    conditions.append("record.origin IN @origins")
                if connectors and include_filter_vars:
                    conditions.append("record.connectorName IN @connectors")
                if indexing_status and include_filter_vars:
                    conditions.append("record.indexingStatus IN @indexing_status")
                if date_from and include_filter_vars:
                    conditions.append("record.createdAtTimestamp >= @date_from")
                if date_to and include_filter_vars:
                    conditions.append("record.createdAtTimestamp <= @date_to")

                return " AND " + " AND ".join(conditions) if conditions else ""

            base_kb_roles = {"OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"}
            if permissions:
                # This ensures we only filter by roles that are valid for KBs AND requested by the user.
                final_kb_roles = list(base_kb_roles.intersection(set(permissions)))
                # If the intersection is empty, no KB records will match, which is correct.
                if not final_kb_roles:
                    # To prevent an empty `IN []` which can be inefficient, we can just disable the kbRecords part.
                    include_kb_records = False
            else:
                final_kb_roles = list(base_kb_roles)

            # Build permission filter for connector records
            def build_permission_filter(include_filter_vars: bool = True) -> str:
                if permissions and include_filter_vars:
                    return " AND permissionEdge.role IN @permissions"
                return ""

            # ===== MAIN QUERY (with pagination and filters and file records) =====
            record_filter = build_record_filters(True)
            permission_filter = build_permission_filter(True)

            main_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            // KB Records Section - Get records DIRECTLY from belongs_to_kb edges (not through folders)
            LET kbRecords = {
                f'''(
                    FOR kbEdge IN @@permissions_to_kb
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN @kb_permissions
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        // Get records that belong directly to the KB
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            // Only include actual records (not folders)
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN {{
                                record: record,
                                permission: {{ role: kbEdge.role, type: kbEdge.type }},
                                kb_id: kb._key,
                                kb_name: kb.groupName
                            }}
                )''' if include_kb_records else '[]'
            }
            // Connector Records Section - Direct connector permissions
            LET connectorRecords = {
                f'''(
                    FOR permissionEdge IN @@permissions_to_kb
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN {{
                            record: record,
                            permission: {{ role: permissionEdge.role, type: permissionEdge.type }}
                        }}
                )''' if include_connector_records else '[]'
            }
            LET allRecords = APPEND(kbRecords, connectorRecords)
            FOR item IN allRecords
                LET record = item.record
                SORT record.{sort_by} {sort_order.upper()}
                LIMIT @skip, @limit
                LET fileRecord = FIRST(
                    FOR fileEdge IN @@is_of_type
                        FILTER fileEdge._from == record._id
                        LET file = DOCUMENT(fileEdge._to)
                        FILTER file != null
                        RETURN {{
                            id: file._key,
                            name: file.name,
                            extension: file.extension,
                            mimeType: file.mimeType,
                            sizeInBytes: file.sizeInBytes,
                            isFile: file.isFile,
                            webUrl: file.webUrl
                        }}
                )
                RETURN {{
                    id: record._key,
                    externalRecordId: record.externalRecordId,
                    externalRevisionId: record.externalRevisionId,
                    recordName: record.recordName,
                    recordType: record.recordType,
                    origin: record.origin,
                    connectorName: record.connectorName || "KNOWLEDGE_BASE",
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    orgId: record.orgId,
                    version: record.version,
                    isDeleted: record.isDeleted,
                    deletedByUserId: record.deletedByUserId,
                    isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true,
                    webUrl: record.webUrl,
                    fileRecord: fileRecord,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb: {{id: item.kb_id || null, name: item.kb_name || null }}
                }}
            """

            # ===== COUNT QUERY =====
            count_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET kbCount = {
                f'''LENGTH(
                    FOR kbEdge IN @@permissions_to_kb
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN @kb_permissions
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            FILTER record.isFile != false
                            {record_filter}
                            RETURN 1
                )''' if include_kb_records else '0'
            }
            LET connectorCount = {
                f'''LENGTH(
                    FOR permissionEdge IN @@permissions_to_kb
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        {permission_filter}
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        {record_filter}
                        RETURN 1
                )''' if include_connector_records else '0'
            }
            RETURN kbCount + connectorCount
            """

            # ===== FILTERS QUERY =====
            filters_query = f"""
            LET user_from = @user_from
            LET org_id = @org_id
            LET allKbRecords = {
                '''(
                    FOR kbEdge IN @@permissions_to_kb
                        FILTER kbEdge._from == user_from
                        FILTER kbEdge.type == "USER"
                        FILTER kbEdge.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                        LET kb = DOCUMENT(kbEdge._to)
                        FILTER kb != null AND kb.orgId == org_id
                        FOR belongsEdge IN @@belongs_to_kb
                            FILTER belongsEdge._to == kb._id
                            LET record = DOCUMENT(belongsEdge._from)
                            FILTER record != null
                            FILTER record.isDeleted != true
                            FILTER record.orgId == org_id
                            FILTER record.origin == "UPLOAD"
                            FILTER record.isFile != false
                            RETURN {
                                record: record,
                                permission: { role: kbEdge.role }
                            }
                )''' if include_kb_records else '[]'
            }
            LET allConnectorRecords = {
                '''(
                    FOR permissionEdge IN @@permissions_to_kb
                        FILTER permissionEdge._from == user_from
                        FILTER permissionEdge.type == "USER"
                        LET record = DOCUMENT(permissionEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == org_id
                        FILTER record.origin == "CONNECTOR"
                        RETURN {
                            record: record,
                            permission: { role: permissionEdge.role }
                        }
                )''' if include_connector_records else '[]'
            }
            LET allRecords = APPEND(allKbRecords, allConnectorRecords)
            LET flatRecords = (
                FOR item IN allRecords
                    RETURN item.record
            )
            LET permissionValues = (
                FOR item IN allRecords
                    FILTER item.permission != null
                    RETURN item.permission.role
            )
            LET connectorValues = (
                FOR record IN flatRecords
                    FILTER record.connectorName != null
                    RETURN record.connectorName
            )
            RETURN {{
                recordTypes: UNIQUE(flatRecords[*].recordType) || [],
                origins: UNIQUE(flatRecords[*].origin) || [],
                connectors: UNIQUE(connectorValues) || [],
                indexingStatus: UNIQUE(flatRecords[*].indexingStatus) || [],
                permissions: UNIQUE(permissionValues) || []
            }}
            """

            # Build bind variables
            filter_bind_vars = {}
            if search:
                filter_bind_vars["search"] = f"%{search.lower()}%"
            if record_types:
                filter_bind_vars["record_types"] = record_types
            if origins:
                filter_bind_vars["origins"] = origins
            if connectors:
                filter_bind_vars["connectors"] = connectors
            if indexing_status:
                filter_bind_vars["indexing_status"] = indexing_status
            if permissions:
                filter_bind_vars["permissions"] = permissions
            if date_from:
                filter_bind_vars["date_from"] = date_from
            if date_to:
                filter_bind_vars["date_to"] = date_to

            main_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "skip": skip,
                "limit": limit,
                "kb_permissions": final_kb_roles,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "kb_permissions": final_kb_roles,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "user_from": f"users/{user_id}",
                "org_id": org_id,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
            }

            # Execute queries
            db = self.db
            records = list(db.aql.execute(main_query, bind_vars=main_bind_vars))
            count = list(db.aql.execute(count_query, bind_vars=count_bind_vars))[0]
            available_filters = list(db.aql.execute(filters_query, bind_vars=filters_bind_vars))[0]

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [])

            self.logger.info(f"✅ Listed {len(records)} records out of {count} total")
            return records, count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list all records: {str(e)}")
            return [], 0, {
                "recordTypes": [],
                "origins": [],
                "connectors": [],
                "indexingStatus": [],
                "permissions": []
            }

    async def list_kb_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        skip: int,
        limit: int,
        search: Optional[str],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        date_from: Optional[int],
        date_to: Optional[int],
        sort_by: str,
        sort_order: str,
        folder_id: Optional[str] = None,  # Add folder filter parameter
    ) -> Tuple[List[Dict], int, Dict]:
        """
        List all records in a specific KB through folder structure for better folder-based filtering.
        """
        try:
            self.logger.info(f"🔍 Listing records for KB {kb_id} (folder-based)")

            db = self.db

            # Check user permissions first
            perm_query = """
            FOR perm IN @@permissions_to_kb
                FILTER perm._from == @user_from
                FILTER perm._to == @kb_to
                FILTER perm.type == "USER"
                FILTER perm.role IN ['OWNER', 'READER', 'FILEORGANIZER', 'WRITER', 'COMMENTER', 'ORGANIZER']
                RETURN perm.role
            """

            perm_cursor = db.aql.execute(perm_query, bind_vars={
                "user_from": f"users/{user_id}",
                "kb_to": f"recordGroups/{kb_id}",
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
            })

            user_permission = next(perm_cursor, None)
            if not user_permission:
                self.logger.warning(f"⚠️ User {user_id} has no access to KB {kb_id}")
                return [], 0, {
                    "recordTypes": [],
                    "origins": [],
                    "connectors": [],
                    "indexingStatus": [],
                    "permissions": []
                }

            # Build filter conditions
            def build_record_filters(include_filter_vars: bool = True) -> str:
                conditions = []
                if search and include_filter_vars:
                    conditions.append("(LIKE(LOWER(record.recordName), @search) OR LIKE(LOWER(record.externalRecordId), @search))")
                if record_types and include_filter_vars:
                    conditions.append("record.recordType IN @record_types")
                if origins and include_filter_vars:
                    conditions.append("record.origin IN @origins")
                if connectors and include_filter_vars:
                    conditions.append("record.connectorName IN @connectors")
                if indexing_status and include_filter_vars:
                    conditions.append("record.indexingStatus IN @indexing_status")
                if date_from and include_filter_vars:
                    conditions.append("record.createdAtTimestamp >= @date_from")
                if date_to and include_filter_vars:
                    conditions.append("record.createdAtTimestamp <= @date_to")

                return " AND " + " AND ".join(conditions) if conditions else ""

            def build_folder_filter(include_filter_vars: bool = True) -> str:
                if folder_id and include_filter_vars:
                    return " AND folder._key == @folder_id"
                return ""

            # ===== MAIN QUERY =====
            record_filter = build_record_filters(True)
            folder_filter = build_folder_filter(True)

            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET user_permission = @user_permission
            // Get all folders in the KB
            LET kbFolders = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    {folder_filter}
                    RETURN folder
            )
            // Get records from folders via PARENT_CHILD relationships
            LET folderRecords = (
                FOR folder IN kbFolders
                    FOR relEdge IN @@record_relations
                        FILTER relEdge._from == folder._id
                        FILTER relEdge.relationshipType == "PARENT_CHILD"
                        LET record = DOCUMENT(relEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == @org_id
                        FILTER record.isFile != false  // Ensure it's a record, not a folder
                        {record_filter}
                        RETURN {{
                            record: record,
                            folder_id: folder._key,
                            folder_name: folder.name,
                            permission: {{ role: user_permission, type: "USER" }},
                            kb_id: @kb_id
                        }}
            )
            FOR item IN folderRecords
                LET record = item.record
                SORT record.{sort_by} {sort_order.upper()}
                LIMIT @skip, @limit
                LET fileRecord = FIRST(
                    FOR fileEdge IN @@is_of_type
                        FILTER fileEdge._from == record._id
                        LET file = DOCUMENT(fileEdge._to)
                        FILTER file != null
                        RETURN {{
                            id: file._key,
                            name: file.name,
                            extension: file.extension,
                            mimeType: file.mimeType,
                            sizeInBytes: file.sizeInBytes,
                            isFile: file.isFile,
                            webUrl: file.webUrl
                        }}
                )
                RETURN {{
                    id: record._key,
                    externalRecordId: record.externalRecordId,
                    externalRevisionId: record.externalRevisionId,
                    recordName: record.recordName,
                    recordType: record.recordType,
                    origin: record.origin,
                    connectorName: record.connectorName || "KNOWLEDGE_BASE",
                    indexingStatus: record.indexingStatus,
                    createdAtTimestamp: record.createdAtTimestamp,
                    updatedAtTimestamp: record.updatedAtTimestamp,
                    sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                    sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                    orgId: record.orgId,
                    version: record.version,
                    isDeleted: record.isDeleted,
                    deletedByUserId: record.deletedByUserId,
                    isLatestVersion: record.isLatestVersion != null ? record.isLatestVersion : true,
                    webUrl: record.webUrl,
                    fileRecord: fileRecord,
                    permission: {{role: item.permission.role, type: item.permission.type}},
                    kb_id: item.kb_id,
                    folder: {{id: item.folder_id, name: item.folder_name}},
                }}
            """

            # ===== COUNT QUERY =====
            count_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET kbFolders = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    {folder_filter}
                    RETURN folder
            )
            LET folderRecords = (
                FOR folder IN kbFolders
                    FOR relEdge IN @@record_relations
                        FILTER relEdge._from == folder._id
                        FILTER relEdge.relationshipType == "PARENT_CHILD"
                        LET record = DOCUMENT(relEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == @org_id
                        FILTER record.isFile != false
                        {record_filter}
                        RETURN 1
            )
            RETURN LENGTH(folderRecords)
            """

            # ===== FILTERS QUERY =====
            filters_query = """
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            LET user_permission = @user_permission
            LET kbFolders = (
                FOR belongsEdge IN @@belongs_to_kb
                    FILTER belongsEdge._to == kb._id
                    LET folder = DOCUMENT(belongsEdge._from)
                    FILTER folder != null
                    FILTER folder.isFile == false
                    RETURN folder
            )
            LET allRecords = (
                FOR folder IN kbFolders
                    FOR relEdge IN @@record_relations
                        FILTER relEdge._from == folder._id
                        FILTER relEdge.relationshipType == "PARENT_CHILD"
                        LET record = DOCUMENT(relEdge._to)
                        FILTER record != null
                        FILTER record.isDeleted != true
                        FILTER record.orgId == @org_id
                        FILTER record.isFile != false
                        RETURN record
            )
            LET connectorValues = (
                FOR record IN allRecords
                    FILTER record.connectorName != null
                    RETURN record.connectorName
            )
            // Get available folders for filtering
            LET availableFolders = (
                FOR folder IN kbFolders
                    RETURN {
                        id: folder._key,
                        name: folder.name
                    }
            )
            RETURN {
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(connectorValues) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || [],
                permissions: [user_permission] || [],
                folders: availableFolders || []
            }
            """

            # Build bind variables
            filter_bind_vars = {}
            if search:
                filter_bind_vars["search"] = f"%{search.lower()}%"
            if record_types:
                filter_bind_vars["record_types"] = record_types
            if origins:
                filter_bind_vars["origins"] = origins
            if connectors:
                filter_bind_vars["connectors"] = connectors
            if indexing_status:
                filter_bind_vars["indexing_status"] = indexing_status
            if date_from:
                filter_bind_vars["date_from"] = date_from
            if date_to:
                filter_bind_vars["date_to"] = date_to
            if folder_id:
                filter_bind_vars["folder_id"] = folder_id

            main_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission,
                "skip": skip,
                "limit": limit,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_bind_vars,
            }

            count_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                **filter_bind_vars,
            }

            filters_bind_vars = {
                "kb_id": kb_id,
                "org_id": org_id,
                "user_permission": user_permission,
                "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
            }

            # Execute queries
            records = list(db.aql.execute(main_query, bind_vars=main_bind_vars))
            count = list(db.aql.execute(count_query, bind_vars=count_bind_vars))[0]
            available_filters = list(db.aql.execute(filters_query, bind_vars=filters_bind_vars))[0]

            # Ensure filter structure
            if not available_filters:
                available_filters = {}
            available_filters.setdefault("recordTypes", [])
            available_filters.setdefault("origins", [])
            available_filters.setdefault("connectors", [])
            available_filters.setdefault("indexingStatus", [])
            available_filters.setdefault("permissions", [user_permission] if user_permission else [])
            available_filters.setdefault("folders", [])

            self.logger.info(f"✅ Listed {len(records)} KB records out of {count} total")
            return records, count, available_filters

        except Exception as e:
            self.logger.error(f"❌ Failed to list KB records: {str(e)}")
            return [], 0, {
                "recordTypes": [],
                "origins": [],
                "connectors": [],
                "indexingStatus": [],
                "permissions": [],
                "folders": []
            }

    async def get_kb_children(
        self,
        kb_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Get KB root contents with folders_first pagination and level order traversal
        Folders First Logic:
        - Show ALL folders first (within page limits)
        - Then show records in remaining space
        - If folders exceed page limit, paginate folders only
        - If folders fit in page, fill remaining space with records
        """
        try:
            db = transaction if transaction else self.db

            # Build filter conditions
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars = {}

                if search:
                    folder_conditions.append("LIKE(LOWER(folder.name), @search_term)")
                    record_conditions.append("(LIKE(LOWER(record.recordName), @search_term) OR LIKE(LOWER(record.externalRecordId), @search_term))")
                    bind_vars["search_term"] = f"%{search.lower()}%"

                if record_types:
                    record_conditions.append("record.recordType IN @record_types")
                    bind_vars["record_types"] = record_types

                if origins:
                    record_conditions.append("record.origin IN @origins")
                    bind_vars["origins"] = origins

                if connectors:
                    record_conditions.append("record.connectorName IN @connectors")
                    bind_vars["connectors"] = connectors

                if indexing_status:
                    record_conditions.append("record.indexingStatus IN @indexing_status")
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            # Sort field mapping for records (folders always sorted by name)
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            LET kb = DOCUMENT("recordGroups", @kb_id)
            FILTER kb != null
            // Get ALL folders with level traversal (sorted by name)
            LET allFolders = (
                FOR v, e, p IN 1..@level OUTBOUND kb._id @@record_relations
                    FILTER e.relationshipType == "PARENT_CHILD"
                    FILTER v.isFile == false
                    LET folder = v
                    LET current_level = LENGTH(p.edges)
                    {folder_filter}
                    // Get counts for this folder
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == folder._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET subfolder = DOCUMENT(relEdge._to)
                            FILTER subfolder != null AND subfolder.isFile == false
                            RETURN 1
                    )
                    LET direct_records = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == folder._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET record = DOCUMENT(relEdge._to)
                            FILTER record != null AND record.isDeleted != true AND record.isFile != false
                            RETURN 1
                    )
                    SORT folder.name ASC
                    RETURN {{
                        id: folder._key,
                        name: folder.name,
                        path: folder.path,
                        level: current_level,
                        parent_id: p.edges[-1] ? PARSE_IDENTIFIER(p.edges[-1]._from).key : null,
                        webUrl: folder.webUrl,
                        recordGroupId: folder.recordGroupId,
                        type: "folder",
                        createdAtTimestamp: folder.createdAtTimestamp,
                        updatedAtTimestamp: folder.updatedAtTimestamp,
                        counts: {{
                            subfolders: direct_subfolders,
                            records: direct_records,
                            totalItems: direct_subfolders + direct_records
                        }},
                        hasChildren: direct_subfolders > 0 OR direct_records > 0
                    }}
            )
            // Get ALL records directly in KB root
            LET allRecords = (
                FOR edge IN @@record_relations
                    FILTER edge._from == kb._id
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(edge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    FILTER record.isFile != false
                    {record_filter}
                    // Get associated file record
                    LET fileEdge = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            RETURN isEdge
                    )
                    LET fileRecord = fileEdge ? DOCUMENT(fileEdge._to) : null
                    SORT {record_sort_field} {sort_direction}
                    RETURN {{
                        id: record._key,
                        recordName: record.recordName,
                        name: record.recordName,
                        recordType: record.recordType,
                        externalRecordId: record.externalRecordId,
                        origin: record.origin,
                        connectorName: record.connectorName || "KNOWLEDGE_BASE",
                        indexingStatus: record.indexingStatus,
                        version: record.version,
                        isLatestVersion: record.isLatestVersion,
                        createdAtTimestamp: record.createdAtTimestamp,
                        updatedAtTimestamp: record.updatedAtTimestamp,
                        sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                        sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                        webUrl: record.webUrl,
                        orgId: record.orgId,
                        type: "record",
                        fileRecord: fileRecord ? {{
                            id: fileRecord._key,
                            name: fileRecord.name,
                            extension: fileRecord.extension,
                            mimeType: fileRecord.mimeType,
                            sizeInBytes: fileRecord.sizeInBytes,
                            webUrl: fileRecord.webUrl,
                            path: fileRecord.path,
                            isFile: fileRecord.isFile
                        }} : null
                    }}
            )
            LET totalFolders = LENGTH(allFolders)
            LET totalRecords = LENGTH(allRecords)
            LET totalCount = totalFolders + totalRecords
            // Pagination Logic for Folders First:
            // 1. If skip < totalFolders: Show folders from skip position
            // 2. Fill remaining limit with records
            // 3. If skip >= totalFolders: Show only records (skip folders entirely)
            LET paginatedFolders = (
                @skip < totalFolders ?
                    SLICE(allFolders, @skip, @limit)
                : []
            )
            LET foldersShown = LENGTH(paginatedFolders)
            LET remainingLimit = @limit - foldersShown
            LET recordSkip = @skip >= totalFolders ? (@skip - totalFolders) : 0
            LET recordLimit = @skip >= totalFolders ? @limit : remainingLimit
            LET paginatedRecords = (
                recordLimit > 0 ?
                    SLICE(allRecords, recordSkip, recordLimit)
                : []
            )
            // Available filter values from all records (not just paginated)
            LET availableFilters = {{
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(allRecords[*].connectorName) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || []
            }}
            RETURN {{
                success: true,
                container: {{
                    id: kb._key,
                    name: kb.groupName,
                    path: "/",
                    type: "kb",
                    webUrl: CONCAT("/kb/", kb._key),
                    recordGroupId: kb._key
                }},
                folders: paginatedFolders,
                records: paginatedRecords,
                level: @level,
                totalCount: totalCount,
                counts: {{
                    folders: LENGTH(paginatedFolders),
                    records: LENGTH(paginatedRecords),
                    totalItems: LENGTH(paginatedFolders) + LENGTH(paginatedRecords),
                    totalFolders: totalFolders,
                    totalRecords: totalRecords
                }},
                availableFilters: availableFilters,
                paginationMode: "folders_first"
            }}
            """

            bind_vars = {
                "kb_id": kb_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars
            }

            cursor = db.aql.execute(main_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Knowledge base not found"}

            self.logger.info(f"✅ Retrieved KB children with folders_first pagination: {result['counts']['totalItems']} items")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get KB children with folders_first pagination: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def get_folder_children(
        self,
        kb_id: str,
        folder_id: str,
        skip: int,
        limit: int,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Get folder contents with folders_first pagination and level order traversal
        """
        try:
            db = transaction if transaction else self.db

            # Build filter conditions (same as KB version)
            def build_filters() -> Tuple[str, str, Dict]:
                folder_conditions = []
                record_conditions = []
                bind_vars = {}

                if search:
                    folder_conditions.append("LIKE(LOWER(subfolder.name), @search_term)")
                    record_conditions.append("(LIKE(LOWER(record.recordName), @search_term) OR LIKE(LOWER(record.externalRecordId), @search_term))")
                    bind_vars["search_term"] = f"%{search.lower()}%"

                if record_types:
                    record_conditions.append("record.recordType IN @record_types")
                    bind_vars["record_types"] = record_types

                if origins:
                    record_conditions.append("record.origin IN @origins")
                    bind_vars["origins"] = origins

                if connectors:
                    record_conditions.append("record.connectorName IN @connectors")
                    bind_vars["connectors"] = connectors

                if indexing_status:
                    record_conditions.append("record.indexingStatus IN @indexing_status")
                    bind_vars["indexing_status"] = indexing_status

                folder_filter = " AND " + " AND ".join(folder_conditions) if folder_conditions else ""
                record_filter = " AND " + " AND ".join(record_conditions) if record_conditions else ""

                return folder_filter, record_filter, bind_vars

            folder_filter, record_filter, filter_vars = build_filters()

            # Sort field mapping for records
            record_sort_map = {
                "name": "record.recordName",
                "created_at": "record.createdAtTimestamp",
                "updated_at": "record.updatedAtTimestamp",
                "size": "fileRecord.sizeInBytes"
            }
            record_sort_field = record_sort_map.get(sort_by, "record.recordName")
            sort_direction = sort_order.upper()

            main_query = f"""
            LET folder = DOCUMENT("files", @folder_id)
            FILTER folder != null
            FILTER folder.isFile == false
            FILTER folder.recordGroupId == @kb_id
            // Get ALL subfolders with level traversal
            LET allSubfolders = (
                FOR v, e, p IN 1..@level OUTBOUND folder._id @@record_relations
                    FILTER e.relationshipType == "PARENT_CHILD"
                    FILTER v.isFile == false
                    LET subfolder = v
                    LET current_level = LENGTH(p.edges)
                    {folder_filter}
                    LET direct_subfolders = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == subfolder._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET subsubfolder = DOCUMENT(relEdge._to)
                            FILTER subsubfolder != null AND subsubfolder.isFile == false
                            RETURN 1
                    )
                    LET direct_records = LENGTH(
                        FOR relEdge IN @@record_relations
                            FILTER relEdge._from == subfolder._id
                            FILTER relEdge.relationshipType == "PARENT_CHILD"
                            LET record = DOCUMENT(relEdge._to)
                            FILTER record != null AND record.isDeleted != true AND record.isFile != false
                            RETURN 1
                    )
                    SORT subfolder.name ASC
                    RETURN {{
                        id: subfolder._key,
                        name: subfolder.name,
                        path: subfolder.path,
                        level: current_level,
                        parentId: p.edges[-1] ? PARSE_IDENTIFIER(p.edges[-1]._from).key : null,
                        webUrl: subfolder.webUrl,
                        recordGroupId: subfolder.recordGroupId,
                        type: "folder",
                        createdAtTimestamp: subfolder.createdAtTimestamp,
                        updatedAtTimestamp: subfolder.updatedAtTimestamp,
                        counts: {{
                            subfolders: direct_subfolders,
                            records: direct_records,
                            totalItems: direct_subfolders + direct_records
                        }},
                        hasChildren: direct_subfolders > 0 OR direct_records > 0
                    }}
            )
            // Get ALL records in this folder
            LET allRecords = (
                FOR edge IN @@record_relations
                    FILTER edge._from == folder._id
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET record = DOCUMENT(edge._to)
                    FILTER record != null
                    FILTER record.isDeleted != true
                    FILTER record.isFile != false
                    {record_filter}
                    LET fileEdge = FIRST(
                        FOR isEdge IN @@is_of_type
                            FILTER isEdge._from == record._id
                            RETURN isEdge
                    )
                    LET fileRecord = fileEdge ? DOCUMENT(fileEdge._to) : null
                    SORT {record_sort_field} {sort_direction}
                    RETURN {{
                        id: record._key,
                        recordName: record.recordName,
                        name: record.recordName,
                        recordType: record.recordType,
                        externalRecordId: record.externalRecordId,
                        origin: record.origin,
                        connectorName: record.connectorName || "KNOWLEDGE_BASE",
                        indexingStatus: record.indexingStatus,
                        version: record.version,
                        isLatestVersion: record.isLatestVersion,
                        createdAtTimestamp: record.createdAtTimestamp,
                        updatedAtTimestamp: record.updatedAtTimestamp,
                        sourceCreatedAtTimestamp: record.sourceCreatedAtTimestamp,
                        sourceLastModifiedTimestamp: record.sourceLastModifiedTimestamp,
                        webUrl: record.webUrl,
                        orgId: record.orgId,
                        type: "record",
                        parent_folder_id: @folder_id,
                        sizeInBytes: fileRecord ? fileRecord.sizeInBytes : 0,
                        fileRecord: fileRecord ? {{
                            id: fileRecord._key,
                            name: fileRecord.name,
                            extension: fileRecord.extension,
                            mimeType: fileRecord.mimeType,
                            sizeInBytes: fileRecord.sizeInBytes,
                            webUrl: fileRecord.webUrl,
                            path: fileRecord.path,
                            isFile: fileRecord.isFile
                        }} : null
                    }}
            )
            LET totalSubfolders = LENGTH(allSubfolders)
            LET totalRecords = LENGTH(allRecords)
            LET totalCount = totalSubfolders + totalRecords
            // Folders First Pagination Logic
            LET paginatedSubfolders = (
                @skip < totalSubfolders ?
                    SLICE(allSubfolders, @skip, @limit)
                : []
            )
            LET subfoldersShown = LENGTH(paginatedSubfolders)
            LET remainingLimit = @limit - subfoldersShown
            LET recordSkip = @skip >= totalSubfolders ? (@skip - totalSubfolders) : 0
            LET recordLimit = @skip >= totalSubfolders ? @limit : remainingLimit
            LET paginatedRecords = (
                recordLimit > 0 ?
                    SLICE(allRecords, recordSkip, recordLimit)
                : []
            )
            LET availableFilters = {{
                recordTypes: UNIQUE(allRecords[*].recordType) || [],
                origins: UNIQUE(allRecords[*].origin) || [],
                connectors: UNIQUE(allRecords[*].connectorName) || [],
                indexingStatus: UNIQUE(allRecords[*].indexingStatus) || []
            }}
            RETURN {{
                success: true,
                container: {{
                    id: folder._key,
                    name: folder.name,
                    path: folder.path,
                    type: "folder",
                    webUrl: folder.webUrl,
                    recordGroupId: folder.recordGroupId
                }},
                folders: paginatedSubfolders,
                records: paginatedRecords,
                level: @level,
                totalCount: totalCount,
                counts: {{
                    folders: LENGTH(paginatedSubfolders),
                    records: LENGTH(paginatedRecords),
                    totalItems: LENGTH(paginatedSubfolders) + LENGTH(paginatedRecords),
                    totalFolders: totalSubfolders,
                    totalRecords: totalRecords
                }},
                available_filters: availableFilters,
                pagination_mode: "folders_first"
            }}
            """

            bind_vars = {
                "kb_id": kb_id,
                "folder_id": folder_id,
                "skip": skip,
                "limit": limit,
                "level": level,
                "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                **filter_vars
            }

            cursor = db.aql.execute(main_query, bind_vars=bind_vars)
            result = next(cursor, None)

            if not result:
                return {"success": False, "reason": "Folder not found"}

            self.logger.info(f"✅ Retrieved folder children with folders_first pagination: {result['counts']['totalItems']} items")
            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder children with folders_first pagination: {str(e)}")
            return {"success": False, "reason": str(e)}

    async def delete_knowledge_base(
        self,
        kb_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """
        Delete a knowledge base with ALL nested content
        - All folders (recursive, any depth)
        - All records in all folders
        - All file records
        - All edges (belongs_to_kb, record_relations, is_of_type, permissions)
        - The KB document itself
        """
        try:
            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.RECORD_GROUPS.value,
                            CollectionNames.FILES.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.BELONGS_TO.value,
                            CollectionNames.IS_OF_TYPE.value,
                            CollectionNames.PERMISSIONS_TO_KB.value,
                        ]
                    )
                    self.logger.info(f"🔄 Transaction created for complete KB {kb_id} deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return False

            try:
                # Step 1: Get complete inventory of what we're deleting
                inventory_query = """
                LET kb = DOCUMENT("recordGroups", @kb_id)
                FILTER kb != null
                LET all_folders = (
                    FOR folder IN @@files_collection
                        FILTER folder.recordGroupId == @kb_id AND folder.isFile == false
                        RETURN folder._key
                )
                LET all_kb_records_with_details = (
                    FOR edge IN @@belongs_to_kb
                        FILTER edge._to == CONCAT('recordGroups/', @kb_id)
                        LET record = DOCUMENT(edge._from)
                        FILTER record != null AND record.isFile != false
                        // Get associated file record for each record
                        LET file_record = FIRST(
                            FOR isEdge IN @@is_of_type
                                FILTER isEdge._from == record._id
                                LET fileRecord = DOCUMENT(isEdge._to)
                                FILTER fileRecord != null
                                RETURN fileRecord
                        )
                        RETURN {
                            record: record,
                            file_record: file_record
                        }
                )
                LET all_file_records = (
                    FOR record_data IN all_kb_records_with_details
                        FILTER record_data.file_record != null
                        RETURN record_data.file_record._key
                )
                RETURN {
                    kb_exists: kb != null,
                    folders: all_folders,
                    records_with_details: all_kb_records_with_details,
                    file_records: all_file_records,
                    total_folders: LENGTH(all_folders),
                    total_records: LENGTH(all_kb_records_with_details),
                    total_file_records: LENGTH(all_file_records)
                }
                """

                cursor = transaction.aql.execute(inventory_query, bind_vars={
                    "kb_id": kb_id,
                    "@files_collection": CollectionNames.FILES.value,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                inventory = next(cursor, {})

                if not inventory.get("kb_exists"):
                    self.logger.warning(f"⚠️ KB {kb_id} not found, deletion considered successful.")
                    if should_commit:
                        # No need to abort, just commit the empty transaction
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                    return True

                records_with_details = inventory.get("records_with_details", [])

                self.logger.info(f"📋 KB {kb_id} deletion inventory:")
                self.logger.info(f"   📁 Folders: {inventory['total_folders']}")
                self.logger.info(f"   📄 Records: {inventory['total_records']}")
                self.logger.info(f"   🗂️ File records: {inventory['total_file_records']}")

                # Step 2: Delete ALL edges first (prevents foreign key issues)
                self.logger.info("🗑️ Step 1: Deleting all edges...")

                all_record_keys = [rd["record"]["_key"] for rd in records_with_details]
                edges_cleanup_query = """
                LET btk_keys_to_delete = (FOR e IN @@belongs_to_kb FILTER e._to == CONCAT('recordGroups/', @kb_id) RETURN e._key)
                LET perm_keys_to_delete = (FOR e IN @@permissions_to_kb FILTER e._to == CONCAT('recordGroups/', @kb_id) RETURN e._key)
                LET iot_keys_to_delete = (FOR rk IN @all_records FOR e IN @@is_of_type FILTER e._from == CONCAT('records/', rk) RETURN e._key)
                LET all_related_doc_ids = APPEND((FOR f IN @all_folders RETURN CONCAT('files/', f)), (FOR r IN @all_records RETURN CONCAT('records/', r)))
                LET relation_keys_to_delete = (FOR e IN @@record_relations FILTER e._from IN all_related_doc_ids OR e._to IN all_related_doc_ids COLLECT k = e._key RETURN k)
                FOR btk_key IN btk_keys_to_delete REMOVE btk_key IN @@belongs_to_kb OPTIONS { ignoreErrors: true }
                FOR perm_key IN perm_keys_to_delete REMOVE perm_key IN @@permissions_to_kb OPTIONS { ignoreErrors: true }
                FOR iot_key IN iot_keys_to_delete REMOVE iot_key IN @@is_of_type OPTIONS { ignoreErrors: true }
                FOR relation_key IN relation_keys_to_delete REMOVE relation_key IN @@record_relations OPTIONS { ignoreErrors: true }
                """
                transaction.aql.execute(edges_cleanup_query, bind_vars={
                    "kb_id": kb_id,
                    "all_folders": inventory["folders"],
                    "all_records": all_record_keys,
                    "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })
                self.logger.info(f"✅ All edges deleted for KB {kb_id}")

                # Step 3: Delete all file records
                if inventory["file_records"]:
                    self.logger.info(f"🗑️ Step 2: Deleting {len(inventory['file_records'])} file records...")
                    transaction.aql.execute(
                        "FOR k IN @keys REMOVE k IN @@files_collection OPTIONS { ignoreErrors: true }",
                        bind_vars={
                            "keys": inventory["file_records"],
                            "@files_collection": CollectionNames.FILES.value
                        }
                    )
                    self.logger.info(f"✅ Deleted {len(inventory['file_records'])} file records")

                # Step 4: Delete all records
                if all_record_keys:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(all_record_keys)} records...")
                    transaction.aql.execute(
                        "FOR k IN @keys REMOVE k IN @@records_collection OPTIONS { ignoreErrors: true }",
                        bind_vars={
                            "keys": all_record_keys,
                            "@records_collection": CollectionNames.RECORDS.value
                        }
                    )
                    self.logger.info(f"✅ Deleted {len(all_record_keys)} records")

                # Step 5: Delete all folders
                if inventory["folders"]:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(inventory['folders'])} folders...")
                    transaction.aql.execute(
                        "FOR k IN @keys REMOVE k IN @@files_collection OPTIONS { ignoreErrors: true }",
                        bind_vars={
                            "keys": inventory["folders"],
                            "@files_collection": CollectionNames.FILES.value
                        }
                    )
                    self.logger.info(f"✅ Deleted {len(inventory['folders'])} folders")

                # Step 6: Delete the KB document itself
                self.logger.info(f"🗑️ Step 5: Deleting KB document {kb_id}...")
                transaction.aql.execute(
                    "REMOVE @kb_id IN @@recordGroups_collection OPTIONS { ignoreErrors: true }",
                    bind_vars={
                        "kb_id": kb_id,
                        "@recordGroups_collection": CollectionNames.RECORD_GROUPS.value
                    }
                )

                # Step 7: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete deletion transaction...")
                    await asyncio.to_thread(lambda: transaction.commit_transaction())
                    self.logger.info("✅ Transaction committed successfully!")

                # Step 8: Publish delete events for all records (after successful transaction)
                try:
                    delete_event_tasks = []
                    for record_data in records_with_details:
                        delete_payload = await self._create_deleted_record_event_payload(
                            record_data["record"], record_data["file_record"]
                        )
                        if delete_payload:
                            delete_event_tasks.append(
                                self._publish_record_event("deleteRecord", delete_payload)
                            )

                    if delete_event_tasks:
                        await asyncio.gather(*delete_event_tasks, return_exceptions=True)
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records from KB deletion")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish KB deletion events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"🎉 KB {kb_id} and ALL contents deleted successfully.")
                return True

            except Exception as db_error:
                self.logger.error(f"❌ Database error during KB deletion: {str(db_error)}")
                if should_commit and transaction:
                    await asyncio.to_thread(lambda: transaction.abort_transaction())
                    self.logger.info("🔄 Transaction aborted due to error")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete KB {kb_id} completely: {str(e)}")
            return False

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> bool:
        """
        Delete a folder with ALL nested content and publish delete events for all records
        Fixed to handle edge deletion properly and avoid "document not found" errors
        """
        try:
            # Create transaction if not provided
            should_commit = False
            if transaction is None:
                should_commit = True
                try:
                    transaction = self.db.begin_transaction(
                        write=[
                            CollectionNames.FILES.value,
                            CollectionNames.RECORDS.value,
                            CollectionNames.RECORD_RELATIONS.value,
                            CollectionNames.BELONGS_TO.value,
                            CollectionNames.IS_OF_TYPE.value,
                        ]
                    )
                    self.logger.info(f"🔄 Transaction created for complete folder {folder_id} deletion")
                except Exception as tx_error:
                    self.logger.error(f"❌ Failed to create transaction: {str(tx_error)}")
                    return False

            try:
                # Step 1: Get complete inventory including all records for event publishing
                inventory_query = """
                LET target_folder = DOCUMENT("files", @folder_id)
                FILTER target_folder != null
                FILTER target_folder.isFile == false
                FILTER target_folder.recordGroupId == @kb_id
                // Get ALL subfolders recursively using traversal
                LET all_subfolders = (
                    FOR v, e, p IN 1..20 OUTBOUND target_folder._id @@record_relations
                        FILTER e.relationshipType == "PARENT_CHILD"
                        FILTER v.isFile == false
                        RETURN v._key
                )
                // All folders to delete (target + subfolders)
                LET all_folders = APPEND([target_folder._key], all_subfolders)
                // Get ALL records in target folder and all subfolders with their file records
                LET all_folder_records_with_details = (
                    FOR folder_key IN all_folders
                        FOR folder_record_edge IN @@record_relations
                            FILTER folder_record_edge._from == CONCAT('files/', folder_key)
                            FILTER folder_record_edge.relationshipType == "PARENT_CHILD"
                            LET record = DOCUMENT(folder_record_edge._to)
                            FILTER record != null
                            FILTER record.isFile != false  // Ensure it's a record
                            // Get associated file record
                            LET file_record = FIRST(
                                FOR record_file_edge IN @@is_of_type
                                    FILTER record_file_edge._from == record._id
                                    LET fileRecord = DOCUMENT(record_file_edge._to)
                                    FILTER fileRecord != null
                                    RETURN fileRecord
                            )
                            RETURN {
                                record: record,
                                file_record: file_record
                            }
                )
                // Get file records associated with all these records
                LET all_file_records = (
                    FOR record_data IN all_folder_records_with_details
                        FILTER record_data.file_record != null
                        RETURN record_data.file_record._key
                )
                RETURN {
                    folder_exists: target_folder != null,
                    target_folder: target_folder._key,
                    all_folders: all_folders,
                    subfolders: all_subfolders,
                    records_with_details: all_folder_records_with_details,
                    file_records: all_file_records,
                    total_folders: LENGTH(all_folders),
                    total_subfolders: LENGTH(all_subfolders),
                    total_records: LENGTH(all_folder_records_with_details),
                    total_file_records: LENGTH(all_file_records)
                }
                """

                cursor = transaction.aql.execute(inventory_query, bind_vars={
                    "folder_id": folder_id,
                    "kb_id": kb_id,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                })

                inventory = next(cursor, {})

                if not inventory.get("folder_exists"):
                    self.logger.warning(f"⚠️ Folder {folder_id} not found in KB {kb_id}")
                    if should_commit:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                    return False

                records_with_details = inventory.get("records_with_details", [])
                self.logger.info(f"📋 Folder {folder_id} deletion inventory:")
                self.logger.info(f"   📁 Total folders (including target): {inventory['total_folders']}")
                self.logger.info(f"   📄 Records: {inventory['total_records']}")
                self.logger.info(f"   🗂️ File records: {inventory['total_file_records']}")

                # Step 2: Delete ALL edges in separate queries to avoid "access after data-modification" error
                self.logger.info("🗑️ Step 1: Deleting all edges...")

                all_record_keys = [rd["record"]["_key"] for rd in records_with_details]

                # Delete record relation edges
                if all_record_keys or inventory["all_folders"]:
                    record_relations_delete = """
                    LET record_edges = (
                        FOR record_key IN @all_records
                            FOR rec_edge IN @@record_relations
                                FILTER rec_edge._from == CONCAT('records/', record_key) OR rec_edge._to == CONCAT('records/', record_key)
                                RETURN rec_edge._key
                    )
                    LET folder_edges = (
                        FOR folder_key IN @all_folders
                            FOR folder_edge IN @@record_relations
                                FILTER folder_edge._from == CONCAT('files/', folder_key) OR folder_edge._to == CONCAT('files/', folder_key)
                                RETURN folder_edge._key
                    )
                    LET all_relation_edges = APPEND(record_edges, folder_edges)
                    FOR edge_key IN all_relation_edges
                        REMOVE edge_key IN @@record_relations OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(record_relations_delete, bind_vars={
                        "all_records": all_record_keys,
                        "all_folders": inventory["all_folders"],
                        "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                    })
                    self.logger.info("✅ Deleted record relation edges")

                # Delete is_of_type edges
                if all_record_keys:
                    is_of_type_delete = """
                    LET type_edges = (
                        FOR record_key IN @all_records
                            FOR type_edge IN @@is_of_type
                                FILTER type_edge._from == CONCAT('records/', record_key)
                                RETURN type_edge._key
                    )
                    FOR edge_key IN type_edges
                        REMOVE edge_key IN @@is_of_type OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(is_of_type_delete, bind_vars={
                        "all_records": all_record_keys,
                        "@is_of_type": CollectionNames.IS_OF_TYPE.value,
                    })
                    self.logger.info("✅ Deleted is_of_type edges")

                # Delete belongs_to_kb edges
                if all_record_keys or inventory["all_folders"]:
                    belongs_to_kb_delete = """
                    LET record_kb_edges = (
                        FOR record_key IN @all_records
                            FOR record_kb_edge IN @@belongs_to_kb
                                FILTER record_kb_edge._from == CONCAT('records/', record_key)
                                RETURN record_kb_edge._key
                    )
                    LET folder_kb_edges = (
                        FOR folder_key IN @all_folders
                            FOR folder_kb_edge IN @@belongs_to_kb
                                FILTER folder_kb_edge._from == CONCAT('files/', folder_key)
                                RETURN folder_kb_edge._key
                    )
                    LET all_kb_edges = APPEND(record_kb_edges, folder_kb_edges)
                    FOR edge_key IN all_kb_edges
                        REMOVE edge_key IN @@belongs_to_kb OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(belongs_to_kb_delete, bind_vars={
                        "all_records": all_record_keys,
                        "all_folders": inventory["all_folders"],
                        "@belongs_to_kb": CollectionNames.BELONGS_TO.value,
                    })
                    self.logger.info("✅ Deleted belongs_to_kb edges")

                # Step 3: Delete all file records with error handling
                if inventory["file_records"]:
                    self.logger.info(f"🗑️ Step 2: Deleting {len(inventory['file_records'])} file records...")

                    file_records_delete_query = """
                    FOR file_key IN @file_keys
                        REMOVE file_key IN @@files_collection OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(file_records_delete_query, bind_vars={
                        "file_keys": inventory["file_records"],
                        "@files_collection": CollectionNames.FILES.value,
                    })

                    self.logger.info(f"✅ Deleted {len(inventory['file_records'])} file records")

                # Step 4: Delete all records with error handling
                if all_record_keys:
                    self.logger.info(f"🗑️ Step 3: Deleting {len(all_record_keys)} records...")

                    records_delete_query = """
                    FOR record_key IN @record_keys
                        REMOVE record_key IN @@records_collection OPTIONS { ignoreErrors: true }
                    """

                    transaction.aql.execute(records_delete_query, bind_vars={
                        "record_keys": all_record_keys,
                        "@records_collection": CollectionNames.RECORDS.value,
                    })

                    self.logger.info(f"✅ Deleted {len(all_record_keys)} records")

                # Step 5: Delete all folders with error handling (deepest first)
                if inventory["all_folders"]:
                    self.logger.info(f"🗑️ Step 4: Deleting {len(inventory['all_folders'])} folders...")

                    # Sort folders by depth (deepest first) to avoid dependency issues
                    folders_delete_query = """
                    FOR folder_key IN @folder_keys
                        REMOVE folder_key IN @@files_collection OPTIONS { ignoreErrors: true }
                    """

                    # Reverse the folder list to delete children before parents
                    reversed_folders = list(reversed(inventory["all_folders"]))

                    transaction.aql.execute(folders_delete_query, bind_vars={
                        "folder_keys": reversed_folders,
                        "@files_collection": CollectionNames.FILES.value,
                    })

                    self.logger.info(f"✅ Deleted {len(inventory['all_folders'])} folders")

                # Step 6: Commit transaction
                if should_commit:
                    self.logger.info("💾 Committing complete folder deletion transaction...")
                    try:
                        await asyncio.to_thread(lambda: transaction.commit_transaction())
                        self.logger.info("✅ Transaction committed successfully!")
                    except Exception as commit_error:
                        self.logger.error(f"❌ Transaction commit failed: {str(commit_error)}")
                        try:
                            await asyncio.to_thread(lambda: transaction.abort_transaction())
                            self.logger.info("🔄 Transaction aborted after commit failure")
                        except Exception as abort_error:
                            self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                        return False

                # Step 7: Publish delete events for all records (after successful transaction)
                try:
                    delete_event_tasks = []
                    for record_data in records_with_details:
                        delete_payload = await self._create_deleted_record_event_payload(
                            record_data["record"], record_data["file_record"]
                        )
                        if delete_payload:
                            delete_event_tasks.append(
                                self._publish_record_event("deleteRecord", delete_payload)
                            )

                    if delete_event_tasks:
                        await asyncio.gather(*delete_event_tasks, return_exceptions=True)
                        self.logger.info(f"✅ Published delete events for {len(delete_event_tasks)} records from folder deletion")

                except Exception as event_error:
                    self.logger.error(f"❌ Failed to publish folder deletion events: {str(event_error)}")
                    # Don't fail the main operation for event publishing errors

                self.logger.info(f"🎉 Folder {folder_id} and ALL contents deleted successfully:")
                self.logger.info(f"   📁 {inventory['total_folders']} folders (including target)")
                self.logger.info(f"   📄 {inventory['total_records']} records")
                self.logger.info(f"   🗂️ {inventory['total_file_records']} file records")
                self.logger.info("   🔗 All associated edges")

                return True

            except Exception as db_error:
                self.logger.error(f"❌ Database error during folder deletion: {str(db_error)}")
                if should_commit and transaction:
                    try:
                        await asyncio.to_thread(lambda: transaction.abort_transaction())
                        self.logger.info("🔄 Transaction aborted due to error")
                    except Exception as abort_error:
                        self.logger.error(f"❌ Transaction abort failed: {str(abort_error)}")
                raise db_error

        except Exception as e:
            self.logger.error(f"❌ Failed to delete folder {folder_id} completely: {str(e)}")
            return False

    async def _check_name_conflict_in_parent(
        self,
        kb_id: str,
        parent_folder_id: Optional[str],
        item_name: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Dict:
        """
        Check if an item (folder or file) name already exists in the target parent location
        Handles different field names: folders have 'name', records have 'recordName'
        """
        try:
            db = transaction if transaction else self.db

            if parent_folder_id:
                # Check siblings in folder
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @parent_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET child = DOCUMENT(edge._to)
                    FILTER child != null
                    // Get the appropriate name field based on document type
                    LET child_name = child.isFile == false ? child.name : child.recordName
                    FILTER LOWER(child_name) == LOWER(@item_name)
                    RETURN {
                        id: child._key,
                        name: child_name,
                        type: child.isFile == false ? "folder" : "record",
                        document_type: child.isFile == false ? "files" : "records"
                    }
                """

                cursor = db.aql.execute(query, bind_vars={
                    "parent_from": f"files/{parent_folder_id}",
                    "item_name": item_name,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })
            else:
                # Check siblings in KB root
                query = """
                FOR edge IN @@record_relations
                    FILTER edge._from == @kb_from
                    FILTER edge.relationshipType == "PARENT_CHILD"
                    LET child = DOCUMENT(edge._to)
                    FILTER child != null
                    // Get the appropriate name field based on document type
                    LET child_name = child.isFile == false ? child.name : child.recordName
                    FILTER LOWER(child_name) == LOWER(@item_name)
                    RETURN {
                        id: child._key,
                        name: child_name,
                        type: child.isFile == false ? "folder" : "record",
                        document_type: child.isFile == false ? "files" : "records"
                    }
                """

                cursor = db.aql.execute(query, bind_vars={
                    "kb_from": f"recordGroups/{kb_id}",
                    "item_name": item_name,
                    "@record_relations": CollectionNames.RECORD_RELATIONS.value,
                })

            conflicts = list(cursor)

            return {
                "has_conflict": len(conflicts) > 0,
                "conflicts": conflicts
            }

        except Exception as e:
            self.logger.error(f"❌ Failed to check name conflict: {str(e)}")
            return {"has_conflict": False, "conflicts": []}

    async def get_folder_by_kb_and_path(
        self,
        kb_id: str,
        folder_path: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> Optional[Dict]:
        """
        Get folder by KB ID + path (much faster than edge traversal)
        Uses the kbId field stored directly in folder records
        """
        try:
            db = transaction if transaction else self.db

            # Direct query using KB ID + path - very fast with proper indexing
            query = """
            FOR folder IN @@files_collection
                FILTER folder.recordGroupId == @kb_id
                FILTER folder.path == @folder_path
                FILTER folder.isFile == false
                RETURN folder
            """

            cursor = db.aql.execute(query, bind_vars={
                "kb_id": kb_id,
                "folder_path": folder_path,
                "@files_collection": CollectionNames.FILES.value,
            })

            result = next(cursor, None)

            if result:
                self.logger.debug(f"✅ Found folder: {folder_path} in KB {kb_id}")
            else:
                self.logger.debug(f"❌ Folder not found: {folder_path} in KB {kb_id}")

            return result

        except Exception as e:
            self.logger.error(f"❌ Failed to get folder by KB and path: {str(e)}")
            return None

    async def validate_folder_exists_in_kb(
        self,
        kb_id: str,
        folder_id: str,
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Validate folder exists in specific KB
        Uses direct KB ID check instead of edge traversal
        """
        try:
            db = transaction if transaction else self.db

            query = """
            FOR folder IN @@files_collection
                FILTER folder._key == @folder_id
                FILTER folder.recordGroupId == @kb_id
                FILTER folder.isFile == false
                RETURN true
            """

            cursor = db.aql.execute(query, bind_vars={
                "folder_id": folder_id,
                "kb_id": kb_id,
                "@files_collection": CollectionNames.FILES.value,
            })

            return next(cursor, False)

        except Exception as e:
            self.logger.error(f"❌ Failed to validate folder in KB: {str(e)}")
            return False

    async def upload_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
        parent_folder_id: Optional[str] = None,  # None = KB root, str = specific folder
    ) -> Dict:
        """
        - KB root upload (parent_folder_id=None)
        - Folder upload (parent_folder_id=folder_id)
        """
        try:
            upload_type = "folder" if parent_folder_id else "KB root"
            self.logger.info(f"🚀 Starting unified upload to {upload_type} in KB {kb_id}")
            self.logger.info(f"📊 Processing {len(files)} files")

            # Step 1: Validate user permissions and target location
            validation_result = await self._validate_upload_context(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                parent_folder_id=parent_folder_id
            )
            if not validation_result["valid"]:
                return validation_result

            # Step 2: Analyze folder structure relative to upload target
            folder_analysis = self._analyze_upload_structure(files, validation_result)
            self.logger.info(f"📁 Structure analysis: {folder_analysis['summary']}")

            # Step 3: Execute upload in single transaction
            result = await self._execute_upload_transaction(
                kb_id=kb_id,
                user_id=user_id,
                org_id=org_id,
                files=files,
                folder_analysis=folder_analysis,
                validation_result=validation_result
            )

            if result["success"]:
                return {
                    "success": True,
                    "message": self._generate_upload_message(result, upload_type),
                    "totalCreated": result["total_created"],
                    "foldersCreated": result["folders_created"],
                    "createdFolders": result["created_folders"],
                    "failedFiles": result["failed_files"],
                    "kbId": kb_id,
                    "parentFolderId": parent_folder_id,
                }
            else:
                return result

        except Exception as e:
            self.logger.error(f"❌ Unified upload failed: {str(e)}")
            return {"success": False, "reason": f"Upload failed: {str(e)}", "code": 500}
