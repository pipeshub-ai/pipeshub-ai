"""ArangoDB service for interacting with the database"""

from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone, timedelta
from app.config.configuration_service import ConfigurationService
from arango.database import TransactionDatabase
from app.utils.logger import create_logger
import uuid
import json
from arango import ArangoClient
from app.connectors.core.base_arango_service import BaseArangoService
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.config.arangodb_constants import CollectionNames, Connectors

logger = create_logger('google_connectors')

class ArangoService(BaseArangoService):
    """ArangoDB service class for interacting with the database"""

    def __init__(self, arango_client: ArangoClient, kafka_service, config: ConfigurationService):
        # Call parent class constructor to initialize shared attributes
        super().__init__(arango_client, config)
        self.kafka_service = kafka_service

    async def store_page_token(self, channel_id: str, resource_id: str, user_email: str, token: str):
        """Store page token with user channel information"""
        try:
            logger.info("""
            🚀 Storing page token:

            - Channel: %s
            - Resource: %s
            - User Email: %s
            - Token: %s
            """, channel_id, resource_id, user_email, token)

            if not self.db.has_collection(CollectionNames.PAGE_TOKENS.value):
                self.db.create_collection(CollectionNames.PAGE_TOKENS.value)

            unique_key = f"{channel_id}_{resource_id}"
            token_doc = {
                'channelId': channel_id,
                'resourceId': resource_id,
                'userEmail': user_email,
                'token': token,
                'createdAt': int(datetime.now(timezone(timedelta(hours=5, minutes=30))).timestamp()),
            }

            # Upsert to handle updates to existing channel tokens
            query = """
            UPSERT { _key: @unique_key }
            INSERT @token_doc
            UPDATE @token_doc
            IN @@pageTokens
            RETURN NEW
            """

            result = list(self.db.aql.execute(
                query,
                bind_vars={
                    'unique_key': unique_key,
                    'token_doc': token_doc,
                    '@pageTokens': CollectionNames.PAGE_TOKENS.value
                }
            ))

            logger.info("✅ Page token stored successfully")
            return result[0] if result else None

        except Exception as e:
            logger.error("❌ Error storing page token: %s", str(e))
            return None

    async def get_page_token_db(self, channel_id: str = None, resource_id: str = None, user_email: str = None) -> Optional[str]:
        """Get page token for specific channel"""
        try:
            logger.info("""
            🔍 Getting page token for:
            - Channel: %s
            - Resource: %s
            - User Email: %s
            """, channel_id, resource_id, user_email)

            query = """
            FOR token IN @@pageTokens
            FILTER token.channelId == @channel_id
            or token.resourceId == @resource_id
            or token.userEmail == @user_email
            RETURN token
            """

            result = list(self.db.aql.execute(
                query,
                bind_vars={
                    'channel_id': channel_id,
                    'resource_id': resource_id,
                    'user_email': user_email,
                    '@pageTokens': CollectionNames.PAGE_TOKENS.value
                }
            ))

            if result:
                logger.info("✅ Found token for channel")
                return result[0]

            logger.warning("⚠️ No token found for channel")
            return None

        except Exception as e:
            logger.error("❌ Error getting page token: %s", str(e))
            return None

    async def get_all_channel_tokens(self) -> List[Dict]:
        """Get all active channel tokens"""
        try:
            logger.info("🚀 Getting all channel tokens")
            query = """
            FOR token IN pageTokens
            RETURN {
                user_email: token.user_email,
                channel_id: token.channel_id,
                resource_id: token.resource_id,
                token: token.token,
                updatedAt: token.updatedAt
            }
            """

            result = list(self.db.aql.execute(query))
            logger.info("✅ Retrieved %d channel tokens", len(result))
            return result

        except Exception as e:
            logger.error("❌ Error getting all channel tokens: %s", str(e))
            return []

    async def store_channel_history_id(self, channel_data, user_email: str):
        """
        Store the latest historyId for a user's channel watch

        Args:
            user_email (str): Email of the user
            history_id (str): Latest historyId from channel watch
            channel_id (str, optional): Channel ID associated with the watch
            resource_id (str, optional): Resource ID associated with the watch
        """
        try:
            logger.info(f"🚀 Storing historyId for user {user_email}")

            history_id = channel_data['historyId']

            query = """
            UPSERT { user_email: @user_email }
            INSERT { 
                user_email: @user_email, 
                historyId: @history_id,
                updatedAt: DATE_NOW()
            }
            UPDATE { 
                history_id: @history_id,
                updatedAt: DATE_NOW()
            } IN channelHistory
            RETURN NEW
            """

            result = list(self.db.aql.execute(
                query,
                bind_vars={
                    'user_email': user_email,
                    'history_id': history_id,
                }
            ))

            if result:
                logger.info(
                    f"✅ Successfully stored historyId for {user_email}")
                return result[0]

            logger.warning(f"⚠️ Failed to store historyId for {user_email}")
            return None

        except Exception as e:
            logger.error(f"❌ Error storing historyId: {str(e)}")
            return None

    async def get_channel_history_id(self, user_email: str) -> Optional[str]:
        """
        Retrieve the latest historyId for a user

        Args:
            user_email (str): Email of the user

        Returns:
            Optional[str]: Latest historyId if found, None otherwise
        """
        try:
            logger.info(f"🚀 Retrieving historyId for user {user_email}")

            query = """
            FOR history IN channelHistory
            FILTER history.user_email == @user_email
            RETURN {
                historyId: history.historyId,
                updatedAt: history.updatedAt
            }
            """

            result = list(self.db.aql.execute(
                query,
                bind_vars={'user_email': user_email}
            ))

            if result:
                logger.info(f"✅ Found historyId for {user_email}")
                return result[0]

            logger.warning(f"⚠️ No historyId found for {user_email}")
            return None

        except Exception as e:
            logger.error(f"❌ Error retrieving historyId: {str(e)}")
            return None

    async def cleanup_expired_tokens(self, expiry_hours: int = 24):
        """Clean up tokens that haven't been updated recently"""
        try:
            expiry_time = (datetime.now(timezone.utc) -
                           timedelta(hours=expiry_hours))

            query = """
            FOR token IN pageTokens
            FILTER token.updatedAt < @expiry_time
            REMOVE token IN pageTokens
            RETURN OLD
            """

            removed = list(self.db.aql.execute(
                query,
                bind_vars={'expiry_time': expiry_time}
            ))

            logger.info("🧹 Cleaned up %d expired tokens", len(removed))
            return len(removed)

        except Exception as e:
            logger.error("❌ Error cleaning up tokens: %s", str(e))
            return 0

    async def get_document(self, document_key: str, collection: str) -> Optional[Dict]:
        """Get a document by its key"""
        try:
            query = """
            FOR doc IN @@collection
                FILTER doc._key == @document_key
                RETURN doc
            """
            cursor = self.db.aql.execute(
                query, bind_vars={'document_key': document_key, '@collection': collection})
            result = list(cursor)
            return result[0] if result else None
        except Exception as e:
            logger.error("❌ Error getting document: %s", str(e))
            return None

    async def batch_upsert_nodes(self, nodes: List[Dict], collection: str, transaction: Optional[TransactionDatabase] = None):
        """Batch upsert multiple nodes using Python-Arango SDK methods"""
        try:
            logger.info("🚀 Batch upserting nodes: %s", collection)

            batch_query = """
            FOR node IN @nodes
                UPSERT { _key: node._key }
                INSERT node
                UPDATE node
                IN @@collection
                RETURN NEW
            """

            bind_vars = {
                'nodes': nodes,
                "@collection": collection
            }

            db = transaction if transaction else self.db

            cursor = db.aql.execute(
                batch_query,
                bind_vars=bind_vars
            )
            results = list(cursor)
            logger.info("✅ Successfully upserted %d nodes in collection '%s'.", len(
                results), collection)
            return True

        except Exception as e:
            logger.error(
                "❌ Batch upsert failed: %s",
                str(e)
            )
            if transaction:
                raise
            return False

    async def batch_create_edges(self, edges: List[Dict], collection: str, transaction: Optional[TransactionDatabase] = None):
        """Batch create PARENT_CHILD relationships"""
        try:
            logger.info("🚀 Batch creating edges: %s", collection)

            batch_query = """
            FOR edge IN @edges
                UPSERT { _from: edge._from, _to: edge._to }
                INSERT edge
                UPDATE edge
                IN @@collection
                RETURN NEW
            """
            bind_vars = {'edges': edges, '@collection': collection}

            db = transaction if transaction else self.db

            cursor = db.aql.execute(batch_query, bind_vars=bind_vars)
            results = list(cursor)
            logger.info("✅ Successfully created %d edges in collection '%s'.", len(
                results), collection)
            return True
        except Exception as e:
            logger.error(
                "❌ Batch edge creation failed: %s",
                str(e)
            )
            return False

    async def remove_existing_edges(self, file_id: str) -> bool:
        """Remove all existing edges for a record"""
        try:
            logger.info("🚀 Removing all existing edges for file %s", file_id)
            query = """
            FOR edge in @@recordRelations
                FILTER edge._from == @@records/@file_id} OR edge._to == @@records/@file_id
                REMOVE edge._key IN @@recordRelations
            """
            self.db.aql.execute(
                query,
                bind_vars={'file_id': file_id, '@records': CollectionNames.RECORDS.value, '@recordRelations': CollectionNames.RECORD_RELATIONS.value}
            )
            logger.info("✅ Removed all existing edges for file %s", file_id)
            return True
        except Exception as e:
            logger.error(
                "❌ Failed to remove existing edges: %s",
                str(e)
            )
            return False

    async def get_file_parents(self, file_key: str, transaction) -> List[Dict]:
        try:
            if not file_key:
                raise ValueError("File ID is required")

            logger.info("🚀 Getting parents for record %s", file_key)

            query = """
            LET relations = (
                FOR rel IN @@recordRelations
                    FILTER rel._to == @record_id
                    RETURN rel._from
            )
            
            LET parent_keys = (
                FOR rel IN relations
                    LET key = PARSE_IDENTIFIER(rel).key
                    RETURN {
                        original_id: rel,
                        parsed_key: key
                    }
            )
            
            LET parent_files = (
                FOR parent IN parent_keys
                    FOR record IN @@records 
                        FILTER record._key == parent.parsed_key
                        RETURN {
                            key: record._key,
                            externalRecordId: record.externalRecordId
                        }
            )
            
            RETURN {
                input_file_key: @file_key,
                found_relations: relations,
                parsed_parent_keys: parent_keys,
                found_parent_files: parent_files
            }
            """

            bind_vars = {
                'file_key': file_key,
                'record_id': CollectionNames.RECORDS.value + '/' + file_key,
                '@records': CollectionNames.RECORDS.value,
                '@recordRelations': CollectionNames.RECORD_RELATIONS.value
            }

            db = transaction if transaction else self.db
            cursor = db.aql.execute(query, bind_vars=bind_vars)
            result = list(cursor)

            # Log the detailed results
            logger.info("🔍 Query diagnostic results: %s", result)

            if not result or not result[0]['found_relations']:
                logger.warning("⚠️ No relations found for record %s", file_key)
            if not result or not result[0]['parsed_parent_keys']:
                logger.warning(
                    "⚠️ No parent keys parsed for record %s", file_key)
            if not result or not result[0]['found_parent_files']:
                logger.warning(
                    "⚠️ No parent files found for record %s", file_key)

            # Return just the external file IDs if everything worked
            return [record['externalRecordId'] for record in result[0]['found_parent_files']] if result else []

        except ValueError as ve:
            logger.error(f"❌ Validation error: {str(ve)}")
            return []
        except Exception as e:
            logger.error("❌ Error getting parents for record %s: %s",
                         file_key, str(e))
            return []

    async def get_entity_id_by_email(self, email: str, transaction: Optional[TransactionDatabase] = None) -> Optional[str]:
        """
        Get user or group ID by email address

        Args:
            email (str): Email address to look up

        Returns:
            Optional[str]: Entity ID (_key) if found, None otherwise
        """
        try:
            logger.info("🚀 Getting Entity Key by mail")

            # First check users collection
            query = """
            FOR doc IN users
                FILTER doc.email == @email
                RETURN doc._key
            """
            db = transaction if transaction else self.db
            result = db.aql.execute(query, bind_vars={'email': email})
            user_id = next(result, None)
            if user_id:
                logger.info("✅ Got User ID: %s", user_id)
                return user_id

            # If not found in users, check groups collection
            query = """
            FOR doc IN groups
                FILTER doc.email == @email
                RETURN doc._key
            """
            result = db.aql.execute(query, bind_vars={'email': email})
            group_id = next(result, None)
            if group_id:
                logger.info("✅ Got group ID: %s", group_id)
                return group_id

            return None

        except Exception as e:
            logger.error(
                "❌ Failed to get entity ID for email %s: %s", email, str(e))
            return None

    async def organization_exists(self, organization_name: str) -> bool:
        """Check if the organization exists in the database"""
        logger.info("🚀 Checking whether the organization exists")
        query = """
        FOR doc IN @@orgs
            FILTER doc.name == @organization_name
            RETURN doc._key
        """
        result = self.db.aql.execute(
            query, bind_vars={'organization_name': organization_name, '@orgs': CollectionNames.ORGS.value})
        response = bool(next(result, None))
        logger.info("Does Organization exist?: %s", response)
        return response

    async def get_group_members(self, group_id: str) -> List[Dict]:
        """Get all users in a group"""
        try:
            logger.info("🚀 Getting group members for %s", group_id)
            query = """
            FOR user, membership IN 1..1 OUTBOUND @group_id GRAPH 'fileAccessGraph'
                FILTER membership.type == 'membership'
                RETURN DISTINCT user
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={'group_id': f'groups/{group_id}'}
            )
            logger.info("✅ Group members retrieved successfully")
            return list(cursor)

        except Exception as e:
            logger.error(
                "❌ Failed to get group members: %s",
                str(e)
            )
            return []

    async def get_file_permissions(self, file_key: str, transaction: Optional[TransactionDatabase] = None) -> List[Dict]:
        """Get current permissions for a file"""
        try:
            logger.info("🚀 Getting file permissions for %s", file_key)
            query = """
            FOR perm IN @@permissions
                FILTER perm._from == @file_key
                RETURN perm
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={'file_key': f'records/{file_key}', '@permissions': CollectionNames.PERMISSIONS.value}
            )
            logger.info("✅ File permissions retrieved successfully")
            return list(cursor)

        except Exception as e:
            logger.error(
                "❌ Failed to get file permissions: %s",
                str(e)
            )
            return []

    async def store_permission(self, file_key: str, entity_key: str, permission_data: Dict, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Store or update permission relationship with change detection"""
        try:
            logger.info(
                "🚀 Storing permission for file %s and entity %s", file_key, entity_key)

            if not entity_key:
                logger.warning(
                    "⚠️ Cannot store permission - missing entity_key")
                return False

            # Use transaction if provided, otherwise use self.db
            db = transaction if transaction else self.db
            permissions_collection = db.collection(CollectionNames.PERMISSIONS.value)

            edge_key = str(uuid.uuid4())
            timestamp =  get_epoch_timestamp_in_ms()

            # Determine the correct collection for the _to field
            entityType = permission_data.get("type", "user").lower()
            if entityType == "domain":
                to_collection = CollectionNames.ORGS.value
            else:
                to_collection = f"{entityType}s"

            logger.info("Permission data is %s", permission_data)
            # Create edge document with proper formatting
            edge = {
                '_key': edge_key,
                '_from': f'records/{file_key}',
                '_to': f'{to_collection}/{entity_key}',
                'type': permission_data.get('type').upper(),
                'role': permission_data.get('role', 'READER').upper(),
                'externalPermissionId': permission_data.get('id'),
                'createdAtTimestamp': timestamp,
                'updatedAtTimestamp': timestamp,
                'lastUpdatedTimestampAtSource': timestamp,
            }

            # Log the edge document for debugging
            logger.debug("Creating edge document: %s", edge)

            # Check if permission edge exists
            try:
                existing_edge = permissions_collection.get(edge_key)

                if not existing_edge:
                    # New permission
                    permissions_collection.insert(edge)
                    logger.info("✅ Created new permission edge: %s", edge_key)
                elif self._permission_needs_update(existing_edge, permission_data):
                    # Update existing permission
                    permissions_collection.update(edge_key, edge)
                    logger.info("✅ Updated permission edge: %s", edge_key)
                else:
                    logger.info(
                        "✅ No update needed for permission edge: %s", edge_key)

                return True

            except Exception as e:
                logger.error(
                    "❌ Failed to access permissions collection: %s",
                    str(e)
                )
                if transaction:
                    raise
                return False

        except Exception as e:
            logger.error(
                "❌ Failed to store permission: %s",
                str(e)
            )
            if transaction:
                raise
            return False

    async def store_membership(self, group_id: str, user_id: str, role: str = 'member') -> bool:
        """Store group membership"""
        try:
            logger.info(
                "🚀 Storing membership for group %s and user %s", group_id, user_id)
            edge = {
                '_from': f'groups/{group_id}',
                '_to': f'users/{user_id}',
                'type': 'membership',
                'role': role
            }
            self._collections[CollectionNames.BELONGS_TO.value].insert(edge, overwrite=True)
            logger.info("✅ Membership stored successfully")
            return True
        except Exception as e:
            logger.error(
                "❌ Failed to store membership: %s",
                str(e)
            )
            return False

    async def process_file_permissions(
        self,
        org_id: str,
        file_key: str,
        permissions_data: List[Dict],
        transaction: Optional[TransactionDatabase] = None
    ) -> bool:
        """
        Process file permissions by comparing new permissions with existing ones.
        Assumes all entities and files already exist in the database.
        """
        try:
            logger.info("🚀 Processing permissions for file %s", file_key)
            timestamp =  get_epoch_timestamp_in_ms()

            db = transaction if transaction else self.db

            # Get existing permissions for comparison
            # Remove 'anyone' permission for this file if it exists
            query = """
            FOR a IN anyone
                FILTER a.file_key == @file_key
                FILTER a.organization == @org_id
                REMOVE a IN anyone
            """
            db.aql.execute(query, bind_vars={'file_key': file_key, 'org_id': org_id})
            logger.info(
                "🗑️ Removed 'anyone' permission for file %s", file_key)

            existing_permissions = await self.get_file_permissions(file_key, transaction=transaction)
            logger.info("🚀 Existing permissions: %s", existing_permissions)

            # Get all permission IDs from new permissions
            new_permission_ids = list({p.get('id')
                                       for p in permissions_data})
            logger.info("🚀 New permission IDs: %s", new_permission_ids)
            # Find permissions that exist but are not in new permissions
            permissions_to_remove = [
                perm for perm in existing_permissions
                if perm.get('externalPermissionId') not in new_permission_ids
            ]

            # Remove permissions that no longer exist
            if permissions_to_remove:
                logger.info("🗑️ Removing %d obsolete permissions",
                            len(permissions_to_remove))
                # Check if 'anyone' type permissions exist in new permissions

                for perm in permissions_to_remove:
                    query = """
                    FOR p IN permissions
                        FILTER p._key == @perm_key
                        REMOVE p IN permissions
                    """
                    db.aql.execute(query, bind_vars={
                        'perm_key': perm['_key']})

            # Process permissions by type
            for perm_type in ['user', 'group', 'domain', 'anyone']:
                # Filter new permissions for current type
                new_perms = [p for p in permissions_data if p.get(
                    'type', '').lower() == perm_type]
                # Filter existing permissions for current type
                existing_perms = [p for p in existing_permissions if p.get(
                    'type').lower() == perm_type]

                # Compare and update permissions
                if perm_type == 'user' or perm_type == 'group' or perm_type == 'domain':
                    for new_perm in new_perms:
                        perm_id = new_perm.get('id')
                        if existing_perms:
                            existing_perm = next((p for p in existing_perms if p.get(
                                'externalPermissionId') == perm_id), None)
                        else:
                            existing_perm = None

                        if existing_perm:
                            # Update existing permission
                            await self.store_permission(file_key, existing_perm.get('_key'), new_perm, transaction)
                        else:
                            # Get entity key from email for user/group
                            # Create new permission
                            if perm_type == 'user' or perm_type == 'group':
                                entity_key = await self.get_entity_id_by_email(new_perm.get('emailAddress'))
                                if not entity_key:
                                    logger.warning(
                                        f"⚠️ Skipping permission for non-existent user or group: {entity_key}")
                                    pass
                            elif perm_type == 'domain':
                                entity_key = org_id
                                if not entity_key:
                                    logger.warning(
                                        f"⚠️ Skipping permission for non-existent domain: {entity_key}")
                                    pass
                            else:
                                entity_key = None
                                # Skip if entity doesn't exist
                                if not entity_key:
                                    logger.warning(
                                        f"⚠️ Skipping permission for non-existent entity: {entity_key}")
                                    pass
                            if entity_key != 'anyone' and entity_key:
                                logger.info(
                                    "🚀 Storing permission for file %s and entity %s: %s", file_key, entity_key, new_perm)
                                await self.store_permission(file_key, entity_key, new_perm, transaction)

                if perm_type == "anyone":
                    # For anyone type, add permission directly to anyone collection
                    for new_perm in new_perms:
                        permission_data = {
                            'type': 'anyone',
                            'file_key': file_key,
                            'organization': org_id,
                            'role': new_perm.get('role', 'READER'),
                            'externalPermissionId': new_perm.get('id'),
                            'lastUpdatedTimestampAtSource': timestamp,
                            'active': True
                        }
                    # Store/update permission
                        await self.batch_upsert_nodes([permission_data], collection=CollectionNames.ANYONE.value)

            logger.info(
                "✅ Successfully processed all permissions for file %s", file_key)
            return True

        except Exception as e:
            logger.error("❌ Failed to process permissions: %s", str(e))
            if transaction:
                raise
            return False

    async def process_mail_permissions(
        self,
        message_id: str,
        permissions_data: List[Dict]
    ) -> bool:
        """Process mail permissions"""
        try:
            logger.info("🚀 Processing permissions for message %s", message_id)

        except Exception as e:
            logger.error("❌ Failed to process permissions: %s", str(e))
            return False

    def _get_access_level(self, role: str) -> int:
        """Convert role to numeric access level for easy comparison"""
        role_levels = {
            'owner': 100,
            'organizer': 90,
            'fileorganizer': 80,
            'writer': 70,
            'commenter': 60,
            'reader': 50,
            'none': 0
        }
        return role_levels.get(role.lower(), 0)

    async def _cleanup_old_permissions(
        self,
        file_id: str,
        current_entities: Set[Tuple[str, str]]
    ) -> None:
        """Mark old access edges as inactive"""
        try:
            logger.info("🚀 Cleaning up old permissions for file %s", file_id)

            query = """
            FOR edge IN permissions
                FILTER edge._from == @file_id
                AND NOT ([PARSE_IDENTIFIER(edge._to).key, PARSE_IDENTIFIER(edge._to).collection] IN @current_entities)
                UPDATE edge WITH {
                    hasAccess: false,
                } IN permissions
            """

            self.db.aql.execute(
                query,
                bind_vars={
                    'file_id': f'records/{file_id}',
                    'current_entities': list(current_entities),
                }
            )
            logger.info("✅ Old access edges cleaned up successfully")

        except Exception as e:
            logger.error(
                "❌ Failed to cleanup old access edges: %s",
                str(e)
            )
            return

    def _permission_needs_update(self, existing: Dict, new: Dict) -> bool:
        """Check if permission data needs to be updated"""
        logger.info("🚀 Checking if permission data needs to be updated")
        relevant_fields = ['role', 'permissionDetails', 'active']

        for field in relevant_fields:
            if field in new:
                if field == 'permissionDetails':
                    if json.dumps(
                        new[field],
                        sort_keys=True) != json.dumps(
                        existing.get(
                            field,
                            {}),
                            sort_keys=True):
                        logger.info("✅ Permission data needs to be updated")
                        return True
                elif new[field] != existing.get(field):
                    logger.info("✅ Permission data needs to be updated")
                    return True

        logger.info("✅ Permission data does not need to be updated")
        return False

    async def get_file_access_history(self, file_id: str, transaction: Optional[TransactionDatabase] = None) -> List[Dict]:
        """Get historical access information for a file"""
        try:
            logger.info(
                "🚀 Getting historical access information for file %s", file_id)
            query = """
            FOR perm IN permissions
                FILTER perm._from == @file_id
                SORT perm.lastUpdatedTimestampAtSource DESC
                RETURN {
                    entity: DOCUMENT(perm._to),
                    permission: perm
                }
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query,
                bind_vars={'file_id': f'records/{file_id}'}
            )
            logger.info("✅ File access history retrieved successfully")
            return list(cursor)

        except Exception as e:
            logger.error(
                "❌ Failed to get file access history: %s",
                str(e)
            )
            return []

    async def delete_records_and_relations(self, node_key: str, hard_delete: bool = False, transaction: Optional[TransactionDatabase] = None) -> bool:
        """Delete a node and its edges from the specified collection"""
        try:
            logger.info("🚀 Deleting node %s from collection Records, Files (hard_delete=%s)",
                        node_key, hard_delete)

            if not hard_delete:
                # Soft delete query
                query = """
                UPDATE { _key: @node_key } 
                WITH {isDeleted: true , deletedAt: timestamp} 
                
                IN records
                RETURN true
                """

                bind_vars = {
                    'node_key': node_key,
                    'timestamp':  get_epoch_timestamp_in_ms()
                }

            else:
                # Hard delete query
                logger.info("Hard deleting edges and nodes")
                query = """
                LET record_id_full = CONCAT('records/', @node_key)
                LET file_id_full = CONCAT('files/', @node_key)
                
                // Remove edges first
                LET removed_edges = (
                    FOR edge IN @@recordRelations
                        FILTER edge._from == record_id_full OR edge._to == record_id_full
                        REMOVE edge IN @@recordRelations
                        RETURN OLD
                )
                
                // Remove record
                LET removed_record = (
                    FOR doc IN @@records
                        FILTER doc._key == @node_key
                        REMOVE doc IN @@records
                        RETURN OLD
                )
                
                // Remove file
                LET removed_file = (
                    FOR doc IN @@files
                        FILTER doc._key == @node_key
                        REMOVE doc IN @@files
                        RETURN OLD
                )
                
                RETURN {
                    edges_removed: LENGTH(removed_edges),
                    record_removed: LENGTH(removed_record) > 0,
                    file_removed: LENGTH(removed_file) > 0
                }
                """

                bind_vars = {
                    'node_key': node_key,
                    '@recordRelations': CollectionNames.RECORD_RELATIONS.value,
                    '@records': CollectionNames.RECORDS.value,
                    '@files': CollectionNames.FILES.value
                    
                }
                db = transaction if transaction else self.db

                cursor = db.aql.execute(query, bind_vars=bind_vars)
                result = list(cursor)

                logger.info("🚀 Node Deletion Result: %s", result)

            # Use provided transaction or direct db connection
            logger.info("✅ Node %s and its edges %s deleted: %s",
                        node_key, "hard" if hard_delete else "soft", result)
            return True

        except Exception as e:
            logger.error(
                "❌ Failed to delete node %s: %s",
                node_key,
                str(e)
            )
            if transaction:
                raise
            return False

    async def get_users(self, org_id, active=True) -> List[Dict]:
        """
        Fetch all active users from the database who belong to the organization.

        Args:
            org_id (str): Organization ID
            active (bool): Filter for active users only if True

        Returns:
            List[Dict]: List of user documents with their details
        """
        try:
            logger.info("🚀 Fetching all users from database")

            if active:
                # Updated query to check for belongsTo edge with organization
                query = """
                FOR edge IN belongsTo
                    FILTER edge._to == CONCAT('organizations/', @org_id)
                    AND edge.entityType == 'ORGANIZATION'
                    LET user = DOCUMENT(edge._from)
                    FILTER user.isActive == true
                    RETURN user
                """
                
            else:
                query = """
                FOR edge IN belongsTo
                    FILTER edge._to == CONCAT('organizations/', @org_id)
                    AND edge.entityType == 'ORGANIZATION'
                    LET user = DOCUMENT(edge._from)
                    RETURN user
                """

            # Execute query with organization parameter
            cursor = self.db.aql.execute(
                query, bind_vars={'org_id': org_id})
            users = list(cursor)

            logger.info("✅ Successfully fetched %s users", len(users))
            return users

        except Exception as e:
            logger.error("❌ Failed to fetch users: %s", str(e))
            return []

    async def save_to_people_collection(self, entity_id: str, email: str) -> bool:
        """Save an entity to the people collection if it doesn't already exist"""
        try:
            logger.info(
                "🚀 Checking if entity %s exists in people collection", entity_id)
            # has() checks document _key, not field values
            # Need to query by entity_id field instead
            query = "FOR doc IN people FILTER doc.entity_id == @entity_id RETURN doc"
            exists = list(self.db.aql.execute(
                query, bind_vars={'entity_id': entity_id}))
            if not exists:
                logger.info(
                    "➕ Entity does not exist, saving to people collection")
                self.db.collection(CollectionNames.PEOPLE.value).insert(
                    {'_key': entity_id, 'email': email})
                logger.info(
                    "✅ Entity %s saved to people collection", entity_id)
                return True
            else:
                logger.info(
                    "⏩ Entity %s already exists in people collection", entity_id)
                return False
        except Exception as e:
            logger.error(
                "❌ Error saving entity to people collection: %s", str(e))
            return False

    async def get_all_pageTokens(self):
        """Get all page tokens from the pageTokens collection.

        Returns:
            list: List of page token documents, or empty list if none found or error occurs
        """
        try:
            if not self.db.has_collection(CollectionNames.PAGE_TOKENS.value):
                logger.warning("❌ pageTokens collection does not exist")
                return []

            query = """
            FOR doc IN pageTokens
                RETURN doc
            """

            result = list(self.db.aql.execute(query))

            logger.info("✅ Retrieved %d page tokens", len(result))
            return result

        except Exception as e:
            logger.error("❌ Error retrieving page tokens: %s", str(e))
            return []

    async def get_key_by_external_file_id(self, external_file_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[str]:
        """
        Get internal file key using the external file ID

        Args:
            external_file_id (str): External file ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal file key if found, None otherwise
        """
        try:
            logger.info(
                "🚀 Retrieving internal key for external file ID %s", external_file_id)

            query = f"""
            FOR record IN {CollectionNames.RECORDS.value}
                FILTER record.externalRecordId == @external_file_id
                RETURN record._key
            """

            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={'external_file_id': external_file_id})
            result = next(cursor, None)

            if result:
                logger.info(
                    "✅ Successfully retrieved internal key for external file ID %s", external_file_id)
                return result
            else:
                logger.warning(
                    "⚠️ No internal key found for external file ID %s", external_file_id)
                return None

        except Exception as e:
            logger.error(
                "❌ Failed to retrieve internal key for external file ID %s: %s",
                external_file_id,
                str(e)
            )
            return None

    async def get_key_by_external_message_id(self, external_message_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[str]:
        """
        Get internal message key using the external message ID

        Args:
            external_message_id (str): External message ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal message key if found, None otherwise
        """
        try:
            logger.info(
                "🚀 Retrieving internal key for external message ID %s", external_message_id)

            query = f"""
            FOR doc IN {CollectionNames.RECORDS.value}
                FILTER doc.externalRecordId == @external_message_id
                RETURN doc._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={'external_message_id': external_message_id})
            result = next(cursor, None)

            if result:
                logger.info(
                    "✅ Successfully retrieved internal key for external message ID %s", external_message_id)
                return result
            else:
                logger.warning(
                    "⚠️ No internal key found for external message ID %s", external_message_id)
                return None

        except Exception as e:
            logger.error(
                "❌ Failed to retrieve internal key for external message ID %s: %s",
                external_message_id,
                str(e)
            )
            return None
        
    async def get_key_by_attachment_id(self, external_attachment_id: str, transaction: Optional[TransactionDatabase] = None) -> Optional[str]:
        """
        Get internal attachment key using the external attachment ID

        Args:
            external_attachment_id (str): External attachment ID to look up
            transaction (Optional[TransactionDatabase]): Optional database transaction

        Returns:
            Optional[str]: Internal attachment key if found, None otherwise
        """
        try:
            logger.info(
                "🚀 Retrieving internal key for external attachment ID %s", external_attachment_id)

            query = """
            FOR record IN records
                FILTER record.externalRecordId == @external_attachment_id
                RETURN record._key
            """
            db = transaction if transaction else self.db
            cursor = db.aql.execute(
                query, bind_vars={'external_attachment_id': external_attachment_id})
            result = next(cursor, None)

            if result:
                logger.info(
                    "✅ Successfully retrieved internal key for external attachment ID %s", external_attachment_id)
                return result
            else:
                logger.warning(
                    "⚠️ No internal key found for external attachment ID %s", external_attachment_id)
                return None

        except Exception as e:
            logger.error(
                "❌ Failed to retrieve internal key for external attachment ID %s: %s",
                external_attachment_id,
                str(e)
            )
            return None
        
    async def get_user_by_user_id(self, user_id: str) -> Optional[Dict]:
        """Get user by user ID"""
        try:
            query = f"""
                FOR user IN {CollectionNames.USERS.value}
                    FILTER user.userId == @user_id
                    RETURN user
            """
            cursor = self.db.aql.execute(query, bind_vars={'user_id': user_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            logger.error(f"Error getting user by user ID: {str(e)}")
            return None

    async def get_account_type(self, org_id: str) -> str:
        """Get account type for an organization
        
        Args:
            org_id (str): Organization ID
            
        Returns:
            str: Account type ('individual' or 'business')
        """
        try:
            query = f"""
                FOR org IN organizations
                    FILTER org._key == @org_id
                    RETURN org.accountType
            """
            cursor = self.db.aql.execute(query, bind_vars={'org_id': org_id})
            result = next(cursor, None)
            return result
        except Exception as e:
            logger.error(f"Error getting account type: {str(e)}")
            return None

    async def update_user_sync_state(self, user_email: str, state: str, service_type: str = Connectors.GOOGLE_DRIVE.value) -> Optional[Dict]:
        """
        Update user's sync state in USER_APP_RELATION collection for specific service

        Args:
            user_email (str): Email of the user
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            service_type (str): Type of service 

        Returns:
            Optional[Dict]: Updated relation document if successful, None otherwise
        """
        try:
            logger.info("🚀 Updating %s sync state for user %s to %s", 
                       service_type, user_email, state)
            
            user_key = await self.get_entity_id_by_email(user_email)

            # Get user key and app key based on service type and update the sync state
            query = f"""
            LET app = FIRST(FOR a IN {CollectionNames.APPS.value} 
                          FILTER LOWER(a.name) == LOWER(@service_type)
                          RETURN {{
                              _key: a._key,
                              name: a.name
                          }})
            
            LET edge = FIRST(
                FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', app._key)
                    UPDATE rel WITH {{ syncState: @state }} IN {CollectionNames.USER_APP_RELATION.value}
                    RETURN NEW
            )
            
            RETURN edge
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    'user_key': user_key,
                    'service_type': service_type,
                    'state': state
                }
            )

            result = next(cursor, None)
            if result:
                logger.info("✅ Successfully updated %s sync state for user %s to %s", 
                           service_type, user_email, state)
                return result

            logger.warning("⚠️ UPDATE:No user-app relation found for email %s and service %s", 
                          user_email, service_type)
            return None

        except Exception as e:
            logger.error("❌ Failed to update user %s sync state: %s", 
                        service_type, str(e))
            return None
        
    async def get_user_sync_state(self, user_email: str, service_type: str = Connectors.GOOGLE_DRIVE.value) -> Optional[Dict]:
        """
        Get user's sync state from USER_APP_RELATION collection for specific service

        Args:
            user_email (str): Email of the user
            service_type (str): Type of service

        Returns:
            Optional[Dict]: Relation document containing sync state if found, None otherwise
        """
        try:
            logger.info("🔍 Getting %s sync state for user %s", 
                       service_type, user_email)
            
            user_key = await self.get_entity_id_by_email(user_email)
            
            query = f"""
            LET app = FIRST(FOR a IN {CollectionNames.APPS.value} 
                          FILTER LOWER(a.name) == LOWER(@service_type)
                          RETURN {{
                              _key: a._key,
                              name: a.name
                          }})
            
            LET edge = FIRST(
                FOR rel in {CollectionNames.USER_APP_RELATION.value}
                    FILTER rel._from == CONCAT('users/', @user_key)
                    FILTER rel._to == CONCAT('apps/', app._key)
                    RETURN rel
            )
            
            RETURN edge
            """

            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    'user_key': user_key,
                    'service_type': service_type,
                }
            )

            result = next(cursor, None)
            if result:
                logger.info("Result: %s", result)
                logger.info("✅ Found %s sync state for user %s: %s", 
                           service_type, user_email, result['syncState'])
                return result
            
            logger.warning("⚠️ GET:No user-app relation found for email %s and service %s", 
                            user_email, service_type)
            return None

        except Exception as e:
            logger.error("❌ Failed to get user %s sync state: %s", 
                        service_type, str(e))
            return None

    async def update_drive_sync_state(self, drive_id: str, state: str) -> Optional[Dict]:
        """
        Update drive's sync state in drives collection
        
        Args:
            drive_id (str): ID of the drive
            state (str): Sync state (NOT_STARTED, RUNNING, PAUSED, COMPLETED)
            additional_data (dict, optional): Additional data to update
            
        Returns:
            Optional[Dict]: Updated drive document if successful, None otherwise
        """
        try:
            logger.info("🚀 Updating sync state for drive %s to %s", drive_id, state)
            
            update_data = {
                'sync_state': state,
                'last_sync_update': datetime.now(timezone.utc).isoformat()
            }
                            
            query = """
            FOR drive IN drives
                FILTER drive._key == @drive_id
                UPDATE drive WITH @update IN drives
                RETURN NEW
            """
            
            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    'drive_id': drive_id,
                    'update': update_data
                }
            )
            
            result = next(cursor, None)
            if result:
                logger.info("✅ Successfully updated sync state for drive %s", drive_id)
                return result
                
            logger.warning("⚠️ No drive found with ID %s", drive_id)
            return None
            
        except Exception as e:
            logger.error("❌ Failed to update drive sync state: %s", str(e))
            return None

    async def get_drive_sync_state(self, drive_id: str) -> Optional[str]:
        """Get sync state for a specific drive
        
        Args:
            drive_id (str): ID of the drive to check
            
        Returns:
            Optional[str]: Current sync state of the drive ('NOT_STARTED', 'IN_PROGRESS', 'PAUSED', 'COMPLETED', 'FAILED')
                          or None if drive not found
        """
        try:
            logger.info("🔍 Getting sync state for drive %s", drive_id)
            
            query = """
            FOR drive IN drives
                FILTER drive._key == @drive_id
                RETURN drive.sync_state
            """
            
            result = list(self.db.aql.execute(
                query,
                bind_vars={'drive_id': drive_id}
            ))
            
            if result:
                logger.debug("✅ Found sync state for drive %s: %s", drive_id, result[0])
                return result[0]
            
            logger.debug("No sync state found for drive %s, assuming NOT_STARTED", drive_id)
            return 'NOT_STARTED'

        except Exception as e:
            logger.error("❌ Error getting drive sync state: %s", str(e))
            return None

    async def check_edge_exists(self, from_id: str, to_id: str, collection: str) -> bool:
        """Check if an edge exists between two nodes in a specified collection."""
        try:
            logger.info("🔍 Checking if edge exists from %s to %s in collection %s", from_id, to_id, collection)
            
            query = """
            FOR edge IN @@collection
                FILTER edge._from == @from_id AND edge._to == @to_id
                RETURN edge
            """
            
            cursor = self.db.aql.execute(
                query,
                bind_vars={
                    'from_id': from_id,
                    'to_id': to_id,
                    '@collection': collection
                }
            )
            
            result = next(cursor, None)
            exists = result is not None
            logger.info("✅ Edge exists: %s", exists)
            return exists

        except Exception as e:
            logger.error("❌ Error checking edge existence: %s", str(e))
            return False
