"""
Client factory for MariaDB.
"""

from typing import Any, Dict

from app.agents.tools.factories.base import ClientFactory
from app.sources.client.mariadb.mariadb import MariaDBClient


class MariaDBClientFactory(ClientFactory):
	"""Factory for creating MariaDB clients from toolset configuration."""

	async def create_client(  # type: ignore[override]
		self,
		config_service: object,
		logger,
		toolset_config: Dict[str, Any],
		state=None,
	) -> MariaDBClient:
		"""Create MariaDB client instance from toolset configuration."""
		return await MariaDBClient.build_from_toolset(
			toolset_config=toolset_config,
			logger=logger,
			config_service=config_service,
		)
