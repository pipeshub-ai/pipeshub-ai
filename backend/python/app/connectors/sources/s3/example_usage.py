"""Example usage of the S3 connector"""

import asyncio
import logging
from typing import Any, Dict

from app.connectors.core.interfaces.connector.iconnector_service import (
    IConnectorService,
)
from app.connectors.sources.s3.factories.connector_factory import S3ConnectorFactory


async def basic_s3_example():
    """Basic example of using the S3 connector with aioboto3"""

    # Setup logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    connector = None

    try:
        # Create connector using aioboto3
        connector: IConnectorService = S3ConnectorFactory.create_connector(logger)

        # Connect with AWS credentials using aioboto3
        credentials = {
            "aws_access_key_id": "x",
            "aws_secret_access_key": "y",
            "region_name": "z"  # or your preferred region
        }

        logger.info("🔌 Connecting to AWS S3 using aioboto3...")
        if await connector.connect(credentials):
            logger.info("✅ Successfully connected to AWS S3 using aioboto3")

            # Test connection using aioboto3
            if await connector.test_connection():
                logger.info("✅ Connection test passed using aioboto3")

                # Get service info
                info = connector.get_service_info()
                logger.info(f"📊 Service info: {info}")

                # List buckets using aioboto3
                buckets = await connector.list_buckets()
                logger.info(f"📦 Found {len(buckets)} S3 buckets using aioboto3")

                # Get first bucket details
                bucket_name = None
                if buckets:
                    first_bucket = buckets[0]
                    bucket_name = first_bucket.get("name")

                    # Get bucket metadata using aioboto3
                    metadata = await connector.get_bucket_metadata(bucket_name)
                    if metadata:
                        logger.info(f"📋 Bucket region: {metadata.get('region')}")
                        logger.info(f"📋 Bucket versioning: {metadata.get('versioning')}")

                    # List objects in the bucket using aioboto3
                    objects = await connector.list_bucket_objects(bucket_name, max_keys=10)
                    logger.info(f"📄 Found {len(objects)} objects in bucket {bucket_name} using aioboto3")

                    # Get first object content if available using aioboto3
                    if objects:
                        first_object = objects[0]
                        object_key = first_object.get("name")
                        content = await connector.get_object_content(bucket_name, object_key)
                        if content:
                            logger.info(f"📝 Object content length: {len(content)} bytes using aioboto3")

                # Search objects using aioboto3 (only if we have a bucket)
                if bucket_name:
                    search_results = await connector.search_objects("test", bucket_name)
                    logger.info(f"🔍 Found {len(search_results)} objects matching 'test' using aioboto3")
                else:
                    logger.info("⚠️ No buckets available for search")

            else:
                logger.error("❌ Connection test failed")
        else:
            logger.error("❌ Failed to connect to AWS S3")

    except Exception as e:
        logger.error(f"❌ Error in S3 example: {str(e)}")

    finally:
        # Disconnect
        if connector:
            await connector.disconnect()
            logger.info("🔌 Disconnected from AWS S3 using aioboto3")


