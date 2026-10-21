from dataclasses import dataclass


@dataclass
class QdrantConfig:
    host: str
    port: int
    api_key: str = ""
    prefer_grpc: bool = True
    https: bool = False
    timeout: int = 300
    # None: the client's default (6334). The stored config carries it as `grpcPort`.
    grpc_port: int | None = None

    @property
    def qdrant_config(self) -> dict:
        config = {
            "host": self.host,
            "port": self.port,
            "api_key": self.api_key,
            "prefer_grpc": self.prefer_grpc,
            "https": self.https,
            "timeout": self.timeout
        }
        if self.grpc_port is not None:
            config["grpc_port"] = self.grpc_port
        return config

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
            # `grpc_port` is the canonical spelling, `grpcPort` the Node.js one the config manager stores
            grpc_port=int(grpc_port) if (grpc_port := data.get("grpc_port") or data.get("grpcPort")) else None,
        )
