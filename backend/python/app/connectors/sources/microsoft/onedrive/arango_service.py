from arango import ArangoClient

from app.connectors.services.base_arango_service import BaseArangoService


class ArangoService(BaseArangoService):
    def __init__(self, logger, config_service, kafka_service) -> None:
        super().__init__(logger, ArangoClient(), config_service, kafka_service)
        self.logger = logger

    async def get_all_users(self) -> None:
        pass

    async def get_all_user_groups(self) -> None:
        pass

    async def get_all_drives(self) -> None:
        pass
