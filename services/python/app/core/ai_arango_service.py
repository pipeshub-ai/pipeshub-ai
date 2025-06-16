"""ArangoDB service for interacting with the database"""

# pylint: disable=E1101, W0718
from typing import Dict, List, Optional

from arango import ArangoClient
from arango.database import TransactionDatabase

from app.config.configuration_service import ConfigurationService, config_node_constants
from app.config.utils.named_constants.arangodb_constants import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ArangoService:
    """ArangoDB service for interacting with the database"""

    def __init__(
        self, logger, arango_client: ArangoClient, config: ConfigurationService
    ):
        self.logger = logger
        self.logger.info("🚀 Initializing ArangoService")
        self.config_service = config
        self.client = arango_client
        self.db = None

    async def connect(self) -> bool:
        """Connect to ArangoDB and initialize collections"""
        try:
            self.logger.info("🚀 Connecting to ArangoDB...")
            arangodb_config = await self.config_service.get_config(
                config_node_constants.ARANGODB.value
            )
            arango_url = arangodb_config["url"]
            arango_user = arangodb_config["username"]
            arango_password = arangodb_config["password"]
            arango_db = arangodb_config["db"]

            if not isinstance(arango_url, str):
                raise ValueError("ArangoDB URL must be a string")
            if not self.client:
                self.logger.error("ArangoDB client not initialized")
                return False

            # Connect to system db to ensure our db exists
            self.logger.debug("Connecting to system db")
            sys_db = self.client.db(
                "_system", username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("System DB: %s", sys_db)

            # # Create our database if it doesn't exist
            # self.logger.debug("Checking if our database exists")
            # if not sys_db.has_database(arango_db):
            #     self.logger.info(
            #         "🚀 Database %s does not exist. Creating...",
            #         arango_db
            #     )
            #     sys_db.create_database(arango_db)
            #     self.logger.info("✅ Database created successfully")

            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=True
            )

            return True

        except Exception as e:
            self.logger.error("❌ Failed to connect to ArangoDB: %s", str(e))
            self.client = None
            self.db = None

            return False

    async def disconnect(self):
        """Disconnect from ArangoDB"""
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB")
            if self.client:
                self.client.close()
            self.logger.info("✅ Disconnected from ArangoDB successfully")
        except Exception as e:
            self.logger.error("❌ Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def get_key_by_external_file_id(
        self, external_file_id: str, transaction: Optional[TransactionDatabase] = None
    ) -> Optional[str]:
        """
        Get internal file key using the external file ID

        Args:
            external_file_id (str): External file ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external file ID %s", external_file_id
            )

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_file_id
                RETURN record._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_file_id": external_file_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external file ID %s",
                    external_file_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external file ID %s", external_file_id
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external file ID %s: %s",
                external_file_id,
                str(e),
            )
            return None

    async def get_key_by_external_message_id(
        self,
        external_message_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[str]:
        """
        Get internal message key using the external message ID

        Args:
            external_message_id (str): External message ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal message key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external message ID %s",
                external_message_id,
            )

            query = f"""
            FOR doc IN {CollectionNames.RECORDS.value}
                FILTER doc.externalRecordId == @external_message_id
                RETURN doc._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_message_id": external_message_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external message ID %s",
                    external_message_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external message ID %s",
                    external_message_id,
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external message ID %s: %s",
                external_message_id,
                str(e),
            )
            return None

    async def get_key_by_attachment_id(
        self,
        external_attachment_id: str,
        transaction: Optional[TransactionDatabase] = None,
    ) -> Optional[str]:
        """
        Get internal attachment key using the external attachment ID

        Args:
            external_attachment_id (str): External attachment ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal attachment key if found, None otherwise
        """
        try:
            self.logger.info(
                "🚀 Retrieving internal key for external attachment ID %s",
                external_attachment_id,
            )

            query = """
            FOR attachment IN attachments
                FILTER attachment.externalAttachmentId == @external_attachment_id
                RETURN attachment._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={"external_attachment_id": external_attachment_id}
            )
            result = next(cursor, None)

            if result:
                self.logger.info(
                    "✅ Successfully retrieved internal key for external attachment ID %s",
                    external_attachment_id,
                )
                return result
            else:
                self.logger.warning(
                    "⚠️ No internal key found for external attachment ID %s",
                    external_attachment_id,
                )
                return None

        except Exception as e:
            self.logger.error(
                "❌ Failed to retrieve internal key for external attachment ID %s: %s",
                external_attachment_id,
                str(e),
            )
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
    ):
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
    ):
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

    async def get_departments(self, org_id: Optional[str] = None) -> List[str]:
        """
        Get all departments that either have no org_id or match the given org_id

        Args:
            org_id (Optional[str]): Organization ID to filter departments

        Returns:
            List[str]: List of department names
        """
        query = f"""
            FOR department IN {CollectionNames.DEPARTMENTS.value}
                FILTER department.orgId == null OR department.orgId == '{org_id}'
                RETURN department.departmentName
        """
        cursor = self.db.aql.execute(query)
        return list(cursor)

    async def find_duplicate_files(
        self,
        file_key: str,
        md5_checksum: str,
        size_in_bytes: int,
        transaction: Optional[TransactionDatabase] = None,
    ) -> List[str]:
        """
        Find duplicate files based on MD5 checksum and file size

        Args:
            md5_checksum (str): MD5 checksum of the file
            size_in_bytes (int): Size of the file in bytes
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            List[str]: List of file keys that match both criteria
        """
        try:
            self.logger.info(
                "🔍 Finding duplicate files with MD5: %s and size: %d bytes",
                md5_checksum,
                size_in_bytes,
            )

            query = f"""
            FOR file IN {CollectionNames.FILES.value}
                FILTER file.md5Checksum == @md5_checksum
                AND file.sizeInBytes == @size_in_bytes
                AND file._key != @file_key
                LET record = (
                    FOR r IN {CollectionNames.RECORDS.value}
                        FILTER r._key == file._key
                        RETURN r
                )[0]
                RETURN record
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "md5_checksum": md5_checksum,
                    "size_in_bytes": size_in_bytes,
                    "file_key": file_key
                }
            )

            duplicate_records = list(cursor)

            if duplicate_records:
                self.logger.info(
                    "✅ Found %d duplicate record(s) matching criteria",
                    len(duplicate_records)
                )
                self.logger.info(f"Duplicate records: {[record['_key'] for record in duplicate_records]}")
            else:
                self.logger.info("✅ No duplicate records found")

            return duplicate_records

        except Exception as e:
            self.logger.error(
                "❌ Failed to find duplicate files: %s",
                str(e)
            )
            if transaction:
                raise
            return []

    async def copy_document_relationships(self, source_key: str, target_key: str):
        """
        Copy all relationships (edges) from source document to target document.
        This includes departments, categories, subcategories, languages, and topics.

        Args:
            source_key (str): Key of the source document
            target_key (str): Key of the target document
        """
        try:
            self.logger.info(f"🚀 Copying relationships from {source_key} to {target_key}")

            # Define collections to copy relationships from
            edge_collections = [
                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                CollectionNames.BELONGS_TO_CATEGORY.value,
                CollectionNames.BELONGS_TO_LANGUAGE.value,
                CollectionNames.BELONGS_TO_TOPIC.value
            ]

            for collection in edge_collections:
                # Find all edges from source document
                query = f"""
                FOR edge IN {collection}
                    FILTER edge._from == @source_doc
                    RETURN {{
                        from: edge._from,
                        to: edge._to,
                        timestamp: edge.createdAtTimestamp
                    }}
                """

                cursor = self.db.aql.execute(
                    query,
                    bind_vars={
                        "source_doc": f"{CollectionNames.RECORDS.value}/{source_key}"
                    }
                )

                edges = list(cursor)

                if edges:
                    # Create new edges for target document
                    new_edges = []
                    for edge in edges:
                        new_edge = {
                            "_from": f"{CollectionNames.RECORDS.value}/{target_key}",
                            "_to": edge["to"],
                            "createdAtTimestamp": get_epoch_timestamp_in_ms()
                        }
                        new_edges.append(new_edge)

                    # Batch create the new edges
                    await self.batch_create_edges(new_edges, collection)
                    self.logger.info(
                        f"✅ Copied {len(new_edges)} relationships from collection {collection}"
                    )

            self.logger.info(f"✅ Successfully copied all relationships to {target_key}")

        except Exception as e:
            self.logger.error(
                f"❌ Error copying relationships from {source_key} to {target_key}: {str(e)}"
            )
            raise

    async def get_records_by_virtual_record_id(
        self,
        virtual_record_id: str,
        accessible_record_ids: Optional[List[str]] = None
    ) -> List[str]:
        """
        Get all record keys that have the given virtualRecordId.
        Optionally filter by a list of record IDs.

        Args:
            virtual_record_id (str): Virtual record ID to look up
            record_ids (Optional[List[str]]): Optional list of record IDs to filter by

        Returns:
            List[str]: List of record keys that match the criteria
        """
        try:
            self.logger.info(
                "🔍 Finding records with virtualRecordId: %s", virtual_record_id
            )

            # Base query
            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.virtualRecordId == @virtual_record_id
            """

            # Add optional filter for record IDs
            if accessible_record_ids:
                query += """
                AND record._key IN @accessible_record_ids
                """

            query += """
                RETURN record._key
            """

            bind_vars = {"virtual_record_id": virtual_record_id}
            if accessible_record_ids:
                bind_vars["accessible_record_ids"] = accessible_record_ids

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            results = list(cursor)

            self.logger.info(
                "✅ Found %d records with virtualRecordId %s",
                len(results),
                virtual_record_id
            )
            return results

        except Exception as e:
            self.logger.error(
                "❌ Error finding records with virtualRecordId %s: %s",
                virtual_record_id,
                str(e)
            )
            return []

    async def get_documents_by_status(self, collection: str, status: str) -> List[Dict]:
        """
        Get all documents with a specific indexing status

        Args:
            collection (str): Collection name
            status (str): Status to filter by

        Returns:
            List[Dict]: List of matching documents
        """
        query = """
        FOR doc IN @@collection
            FILTER doc.indexingStatus == @status
            RETURN doc
        """

        bind_vars = {
            "@collection": collection,
            "status": status
        }

        cursor = self.db.aql.execute(query, bind_vars=bind_vars)
        return list(cursor)


    async def delete_blocks_by_virtual_record_id(self, virtual_record_id: str) -> bool:
        """
        Delete all blocks that have the given virtualRecordId and their associated edges.
        Uses a transaction to ensure atomicity of all operations.
        """
        try:
            self.logger.info("🗑️ Deleting blocks with virtualRecordId: %s", virtual_record_id)

            # Define collections that will be accessed
            edge_collections = [
                CollectionNames.BELONGS_TO_DEPARTMENT.value,
                CollectionNames.BELONGS_TO_CATEGORY.value,
                CollectionNames.BELONGS_TO_LANGUAGE.value,
                CollectionNames.BELONGS_TO_TOPIC.value,
                CollectionNames.HAS_ENTITY.value
            ]

            # Entity Node Collections
            entity_collections = [
                CollectionNames.ENTITY_ORGANIZATIONS.value,
                CollectionNames.ENTITY_LOCATIONS.value,
                CollectionNames.ENTITY_PRODUCTS.value,
                CollectionNames.ENTITY_EVENTS.value,
                CollectionNames.ENTITY_DATES.value,
                CollectionNames.ENTITY_DURATIONS.value,
                CollectionNames.ENTITY_MONETARY.value,
                CollectionNames.ENTITY_PERCENTAGES.value,
                CollectionNames.ENTITY_CONTACT_INFO.value,
                CollectionNames.ENTITY_DOCUMENT_REFS.value,
                CollectionNames.ENTITY_PROJECTS.value,
                CollectionNames.ENTITY_TECHNOLOGIES.value,
                CollectionNames.ENTITY_LEGAL_TERMS.value,
                CollectionNames.ENTITY_JOB_TITLES.value,
                CollectionNames.ENTITY_SYSTEM_IDS.value
            ]

            # Start transaction with write access to necessary collections
            transaction = self.db.begin_transaction(
                read=[CollectionNames.BLOCKS.value] + edge_collections + entity_collections,
                write=[CollectionNames.BLOCKS.value] + edge_collections + entity_collections
            )

            try:
                query = f"""
                // Get all block keys first
                LET block_keys = (
                    FOR block IN {CollectionNames.BLOCKS.value}
                        FILTER block.virtualRecordId == @virtual_record_id
                        RETURN block._key
                )


                // Find and delete all entities connected to these blocks
                LET entity_deletions = (
                    FOR block_key IN block_keys
                        FOR edge IN hasEntity
                            FILTER edge._from == CONCAT("blocks/", block_key)
                            LET entity_id = edge._to
                            LET entity = DOCUMENT(entity_id)
                            LET coll = PARSE_IDENTIFIER(entity_id).collection

                            RETURN (
                                coll == "entityOrganizations"    ? (REMOVE entity IN entityOrganizations  OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityLocations"        ? (REMOVE entity IN entityLocations        OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityProducts"         ? (REMOVE entity IN entityProducts         OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityEvents"           ? (REMOVE entity IN entityEvents           OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityDates"            ? (REMOVE entity IN entityDates            OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityDurations"        ? (REMOVE entity IN entityDurations        OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityMonetary"         ? (REMOVE entity IN entityMonetary         OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityPercentages"      ? (REMOVE entity IN entityPercentages      OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityContactInfo"      ? (REMOVE entity IN entityContactInfo      OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityDocumentRefs"     ? (REMOVE entity IN entityDocumentRefs     OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityProjects"         ? (REMOVE entity IN entityProjects         OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityTechnologies"     ? (REMOVE entity IN entityTechnologies     OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityLegalTerms"       ? (REMOVE entity IN entityLegalTerms       OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entityJobTitles"        ? (REMOVE entity IN entityJobTitles        OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                coll == "entitySystemIds"        ? (REMOVE entity IN entitySystemIds        OPTIONS {{"ignoreErrors": true}}   RETURN 1)[0] :
                                null
                            )
                )

                // Delete edges from each collection
                LET dept_edges = (
                    FOR block_key IN block_keys
                        FOR edge IN {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                            FILTER edge._from == CONCAT("{CollectionNames.BLOCKS.value}/", block_key)
                            REMOVE edge IN {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                )

                LET cat_edges = (
                    FOR block_key IN block_keys
                        FOR edge IN {CollectionNames.BELONGS_TO_CATEGORY.value}
                            FILTER edge._from == CONCAT("{CollectionNames.BLOCKS.value}/", block_key)
                            REMOVE edge IN {CollectionNames.BELONGS_TO_CATEGORY.value}
                )

                LET lang_edges = (
                    FOR block_key IN block_keys
                        FOR edge IN {CollectionNames.BELONGS_TO_LANGUAGE.value}
                            FILTER edge._from == CONCAT("{CollectionNames.BLOCKS.value}/", block_key)
                            REMOVE edge IN {CollectionNames.BELONGS_TO_LANGUAGE.value}
                )

                LET topic_edges = (
                    FOR block_key IN block_keys
                        FOR edge IN {CollectionNames.BELONGS_TO_TOPIC.value}
                            FILTER edge._from == CONCAT("{CollectionNames.BLOCKS.value}/", block_key)
                            REMOVE edge IN {CollectionNames.BELONGS_TO_TOPIC.value}
                )

                LET entity_edges = (
                    FOR block_key IN block_keys
                        FOR edge IN {CollectionNames.HAS_ENTITY.value}
                            FILTER edge._from == CONCAT("{CollectionNames.BLOCKS.value}/", block_key)
                            REMOVE edge IN {CollectionNames.HAS_ENTITY.value}
                )

                // Delete the blocks themselves
                LET deleted_blocks = (
                    FOR block_key IN block_keys
                        REMOVE block_key IN {CollectionNames.BLOCKS.value}
                        RETURN OLD
                )

                RETURN {{
                    "deletedBlocks": LENGTH(deleted_blocks),
                    "deletedEntities": LENGTH(entity_deletions)
                }}
                """
                self.logger.info(f"🚀 Executing query: {query}")
                cursor = transaction.aql.execute(
                    query,
                    bind_vars={
                        "virtual_record_id": virtual_record_id
                    }
                )
                self.logger.info("🚀 Query executed successfully")
                result = next(cursor)
                self.logger.info(f"🚀 Result: {result}")

                transaction.commit_transaction()

                self.logger.info(
                    "✅ Successfully deleted %d blocks and %d entities with virtualRecordId %s",
                    result["deletedBlocks"],
                    result["deletedEntities"],
                    virtual_record_id
                )
                return True

            except Exception as e:
                transaction.abort_transaction()
                raise e

        except Exception as e:
            self.logger.error(
                "❌ Failed to delete blocks with virtualRecordId %s: %s",
                virtual_record_id,
                str(e)
            )
            return False

    async def get_summary_document_id(self, record_id: str) -> str | None:
        """Get summary document ID from record ID

        Args:
            record_id (str): Record ID (key) of the document

        Returns:
            str | None: Summary document ID if found, None otherwise
        """
        try:
            self.logger.debug("🔍 Getting summary document ID for record: %s", record_id)

            # Query to get summaryDocumentId from records collection
            query = f"""
            FOR doc IN {CollectionNames.RECORDS.value}
            FILTER doc._key == @record_id
            RETURN doc.summaryDocumentId
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={"record_id": record_id}
            )

            try:
                summary_doc_id = cursor.next()
                if not summary_doc_id:
                    self.logger.warning("⚠️ No summary document ID found for record: %s", record_id)
                    return None

                return summary_doc_id

            except StopIteration:
                self.logger.warning("⚠️ Record not found: %s", record_id)
                return None

        except Exception as e:
            self.logger.error("❌ Error getting summary document ID: %s", str(e))
            return None
