"""Kafka topic administration for the topics the Python services own (pipeline stage topics).

Node's admin service creates the topics in its own enum; stage topics are declared by the
Python stage registry, so the indexing service creates them here at startup. Brokers may
run with auto-create off.
"""

import asyncio
from logging import Logger
from typing import override

from aiokafka.admin import AIOKafkaAdminClient, NewTopic

from app.services.messaging.config import REQUIRED_TOPICS, messaging_env
from app.services.messaging.interface.admin import IMessageAdmin
from app.services.messaging.kafka.config.kafka_config import (
    KafkaProducerConfig,
    kafka_security_kwargs,
)

# New topics can take a moment to appear in cluster metadata after CreateTopics returns.
_VISIBILITY_ATTEMPTS = 10
_VISIBILITY_DELAY_S = 0.5


class KafkaAdmin(IMessageAdmin):
    def __init__(
        self,
        logger: Logger,
        config: KafkaProducerConfig,
        *,
        partitions: int | None = None,
        replication_factor: int = 1,
    ) -> None:
        super().__init__()
        self.logger = logger
        self.config = config
        self._partitions = partitions or messaging_env.kafka_topic_partitions
        self._replication_factor = replication_factor

    def _client(self) -> AIOKafkaAdminClient:
        return AIOKafkaAdminClient(
            bootstrap_servers=",".join(self.config.bootstrap_servers),
            client_id=f"{self.config.client_id}-admin",
            **kafka_security_kwargs(ssl_enabled=self.config.ssl, sasl=self.config.sasl),
        )

    @override
    async def ensure_topics_exist(self, topics: list[str] | None = None) -> None:
        wanted = list(dict.fromkeys(topics or REQUIRED_TOPICS))
        admin = self._client()
        try:
            # Inside the try: a start that fails after connecting leaves a metadata task to close.
            await admin.start()
            existing = set(await admin.list_topics())
            missing = [topic for topic in wanted if topic not in existing]
            if not missing:
                return
            # Another instance may be creating the same topics; whatever the response
            # says, the re-list below is what decides success.
            await admin.create_topics(
                [
                    NewTopic(name=topic, num_partitions=self._partitions, replication_factor=self._replication_factor)
                    for topic in missing
                ]
            )
            for _ in range(_VISIBILITY_ATTEMPTS):
                existing = set(await admin.list_topics())
                if all(topic in existing for topic in missing):
                    self.logger.info(
                        "Created Kafka topics %s with %d partition(s)", missing, self._partitions
                    )
                    return
                await asyncio.sleep(_VISIBILITY_DELAY_S)
            still_missing = [topic for topic in missing if topic not in existing]
            raise RuntimeError(f"Kafka topics were not created: {still_missing}")
        finally:
            await admin.close()

    @override
    async def list_topics(self) -> list[str]:
        admin = self._client()
        try:
            await admin.start()
            return sorted(await admin.list_topics())
        finally:
            await admin.close()
