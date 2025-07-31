from app.connectors.core.base.event_service.event_service import BaseEventService
from app.config.utils.named_constants.arangodb_constants import AccountType
from app.config.utils.named_constants.arangodb_constants import Connectors
from app.config.utils.named_constants.arangodb_constants import CollectionNames
from app.setups.connector_setup import AppContainer, initialize_enterprise_account_services_fn, initialize_individual_account_services_fn
from app.connectors.sources.google.common.arango_service import ArangoService
from app.connectors.sources.google.common.sync_tasks import SyncTasks
from app.utils.time_conversion import get_epoch_timestamp_in_ms
import asyncio
from uuid import uuid4

class EntityEventService(BaseEventService):
    def __init__(self, logger, 
                sync_tasks: SyncTasks, 
                arango_service: ArangoService,
                app_container: AppContainer):
        self.logger = logger
        self.sync_tasks = sync_tasks
        self.arango_service = arango_service
        self.app_container = app_container

    async def process_event(self, event_type: str, value: dict) -> bool:
        """Handle entity-related events by calling appropriate handlers"""
        try:
            self.logger.info(f"Processing entity event: {event_type}")
            if event_type == "orgCreated":
                return await self.__handle_org_created(value)
            elif event_type == "orgUpdated":
                return await self.__handle_org_updated(value)
            elif event_type == "orgDeleted":
                return await self.__handle_org_deleted(value)
            elif event_type == "userAdded":    
                return await self.__handle_user_added(value)
            elif event_type == "userUpdated":
                return await self.__handle_user_updated(value)
            elif event_type == "userDeleted":
                return await self.__handle_user_deleted(value)
            elif event_type == "appEnabled":
                return await self.__handle_app_enabled(value)
            elif event_type == "appDisabled":
                return await self.__handle_app_disabled(value)
            elif event_type == "llmConfigured":
                return await self.__handle_llm_configured(value)
            elif event_type == "embeddingModelConfigured":
                return await self.__handle_embedding_configured(value)
            else:   
                self.logger.error(f"Unknown entity event type: {event_type}")
                return False
        except Exception as e:
            self.logger.error(f"Error processing entity event: {str(e)}")
            return False

    # ORG EVENTS
    async def __handle_org_created(self, payload: dict) -> bool:
        """Handle organization creation event"""

        accountType = (
            AccountType.ENTERPRISE.value
            if payload["accountType"] in [AccountType.BUSINESS.value, AccountType.ENTERPRISE.value]
            else AccountType.INDIVIDUAL.value
        )
        try:
            org_data = {
                "_key": payload["orgId"],
                "name": payload.get("registeredName", "Individual Account"),
                "accountType": accountType,
                "isActive": True,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert org
            await self.arango_service.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )

            # Write a query to get departments with orgId == None
            query = f"""
                FOR d IN {CollectionNames.DEPARTMENTS.value}
                FILTER d.orgId == null
                RETURN d
            """
            cursor = self.arango_service.db.aql.execute(query)
            departments = list(cursor)

            # Create relationships between org and departments
            org_department_relations = []
            for department in departments:
                relation_data = {
                    "_from": f"{CollectionNames.ORGS.value}/{payload['orgId']}",
                    "_to": f"{CollectionNames.DEPARTMENTS.value}/{department['_key']}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                org_department_relations.append(relation_data)

            if org_department_relations:
                await self.arango_service.batch_create_edges(
                    org_department_relations,
                    CollectionNames.ORG_DEPARTMENT_RELATION.value,
                )
                self.logger.info(
                    f"✅ Successfully created organization: {payload['orgId']} and relationships with departments"
                )
            else:
                self.logger.info(
                    f"✅ Successfully created organization: {payload['orgId']}"
                )

            return True

        except Exception as e:
            self.logger.error(f"❌ Error creating organization: {str(e)}")
            return False

    async def __handle_org_updated(self, payload: dict) -> bool:
        """Handle organization update event"""
        try:
            self.logger.info(f"📥 Processing org updated event: {payload}")
            org_data = {
                "_key": payload["orgId"],
                "name": payload["registeredName"],
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert org
            await self.arango_service.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )
            self.logger.info(
                f"✅ Successfully updated organization: {payload['orgId']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating organization: {str(e)}")
            return False

    async def __handle_org_deleted(self, payload: dict) -> bool:
        """Handle organization deletion event"""
        try:
            self.logger.info(f"📥 Processing org deleted event: {payload}")
            org_data = {
                "_key": payload["orgId"],
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert org with isActive = False
            await self.arango_service.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )
            self.logger.info(
                f"✅ Successfully soft-deleted organization: {payload['orgId']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting organization: {str(e)}")
            return False

    # USER EVENTS
    async def __handle_user_added(self, payload: dict) -> bool:
        """Handle user creation event"""
        try:
            self.logger.info(f"📥 Processing user added event: {payload}")
            # Check if user already exists by email
            existing_user = await self.arango_service.get_entity_id_by_email(
                payload["email"]
            )

            current_timestamp = get_epoch_timestamp_in_ms()

            if existing_user:
                user_key = existing_user
                user_data = {
                    "_key": existing_user,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "isActive": True,
                    "updatedAtTimestamp": current_timestamp,
                }
            else:
                user_key = str(uuid4())
                user_data = {
                    "_key": user_key,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "email": payload["email"],
                    "fullName": payload.get("fullName", ""),
                    "firstName": payload.get("firstName", ""),
                    "middleName": payload.get("middleName", ""),
                    "lastName": payload.get("lastName", ""),
                    "designation": payload.get("designation", ""),
                    "businessPhones": payload.get("businessPhones", []),
                    "isActive": True,
                    "createdAtTimestamp": current_timestamp,
                    "updatedAtTimestamp": current_timestamp,
                }

            # Get org details to check account type
            org_id = payload["orgId"]
            org = await self.arango_service.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            # Batch upsert user
            await self.arango_service.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )

            # Create edge between org and user if it doesn't exist
            edge_data = {
                "_to": f"{CollectionNames.ORGS.value}/{payload['orgId']}",
                "_from": f"{CollectionNames.USERS.value}/{user_data['_key']}",
                "entityType": "ORGANIZATION",
                "createdAtTimestamp": current_timestamp,
            }
            await self.arango_service.batch_create_edges(
                [edge_data],
                CollectionNames.BELONGS_TO.value,
            )

            # Get or create knowledge base for the user
            await self.__get_or_create_knowledge_base(user_key,payload["userId"], payload["orgId"])

            # Only proceed with app connections if syncAction is 'immediate'
            if payload["syncAction"] == "immediate":
                # Get all apps associated with the org
                org_apps = await self.arango_service.get_org_apps(payload["orgId"])

                for app in org_apps:
                    if app["name"].lower() in ["calendar"]:
                        self.logger.info("Skipping init")
                        continue

                    # Start sync for the specific user
                    await self._handle_sync_event(
                        event_type=f'{app["name"].lower()}.user',
                        value={"email": payload["email"]},
                    )

            self.logger.info(
                f"✅ Successfully created/updated user: {payload['email']}"
            )
            return True

        except Exception as e:
            self.logger.error(f"❌ Error creating/updating user: {str(e)}")
            return False

    async def __handle_user_updated(self, payload: dict) -> bool:
        """Handle user update event"""
        try:
            self.logger.info(f"📥 Processing user updated event: {payload}")
            # Find existing user by email
            existing_user = await self.arango_service.get_user_by_user_id(
                payload["userId"],
            )

            if not existing_user:
                self.logger.error(f"User not found with userId: {payload['userId']}")
                return False
            user_data = {
                "_key": existing_user["_key"],
                "userId": payload["userId"],
                "orgId": payload["orgId"],
                "email": payload["email"],
                "fullName": payload.get("fullName", ""),
                "firstName": payload.get("firstName", ""),
                "middleName": payload.get("middleName", ""),
                "lastName": payload.get("lastName", ""),
                "designation": payload.get("designation", ""),
                "businessPhones": payload.get("businessPhones", []),

                "isActive": True,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Add only non-null optional fields
            optional_fields = [
                "fullName",
                "firstName",
                "middleName",
                "lastName",
                "email",
            ]
            user_data.update(
                {
                    key: payload[key]
                    for key in optional_fields
                    if payload.get(key) is not None
                }
            )

            # Batch upsert user
            await self.arango_service.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )
            self.logger.info(f"✅ Successfully updated user: {payload['email']}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error updating user: {str(e)}")
            return False

    async def __handle_user_deleted(self, payload: dict) -> bool:
        """Handle user deletion event"""
        try:
            self.logger.info(f"📥 Processing user deleted event: {payload}")
            # Find existing user by userId
            existing_user = await self.arango_service.get_entity_id_by_email(
                payload["email"]
            )
            if not existing_user:
                self.logger.error(f"User not found with mail: {payload['email']}")
                return False

            user_data = {
                "_key": existing_user,
                "orgId": payload["orgId"],
                "email": payload["email"],
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert user with isActive = False
            await self.arango_service.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )
            self.logger.info(f"✅ Successfully soft-deleted user: {payload['email']}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error deleting user: {str(e)}")
            return False

    # APP EVENTS
    async def __handle_app_enabled(self, payload: dict) -> bool:
        """Handle app enabled event"""
        try:
            self.logger.info(f"📥 Processing app enabled event: {payload}")
            org_id = payload["orgId"]
            app_group = payload["appGroup"]
            app_group_id = payload["appGroupId"]
            apps = payload["apps"]
            sync_action = payload.get("syncAction", "none")

            # Get org details to check account type
            org = await self.arango_service.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            # Create app entities
            app_docs = []
            for app_name in apps:

                app_data = {
                    "_key": f"{org_id}_{app_name}",
                    "name": app_name,
                    "type": app_name,
                    "appGroup": app_group,
                    "appGroupId": app_group_id,
                    "isActive": True,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                app_docs.append(app_data)

            # Batch create apps
            await self.arango_service.batch_upsert_nodes(
                app_docs, CollectionNames.APPS.value
            )

            # Create edges between org and apps
            org_app_edges = []
            for app in app_docs:
                edge_data = {
                    "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                    "_to": f"{CollectionNames.APPS.value}/{app['_key']}",
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                org_app_edges.append(edge_data)

            await self.arango_service.batch_create_edges(
                org_app_edges,
                CollectionNames.ORG_APP_RELATION.value,
            )

            # Check if Google apps (Drive, Gmail) are enabled
            enabled_apps = set(apps)

            if enabled_apps:
                self.logger.info(f"Enabled apps are: {enabled_apps}")
                # Initialize services based on account type
                if self.app_container:
                    accountType = org["accountType"]
                    # Use the existing app container to initialize services
                    if accountType == AccountType.ENTERPRISE.value or accountType == AccountType.BUSINESS.value:
                        await initialize_enterprise_account_services_fn(
                            org_id, self.app_container
                        )
                    elif accountType == AccountType.INDIVIDUAL.value:
                        await initialize_individual_account_services_fn(
                            org_id, self.app_container
                        )
                    else:
                        self.logger.error("Account Type not valid")
                        return False
                    self.logger.info(
                        f"✅ Successfully initialized services for account type: {org['accountType']}"
                    )
                else:
                    self.logger.warning(
                        "App container not provided, skipping service initialization"
                    )

                user_type = (
                    AccountType.ENTERPRISE.value
                    if org["accountType"] in [AccountType.ENTERPRISE.value, AccountType.BUSINESS.value]
                    else AccountType.INDIVIDUAL.value
                )

                # Handle enterprise/business account type
                if user_type == AccountType.ENTERPRISE.value:
                    active_users = await self.arango_service.get_users(
                        org_id, active=True
                    )

                    for app_name in enabled_apps:
                        if app_name in [Connectors.GOOGLE_CALENDAR.value]:
                            self.logger.info(f"Skipping init for {app_name}")
                            continue

                        # Initialize app (this will fetch and create users)
                        await self._handle_sync_event(
                            event_type=f"{app_name.lower()}.init",
                            value={"orgId": org_id},
                        )

                        await asyncio.sleep(5)

                        if sync_action == "immediate":
                            # Start sync for all users
                            await self._handle_sync_event(
                                event_type=f"{app_name.lower()}.start",
                                value={"orgId": org_id},
                            )
                            await asyncio.sleep(5)

                # For individual accounts, create edges between existing active users and apps
                else:
                    active_users = await self.arango_service.get_users(
                        org_id, active=True
                    )

                    # First initialize each app
                    for app_name in enabled_apps:
                        if app_name in [Connectors.GOOGLE_CALENDAR.value]:
                            self.logger.info(f"Skipping init for {app_name}")
                            continue

                        # Initialize app
                        await self._handle_sync_event(
                            event_type=f"{app_name.lower()}.init",
                            value={"orgId": org_id},
                        )

                        await asyncio.sleep(5)

                    # Then create edges and start sync if needed
                    for user in active_users:
                        for app in app_docs:
                            if sync_action == "immediate":
                                # Start sync for individual user
                                if app["name"] in [Connectors.GOOGLE_CALENDAR.value]:
                                    self.logger.info("Skipping start")
                                    continue

                                await self._handle_sync_event(
                                    event_type=f'{app["name"].lower()}.start',
                                    value={
                                        "orgId": org_id,
                                        "email": user["email"],
                                    },
                                )
                                await asyncio.sleep(5)

            self.logger.info(f"✅ Successfully enabled apps for org: {org_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error enabling apps: {str(e)}")
            return False

    async def __handle_app_disabled(self, payload: dict) -> bool:
        """Handle app disabled event"""
        try:
            org_id = payload["orgId"]
            apps = payload["apps"]

            # Stop sync for each app
            self.logger.info(f"📥 Processing app disabled event: {payload}")

            # Set apps as inactive
            app_updates = []
            for app_name in apps:
                app_doc = await self.arango_service.get_document(
                    f"{org_id}_{app_name}", CollectionNames.APPS.value
                )
                app_data = {
                    "_key": f"{org_id}_{app_name}",  # Construct the app _key
                    "name": app_doc["name"],
                    "type": app_doc["type"],
                    "appGroup": app_doc["appGroup"],
                    "appGroupId": app_doc["appGroupId"],
                    "isActive": False,
                    "createdAtTimestamp": app_doc["createdAtTimestamp"],
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                app_updates.append(app_data)

            # Update apps in database
            await self.arango_service.batch_upsert_nodes(
                app_updates, CollectionNames.APPS.value
            )

            self.logger.info(f"✅ Successfully disabled apps for org: {org_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error disabling apps: {str(e)}")
            return False

    async def __handle_llm_configured(self, payload: dict) -> bool:
        """Handle LLM configured event"""
        try:
            self.logger.info("📥 Processing LLM configured event in Query Service")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling LLM configured event: {str(e)}")
            return False

    async def __handle_embedding_configured(self, payload: dict) -> bool:
        """Handle embedding configured event"""
        try:
            self.logger.info(
                "📥 Processing embedding configured event in Query Service"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling embedding configured event: {str(e)}")
            return False
        
    async def __get_or_create_knowledge_base(self, user_key: str, userId: str, orgId: str, name: str = "Default") -> dict:
        """Get or create a knowledge base for a user"""
        try:
            if not userId or not orgId:
                self.logger.error("Both User ID and Organization ID are required to get or create a knowledge base")
                return None

            # Check if a knowledge base already exists for this user in this organization
            query = f"""
            FOR kb IN {CollectionNames.KNOWLEDGE_BASE.value}
                FILTER kb.userId == @userId AND kb.orgId == @orgId AND kb.isDeleted == false
                RETURN kb
            """
            bind_vars = {"userId": userId, "orgId": orgId}

            # Use the correct pattern for your ArangoDB Python driver
            cursor = self.arango_service.db.aql.execute(query, bind_vars=bind_vars)
            existing_kbs = [doc for doc in cursor]

            if existing_kbs and len(existing_kbs) > 0:
                self.logger.info(f"Found existing knowledge base for user {userId} in organization {orgId}")
                return existing_kbs[0]

            # Create a new knowledge base
            current_timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid4())
            kb_data = {
                "_key": kb_key,
                "userId": userId,
                "orgId": orgId,
                "name": name,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
                "isDeleted": False,
                "isArchived": False,
            }

            # Save the knowledge base
            await self.arango_service.batch_upsert_nodes(
                [kb_data], CollectionNames.KNOWLEDGE_BASE.value
            )

            # Create permission edge from user to knowledge base with OWNER role
            permission_edge = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.KNOWLEDGE_BASE.value}/{kb_key}",
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
                "lastUpdatedTimestampAtSource": current_timestamp,
            }

            await self.arango_service.batch_create_edges(
                [permission_edge],
                CollectionNames.PERMISSIONS_TO_KNOWLEDGE_BASE.value,
            )

            self.logger.info(f"Created new knowledge base for user {userId} in organization {orgId}")
            return kb_data

        except Exception as e:
            self.logger.error(f"Failed to get or create knowledge base: {str(e)}")
            return None
