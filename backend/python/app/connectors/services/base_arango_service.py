from __future__ import annotations

from logging import Logger
from typing import Any


class BaseArangoService:
    """Backward-compatible shim for legacy connector container wiring.

    This project migrated ArangoDB operations to graph providers/data stores,
    but some imports still reference this legacy service path.
    """

    def __init__(
        self,
        logger: Logger,
        arango_client: Any,
        config_service: Any,
        kafka_service: Any,
        enable_schema_init: bool = True,
    ) -> None:
        self.logger = logger
        self.arango_client = arango_client
        self.config_service = config_service
        self.kafka_service = kafka_service
        self.enable_schema_init = enable_schema_init
        self._connected = False

    async def connect(self) -> bool:
        """Maintain legacy async connect contract used by container startup."""
        self._connected = True
        return True

    async def disconnect(self) -> bool:
        """Maintain legacy async disconnect contract."""
        self._connected = False
        return True
