from enum import Enum


class QdrantFilterMode(Enum):
    """Filter operation modes"""
    MUST = "must"           # AND logic - all conditions must be true
    SHOULD = "should"       # OR logic - at least one condition must be true
    MUST_NOT = "must_not"   # NOT logic - none of the conditions should be true
