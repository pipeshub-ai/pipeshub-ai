# ruff: noqa
"""
Simple S3 API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.client.s3.s3 import S3Client, S3AccessKeyConfig, S3Response
from app.sources.external.s3.s3 import S3DataSource

async def main():
    # S3 credentials
    ACCESS_KEY = os.getenv("S3_ACCESS_KEY_ID")
    SECRET_KEY = os.getenv("S3_SECRET_ACCESS")
    REGION = os.getenv("S3_REGION")
    BUCKET = os.getenv("S3_BUCKET_NAME")
    # Create client
    config = S3AccessKeyConfig(access_key_id=ACCESS_KEY, secret_access_key=SECRET_KEY, region_name=REGION, bucket_name=BUCKET)

    print("config", config)
    client = S3Client.build_with_config(config)
    print("client", client)
    s3_data_source = S3DataSource(client)
    print("s3_data_source", s3_data_source)

    print("Searching for 'project'...")
    response: S3Response = await s3_data_source.list_buckets()
    print("response", response)


if __name__ == "__main__":
    asyncio.run(main())