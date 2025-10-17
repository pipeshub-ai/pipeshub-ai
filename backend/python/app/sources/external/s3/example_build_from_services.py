# ruff: noqa
import asyncio

from app.sources.client.s3.s3 import S3Client
from app.sources.external.s3.s3 import S3DataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build S3 client using configuration service (await the async method)
    try:
        s3_client = await S3Client.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"S3 client created successfully: {s3_client}")
    except Exception as e:
        logger.error(f"Failed to create S3 client: {e}")
        print(f"❌ Error creating S3 client: {e}")
        return
    
    # Create data source and use it
    s3_data_source = S3DataSource(s3_client)
    
    # Test list buckets
    try:
        response = await s3_data_source.list_buckets()
        print(f"✅ S3 list buckets response: {response}")
    except Exception as e:
        print(f"❌ Error getting S3 list buckets: {e}")


if __name__ == "__main__":
    asyncio.run(main())