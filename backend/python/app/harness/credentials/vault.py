"""The vault: secrets the credential service owns, encrypted at rest under versioned keys.

Crypto is the platform's: each secret version is encrypted with
``EncryptionService`` (AES-256-GCM, ``iv:ciphertext:tag``, the helper the
configuration store uses for connector secrets) under a data key of the
keyring, and both the documents and the keyring are stored through
``ConfigurationService``, whose encrypted store adds the platform's
``SECRET_KEY`` layer on top. A key version only names which data key encrypted
a document, so rotating the key re-encrypts every document under a new one and
then drops the old key: a stolen old key no longer opens anything.

Node writes model accounts into the same documents with compare-and-set
(P2.4b); Python's configuration service has none, so a key rotation here is an
operator action with one writer.

Every read raises on a store failure (``raise_on_error``): an outage must never
read as "no keyring" (provisioning a new one over the real one would strand
every secret) or as "no history" (which a write would overwrite). A revoked
document keeps ``lastVersion`` and ``revoked``: every version reads as revoked,
and numbering continues, so a handle pinned to an old version never resolves a
key added later.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from app.utils.encryption.encryption_service import EncryptionService
from harness_core.credentials.errors import (
    CredentialRevoked,
    CredentialStoreUnavailable,
)
from harness_core.credentials.types import MAX_TTL_S, Secret
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.clock import Clock
    from harness_core.credentials.types import CredentialScope

VAULT_KIND = "vault"
VAULT_ROOT = "/services/agentHarness/credentials"
KEYRING_PATH = "/services/agentHarness/credentialKeys"
ALGORITHM = "aes-256-gcm"
DATA_KEY_BYTES = 32
_SEGMENT = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


class VaultConfig(Protocol):
    """The part of ``ConfigurationService`` the vault uses."""

    async def get_config(
        self,
        key: str,
        default: Any = None,  # noqa: ANN401
        use_cache: bool = False,  # noqa: FBT001, FBT002
        *,
        raise_on_error: bool = False,
    ) -> Any: ...  # noqa: ANN401

    async def set_config(self, key: str, value: Any) -> bool: ...  # noqa: ANN401

    async def create_config_if_absent(self, key: str, value: Any) -> bool: ...  # noqa: ANN401

    async def delete_config(self, key: str) -> bool: ...

    async def list_keys_in_directory(self, directory: str) -> list[str]: ...


def vault_path(tenant: str, name: str | None = None) -> str:
    """The document of ``name`` in ``tenant``'s vault; without a name, the tenant's directory."""
    segments = [tenant, *(name.split("/") if name is not None else [])]
    if not all(_SEGMENT.match(s) and s not in (".", "..") for s in segments):
        raise ValueError("vault secret names are path-safe segments")
    return "/".join([VAULT_ROOT, *segments])


def _versions(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    versions = document.get("versions")
    if not isinstance(versions, list):
        return []
    return [cast("dict[str, Any]", v) for v in cast("list[object]", versions) if isinstance(v, dict)]


def _last_version(document: Mapping[str, Any], versions: list[dict[str, Any]]) -> int:
    return max([int(str(v["version"])) for v in versions] + [int(str(document.get("lastVersion") or 0))])


class VaultSecretStore:
    """``SecretSource`` for kind ``vault``: versioned secrets the service writes itself."""

    def __init__(
        self,
        config: VaultConfig,
        clock: Clock,
        *,
        retention_s: int = MAX_TTL_S,
        random_bytes: Callable[[int], bytes] = os.urandom,
        log: HarnessLog | None = None,
    ) -> None:
        self._config = config
        self._clock = clock
        self._retention = timedelta(seconds=retention_s)
        self._random_bytes = random_bytes
        self._log = log or HarnessLog.named("app.harness.credentials")
        self._cipher_log = logging.getLogger("app.harness.credentials.cipher")

    def _cipher(self, key_hex: str) -> EncryptionService:
        return EncryptionService(ALGORITHM, key_hex, self._cipher_log)

    def _new_key(self) -> str:
        return self._random_bytes(DATA_KEY_BYTES).hex()

    async def _get(self, path: str, tenant: str | None = None) -> object:
        try:
            return await self._config.get_config(path, default=None, use_cache=False, raise_on_error=True)
        except Exception as exc:
            self._log.warning(
                "credential.vault_unreadable", reason="store_unavailable", orgId=tenant, errorType=type(exc).__name__
            )
            raise CredentialStoreUnavailable("the vault could not be read") from None

    async def _keyring(self, *, provision: bool) -> dict[str, Any] | None:
        stored = await self._get(KEYRING_PATH)
        ring = cast("dict[str, Any]", stored) if isinstance(stored, dict) else {}
        if ring.get("active") and isinstance(ring.get("keys"), dict):
            return ring
        if not provision:
            return None
        _ = await self._config.create_config_if_absent(KEYRING_PATH, {"active": "1", "keys": {"1": self._new_key()}})
        stored = await self._get(KEYRING_PATH)
        if not isinstance(stored, dict):
            raise RuntimeError("the vault keyring could not be provisioned")
        ring = cast("dict[str, Any]", stored)
        self._log.info("credential.keyring_provisioned", keyVersion=str(ring.get("active")))
        return ring

    async def _document(self, path: str, tenant: str | None = None) -> dict[str, Any]:
        stored = await self._get(path, tenant)
        return dict(cast("dict[str, Any]", stored)) if isinstance(stored, dict) else {}

    def _live(self, versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        horizon = self._clock.now() - self._retention
        return [
            v for v in versions
            if not v.get("supersededAt") or datetime.fromisoformat(str(v["supersededAt"])) > horizon
        ]

    def _decrypt(self, entry: dict[str, Any], keys: dict[str, str], tenant: str, scope: CredentialScope) -> Secret | None:
        key_version = str(entry.get("keyVersion"))
        key = keys.get(key_version)
        if key is None:
            self._log.warning(
                "credential.vault_unreadable", reason="key_version_missing", orgId=tenant, kind=scope.kind, keyVersion=key_version
            )
            return None
        try:
            plaintext = self._cipher(key).decrypt(str(entry.get("ciphertext")))
        except Exception as exc:  # a corrupt document reads as "no secret", never as a crash
            self._log.warning(
                "credential.vault_unreadable", reason="decrypt_failed", orgId=tenant, kind=scope.kind, errorType=type(exc).__name__
            )
            return None
        return Secret(str(entry.get("version")), cast("JsonValue", json.loads(plaintext)))

    async def read(self, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        return await self.read_at(vault_path(tenant, scope.name), tenant, scope, version)

    async def read_at(self, path: str, tenant: str, scope: CredentialScope, version: str | None = None) -> Secret | None:
        """As ``read``, for a document in the same envelope at another path (the control plane's bindings)."""
        document = await self._document(path, tenant)
        if document.get("revoked"):
            raise CredentialRevoked("the secret was revoked by its owner")
        versions = self._live(_versions(document))
        if not versions:
            return None
        entry = versions[-1] if version is None else next((v for v in versions if str(v.get("version")) == version), None)
        if entry is None:
            return None
        ring = await self._keyring(provision=False)
        if ring is None:
            self._log.warning("credential.vault_unreadable", reason="keyring_missing", orgId=tenant, kind=scope.kind)
            return None
        return self._decrypt(entry, cast("dict[str, str]", ring["keys"]), tenant, scope)

    async def document(self, tenant: str, name: str) -> dict[str, Any]:
        """The stored document as is: metadata, tombstone and encrypted versions."""
        return await self._document(vault_path(tenant, name), tenant)

    async def document_at(self, path: str, tenant: str) -> dict[str, Any]:
        """As ``document``, at another path; ``{}`` when there is none."""
        return await self._document(path, tenant)

    async def write(
        self,
        tenant: str,
        scope: CredentialScope,
        material: JsonValue,
        *,
        fields: Mapping[str, JsonValue] | None = None,
    ) -> str:
        """A new current version; ``fields`` (metadata) are kept beside the versions.
        Writing a revoked secret starts it again after its last version."""
        path = vault_path(tenant, scope.name)
        ring = await self._keyring(provision=True)
        assert ring is not None
        active = str(ring["active"])
        document = await self._document(path, tenant)
        versions = _versions(document)
        number = _last_version(document, versions) + 1
        if versions:
            versions[-1]["supersededAt"] = self._clock.now().isoformat()
        versions = self._live(versions)
        ciphertext = self._cipher(cast("dict[str, str]", ring["keys"])[active]).encrypt(json.dumps(material, sort_keys=True))
        versions.append({"version": str(number), "keyVersion": active, "ciphertext": ciphertext, "supersededAt": None})
        stored: dict[str, Any] = {k: v for k, v in document.items() if k != "revoked"}
        stored.update(fields or {})
        stored.update({"versions": versions, "lastVersion": number})
        if not await self._config.set_config(path, stored):
            raise RuntimeError("the vault could not store the secret")
        return str(number)

    async def revoke(self, tenant: str, name: str, *, by: str) -> bool:
        """Every version unreadable at once (``credential_revoked``); False when nothing was live."""
        path = vault_path(tenant, name)
        document = await self._document(path, tenant)
        versions = _versions(document)
        if not versions or document.get("revoked"):
            return False
        tombstone: dict[str, object] = {
            "versions": [],
            "lastVersion": _last_version(document, versions),
            "revoked": {"at": self._clock.now().isoformat(), "by": by},
        }
        if not await self._config.set_config(path, tombstone):
            raise RuntimeError("the vault could not revoke the secret")
        return True

    async def names(self, tenant: str, prefix: str) -> list[str]:
        """The names of the tenant's secrets under the directory ``prefix``."""
        tenant_root = vault_path(tenant) + "/"
        return sorted(
            path[len(tenant_root) :] for path in await self._config.list_keys_in_directory(vault_path(tenant, prefix) + "/")
        )

    async def delete(self, tenant: str, scope: CredentialScope) -> None:
        _ = await self._config.delete_config(vault_path(tenant, scope.name))

    async def erase(self, tenant: str, prefix: str | None = None) -> int:
        """Deletes the tenant's secrets under ``prefix`` (``None``: all of them), leaving nothing."""
        doomed = await self._config.list_keys_in_directory(vault_path(tenant, prefix) + "/")
        for path in doomed:
            # delete_config answers False both on a store failure and for a key already gone (a concurrent
            # erase); a fresh read tells them apart, so an erasure neither passes silently nor fails on a race.
            if not await self._config.delete_config(path) and (
                await self._config.get_config(path, use_cache=False, raise_on_error=True) is not None
            ):
                raise RuntimeError("the vault could not erase a secret")
        return len(doomed)

    async def rotate_key(self) -> int:
        """A new active data key; every document re-encrypted under it; older keys dropped.
        Returns the number of documents re-encrypted."""
        ring = await self._keyring(provision=True)
        assert ring is not None
        keys = dict(cast("dict[str, str]", ring["keys"]))
        new_version = str(max(int(v) for v in keys) + 1)
        keys[new_version] = self._new_key()
        if not await self._config.set_config(KEYRING_PATH, {"active": new_version, "keys": keys}):
            raise RuntimeError("the vault keyring could not be updated")
        new_cipher = self._cipher(keys[new_version])
        moved = 0
        for path in await self._config.list_keys_in_directory(VAULT_ROOT + "/"):
            document = await self._document(path)
            versions = _versions(document)
            changed = False
            for entry in versions:
                old = keys.get(str(entry.get("keyVersion")))
                if old is None or str(entry.get("keyVersion")) == new_version:
                    continue
                plaintext = self._cipher(old).decrypt(str(entry.get("ciphertext")))
                entry["ciphertext"], entry["keyVersion"] = new_cipher.encrypt(plaintext), new_version
                changed = True
            if changed:
                if not await self._config.set_config(path, {**document, "versions": versions}):
                    raise RuntimeError("the vault could not re-encrypt a secret")
                moved += 1
        if not await self._config.set_config(KEYRING_PATH, {"active": new_version, "keys": {new_version: keys[new_version]}}):
            raise RuntimeError("the vault keyring could not drop the old keys")
        self._log.info("credential.key_rotated", keyVersion=new_version, secrets=moved)
        return moved


__all__ = ["KEYRING_PATH", "VAULT_KIND", "VAULT_ROOT", "VaultConfig", "VaultSecretStore", "vault_path"]
