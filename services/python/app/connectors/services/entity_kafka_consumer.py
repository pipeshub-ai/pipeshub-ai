import asyncio
import json
import time
from typing import Dict, List
from uuid import uuid4

import aiohttp
from confluent_kafka import Consumer, KafkaError, Producer
from dependency_injector.wiring import inject

from app.config.configuration_service import KafkaConfig, config_node_constants
from app.config.utils.named_constants.arangodb_constants import (
    AccountType,
    CollectionNames,
    Connectors,
)

# Import required services
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.notion.notion_credentials_handler import (
    NotionCredentialsHandler,
)
from app.connectors.sources.notion.notion_service import NotionService
from app.setups.connector_setup import (
    initialize_enterprise_account_services_fn,
    initialize_individual_account_services_fn,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class EntityKafkaRouteConsumer:
    def __init__(
        self, logger, config_service, arango_service, routes=[], app_container=None
    ) -> None:
        self.logger = logger
        self.producer = None
        self.consumer = None
        self.running = False
        self.config_service = config_service
        self.arango_service = arango_service
        self.routes = routes
        self.processed_messages: Dict[str, List[int]] = {}
        self.app_container = app_container  # Store the app container reference
        self.route_mapping = {
            "entity-events": {
                "orgCreated": self.handle_org_created,
                "orgUpdated": self.handle_org_updated,
                "orgDeleted": self.handle_org_deleted,
                "userAdded": self.handle_user_added,
                "userUpdated": self.handle_user_updated,
                "userDeleted": self.handle_user_deleted,
                "appEnabled": self.handle_app_enabled,
                "appDisabled": self.handle_app_disabled,
                "llmConfigured": self.handle_llm_configured,
                "embeddingModelConfigured": self.handle_embedding_configured,
            },
        }

    async def create_consumer_and_producer(self) -> None:
        """Initialize the Kafka consumer and producer"""
        try:
            async def get_kafka_config() -> dict:
                kafka_config = await self.config_service.get_config(
                    config_node_constants.KAFKA.value
                )
                brokers = kafka_config['brokers']
                return {
                    'bootstrap.servers': ",".join(brokers),
                    'group.id': 'record_consumer_group',
                    'auto.offset.reset': 'earliest',
                    'enable.auto.commit': True,
                    'isolation.level': 'read_committed',
                    'enable.partition.eof': False,
                    'client.id': KafkaConfig.CLIENT_ID_RECORDS.value
                }

            KAFKA_CONFIG = await get_kafka_config()

            self.consumer = Consumer(KAFKA_CONFIG)
            # Initialize producer with the same broker config
            producer_config = {
                'bootstrap.servers': KAFKA_CONFIG['bootstrap.servers'],
                'client.id': 'entity_producer'
            }
            self.producer = Producer(producer_config)

            # Add a small delay to allow for topic creation
            time.sleep(2)
            # Subscribe to the two main topics
            self.consumer.subscribe(['entity-events'])
            self.logger.info("Successfully subscribed to topics: entity-events")
        except Exception as e:
            self.logger.error(f"Failed to create consumer: {e}")
            raise

    def is_message_processed(self, message_id: str) -> bool:
        """Check if a message has already been processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        return (
            topic_partition in self.processed_messages
            and offset in self.processed_messages[topic_partition]
        )

    def mark_message_processed(self, message_id: str) -> None:
        """Mark a message as processed."""
        topic_partition = "-".join(message_id.split("-")[:-1])
        offset = int(message_id.split("-")[-1])
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)

    @inject
    async def process_message(self, message) -> bool:
        """Process incoming Kafka messages and route them to appropriate handlers"""
        message_id = None
        try:
            message_id = f"{message.topic()}-{message.partition()}-{message.offset()}"
            self.logger.debug(f"Processing message {message_id}")

            if self.is_message_processed(message_id):
                self.logger.info(f"Message {message_id} already processed, skipping")
                return True

            topic = message.topic()
            message_value = message.value()
            value = None
            event_type = None

            # Message decoding and parsing
            try:
                if isinstance(message_value, bytes):
                    message_value = message_value.decode("utf-8")
                    self.logger.debug(f"Decoded bytes message for {message_id}")

                if isinstance(message_value, str):
                    try:
                        value = json.loads(message_value)
                        # Handle double-encoded JSON
                        if isinstance(value, str):
                            value = json.loads(value)
                            self.logger.debug("Handled double-encoded JSON message")

                        event_type = value.get("eventType")
                        self.logger.debug(
                            f"Parsed message {message_id}: type={type(value)}, event_type={event_type}"
                        )
                    except json.JSONDecodeError as e:
                        self.logger.error(
                            f"JSON parsing failed for message {message_id}: {str(e)}\n"
                            f"Raw message: {message_value[:1000]}..."  # Log first 1000 chars
                        )
                        return False
                else:
                    self.logger.error(
                        f"Unexpected message value type for {message_id}: {type(message_value)}"
                    )
                    return False

            except UnicodeDecodeError as e:
                self.logger.error(
                    f"Failed to decode message {message_id}: {str(e)}\n"
                    f"Raw bytes: {message_value[:100]}..."  # Log first 100 bytes
                )
                return False

            # Validation
            if not event_type:
                self.logger.error(f"Missing event_type in message {message_id}")
                return False

            if topic not in self.route_mapping:
                self.logger.error(f"Unknown topic {topic} for message {message_id}")
                return False

            # Route and handle message
            try:
                if topic == "sync-events":
                    self.logger.info(f"Processing sync event: {event_type}")
                    return await self._handle_sync_event(event_type, value)
                elif topic == "entity-events":
                    self.logger.info(f"Processing entity event: {event_type}")
                    return await self._handle_entity_event(event_type, value)
                else:
                    self.logger.warning(
                        f"Unhandled topic {topic} for message {message_id}"
                    )
                    return False

            except asyncio.TimeoutError:
                self.logger.error(
                    f"Timeout while processing {event_type} event in message {message_id}"
                )
                return False
            except ValueError as e:
                self.logger.error(
                    f"Validation error processing {event_type} event: {str(e)}"
                )
                return False
            except Exception as e:
                self.logger.error(
                    f"Error processing {event_type} event in message {message_id}: {str(e)}",
                    exc_info=True,
                )
                return False

        except Exception as e:
            self.logger.error(
                f"Unexpected error processing message {message_id if message_id else 'unknown'}: {str(e)}",
                exc_info=True,
            )
            return False
        finally:
            if message_id:
                self.mark_message_processed(message_id)

    async def _handle_sync_event(self, event_type: str, value: dict) -> bool:
        """Handle sync-related events by sending them to the sync-events topic"""
        try:
            # Prepare the message
            message = {
                'eventType': event_type,
                'payload': value,
                'timestamp': get_epoch_timestamp_in_ms()
            }

            # Convert message to JSON string
            message_str = json.dumps(message)

            # Send the message to sync-events topic
            self.producer.produce(
                'sync-events',
                value=message_str,
                callback=lambda err, msg: self.logger.error(f"Failed to deliver message: {err}") if err else None
            )

            # Flush to ensure the message is sent
            self.producer.flush()

            self.logger.info(f"Successfully sent sync event: {event_type}")
            return True

        except Exception as e:
            self.logger.error(f"Error sending sync event: {str(e)}")
            return False

    async def _handle_entity_event(self, event_type: str, value: dict) -> bool:
        """Handle entity-related events by calling appropriate ArangoDB methods"""
        handler = self.route_mapping["entity-events"].get(event_type)
        if not handler:
            self.logger.error(f"Unknown entity event type: {event_type}")
            return False

        return await handler(value["payload"])

    async def _get_or_create_knowledge_base(self, user_key: str, userId: str, orgId: str, name: str = "Default") -> dict:
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

    # ORG EVENTS
    async def handle_org_created(self, payload: dict) -> bool:
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

    async def handle_org_updated(self, payload: dict) -> bool:
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

    async def handle_org_deleted(self, payload: dict) -> bool:
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
    async def handle_user_added(self, payload: dict) -> bool:
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
            await self._get_or_create_knowledge_base(user_key,payload["userId"], payload["orgId"])

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

    async def handle_user_updated(self, payload: dict) -> bool:
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

    async def handle_user_deleted(self, payload: dict) -> bool:
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
    async def handle_app_enabled(self, payload: dict) -> bool:
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
                if app_group == 'Google Workspace':
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

                elif app_group == 'Notion':
                    self.logger.info("📥 Processing Notion app enabled event")

                    try:
                        # Initialize services
                        kafka_service = KafkaService(self.config_service, self.logger)
                        notion_credentials_handler = NotionCredentialsHandler(
                            logger=self.logger,
                            config_service=self.config_service,
                            arango_service=self.arango_service
                        )

                        # # Get the global NotionApp singleton
                        # notion_app = NotionApp(self.logger)
                        # self.logger.info(f"📱 Using NotionApp singleton: {notion_app}")

                        # # Get secrets from credentials handler
                        notion_secrets_response = await notion_credentials_handler.get_notion_secret(org_id)

                        integration_secrets = notion_secrets_response.get('integrationSecrets', [])
                        # # Load integrations into the global NotionApp
                        # loaded_integrations = notion_app.load_from_secrets_response(org_id, notion_secrets_response)
                        # self.logger.info(f"Found {loaded_integrations} Notion integration(s)")

                        # if loaded_integrations == 0:
                        #     self.logger.warning("No Notion integrations found")
                        #     return False

                        # # Get all integrations for this org from the singleton
                        # org_integrations = notion_app.get_org_integrations(org_id)

                        # Process each integration separately
                        successful_integrations = 0
                        failed_integrations = 0

                        for i, integration_secret in enumerate(integration_secrets):
                            try:
                                # self.logger.info(f"🔄 Processing Notion integration {i+1}/{len(org_integrations)} "
                                #             f"(org: {org_id}, workspace: {workspace_id})")

                                # Initialize Notion Service for this specific secret
                                workspace_id=None
                                url = "https://api.notion.com/v1/users/me"

                                async with aiohttp.ClientSession() as session:
                                    async with session.get(url, headers={
                                        "Authorization": f"Bearer {integration_secret}",
                                        "Notion-Version": "2022-06-28",
                                        "Content-Type": "application/json",
                                    }) as response:
                                        if response.status != 200:
                                            error_text = await response.text()
                                            self.logger.warning(f"Failed to fetch workspace info for integration {i}: {error_text}")
                                            continue

                                        workspace_data = await response.json()

                                        # Extract workspace ID from the response
                                        current_workspace_id = workspace_data.get("id", "")

                                        # Match workspace_id (remove dashes for comparison)
                                        workspace_id=current_workspace_id.replace("-", "")


                                notion_service = NotionService(
                                    integration_secret=integration_secret,
                                    org_id=org_id,
                                    workspace_id= workspace_id,  # Pass workspace_id if supported
                                    logger=self.logger,
                                    arango_service=self.arango_service,
                                    kafka_service=kafka_service,
                                    config_service=self.config_service
                                )

                                # Process the async generator properly
                                sync_completed = False
                                async for result in notion_service.fetch_and_create_notion_records():
                                    step = result.get("step")
                                    status = result.get("status")
                                    message = result.get("message")
                                    data = result.get("data", {})

                                    if status == "started":
                                        self.logger.info(f"🔄 {message}")
                                    elif status == "completed":
                                        if step == "sync":
                                            # Final completion
                                            self.logger.info(f"✅ {message}")
                                            self.logger.info(f"   📊 Summary: {data.get('user_count', 0)} users, "
                                                        f"{data.get('page_count', 0)} pages, "
                                                        f"{data.get('database_count', 0)} databases")
                                            sync_completed = True
                                        else:
                                            # Individual step completion
                                            self.logger.info(f"✅ {message}")
                                    elif status == "failed":
                                        # Error occurred
                                        self.logger.error(f"❌ {message}")
                                        raise Exception(result.get("error", "Unknown error"))

                                if sync_completed:
                                    self.logger.info(f"✅ Successfully processed Notion integration {i+1} "
                                                f"(workspace: {integration_secret})")
                                    successful_integrations += 1
                                else:
                                    self.logger.warning(f"⚠️ Notion integration {i+1} completed but sync status unclear "
                                                    f"(workspace: {integration_secret})")

                            except Exception as e:
                                self.logger.error(f"❌ Error processing Notion integration {i+1} "
                                                f"(workspace: {integration_secret}): {str(e)}")
                                failed_integrations += 1
                                # Continue with next integration instead of returning False immediately
                                continue

                        # Log final results
                        if successful_integrations > 0:
                            self.logger.info(
                                f"✅ Successfully processed {successful_integrations} Notion integration(s). "
                                f"Failed: {failed_integrations}"
                            )

                            # Log global NotionApp statistics
                            # global_stats = notion_app.get_stats()
                            # self.logger.info(f"📊 Global NotionApp Stats: {global_stats}")

                            # Consider this successful if at least one integration worked
                            return True
                        else:
                            self.logger.error("❌ All Notion integrations failed")
                            return False

                    except Exception as e:
                        self.logger.error(f"❌ Error processing Notion app enabled event: {str(e)}")
                        return False

        except Exception as e:
            self.logger.error(f"❌ Error enabling apps: {str(e)}")
            return False

        self.logger.info(f"✅ Successfully enabled apps for org: {org_id}")
        return True



    async def handle_app_disabled(self, payload: dict) -> bool:
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

    async def handle_llm_configured(self, payload: dict) -> bool:
        """Handle LLM configured event"""
        try:
            self.logger.info("📥 Processing LLM configured event in Query Service")
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling LLM configured event: {str(e)}")
            return False

    async def handle_embedding_configured(self, payload: dict) -> bool:
        """Handle embedding configured event"""
        try:
            self.logger.info(
                "📥 Processing embedding configured event in Query Service"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Error handling embedding configured event: {str(e)}")
            return False

    async def consume_messages(self) -> None:
        """Main consumption loop."""
        try:
            self.logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    message = self.consumer.poll(1.0)

                    if message is None:
                        await asyncio.sleep(0.1)
                        continue

                    if message.error():
                        if message.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        else:
                            self.logger.error(f"Kafka error: {message.error()}")
                            continue

                    self.logger.info(f"Received message: {message}")
                    success = await self.process_message(message)

                    if success:
                        self.consumer.commit(message)
                        self.logger.info(
                            f"Committed offset for topic-partition {message.topic()}-{message.partition()} at offset {message.offset()}"
                        )

                except asyncio.CancelledError:
                    self.logger.info("Kafka consumer task cancelled")
                    break
                except Exception as e:
                    self.logger.error(f"Error processing Kafka message: {e}")
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            if self.consumer:
                self.consumer.close()
                self.logger.info("Kafka consumer closed")

    async def start(self) -> None:
        """Start the consumer."""
        self.running = True
        await self.create_consumer_and_producer()

    def stop(self) -> None:
        """Stop the consumer."""
        self.running = False
