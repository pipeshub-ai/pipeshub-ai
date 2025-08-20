from dataclasses import dataclass


@dataclass
class QdrantConfig:
    host: str
    port: int
    api_key: str
    prefer_grpc: bool
    https: bool
    timeout: int

    @property
    def qdrant_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "api_key": self.api_key,
            "prefer_grpc": self.prefer_grpc,
            "https": self.https,
            "timeout": self.timeout
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'QdrantConfig':
        return cls(
            host=data.get("host", ""),
            port=data.get("port", 0),
            api_key=data.get("api_key", ""),
            prefer_grpc=data.get("prefer_grpc", True),
            https=data.get("https", False),
            timeout=data.get("timeout", 180)
        )
