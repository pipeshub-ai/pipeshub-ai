"""Credential service (P2.4a): purpose-bound, short-lived handles resolved only at the effect boundary.

Product-neutral: tenants, runs, tools and secret names are opaque strings; time
and handle ids are injected. A host binds ``SecretSource`` per kind of secret.
"""

from harness_core.credentials.errors import (
    CredentialExpired,
    CredentialKindUnknown,
    CredentialPurposeMismatch,
    CredentialReadOnly,
    CredentialRefused,
    CredentialRevoked,
    CredentialStoreUnavailable,
    CredentialTenantMismatch,
    CredentialTtlInvalid,
    CredentialUnavailable,
    CredentialUnknown,
)
from harness_core.credentials.leases import CredentialLeases
from harness_core.credentials.memory import InMemorySecretSource
from harness_core.credentials.ports import CredentialService, SecretSource
from harness_core.credentials.service import HandleCredentialService
from harness_core.credentials.types import (
    MAX_TTL_S,
    REFRESH_BEFORE_S,
    CredentialPrincipal,
    CredentialPurpose,
    CredentialScope,
    Secret,
    ShortLivedCredential,
)

__all__ = [
    "MAX_TTL_S",
    "REFRESH_BEFORE_S",
    "CredentialExpired",
    "CredentialKindUnknown",
    "CredentialLeases",
    "CredentialPrincipal",
    "CredentialPurpose",
    "CredentialPurposeMismatch",
    "CredentialReadOnly",
    "CredentialRefused",
    "CredentialRevoked",
    "CredentialStoreUnavailable",
    "CredentialScope",
    "CredentialService",
    "CredentialTenantMismatch",
    "CredentialTtlInvalid",
    "CredentialUnavailable",
    "CredentialUnknown",
    "HandleCredentialService",
    "InMemorySecretSource",
    "Secret",
    "SecretSource",
    "ShortLivedCredential",
]
