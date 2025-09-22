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

            # Build anyoneRecords (always, for app-based selection)
            query += """
            LET anyoneRecords = (
                FOR records IN @@anyone
                    FILTER records.organization == @orgId
                    FOR record IN @@records
                        FILTER record != null AND record._key == records.file_key
                        RETURN record
            )
            """

            # Build base accessible from direct/group/org plus anyone and kb (union of paths)
            query += """
            LET baseAccessible = UNIQUE(UNION(directAndGroupRecords, kbRecords, anyoneRecords))
            """

            # Apps filtering when provided (works over baseAccessible)
            if filters and filters.get("apps"):
                query += """
                LET appFilteredRecords = (
                    FOR record IN baseAccessible
                        FILTER LENGTH(
                            FOR app IN @apps
                                FILTER LOWER(record.connectorName) == app
                                LIMIT 1
                                RETURN 1
                        ) > 0
                        RETURN DISTINCT record
                )
                """
            else:
                query += """
                LET appFilteredRecords = []
                """

            # Final accessible set logic (KB and Apps are OR-ed when both are present)
            if kb_ids and (filters and filters.get("apps")):
                query += """
                LET allAccessibleRecords = UNIQUE(UNION(kbRecords, appFilteredRecords))
                """
            elif kb_ids:
                query += """
                LET allAccessibleRecords = UNIQUE(kbRecords)
                """
            elif filters and filters.get("apps"):
                query += """
                LET allAccessibleRecords = UNIQUE(appFilteredRecords)
                """
            else:
                query += """
                LET allAccessibleRecords = baseAccessible
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
        """Get all agent templates accessible to a user via individual or team access"""
        try:
            query = f"""
            LET user_key = @user_id

            // Get user's teams
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Get templates with individual access
            LET individual_templates = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                    LET template = DOCUMENT(perm._to)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: perm.type,
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Get templates with team access (excluding those already found via individual access)
            LET individual_template_ids = (FOR ind_template IN individual_templates RETURN ind_template._id)

            LET team_templates = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm.type == "TEAM"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                    FILTER perm._to NOT IN individual_template_ids
                    LET template = DOCUMENT(perm._to)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: perm.type,
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Flatten and return all templates
            FOR template_result IN APPEND(individual_templates, team_templates)
                RETURN template_result
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
        """Get a template by ID with user permissions"""
        try:
            query = f"""
            LET user_key = @user_id
            LET template_path = CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)

            // Get user's teams first
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Check individual user permissions on the template
            LET individual_access = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm._to == template_path
                    FILTER perm.type == "USER"
                    LET template = DOCUMENT(template_path)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Check team permissions on the template (only if no individual access)
            LET team_access = LENGTH(individual_access) == 0 ? (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm._to == template_path
                    FILTER perm.type == "TEAM"
                    LET template = DOCUMENT(template_path)
                    FILTER template != null
                    FILTER template.isDeleted != true
                    RETURN MERGE(template, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        permission_id: perm._key,
                        permission_from: perm._from,
                        permission_to: perm._to,
                        permission_created_at: perm.createdAtTimestamp,
                        permission_updated_at: perm.updatedAtTimestamp,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            ) : []

            // Return individual access first, then team access
            LET final_result = LENGTH(individual_access) > 0 ?
                FIRST(individual_access) :
                (LENGTH(team_access) > 0 ? FIRST(team_access) : null)

            RETURN final_result
            """

            bind_vars = {
                "template_id": template_id,
                "user_id": user_id,
            }

            self.logger.info(f"Getting template {template_id} accessible by user {user_id}")
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            if len(result) == 0 or result[0] is None:
                return None

            return result[0]

        except Exception as e:
            self.logger.error("❌ Failed to get template access: %s", str(e))
            return None

    async def share_agent_template(self, template_id: str, user_id: str, user_ids: Optional[List[str]] = None, team_ids: Optional[List[str]] = None) -> Optional[bool]:
        """Share an agent template with users"""
        try:
            self.logger.info(f"Sharing agent template {template_id} with users {user_ids}")

            user_owner_access_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_TEMPLATES.value}/', @template_id)
                FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', @user_id)
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
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

            if user_ids is None and team_ids is None:
                return False

            #  users to be given access
            user_template_accesses = []
            if user_ids:
                for user_id in user_ids:
                    user = await self.get_user_by_user_id(user_id)
                    if user is None:
                        return False
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "USER",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_template_accesses.append(edge)

            if team_ids:
                for team_id in team_ids:
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team_id}",
                        "_to": f"{CollectionNames.AGENT_TEMPLATES.value}/{template_id}",
                        "type": "TEAM",
                        "role": "READER",
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_template_accesses.append(edge)

            result = await self.batch_create_edges(user_template_accesses, CollectionNames.PERMISSION.value)
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
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
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
            permission = permissions[0]
            if permission.get("role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the owner of template {template_id}")
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
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == @template_document_id
                FILTER perm._from == @user_document_id
                FILTER perm.role == "OWNER"
                FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_TEMPLATES.value}/')
                FILTER DOCUMENT(perm._to).isDeleted == false
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
            permission = permissions[0]
            if permission.get("role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the owner of template {template_id}")
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

    async def get_agent(self, agent_id: str, user_id: str) -> Optional[Dict]:
        """Get an agent by ID with user permissions - flattened response"""
        try:
            query = f"""
            LET user_key = @user_id
            LET agent_path = CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)

            // Get user's teams first
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Check individual user permissions on the agent
            LET individual_access = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm._to == agent_path
                    FILTER perm.type == "USER"
                    LET agent = DOCUMENT(agent_path)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Check team permissions on the agent (only if no individual access)
            LET team_access = LENGTH(individual_access) == 0 ? (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm._to == agent_path
                    FILTER perm.type == "TEAM"
                    LET agent = DOCUMENT(agent_path)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            ) : []

            // Return individual access first, then team access
            LET final_result = LENGTH(individual_access) > 0 ?
                FIRST(individual_access) :
                (LENGTH(team_access) > 0 ? FIRST(team_access) : null)

            RETURN final_result
            """

            bind_vars = {
                "agent_id": agent_id,
                "user_id": user_id,
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            if len(result) == 0 or result[0] is None:
                self.logger.warning(f"No permissions found for user {user_id} on agent {agent_id}")
                return None

            return result[0]

        except Exception as e:
            self.logger.error(f"Failed to get agent: {str(e)}")
            return None

    async def get_all_agents(self, user_id: str) -> List[Dict]:
        """Get all agents accessible to a user via individual or team access - flattened response"""
        try:
            query = f"""
            LET user_key = @user_id

            // Get user's teams
            LET user_teams = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.TEAMS.value}/')
                    RETURN perm._to
            )

            // Get agents with individual access
            LET individual_agents = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from == CONCAT('{CollectionNames.USERS.value}/', user_key)
                    FILTER perm.type == "USER"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_INSTANCES.value}/')
                    LET agent = DOCUMENT(perm._to)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "INDIVIDUAL",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Get agents with team access (excluding those already found via individual access)
            LET individual_agent_ids = (FOR ind_agent IN individual_agents RETURN ind_agent._id)

            LET team_agents = (
                FOR perm IN {CollectionNames.PERMISSION.value}
                    FILTER perm._from IN user_teams
                    FILTER perm.type == "TEAM"
                    FILTER STARTS_WITH(perm._to, '{CollectionNames.AGENT_INSTANCES.value}/')
                    FILTER perm._to NOT IN individual_agent_ids
                    LET agent = DOCUMENT(perm._to)
                    FILTER agent != null
                    FILTER agent.isDeleted != true
                    RETURN MERGE(agent, {{
                        access_type: "TEAM",
                        user_role: perm.role,
                        can_edit: perm.role IN ["OWNER", "WRITER", "ORGANIZER"],
                        can_delete: perm.role == "OWNER",
                        can_share: perm.role IN ["OWNER", "ORGANIZER"],
                        can_view: true
                    }})
            )

            // Flatten and return all agents
            FOR agent_result IN APPEND(individual_agents, team_agents)
                RETURN agent_result
            """

            bind_vars = {
                "user_id": user_id,
            }

            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            return list(cursor)

        except Exception as e:
            self.logger.error(f"Failed to get all agents: {str(e)}")
            return []

    async def update_agent(self, agent_id: str, agent_updates: Dict[str, Any], user_id: str) -> Optional[bool]:
        """Update an agent"""
        try:
            # Check if user has permission to update the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can edit the agent
            if not agent_with_permission.get("can_edit", False):
                self.logger.warning(f"User {user_id} does not have edit permission on agent {agent_id}")
                return False

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

            # Update the agent using AQL UPDATE statement - Fixed to use proper collection name
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
            self.logger.error(f"Failed to update agent: {str(e)}")
            return False

    async def delete_agent(self, agent_id: str, user_id: str) -> Optional[bool]:
        """Delete an agent"""
        try:
            # Check if agent exists
            agent = await self.get_document(agent_id, CollectionNames.AGENT_INSTANCES.value)
            if agent is None:
                self.logger.warning(f"Agent {agent_id} not found")
                return False

            # Check if user has permission to delete the agent using the new method
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can delete the agent
            if not agent_with_permission.get("can_delete", False):
                self.logger.warning(f"User {user_id} does not have delete permission on agent {agent_id}")
                return False

            # Prepare update data for soft delete
            update_data = {
                "isDeleted": True,
                "deletedAtTimestamp": get_epoch_timestamp_in_ms(),
                "deletedByUserId": user_id
            }

            # Soft delete the agent using AQL UPDATE - Fixed to use f-string
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
            self.logger.error(f"Failed to delete agent: {str(e)}")
            return False

    async def share_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]]) -> Optional[bool]:
        """Share an agent to users and teams"""
        try:
            # Check if agent exists and user has permission to share it
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return False

            # Check if user can share the agent
            if not agent_with_permission.get("can_share", False):
                self.logger.warning(f"User {user_id} does not have share permission on agent {agent_id}")
                return False

            # Share the agent to users
            user_agent_edges = []
            if user_ids:
                for user_id_to_share in user_ids:
                    user = await self.get_user_by_user_id(user_id_to_share)
                    if user is None:
                        self.logger.warning(f"User {user_id_to_share} not found")
                        continue
                    edge = {
                        "_from": f"{CollectionNames.USERS.value}/{user.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "USER",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    user_agent_edges.append(edge)

                result = await self.batch_create_edges(user_agent_edges, CollectionNames.PERMISSION.value)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to user {user_id_to_share}")
                    return False

            # Share the agent to teams
            team_agent_edges = []
            if team_ids:
                for team_id in team_ids:
                    team = await self.get_document(team_id, CollectionNames.TEAMS.value)
                    if team is None:
                        self.logger.warning(f"Team {team_id} not found")
                        continue
                    edge = {
                        "_from": f"{CollectionNames.TEAMS.value}/{team.get('_key')}",
                        "_to": f"{CollectionNames.AGENT_INSTANCES.value}/{agent_id}",
                        "role": "READER",
                        "type": "TEAM",
                        "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                        "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                    }
                    team_agent_edges.append(edge)
                result = await self.batch_create_edges(team_agent_edges, CollectionNames.PERMISSION.value)
                if not result:
                    self.logger.error(f"Failed to share agent {agent_id} to team {team_id}")
                    return False
            return True
        except Exception as e:
            self.logger.error("❌ Failed to share agent: %s", str(e), exc_info=True)
            return False

    async def unshare_agent(self, agent_id: str, user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]]) -> Optional[Dict]:
        """Unshare an agent from users and teams - direct deletion without validation"""
        try:
            # Check if user has permission to unshare the agent
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None or not agent_with_permission.get("can_share", False):
                return {"success": False, "reason": "Insufficient permissions to unshare agent"}

            # Build conditions for batch delete
            conditions = []
            bind_vars = {"agent_id": agent_id}

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"{CollectionNames.USERS.value}/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"{CollectionNames.TEAMS.value}/{team_id}" for team_id in team_ids]

            if not conditions:
                return {"success": False, "reason": "No users or teams provided"}

            # Single batch delete query
            batch_delete_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                FILTER ({' OR '.join(conditions)})
                REMOVE perm IN {CollectionNames.PERMISSION.value}
                RETURN OLD._key
            """

            cursor = self.db.aql.execute(batch_delete_query, bind_vars=bind_vars)
            deleted_permissions = list(cursor)

            self.logger.info(f"Unshared agent {agent_id}: removed {len(deleted_permissions)} permissions")

            return {
                "success": True,
                "agent_id": agent_id,
                "deleted_permissions": len(deleted_permissions)
            }

        except Exception as e:
            self.logger.error("Failed to unshare agent: %s", str(e), exc_info=True)
            return {"success": False, "reason": f"Internal error: {str(e)}"}

    async def update_agent_permission(self, agent_id: str, owner_user_id: str, user_ids: Optional[List[str]], team_ids: Optional[List[str]], role: str) -> Optional[Dict]:
        """Update permission role for users and teams on an agent (only OWNER can do this)"""
        try:
            # Check if the requesting user is the OWNER of the agent
            agent_with_permission = await self.get_agent(agent_id, owner_user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {owner_user_id} on agent {agent_id}")
                return {"success": False, "reason": "Agent not found or no permission"}

            # Only OWNER can update permissions - Fixed to use the flattened structure
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {owner_user_id} is not the OWNER of agent {agent_id}")
                return {"success": False, "reason": "Only OWNER can update permissions"}

            # Build conditions for batch update
            conditions = []
            bind_vars = {
                "agent_id": agent_id,
                "new_role": role,
                "timestamp": get_epoch_timestamp_in_ms(),
            }

            if user_ids:
                conditions.append("(perm._from IN @user_froms AND perm.type == 'USER' AND perm.role != 'OWNER')")
                bind_vars["user_froms"] = [f"{CollectionNames.USERS.value}/{user_id}" for user_id in user_ids]

            if team_ids:
                conditions.append("(perm._from IN @team_froms AND perm.type == 'TEAM')")
                bind_vars["team_froms"] = [f"{CollectionNames.TEAMS.value}/{team_id}" for team_id in team_ids]

            if not conditions:
                return {"success": False, "reason": "No users or teams provided"}

            # Single batch update query
            batch_update_query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                FILTER ({' OR '.join(conditions)})
                UPDATE perm WITH {{
                    role: @new_role,
                    updatedAtTimestamp: @timestamp
                }} IN {CollectionNames.PERMISSION.value}
                RETURN {{
                    _key: NEW._key,
                    _from: NEW._from,
                    type: NEW.type,
                    role: NEW.role
                }}
            """

            cursor = self.db.aql.execute(batch_update_query, bind_vars=bind_vars)
            updated_permissions = list(cursor)

            if not updated_permissions:
                self.logger.warning(f"No permission edges found to update for agent {agent_id}")
                return {"success": False, "reason": "No permissions found to update"}

            # Count updates by type
            updated_users = sum(1 for perm in updated_permissions if perm["type"] == "USER")
            updated_teams = sum(1 for perm in updated_permissions if perm["type"] == "TEAM")

            self.logger.info(f"Successfully updated {len(updated_permissions)} permissions for agent {agent_id} to role {role}")

            return {
                "success": True,
                "agent_id": agent_id,
                "new_role": role,
                "updated_permissions": len(updated_permissions),
                "updated_users": updated_users,
                "updated_teams": updated_teams
            }

        except Exception as e:
            self.logger.error(f"Failed to update agent permission: {str(e)}")
            return {"success": False, "reason": f"Internal error: {str(e)}"}

    async def get_agent_permissions(self, agent_id: str, user_id: str) -> Optional[List[Dict]]:
        """Get all permissions for an agent (only OWNER can view all permissions)"""
        try:
            # Check if user has access to the agent
            agent_with_permission = await self.get_agent(agent_id, user_id)
            if agent_with_permission is None:
                self.logger.warning(f"No permission found for user {user_id} on agent {agent_id}")
                return None

            # Only OWNER can view all permissions - Fixed to use the flattened structure
            if agent_with_permission.get("user_role") != "OWNER":
                self.logger.warning(f"User {user_id} is not the OWNER of agent {agent_id}")
                return None

            # Get all permissions for the agent
            query = f"""
            FOR perm IN {CollectionNames.PERMISSION.value}
                FILTER perm._to == CONCAT('{CollectionNames.AGENT_INSTANCES.value}/', @agent_id)
                LET entity = DOCUMENT(perm._from)
                FILTER entity != null
                RETURN {{
                    id: entity._key,
                    name: entity.fullName || entity.name || entity.userName,
                    userId: entity.userId,
                    email: entity.email,
                    role: perm.role,
                    type: perm.type,
                    createdAtTimestamp: perm.createdAtTimestamp,
                    updatedAtTimestamp: perm.updatedAtTimestamp
                }}
            """

            bind_vars = {
                "agent_id": agent_id,
            }
            cursor = self.db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            return result

        except Exception as e:
            self.logger.error(f"Failed to get agent permissions: {str(e)}")
            return None
