from enum import Enum, auto


class StoreType(Enum):
    """Enum defining supported key-value store types."""

    ETCD3 = auto()
    IN_MEMORY = auto()
    ENVIRONMENT = auto()
