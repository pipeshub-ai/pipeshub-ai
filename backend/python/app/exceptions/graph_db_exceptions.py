class GraphDBError(Exception):
    """Base exception for graph database failures."""


class GraphQueryError(GraphDBError):
    """Raised when a graph read could not be completed.

    Exists so callers can tell "the query ran and matched nothing" from "the
    query never ran". Returning an empty list for both makes a database that is
    down, a malformed query, or an expired transaction indistinguishable from a
    normal, expected empty result, and callers act on it: safety gates report
    "nothing in flight", paging loops stop as if complete, and resets report a
    finished cleanup.
    """


class PermissionVerificationUnavailableError(Exception):
    """The graph could not adjudicate which retrieved records a user may read.

    Raised rather than returning an empty map, because an empty map is also the
    honest answer when every candidate is denied — and the two need opposite
    responses: retry later versus "nothing you can read matched".
    """
