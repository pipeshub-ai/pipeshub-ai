"""Typed refusals of the credential service, each with a unique ``reason_code`` (C-6).

A refusal never carries the secret, and a missing credential is a refusal the
caller turns into an unavailable tool, never a fallback to another credential
(principle 6).
"""

from __future__ import annotations

from harness_core.refusals import HarnessRefusal


class CredentialRefused(HarnessRefusal):
    reason_code: str = "credential_refused"


class CredentialUnknown(CredentialRefused):
    """No such handle in this service (never issued here, or forgotten after expiry)."""

    reason_code = "credential_unknown"


class CredentialExpired(CredentialRefused):
    reason_code = "credential_expired"


class CredentialRevoked(CredentialRefused):
    reason_code = "credential_revoked"


class CredentialPurposeMismatch(CredentialRefused):
    """The handle was issued for another tool, run, attempt or tenant."""

    reason_code = "credential_purpose_mismatch"


class CredentialUnavailable(CredentialRefused):
    """The secret does not exist, is not authenticated, or cannot be read."""

    reason_code = "credential_unavailable"


class CredentialMissing(CredentialUnavailable):
    """The run's owner never connected the item (or its OAuth flow is unfinished): the item is unavailable, the run
    goes on (QA23-F1)."""

    reason_code = "credential_missing"


class CredentialStoreUnavailable(CredentialUnavailable):
    """The store holding the secret could not be read: the secret may exist, and is refused anyway."""

    reason_code = "credential_store_unavailable"


class CredentialTtlInvalid(CredentialRefused):
    reason_code = "credential_ttl_invalid"


class CredentialTenantMismatch(CredentialRefused):
    """Principal or scope belong to another tenant than the purpose."""

    reason_code = "credential_tenant_mismatch"


class CredentialKindUnknown(CredentialRefused):
    """No source serves the scope's kind."""

    reason_code = "credential_kind_unknown"


class CredentialOwnerMismatch(CredentialRefused):
    """The secret belongs to someone other than the principal it would be used for (SS2):
    a run never resolves another person's connection."""

    reason_code = "credential_owner_mismatch"


class CredentialReadOnly(CredentialRefused):
    """The source of this kind is written by its owner (OAuth callback, settings), not by the service."""

    reason_code = "credential_read_only"


__all__ = [
    "CredentialExpired",
    "CredentialKindUnknown",
    "CredentialMissing",
    "CredentialOwnerMismatch",
    "CredentialPurposeMismatch",
    "CredentialReadOnly",
    "CredentialRefused",
    "CredentialRevoked",
    "CredentialStoreUnavailable",
    "CredentialTenantMismatch",
    "CredentialTtlInvalid",
    "CredentialUnavailable",
    "CredentialUnknown",
]
