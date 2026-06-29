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
    def from_dict(cls, data: dict) -> "QdrantConfig":
        return cls(
            host=data.get("host", "localhost"),
            # Accept both `port` (canonical) and legacy spellings
            port=int(data.get("port", 6333)),
            # Accept both `api_key` (canonical) and `apiKey` (Node.js style)
            api_key=data.get("api_key") or data.get("apiKey") or "",
            prefer_grpc=bool(data.get("prefer_grpc", True)),
            https=bool(data.get("https", False)),
            timeout=int(data.get("timeout", 300)),
        )
