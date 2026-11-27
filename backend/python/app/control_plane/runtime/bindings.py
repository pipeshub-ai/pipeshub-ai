"""Where a binding's secret lives and how its versions are named (02-lld §4.3; CPD-8).

Node's twin: ``control-plane/domain/bindings.ts``; the shared vector is
``contracts/control-plane/vectors/binding-version-digests.json``.
"""

from __future__ import annotations

from harness_core.contracts.canonical_json import canonical_digest

CREDENTIALS_ROOT = "/services/controlPlane/credentials"
BINDING_POINTERS_ROOT = "/services/controlPlane/bindings"
BINDING_KIND = "binding"


def binding_version_digest(binding: str, version: int) -> str:
    """The digest of one version of one binding: a pin names it, and every later version of the binding is checkable."""
    return canonical_digest({"binding": binding, "version": version})


def binding_vault_path(org_id: str, installation: str, owner: str) -> str:
    """``owner`` is ``org``, ``agent:<key>`` or ``user:<id>``; the org owner is ``org/org``."""
    owner_path = "org/org" if owner == "org" else owner.replace(":", "/", 1)
    return f"{CREDENTIALS_ROOT}/{org_id}/{installation}/{owner_path}"


def binding_pointer_path(org_id: str, binding: str) -> str:
    return f"{BINDING_POINTERS_ROOT}/{org_id}/{binding}"


def is_version_of(binding: str, version_digest: str, last_version: int) -> bool:
    """Whether ``version_digest`` names a version ``1..last_version`` of ``binding``."""
    return any(binding_version_digest(binding, v) == version_digest for v in range(last_version, 0, -1))


__all__ = [
    "BINDING_KIND",
    "BINDING_POINTERS_ROOT",
    "CREDENTIALS_ROOT",
    "binding_pointer_path",
    "binding_vault_path",
    "binding_version_digest",
    "is_version_of",
]