async def advanced_s3_example():
    """Advanced example with error handling and batch operations using aioboto3"""

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    connector = None

    try:
        # Create connector using aioboto3
        connector: IConnectorService = S3ConnectorFactory.create_connector(logger)


        # Connect using aioboto3
        credentials = {
            "aws_access_key_id": "x",
            "aws_secret_access_key": "y",
            "region_name": "z"
        }

        if not await connector.connect(credentials):
            raise Exception("Failed to connect to AWS S3 using aioboto3")

        # List all buckets using aioboto3
        buckets = await connector.list_buckets()
        logger.info(f"📚 Total buckets found: {len(buckets)} using aioboto3")

        # Process buckets and their objects
        for bucket in buckets:
            bucket_name = bucket.get("name")
            logger.info(f"🔄 Processing bucket: {bucket_name} using aioboto3")

            try:
                # Get bucket metadata using aioboto3
                metadata = await connector.get_bucket_metadata(bucket_name)
                logger.info(f"📋 Bucket {bucket_name} metadata: {metadata}")

                # List objects in bucket using aioboto3 (limit to first 100 for demo)
                objects = await connector.list_bucket_objects(bucket_name, max_keys=100)
                logger.info(f"📄 Found {len(objects)} objects in bucket {bucket_name} using aioboto3")

                # Process objects in batches
                batch_size = 10
                for i in range(0, len(objects), batch_size):
                    batch = objects[i:i + batch_size]
                    logger.info(f"🔄 Processing batch {i//batch_size + 1} of bucket {bucket_name} using aioboto3")

                    for obj in batch:
                        try:
                            object_key = obj.get("name")
                            size = obj.get("size", 0)

                            # Only get content for small files (less than 1MB) using aioboto3
                            if size < 1024 * 1024:
                                content = await connector.get_object_content(bucket_name, object_key)
                                if content:
                                    logger.info(f"✅ Processed object: {object_key} ({len(content)} bytes) using aioboto3")
                            else:
                                logger.info(f"⏭️ Skipped large object: {object_key} ({size} bytes)")

                        except Exception as e:
                            logger.error(f"❌ Failed to process object {obj.get('name')}: {str(e)}")

            except Exception as e:
                logger.error(f"❌ Failed to process bucket {bucket_name}: {str(e)}")

        logger.info("🎉 All buckets processed successfully using aioboto3")

    except Exception as e:
        logger.error(f"❌ Advanced example failed: {str(e)}")

    finally:
        if connector:
            await connector.disconnect()
            logger.info("🔌 Disconnected from AWS S3 using aioboto3")


# Integration with main application
class S3ApplicationManager:
    """Manages S3 connector in the main application"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.connector = None

    async def connect_to_s3(self, credentials: Dict[str, Any]) -> bool:
        """Connect to S3 using aioboto3"""
        try:
            self.connector = S3ConnectorFactory.create_connector(self.logger)

            if await self.connector.connect(credentials):
                self.logger.info("✅ Connected to AWS S3 using aioboto3")
                return True
            else:
                self.logger.error("❌ Failed to connect to AWS S3 using aioboto3")
                return False

        except Exception as e:
            self.logger.error(f"❌ Error connecting to AWS S3 using aioboto3: {str(e)}")
            return False

    async def get_s3_buckets(self) -> list:
        """Get all S3 buckets using aioboto3"""
        try:
            if not self.connector:
                raise Exception("S3 connector not connected")

            return await self.connector.list_buckets()

        except Exception as e:
            self.logger.error(f"❌ Error getting S3 buckets using aioboto3: {str(e)}")
            return []

    async def get_bucket_objects(self, bucket_name: str, max_keys: int = 1000) -> list:
        """Get objects from a specific bucket using aioboto3"""
        try:
            if not self.connector:
                raise Exception("S3 connector not connected")

            return await self.connector.list_bucket_objects(bucket_name, max_keys=max_keys)

        except Exception as e:
            self.logger.error(f"❌ Error getting bucket objects using aioboto3: {str(e)}")
            return []

    async def disconnect(self):
        """Disconnect from S3 using aioboto3"""
        if self.connector:
            await self.connector.disconnect()
            self.logger.info("🔌 Disconnected from AWS S3 using aioboto3")


# Usage in main application
async def main():
    """Main application example"""

    logging.basicConfig(level=logging.INFO)
    manager = S3ApplicationManager()

    try:
        # Connect to S3
        credentials = {
            "aws_access_key_id": "x",
            "aws_secret_access_key": "y",
            "region_name": "z"
        }

        if await manager.connect_to_s3(credentials):
            # Get buckets
            buckets = await manager.get_s3_buckets()
            print(f"Found {len(buckets)} S3 buckets")

            # Get objects from first bucket
            if buckets:
                first_bucket = buckets[0]
                bucket_name = first_bucket.get("name")
                objects = await manager.get_bucket_objects(bucket_name, max_keys=10)
                print(f"Found {len(objects)} objects in bucket {bucket_name}")

    finally:
        # Cleanup
        await manager.disconnect()


if __name__ == "__main__":
    # Run basic example
    # asyncio.run(basic_s3_example())

    # Run advanced example
    asyncio.run(advanced_s3_example())

    # Run main application example
    # asyncio.run(main())
