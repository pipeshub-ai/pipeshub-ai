from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class ArangoConfig:
    url: str
    db: str
    username: str
    password: str

    @property
    def arango_config(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "db": self.db,
            "username": self.username,
            "password": self.password
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ArangoConfig':
        return cls(
            url=data.get("url", ""),
            db=data.get("db", ""),
            username=data.get("username", ""),
            password=data.get("password", "")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "db": self.db,
            "username": self.username,
            "password": self.password
        }
