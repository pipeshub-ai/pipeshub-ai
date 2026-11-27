"""CPD-8 test support: binding documents as Node's ``KvBindingVault`` writes them, over ``DictConfig``.

The format is the P2.4b envelope Node writes (``control-plane/adapters/kv/kv-binding-vault.ts``): versions
encrypted under the vault keyring's active key, ``binding`` metadata, a tombstone that keeps
``lastVersion``, and a pointer from the binding id. ``test_binding_interop.py`` holds the real Node code to it.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.control_plane.runtime.bindings import (
    binding_pointer_path,
    binding_vault_path,
    binding_version_digest,
)
from app.harness.credentials.binding_writer import (
    BindingConflict,
    BindingNotImported,
    BindingRow,
    BindingWriteFailed,
    DueBinding,
    Imported,
    ImportRequest,
    Material,
    Reverted,
)
from app.harness.credentials.vault import ALGORITHM, KEYRING_PATH
from app.utils.encryption.encryption_service import EncryptionService

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.credentials.support import DictConfig

# Node's REFRESH_CLAIM_MS (binding-service.ts).
REFRESH_CLAIM_MS = 6 * 60_000
DATA_KEY = "ab" * 32


class NodeFormatBindings:
    def __init__(self, config: DictConfig, org: str, clock: ManualClock | None = None) -> None:
        self.config = config
        self.org = org
        self.clock = clock
        self._owners: dict[str, tuple[str, str]] = {}
        config.values.setdefault(KEYRING_PATH, {"active": "1", "keys": {"1": DATA_KEY}})

    def _seal(self, material: JsonValue) -> str:
        ring = self.config.values[KEYRING_PATH]
        key = ring["keys"][ring["active"]]
        return EncryptionService(ALGORITHM, key, logging.getLogger("test.bindings")).encrypt(json.dumps(material))

    def path(self, binding: str) -> str:
        installation, owner = self._owners[binding]
        return binding_vault_path(self.org, installation, owner)

    def store(
        self,
        binding: str,
        material: JsonValue,
        *,
        installation: str = "inst-1",
        owner: str = "user:u3",
        source: dict[str, str] | None = None,
        kind: str | None = None,
    ) -> str:
        """Writes the next version (Node's binder, or a refresh) and answers its digest."""
        self._owners.setdefault(binding, (installation, owner))
        path = self.path(binding)
        document: dict[str, Any] = dict(self.config.values.get(path) or {"versions": []})
        number = max([int(v["version"]) for v in document["versions"]] + [int(document.get("lastVersion") or 0)]) + 1
        superseded = self.clock.now().isoformat() if self.clock is not None else "2026-10-10T12:00:00+00:00"
        versions = [{**v, "supersededAt": v["supersededAt"] or superseded} for v in document["versions"]]
        versions.append({"version": str(number), "keyVersion": "1", "ciphertext": self._seal(material), "supersededAt": None})
        previous = document.get("binding") or {}
        document = {
            "versions": versions,
            "lastVersion": number,
            "binding": {
                "binding": binding,
                "installation": self._owners[binding][0],
                "owner": self._owners[binding][1],
                "mode": "oauth",
                "materialKind": "record",
                "kind": kind or previous.get("kind") or "toolset",
                "provider": "slack",
                "source": source if source is not None else previous.get("source"),
                "importedVersion": previous.get("importedVersion", 1 if source is not None else None),
            },
        }
        self.config.values[path] = document
        self.config.values[binding_pointer_path(self.org, binding)] = {"path": path}
        return binding_version_digest(binding, number)

    def tombstone(self, binding: str) -> None:
        path = self.path(binding)
        document = self.config.values[path]
        self.config.values[path] = {
            "versions": [],
            "lastVersion": document["lastVersion"],
            "revoked": {"at": "2026-10-10T12:00:00.000Z", "by": "user:u3"},
            "binding": document["binding"],
        }


class FakeBindingWriter:
    """``BindingWriter`` with Node's binder semantics (``binding-vault.test.ts`` BM-1..9, ``binding-routes.http.test.ts``),
    writing the documents ``NodeFormatBindings`` writes. ``test_binding_interop.py`` runs the real one."""

    def __init__(self, config: DictConfig, now_ms: Callable[[], int] = lambda: 1_000) -> None:
        self.config = config
        self.now_ms = now_ms
        self.rows: dict[str, dict[str, Any]] = {}
        self._orgs: dict[str, NodeFormatBindings] = {}
        self.calls: list[tuple[str, str]] = []
        self.audited: list[tuple[str, str, str]] = []
        self._ids = 0

    def vault(self, org: str) -> NodeFormatBindings:
        return self._orgs.setdefault(org, NodeFormatBindings(self.config, org))

    def find(self, org: str, installation: str, owner: str) -> dict[str, Any] | None:
        return next(
            (r for r in self.rows.values() if (r["orgId"], r["installation"], r["owner"]) == (org, installation, owner)),
            None,
        )

    def _row(self, row: dict[str, Any]) -> BindingRow:
        return BindingRow.of({**row, "secret": {"path": self.vault(row["orgId"]).path(row["id"]), "version": row["version"]}})

    def _meta(self, row: dict[str, Any]) -> dict[str, Any]:
        return self.config.values[self.vault(row["orgId"]).path(row["id"])]["binding"]

    def _write(self, row: dict[str, Any], material: Material, source: dict[str, str] | None = None, actor: str = "") -> None:
        vault = self.vault(row["orgId"])
        _ = vault.store(
            row["id"], dict(material.value), installation=row["installation"], owner=row["owner"], source=source, kind=row["kind"]
        )
        path = vault.path(row["id"])
        if source is not None:
            self.config.values[path]["binding"]["importedVersion"] = self.config.values[path]["lastVersion"]
        row.update(status="active", version=self.config.values[path]["lastVersion"], expiresAt=material.expires_at)
        row["revision"] += 1
        self.audited.append((row["orgId"], row["id"], actor))

    async def import_record(self, org_id: str, request: ImportRequest, *, user_id: str | None = None) -> Imported:
        self.calls.append(("import", request.installation))
        source = None if request.source_path is None else {"path": request.source_path, "digest": str(request.source_digest)}
        existing = self.find(org_id, request.installation, request.owner)
        if existing is None:
            self._ids += 1
            row = {
                "id": f"bnd_{self._ids:026d}",
                "orgId": org_id,
                "installation": request.installation,
                "owner": request.owner,
                "kind": request.kind,
                "status": "pending",
                "revision": 0,
                "version": 0,
                "expiresAt": None,
                "lastError": None,
            }
            self.rows[row["id"]] = row
            self._write(row, request.material, source, "migration" if source else f"user:{user_id}")
            return Imported(self._row(row), "created")
        if source is not None:
            meta = self._meta(existing)
            if existing["status"] == "revoked" or (meta.get("source") or {}).get("digest") == source["digest"]:
                return Imported(self._row(existing), "unchanged")
            if meta.get("source") is None:
                return Imported(self._row(existing), "conflict")
        self._write(existing, request.material, source, "migration" if source else f"user:{user_id}")
        if source is None and request.observed_path is not None:
            self._meta(existing)["source"] = {"path": request.observed_path, "digest": str(request.observed_digest)}
        return Imported(self._row(existing), "updated")

    async def revert_import(self, org_id: str, installation: str, owner: str) -> Reverted:
        self.calls.append(("revert", installation))
        row = self.find(org_id, installation, owner)
        if row is None:
            return Reverted(None, False)
        meta = self._meta(row)
        if meta.get("source") is None:
            raise BindingNotImported(row["id"])
        path = self.vault(org_id).path(row["id"])
        changed = self.config.values[path]["lastVersion"] != meta.get("importedVersion")
        answer = self._row(row)
        del self.config.values[path]
        del self.config.values[binding_pointer_path(org_id, row["id"])]
        del self.rows[row["id"]]
        return Reverted(answer, changed)

    async def revoke_owner(
        self, org_id: str, installation: str, owner: str, *, user_id: str | None = None
    ) -> BindingRow | None:
        row = self.find(org_id, installation, owner)
        if row is None or row["status"] == "revoked":
            return None if row is None else self._row(row)
        self.vault(org_id).tombstone(row["id"])
        row.update(status="revoked", revision=row["revision"] + 1)
        return self._row(row)

    def _claimable(self, row: dict[str, Any], holder: str) -> bool:
        """Node's refresh claim (QA23-F2): one holder's until it lapses; any write (a new revision) frees it."""
        held = row.get("claim")
        return held is None or held[2] != row["revision"] or held[1] <= self.now_ms() or held[0] == holder

    def _claim(self, row: dict[str, Any], holder: str) -> bool:
        if not self._claimable(row, holder):
            return False
        row["claim"] = (holder, self.now_ms() + REFRESH_CLAIM_MS, row["revision"])
        return True

    def _due(self, row: dict[str, Any], claimed: bool = True) -> DueBinding:
        return DueBinding(row["id"], row["orgId"], row["installation"], row["owner"], "oauth", row["revision"],
                          row["expiresAt"], self.vault(row["orgId"]).path(row["id"]), claimed)

    async def due(self, before_ms: int, limit: int, holder: str) -> list[DueBinding]:
        live = [
            r for r in self.rows.values()
            if r["status"] in ("active", "expiring") and r["expiresAt"] is not None and r["expiresAt"] < before_ms
        ]
        live.sort(key=lambda r: (r["expiresAt"], r["id"]))
        return [self._due(r) for r in [r for r in live if self._claimable(r, holder)][:limit] if self._claim(r, holder)]

    async def find_owner(self, org_id: str, installation: str, owner: str, holder: str) -> DueBinding | None:
        row = self.find(org_id, installation, owner)
        return None if row is None else self._due(row, self._claim(row, holder))

    def _current(self, org_id: str, binding: str, expected_revision: int) -> dict[str, Any]:
        row = self.rows.get(binding)
        if row is None or row["orgId"] != org_id:
            raise BindingWriteFailed(f"no binding {binding}")
        if row["revision"] != expected_revision:
            raise BindingConflict(binding)
        return row

    async def refreshed(self, org_id: str, binding: str, expected_revision: int, material: Material) -> BindingRow:
        row = self._current(org_id, binding, expected_revision)
        row["lastError"] = None
        self._write(row, material, None, "service:token-refresher")
        return self._row(row)

    async def refresh_failed(
        self, org_id: str, binding: str, expected_revision: int, code: str, *, permanent: bool
    ) -> BindingRow:
        row = self._current(org_id, binding, expected_revision)
        row.update(lastError=code, revision=row["revision"] + 1)
        if permanent:
            row["status"] = "error"
        return self._row(row)
