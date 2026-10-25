"""Errors raised by network-share data sources and translated by connectors."""


class DirectoryListingError(Exception):
    def __init__(self, share: str, path: str, message: str) -> None:
        super().__init__(message)
        self.share = share
        self.path = path
        self.message = message


class AccessDeniedError(DirectoryListingError):
    """The account may not list this folder. It stays that way until someone changes its
    permissions, unlike a dropped connection or a busy server."""


class ShareListingError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NetworkShareAuthError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DialectError(Exception):
    """The server negotiated a dialect this connector refuses."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
