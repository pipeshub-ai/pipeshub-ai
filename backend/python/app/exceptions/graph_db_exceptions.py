class PermissionVerificationUnavailableError(Exception):
    """The graph could not adjudicate which retrieved records a user may read.

    Raised rather than returning an empty map, because an empty map is also the
    honest answer when every candidate is denied — and the two need opposite
    responses: retry later versus "nothing you can read matched".
    """
