"""Topic administration for ``MESSAGE_BROKER=postgres``. A topic is a name on a row, so there is nothing to create: a message published to a
topic nobody reads yet is kept, and a consumer group created later is given it (``PgQueue.ensure_group``)."""

from logging import Logger
from typing import override

from app.services.messaging.interface.admin import IMessageAdmin
from app.services.messaging.postgres.queue import PgQueue


class PgMessageAdmin(IMessageAdmin):
    def __init__(self, logger: Logger, queue: PgQueue | None = None) -> None:
        self.logger = logger
        self.queue = queue or PgQueue()

    @override
    async def ensure_topics_exist(self, topics: list[str] | None = None) -> None:
        # One constant query, so a database that cannot be reached fails here (and the stages wait) rather than at the first publish,
        # without a DISTINCT pass over the whole message table.
        await self.queue.ping()

    @override
    async def list_topics(self) -> list[str]:
        return await self.queue.topics()
