from dataclasses import dataclass, field
from typing import Optional


@dataclass
class OpenSearchConfig:
    host: str = "localhost"
    port: int = 9200
    username: str = "admin"
    password: str = "admin"
    use_ssl: bool = False
    verify_certs: bool = False
    ssl_show_warn: bool = False
    timeout: int = 300
    # Pluggable auth seam.  Only "basic" is implemented.  Adding AWS SigV4 later
    # is a single new branch in _build_client() — zero call-site changes.
    # Any unrecognised value raises ValueError at connect() time.
    auth_type: str = "basic"

    @property
    def opensearch_config(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "useSsl": self.use_ssl,
            "verifyCerts": self.verify_certs,
            "sslShowWarn": self.ssl_show_warn,
            "timeout": self.timeout,
            "authType": self.auth_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "OpenSearchConfig":
        return cls(
            host=data.get("host", "localhost"),
            port=int(data.get("port", 9200)),
            username=data.get("username", "admin"),
            password=data.get("password", "admin"),
            use_ssl=bool(data.get("useSsl", data.get("use_ssl", False))),
            verify_certs=bool(data.get("verifyCerts", data.get("verify_certs", False))),
            ssl_show_warn=bool(data.get("sslShowWarn", data.get("ssl_show_warn", False))),
            timeout=int(data.get("timeout", 300)),
            auth_type=data.get("authType", data.get("auth_type", "basic")),
        )
