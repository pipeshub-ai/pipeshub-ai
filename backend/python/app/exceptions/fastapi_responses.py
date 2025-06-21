from enum import Enum


class Status(Enum):
    SUCCESS = "success"
    ERROR = "error"
    ACCESSIBLE_RECORDS_NOT_FOUND = "accessible_records_not_found"
    VECTOR_DB_EMPTY = "vector_db_empty"
    VECTOR_DB_NOT_READY = "vector_db_not_ready"
    EMPTY_RESPONSE = "empty_response"
