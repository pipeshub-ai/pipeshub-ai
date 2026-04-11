from dataclasses import dataclass


@dataclass
class OpenSearchConfig:
    host: str
    port: int
    username: str
    password: str
    use_ssl: bool
    verify_certs: bool
    ssl_show_warn: bool
    timeout: int

    @property
    def opensearch_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "use_ssl": self.use_ssl,
            "verify_certs": self.verify_certs,
            "ssl_show_warn": self.ssl_show_warn,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'OpenSearchConfig':
        return cls(
            host=data.get("host", "localhost"),
            port=data.get("port", 9200),
            username=data.get("username", "admin"),
            password=data.get("password", "admin"),
            use_ssl=data.get("useSsl", False),
            verify_certs=data.get("verifyCerts", False),
            ssl_show_warn=data.get("sslShowWarn", False),
            timeout=data.get("timeout", 300),
        )
