import traceback
from typing import Dict

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    RecordRelations,
    RecordTypes,
)
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.sources.google.google_drive.file_processor import process_drive_file
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


class DriveChangeHandler:
    def __init__(self, logger, config_service: ConfigurationService, arango_service) -> None:
        self.logger = logger
        self.config_service = config_service
        self.arango_service = arango_service

    async def process_change(self, change: Dict, user_service, org_id, user_id) -> None:
        """Process a single change with revision checking"""
        txn = None
        try:
            self.logger.info(f"user_id: {user_id}")
            self.logger.info(f"change: {change}")
            file_id = change.get("fileId")
            if not file_id:
                self.logger.warning("⚠️ Change missing fileId")
                return

            new_file = await user_service.batch_fetch_metadata_and_permissions(
                [file_id]
            )
            new_file = new_file[0]
            if not new_file:
                self.logger.warning(f"File not found in database: {file_id}")
                return
            cursor = self.arango_service.db.aql.execute(
                f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @file_id RETURN doc._key",
                bind_vars={"file_id": file_id},
            )
            file_key = next(cursor, None)
            self.logger.info(f"🚀 File key: {file_key}")

            if file_key:
                # Retrieve file and record using file_key
                file_query = f"""
                RETURN DOCUMENT({CollectionNames.FILES.value}, @file_key)
                """
                record_query = f"""
                RETURN DOCUMENT({CollectionNames.RECORDS.value}, @file_key)
                """

                file_result = self.arango_service.db.aql.execute(
                    file_query, bind_vars={"file_key": file_key}
                )
                file_result = next(file_result, None)

                record_result = self.arango_service.db.aql.execute(
                    record_query, bind_vars={"file_key": file_key}
                )
                record_result = next(record_result, None)

                db_file = file_result if file_result else None
                db_record = record_result if record_result else None

                if not db_file or not db_record:
                    self.logger.warning(
                        f"❌ Could not find file or record for key: {file_key}"
                    )
                    return

            removed = change.get("removed", False)

            self.logger.info(f"🚀 New file: {new_file}")
            is_trashed = new_file.get("trashed", False)

            self.logger.info(
                f"""
            🔄 Processing change:
            - File key: {file_key}
            - File ID: {file_id}
            - Name: {new_file.get('name', 'Unknown')}
            - Removed: {removed}
            - Trashed: {is_trashed}
            """
            )

            txn = self.arango_service.db.begin_transaction(
                read=[
                    CollectionNames.FILES.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USERS.value,
                    CollectionNames.GROUPS.value,
                    CollectionNames.ORGS.value,
                    CollectionNames.ANYONE.value,
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.BELONGS_TO_DEPARTMENT.value,
                    CollectionNames.BELONGS_TO_CATEGORY.value,
                    CollectionNames.BELONGS_TO_LANGUAGE.value,
                    CollectionNames.BELONGS_TO_TOPIC.value,
                ],
                write=[
                    CollectionNames.FILES.value,
                    CollectionNames.MAILS.value,
                    CollectionNames.RECORDS.value,
                    CollectionNames.RECORD_RELATIONS.value,
                    CollectionNames.IS_OF_TYPE.value,
                    CollectionNames.USERS.value,
                    CollectionNames.GROUPS.value,
                    CollectionNames.ORGS.value,
                    CollectionNames.ANYONE.value,
                    CollectionNames.PERMISSIONS.value,
                    CollectionNames.BELONGS_TO.value,
                    CollectionNames.BELONGS_TO_DEPARTMENT.value,
                    CollectionNames.BELONGS_TO_CATEGORY.value,
                    CollectionNames.BELONGS_TO_LANGUAGE.value,
                    CollectionNames.BELONGS_TO_TOPIC.value,
                ],
            )

            if not file_key:
                if removed or is_trashed:
                    change_type = ""
                else:
                    await self.handle_insert(new_file, org_id, transaction=txn)
                    change_type = EventTypes.NEW_RECORD.value
            else:
                if removed or is_trashed:
                    await self.handle_removal(db_file, db_record, transaction=txn)
                    change_type = EventTypes.DELETE_RECORD.value
                else:
                    if not new_file:
                        return
                    needs_update_var, reindex_var = await self.needs_update(
                        new_file, db_file, db_record, transaction=txn
                    )
                    if needs_update_var:
                        await self.handle_update(
                            new_file, db_file, db_record, org_id, transaction=txn
                        )
                        if reindex_var:
                            change_type = EventTypes.UPDATE_RECORD.value
                        else:
                            change_type = ""
                    else:
                        self.logger.info(
                            f"✅ File {file_id} is up to date, skipping update"
                        )
                        change_type = ""

            txn.commit_transaction()
            txn = None
            self.logger.info("Transaction committed for file: %s", {file_id})

            # SEND KAFKA EVENT FOR REINDEXING
            self.logger.info(f"🚀 Change: {change_type}")
            if (
                change_type == EventTypes.NEW_RECORD.value
                or change_type == EventTypes.UPDATE_RECORD.value
            ):
                file_key = await self.arango_service.get_key_by_external_file_id(
                    file_id
                )
                self.logger.info(f"🚀 File key: {file_key}")
                record = await self.arango_service.get_document(
                    file_key, CollectionNames.RECORDS.value
                )
                file = await self.arango_service.get_document(
                    file_key, CollectionNames.FILES.value
                )

                if file:
                    extension = file.get("extension")
                    mime_type = file.get("mimeType")

            else:
                record = {}
                file = {}
                extension = None
                mime_type = None

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            connector_endpoint = endpoints.get("connectors").get("endpoint", DefaultEndpoints.CONNECTOR_ENDPOINT.value)

            reindex_event = None

            # INSERTION
            if change_type == EventTypes.NEW_RECORD.value:
                reindex_event = {
                    "orgId": org_id,
                    "recordId": file_key,
                    "recordName": record.get("recordName"),
                    "recordVersion": 0,
                    "recordType": record.get("recordType"),
                    "eventType": change_type,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "extension": extension,
                    "mimeType": mime_type,
                    "createdAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("createdTime"))
                    ),
                    "modifiedAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("modifiedTime"))
                    ),
                }

            # UPDATION
            elif change_type == EventTypes.UPDATE_RECORD.value:
                reindex_event = {
                    "orgId": org_id,
                    "recordId": file_key,
                    "virtualRecordId": db_record.get("virtualRecordId", None),
                    "recordName": db_record.get("recordName", ""),
                    "recordVersion": 0,
                    "recordType": db_record.get("recordType", ""),
                    "eventType": change_type,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "extension": new_file.get("extension"),
                    "mimeType": new_file.get("mimeType"),
                    "createdAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("createdTime"))
                    ),
                    "modifiedAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("modifiedTime"))
                    ),
                }

            # DELETION
            elif change_type == EventTypes.DELETE_RECORD.value:
                reindex_event = {
                    "orgId": org_id,
                    "recordId": file_key,
                    "virtualRecordId": db_record.get("virtualRecordId", None),
                    "recordName": db_record.get("recordName", ""),
                    "recordVersion": 0,
                    "recordType": db_record.get("recordType", ""),
                    "eventType": change_type,
                    "signedUrlRoute": f"{connector_endpoint}/api/v1/{org_id}/{user_id}/drive/record/{file_key}/signedUrl",
                    "connectorName": Connectors.GOOGLE_DRIVE.value,
                    "origin": OriginTypes.CONNECTOR.value,
                    "extension": extension,
                    "mimeType": mime_type,
                    "createdAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("createdTime"))
                    ),
                    "modifiedAtSourceTimestamp": int(
                        parse_timestamp(new_file.get("modifiedTime"))
                    ),
                }

            else:
                self.logger.info("NO CHANGE DETECTED. NO KAFKA EVENT SENT")

            if reindex_event:
                await self.arango_service.kafka_service.send_event_to_kafka(
                    reindex_event
                )
                self.logger.info("📨 Sent Kafka reindexing event for file %s", file_id)

        except Exception as e:
            if txn:
                txn.abort_transaction()
                txn = None
            self.logger.error(f"❌ Error processing change: {str(e)}")
            traceback.print_exc()

    async def needs_update(
        self, updated_file, existing_file, existing_record, transaction
    ) -> bool:
        """Check if file needs update based on revision"""
        try:

            self.logger.info(
                "🚀 Checking if file needs update %s, %s",
                updated_file.get("id"),
                updated_file.get("name"),
            )
            if not existing_file:
                self.logger.info("🟢 File doesn't exist in DB")
                return True, True

            # Extract permissions from updated file
            new_permissions = updated_file.get("permissions", [])

            # Get existing permissions from database
            existing_permissions = await self.arango_service.get_file_permissions(
                existing_file["_key"], transaction
            )
            existing_permissions = existing_permissions if existing_permissions else []

            self.logger.info("🚀 Existing permissions: %s", existing_permissions)

            # Compare basic metadata first
            latest_revision_id = updated_file.get("headRevisionId")
            latest_file_name = updated_file.get("name")
            latest_parents = updated_file.get("parents", [])
            latest_modified_at = int(
                parse_timestamp(updated_file.get("modifiedTime"))
            )
            db_revision_id = existing_record.get("externalRevisionId")
            db_file_name = existing_file.get("name")
            db_parents = await self.arango_service.get_file_parents(
                existing_file["_key"], transaction
            )
            db_modified_at = existing_record.get("sourceLastModifiedTimestamp")

            self.logger.info(
                "Latest revision ID: %s, DB revision ID: %s, Latest parents: %s, "
                "DB parents: %s, Latest file name: %s, DB file name: %s, Latest modified at: %s, DB modified at: %s",
                latest_revision_id,
                db_revision_id,
                latest_parents,
                db_parents,
                latest_file_name,
                db_file_name,
                latest_modified_at,
                db_modified_at,
            )

            # Check permissions changes
            permissions_changed = False
            if len(new_permissions) != len(existing_permissions):
                self.logger.info("🟢 Number of permissions changed")
                permissions_changed = True
            else:
                # Compare each permission's key attributes
                for new_perm in new_permissions:
                    matching_perm = next(
                        (
                            p
                            for p in existing_permissions
                            if p["externalPermissionId"] == new_perm["id"]
                        ),
                        None,
                    )
                    if not matching_perm:
                        permissions_changed = True
                        break
                    # Compare relevant permission attributes
                    if (
                        new_perm.get("role") != matching_perm.get("role")
                        or new_perm.get("Type") != matching_perm.get("type")
                        or new_perm.get("emailAddress")
                        != matching_perm.get("emailAddress")
                    ):
                        permissions_changed = True
                        break

            fallback = False
            if not latest_revision_id or not db_revision_id:
                self.logger.info("🟢 No revision found")
                fallback = True
                if not latest_modified_at or not db_modified_at:
                    self.logger.info("🟢 No modified at found")
                    return False, False

            if not latest_file_name or not db_file_name:
                self.logger.info("🟢 No file name found")
                return False, False

            if not latest_parents or not db_parents:
                self.logger.info("🟢 No parents found")
                return False, False

            if fallback:
                needs_update_var = (
                    db_modified_at != latest_modified_at
                    or db_file_name != latest_file_name
                    or db_parents != latest_parents
                    or permissions_changed
                )

                reindex_var = db_modified_at != latest_modified_at
            else:
                needs_update_var = (
                    db_revision_id != latest_revision_id
                    or db_file_name != latest_file_name
                    or db_parents != latest_parents
                    or permissions_changed
                )

                reindex_var = db_revision_id != latest_revision_id

            if permissions_changed:
                self.logger.info("🟢 Permissions have changed")

            self.logger.info("🟢 Needs update: %s", needs_update_var)
            self.logger.info("🟢 Reindex: %s", reindex_var)
            return needs_update_var, reindex_var

        except Exception as e:
            self.logger.error(
                "❌ Error comparing updates for file %s: %s", db_file_name, str(e)
            )
            return False, False

    async def handle_removal(self, existing_file, existing_record, transaction=None) -> None:
        """Handle file removal or access loss"""
        try:
            self.logger.info(
                "🚀 Handling removal of record: %s: %s",
                existing_record["_key"],
                existing_record["recordName"],
            )

            db = transaction if transaction else self.arango_service.db

            # Get existing file data before marking as deleted
            if not existing_file:
                self.logger.warning("File %s not found in database")
                return

            query = """
                FOR a IN anyone
                    FILTER a.file_key == @file_key
                    REMOVE a IN anyone
                """
            db.aql.execute(query, bind_vars={"file_key": existing_file["_key"]})
            self.logger.info(
                "🗑️ Removed 'anyone' permission for file %s", existing_file["_key"]
            )

            existing_permissions = await self.arango_service.get_file_permissions(
                existing_file["_key"], transaction=transaction
            )
            self.logger.info("🚀 Existing permissions: %s", existing_permissions)

            # Remove permissions that no longer exist
            if existing_permissions:
                self.logger.info(
                    "🗑️ Removing %d obsolete permissions", len(existing_permissions)
                )
                for perm in existing_permissions:
                    query_permissions = """
                    FOR p IN permissions
                        FILTER p._key == @perm_key
                        REMOVE p IN permissions
                    """
                    db.aql.execute(
                        query_permissions, bind_vars={"perm_key": perm["_key"]}
                    )

            await self.arango_service.delete_records_and_relations(
                existing_record["_key"], hard_delete=True, transaction=transaction
            )
            self.logger.info("✅ Successfully handled removal of file")

        except Exception as e:
            self.logger.error(
                "❌ Error handling removal of record: %s: %s, %s",
                existing_record["_key"],
                existing_record["recordName"],
                str(e),
            )
            raise

    async def handle_insert(self, file_metadata, org_id, transaction) -> None:
        """Handle file insert"""
        try:
            self.logger.info(
                "🚀 Handling insert of file: %s", file_metadata.get("name")
            )
            permissions = file_metadata.get("permissions", [])
            file_id = file_metadata.get("id")
            existing_files = []

            db = transaction if transaction else self.arango_service.db

            # Check if file already exists in ArangoDB
            existing_file = self.arango_service.db.aql.execute(
                f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @file_id RETURN doc",
                bind_vars={"file_id": file_id},
            )
            existing = next(existing_file, None)

            if existing:
                self.logger.debug(f"File {file_id} already exists in ArangoDB")
                existing_files.append(file_id)

            else:
                file_record, record, is_of_type_record = await process_drive_file(file_metadata, org_id)
                self.logger.info("file_record: %s", file_record.to_dict())
                self.logger.info("record: %s", record.to_dict())
                recordRelations = []

                if "parents" in file_metadata:
                    for parent_id in file_metadata["parents"]:
                        parent_cursor = db.aql.execute(
                            f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @parent_id RETURN doc._key",
                            bind_vars={"parent_id": parent_id},
                        )

                        parent_key = next(parent_cursor, None)
                        file_key = file_record.key
                        self.logger.info(
                            "🚀 Parent key: %s, File key: %s", parent_key, file_key
                        )

                        if parent_key and file_key:
                            recordRelations.append(
                                {
                                    "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                                    "_to": f"{CollectionNames.RECORDS.value}/{file_key}",
                                    "relationType": RecordRelations.PARENT_CHILD.value,
                                }
                            )

                if file_record:
                    await self.arango_service.batch_upsert_nodes(
                        [file_record.to_dict()], CollectionNames.FILES.value, transaction=transaction
                    )
                if record:
                    await self.arango_service.batch_upsert_nodes(
                        [record.to_dict()], CollectionNames.RECORDS.value, transaction=transaction
                    )

                if is_of_type_record:
                    await self.arango_service.batch_create_edges(
                        [is_of_type_record],
                        collection=CollectionNames.IS_OF_TYPE.value,
                        transaction=transaction,
                    )

                if recordRelations:
                    await self.arango_service.batch_create_edges(
                        recordRelations,
                        collection=CollectionNames.RECORD_RELATIONS.value,
                        transaction=transaction,
                    )

                if permissions:
                    await self.arango_service.process_file_permissions(
                        org_id, file_record.key, permissions, transaction=transaction
                    )

                self.logger.info(
                    "✅ Successfully handled insert of file %s", file_record.key
                )

        except Exception as e:
            self.logger.error("❌ Error handling insert for file: %s", str(e))
            raise

    async def handle_update(
        self, updated_file, existing_file, existing_record, org_id, transaction
    ) -> None:
        """Handle file update or creation"""
        try:
            self.logger.info("🚀 Handling update of file: %s", updated_file.get("name"))

            permissions = updated_file.pop("permissions", [])
            # file_record, record, is_of_type_record = await process_drive_file(updated_file, org_id)
            file = {
                "_key": existing_file["_key"],
                "orgId": org_id,
                "name": str(updated_file.get("name")),
                "extension": updated_file.get("fileExtension", None),
                "mimeType": updated_file.get("mimeType", None),
                "sizeInBytes": int(updated_file.get("size", 0)),
                "webUrl": updated_file.get("webViewLink", None),
                "etag": updated_file.get("etag", None),
                "ctag": updated_file.get("ctag", None),
                "quickXorHash": updated_file.get("quickXorHash", None),
                "crc32Hash": updated_file.get("crc32Hash", None),
                "md5Checksum": updated_file.get("md5Checksum", None),
                "sha1Hash": updated_file.get("sha1Checksum", None),
                "sha256Hash": updated_file.get("sha256Checksum", None),
                "path": updated_file.get("path", None),
            }

            record = {
                "_key": existing_record["_key"],
                "orgId": org_id,
                "recordName": f'{file["name"]}',
                "recordType": RecordTypes.FILE.value,
                "version": 0,
                "externalRecordId": str(updated_file.get("id")),
                "externalRevisionId": updated_file.get("headRevisionId", None),
                "createdAtTimestamp": existing_record.get(
                    "createdAtTimestamp", get_epoch_timestamp_in_ms()
                ),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "sourceCreatedAtTimestamp": existing_record.get(
                    "sourceCreatedAtTimestamp",
                    int(parse_timestamp(updated_file.get("createdTime"))),
                ),
                "sourceLastModifiedTimestamp": existing_record.get(
                    "sourceLastModifiedTimestamp",
                    int(parse_timestamp(updated_file.get("modifiedTime"))),
                ),
                "origin": OriginTypes.CONNECTOR.value,
                "connectorName": Connectors.GOOGLE_DRIVE.value,
                "isArchived": False,
                "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                "isDeleted": existing_record.get("isDeleted", False),
                "virtualRecordId": existing_record.get("virtualRecordId", None),
                "indexingStatus": existing_record.get("indexingStatus", "NOT_STARTED"),
                "extractionStatus": existing_record.get("extractionStatus", "NOT_STARTED"),
                "lastIndexTimestamp": existing_record.get("lastIndexTimestamp", None),
                "lastExtractionTimestamp": existing_record.get("lastExtractionTimestamp", None),
                "isLatestVersion": True,
                "isDirty": False,
                "reason": None,
                "webUrl": updated_file.get("webViewLink", None),
                "mimeType": updated_file.get("mimeType", None),
            }
            is_of_type_record = {
                "_from": f"{CollectionNames.RECORDS.value}/{record['_key']}",
                "_to": f"{CollectionNames.FILES.value}/{file['_key']}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            await self.arango_service.batch_upsert_nodes(
                [file], CollectionNames.FILES.value, transaction=transaction
            )
            await self.arango_service.batch_upsert_nodes(
                [record], CollectionNames.RECORDS.value, transaction=transaction
            )
            if is_of_type_record:
                await self.arango_service.batch_create_edges(
                    [is_of_type_record],
                    collection=CollectionNames.IS_OF_TYPE.value,
                    transaction=transaction,
                )

            await self.update_relationships(
                existing_file["_key"], updated_file, transaction
            )

            if permissions:
                await self.arango_service.process_file_permissions(
                    org_id, existing_file["_key"], permissions, transaction=transaction
                )

            self.logger.info("✅ Successfully updated file %s", existing_file["_key"])

        except Exception as e:
            self.logger.error(
                "❌ Error handling update for file %s: %s",
                existing_file["_key"],
                str(e),
            )
            raise

    async def update_relationships(
        self, file_key: str, updated_file: Dict, transaction
    ) -> bool:
        """Update PARENT_CHILD relationships for a file

        Args:
            file_key (str): The file's key in ArangoDB
            updated_file (Dict): Updated file metadata from Google Drive
            transaction: ArangoDB transaction object

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            self.logger.info("🚀 Updating relationships for file: %s", file_key)

            # Get current parents from database
            current_parents = await self.arango_service.get_file_parents(
                file_key, transaction
            )
            # Get new parents from updated file
            new_parents = updated_file.get("parents", [])

            db = transaction if transaction else self.arango_service.db

            self.logger.info(
                "Current parents: %s, New parents: %s", current_parents, new_parents
            )

            # Find parents to remove and add
            parents_to_remove = set(current_parents) - set(new_parents)
            parents_to_add = set(new_parents) - set(current_parents)

            if not parents_to_remove and not parents_to_add:
                self.logger.info("✅ No parent changes needed for file %s", file_key)
                return True

            # Remove old relationships
            if parents_to_remove:
                self.logger.info(
                    "🗑️ Removing old parent relationships: %s", parents_to_remove
                )
                for parent_id in parents_to_remove:
                    # Get parent key from external ID
                    # Remove parent relationship for file
                    query = """
                    FOR edge IN @@recordRelations
                        FILTER edge._to == @file_id
                        REMOVE edge IN @@recordRelations
                    """
                    db.aql.execute(
                        query,
                        bind_vars={
                            "file_id": f"{CollectionNames.RECORDS.value}/{file_key}",
                            "@recordRelations": CollectionNames.RECORD_RELATIONS.value,
                        },
                    )

            # Add new relationships
            if parents_to_add:
                self.logger.info(
                    "📝 Adding new parent relationships: %s", parents_to_add
                )
                new_edges = []

                for parent_id in parents_to_add:
                    # Get parent key from external ID
                    parent_cursor = db.aql.execute(
                        f"FOR doc IN {CollectionNames.RECORDS.value} FILTER doc.externalRecordId == @parent_id RETURN doc._key",
                        bind_vars={"parent_id": parent_id},
                    )
                    parent_key = next(parent_cursor, None)

                    if parent_key:
                        new_edges.append(
                            {
                                "_from": f"{CollectionNames.RECORDS.value}/{parent_key}",
                                "_to": f"{CollectionNames.RECORDS.value}/{file_key}",
                                "relationType": RecordRelations.PARENT_CHILD.value,
                            }
                        )

                if new_edges:
                    await self.arango_service.batch_create_edges(
                        new_edges,
                        collection=CollectionNames.RECORD_RELATIONS.value,
                        transaction=transaction,
                    )

            self.logger.info(
                "✅ Successfully updated relationships for file %s", file_key
            )
            return True

        except Exception as e:
            self.logger.error(
                "❌ Failed to update relationships for file %s: %s", file_key, str(e)
            )
            if transaction:
                raise
            return False
