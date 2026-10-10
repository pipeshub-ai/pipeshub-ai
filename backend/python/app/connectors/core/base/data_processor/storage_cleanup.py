"""Storage cleanup helper for blob lifecycle management.

Invoked from DataSourceEntitiesProcessor when records are deleted or moved to
ensure the corresponding blobs are purged / relocated in the storage backend.
The helper calls the Node.js storage service using the same scoped-JWT auth
pattern as BlobStorage.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import aiohttp

from app.config.constants.arangodb import (
    CollectionNames,
    EventTypes,
    ProgressStatus,
    RecordTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.services.messaging.config import Topic
from app.utils.jwt import mint_service_token
from app.utils.request_context import inject_request_headers
from app.utils.storage_path import (
    build_hierarchical_storage_path,
    build_record_group_prefix_from_chain,
)
from app.utils.storage_path import (
    build_record_group_path as _build_record_group_path,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

# Virtual records listed per page, and handovers per relocate call (Node's cap).
VIRTUAL_RECORD_PAGE_SIZE = 500
RELOCATE_BATCH_SIZE = 100
_ROUTE_ABSENT = (HttpStatusCode.NOT_FOUND.value, 405)


class StorageHandoverError(Exception):
    """Shared content could not be handed over, so nothing may be deleted."""


@dataclass
class StorageReleaseResult:
    completed: bool = False
    listed: int = 0
    handed_over: int = 0
    failed: int = 0
    deleted: int = 0
    reason: str | None = None


def choose_storage_owner(holders: list[dict]) -> dict:
    """Live holders before trashed ones, then the smallest key, so every run
    picks the same owner."""
    return min(holders, key=lambda h: (bool(h.get("isDeleted")), str(h.get("id"))))


# Node's cap on one missing-documents lookup (MAX_MISSING_DOCUMENT_IDS).
MISSING_DOCUMENTS_BATCH = 500


class MissingDocumentsRouteUnavailable(Exception):
    """Node predates the missing-documents route (404/405), e.g. mid-upgrade."""

    def __init__(self, status: int) -> None:
        super().__init__(f"storage has no missing-documents route (HTTP {status})")
        self.status = status


# A record in one of these is already headed for indexing, and indexing a holder
# rewrites the VRID's stored content.
_INDEXING_UNDER_WAY = frozenset({
    ProgressStatus.NOT_STARTED.value,
    ProgressStatus.QUEUED.value,
    ProgressStatus.IN_PROGRESS.value,
})


def indexing_under_way(record: dict | None) -> bool:
    return bool(record) and record.get("indexingStatus") in _INDEXING_UNDER_WAY


@dataclass(frozen=True)
class HolderReindex:
    """What ``reindex_one_holder`` did: published a re-index of ``record_key``,
    found ``record_key`` already being indexed, or found no live holder."""

    published: bool = False
    record_key: str | None = None

    @property
    def under_way(self) -> bool:
        return not self.published and self.record_key is not None


class StorageCleanupHelper:
    """Handles blob storage cleanup when records are deleted or moved."""

    def __init__(self, logger, graph_provider, config_service) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Path building helpers (delegates to shared app.utils.storage_path)
    # ------------------------------------------------------------------

    async def build_record_path(
        self, record: Any, transaction: str | None = None
    ) -> str | None:
        """Build the hierarchical storage path for any record (file or folder).

        Returns None when the path cannot be reliably computed — callers
        must treat None as "skip this move".
        """
        return await build_hierarchical_storage_path(
            record,
            self.graph_provider,
            virtual_record_id=getattr(record, "virtual_record_id", None),
            transaction=transaction,
            logger=self.logger,
        )

    def build_record_group_path(
        self, connector_id: str | None, group_name: str | None
    ) -> str | None:
        """Build the storage path prefix for a record group."""
        return _build_record_group_path(connector_id, group_name)

    async def build_record_group_hierarchical_prefix(
        self,
        record_group_id: str,
        connector_id: str,
        *,
        override_leaf_name: str | None = None,
        transaction: str | None = None,
    ) -> str | None:
        """Build full hierarchical prefix for a record group via graph traversal.

        *override_leaf_name* replaces the leaf group's own name — used to
        compute the old prefix during renames when the graph already holds
        the new name.  Falls back to the flat single-segment prefix when the
        traversal returns nothing; returns None when the traversal fails.
        """
        try:
            gp_kwargs: dict = {"raise_on_error": True}
            if transaction is not None:
                gp_kwargs["transaction"] = transaction
            group_names = await self.graph_provider.get_record_group_path(
                record_group_id, **gp_kwargs
            )
        except Exception as e:
            self.logger.warning("get_record_group_path failed: %s", str(e))
            # Unknown ancestry: a flat guess could name a different group's tree.
            return None

        if group_names and override_leaf_name is not None:
            group_names = group_names[:-1] + [override_leaf_name]

        if group_names:
            return build_record_group_prefix_from_chain(connector_id, group_names)

        leaf = override_leaf_name
        if not leaf:
            try:
                gp_kwargs = {}
                if transaction is not None:
                    gp_kwargs["transaction"] = transaction
                group = await self.graph_provider.get_record_group_by_id(
                    record_group_id, **gp_kwargs
                )
                if group:
                    leaf = group.get("groupName") or group.get("name", "")
            except Exception:
                pass
        return _build_record_group_path(connector_id, leaf) if leaf else None

    # ------------------------------------------------------------------
    # Internal auth / config helpers (mirrors BlobStorage._get_auth_and_config)
    # ------------------------------------------------------------------

    async def _get_auth_headers_and_endpoint(self, org_id: str) -> tuple[dict, str]:
        """Return (headers, nodejs_endpoint) for internal storage API calls."""
        payload = {
            "orgId": org_id,
            "scopes": [TokenScopes.STORAGE_TOKEN.value],
        }
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise ValueError("Missing scoped JWT secret")

        jwt_token = mint_service_token(scoped_jwt_secret, payload)
        headers = inject_request_headers({"Authorization": f"Bearer {jwt_token}"})

        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        nodejs_endpoint = endpoints.get("cm", {}).get(
            "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
        )
        if not nodejs_endpoint:
            raise ValueError("Missing CM endpoint configuration")

        return headers, nodejs_endpoint

    # ------------------------------------------------------------------
    # Storage document operations
    # ------------------------------------------------------------------

    async def release_connector_storage(
        self, org_id: str, connector_id: str
    ) -> StorageReleaseResult:
        """Delete a deleted connector's storage, handing shared content over first.

        Deduplicated content is stored once, under whichever connector indexed
        it first, and records elsewhere read the same documents. Each virtual
        record this connector's storage holds that a record in another
        connector (live, else trashed) still has is moved under that record's
        own path and re-tagged with its connector; the document id, and so the
        mapping every holder reads, stays the same. Only once every handover
        succeeded is the connector's storage deleted: on any failure nothing is
        deleted and the result says why, so the caller can retry. Run after the
        connector's records left the graph; sharing is read from storage and
        the graph each time, so a retry repeats exactly the same work.
        """
        result = StorageReleaseResult()
        self.logger.info(
            "Storage release started | org=%s connector=%s", org_id, connector_id
        )
        try:
            after: str | None = None
            while True:
                vrids, after = await self._list_connector_virtual_records(
                    org_id, connector_id, after
                )
                result.listed += len(vrids)
                if vrids:
                    moved, failed = await self._hand_over_shared(org_id, connector_id, vrids)
                    result.handed_over += moved
                    result.failed += failed
                if not after:
                    break
        except Exception as e:
            result.reason = f"handover could not run: {e}"
            self.logger.error(
                "Storage release: handover could not run; storage kept | org=%s connector=%s "
                "listed=%d handed_over=%d: %s",
                org_id, connector_id, result.listed, result.handed_over, e,
            )
            return result

        if result.failed:
            result.reason = f"{result.failed} shared virtual record(s) were not handed over"
            self.logger.warning(
                "Storage release: delete skipped, handover incomplete | org=%s connector=%s "
                "listed=%d handed_over=%d failed=%d",
                org_id, connector_id, result.listed, result.handed_over, result.failed,
            )
            return result

        try:
            result.deleted = await self.delete_connector_storage(org_id, connector_id)
        except Exception as e:
            result.reason = f"storage delete failed: {e}"
            self.logger.error(
                "Storage release: delete failed after handover | org=%s connector=%s "
                "handed_over=%d: %s",
                org_id, connector_id, result.handed_over, e,
            )
            return result

        result.completed = True
        self.logger.info(
            "Storage release completed | org=%s connector=%s listed=%d handed_over=%d deleted=%d",
            org_id, connector_id, result.listed, result.handed_over, result.deleted,
        )
        return result

    async def _list_connector_virtual_records(
        self, org_id: str, connector_id: str, after: str | None
    ) -> tuple[list[str], str | None]:
        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(org_id)
        url = (
            f"{nodejs_endpoint}"
            f"{Routes.STORAGE_CONNECTOR_VIRTUAL_RECORDS.value.format(connector_id=connector_id)}"
        )
        params: dict[str, str | int] = {"limit": VIRTUAL_RECORD_PAGE_SIZE}
        if after:
            params["after"] = after
        session = await self._get_session()
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                raise StorageHandoverError(
                    f"listing the connector's virtual records failed: {resp.status} "
                    f"{(await resp.text())[:200]}"
                )
            body = await resp.json()
        vrids = [v for v in body.get("virtualRecordIds") or [] if isinstance(v, str) and v]
        nxt = body.get("next")
        return vrids, nxt if isinstance(nxt, str) and nxt else None

    async def _hand_over_shared(
        self, org_id: str, connector_id: str, vrids: list[str]
    ) -> tuple[int, int]:
        """(handed over, failed) for one page of the connector's virtual records."""
        holders = await self.graph_provider.get_virtual_record_holders(vrids, org_id)
        moves: list[dict] = []
        failed = 0
        for vrid in vrids:
            others = [
                h for h in holders.get(vrid) or []
                if h.get("id") and h.get("connectorId") and h["connectorId"] != connector_id
            ]
            if not others:
                continue
            owner = choose_storage_owner(others)
            path = await self._owner_storage_path(owner)
            if not path:
                failed += 1
                self.logger.warning(
                    "Storage release: no storage path for holder %s of VRID %s | connector=%s",
                    owner.get("id"), vrid, connector_id,
                )
                continue
            move = {"virtualRecordId": vrid, "newPath": path, "connectorId": owner["connectorId"]}
            if owner.get("recordGroupId"):
                move["recordGroupId"] = owner["recordGroupId"]
            moves.append(move)

        moved = 0
        for i in range(0, len(moves), RELOCATE_BATCH_SIZE):
            batch = moves[i : i + RELOCATE_BATCH_SIZE]
            done = await self._relocate(org_id, connector_id, batch)
            moved += done
            failed += len(batch) - done
        return moved, failed

    async def _owner_storage_path(self, owner: dict) -> str | None:
        # No flat fallback: a failed path lookup is retried rather than filing
        # the copy where the owner's connector scope cannot find it.
        record = SimpleNamespace(
            id=owner.get("id"),
            connector_id=owner.get("connectorId"),
            connector_name=owner.get("connectorName"),
            record_group_id=owner.get("recordGroupId"),
            record_name=owner.get("recordName"),
            weburl=owner.get("webUrl"),
        )
        return await build_hierarchical_storage_path(
            record, self.graph_provider, logger=self.logger
        )

    async def _relocate(self, org_id: str, connector_id: str, moves: list[dict]) -> int:
        """How many of ``moves`` Node handed over (or found nothing to move for)."""
        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(org_id)
        url = f"{nodejs_endpoint}{Routes.STORAGE_RELOCATE_RECORDS.value}"
        session = await self._get_session()
        try:
            async with session.post(
                url, json={"fromConnectorId": connector_id, "moves": moves}, headers=headers
            ) as resp:
                if resp.status in _ROUTE_ABSENT:
                    # A storage service older than this one: nothing can be handed over.
                    raise StorageHandoverError(
                        f"the storage service has no relocate route ({resp.status})"
                    )
                if resp.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(
                        "Storage release: relocate failed | org=%s connector=%s moves=%d: %s %s",
                        org_id, connector_id, len(moves), resp.status, (await resp.text())[:200],
                    )
                    return 0
                body = await resp.json()
        except StorageHandoverError:
            raise
        except Exception as e:
            self.logger.warning(
                "Storage release: relocate request failed | org=%s connector=%s moves=%d: %s",
                org_id, connector_id, len(moves), e,
            )
            return 0
        asked = {m["virtualRecordId"] for m in moves}
        settled = asked & {*(body.get("moved") or []), *(body.get("missing") or [])}
        return len(settled)

    async def reindex_one_holder(
        self,
        vrid: str,
        publish: Callable[[str, dict], Awaitable[Any]],
    ) -> HolderReindex:
        """Force re-index one live record holding ``vrid``; its storage write
        re-points the VRID's mapping at a new document, healing every holder.

        Nothing is published while a holder is already being indexed: that run
        rewrites the content too. A published holder is marked QUEUED, as a
        manual re-index marks it, so the next caller (or the next process) sees
        it under way. With no live holder nothing is published (the mapping row
        is then the orphan sweeper's to drop). Raises when the event could not
        be published.
        """
        holders = await self.graph_provider.get_records_by_virtual_record_id(
            vrid, raise_on_error=True
        )
        record = None
        for key in holders:
            doc = await self.graph_provider.get_document(key, CollectionNames.RECORDS.value)
            if indexing_under_way(doc):
                return HolderReindex(record_key=key)
            record = record or doc
        if not record:
            self.logger.warning(
                "No live record found for shared VRID %s; nothing re-indexed", vrid
            )
            return HolderReindex()
        record_key = record.get("_key") or record.get("id")
        file_record = None
        if record.get("recordType") == RecordTypes.FILE.value:
            file_record = await self.graph_provider.get_document(
                record_key, CollectionNames.FILES.value
            )
        payload = await self.graph_provider._create_reindex_event_payload(record, file_record)
        payload["forceReindex"] = True
        sent = await publish(
            Topic.RECORD_EVENTS.value,
            {
                "eventType": EventTypes.NEW_RECORD.value,
                "timestamp": get_epoch_timestamp_in_ms(),
                "payload": payload,
            },
        )
        # Publishers report failure by returning False rather than raising.
        if sent is False:
            raise RuntimeError("re-index event was not published")
        # After the publish, never before (a record marked QUEUED for an event
        # that never left is stuck), and conditional: indexing may already have
        # moved it on.
        try:
            await self.graph_provider.compare_and_set_indexing_status(
                [record_key], record.get("indexingStatus"), ProgressStatus.QUEUED.value,
            )
        except Exception as e:
            self.logger.warning(
                "Re-index of %s for VRID %s published but not marked QUEUED: %s",
                record_key, vrid, e,
            )
        return HolderReindex(published=True, record_key=record_key)

    async def find_missing_documents(self, org_id: str, document_ids: list[str]) -> list[str]:
        """The ids storage cannot serve for the org: absent or deleted, as download sees it.

        Raises ``MissingDocumentsRouteUnavailable`` when Node has no such route,
        and any other failure as an exception: never an empty answer that would
        read as "nothing is missing".
        """
        ids = list(dict.fromkeys(d for d in document_ids if d))
        missing: list[str] = []
        for start in range(0, len(ids), MISSING_DOCUMENTS_BATCH):
            batch = ids[start:start + MISSING_DOCUMENTS_BATCH]
            headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(org_id)
            url = f"{nodejs_endpoint}{Routes.STORAGE_MISSING_DOCUMENTS.value}"
            session = await self._get_session()
            async with session.post(url, json={"documentIds": batch}, headers=headers) as resp:
                if resp.status in (HttpStatusCode.NOT_FOUND.value, HttpStatusCode.METHOD_NOT_ALLOWED.value):
                    raise MissingDocumentsRouteUnavailable(resp.status)
                if resp.status != HttpStatusCode.SUCCESS.value:
                    error_text = await resp.text()
                    raise Exception(
                        f"missing-documents lookup failed: {resp.status} {error_text[:200]}"
                    )
                body = await resp.json()
            answer = body.get("missing") if isinstance(body, dict) else None
            if not isinstance(answer, list):
                raise ValueError("storage answered a malformed missing-documents reply")
            missing.extend(str(d) for d in answer)
        return missing

    async def delete_connector_storage(
        self, org_id: str, connector_id: str
    ) -> int:
        """Delete all blobs and MongoDB storage documents for a connector.

        Returns the number of storage documents deleted.
        """
        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(
            org_id
        )
        delete_url = (
            f"{nodejs_endpoint}"
            f"{Routes.STORAGE_DELETE_CONNECTOR.value.format(connector_id=connector_id)}"
        )
        session = await self._get_session()
        async with session.delete(delete_url, headers=headers) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                error_text = await resp.text()
                raise Exception(
                    f"Connector storage delete failed: "
                    f"{resp.status} {error_text[:200]}"
                )
            body = await resp.json()
            deleted = body.get("deleted", 0)
        self.logger.info(
            "Deleted %d storage documents for connector %s",
            deleted,
            connector_id,
        )
        return deleted

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def move_record_tree(
        self,
        org_id: str,
        old_path: str,
        new_path: str,
        *,
        virtual_record_id: str | None = None,
        virtual_record_ids: list[str] | None = None,
    ) -> dict:
        """Relocate a record's own content (if any) and every descendant
        currently stored under old_path, in one call to Node's move-tree
        endpoint. See docs/superpowers/specs/2026-07-07-blob-move-tree-design.md.

        When *virtual_record_id* is supplied, the endpoint detects whether
        other records share the same storage prefix.  On collision it moves
        only the identified record's documents (both ``record_<vrid>`` and
        ``metadata_<vrid>``) instead of the whole prefix tree, preventing
        sibling records' blobs from being swept up.

        *virtual_record_ids* is the folder form: every vrid stored beneath
        the moved record (possibly none). When old_path also holds documents
        of other vrids — a sibling folder whose name sanitizes the same — the
        endpoint moves only the listed ones.

        Returns the JSON response from Node (always contains ``moved``
        count and, when either vrid argument was given, a ``collision`` flag).

        Safe to call with old_path == new_path -- becomes a no-op with no
        network call, since there would be nothing to move.
        """
        if old_path == new_path:
            return {"moved": 0}

        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(org_id)
        move_url = f"{nodejs_endpoint}{Routes.STORAGE_MOVE_TREE.value}"
        body: dict = {"oldPath": old_path, "newPath": new_path}
        if virtual_record_id:
            body["virtualRecordId"] = virtual_record_id
        elif virtual_record_ids is not None:
            body["virtualRecordIds"] = virtual_record_ids

        session = await self._get_session()
        async with session.post(move_url, json=body, headers=headers) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                error_text = await resp.text()
                raise Exception(
                    f"move-tree failed: {resp.status} {error_text[:200]}"
                )
            result = await resp.json()
        self.logger.info("✅ Moved storage tree %s -> %s", old_path, new_path)
        return result if isinstance(result, dict) else {"moved": 0}
