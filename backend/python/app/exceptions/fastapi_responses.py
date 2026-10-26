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
    # A filter the search cannot run as given: malformed, or matching too many records.
    INVALID_FILTER = "invalid_filter"
    # The vector store timed out or could not be reached, after a retry. Not
    # EMPTY_RESPONSE: nothing was searched, so nothing is known to be missing.
    SEARCH_INDEX_UNAVAILABLE = "search_index_unavailable"
    # The search ran past its deadline (SEARCH_DEADLINE_SECONDS) outside the
    # vector store, and was stopped so its caller hears why instead of timing out.
    SEARCH_TIMED_OUT = "search_timed_out"
    # A selection below app level (record groups, folders, records) that
    # cannot be searched as asked: too many nodes, a source not yet prepared
    # for it, or nothing in it the user can read.
    SELECTION_TOO_LARGE = "selection_too_large"
    SELECTION_NOT_READY = "selection_not_ready"
    SELECTION_EMPTY = "selection_empty"
