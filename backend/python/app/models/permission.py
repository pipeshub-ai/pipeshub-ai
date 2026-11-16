from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class PermissionType(str, Enum):
    READ = "READER"
    WRITE = "WRITER"
    OWNER = "OWNER"

#: Roles the model does not have. Stored grants that carry one are not
#: migrated; they read as READER.
RETIRED_ROLES = frozenset({"ORGANIZER", "COMMENTER", "FILEORGANIZER", "OTHERS"})


def read_role(role: str | None) -> str | None:
    """A stored role as the model reads it: a retired role is READER."""
    return PermissionType.READ.value if role in RETIRED_ROLES else role


#: The access paths of a record's details that are grants on the record itself: the user's (or a linked source account's), a group's or role's, the
#: organisation's.
RECORD_OWN_ACCESS_TYPES = frozenset({"DIRECT", "GROUP", "ORGANIZATION"})


def best_record_access(access: list[dict], access_rule: str | None) -> dict:
    """The access path whose role a record's details show: the highest. A record whose rule needs its own grant (RESTRICTED, PARENT_AND_GRANT) takes
    it from its own grants alone, as the role reads do: a grant on what it inherits from never sets it (READER, read access, when none is listed)."""
    if access_rule in ("RESTRICTED", "PARENT_AND_GRANT"):
        access = [a for a in access if a.get("type") in RECORD_OWN_ACCESS_TYPES] or [{"type": "CONNECTOR", "role": PermissionType.READ.value}]
    rank = {"OWNER": 3, "WRITER": 2, "READER": 1}
    return max(access, key=lambda a: rank.get(read_role(a.get("role", "")), 0))


class EntityType(str, Enum):
    USER = "USER"
    GROUP = "GROUP"
    ROLE = "ROLE"
    ORG = "ORG"
    TEAM = "TEAM"
    # What a source reports for a domain, public or link share. A connector may
    # name them; they write no grant.
    DOMAIN = "DOMAIN"
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
    # Set by SharePoint when the grant comes from a sharing link's identities.
    # Not written onto the permission edge.
    link_grant: bool = False
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
