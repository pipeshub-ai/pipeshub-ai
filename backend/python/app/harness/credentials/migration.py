"""The D2 migration of toolset and MCP credentials into control-plane bindings (CPD-8, 02-lld §11.4).

Each authenticated record at ``/services/toolsets/{instance}/{owner}`` or
``/services/mcp/credentials/{instance}/{owner}`` is copied into a binding through Node's binder
(``BindingWriter``), read back through the vault and its digest compared with the old record's;
a mismatch removes the copy again. The old path is never written by ``apply``: it stays readable
for one release and ``revert`` removes only what this migration imported, so reads fall back to
the old path. A binding refreshed since its copy is written back to the old path first, and a
binding revoked since is deleted there, so a revert neither loses a rotated refresh token nor
revives a revoked credential.

Scoping (SEC5): the owner's org comes from the graph; the instance must be one that org sees
(``get_instance_for_org`` for MCP, ``get_toolset_by_id(org_id=…)`` for toolsets), or the record
is held back. The report and the ledger carry ids, outcomes and digests, never a secret.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from app.core.org_context import org_of
from app.harness.credentials.binding_writer import (
    BindingNotImported,
    ImportRequest,
    Material,
)
from app.harness.credentials.bindings import last_version
from app.harness.credentials.read_through import record_digest
from app.harness.tool_identity.migration import LedgerUnwritable
from harness_core.credentials.types import CredentialScope
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from app.harness.credentials.binding_writer import BindingWriter
    from app.harness.credentials.bindings import BindingDocuments

MIGRATION_ID = "control_plane_credential_bindings_v1"
LEDGER_PATH = f"/migrations/{MIGRATION_ID}"
TOOLSETS_ROOT = "/services/toolsets/"
TOOLSET_INSTANCES_PATH = "/services/toolset-instances"
MCP_CREDENTIALS_ROOT = "/services/mcp/credentials/"
Mode = Literal["dry_run", "apply", "revert"]
Kind = Literal["toolset", "mcp"]
_MCP_MODES = {"oauth": "oauth", "api_token": "api_key", "headers": "header"}


def _ledger_org(entry: Mapping[str, str]) -> str:
    """The org a ledger entry was imported for; every entry names one, so one without it is a broken ledger."""
    org_id = org_of(entry)
    if org_id is None:
        raise KeyError("orgId")
    return org_id


class _Config(Protocol):
    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False  # noqa: FBT001, FBT002
    ) -> object: ...

    async def set_config(self, key: str, value: object) -> bool: ...

    async def delete_config(self, key: str) -> bool: ...

    async def list_keys_in_directory(self, directory: str) -> list[str]: ...


class OwnerOrgs(Protocol):
    """The org a credential owner belongs to: a user id, or an agent key; ``None`` when unknown."""

    async def org_of(self, kind: Literal["user", "agent"], owner: str) -> str | None: ...


class GraphOwnerOrgs:
    """``OwnerOrgs`` over ``IGraphDBProvider``: users by user id; agents by their document, else their creator."""

    def __init__(self, graph: Any) -> None:  # noqa: ANN401
        self._graph = graph

    async def org_of(self, kind: Literal["user", "agent"], owner: str) -> str | None:
        from app.config.constants.arangodb import CollectionNames

        if kind == "user":
            user = await self._graph.get_user_by_user_id(owner, raise_on_error=True)
            return _text(user, "orgId")
        agent = await self._graph.get_document(owner, CollectionNames.AGENT_INSTANCES.value)
        if _text(agent, "orgId"):
            return _text(agent, "orgId")
        creator = _text(agent, "createdBy")
        user = await self._graph.get_document(creator, CollectionNames.USERS.value) if creator else None
        return _text(user, "orgId")


def _text(document: object, key: str) -> str | None:
    value = cast("dict[str, Any]", document).get(key) if isinstance(document, dict) else None
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class Candidate:
    kind: Kind
    instance: str
    owner_id: str
    path: str
    record: dict[str, Any]

    @property
    def owner(self) -> str:
        return owner_ref(self.record, self.owner_id)

    @property
    def owner_kind(self) -> Literal["user", "agent"]:
        return "agent" if self.owner.startswith("agent:") else "user"

    def entry(self, org: str | None, outcome: str, **extra: str) -> dict[str, str]:
        return {"orgId": org or "", "kind": self.kind, "installation": self.instance, "owner": self.owner, "outcome": outcome, **extra}


def record_expiry_ms(kind: Kind, record: dict[str, Any]) -> int | None:
    """When the record's access token expires, in epoch ms, if it says (the refresher's due time)."""
    try:
        if kind == "mcp":
            from app.agents.mcp.models import OAuthTokens

            tokens = record.get("oauthTokens")
            if not isinstance(tokens, dict):
                return None
            parsed = OAuthTokens.model_validate(tokens)
            if not parsed.expires_in:
                return None
            return int((parsed.created_at.timestamp() + parsed.expires_in) * 1000)
        from app.connectors.core.base.token_service.oauth_service import OAuthToken

        credentials = record.get("credentials")
        if not isinstance(credentials, dict) or "access_token" not in credentials:
            return None
        at = OAuthToken.from_dict(cast("dict[str, Any]", credentials)).expires_at_epoch
        return None if at is None else at * 1000
    except (ValueError, TypeError):
        return None


def record_mode(kind: Kind, record: dict[str, Any]) -> Literal["oauth", "api_key", "header"]:
    """The binding mode of an old-path record: OAuth, or a key or header the user pasted."""
    if kind == "mcp":
        auth_mode = str(record.get("authMode") or "")
        if auth_mode in _MCP_MODES:
            return cast("Literal['oauth', 'api_key', 'header']", _MCP_MODES[auth_mode])
        return "oauth" if "oauthTokens" in record else "api_key"
    return "oauth" if str(record.get("authType") or "OAUTH").upper() == "OAUTH" else "api_key"


def owner_ref(record: dict[str, Any], owner_id: str) -> str:
    """``agent:<key>`` for a service-account agent's record (it names its ``agentKey``), else ``user:<id>``."""
    return f"agent:{owner_id}" if record.get("agentKey") == owner_id else f"user:{owner_id}"


