import asyncio
import hashlib
import os
import time
from datetime import datetime, timedelta
from typing import Dict

from arango import ArangoClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.zammad.connector import ZammadConnector
from app.services.kafka_consumer import KafkaConsumerManager
from app.sources.client.zammad.zammad import ZammadClient, ZammadTokenConfig
from app.sources.external.zammad.zammad import ZammadDataSource, ZammadResponse
from app.utils.logger import create_logger
from app.utils.time_conversion import get_epoch_timestamp_in_ms


async def test_sync_sub_organizations() -> None:
    """Test syncing Zammad sub-organizations as RecordGroups"""
    org_id = "68f43ef86bd82ed71df4e201"

    # 1. Initialize logger first
    logger = create_logger("zammad_connector")

    # Helper function to clean ArangoDB
    async def clean_database(arango_service: BaseArangoService) -> None:
        """Clean/delete existing data from ArangoDB collections"""
        try:
            db = arango_service.db
            collections_to_clean = [
                CollectionNames.APPS.value,
                CollectionNames.ORGS.value,
                CollectionNames.USERS.value,
                CollectionNames.GROUPS.value,
                CollectionNames.RECORD_GROUPS.value,
                CollectionNames.RECORDS.value,
                CollectionNames.ORG_APP_RELATION.value,
                CollectionNames.BELONGS_TO.value,
                CollectionNames.PERMISSION.value,
            ]

            for collection_name in collections_to_clean:
                try:
                    if db.has_collection(collection_name):
                        collection = db.collection(collection_name)
                        collection.truncate()
                        logger.info(f"Truncated collection: {collection_name}")
                except Exception as e:
                    logger.warning(f"Could not truncate collection {collection_name}: {e}")

            logger.info("Database cleaned successfully")

        except Exception as e:
            logger.error(f"Error cleaning database: {e}", exc_info=True)
            raise

    # Helper function to create prerequisites in ArangoDB
    async def create_prerequisites(arango_service: BaseArangoService) -> None:
        """Create organization and ZAMMAD app in ArangoDB"""
        try:
            # Create organization
            org = {
                "_key": org_id,
                "accountType": "enterprise",
                "name": "Test Org",
                "isActive": True,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)
            logger.info(f"Created organization: {org_id}")

            # Create ZAMMAD app following the proper schema
            app_name = "Zammad"
            app_type = Connectors.ZAMMAD.value
            app_group = "Support & Helpdesk"
            app_group_id = hashlib.sha256(app_group.encode()).hexdigest()
            app_key = f"{org_id}_{app_name.replace(' ', '_').upper()}"

            zammad_app = {
                "_key": app_key,
                "name": app_name,
                "type": app_type,
                "appGroup": app_group,
                "appGroupId": app_group_id,
                "authType": "API_KEY",
                "isActive": True,
                "isConfigured": False,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            logger.info(f"Creating ZAMMAD app with key: {app_key}")
            result = await arango_service.batch_upsert_nodes([zammad_app], CollectionNames.APPS.value)
            logger.info(f"ZAMMAD app creation result: {result}")

            # Verify app was created
            db = arango_service.db
            apps_collection = db.collection(CollectionNames.APPS.value)
            app_doc = apps_collection.get(app_key)
            logger.info(f"Verified ZAMMAD app in DB: {app_doc}")

            # Link app to organization (basic_edge_schema only allows _from, _to, createdAtTimestamp)
            await arango_service.batch_create_edges([{
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{app_key}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }], CollectionNames.ORG_APP_RELATION.value)
            logger.info("Created edge from org to app")

        except Exception as e:
            logger.error(f"Error creating prerequisites: {e}", exc_info=True)
            raise

    # 2. Initialize services
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)

    # 3. Clean database (remove existing data)
    await clean_database(arango_service)

    # 4. Create prerequisites (organization and app)
    await create_prerequisites(arango_service)
    logger.info(f"Created organization {org_id} and ZAMMAD app in ArangoDB")

    # 5. Read Zammad credentials from environment
    zammad_token = os.getenv("ZAMMAD_TOKEN")
    zammad_base_url = os.getenv("ZAMMAD_BASE_URL")

    if not zammad_token or not zammad_base_url:
        logger.error("ZAMMAD_TOKEN and ZAMMAD_BASE_URL environment variables must be set.")
        return

    # 6. Store Zammad config in the in-memory config store
    config = {
        "auth": {
            "baseUrl": zammad_base_url,
            "token": zammad_token
        }
    }

    await key_value_store.create_key("/services/connectors/zammad/config", config)

    logger.info("\nDEBUG: Stored config at key: /services/connectors/zammad/config")
    stored_config = await key_value_store.get_key("/services/connectors/zammad/config")
    logger.info(f"\nDEBUG: Retrieved config: {stored_config}")

    # 7. Create and initialize the Zammad connector
    try:
        zammad_connector: ZammadConnector = await ZammadConnector.create_connector(logger, data_store_provider, config_service)
        await zammad_connector.init()
        logger.info("Zammad connector initialized successfully.")

        # 8. Call only sync_sub_organizations_as_record_groups method
        logger.info("\n=== Starting sync_sub_organizations_as_record_groups ===")
        await zammad_connector.run_sync()
        logger.info("=== Completed sync_sub_organizations_as_record_groups ===\n")

    except Exception as e:
        logger.error(f"An error occurred during Zammad sub-org sync: {e}", exc_info=True)


