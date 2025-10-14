# ruff: noqa
import asyncio

from app.sources.client.github.github import GitHubClient
from app.sources.external.github.github_ import GitHubDataSource
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

    # Build GitHub client using configuration service (await the async method)
    try:
        github_client = await GitHubClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"GitHub client created successfully: {github_client}")
    except Exception as e:
        logger.error(f"Failed to create GitHub client: {e}")
        print(f"❌ Error creating GitHub client: {e}")
        return
    
    # Create data source and use it
    github_data_source = GitHubDataSource(github_client)
    
    # Test get authenticated
    try:
        response = await github_data_source.get_authenticated()
        print(f"✅ GitHub get authenticated response: {response}")
    except Exception as e:
        print(f"❌ Error getting GitHub get authenticated: {e}")


if __name__ == "__main__":
    asyncio.run(main())