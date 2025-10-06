import asyncio
from uuid import uuid4

from app.config.constants.arangodb import AccountType, CollectionNames, Connectors
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.containers.connector import (
    ConnectorAppContainer,
    initialize_enterprise_google_account_services_fn,
    initialize_individual_google_account_services_fn,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EntityEventService(BaseEventService):
    def __init__(self, logger,
                arango_service: ArangoService,
                app_container: ConnectorAppContainer) -> None:
        self.logger = logger
        self.arango_service = arango_service
        self.app_container = app_container

    async def process_event(self, event_type: str, payload: dict) -> bool:
        """Handle entity-related events by calling appropriate handlers"""
        try:
            self.logger.info(f"Processing entity event: {event_type}")
            if event_type == "orgCreated":
                return await self.__handle_org_created(payload)
            elif event_type == "orgUpdated":
                return await self.__handle_org_updated(payload)
            elif event_type == "orgDeleted":
                return await self.__handle_org_deleted(payload)
            elif event_type == "userAdded":
                return await self.__handle_user_added(payload)
            elif event_type == "userUpdated":
                return await self.__handle_user_updated(payload)
            elif event_type == "userDeleted":
                return await self.__handle_user_deleted(payload)
            elif event_type == "appEnabled":
                return await self.__handle_app_enabled(payload)
            elif event_type == "appDisabled":
                return await self.__handle_app_disabled(payload)
            else:
                self.logger.error(f"Unknown entity event type: {event_type}")
                return False
        except Exception as e:
            self.logger.error(f"Error processing entity event: {str(e)}")
            return False

    async def __handle_sync_event(self,event_type: str, value: dict) -> bool:
        """Handle sync-related events by sending them to the sync-events topic"""
        try:
            # Prepare the message
            message = {
                'eventType': event_type,
                'payload': value,
                'timestamp': get_epoch_timestamp_in_ms()
            }

            # Send the message to sync-events topic using aiokafka
            await self.app_container.messaging_producer.send_message(
                topic='sync-events',
                message=message
            )

            self.logger.info(f"Successfully sent sync event: {event_type}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending sync event: {str(e)}")
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
            cursor = self.arango_service.db.aql.execute(query) # type: ignore
            departments = list(cursor) # type: ignore

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
                    await self.__handle_sync_event(
                        event_type=f'{app["name"].lower()}.user',
                        value={
                            "email": payload["email"],
                            "connector":app["name"]
                        },
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

    async def __handle_google_app_account_services(self, org_id: str, account_type: str, app_names: list[str]) -> bool:
        """Handle Google account services"""
        if account_type == AccountType.ENTERPRISE.value or account_type == AccountType.BUSINESS.value:
            await initialize_enterprise_google_account_services_fn(org_id, self.app_container, app_names)
        elif account_type == AccountType.INDIVIDUAL.value:
            await initialize_individual_google_account_services_fn(org_id, self.app_container, app_names)
        else:
            self.logger.error("Account Type not valid")
            return False

    # APP EVENTS
    async def __handle_app_enabled(self, payload: dict) -> bool:
        """Handle app enabled event"""
        try:
            self.logger.info(f"📥 Processing app enabled event: {payload}")
            org_id = payload["orgId"]
            app_group = payload["appGroup"]
            apps = payload["apps"]
            sync_action = payload.get("syncAction", "none")

            # Get org details to check account type
            org = await self.arango_service.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            # Check if Google apps (Drive, Gmail) are enabled
            enabled_apps = set(apps)

            if enabled_apps:
                self.logger.info(f"Enabled apps are: {enabled_apps}")
                # Initialize services based on account type
                if self.app_container and "google" in app_group.lower():
                    accountType = org["accountType"]
                    # Use the existing app container to initialize services
                    await self.__handle_google_app_account_services(org_id, accountType, enabled_apps)
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
                        await self.__handle_sync_event(
                            event_type=f"{app_name.lower()}.init",
                            value={
                                "orgId": org_id,
                                "connector":app_name
                            },
                        )

                        # TODO: Remove this sleep
                        await asyncio.sleep(5)

                        if sync_action == "immediate":
                            # Start sync for all users
                            await self.__handle_sync_event(
                                event_type=f"{app_name.lower()}.start",
                                value={
                                    "orgId": org_id,
                                    "connector":app_name
                                },
                            )
                            # TODO: Remove this sleep
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
                        await self.__handle_sync_event(
                            event_type=f"{app_name.lower()}.init",
                            value={
                                "orgId": org_id,
                                "connector":app_name
                            },
                        )
                        # TODO: Remove this sleep
                        await asyncio.sleep(5)

                    # Then create edges and start sync if needed
                    for user in active_users:
                        for app in enabled_apps:
                            if sync_action == "immediate":
                                # Start sync for individual user
                                if app in [Connectors.GOOGLE_CALENDAR.value]:
                                    self.logger.info("Skipping start")
                                    continue

                                await self.__handle_sync_event(
                                    event_type=f'{app.lower()}.start',
                                    value={
                                        "orgId": org_id,
                                        "email": user["email"],
                                        "connector":app
                                    },
                                )
                                # TODO: Remove this sleep
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

            if not org_id or not apps:
                self.logger.error("Both orgId and apps are required to disable apps")
                return False

            # Stop sync for each app
            self.logger.info(f"📥 Processing app disabled event: {payload}")

            # Set apps as inactive
            app_updates = []
            for app_name in apps:
                app_doc = await self.arango_service.get_document(
                    f"{org_id}_{app_name}", CollectionNames.APPS.value
                )
                if not app_doc:
                    self.logger.error(f"App not found: {app_name}")
                    return False
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

    async def __get_or_create_knowledge_base(
        self,
        user_key: str,
        userId: str,
        orgId: str,
        name: str = "Default"
    ) -> dict:
        """Get or create a knowledge base for a user, with root folder and permissions."""
        try:
            if not userId or not orgId:
                self.logger.error("Both User ID and Organization ID are required to get or create a knowledge base")
                return {}

            # Check if a knowledge base already exists for this user in this organization
            query = f"""
            FOR kb IN {CollectionNames.RECORD_GROUPS.value}
                FILTER kb.createdBy == @userId AND kb.orgId == @orgId AND kb.groupType == @kb_type AND kb.connectorName == @kb_connector AND (kb.isDeleted == false OR kb.isDeleted == null)
                RETURN kb
            """
            bind_vars = {
                "userId": userId,
                "orgId": orgId,
                "kb_type": Connectors.KNOWLEDGE_BASE.value,
                "kb_connector": Connectors.KNOWLEDGE_BASE.value,
            }
            cursor = self.arango_service.db.aql.execute(query, bind_vars=bind_vars) # type: ignore
            existing_kbs = [doc for doc in cursor] # type: ignore

            if existing_kbs:
                self.logger.info(f"Found existing knowledge base for user {userId} in organization {orgId}")
                return existing_kbs[0]

            # Create a new knowledge base with root folder and permissions in a transaction
            current_timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid4())
            folder_id = str(uuid4())

            kb_data = {
                "_key": kb_key,
                "createdBy": userId,
                "orgId": orgId,
                "groupName": name,
                "groupType": Connectors.KNOWLEDGE_BASE.value,
                "connectorName": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            root_folder_data = {
                "_key": folder_id,
                "orgId": orgId,
                "name": name,
                "isFile": False,
                "extension": None,
                "mimeType": "application/vnd.folder",
                "sizeInBytes": 0,
                "webUrl": f"/kb/{kb_key}/folder/{folder_id}",
                "path": f"/{name}"
            }
            permission_edge = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_key}",
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
                "lastUpdatedTimestampAtSource": current_timestamp,
            }
            folder_edge = {
                "_from": f"{CollectionNames.FILES.value}/{folder_id}",
                "_to": f"{CollectionNames.RECORD_GROUPS.value}/{kb_key}",
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            # Insert all in transaction
            # TODO: Use transaction instead of batch upsert
            await self.arango_service.batch_upsert_nodes([kb_data], CollectionNames.RECORD_GROUPS.value)
            await self.arango_service.batch_upsert_nodes([root_folder_data], CollectionNames.FILES.value)
            await self.arango_service.batch_create_edges([permission_edge], CollectionNames.PERMISSIONS_TO_KB.value)
            await self.arango_service.batch_create_edges([folder_edge], CollectionNames.BELONGS_TO.value)

            self.logger.info(f"Created new knowledge base for user {userId} in organization {orgId}")
            return {
                "kb_id": kb_key,
                "name": name,
                "root_folder_id": folder_id,
                "created_at": current_timestamp,
                "updated_at": current_timestamp,
                "success": True
            }

        except Exception as e:
            self.logger.error(f"Failed to get or create knowledge base: {str(e)}")
            return {}