async def test_incremental_sync() -> None:
    """Test incremental sync by creating new entities and validating detection"""
    org_id = "68f43ef86bd82ed71df4e201"

    # 1. Initialize logger
    logger = create_logger("zammad_incremental_test")

    # Helper function to clean ArangoDB
    async def clean_database(arango_service: BaseArangoService) -> None:
        """Clean/delete existing data from ArangoDB collections"""
        try:
            db = arango_service.db
            collections_to_clean = [
                CollectionNames.APPS.value,
                CollectionNames.ORGS.value,
                CollectionNames.USERS.value,
                CollectionNames.GROUPS.value,
                CollectionNames.RECORD_GROUPS.value,
                CollectionNames.RECORDS.value,
                CollectionNames.ORG_APP_RELATION.value,
                CollectionNames.BELONGS_TO.value,
                CollectionNames.PERMISSION.value,
                CollectionNames.SYNC_POINTS.value,
            ]

            for collection_name in collections_to_clean:
                try:
                    if db.has_collection(collection_name):
                        collection = db.collection(collection_name)
                        collection.truncate()
                        logger.info(f"Truncated collection: {collection_name}")
                except Exception as e:
                    logger.warning(f"Could not truncate collection {collection_name}: {e}")

            logger.info("Database cleaned successfully")

        except Exception as e:
            logger.error(f"Error cleaning database: {e}", exc_info=True)
            raise

    # Helper function to create prerequisites in ArangoDB
    async def create_prerequisites(arango_service: BaseArangoService) -> None:
        """Create organization and ZAMMAD app in ArangoDB"""
        try:
            # Create organization
            org = {
                "_key": org_id,
                "accountType": "enterprise",
                "name": "Test Org",
                "isActive": True,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)
            logger.info(f"Created organization: {org_id}")

            # Create ZAMMAD app
            app_name = "Zammad"
            app_type = Connectors.ZAMMAD.value
            app_group = "Support & Helpdesk"
            app_group_id = hashlib.sha256(app_group.encode()).hexdigest()
            app_key = f"{org_id}_{app_name.replace(' ', '_').upper()}"

            zammad_app = {
                "_key": app_key,
                "name": app_name,
                "type": app_type,
                "appGroup": app_group,
                "appGroupId": app_group_id,
                "authType": "API_KEY",
                "isActive": True,
                "isConfigured": False,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
            }
            logger.info(f"Creating ZAMMAD app with key: {app_key}")
            await arango_service.batch_upsert_nodes([zammad_app], CollectionNames.APPS.value)

            # Link app to organization
            await arango_service.batch_create_edges([{
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{app_key}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }], CollectionNames.ORG_APP_RELATION.value)
            logger.info("Created edge from org to app")

        except Exception as e:
            logger.error(f"Error creating prerequisites: {e}", exc_info=True)
            raise

    # Helper to count records in ArangoDB
    async def count_records(arango_service: BaseArangoService) -> Dict[str, int]:
        """Count entities in ArangoDB"""
        try:
            db = arango_service.db
            counts = {
                "users": db.collection(CollectionNames.USERS.value).count(),
                "tickets": db.collection(CollectionNames.RECORDS.value).count(),
                "organizations": db.collection(CollectionNames.RECORD_GROUPS.value).count(),
            }
            return counts
        except Exception as e:
            logger.error(f"Error counting records: {e}", exc_info=True)
            return {}

    # 2. Initialize services
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")
    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)

    # 3. Clean database
    await clean_database(arango_service)

    # 4. Create prerequisites
    await create_prerequisites(arango_service)
    logger.info(f"Created organization {org_id} and ZAMMAD app in ArangoDB")

    # 5. Read Zammad credentials from environment
    zammad_token = os.getenv("ZAMMAD_TOKEN")
    zammad_base_url = os.getenv("ZAMMAD_BASE_URL")

    if not zammad_token or not zammad_base_url:
        logger.error("ZAMMAD_TOKEN and ZAMMAD_BASE_URL environment variables must be set.")
        return

    # 6. Store Zammad config
    config = {
        "auth": {
            "baseUrl": zammad_base_url,
            "token": zammad_token
        }
    }
    await key_value_store.create_key("/services/connectors/zammad/config", config)

    # 7. Initialize Zammad client for direct API calls
    zammad_config = ZammadTokenConfig(base_url=zammad_base_url, token=zammad_token)
    zammad_client = ZammadClient.build_with_config(zammad_config)
    zammad_datasource = ZammadDataSource(zammad_client)

    try:
        # ====================================================================================
        # STEP 1: Run initial full sync
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 1: Running initial FULL SYNC")
        logger.info("="*80)

        zammad_connector: ZammadConnector = await ZammadConnector.create_connector(
            logger, data_store_provider, config_service
        )
        await zammad_connector.init()

        # Run full sync
        await zammad_connector.run_sync()

        # Count initial records
        initial_counts = await count_records(arango_service)
        logger.info("\n📊 Initial sync completed:")
        logger.info(f"   - Users: {initial_counts.get('users', 0)}")
        logger.info(f"   - Tickets: {initial_counts.get('tickets', 0)}")
        logger.info(f"   - Organizations: {initial_counts.get('organizations', 0)}")

        # ====================================================================================
        # STEP 2: Create new user via Zammad API
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 2: Creating NEW USER via Zammad API")
        logger.info("="*80)

        # Wait a bit to ensure timestamp difference
        time.sleep(2)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        new_user_email = f"test_user_{timestamp}@example.com"

        # Fetch available roles to determine correct role_id
        customer_role_id = 3  # Default
        try:
            roles_response = await zammad_datasource.list_roles(expand="false")
            if roles_response.success and roles_response.data:
                roles = roles_response.data if isinstance(roles_response.data, list) else []
                logger.info("Available roles in Zammad:")
                for role in roles:
                    role_name = role.get("name", "")
                    role_id = role.get("id")
                    logger.info(f"  - {role_name} (ID: {role_id})")
                    if role_name.lower() == "customer":
                        customer_role_id = role_id
                logger.info(f"Using Customer role ID: {customer_role_id}")
        except Exception as e:
            logger.warning(f"Could not fetch roles: {e}, using default role_id=3")

        # First, try to get an existing organization to use
        org_id_to_use = None
        try:
            orgs_response = await zammad_datasource.list_organizations(expand="false")
            if orgs_response.success and orgs_response.data:
                orgs = orgs_response.data if isinstance(orgs_response.data, list) else []
                if orgs:
                    org_id_to_use = orgs[0].get("id")
                    logger.info(f"Using existing organization ID: {org_id_to_use}")
        except Exception as e:
            logger.warning(f"Could not fetch organizations: {e}")

        # Create user with the detected Customer role ID
        create_user_response: ZammadResponse = await zammad_datasource.create_user(
            firstname="Test",
            lastname=f"User_{timestamp}",
            email=new_user_email,
            login=new_user_email,
            organization_id=org_id_to_use,
            role_ids=[customer_role_id]
        )

        if create_user_response.success:
            new_user_id = create_user_response.data.get("id")
            logger.info(f"✅ Created new user: {new_user_email} (ID: {new_user_id})")
        else:
            # Extract error from response
            error_msg = create_user_response.error or create_user_response.message or "Unknown error"
            error_data = create_user_response.data

            # If error is in the data field (422 responses)
            if isinstance(error_data, dict) and "error" in error_data:
                error_msg = error_data.get("error", error_msg)

            logger.error(f"❌ Failed to create user: {error_msg}")
            logger.error(f"   Response data: {error_data}")
            logger.info("   Tip: Check that role_ids=[3] exists in your Zammad instance")
            logger.info("   You may need to use different role IDs based on your Zammad configuration")
            return

        # ====================================================================================
        # STEP 3: Create new ticket via Zammad API
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 3: Creating NEW TICKET via Zammad API")
        logger.info("="*80)

        time.sleep(1)

        ticket_title = f"Incremental Sync Test Ticket - {timestamp}"
        create_ticket_response: ZammadResponse = await zammad_datasource.create_ticket(
            title=ticket_title,
            group="Users",
            customer_id=new_user_id,
            article={
                "subject": ticket_title,
                "body": "This ticket is created to test incremental sync functionality.",
                "type": "note",
                "internal": False
            }
        )

        if create_ticket_response.success:
            new_ticket_id = create_ticket_response.data.get("id")
            logger.info(f"✅ Created new ticket: {ticket_title} (ID: {new_ticket_id})")
        else:
            error_msg = create_ticket_response.error or create_ticket_response.message
            error_data = create_ticket_response.data
            logger.error(f"❌ Failed to create ticket: {error_msg}")
            logger.error(f"   Response data: {error_data}")
            logger.info("   Tip: Check that group 'Users' exists in your Zammad instance")
            return

        # ====================================================================================
        # STEP 4: Run incremental sync
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 4: Running INCREMENTAL SYNC")
        logger.info("="*80)

        logger.info("Waiting 5 seconds for Zammad to index new entities...")
        time.sleep(5)  # Wait to ensure entities are indexed by Zammad

        # Verify entities were created in Zammad
        logger.info("Verifying entities in Zammad before incremental sync...")
        verify_user_response = await zammad_datasource.get_user(new_user_id)
        if verify_user_response.success:
            logger.info(f"✅ User #{new_user_id} exists in Zammad")
            user_updated_at = verify_user_response.data.get("updated_at")
            logger.info(f"   User updated_at: {user_updated_at}")
        else:
            logger.error(f"❌ User #{new_user_id} not found in Zammad")

        verify_ticket_response = await zammad_datasource.get_ticket(new_ticket_id)
        if verify_ticket_response.success:
            logger.info(f"✅ Ticket #{new_ticket_id} exists in Zammad")
            ticket_updated_at = verify_ticket_response.data.get("updated_at")
            logger.info(f"   Ticket updated_at: {ticket_updated_at}")
        else:
            logger.error(f"❌ Ticket #{new_ticket_id} not found in Zammad")

        # Test search_users API directly to debug
        logger.info("Testing search_users API directly...")
        test_timestamp = (datetime.utcnow() - timedelta(minutes=10)).strftime('%Y-%m-%dT%H:%M:%SZ')
        test_query = f"updated_at:>{test_timestamp}"
        logger.info(f"Test query: {test_query}")

        test_search_response = await zammad_datasource.search_users(
            query=test_query,
            limit=10
        )

        if test_search_response.success:
            logger.info("✅ User search API works!")
            logger.info(f"   Response data type: {type(test_search_response.data)}")
            if isinstance(test_search_response.data, dict):
                logger.info(f"   Response keys: {test_search_response.data.keys()}")
                users = test_search_response.data.get('users', [])
                logger.info(f"   Found {len(users)} users in last 10 minutes")
                for user in users[:3]:  # Show first 3
                    logger.info(f"     - User #{user.get('id')}: {user.get('email')} (updated: {user.get('updated_at')})")
            elif isinstance(test_search_response.data, list):
                logger.info(f"   Found {len(test_search_response.data)} users in last 10 minutes")
        else:
            logger.error(f"❌ User search API failed: {test_search_response.error or test_search_response.message}")

        # Create fresh connector instance for incremental sync
        zammad_connector_inc: ZammadConnector = await ZammadConnector.create_connector(
            logger, data_store_provider, config_service
        )
        await zammad_connector_inc.init()

        # Run incremental sync
        await zammad_connector_inc.run_incremental_sync()

        # Count records after incremental sync
        after_counts = await count_records(arango_service)
        logger.info("\n📊 After incremental sync:")
        logger.info(f"   - Users: {after_counts.get('users', 0)} (was {initial_counts.get('users', 0)})")
        logger.info(f"   - Tickets: {after_counts.get('tickets', 0)} (was {initial_counts.get('tickets', 0)})")
        logger.info(f"   - Organizations: {after_counts.get('organizations', 0)} (was {initial_counts.get('organizations', 0)})")

        # ====================================================================================
        # STEP 5: Validate incremental sync results
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 5: VALIDATING INCREMENTAL SYNC RESULTS")
        logger.info("="*80)

        users_added = after_counts.get('users', 0) - initial_counts.get('users', 0)
        tickets_added = after_counts.get('tickets', 0) - initial_counts.get('tickets', 0)

        logger.info("\n📈 Changes detected by incremental sync:")
        logger.info(f"   - New users synced: {users_added}")
        logger.info(f"   - New tickets synced: {tickets_added}")

        # Validate
        success = True
        if users_added >= 1:
            logger.info("✅ SUCCESS: New user was detected and synced!")
        else:
            logger.error("❌ FAILED: New user was NOT detected by incremental sync")
            success = False

        if tickets_added >= 1:
            logger.info("✅ SUCCESS: New ticket was detected and synced!")
        else:
            logger.error("❌ FAILED: New ticket was NOT detected by incremental sync")
            success = False

        # ====================================================================================
        # STEP 6: Verify specific entities in database
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("STEP 6: VERIFYING ENTITIES IN DATABASE")
        logger.info("="*80)

        # Check if the new user exists in database
        db = arango_service.db

        # Query for user by email
        query = f"""
        FOR user IN {CollectionNames.USERS.value}
            FILTER user.email == @email
            RETURN user
        """
        cursor = db.aql.execute(query, bind_vars={"email": new_user_email})
        user_found = list(cursor)

        if user_found:
            logger.info(f"✅ Found new user in database: {user_found[0].get('fullName')} ({new_user_email})")
        else:
            logger.error(f"❌ New user NOT found in database: {new_user_email}")
            success = False

        # Check if the new ticket exists
        #records_collection = db.collection(CollectionNames.RECORDS.value)
        ticket_query = f"""
        FOR record IN {CollectionNames.RECORDS.value}
            FILTER record.externalRecordId == @ticket_id
            RETURN record
        """
        ticket_cursor = db.aql.execute(ticket_query, bind_vars={"ticket_id": str(new_ticket_id)})
        ticket_found = list(ticket_cursor)

        if ticket_found:
            logger.info(f"✅ Found new ticket in database: {ticket_found[0].get('name')}")
        else:
            logger.error(f"❌ New ticket NOT found in database (ID: {new_ticket_id})")
            success = False

        # ====================================================================================
        # FINAL RESULTS
        # ====================================================================================
        logger.info("\n" + "="*80)
        if success:
            logger.info("🎉 INCREMENTAL SYNC TEST PASSED!")
            logger.info("   All new entities were successfully detected and synced.")
        else:
            logger.error("❌ INCREMENTAL SYNC TEST FAILED!")
            logger.error("   Some entities were not detected or synced properly.")
        logger.info("="*80 + "\n")

        # ====================================================================================
        # CLEANUP: Delete test entities from Zammad
        # ====================================================================================
        logger.info("\n" + "="*80)
        logger.info("CLEANUP: Deleting test entities from Zammad")
        logger.info("="*80)

        try:
            # Delete ticket
            delete_ticket_response = await zammad_datasource.delete_ticket(new_ticket_id)
            if delete_ticket_response.success:
                logger.info(f"✅ Deleted test ticket (ID: {new_ticket_id})")
            else:
                logger.warning(f"⚠️ Could not delete test ticket: {delete_ticket_response.error}")

            # Delete user
            delete_user_response = await zammad_datasource.delete_user(new_user_id)
            if delete_user_response.success:
                logger.info(f"✅ Deleted test user (ID: {new_user_id})")
            else:
                logger.warning(f"⚠️ Could not delete test user: {delete_user_response.error}")

        except Exception as e:
            logger.warning(f"⚠️ Error during cleanup: {e}")

        logger.info("="*80 + "\n")

    except Exception as e:
        logger.error(f"An error occurred during incremental sync test: {e}", exc_info=True)


if __name__ == "__main__":
    # Set environment variables:
    # export ZAMMAD_TOKEN='your-token-here'
    # export ZAMMAD_BASE_URL='https://your-domain.zammad.com'

    # Run the test you want:
    # asyncio.run(test_sync_sub_organizations())  # For full sync test
    asyncio.run(test_incremental_sync())  # For incremental sync test

