"""Typed errors of the permission-scope layer. Each one means "use the legacy path", never "admit"."""

from __future__ import annotations


class ViewerScopesUnavailable(Exception):
    """The viewer's scopes could not be computed (graph unreachable, a port failed, an answer was inconsistent).

    The caller falls back to the legacy filter and counts it. Nothing may treat this as an empty or a wide
    scope: an empty `ViewerScopes` means "reaches nothing", this means "could not tell"."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