@dataclass
class CredentialMigrationReport:
    mode: Mode
    copied: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    unchanged: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    held: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    mismatched: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    reverted: list[dict[str, str]] = field(default_factory=list[dict[str, str]])

    def counts(self) -> dict[str, int]:
        return {name: len(cast("list[object]", value)) for name, value in asdict(self).items() if isinstance(value, list)}

    def as_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "counts": self.counts(), **{k: v for k, v in asdict(self).items() if isinstance(v, list)}}


class CredentialBindingMigration:
    """``dry_run`` reports, ``apply`` copies and verifies, ``revert`` undoes ``apply``; each is idempotent."""

    id = MIGRATION_ID
    ledger_path = LEDGER_PATH

    def __init__(
        self,
        config: Any,  # noqa: ANN401
        owners: OwnerOrgs,
        writer: BindingWriter,
        documents: BindingDocuments,
        *,
        now_ms: Callable[[], int],
        log: HarnessLog | None = None,
    ) -> None:
        self._config = cast("_Config", config)
        self._owners = owners
        self._writer = writer
        self._documents = documents
        self._now_ms = now_ms
        self._log = log or HarnessLog.named("app.harness.credentials.migration", migration=MIGRATION_ID)

    async def run(self, mode: Mode) -> CredentialMigrationReport:
        if mode == "revert":
            return await self.revert()
        return await self.apply() if mode == "apply" else await self.dry_run()

    async def dry_run(self) -> CredentialMigrationReport:
        report = CredentialMigrationReport("dry_run")
        async for candidate, org, held in self._candidates():
            if held is not None:
                report.held.append(candidate.entry(org, "held", reason=held))
                continue
            assert org is not None
            state = await self._copied_digest(org, candidate)
            target = report.unchanged if state == record_digest(candidate.record) else report.copied
            target.append(candidate.entry(org, "unchanged" if target is report.unchanged else "copy"))
        self._log.info("migration.dry_run", **report.counts())
        return report

    async def apply(self) -> CredentialMigrationReport:
        report = CredentialMigrationReport("apply")
        async for candidate, org, held in self._candidates():
            if held is not None:
                report.held.append(candidate.entry(org, "held", reason=held))
                self._log.warning("migration.credential_held", orgId=org, kind=candidate.kind, reasonCode=held)
                continue
            assert org is not None
            await self._copy(org, candidate, report)
        self._log.info("migration.applied", **report.counts())
        return report

    async def _copy(self, org: str, candidate: Candidate, report: CredentialMigrationReport) -> None:
        digest = record_digest(candidate.record)
        await self._ledger_update(add=[candidate.entry(org, "imported", sourcePath=candidate.path, at=str(self._now_ms()))])
        imported = await self._writer.import_record(org, self._request(candidate, digest))
        if imported.outcome == "unchanged":
            report.unchanged.append(candidate.entry(org, "unchanged"))
            return
        if imported.outcome == "conflict":
            await self._ledger_update(remove={(org, candidate.instance, candidate.owner)})
            report.held.append(candidate.entry(org, "held", reason="binding_exists"))
            return
        copied = await self._read_back(org, candidate)
        if copied is None or record_digest(copied) != digest:
            _ = await self._writer.revert_import(org, candidate.instance, candidate.owner)
            await self._ledger_update(remove={(org, candidate.instance, candidate.owner)})
            report.mismatched.append(candidate.entry(org, "digest_mismatch"))
            self._log.warning("migration.digest_mismatch", orgId=org, kind=candidate.kind, reasonCode="digest_mismatch")
            return
        report.copied.append(candidate.entry(org, imported.outcome, binding=imported.binding.id if imported.binding else ""))

    async def revert(self) -> CredentialMigrationReport:
        report = CredentialMigrationReport("revert")
        for entry in await self._ledger():
            org, installation, owner = _ledger_org(entry), entry["installation"], entry["owner"]
            path, document = await self._documents.owner_document(org, installation, owner)
            if document:
                await self._restore_old_path(entry["sourcePath"], org, path, document)
            try:
                _ = await self._writer.revert_import(org, installation, owner)
            except BindingNotImported:
                report.held.append({**entry, "outcome": "held", "reason": "binding_not_imported"})
                continue
            await self._ledger_update(remove={(org, installation, owner)})
            report.reverted.append({k: v for k, v in entry.items() if k != "at"} | {"outcome": "reverted"})
        self._log.info("migration.reverted", **report.counts())
        return report

    async def _restore_old_path(self, source: str, org: str, path: str, document: dict[str, Any]) -> None:
        if document.get("revoked"):
            _ = await self._config.delete_config(source)
            return
        meta = cast("dict[str, Any]", document.get("binding") or {})
        if last_version(document) == meta.get("importedVersion"):
            return
        current = await self._documents.vault.read_at(path, org, CredentialScope("binding", str(meta.get("binding"))))
        if current is not None and not await self._config.set_config(source, current.material):
            raise LedgerUnwritable(f"{source} could not be restored")

    async def _copied_digest(self, org: str, candidate: Candidate) -> str | None:
        _, document = await self._documents.owner_document(org, candidate.instance, candidate.owner)
        meta = document.get("binding")
        source = cast("dict[str, Any]", meta).get("source") if isinstance(meta, dict) else None
        return cast("dict[str, Any]", source).get("digest") if isinstance(source, dict) else None

    async def _read_back(self, org: str, candidate: Candidate) -> object:
        path, document = await self._documents.owner_document(org, candidate.instance, candidate.owner)
        meta = cast("dict[str, Any]", document.get("binding") or {})
        secret = await self._documents.vault.read_at(path, org, CredentialScope("binding", str(meta.get("binding"))))
        return None if secret is None else secret.material

    def _request(self, candidate: Candidate, digest: str) -> ImportRequest:
        return ImportRequest(
            installation=candidate.instance,
            owner=candidate.owner,
            mode=record_mode(candidate.kind, candidate.record),
            kind=candidate.kind,
            provider=None,
            material=Material("record", candidate.record, record_expiry_ms(candidate.kind, candidate.record)),
            source_path=candidate.path,
            source_digest=digest,
        )

    async def _candidates(self):  # noqa: ANN202 - an async generator of (Candidate, org, held reason)
        for kind, root, depth in (("toolset", TOOLSETS_ROOT, 4), ("mcp", MCP_CREDENTIALS_ROOT, 5)):
            for path in sorted(await self._config.list_keys_in_directory(root)):
                parts = path.strip("/").split("/")
                if len(parts) != depth:
                    continue
                record = await self._config.get_config(path, default=None, use_cache=False, raise_on_error=True)
                if not isinstance(record, dict) or not cast("dict[str, Any]", record).get("isAuthenticated"):
                    continue
                candidate = Candidate(cast("Kind", kind), parts[-2], parts[-1], path, cast("dict[str, Any]", record))
                org = await self._owners.org_of(candidate.owner_kind, candidate.owner_id)
                if org is None:
                    yield candidate, None, "owner_unknown"
                elif not await self._visible(candidate, org):
                    yield candidate, org, "not_visible"
                else:
                    yield candidate, org, None

    async def _visible(self, candidate: Candidate, org: str) -> bool:
        from app.agents.constants.mcp_server_constants import get_mcp_instance_path

        if candidate.kind == "mcp":
            from app.agents.mcp.instance_scope import get_instance_for_org

            seen = await get_instance_for_org(candidate.instance, cast("Any", self._config), org) is not None
        else:
            from app import edition_config

            seen = await edition_config.get_toolset_by_id(candidate.instance, cast("Any", self._config), org_id=org) is not None
        if not seen:
            # The edition seams answer None when the store fails: re-read their key so an outage stops the run
            # instead of holding a visible record as `not_visible` (QA12-F2).
            key = get_mcp_instance_path(candidate.instance) if candidate.kind == "mcp" else TOOLSET_INSTANCES_PATH
            _ = await self._config.get_config(key, default=None, use_cache=False, raise_on_error=True)
        return seen

    async def _ledger(self) -> list[dict[str, str]]:
        stored = await self._config.get_config(LEDGER_PATH, default=None, use_cache=False, raise_on_error=True)
        entries = cast("dict[str, Any]", stored).get("imported") if isinstance(stored, dict) else None
        return [cast("dict[str, str]", e) for e in cast("list[object]", entries or []) if isinstance(e, dict)]

    async def _ledger_update(
        self, *, add: list[dict[str, str]] | None = None, remove: set[tuple[str, str, str]] | None = None
    ) -> None:
        def key(entry: dict[str, str]) -> tuple[str, str, str]:
            return _ledger_org(entry), entry["installation"], entry["owner"]

        drop = (remove or set()) | {key(e) for e in add or []}
        entries = [e for e in await self._ledger() if key(e) not in drop] + list(add or [])
        if not await self._config.set_config(LEDGER_PATH, {"migration": MIGRATION_ID, "imported": entries}):
            raise LedgerUnwritable(f"{LEDGER_PATH} could not be written")


__all__ = [
    "LEDGER_PATH",
    "MIGRATION_ID",
    "CredentialBindingMigration",
    "GraphOwnerOrgs",
    "LedgerUnwritable",
    "CredentialMigrationReport",
    "Mode",
    "OwnerOrgs",
    "owner_ref",
    "record_expiry_ms",
    "record_mode",
]
