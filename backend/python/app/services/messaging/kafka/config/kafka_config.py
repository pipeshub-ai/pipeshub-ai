from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class KafkaProducerConfig:
    """Kafka configuration"""
    bootstrap_servers: List[str]
    client_id: str
    ssl: bool = False
    sasl: Optional[Dict[str, str]] = None


@dataclass
class KafkaConsumerConfig:
    """Kafka configuration"""
    topics: List[str]
    client_id: str
    group_id: str
    auto_offset_reset: str
    enable_auto_commit: bool
    bootstrap_servers: List[str]
    ssl: bool = False
    sasl: Optional[Dict[str, str]] = None
