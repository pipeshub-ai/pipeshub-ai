from dataclasses import dataclass


@dataclass
class RedisVectorConfig:
    host: str = "localhost"
    port: int = 6379
    password: str | None = None
    db: int = 0
    timeout: int = 300

    @property
    def redis_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "password": self.password,
            "db": self.db,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RedisVectorConfig":
        password = data.get("password") or None
        if isinstance(password, str) and not password.strip():
            password = None
        return cls(
            host=data.get("host", "localhost"),
            port=int(data.get("port", 6379)),
            password=password,
            db=int(data.get("db", 0)),
            timeout=int(data.get("timeout", 300)),
        )
