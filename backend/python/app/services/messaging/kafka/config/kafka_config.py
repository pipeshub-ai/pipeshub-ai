from dataclasses import dataclass
from typing import List, Optional

@dataclass
class KafkaConfig:
    """Kafka configuration"""
    client_id: str
    group_id: str
    auto_offset_reset: str
    enable_auto_commit: bool
    bootstrap_servers: List[str]