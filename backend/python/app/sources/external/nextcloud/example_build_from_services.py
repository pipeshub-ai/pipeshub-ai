# ruff: noqa
import asyncio
import logging
import os

from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.nextcloud.nextcloud import NextcloudClient
from app.sources.external.nextcloud.nextcloud import NextcloudDataSource 
from app.config.configuration_service import ConfigurationService
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # 1. Set up Logging
    logger = logging.getLogger("nextcloud_service_test")
    logging.basicConfig(level=logging.INFO)

    # 2. Get Credentials from .env
    base_url = os.getenv("NEXTCLOUD_BASE_URL")
    token = os.getenv("NEXTCLOUD_TOKEN")
    username = os.getenv("NEXTCLOUD_USERNAME")
    password = os.getenv("NEXTCLOUD_PASSWORD")

    if not base_url:
        raise Exception("❌ Missing NEXTCLOUD_BASE_URL in .env")

    # 3. Create Configuration Store (ETCD)
    try:
        store = Etcd3EncryptedKeyValueStore(logger=logger)
        config_service = ConfigurationService(logger=logger, key_value_store=store)
    except Exception as e:
        print(f"❌ Failed to connect to ETCD. Is the container running? Error: {e}")
        return

    # STEP A: SEED CONFIGURATION (Writing to Store)
    config_key = "/services/connectors/nextcloud/config"
    
    # Build the payload
    config_data = {
        "baseUrl": base_url,
        "auth": {},
        "credentials": {"baseUrl": base_url}
    }

    if token:
        print("🔹 Seeding ETCD with Bearer Token")
        config_data["auth"] = {"authType": "BEARER_TOKEN", "bearerToken": token}
    elif username and password:
        print("🔹 Seeding ETCD with Basic Auth")
        config_data["auth"] = {"authType": "BASIC_AUTH", "username": username, "password": password}
    else:
        raise Exception("❌ No credentials in .env to seed with!")

    try:
        print(f"⏳ Writing config to ETCD key: {config_key}...")
        
        # FIXED: Logic to handle both Create and Update based on available methods
        try:
            # Try creating first
            await store.create_key(config_key, config_data)
            print("✅ Config created successfully (create_key).")
        except Exception as create_error:
            # If creation fails (likely key exists), try updating
            print(f"⚠️ create_key failed ({create_error}), trying update_value...")
            await store.update_value(config_key, config_data)
            print("✅ Config updated successfully (update_value).")
            
    except Exception as e:
        print(f"❌ Failed to seed config: {e}")
        return

    # STEP B: BUILD FROM SERVICES (Reading from Service)
    print("\n--- Testing NextcloudClient.build_from_services ---")
    
    try:
        # The client uses the config_service to READ and DECRYPT the data we just wrote
        nextcloud_client = await NextcloudClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"✅ Nextcloud client built successfully: {nextcloud_client}")
    except Exception as e:
        logger.error(f"Failed to build client: {e}")
        print(f"❌ Error: {e}")
        return
    
    # STEP C: VERIFY CONNECTION
    nextcloud_data_source = NextcloudDataSource(nextcloud_client)
    
    try:
        print("\n--- Verifying API Access (Get Capabilities) ---")
        response: HTTPResponse = await nextcloud_data_source.get_capabilities()
        
        if response.status == 200:
            data = response.json()
            version = data.get('ocs', {}).get('data', {}).get('version', {}).get('string', 'Unknown')
            print(f"✅ Connection Confirmed! Connected to Nextcloud {version}")
        else:
            print(f"❌ Connection Failed: {response.status} - {response.text()}")

    except Exception as e:
        print(f"❌ API Error: {e}")
    finally:
        await nextcloud_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())