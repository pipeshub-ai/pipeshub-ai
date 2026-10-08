from dataclasses import dataclass


def _coerce_bool(value: object, default: bool) -> bool:
    """Coerce string booleans correctly, preserving real booleans.

    Handles "true"/"false" case-insensitively to avoid ``bool("false")==True``.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return bool(value)


@dataclass
class QdrantConfig:
    host: str
    port: int
    api_key: str = ""
    prefer_grpc: bool = True
    https: bool = False
    timeout: int = 300

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
        # Resolve prefer_grpc with camelCase alias; avoid falsy-or fallback for booleans
        if "prefer_grpc" in data:
            raw_prefer_grpc = data.get("prefer_grpc")
        elif "preferGrpc" in data:
            raw_prefer_grpc = data.get("preferGrpc")
        else:
            raw_prefer_grpc = None

        if "https" in data:
            raw_https = data.get("https")
        else:
            raw_https = None

        return cls(
            host=data.get("host", "localhost"),
            # Accept both `port` (canonical) and legacy spellings
            port=int(data.get("port", 6333)),
            # Accept both `api_key` (canonical) and `apiKey` (Node.js style)
            api_key=data.get("api_key") or data.get("apiKey") or "",
            prefer_grpc=_coerce_bool(raw_prefer_grpc, True),
            https=_coerce_bool(raw_https, False),
            timeout=int(data.get("timeout", 300)),
        )
