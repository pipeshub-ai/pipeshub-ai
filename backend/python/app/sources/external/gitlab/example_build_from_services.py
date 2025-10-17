# ruff: noqa
import asyncio

from app.sources.client.gitlab.gitlab import GitLabClient
from app.sources.external.gitlab.gitlab_ import GitLabDataSource
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

    # Build GitLab client using configuration service (await the async method)
    try:
        gitlab_client = await GitLabClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"GitLab client created successfully: {gitlab_client}")
    except Exception as e:
        logger.error(f"Failed to create GitLab client: {e}")
        print(f"❌ Error creating GitLab client: {e}")
        return
    
    # Create data source and use it
    gitlab_data_source = GitLabDataSource(gitlab_client)
    
    # Test list projects
    try:
        response = await gitlab_data_source.list_projects()
        print(f"✅ GitLab list projects response: {response}")
    except Exception as e:
        print(f"❌ Error getting GitLab list projects: {e}")


if __name__ == "__main__":
    asyncio.run(main())