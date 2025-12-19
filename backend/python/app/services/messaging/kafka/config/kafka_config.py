from dataclasses import dataclass


@dataclass
class KafkaProducerConfig:
    """Kafka configuration"""

    bootstrap_servers: list[str]
    client_id: str


@dataclass
class KafkaConsumerConfig:
    """Kafka configuration"""

    topics: list[str]
    client_id: str
    group_id: str
    auto_offset_reset: str
    enable_auto_commit: bool
    bootstrap_servers: list[str]
