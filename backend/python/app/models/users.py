from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.models.graph import Node


@dataclass
class User(Node):
    email: str
    source_user_id: str
    is_active: bool
    _key: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "first_name": self.first_name,
            "middle_name": self.middle_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "email": self.email,
            "source_user_id": self.source_user_id,
            "title": self.title,
            "is_active": self.is_active
        }

    def validate(self) -> bool:
        return self.email is not None and self.email != ""

    def key(self) -> str:
        return self._key

@dataclass
class UserGroup(Node):
    source_user_group_id: str
    name: str
    mail: Optional[str] = None
    _key: Optional[str] = None
    description: Optional[str] = None
    created_at_timestamp: Optional[float] = None
    updated_at_timestamp: Optional[float] = None
    last_sync_timestamp: Optional[float] = None
    source_created_at_timestamp: Optional[float] = None
    source_last_modified_timestamp: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at_timestamp": self.created_at_timestamp,
            "updated_at_timestamp": self.updated_at_timestamp,
            "last_sync_timestamp": self.last_sync_timestamp,
            "source_created_at_timestamp": self.source_created_at_timestamp,
            "source_last_modified_timestamp": self.source_last_modified_timestamp
        }

    def validate(self) -> bool:
        return True

    def key(self) -> str:
        return self._key
