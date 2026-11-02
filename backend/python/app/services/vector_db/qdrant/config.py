from dataclasses import dataclass


@dataclass
class QdrantConfig:
    host: str
    port: int
    api_key: str = ""
    prefer_grpc: bool = True
    https: bool = False
    timeout: int = 300
    grpc_port: int = 6334

    @property
    def qdrant_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "api_key": self.api_key,
            "prefer_grpc": self.prefer_grpc,
            "https": self.https,
            "timeout": self.timeout,
            "grpc_port": self.grpc_port,
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
            # `grpcPort` is the key the config service and the Node API store
            grpc_port=int(data.get("grpc_port") or data.get("grpcPort") or 6334),
        )
