from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class PermissionType(str, Enum):
    READ = "READER"
    WRITE = "WRITER"
    OWNER = "OWNER"
    COMMENT = "COMMENTER"
    OTHER = "OTHERS"

class EntityType(str, Enum):
    USER = "USER"
    GROUP = "GROUP"
    ROLE = "ROLE"
    DOMAIN = "DOMAIN"
    ORG = "ORG"
    TEAM = "TEAM"
    ANYONE = "ANYONE"
    ANYONE_WITH_LINK = "ANYONE_WITH_LINK"

# The types an organization-to-record (or record group) PERMISSION edge carries when
# it is a real org-wide share: "ORG" from connector permissions, "ORGANIZATION" from
# a service account's chat upload (api/routes/chatbot.py). Access queries accept only
# these, so an org edge of any other type (a domain share) never grants access.
ORG_SHARE_PERMISSION_TYPES: tuple[str, ...] = (EntityType.ORG.value, "ORGANIZATION")


class Permission(BaseModel):
    external_id: Optional[str] = None
    email: Optional[str] = None
    type: PermissionType
    entity_type: EntityType
    created_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch timestamp in milliseconds of the permission creation")
    updated_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch timestamp in milliseconds of the permission update")

    def to_arango_permission(
        self,
        from_id: str,
        from_collection: str,
        to_id: str,
        to_collection: str
    ) -> Dict:
        """
        Create a permission edge in generic format.

        Args:
            from_id: Source node ID
            from_collection: Source node collection name
            to_id: Target node ID
            to_collection: Target node collection name

        Returns:
            Dict with generic edge format (from_id, from_collection, to_id, to_collection)
        """
        return {
            "from_id": from_id,
            "from_collection": from_collection,
            "to_id": to_id,
            "to_collection": to_collection,
            "role": self.type.value,
            "type": self.entity_type.value,
            "createdAtTimestamp": self.created_at,
            "updatedAtTimestamp": self.updated_at,
        }

class AccessControl(BaseModel):
    owners: List[str] = []
    editors: List[str] = []
    viewers: List[str] = []
    domains: List[str] = []
    anyone_with_link: bool = False
