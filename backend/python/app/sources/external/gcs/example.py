import asyncio

from app.config.configuration_service import ConfigurationService
from app.sources.client.gcs.gcs import GCSClient
from app.sources.external.gcs.gcs import GCSDataSource


async def main() -> None:
    config_service = ConfigurationService()
    client = await GCSClient.build_from_services(logger=config_service.logger, config_service=config_service)  # type: ignore

    ds = GCSDataSource(client)
    ensure = await ds.ensure_bucket_exists()
    print(ensure.to_json())

    # Upload and download example
    data = b"hello world"
    up = await ds.upload_object(bucket_name=None, object_name="sample.txt", data=data, content_type="text/plain")
    print(up.to_json())
    down = await ds.download_object(bucket_name=None, object_name="sample.txt")
    print(len(down.data.get("data", b"")) if down.success and down.data else 0)


if __name__ == "__main__":
    asyncio.run(main())


