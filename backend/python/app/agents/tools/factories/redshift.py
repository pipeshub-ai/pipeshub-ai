from typing import Any

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.redshift import RedshiftClient


class RedshiftClientFactory(ClientFactory):

    async def create_client(self, config_service: object, logger, toolset_config: dict[str, Any], state=None,) -> RedshiftClient: return await RedshiftClient.build_from_toolset(
                toolset_config=toolset_config,
                logger=logger,
                config_service=config_service,
    )
