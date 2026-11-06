"""Sync state and page tokens: a user's and a drive's sync state, the page token of a watched channel, the indexed-copy flag of a record and the
status resets and promotions the indexing service makes.

Every method resolves the ambient org first (before any ``try``, so a missing org is an error and never "not found"); every statement is a
literal with bound parameters. A sweep over every org (the flag backfill) runs under a system context only, one org at a time.
"""
from __future__ import annotations

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.core.org_context import current_org_id
from app.services.graph_db.common.record_visibility import (
    HAS_INDEXED_COPY,
    RecordVisibility,
    indexed_copy_fields,
)
from app.services.graph_db.interface.graph_db_provider import (
    DUPLICATE_RECONCILE_ATTEMPTS_FIELD,
    DUPLICATE_RECONCILE_DUE_AT_FIELD,
    DUPLICATE_RECONCILE_GRACE_MS,
    DUPLICATE_RECONCILE_PENDING_FIELD,
    promoted_duplicate_extraction_status,
)
from app.services.graph_db.postgres.base import PgBase, _arango, _doc, _j
from app.services.graph_db.postgres.records import _vis
from app.utils.time_conversion import get_epoch_timestamp_in_ms

_RECONCILED_STATUSES = frozenset({ProgressStatus.COMPLETED.value, ProgressStatus.EMPTY.value})
_ORG_FIELD = "orgId"     # the org property of a stored record, compared with the reference record's; the org of a call is the ambient one
_DRIVES = CollectionNames.DRIVES.value
_TOKENS = CollectionNames.PAGE_TOKENS.value


def _relation(doc: object, from_id: str, to_id: str) -> dict:
    return {**_doc(doc), "from_id": from_id, "from_collection": "users", "to_id": to_id, "to_collection": "apps"}


