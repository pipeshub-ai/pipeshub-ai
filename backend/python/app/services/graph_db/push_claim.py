"""The conditional write that arbitrates concurrent pushes of one record key.

A push reads the record at its key without a lock, decides what to do, and then
claims the change in the first statement of its transaction. The claim only
succeeds while the record still looks the way the decision assumed, so two
pushes of one key (or a push and a trash) cannot both win.
"""

from dataclasses import dataclass
from enum import Enum


class PushClaim(str, Enum):
    #: The claim applies; the caller writes the record in the same transaction.
    CLAIMED = "CLAIMED"
    #: A create found the key taken.
    KEY_TAKEN = "KEY_TAKEN"
    #: An update found the record changed, trashed, or moved since the decision.
    LOST = "LOST"
    KB_MISSING = "KB_MISSING"
    #: The knowledge base is being deleted; nothing may be added to it.
    KB_DELETING = "KB_DELETING"


@dataclass(frozen=True)
class PushExpect:
    """What an update's decision assumed about the stored record."""

    document_id: str
    revision: str
    version: int


@dataclass(frozen=True)
class PushSet:
    """What the claim writes onto the record. The revision is written later, by the record upsert."""

    document_id: str
    version: int


KB_DELETING_STATUS = "DELETING"
