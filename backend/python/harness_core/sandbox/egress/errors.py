"""Why the egress proxy refused a request: one reason code per cause, each with its HTTP status."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness_core.gateway.refusal_reports import SiteReport


class EgressRefusalReason(StrEnum):
    UNBOUND_SOURCE = "unbound_source"
    SOURCE_MISMATCH = "source_mismatch"
    TUNNEL_REFUSED = "tunnel_refused"
    TUNNEL_IP_LITERAL = "tunnel_ip_literal"
    SCHEME_NOT_ALLOWED = "scheme_not_allowed"
    MALFORMED_REQUEST = "malformed_request"
    PATH_NOT_ALLOWED = "path_not_allowed"
    FORBIDDEN_PATTERN = "forbidden_pattern"
    TOKEN_REPLAYED = "token_replayed"
    DESTINATION_NOT_ALLOWED = "destination_not_allowed"
    DESTINATION_PRIVATE_ADDRESS = "destination_private_address"
    DESTINATION_UNRESOLVABLE = "destination_unresolvable"
    PLACEHOLDER_MISSING = "placeholder_missing"
    PLACEHOLDER_FOREIGN = "placeholder_foreign"
    PLACEHOLDER_WRONG_DESTINATION = "placeholder_wrong_destination"
    CALLER_AUTHORIZATION = "caller_authorization"
    BODY_TOO_LARGE = "body_too_large"
    BODY_ENCODED = "body_encoded"
    TOKEN_UNAVAILABLE = "token_unavailable"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    CONTROL_FROM_SANDBOX = "control_from_sandbox"
    # GIT-FLOW: a git host is reached only as its one repository's fetch and session-branch push.
    GIT_OPERATION_NOT_ALLOWED = "git_operation_not_allowed"
    GIT_REF_NOT_ALLOWED = "git_ref_not_allowed"
    GIT_DELETE_REFUSED = "git_delete_refused"
    GIT_FORCE_PUSH_REFUSED = "git_force_push_refused"
    GIT_PUSH_UNREADABLE = "git_push_unreadable"
    # C6.12: the run's sandboxes moved more than the deployment's hard per-run cap; its egress stays stopped.
    VOLUME_CAP_EXCEEDED = "volume_cap_exceeded"
    # WP-63: an allowed website's request that writes on a read-only entry, leaves its paths, or names another
    # host than its connection (the CONNECT, the TLS name and Host must be one).
    SITE_READ_ONLY = "site_read_only"
    SITE_PATH_NOT_ALLOWED = "site_path_not_allowed"
    HOST_MISMATCH = "host_mismatch"
    # A website is never a git remote: repositories come through repository_fetch.
    SITE_GIT_REFUSED = "site_git_refused"
    # The upload card: a read-write website held to read-only after the run read private data, until the person
    # allows uploads to it for the run.
    SITE_UPLOAD_HELD = "site_upload_held"
    # SEC-A-01: after a private read, any other request to an allowed website (a GET's URL carries data too), until
    # the person allows the site for the run; a registry's own path is the only read that goes on.
    SITE_REQUEST_HELD = "site_request_held"
    # A relay host (it fetches what a request names, or shows it to anyone) on a read-only entry: every request is
    # an upload there.
    SITE_RELAY = "site_relay"


# WP-103: the refusals of a request that carried, or looked like it carried, a credential: what the engine and then the
# person are told as one typed code, ``secret_blocked``, while the proxy's log and the audit record keep each reason.
SECRET_BLOCKED = "secret_blocked"
SECRET_REFUSALS: frozenset[EgressRefusalReason] = frozenset(
    {
        EgressRefusalReason.FORBIDDEN_PATTERN,
        EgressRefusalReason.TOKEN_REPLAYED,
        EgressRefusalReason.PLACEHOLDER_WRONG_DESTINATION,
        EgressRefusalReason.PLACEHOLDER_FOREIGN,
    }
)

_STATUS: dict[EgressRefusalReason, int] = {
    EgressRefusalReason.HOST_MISMATCH: 421,
    EgressRefusalReason.MALFORMED_REQUEST: 400,
    EgressRefusalReason.GIT_PUSH_UNREADABLE: 400,
    EgressRefusalReason.BODY_TOO_LARGE: 413,
    EgressRefusalReason.BODY_ENCODED: 415,
    EgressRefusalReason.UPSTREAM_UNAVAILABLE: 502,
    EgressRefusalReason.DESTINATION_UNRESOLVABLE: 502,
    EgressRefusalReason.TOKEN_UNAVAILABLE: 503,
}


def status_for(reason: EgressRefusalReason) -> int:
    return _STATUS.get(reason, 403)


class EgressRefused(Exception):
    """A request the proxy will not forward. ``host`` is set only where logging it is safe."""

    def __init__(self, reason: EgressRefusalReason, message: str, *, host: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.host = host
        self.request_id: str | None = None
        # WP-63: what the refused request did at an allowed website, as the run's activity shows it.
        self.site: SiteReport | None = None

    @property
    def status(self) -> int:
        return status_for(self.reason)


class HostUnresolvable(Exception):
    """A ``HostResolver`` found no address for a name."""


class AddressRefused(HostUnresolvable):
    """A ``HostResolver`` that applies an address policy found a name, or an answer, it will not hand out."""


class TokenUnavailable(Exception):
    """A ``TokenSource`` could not issue a token for a route."""


class ControlPlaneUnconfined(Exception):
    """The control plane would listen where a sandbox can reach it (a wildcard, the sandbox subnet pool) or nowhere:
    the proxy does not start."""

    reason_code = "control_unconfined"


__all__ = [
    "AddressRefused",
    "ControlPlaneUnconfined",
    "EgressRefusalReason",
    "EgressRefused",
    "HostUnresolvable",
    "TokenUnavailable",
    "status_for",
]
