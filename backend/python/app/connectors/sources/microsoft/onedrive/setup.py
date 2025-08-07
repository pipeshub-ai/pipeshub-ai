from app.connectors.sources.microsoft.onedrive.arango_service import ArangoService


class Setup:
    def __init__(self, logger, arango_service: ArangoService) -> None:
        self.logger = logger
        self.arango_service = arango_service

    async def run(self) -> None:

        pass
