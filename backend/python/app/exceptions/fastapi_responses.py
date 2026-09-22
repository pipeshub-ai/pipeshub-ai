from enum import Enum


class Status(Enum):
    SUCCESS = "success"
    ERROR = "error"
    ACCESSIBLE_RECORDS_NOT_FOUND = "accessible_records_not_found"
    VECTOR_DB_EMPTY = "vector_db_empty"
    VECTOR_DB_NOT_READY = "vector_db_not_ready"
    EMPTY_RESPONSE = "empty_response"
    # The graph could not say what this user may read. Distinct from
    # ACCESSIBLE_RECORDS_NOT_FOUND, which tells a user to go add content — the
    # wrong advice when their corpus is fine and verification is simply down.
    PERMISSION_CHECK_UNAVAILABLE = "permission_check_unavailable"
