import uuid
from typing import Any, Dict, List, Optional

from arango import ArangoClient
from arango.database import TransactionDatabase

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    RecordTypes,
)
from app.config.constants.service import config_node_constants
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class ArangoService:
    """ArangoDB service for interacting with the database"""

    def __init__(
        self, logger, arango_client: ArangoClient, config_service: ConfigurationService
    ) -> None:
        self.logger = logger
        self.config_service = config_service
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
            self.logger.info("✅ Database created successfully")

            # Connect to our database
            self.logger.debug("Connecting to our database")
            self.db = self.client.db(
                arango_db, username=arango_user, password=arango_password, verify=True
            )
            self.logger.debug("Our DB: %s", self.db)

            return True
        except Exception as e:
            self.logger.error("❌ Failed to connect to ArangoDB: %s", str(e))
            self.client = None
            self.db = None

            return False

    async def disconnect(self) -> bool:
        """Disconnect from ArangoDB"""
        try:
            self.logger.info("🚀 Disconnecting from ArangoDB")
            if self.client:
                self.client.close()
            self.client = None
            self.db = None
            self.logger.info("✅ Disconnected from ArangoDB successfully")
            return True
        except Exception as e:
            self.logger.error("❌ Failed to disconnect from ArangoDB: %s", str(e))
            return False

    async def get_all_orgs(self, active: bool = True) -> list:
        """Get all organizations, optionally filtering by active status."""
        try:
            query = f"""
            FOR org IN {CollectionNames.ORGS.value}
            FILTER @active == false || org.isActive == true
            RETURN org
            """

            bind_vars = {"active": active}

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error(f"Failed to get organizations: {str(e)}")
            raise

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

    async def get_accessible_records(
        self, user_id: str, org_id: str, filters: dict = None
    ) -> list:
        """
        Get all records accessible to a user based on their permissions and apply filters

        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The org_id to filter anyone collection
            filters (dict): Optional filters for departments, categories, languages, topics etc.
                Format: {
                    'departments': [dept_ids],
                    'categories': [cat_ids],
                    'subcategories1': [subcat1_ids],
                    'subcategories2': [subcat2_ids],
                    'subcategories3': [subcat3_ids],
                    'languages': [language_ids],
                    'topics': [topic_ids],
                    'kb': [kb_ids]
                }
        """
        self.logger.info(
            f"Getting accessible records for user {user_id} in org {org_id} with filters {filters}"
        )

        try:
            # Extract KB IDs from filters if present
            kb_ids = filters.get("kb") if filters else None

            # First get counts separately
            query = f"""
            LET userDoc = FIRST(
                FOR user IN @@users
                FILTER user.userId == @userId
                RETURN user
            )

            LET directRecords = (
                FOR records IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS.value}
                RETURN DISTINCT records
            )

            LET groupRecords = (
                FOR group, edge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER edge.entityType == 'GROUP'
                FOR records IN 1..1 ANY group._id {CollectionNames.PERMISSIONS.value}
                RETURN DISTINCT records
            )

            LET orgRecords = (
                FOR org, edge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER edge.entityType == 'ORGANIZATION'
                FOR records IN 1..1 ANY org._id {CollectionNames.PERMISSIONS.value}
                RETURN DISTINCT records
            )

            LET directAndGroupRecords = UNION_DISTINCT(directRecords, groupRecords, orgRecords)
            """

            # Add KB records section with optional KB filtering
            if kb_ids:
                self.logger.info(f"🔍 Applying KB filtering for KBs: {kb_ids}")
                query += f"""
                LET kbRecords = (
                    FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS_TO_KB.value}
                    FILTER kb._key IN @kb_ids  // Filter by specific KB IDs
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )
                """
            else:
                # No KB filtering - get all accessible KB records
                query += f"""
                LET kbRecords = (
                    FOR kb IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS_TO_KB.value}
                    FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                    RETURN DISTINCT records
                )
                """

            query += """
            LET anyoneRecords = (
                FOR records IN @@anyone
                FILTER records.organization == @orgId
                FOR record IN @@records
                FILTER record != null
                    AND record._key == records.file_key
                RETURN record
            )

            LET allAccessibleRecords = UNIQUE(
                UNION(directAndGroupRecords, kbRecords, anyoneRecords)
            )
            """

            # Add filter conditions if provided
            filter_conditions = []
            if filters:
                if filters.get("departments"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR dept IN OUTBOUND record._id {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                        FILTER dept.departmentName IN @departmentNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("categories"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR cat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER cat.name IN @categoryNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )
                if filters.get("subcategories1"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat1Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("subcategories2"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat2Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("subcategories3"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                        FILTER subcat.name IN @subcat3Names
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("languages"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR lang IN OUTBOUND record._id {CollectionNames.BELONGS_TO_LANGUAGE.value}
                        FILTER lang.name IN @languageNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("topics"):
                    filter_conditions.append(
                        f"""
                    LENGTH(
                        FOR topic IN OUTBOUND record._id {CollectionNames.BELONGS_TO_TOPIC.value}
                        FILTER topic.name IN @topicNames
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )

                if filters.get("apps"):
                    filter_conditions.append(
                        """
                    LENGTH(
                        FOR app IN @apps
                        FILTER LOWER(record.connectorName) == app
                        LIMIT 1
                        RETURN 1
                    ) > 0
                    """
                    )
            # Add filter conditions to main query
            if filter_conditions:
                query += (
                    """
                FOR record IN allAccessibleRecords
                    FILTER """
                    + " AND ".join(filter_conditions)
                    + """
                    RETURN DISTINCT record
                """
                )
            else:
                query += """
                RETURN allAccessibleRecords
                """

            # Prepare bind variables
            bind_vars = {
                "userId": user_id,
                "orgId": org_id,
                "@users": CollectionNames.USERS.value,
                "@records": CollectionNames.RECORDS.value,
                "@anyone": CollectionNames.ANYONE.value,
            }

            # Add KB IDs to bind variables if filtering by KB
            if kb_ids:
                bind_vars["kb_ids"] = kb_ids
            # Add filter bind variables
            if filters:
                if filters.get("departments"):
                    bind_vars["departmentNames"] = filters[
                        "departments"
                    ]  # Direct department names
                if filters.get("categories"):
                    bind_vars["categoryNames"] = filters[
                        "categories"
                    ]  # Direct category names
                if filters.get("subcategories1"):
                    bind_vars["subcat1Names"] = filters[
                        "subcategories1"
                    ]  # Direct subcategory names
                if filters.get("subcategories2"):
                    bind_vars["subcat2Names"] = filters[
                        "subcategories2"
                    ]  # Direct subcategory names
                if filters.get("subcategories3"):
                    bind_vars["subcat3Names"] = filters[
                        "subcategories3"
                    ]  # Direct subcategory names
                if filters.get("languages"):
                    bind_vars["languageNames"] = filters[
                        "languages"
                    ]  # Direct language names
                if filters.get("topics"):
                    bind_vars["topicNames"] = filters["topics"]  # Direct topic names
                if filters.get("apps"):
                    bind_vars["apps"] = [
                        app.lower() for app in filters["apps"]
                    ]  # Lowercase app names

            # Execute with profiling enabled
            cursor = self.db.aql.execute(
                query,
                bind_vars=bind_vars,
                profile=2,
                fail_on_warning=False,
                stream=True
            )
            result = list(cursor)

            if kb_ids:
                self.logger.info(f"✅ KB filtering applied - found {len(result[0]) if result and isinstance(result[0], list) else len(result)} records from {len(kb_ids)} KBs")

            if result:
                if isinstance(result[0], dict):
                    return result
                else:
                    return result[0]
            else:
                return []

        except Exception as e:
            self.logger.error(f"Failed to get accessible records: {str(e)}")
            raise

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

    async def check_record_access_with_details(
        self, user_id: str, org_id: str, record_id: str
    ) -> Optional[Dict]:
        """
        Check record access and return record details if accessible

        Args:
            user_id (str): The userId field value in users collection
            org_id (str): The organization ID
            record_id (str): The record ID to check access for

        Returns:
            dict: Record details with permissions if accessible, None if not
        """
        try:
            # First check access and get permission paths
            access_query = f"""
            LET userDoc = FIRST(
                FOR user IN @@users
                FILTER user.userId == @userId
                RETURN user
            )

            LET directAccess = (
                FOR records, edge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'DIRECT',
                    source: userDoc,
                    role: edge.role
                }}
            )

            LET groupAccess = (
                FOR group, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER belongsEdge.entityType == 'GROUP'
                FOR records, permEdge IN 1..1 ANY group._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'GROUP',
                    source: group,
                    role: permEdge.role
                }}
            )

            LET orgAccess = (
                FOR org, belongsEdge IN 1..1 ANY userDoc._id {CollectionNames.BELONGS_TO.value}
                FILTER belongsEdge.entityType == 'ORGANIZATION'
                FOR records, permEdge IN 1..1 ANY org._id {CollectionNames.PERMISSIONS.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'ORGANIZATION',
                    source: org,
                    role: permEdge.role
                }}
            )

            LET kbAccess = (
                FOR kb, kbEdge IN 1..1 ANY userDoc._id {CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value}
                FOR records IN 1..1 ANY kb._id {CollectionNames.BELONGS_TO.value}
                FILTER records._key == @recordId
                RETURN {{
                    type: 'KNOWLEDGE_BASE',
                    source: kb,
                    role: kbEdge.role
                }}
            )

            LET anyoneAccess = (
                FOR records IN @@anyone
                FILTER records.organization == @orgId
                    AND records.file_key == @recordId
                RETURN {{
                    type: 'ANYONE',
                    source: null,
                    role: records.role
                }}
            )

            LET allAccess = UNION_DISTINCT(
                directAccess,
                groupAccess,
                orgAccess,
                kbAccess,
                anyoneAccess
            )

            RETURN LENGTH(allAccess) > 0 ? allAccess : null
            """

            bind_vars = {
                "userId": user_id,
                "orgId": org_id,
                "recordId": record_id,
                "@users": CollectionNames.USERS.value,
                "@anyone": CollectionNames.ANYONE.value,
            }

            cursor = self.db.aql.execute(access_query, bind_vars=bind_vars)
            access_result = next(cursor, None)

            if not access_result:
                return None

            # If we have access, get the complete record details
            record = await self.get_document(record_id, CollectionNames.RECORDS.value)
            if not record:
                return None

            user = await self.get_user_by_user_id(user_id)

            # Get file or mail details based on record type
            additional_data = None
            if record["recordType"] == RecordTypes.FILE.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.FILES.value
                )
            elif record["recordType"] == RecordTypes.MAIL.value:
                additional_data = await self.get_document(
                    record_id, CollectionNames.MAILS.value
                )
                message_id = record["externalRecordId"]
                # Format the webUrl with the user's email
                additional_data["webUrl"] = (
                    f"https://mail.google.com/mail?authuser={user['email']}#all/{message_id}"
                )

            metadata_query = f"""
            LET record = DOCUMENT(CONCAT('{CollectionNames.RECORDS.value}/', @recordId))

            LET departments = (
                FOR dept IN OUTBOUND record._id {CollectionNames.BELONGS_TO_DEPARTMENT.value}
                RETURN {{
                    id: dept._key,
                    name: dept.departmentName
                }}
            )

            LET categories = (
                FOR cat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(cat._id).collection == '{CollectionNames.CATEGORIES.value}'
                RETURN {{
                    id: cat._key,
                    name: cat.name
                }}
            )

            LET subcategories1 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES1.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET subcategories2 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES2.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET subcategories3 = (
                FOR subcat IN OUTBOUND record._id {CollectionNames.BELONGS_TO_CATEGORY.value}
                FILTER PARSE_IDENTIFIER(subcat._id).collection == '{CollectionNames.SUBCATEGORIES3.value}'
                RETURN {{
                    id: subcat._key,
                    name: subcat.name
                }}
            )

            LET topics = (
                FOR topic IN OUTBOUND record._id {CollectionNames.BELONGS_TO_TOPIC.value}
                RETURN {{
                    id: topic._key,
                    name: topic.name
                }}
            )

            LET languages = (
                FOR lang IN OUTBOUND record._id {CollectionNames.BELONGS_TO_LANGUAGE.value}
                RETURN {{
                    id: lang._key,
                    name: lang.name
                }}
            )

            RETURN {{
                departments: departments,
                categories: categories,
                subcategories1: subcategories1,
                subcategories2: subcategories2,
                subcategories3: subcategories3,
                topics: topics,
                languages: languages
            }}
            """
            metadata_cursor = self.db.aql.execute(
                metadata_query, bind_vars={"recordId": record_id}
            )
            metadata_result = next(metadata_cursor, None)

            # Get knowledge base info if record is in a KB
            kb_info = None
            for access in access_result:
                if access["type"] == "KNOWLEDGE_BASE":
                    kb = access["source"]
                    kb_info = {
                        "id": kb["_key"],
                        "name": kb["name"],
                        "orgId": kb["orgId"],
                    }
                    break

            # Format permissions from access paths
            permissions = []
            for access in access_result:
                permission = {
                    "id": record["_key"],
                    "name": record["recordName"],
                    "type": record["recordType"],
                    "relationship": access["role"],
                }
                permissions.append(permission)

            return {
                "record": {
                    **record,
                    "fileRecord": (
                        additional_data
                        if record["recordType"] == RecordTypes.FILE.value
                        else None
                    ),
                    "mailRecord": (
                        additional_data
                        if record["recordType"] == RecordTypes.MAIL.value
                        else None
                    ),
                },
                "knowledgeBase": kb_info,
                "metadata": metadata_result,
                "permissions": permissions,
            }

        except Exception as e:
            self.logger.error(
                f"Failed to check record access and get details: {str(e)}"
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

    async def validate_user_kb_access(
        self,
        user_id: str,
        org_id: str,
        kb_ids: List[str]
    ) -> Dict[str, List[str]]:
        """
        OPTIMIZED: Validate which KB IDs the user has access to using fast lookups
        Args:
            user_id: External user ID
            org_id: Organization ID
            kb_ids: List of KB IDs to check access for

        Returns:
            Dict with 'accessible' and 'inaccessible' KB IDs
        """
        try:
            self.logger.info(f"🚀 Fast KB access validation for user {user_id} on {len(kb_ids)} KBs")

            if not kb_ids:
                return {"accessible": [], "inaccessible": [], "total_user_kbs": 0}

            user = await self.get_user_by_user_id(user_id=user_id)
            if not user:
                self.logger.warning(f"⚠️ User not found: {user_id}")
                return {
                    "accessible": [],
                    "inaccessible": kb_ids,
                    "error": f"User not found: {user_id}"
                }

            user_key = user.get('_key')

            validation_query = """
            // Convert requested KB list to a set for fast lookup
            LET requested_kb_set = @kb_ids
            LET user_from = @user_from
            LET org_id = @org_id

            // Get user's accessible KBs in this org with direct filtering
            // Using FILTER early to reduce data processing
            LET user_accessible_kbs = (
                FOR perm IN @@permissions_to_kb
                    FILTER perm._from == user_from
                    FILTER perm.type == "USER"
                    // Fast role check using IN operator
                    FILTER perm.role IN ["OWNER", "READER", "FILEORGANIZER", "WRITER", "COMMENTER", "ORGANIZER"]
                    // Extract KB key directly from _to field (faster than DOCUMENT lookup)
                    LET kb_key = PARSE_IDENTIFIER(perm._to).key
                    // Early filter: only check KBs that were requested OR get all for org validation
                    LET kb_doc = DOCUMENT(CONCAT("recordGroups/", kb_key))
                    FILTER kb_doc != null
                    FILTER kb_doc.orgId == org_id
                    FILTER kb_doc.groupType == "KB"
                    FILTER kb_doc.connectorName == "KB"
                    RETURN kb_key
            )

            // Convert to sets for O(1) lookup complexity
            LET accessible_set = user_accessible_kbs
            LET accessible_requested = (
                FOR kb_id IN requested_kb_set
                    FILTER kb_id IN accessible_set
                    RETURN kb_id
            )

            LET inaccessible_requested = (
                FOR kb_id IN requested_kb_set
                    FILTER kb_id NOT IN accessible_set
                    RETURN kb_id
            )

            // Return minimal result set
            RETURN {
                accessible: accessible_requested,
                inaccessible: inaccessible_requested,
                total_user_kbs: LENGTH(accessible_set)
            }
            """

            bind_vars = {
                "user_from": f"users/{user_key}",
                "org_id": org_id,
                "kb_ids": kb_ids,
                "@permissions_to_kb": CollectionNames.PERMISSIONS_TO_KB.value,
            }

            cursor = self.db.aql.execute(
                validation_query,
                bind_vars=bind_vars,
                count=False,           # Don't count results
                batch_size=1000,       # Larger batch size for faster transfer
                cache=True,            # Enable query result caching
                memory_limit=0,        # No memory limit for faster execution
                max_runtime=30.0,      # 30 second timeout
                fail_on_warning=False, # Don't fail on warnings
                profile=False,         # Disable profiling for speed
                stream=True            # Stream results for memory efficiency
            )

            result = next(cursor, {})

            accessible = result.get("accessible", [])
            inaccessible = result.get("inaccessible", [])


            self.logger.info(f"KB validation complete: {len(accessible)}/{len(kb_ids)} accessible")

            if inaccessible:
                self.logger.warning(f"⚠️ User {user_id} lacks access to {len(inaccessible)} KBs")

            return {
                "accessible": accessible,
                "inaccessible": inaccessible,
                "total_user_kbs": result.get("total_user_kbs", 0)
            }

        except Exception as e:
            self.logger.error(f"❌ KB access validation error: {str(e)}")
            return {
                "accessible": [],
                "inaccessible": kb_ids,
                "error": str(e)
            }

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
            if transaction:
                raise
            return False

    async def get_all_agent_templates(self, user_id: str) -> List[Dict]:
        """Get all agent templates accessible by the user"""
        try:
            query = f"""
            FOR perm IN {CollectionNames.TEMPLATE_ACCESS.value}
                FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', @user_id)
                LET doc = DOCUMENT(perm._to)
                FILTER doc != null
                RETURN doc
            """
            bind_vars = {
                "user_id": user_id,
            }
            self.logger.info(f"Getting all agent templates accessible by user {user_id}")

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error("❌ Failed to get all agent templates: %s", str(e))
            return []

    async def get_template(self, template_id: str, user_id: str) -> Optional[Dict]:
        """Get the template accessible by the user"""
        try:
            query = f"""
                FOR perm IN {CollectionNames.TEMPLATE_ACCESS.value}
                    FILTER perm._to == CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', @user_id)
                    LIMIT 1
                    FILTER DOCUMENT(perm._to).isDeleted == false
                    RETURN DOCUMENT(perm._to)
            """
            bind_vars = {
                "template_id": template_id,
                "user_id": user_id,
            }
            self.logger.info(f"Getting template {template_id} accessible by user {user_id}")
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)
            if len(result) == 0:
                return None
            return result[0]
        except Exception as e:
            self.logger.error("❌ Failed to get template access: %s", str(e))
            return None

    async def share_agent_template(self, template_id: str, user_ids: List[str], user_id: str) -> Optional[bool]:
        """Share an agent template with users"""
        try:
            self.logger.info(f"Sharing agent template {template_id} with users {user_ids}")

            user_owner_access_query = f"""
            FOR perm IN {CollectionNames.TEMPLATE_ACCESS.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)
                FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', @user_id)
                LIMIT 1
                RETURN DOCUMENT(perm._to)
            """
            bind_vars = {
                "template_id": template_id,
                "user_id": user_id,
            }
            cursor = self.db.aql.execute(user_owner_access_query, bind_vars=bind_vars)
            user_owner_access = list(cursor)
            if len(user_owner_access) == 0:
                return False
            user_owner_access = user_owner_access[0]
            if user_owner_access.get("role") != "OWNER":
                return False

            #  users to be given access
            user_template_accesses = []
            for user_id in user_ids:
                user = await self.get_user_by_user_id(user_id)
                if user is None:
                    return False
                edge = {
                    "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
                    "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                    "role": "MEMBER",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                user_template_accesses.append(edge)

            result = await self.batch_create_edges(user_template_accesses, CollectionNames.TEMPLATE_ACCESS.value)
            if not result:
                return False
            return True
        except Exception as e:
            self.logger.error("❌ Failed to share agent template: %s", str(e))
            return False

    async def clone_agent_template(self, template_id: str) -> Optional[str]:
        """Clone an agent template"""
        try:
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value)
            if template is None:
                return None
            template_key = str(uuid.uuid4())
            template["_key"] = template_key
            template["isActive"] = True
            template["isDeleted"] = False
            template["deletedAtTimestamp"] = None
            template["deletedByUserId"] = None
            template["updatedAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["updatedByUserId"] = None
            template["createdAtTimestamp"] = get_epoch_timestamp_in_ms()
            template["createdBy"] = None
            template["deletedByUserId"] = None
            template["deletedAtTimestamp"] = None
            template["isDeleted"] = False
            result = await self.batch_upsert_nodes([template], CollectionNames.AGENT_TEMPLATES.value)
            if not result:
                return None
            return template_key
        except Exception as e:
            self.logger.error("❌ Failed to close agent template: %s", str(e))
            return False

    async def delete_agent_template(self, template_id: str, user_id: str) -> Optional[bool]:
        """Delete an agent template"""
        try:
            template_document_id = f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}"
            user_document_id = f"{CollectionNames.USERS.value}/{user_id}"

            permission_query = f"""
            FOR perm IN {CollectionNames.TEMPLATE_ACCESS.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                LIMIT 1
                RETURN perm
            """

            bind_vars = {
                "template_document_id": template_document_id,
                "user_document_id": user_document_id,
            }

            cursor = self.db.aql.execute(permission_query, bind_vars=bind_vars)
            permissions = list(cursor)

            if len(permissions) == 0:
                self.logger.warning(f"No permission found for user {user_id} on template {template_id}")
                return False

            # Check if template exists
            template = await self.get_document(template_id, CollectionNames.AGENT_TEMPLATES.value)
            if template is None:
                self.logger.warning(f"Template {template_id} not found")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the template using AQL UPDATE
            update_query = f"""
            UPDATE @template_key
            WITH @update_data
            IN {CollectionNames.AGENT_TEMPLATES.value}
            RETURN NEW
            """

            bind_vars = {
                "template_key": template_id,
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to delete template {template_id}")
                return False

            self.logger.info(f"Successfully deleted template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to delete agent template: %s", str(e), exc_info=True)
            return False

    async def update_agent_template(self, template_id: str, template_updates: Dict[str, Any], user_id: str) -> Optional[bool]:
        """Update an agent template"""
        try:
            # Check if user is the owner of the template
            template_document_id = f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}"
            user_document_id = f"{CollectionNames.USERS.value}/{user_id}"

            permission_query = f"""
            FOR perm IN {CollectionNames.TEMPLATE_ACCESS.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                LIMIT 1
                RETURN perm
            """

            bind_vars = {
                "template_document_id": template_document_id,
                "user_document_id": user_document_id,
            }

            cursor = self.db.aql.execute(permission_query, bind_vars=bind_vars)
            permissions = list(cursor)

            if len(permissions) == 0:
                self.logger.warning(f"No permission found for user {user_id} on template {template_id}")
                return False

            # Prepare update data
            update_data = {
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedByUserId": user_id
            }

            # Add only the fields that are provided
            allowed_fields = ["name", "description", "startMessage", "systemPrompt", "tools", "models", "memory", "tags"]
            for field in allowed_fields:
                if field in template_updates:
                    update_data[field] = template_updates[field]

            # Update the template - use the collection and document key
            update_query = f"""
            UPDATE @template_key
            WITH @update_data
            IN {CollectionNames.AGENT_TEMPLATES.value}
            RETURN NEW
            """

            bind_vars = {
                "template_key": template_id,  # Use just the key, not the full document ID
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to update template {template_id}")
                return False

            self.logger.info(f"Successfully updated template {template_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to update agent template: %s", str(e), exc_info=True)
            return False

    async def get_all_agents(self, user_id: str) -> List[Dict]:
        """Get all agents of a user"""
        try:
            query = f"""
            FOR doc IN {CollectionNames.AGENT_INSTANCES.value}
                FILTER doc.isDeleted == false
                FILTER doc.createdBy == @user_id
                RETURN doc
            """
            bind_vars = {
                "user_id": user_id,
            }
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)
        except Exception as e:
            self.logger.error("❌ Failed to get all agents: %s", str(e))
            return []

    async def update_agent(self, agent_id: str, agent_updates: Dict[str, Any], user_id: str) -> Optional[bool]:
        """Update an agent"""
        try:
            # Check if the agent exists and user has permission to update it
            existing_agent = await self.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value)
            if existing_agent is None:
                self.logger.warning(f"Agent {agent_id} not found")
                return False

            # Check if user is the owner of the agent (optional - add permission check if needed)
            # You might want to add a permission check here similar to the template update

            # Prepare update data
            update_data = {
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedByUserId": user_id
            }

            # Add only the fields that are provided in agent_updates
            allowed_fields = ["name", "description", "startMessage", "systemPrompt", "tools", "models", "apps", "kb", "vectorDBs", "tags"]
            for field in allowed_fields:
                if field in agent_updates:
                    update_data[field] = agent_updates[field]

            # Update the agent using AQL UPDATE statement
            update_query = f"""
            UPDATE @agent_key
            WITH @update_data
            IN {CollectionNames.AGENT_INSTANCES.value}
            RETURN NEW
            """

            bind_vars = {
                "agent_key": agent_id,  # Use just the key, not the full document ID
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to update agent {agent_id}")
                return False

            self.logger.info(f"Successfully updated agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to update agent: %s", str(e), exc_info=True)
            return False

    async def delete_agent(self, agent_id: str, user_id: str) -> Optional[bool]:
        """Delete an agent"""
        try:
            # Check if agent exists
            agent = await self.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value)
            if agent is None:
                self.logger.warning(f"Agent {agent_id} not found")
                return False

            # Check if user is the owner of the agent
            if agent.get("createdBy") != user_id:
                self.logger.warning(f"User {user_id} is not the owner of agent {agent_id}")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the agent using AQL UPDATE
            update_query = f"""
            UPDATE @agent_key
            WITH @update_data
            IN {CollectionNames.AGENT_INSTANCES.value}
            RETURN NEW
            """

            bind_vars = {
                "agent_key": agent_id,
                "update_data": update_data,
            }

            cursor = self.db.aql.execute(update_query, bind_vars=bind_vars)
            result = list(cursor)

            if not result or len(result) == 0:
                self.logger.error(f"Failed to delete agent {agent_id}")
                return False

            self.logger.info(f"Successfully deleted agent {agent_id}")
            return True

        except Exception as e:
            self.logger.error("❌ Failed to delete agent: %s", str(e), exc_info=True)
            return False
