from typing import Optional
from uuid import uuid4

from app.config.constants.arangodb import (
    AccountType,
    CollectionNames,
    Connectors,
    ConnectorScopes,
)
from app.connectors.core.base.event_service.event_service import BaseEventService
from app.connectors.core.factory.connector_factory import ConnectorFactory
from app.containers.connector import (
    ConnectorAppContainer,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EntityEventService(BaseEventService):
    def __init__(self, logger,
                graph_provider: IGraphDBProvider,
                app_container: ConnectorAppContainer) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
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

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org
            await self.graph_provider.batch_upsert_nodes(
                [org_data], CollectionNames.ORGS.value
            )

            # Get departments with orgId == None using provider
            departments = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.DEPARTMENTS.value,
                filters={"orgId": None}
            )

            # Create relationships between org and departments
            org_department_relations = []
            for department in departments:
                dept_id = department.get("id") or department.get("_key")
                relation_data = {
                    "from_id": payload["orgId"],
                    "from_collection": CollectionNames.ORGS.value,
                    "to_id": dept_id,
                    "to_collection": CollectionNames.DEPARTMENTS.value,
                    "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                org_department_relations.append(relation_data)

            if org_department_relations:
                await self.graph_provider.batch_create_edges(
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

            # Automatically create Knowledge Base connector instance for the new org
            await self.__create_kb_connector_app_instance(payload['orgId'], payload.get('userId'))

            # Create "All" team for the org (first user will be added with OWNER in userAdded)
            await self.__create_all_team_for_org(payload['orgId'], payload.get('userId'))

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

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org
            await self.graph_provider.batch_upsert_nodes(
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

            # Convert _key to id for provider
            org_data["id"] = org_data.pop("_key")

            # Batch upsert org with isActive = False
            await self.graph_provider.batch_upsert_nodes(
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
            existing_user = await self.graph_provider.get_user_by_email(
                payload["email"]
            )

            current_timestamp = get_epoch_timestamp_in_ms()

            if existing_user:
                # existing_user is a User object, get id from it
                user_key = existing_user.id
                user_data = {
                    "id": user_key,
                    "userId": payload["userId"],
                    "orgId": payload["orgId"],
                    "isActive": True,
                    "updatedAtTimestamp": current_timestamp,
                }
            else:
                user_key = str(uuid4())
                user_data = {
                    "id": user_key,
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
            org = await self.graph_provider.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            # Batch upsert user
            await self.graph_provider.batch_upsert_nodes(
                [user_data], CollectionNames.USERS.value
            )

            # Create edge between org and user if it doesn't exist
            edge_data = {
                "from_id": user_data["id"],
                "from_collection": CollectionNames.USERS.value,
                "to_id": payload["orgId"],
                "to_collection": CollectionNames.ORGS.value,
                "entityType": "ORGANIZATION",
                "createdAtTimestamp": current_timestamp,
            }
            await self.graph_provider.batch_create_edges(
                [edge_data],
                CollectionNames.BELONGS_TO.value,
            )

            # Get or create knowledge base for the user
            kb_name = self._kb_name_from_user_added_payload(payload)
            await self.__get_or_create_knowledge_base(user_key, payload["userId"], payload["orgId"], name=kb_name)

            # Create user-app relation edge for KB app
            await self.__create_user_kb_app_relation(user_key, payload["orgId"])

            # Get or create "All" team for org and add user with PERMISSION edge
            await self.__get_or_create_all_team_and_add_user(payload["orgId"], user_key)

            # Only proceed with app connections if syncAction is 'immediate'
            if payload["syncAction"] == "immediate":
                # Get all apps associated with the org
                org_apps = await self.graph_provider.get_org_apps(payload["orgId"])

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
            # Find existing user by userId
            existing_user = await self.graph_provider.get_user_by_user_id(
                payload["userId"],
            )

            if not existing_user:
                self.logger.error(f"User not found with userId: {payload['userId']}")
                return False

            user_id = existing_user.get("id") or existing_user.get("_key")
            user_data = {
                "id": user_id,
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
            await self.graph_provider.batch_upsert_nodes(
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
            # Find existing user by email
            existing_user_id = await self.graph_provider.get_entity_id_by_email(
                payload["email"]
            )
            if not existing_user_id:
                self.logger.error(f"User not found with mail: {payload['email']}")
                return False

            user_data = {
                "id": existing_user_id,
                "orgId": payload["orgId"],
                "email": payload["email"],
                "isActive": False,
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            # Batch upsert user with isActive = False
            await self.graph_provider.batch_upsert_nodes(
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
            apps = payload["apps"]
            sync_action = payload.get("syncAction", "none")
            connector_id = payload.get("connectorId", "")
            scope = payload.get("scope", ConnectorScopes.PERSONAL.value)
            # Get org details to check account type
            org = await self.graph_provider.get_document(
                org_id, CollectionNames.ORGS.value
            )
            if not org:
                self.logger.error(f"Organization not found: {org_id}")
                return False

            for app_name in apps:
                if sync_action == "immediate":
                    # Start sync for each app (connector already initialized for standard connectors)
                    await self.__handle_sync_event(
                        event_type=f"{app_name.lower()}.start",
                        value={
                            "orgId": org_id,
                            "connector":app_name,
                            "connectorId":connector_id,
                            "scope": scope,
                        },
                    )

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
            connector_id = payload.get("connectorId", "")

            if not org_id or not apps:
                self.logger.error("Both orgId and apps are required to disable apps")
                return False

            # Stop sync for each app
            self.logger.info(f"📥 Processing app disabled event: {payload}")

            # Set apps as inactive
            app_updates = []
            for app_name in apps:
                app_doc = await self.graph_provider.get_document(
                    connector_id, CollectionNames.APPS.value
                )
                if not app_doc:
                    self.logger.error(f"App not found: {app_name}")
                    return False
                app_data = {
                    "id": connector_id,
                    "name": app_doc.get("name", app_doc.get("_key", connector_id)),
                    "type": app_doc.get("type"),
                    "appGroup": app_doc.get("appGroup"),
                    "isActive": False,
                    "createdAtTimestamp": app_doc.get("createdAtTimestamp"),
                    "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                }
                app_updates.append(app_data)

            # Update apps in database
            await self.graph_provider.batch_upsert_nodes(
                app_updates, CollectionNames.APPS.value
            )

            self.logger.info(f"✅ Successfully disabled apps for org: {org_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error disabling apps: {str(e)}")
            return False

    def _kb_name_from_user_added_payload(self, payload: dict) -> str:
        """Compute KB display name from userAdded event: fullName's Private or email's Private."""
        full_name = (payload.get("fullName") or "").strip()
        if full_name:
            return f"{full_name}'s Private"
        email = (payload.get("email") or "").strip()
        if email:
            return f"{email}'s Private"
        return "Private"

    async def __create_all_team_for_org(self, org_id: str, created_by_user_id: Optional[str] = None) -> None:
        """
        Create the "All" team when an org is created. Called from __handle_org_created.
        created_by_user_id is the external userId (e.g. MongoDB id); graph user key is set when first user is added.
        """
        try:
            current_timestamp = get_epoch_timestamp_in_ms()
            team_key = f"all_{org_id}"
            created_by = created_by_user_id if created_by_user_id else "system"
            team_node = {
                "id": team_key,
                "name": "All",
                "description": "All organization members",
                "createdBy": created_by,
                "orgId": org_id,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            await self.graph_provider.batch_upsert_nodes(
                [team_node], CollectionNames.TEAMS.value
            )
            self.logger.info(f"Created 'All' team for org {org_id}")
        except Exception as e:
            self.logger.error(f"Failed to create 'All' team for org {org_id}: {str(e)}", exc_info=True)

    async def __get_or_create_all_team_and_add_user(self, org_id: str, user_key: str) -> None:
        """
        Add the user to the org's "All" team with a PERMISSION edge.
        Team is created in __handle_org_created; if missing (e.g. legacy org), create it here.
        First user gets OWNER; subsequent users get READER. Skips if user already in team.
        """
        try:
            current_timestamp = get_epoch_timestamp_in_ms()
            existing = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.TEAMS.value,
                filters={"orgId": org_id, "name": "All"},
            )
            if not existing:
                team_key = f"all_{org_id}"
                team_node = {
                    "id": team_key,
                    "name": "All",
                    "description": "All organization members",
                    "createdBy": user_key,
                    "orgId": org_id,
                    "createdAtTimestamp": current_timestamp,
                    "updatedAtTimestamp": current_timestamp,
                }
                await self.graph_provider.batch_upsert_nodes(
                    [team_node], CollectionNames.TEAMS.value
                )
                self.logger.info(f"Created 'All' team for org {org_id} (fallback, team was missing)")
            else:
                team_key = existing[0].get("_key") or existing[0].get("id")

            existing_edge = await self.graph_provider.get_edge(
                from_id=user_key,
                from_collection=CollectionNames.USERS.value,
                to_id=team_key,
                to_collection=CollectionNames.TEAMS.value,
                collection=CollectionNames.PERMISSION.value,
            )
            if existing_edge:
                self.logger.debug(
                    f"User {user_key} already in 'All' team for org {org_id}, skipping"
                )
                return

            team_with_users = await self.graph_provider.get_team_with_users(
                team_id=team_key, user_key=user_key
            )
            members = (team_with_users or {}).get("members", [])
            role = "OWNER" if len(members) == 0 else "READER"

            if role == "OWNER" and existing:
                try:
                    await self.graph_provider.update_node(
                        team_key,
                        CollectionNames.TEAMS.value,
                        {"createdBy": user_key, "updatedAtTimestamp": current_timestamp},
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to update createdBy for team {team_key}: {e}")

            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": team_key,
                "to_collection": CollectionNames.TEAMS.value,
                "type": "USER",
                "role": role,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            await self.graph_provider.batch_create_edges(
                [permission_edge], CollectionNames.PERMISSION.value
            )
            self.logger.info(
                f"Added user {user_key} to 'All' team for org {org_id} with role {role}"
            )
        except Exception as e:
            self.logger.error(
                f"Failed to add user to 'All' team: {str(e)}", exc_info=True
            )

    async def __get_or_create_knowledge_base(
        self,
        user_key: str,
        userId: str,
        orgId: str,
        name: str = "Private"
    ) -> dict:
        """Get or create a knowledge base for a user, with root folder and permissions."""
        try:
            if not userId or not orgId:
                self.logger.error("Both User ID and Organization ID are required to get or create a knowledge base")
                return {}

            # Check if a knowledge base already exists for this user in this organization
            existing_kbs = await self.graph_provider.get_nodes_by_filters(
                collection=CollectionNames.RECORD_GROUPS.value,
                filters={
                    "createdBy": userId,
                    "orgId": orgId,
                    "groupType": Connectors.KNOWLEDGE_BASE.value,
                    "connectorName": Connectors.KNOWLEDGE_BASE.value,
                }
            )
            # Filter out deleted knowledge bases
            existing_kbs = [kb for kb in existing_kbs if not kb.get("isDeleted", False)]

            if existing_kbs:
                self.logger.info(f"Found existing knowledge base for user {userId} in organization {orgId}")
                return existing_kbs[0]

            kb_app = await self.__get_or_create_kb_app_for_org(orgId, userId)
            if not kb_app:
                self.logger.error(f"Failed to get or create KB app for org {orgId}")
                return {}
            kb_app_id = kb_app.get('_key')
            current_timestamp = get_epoch_timestamp_in_ms()
            kb_key = str(uuid4())

            kb_data = {
                "id": kb_key,
                "createdBy": userId,
                "orgId": orgId,
                "groupName": name,
                "groupType": Connectors.KNOWLEDGE_BASE.value,
                "connectorName": Connectors.KNOWLEDGE_BASE.value,
                "connectorId": kb_app_id,  # Link KB to the app
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }
            permission_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": kb_key,
                "to_collection": CollectionNames.RECORD_GROUPS.value,
                "externalPermissionId": "",
                "type": "USER",
                "role": "OWNER",
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
                "lastUpdatedTimestampAtSource": current_timestamp,
            }

            # Create belongs_to edge from record group to app
            belongs_to_edge = {
                "from_id": kb_key,
                "from_collection": CollectionNames.RECORD_GROUPS.value,
                "to_id": kb_app_id,
                "to_collection": CollectionNames.APPS.value,
                "entityType": Connectors.KNOWLEDGE_BASE.value,
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }

            # Insert all in transaction
            # TODO: Use transaction instead of batch upsert
            await self.graph_provider.batch_upsert_nodes([kb_data], CollectionNames.RECORD_GROUPS.value)
            await self.graph_provider.batch_create_edges([permission_edge], CollectionNames.PERMISSION.value)
            await self.graph_provider.batch_create_edges([belongs_to_edge], CollectionNames.BELONGS_TO.value)

            self.logger.info(f"Created new knowledge base for user {userId} in organization {orgId} with app connection")
            return {
                "kb_id": kb_key,
                "name": name,
                "created_at": current_timestamp,
                "updated_at": current_timestamp,
                "app_id": kb_app_id,
                "success": True
            }

        except Exception as e:
            self.logger.error(f"Failed to get or create knowledge base: {str(e)}")
            return {}

    async def __create_kb_connector_app_instance(self, org_id: str, created_by_user_id: Optional[str] = None) -> Optional[dict]:
        """
        Automatically create a Knowledge Base connector instance when an org is created.

        Args:
            org_id: Organization ID
            created_by_user_id: User ID who created the org (optional, can be None for system-created)

        Returns:
            App document if successful, None otherwise
        """
        try:
            self.logger.info(f"📦 Creating Knowledge Base connector instance for org: {org_id}")

            # Get KB connector metadata from the connector class
            from app.connectors.sources.localKB.connector import (
                KB_CONNECTOR_NAME,
                KnowledgeBaseConnector,
            )

            # Check if KB connector metadata exists
            if not hasattr(KnowledgeBaseConnector, '_connector_metadata'):
                self.logger.warning("Knowledge Base connector metadata not found, skipping auto-creation")
                return None

            metadata = KnowledgeBaseConnector._connector_metadata
            connector_name = metadata.get('name', KB_CONNECTOR_NAME)
            app_group = metadata.get('appGroup', 'Local Storage')

            # Check if KB connector instance already exists for this org
            org_apps = await self.graph_provider.get_org_apps(org_id)
            existing_kb_app = next(
                (app for app in org_apps if app.get('type') == Connectors.KNOWLEDGE_BASE.value),
                None
            )

            if existing_kb_app:
                kb_key = existing_kb_app.get('_key') or existing_kb_app.get('id')
                self.logger.info(
                    f"Knowledge Base connector instance already exists for org {org_id} "
                    f"(id: {kb_key}), skipping creation"
                )
                return existing_kb_app

            # KB connector uses "NONE" auth type
            selected_auth_type = 'NONE'
            # KB connector is team-scoped
            scope = ConnectorScopes.TEAM.value

            # Use system user if no created_by_user_id provided
            created_by = created_by_user_id if created_by_user_id else "system"

            # Create connector instance document
            instance_key = f"knowledgeBase_{org_id}"
            current_timestamp = get_epoch_timestamp_in_ms()

            instance_document = {
                'id': instance_key,
                'name': connector_name,
                'type': Connectors.KNOWLEDGE_BASE.value,
                'appGroup': app_group,
                'authType': selected_auth_type,
                'scope': scope,
                'isActive': True,  # KB is always active (local storage)
                'isAgentActive': True,  # KB supports agents
                'isConfigured': True,  # KB doesn't need configuration
                'isAuthenticated': True,  # KB doesn't need authentication (local storage)
                'createdBy': created_by,
                'updatedBy': created_by,
                'createdAtTimestamp': current_timestamp,
                'updatedAtTimestamp': current_timestamp
            }

            # Create instance in database
            await self.graph_provider.batch_upsert_nodes(
                [instance_document],
                CollectionNames.APPS.value
            )

            # Create relationship edge between organization and instance
            edge_document = {
                "from_id": org_id,
                "from_collection": CollectionNames.ORGS.value,
                "to_id": instance_key,
                "to_collection": CollectionNames.APPS.value,
                "createdAtTimestamp": current_timestamp,
            }

            await self.graph_provider.batch_create_edges(
                [edge_document],
                CollectionNames.ORG_APP_RELATION.value,
            )

            # Create connector instance and add to connectors_map so it is available in-process
            config_service = self.app_container.config_service()
            data_store_provider = await self.app_container.data_store()
            if not hasattr(self.app_container, 'connectors_map'):
                self.logger.info(f"Creating connectors_map for org: {org_id}")
                self.app_container.connectors_map = {}
            connector = await ConnectorFactory.create_and_start_sync(
                name="kb",
                logger=self.logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id=instance_key,
            )
            if connector:
                self.app_container.connectors_map[instance_key] = connector
                self.logger.info(
                    f"✅ KB connector instance (id: {instance_key}) added to connectors_map for org: {org_id}"
                )

            self.logger.info(
                f"✅ Successfully created Knowledge Base connector instance '{connector_name}' "
                f"(id: {instance_key}) for org: {org_id}"
            )
            return instance_document

        except Exception as e:
            self.logger.error(f"❌ Error creating KB connector instance for org {org_id}: {str(e)}")
            # Don't fail org creation if KB connector creation fails
            return None

    async def __get_or_create_kb_app_for_org(self, org_id: str, created_by_user_id: Optional[str] = None) -> Optional[dict]:
        """
        Get or create a Knowledge Base connector instance for an org.

        Args:
            org_id: Organization ID
            created_by_user_id: User ID (optional, defaults to "system")

        Returns:
            KB app document or None if failed
        """
        try:
            # Check if KB connector instance already exists for this org
            org_apps = await self.graph_provider.get_org_apps(org_id)
            existing_kb_app = next(
                (app for app in org_apps if app.get('type') == Connectors.KNOWLEDGE_BASE.value),
                None
            )

            if existing_kb_app:
                kb_key = existing_kb_app.get('_key') or existing_kb_app.get('id')
                self.logger.debug(f"Found existing KB app for org {org_id}: {kb_key}")
                return existing_kb_app

            # Create KB app if it doesn't exist
            self.logger.info(f"KB app not found for org {org_id}, creating one...")
            new_kb_app = await self.__create_kb_connector_app_instance(org_id, created_by_user_id)
            return new_kb_app

        except Exception as e:
            self.logger.error(f"❌ Error getting or creating KB app for org {org_id}: {str(e)}")
            return None

    async def __create_user_kb_app_relation(self, user_key: str, org_id: str) -> bool:
        """
        Create user-app relation edge between user and KB app for the organization.

        Args:
            user_key: User key
            org_id: Organization ID

        Returns:
            True if edge was created or already exists, False on error
        """
        try:
            # Get or create KB app for the org
            kb_app = await self.__get_or_create_kb_app_for_org(org_id)
            if not kb_app:
                self.logger.warning(f"KB app not found for org {org_id}, skipping user-app relation creation")
                return False

            kb_app_id = kb_app.get('_key') or kb_app.get('id')

            # Check if user-app relation already exists using graph_provider
            existing_edges = await self.graph_provider.get_edges_from_node(
                node_id=user_key,
                edge_collection=CollectionNames.USER_APP_RELATION.value
            )

            # Check if any edge points to the KB app
            edge_exists = any(
                edge.get('to_id') == kb_app_id or edge.get('_to') == f"{CollectionNames.APPS.value}/{kb_app_id}"
                for edge in existing_edges
            )

            if edge_exists:
                # Edge already exists
                self.logger.debug(f"User-app relation already exists for user {user_key} and KB app {kb_app_id}")
                return True

            # Create user-app relation edge
            current_timestamp = get_epoch_timestamp_in_ms()
            user_app_edge = {
                "from_id": user_key,
                "from_collection": CollectionNames.USERS.value,
                "to_id": kb_app_id,
                "to_collection": CollectionNames.APPS.value,
                "syncState": "NOT_STARTED",  # Required by schema - KB doesn't sync
                "lastSyncUpdate": current_timestamp,  # Required by schema
                "createdAtTimestamp": current_timestamp,
                "updatedAtTimestamp": current_timestamp,
            }

            await self.graph_provider.batch_create_edges(
                [user_app_edge],
                CollectionNames.USER_APP_RELATION.value
            )

            self.logger.info(f"✅ Created user-app relation for user {user_key} and KB app {kb_app_id}")
            return True

        except Exception as e:
            self.logger.error(f"❌ Error creating user-app relation for user {user_key}: {str(e)}")
            return False
