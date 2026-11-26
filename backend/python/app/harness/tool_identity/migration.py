"""Moving saved agents' type-keyed toolsets to their instance (P2.2), over ``IGraphDBProvider``.

An agent's toolsets are graph nodes (``agentInstances`` → ``agentHasToolset`` → ``agentToolsets``).
A node without ``instanceId`` is bound by type only. ``harness_core.tool_identity.plan_rekeys``
decides what moves; this module reads the graph and the org's toolset instances, writes, and keeps
a ledger of what it wrote (``ConfigurationService``) so ``revert`` undoes exactly that.

Writes are guarded replaces (``update_node_if_match``): a node edited between the plan and the
write is left as the editor left it and reported. A replace, not a patch, so ``revert`` removes
the field on ArangoDB too (a PATCH with ``null`` stores ``null``, which the toolset schema refuses).
The ledger entry is written before the node, so a crash between them leaves an entry for a node
still unkeyed, which ``apply`` rewrites and ``revert`` clears, rather than a node no ledger knows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast

from app.config.constants.arangodb import CollectionNames
from harness_core.logs import HarnessLog
from harness_core.tool_identity import (
    DefinitionBinding,
    InstanceCandidate,
    OwnerBindings,
    Rekey,
    plan_rekeys,
    plan_reverts,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MIGRATION_ID = "agent_toolset_instances_v1"
LEDGER_PATH = f"/migrations/{MIGRATION_ID}"
TOOLSET_INSTANCES_PATH = "/services/toolset-instances"
PAGE = 200
Mode = Literal["dry_run", "apply", "revert"]
Document = dict[str, Any]


class _Graph(Protocol):
    """The ``IGraphDBProvider`` calls the migration makes, with the shapes both providers return."""

    async def get_documents_paginated(
        self, collection: str, skip: int = 0, limit: int = 50, *, sort_field: str | None = None, raise_on_error: bool = False
    ) -> list[Document]: ...

    async def get_edges_from_node(self, node_id: str, edge_collection: str) -> list[Document]: ...

    async def get_document(self, document_key: str, collection: str) -> Document | None: ...

    async def update_node_if_match(
        self, key: str, collection: str, node: Document, match_field: str, match_value: object
    ) -> bool: ...


class _Config(Protocol):
    async def get_config(
        self, key: str, default: object = None, *, use_cache: bool = False, raise_on_error: bool = False
    ) -> object: ...

    async def set_config(self, key: str, value: object) -> bool: ...


def _documents(value: object) -> list[Document]:
    items = cast("list[object]", value) if isinstance(value, list) else []
    return [cast("Document", item) for item in items if isinstance(item, dict)]


def _text(document: Document, key: str) -> str:
    value = document.get(key)
    return value if isinstance(value, str) else ""

_AGENTS = CollectionNames.AGENT_INSTANCES.value
_TOOLSETS = CollectionNames.AGENT_TOOLSETS.value
_HAS_TOOLSET = CollectionNames.AGENT_HAS_TOOLSET.value
_USERS = CollectionNames.USERS.value
# Fields a stored document carries that are the store's, not the node's.
_STORE_FIELDS = frozenset({"_id", "_rev", "id"})


@dataclass
class MigrationReport:
    mode: Mode
    agents: int = 0
    already_keyed: int = 0
    rekeyed: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    held: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    reverted: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    drifted: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    conflicts: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def counts(self) -> dict[str, int]:
        return {
            "agents": self.agents,
            "alreadyKeyed": self.already_keyed,
            "rekeyed": len(self.rekeyed),
            "held": len(self.held),
            "reverted": len(self.reverted),
            "drifted": len(self.drifted),
            "conflicts": len(self.conflicts),
        }

    def as_json(self) -> dict[str, Any]:
        return {"mode": self.mode, "counts": self.counts(), **{k: v for k, v in asdict(self).items() if isinstance(v, list)}}


def _rekey_json(rekey: Rekey) -> dict[str, Any]:
    return {"agentKey": rekey.owner, "toolsetKey": rekey.binding, "toolset": rekey.kind, "instanceId": rekey.instance}


def _node(document: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in _STORE_FIELDS}


def _match_field(document: dict[str, Any]) -> str:
    return "updatedAtTimestamp" if document.get("updatedAtTimestamp") is not None else "createdAtTimestamp"


class AgentToolsetMigration:
    """``dry_run`` reports, ``apply`` re-keys, ``revert`` undoes ``apply``; each is idempotent."""

    id = MIGRATION_ID
    ledger_path = LEDGER_PATH

    def __init__(
        self,
        graph: IGraphDBProvider,
        config: ConfigurationService,
        *,
        now_ms: Callable[[], int],
        log: HarnessLog | None = None,
    ) -> None:
        self._graph = cast("_Graph", graph)
        self._config = cast("_Config", config)
        self._now_ms = now_ms
        self._log = log or HarnessLog.named("app.harness.tool_identity", migration=MIGRATION_ID)

    async def run(self, mode: Mode) -> MigrationReport:
        if mode == "revert":
            return await self.revert()
        return await self.apply() if mode == "apply" else await self.dry_run()

    async def dry_run(self) -> MigrationReport:
        report, _ = await self._plan("dry_run")
        self._log.info("migration.dry_run", **report.counts())
        return report

    async def apply(self) -> MigrationReport:
        report, rekeys = await self._plan("apply")
        report.rekeyed = []
        for rekey in rekeys:
            entry = _rekey_json(rekey) | {"instanceName": rekey.instance_name, "at": self._now_ms()}
            await self._ledger_update(add=[entry])
            if await self._write_rekey(rekey):
                report.rekeyed.append(_rekey_json(rekey))
            else:
                await self._ledger_update(remove={rekey.binding})
                report.conflicts.append(_rekey_json(rekey))
        self._log.info("migration.applied", **report.counts())
        return report

    async def revert(self) -> MigrationReport:
        report = MigrationReport("revert")
        applied = [
            Rekey(e["agentKey"], e["toolsetKey"], e["toolset"], e["instanceId"], e.get("instanceName"))
            for e in (await self._ledger())["applied"]
        ]
        current: dict[str, str | None] = {}
        documents: dict[str, dict[str, Any]] = {}
        for rekey in applied:
            document = await self._graph.get_document(rekey.binding, _TOOLSETS)
            if document:
                documents[rekey.binding] = document
                current[rekey.binding] = _text(document, "instanceId") or None
        plan = plan_reverts(applied, current)
        done: set[str] = {rekey.binding for rekey in plan.settled}
        for rekey in plan.reverts:
            document = documents[rekey.binding]
            node = {k: v for k, v in _node(document).items() if k not in ("instanceId", "instanceName")}
            node["updatedAtTimestamp"] = self._now_ms()
            if await self._graph.update_node_if_match(rekey.binding, _TOOLSETS, node, "instanceId", rekey.instance):
                done.add(rekey.binding)
                report.reverted.append(_rekey_json(rekey))
            else:
                report.conflicts.append(_rekey_json(rekey))
        report.drifted = [_rekey_json(d.rekey) | {"reason": d.reason} for d in plan.drifted]
        if done:
            await self._ledger_update(remove=done)
        self._log.info("migration.reverted", **report.counts())
        return report

    async def _plan(self, mode: Mode) -> tuple[MigrationReport, tuple[Rekey, ...]]:
        owners = [owner async for owner in self._owners()]
        plan = plan_rekeys(owners, await self._candidates())
        report = MigrationReport(mode, agents=len(owners), already_keyed=plan.already_keyed)
        report.rekeyed = [_rekey_json(r) for r in plan.rekeys]
        unresolved: dict[str, list[dict[str, Any]]] = {}
        for item in plan.unresolved:
            unresolved.setdefault(item.owner, []).append(
                {"toolsetKey": item.binding, "toolset": item.kind, "reason": item.reason, "candidates": list(item.candidates)}
            )
        report.held = [{"agentKey": owner, "unresolved": unresolved.get(owner, [])} for owner in plan.held]
        for owner in plan.held:
            for item in unresolved.get(owner, []):
                self._log.warning(
                    "migration.agent_held", agentKey=owner, toolsetKey=item["toolsetKey"], reasonCode=item["reason"],
                    candidates=len(item["candidates"]),
                )
        return report, plan.rekeys

    async def _owners(self):  # noqa: ANN202 - an async generator of OwnerBindings
        tenants: dict[str, str | None] = {}
        skip = 0
        while True:
            page = await self._graph.get_documents_paginated(_AGENTS, skip=skip, limit=PAGE, sort_field="_key", raise_on_error=True)
            if not page:
                return
            skip += len(page)
            for agent in page:
                if agent.get("isDeleted"):
                    continue
                key = _text(agent, "_key") or _text(agent, "id")
                bindings = await self._bindings(key)
                if not bindings:
                    continue
                yield OwnerBindings(key, await self._tenant(_text(agent, "createdBy"), tenants), tuple(bindings))

    async def _bindings(self, agent_key: str) -> list[DefinitionBinding]:
        found: list[DefinitionBinding] = []
        for edge in await self._graph.get_edges_from_node(f"{_AGENTS}/{agent_key}", _HAS_TOOLSET):
            toolset_key = (_text(edge, "_to") or _text(edge, "to_id")).rsplit("/", 1)[-1]
            document = await self._graph.get_document(toolset_key, _TOOLSETS) if toolset_key else None
            if document:
                found.append(DefinitionBinding(toolset_key, _text(document, "name"), _text(document, "instanceId") or None))
        return found

    async def _tenant(self, user_key: str, cache: dict[str, str | None]) -> str | None:
        if user_key not in cache:
            user = await self._graph.get_document(user_key, _USERS) if user_key else None
            cache[user_key] = _text(user or {}, "orgId") or None
        return cache[user_key]

    async def _candidates(self) -> list[InstanceCandidate]:
        instances = await self._config.get_config(TOOLSET_INSTANCES_PATH, default=[], use_cache=False, raise_on_error=True)
        return [
            InstanceCandidate(_text(i, "orgId"), _text(i, "toolsetType"), _text(i, "_id"), _text(i, "instanceName") or None)
            for i in _documents(instances)
            if _text(i, "orgId") and _text(i, "toolsetType") and _text(i, "_id")
        ]

    async def _write_rekey(self, rekey: Rekey) -> bool:
        document = await self._graph.get_document(rekey.binding, _TOOLSETS)
        if not document or document.get("instanceId"):
            return False
        node = _node(document) | {"instanceId": rekey.instance, "updatedAtTimestamp": self._now_ms()}
        if rekey.instance_name:
            node["instanceName"] = rekey.instance_name
        field_name = _match_field(document)
        return await self._graph.update_node_if_match(rekey.binding, _TOOLSETS, node, field_name, document[field_name])

    async def _ledger(self) -> dict[str, list[Document]]:
        stored = await self._config.get_config(LEDGER_PATH, default=None, use_cache=False, raise_on_error=True)
        applied = cast("Document", stored).get("applied") if isinstance(stored, dict) else None
        return {"applied": _documents(applied)}

    async def _ledger_update(self, *, add: list[dict[str, Any]] = (), remove: set[str] = frozenset()) -> None:  # type: ignore[assignment]
        ledger = await self._ledger()
        drop = set(remove) | {e.get("toolsetKey") for e in add}
        applied = [e for e in ledger["applied"] if e.get("toolsetKey") not in drop] + list(add)
        if not await self._config.set_config(LEDGER_PATH, {"migration": MIGRATION_ID, "applied": applied}):
            # Never write a node the ledger cannot undo.
            raise LedgerUnwritable(f"{LEDGER_PATH} could not be written")


class LedgerUnwritable(RuntimeError):
    reason_code = "migration_ledger_unwritable"


__all__ = ["LEDGER_PATH", "MIGRATION_ID", "AgentToolsetMigration", "LedgerUnwritable", "MigrationReport", "Mode"]