class SyncStateMixin(PgBase):
    # ---- a user's sync state on an app (the relation edge of the user to the app of that name) ----------------------
    async def get_user_sync_state(self, user_email: str, service_type: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_user_sync_state */ SELECT e.doc, e.from_id, e.to_id FROM graph.edges e
                JOIN graph.nodes u ON u.org_id = e.org_id AND u.coll = 'users' AND u.id = e.from_id AND u.doc->>'email' = $2
                JOIN graph.nodes a ON a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id AND a.doc->>'name' = $3
                WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.to_coll = 'apps' ORDER BY e.from_id, e.to_id LIMIT 1""",
                                                      org, user_email, service_type), transaction)
        except Exception as e:
            self.logger.error(f"❌ Get user sync state failed: {e}")
            return None
        return _relation(r["doc"], r["from_id"], r["to_id"]) if r else None

    async def update_user_sync_state(self, user_email: str, state: str, service_type: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        try:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.update_user_sync_state */ UPDATE graph.edges e SET doc = e.doc || $4::jsonb FROM graph.nodes u, graph.nodes a
                WHERE e.org_id = $1 AND e.coll = 'userAppRelation' AND e.from_coll = 'users' AND e.to_coll = 'apps'
                  AND u.org_id = e.org_id AND u.coll = 'users' AND u.id = e.from_id AND u.doc->>'email' = $2
                  AND a.org_id = e.org_id AND a.coll = 'apps' AND a.id = e.to_id AND a.doc->>'name' = $3
                RETURNING e.doc, e.from_id, e.to_id""",
                                                     org, user_email, service_type, _j({"syncState": state, "lastSyncUpdate": get_epoch_timestamp_in_ms()})), transaction, write=True)
        except Exception as e:
            self.logger.error(f"❌ Update user sync state failed: {e}")
            return None
        if not rows:
            return None
        first = min(rows, key=lambda r: (r["from_id"], r["to_id"]))
        return _relation(first["doc"], first["from_id"], first["to_id"])

    # ---- a drive's sync state ----------------------------------------------------------------------------------------
    async def get_drive_sync_state(self, drive_id: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        try:
            d = await self._one("/* pipeshub:graph.get_drive_sync_state */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'drives' AND id = $2", org, drive_id, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Get drive sync state failed: {e}")
            return None
        return _arango(d, _DRIVES) if d else None

    async def update_drive_sync_state(self, drive_id: str, state: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        try:
            r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.update_drive_sync_state */ UPDATE graph.nodes SET doc = doc || $3::jsonb
                WHERE org_id = $1 AND coll = 'drives' AND id = $2 RETURNING doc""",
                                                      org, drive_id, _j({"sync_state": state, "last_sync_update": get_epoch_timestamp_in_ms()})), transaction, write=True)
        except Exception as e:
            self.logger.error(f"❌ Update drive sync state failed: {e}")
            return None
        return _arango(_doc(r["doc"]), _DRIVES) if r else None

    # ---- page tokens -------------------------------------------------------------------------------------------------
    async def store_page_token(self, channel_id: str, resource_id: str, user_email: str, token: str, expiration: str | None = None,
                               transaction: str | None = None) -> dict | None:
        """One token per (user, channel, resource): storing again replaces it. The token is not logged."""
        current_org_id()
        try:
            key = f"page_token_{user_email}_{channel_id}_{resource_id}"
            await self.batch_upsert_nodes([{"id": key, "channelId": channel_id, "resourceId": resource_id, "userEmail": user_email, "token": token,
                                            "createdAtTimestamp": get_epoch_timestamp_in_ms(), "expiration": expiration}], _TOKENS, transaction=transaction)
            return await self.get_document(key, _TOKENS, transaction=transaction, raise_on_error=True)
        except Exception as e:
            self.logger.error(f"❌ Store page token failed: {type(e).__name__}")
            return None

    async def get_page_token_db(self, channel_id: str | None = None, resource_id: str | None = None, user_email: str | None = None,
                                transaction: str | None = None) -> dict | None:
        """The newest token matching the filters given (an empty filter is not a filter)."""
        org = current_org_id()
        try:
            d = await self._one("""/* pipeshub:graph.get_page_token_db */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'pageTokens'
                AND ($2::text IS NULL OR doc->>'channelId' = $2) AND ($3::text IS NULL OR doc->>'resourceId' = $3) AND ($4::text IS NULL OR doc->>'userEmail' = $4)
                ORDER BY doc->'createdAtTimestamp' DESC, id LIMIT 1""", org, channel_id or None, resource_id or None, user_email or None, transaction=transaction)
        except Exception as e:
            self.logger.error(f"❌ Get page token failed: {e}")
            return None
        return _arango(d, _TOKENS) if d else None

    # ---- indexing status ---------------------------------------------------------------------------------------------
    async def records_accept_indexed_copy(self) -> bool:
        return True      # documents hold any property

    async def backfill_indexed_copy_flags(self, batch_size: int = 10_000) -> int:
        """Mark COMPLETED records that have no ``hasIndexedCopy`` yet, in batches; the ambient org, or every org under a system context."""
        batch, total, errors = max(batch_size, 1), 0, []
        for org in await self._sweep_orgs():
            with self._sweep(org, errors):
                while True:
                    marked = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.backfill_indexed_copy_flags */ WITH b AS (SELECT n.id FROM graph.nodes n
                        WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'indexingStatus' = $2 AND n.doc->$4::text IS NULL ORDER BY n.id LIMIT $3 FOR UPDATE SKIP LOCKED),
                        u AS (UPDATE graph.nodes n SET doc = n.doc || jsonb_build_object($4::text, true) FROM b WHERE n.org_id = $1 AND n.coll = 'records' AND n.id = b.id RETURNING 1)
                        SELECT count(*) FROM u""", current_org_id(), ProgressStatus.COMPLETED.value, batch, HAS_INDEXED_COPY), None, write=True)
                    total += marked
                    if marked < batch:
                        break
        self._raise_failed(errors)
        return total

    async def reset_indexing_status_for_connector(self, connector_id: str, status: str, exclude_statuses: list[str] | None = None, transaction: str | None = None, *,
                                                  only_statuses: list[str] | None = None, reason: str | None = None,
                                                  visibility: RecordVisibility = RecordVisibility.ALL) -> None:
        """Set the status of the connector's records (those in ``only_statuses`` when given, not those in ``exclude_statuses``). A record keeps its
        previous indexed copy, so the flag is not touched. A failure raises: the rebuild that follows must not run on stale statuses."""
        org = current_org_id()
        if not connector_id:
            return
        excluded = [s for s in (exclude_statuses or []) if isinstance(s, str) and s]
        included = [s for s in (only_statuses or []) if isinstance(s, str) and s]
        if only_statuses is not None and not included:
            return
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.reset_indexing_status_for_connector */ UPDATE graph.nodes n
            SET doc = n.doc || jsonb_build_object('indexingStatus', $3::text) || CASE WHEN $7::text IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('reason', $7::text) END
            WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'connectorId' = $2
              AND (cardinality($4::text[]) = 0 OR NOT coalesce(n.doc->>'indexingStatus', '') = ANY($4::text[]))
              AND ($5::text[] IS NULL OR n.doc->>'indexingStatus' = ANY($5::text[]))
              AND ($6::int = 0 OR coalesce((n.doc->>'isDeleted')::boolean, false) = ($6::int = 2))""",
                                             org, connector_id, status, excluded, included or None, _vis(visibility), reason), transaction, write=True)

    async def update_queued_duplicates_status(self, record_id: str, new_indexing_status: str, virtual_record_id: str | None = None,
                                              transaction: str | None = None, reason: str | None = None) -> int:
        """Give the QUEUED live records of the same org with the reference record's md5 (and size, when it has one) its new status. The count
        updated; -1 on a failure; 0 when the reference record has no md5 or org, or its enrichment has not ended."""
        org = current_org_id()
        try:
            ref = await self._one("/* pipeshub:graph.queued_duplicates_ref */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = $2", org, record_id, transaction=transaction)
            if not ref or not ref.get("md5Checksum") or not ref.get(_ORG_FIELD):
                return 0      # a record without a checksum has nothing waiting on it; one without an org matches no one (fails closed, as dedup does)
            extraction_status = promoted_duplicate_extraction_status(new_indexing_status, _arango(ref, "records"))
            if extraction_status is None:
                return 0
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.queued_duplicates */ SELECT n.id FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = 'records' AND n.doc->>'md5Checksum' = $2 AND n.id <> $3 AND n.doc->>'indexingStatus' = 'QUEUED'
                  AND n.doc->>'orgId' = $4 AND coalesce((n.doc->>'isDeleted')::boolean, false) = false
                  AND ($5::jsonb IS NULL OR n.doc->'sizeInBytes' = $5::jsonb) ORDER BY n.id""",
                                                  org, ref["md5Checksum"], record_id, ref[_ORG_FIELD],
                                                  None if ref.get("sizeInBytes") is None else _j(ref["sizeInBytes"])), transaction)
            if not rows:
                return 0
            now = get_epoch_timestamp_in_ms()
            updates = [{"id": r["id"], "indexingStatus": new_indexing_status, **indexed_copy_fields(new_indexing_status), "lastIndexTimestamp": now,
                        "isDirty": False, "virtualRecordId": virtual_record_id, "extractionStatus": extraction_status, **({"reason": reason} if reason else {})}
                       for r in rows]
            if new_indexing_status in _RECONCILED_STATUSES:     # in the same batch as the promotion, so a crash cannot leave copies with nothing to trigger their taxonomy
                updates.append({"id": record_id, DUPLICATE_RECONCILE_PENDING_FIELD: True, DUPLICATE_RECONCILE_ATTEMPTS_FIELD: 0,
                                DUPLICATE_RECONCILE_DUE_AT_FIELD: now + DUPLICATE_RECONCILE_GRACE_MS})
            await self.batch_update_nodes(updates, "records", transaction)
            return len(rows)
        except Exception as e:
            self.logger.error(f"❌ Failed to update queued duplicates status: {e}")
            return -1
