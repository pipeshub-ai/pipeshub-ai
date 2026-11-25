"""Values of the credential service: purposes, scopes, handles and secrets.

A handle names an issued credential; it is a random id, so it says nothing
about the secret. A ``Secret`` is what the effect boundary gets back; its
material never appears in a ``repr``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from pydantic import JsonValue

# ARCHITECTURE §4.8 / REPORT §6.5.4: issued credentials live at most ten minutes.
MAX_TTL_S = 600
# Workers re-issue this long before expiry, so a call never starts on a handle about to lapse.
REFRESH_BEFORE_S = 60
HANDLE_PREFIX = "crh"


@dataclass(frozen=True, slots=True)
class CredentialPurpose:
    """What one handle may be used for: one tool (or toolset) in one run (or attempt)."""

    tenant: str
    tool: str
    run_id: str
    attempt_id: str | None = None


@dataclass(frozen=True, slots=True)
class CredentialPrincipal:
    """On whose behalf the secret is used (the owner of a user's connection, or the org)."""

    tenant: str
    subject: str


@dataclass(frozen=True, slots=True)
class CredentialScope:
    """Which secret: ``kind`` selects the source that holds it, ``name`` the secret there."""

    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class ShortLivedCredential:
    handle: str
    purpose: CredentialPurpose
    scope: CredentialScope
    version: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Secret:
    version: str
    material: JsonValue = field(repr=False)


__all__ = [
    "HANDLE_PREFIX",
    "MAX_TTL_S",
    "REFRESH_BEFORE_S",
    "CredentialPrincipal",
    "CredentialPurpose",
    "CredentialScope",
    "Secret",
    "ShortLivedCredential",
]
