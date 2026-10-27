from __future__ import annotations

import asyncio

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.modules.demo_data.access import excluded_demo_connector_ids
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.entities import (
    USER_EMAIL_PLACEHOLDER,
    Record,
    RecordType,
    TicketRecord,
    substitute_user_email,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.common.record_visibility import is_searchable_record
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.chat_helpers import (
    accessible_node_ids,
    accessible_node_ids_for_enrichment,
    collection_map,
    create_record_instance_from_dict,
    enrich_read_records_with_address_links,
    get_record,
    keep_record_text_inside_connection,
    live_record_ids,
)
from app.utils.logger import create_logger
from app.utils.not_indexed import record_metadata_lines

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = create_logger(__name__)


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: list[str] = Field(
        ...,
        description=(
            "List of Record IDs to fetch. Each Record ID is shown in the 'Record ID:' line "
            "of the record's context metadata in the conversation. "
            "Use ONLY the exact Record IDs from the context — do NOT invent, guess, or reuse example IDs. "
            "Pass ALL record IDs in a single call."
        )
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Brief explanation of why the full records are needed (e.g., 'query asks for complete details')."
    )


# Live Jira lines that name other issues, or the project above this one. Read
# with the connector's own credentials, so nothing checks them against a limit.
_LIVE_LINES_NAMING_OTHERS = (
    "* Parent:", "* Parent Link:", "* Linked Issues:", "* Sub-tasks:",
    "* Epic Link:", "* Epic Name:", "* Epic Status:", "* Project:",
)


async def _apply_live_ticket_context_metadata(
    record: dict[str, Any],
    *,
    config_service: ConfigurationService | None,
    graph_provider: IGraphDBProvider | None,
    frontend_url: str | None,
    connection_sources: Mapping[str, Iterable[str]] | None = None,
    org_id: str = "",
    user_id: str = "",
) -> None:
    """Upgrade ticket context_metadata with live Jira fields (fetch_full_record only).

    For a connected app whose limit does not cover the ticket's whole app, a
    live line that names another issue or the project is left out."""
    if not config_service:
        return

    record_type = record.get("record_type") or record.get("recordType")
    if record_type != RecordType.TICKET.value:
        return

    record_key = record.get("id") or record.get("_key")
    if not record_key:
        return

    graph_doc = None
    if graph_provider:
        collection = collection_map.get(record_type)
        if collection:
            try:
                graph_doc = await graph_provider.get_document(
                    document_key=record_key,
                    collection=collection,
                )
            except Exception as e:
                logger.warning(
                    "Could not fetch ticket graph doc for live context on %s: %s",
                    record_key,
                    str(e),
                )

    record_instance = create_record_instance_from_dict(record, graph_doc)
    if not isinstance(record_instance, TicketRecord):
        return

    async def readable_issue_ids(connector_id: str, issue_ids: set[str]) -> set[str]:
        """External ids of the issues this user may read; none if that cannot be checked."""
        if not graph_provider or not issue_ids:
            return set()
        # shortcut: one lookup per referenced issue, 16 at a time (no batch lookup by
        # external id exists); upgrade when the graph provider gains one.
        gate = asyncio.Semaphore(16)

        async def key_of(ext_id: str) -> str | None:
            async with gate:
                return await graph_provider.get_record_key_by_external_id(ext_id, connector_id)

        keys = await asyncio.gather(*(key_of(i) for i in issue_ids))
        key_to_ext = {k: i for i, k in zip(issue_ids, keys, strict=True) if k}
        ok = await accessible_node_ids_for_enrichment(graph_provider, set(key_to_ext), user_id, org_id)
        return {key_to_ext[k] for k in ok}

    live = await record_instance.to_llm_context_with_live_fields(
        frontend_url=frontend_url,
        config_service=config_service,
        readable_issue_ids=readable_issue_ids,
    )
    app_id = record.get("connector_id") or record.get("connectorId")
    if connection_sources is not None and app_id not in set(connection_sources.get("apps") or ()):
        # Every such line goes, the ones the record already held included: a
        # record read earlier, unlimited, may carry them in from the live fields.
        live = "\n".join(
            line for line in live.split("\n") if not line.lstrip().startswith(_LIVE_LINES_NAMING_OTHERS)
        )
    record["context_metadata"] = live


async def _enrich_sql_table_with_fk_relations(
    record: dict[str, Any],
    graph_provider: IGraphDBProvider,
    user_id: str = "",
    org_id: str = "",
    connection_sources: Mapping[str, Iterable[str]] | None = None,
) -> dict[str, Any]:
    """Add the tables a SQL_TABLE record is linked to by a foreign key.

    Returns a copy of ``record`` with ``fk_parent_record_ids`` and
    ``fk_child_record_ids``: one dict per live related table the user may
    access, with its record id, table name and columns. ``user_id`` and
    ``org_id`` say whose fetch this is; ``connection_sources`` is a connected
    app's limit, and a table outside it is not named.
    """
    from app.config.constants.arangodb import RecordRelations

    record_id = record.get("id") or record.get("record_id")
    if not record_id:
        logger.debug("FK enrichment skipped: no record_id found in record")
        return record

    record_name = record.get("record_name") or record.get("recordName") or ""
    fk_child_ids: list[dict[str, Any]] = []
    fk_parent_ids: list[dict[str, Any]] = []

    try:
        fk_child_ids = list(await graph_provider.get_child_record_ids_by_relation_type(
            record_id, RecordRelations.FOREIGN_KEY.value
        ))
    except Exception as e:
        logger.warning("Could not fetch child record IDs for %s: %s", record_id, str(e))

    try:
        fk_parent_ids = list(await graph_provider.get_parent_record_ids_by_relation_type(
            record_id, RecordRelations.FOREIGN_KEY.value
        ))
    except Exception as e:
        logger.warning("Could not fetch parent record IDs for %s: %s", record_id, str(e))

    # A related table's id is shown only to a user who may open it, and only while
    # it is live: the trash keeps a dropped table's node and FK edges until the
    # purge, so the edge reads still return it.
    def fk_id(entry: dict[str, Any] | str) -> str | None:
        return entry.get("record_id") if isinstance(entry, dict) else entry

    related = {fk_id(e) for e in fk_child_ids + fk_parent_ids} - {None}
    try:
        allowed = await accessible_node_ids(graph_provider, related, user_id, org_id)
    except PermissionVerificationUnavailableError:
        # The related tables are extra context: the record itself is still shown.
        logger.warning("Could not check access to the tables related to %s", record_id, exc_info=True)
        allowed = set()
    allowed &= await live_record_ids(graph_provider, allowed, org_id)
    if connection_sources is not None and allowed:
        from app.modules.retrieval.selection_scope import outside_connection

        try:
            allowed -= await outside_connection(graph_provider, user_id, org_id, allowed, connection_sources)
        except Exception:
            logger.warning("Could not check the limit for the tables related to %s", record_id, exc_info=True)
            allowed = set()
    fk_child_ids = [e for e in fk_child_ids if fk_id(e) in allowed]
    fk_parent_ids = [e for e in fk_parent_ids if fk_id(e) in allowed]

    # Add FK relations to the record (non-destructive - creates a copy)
    enriched_record = dict(record)
    enriched_record["fk_parent_record_ids"] = fk_parent_ids
    enriched_record["fk_child_record_ids"] = fk_child_ids

    if fk_parent_ids or fk_child_ids:
        logger.info(
            "FK enrichment: enriched SQL_TABLE %s with %d parent and %d child FK relations",
            record_name or record_id, len(fk_parent_ids), len(fk_child_ids)
        )

    return enriched_record


# Concurrency for resolving record ids. Each miss is an ACL check, a graph
# read and a blob download; a handful in flight cuts the wall clock of a
# multi-record fetch without hammering the stores.
_RESOLVE_CONCURRENCY = 5

# Why an id produced no content. Deliberately coarse where it has to be:
# "no access" and "does not exist" share one reason, because distinguishing
# them turns this tool into an existence oracle for records the caller may
# not read. The other two leak nothing and are worth telling the model.
UNAVAILABLE = "not_available"
NOT_INDEXED_YET = "not_indexed_yet"
STORAGE_ERROR = "storage_error"


class _RecordResolver:
    """Turns one record id into a record, or into the reason there isn't one.

    Holds the per-call collaborators — the blob client and the frontend URL
    that used to be rebuilt inside the loop for every record.
    """

    def __init__(
        self,
        *,
        virtual_record_id_to_result: dict[str, Any],
        graph_provider: IGraphDBProvider | None,
        blob_store: BlobStorage | None,
        config_service: ConfigurationService | None,
        org_id: str | None,
        user_id: str | None,
        frontend_url: str | None,
        accessible: set[str] | None = None,
        connection_sources: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self._map = virtual_record_id_to_result
        self._connection_sources = connection_sources
        # The batch check's answer for this call's ids. None only without a graph
        # provider: nothing can check then, and only map entries are served.
        self._accessible = accessible
        self._graph_provider = graph_provider
        self._blob_store = blob_store
        self._config_service = config_service
        self._org_id = org_id
        self._user_id = user_id
        self._frontend_url = frontend_url
        self._endpoints_read = False
        self._excluded: asyncio.Future[frozenset[str]] | None = None
        self._viewer_email: asyncio.Future[str | None] | None = None
        # Records the caller may read that exist but have no indexed content,
        # by id: their status and metadata, so "not indexed" can name them.
        self.not_indexed: dict[str, dict[str, str]] = {}

    async def _excluded_apps(self) -> frozenset[str]:
        """The Acme Corp demo, when this person switched it off; read once per fetch.

        Records resolve concurrently, so every caller waits on the same lookup
        rather than reading a placeholder while it is still running.
        """
        if self._excluded is None:
            self._excluded = asyncio.ensure_future(self._read_excluded())
        return await self._excluded

    async def _read_excluded(self) -> frozenset[str]:
        if not (self._config_service and self._graph_provider and self._org_id and self._user_id):
            return frozenset()
        try:
            return await excluded_demo_connector_ids(
                self._graph_provider, self._config_service, self._org_id, self._user_id
            )
        except Exception:
            logger.warning("Demo data setting unreadable for %s", self._user_id, exc_info=True)
            return frozenset()

    async def _with_viewer_link(self, graph_record: dict[str, Any]) -> dict[str, Any]:
        """``graph_record`` with a shared mailbox's link opened as this viewer.

        Search fills the viewer's email into a Gmail link before handing records
        on; a record read here comes straight from the graph and still holds the
        placeholder, which reached citations as ``authuser={user.email}``.
        """
        web_url = graph_record.get("webUrl")
        if not web_url or USER_EMAIL_PLACEHOLDER not in web_url:
            return graph_record
        if self._viewer_email is None:
            self._viewer_email = asyncio.ensure_future(self._read_viewer_email())
        resolved = substitute_user_email(web_url, await self._viewer_email, graph_record.get("connectorName"))
        return graph_record if resolved == web_url else {**graph_record, "webUrl": resolved}

    async def _read_viewer_email(self) -> str | None:
        if not (self._graph_provider and self._user_id):
            return None
        try:
            user = await self._graph_provider.get_user_by_user_id(self._user_id)
        except Exception:
            logger.warning("User unreadable for %s; mail links keep the placeholder", self._user_id, exc_info=True)
            return None
        return user.get("email") if isinstance(user, dict) else None

    async def resolve(self, record_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
        # The map is not only retrieval's: attachment and FK enrichment write
        # to it too, so an entry proves nothing where the batch check has run.
        if self._accessible is not None and record_id not in self._accessible:
            return record_id, None, UNAVAILABLE
        cached = self._from_map(record_id)
        if cached is not None:
            if "record_name" in cached:
                return record_id, await self._enrich(cached), None
            # Raw graph metadata (e.g. from pattern match) — fetch blob content.
            # ACL was already verified when the entry was added to the map.
            if not cached.get("virtualRecordId"):
                cached["virtualRecordId"] = cached.get("virtual_record_id")
            try:
                record = await self._download(await self._with_viewer_link(cached))
            except Exception:
                logger.warning("Blob read failed for map entry %s", record_id, exc_info=True)
                return record_id, None, STORAGE_ERROR
            if record is None:
                return record_id, None, UNAVAILABLE
            return record_id, await self._enrich(record), None

        # Without a user to check against, an id is never served.
        if not (self._org_id and self._graph_provider and self._user_id):
            return record_id, None, UNAVAILABLE

        try:
            graph_record = await self._graph_provider.get_document(
                document_key=record_id, collection=CollectionNames.RECORDS.value,
            )
        except Exception:
            logger.warning("Graph read failed for %s", record_id, exc_info=True)
            return record_id, None, STORAGE_ERROR

        if not graph_record:
            return record_id, None, UNAVAILABLE
        graph_record = await self._with_viewer_link(graph_record)
        # Search leaves switched-off demo data out; opening it by id must too.
        if graph_record.get("connectorId") in await self._excluded_apps():
            return record_id, None, UNAVAILABLE
        if not is_searchable_record(graph_record):
            # Past the access check, so describing it reveals nothing.
            self.not_indexed[record_id] = {
                "status": graph_record.get("indexingStatus") or "unknown",
                "context": await self._metadata(record_id, graph_record),
            }
            return record_id, None, NOT_INDEXED_YET

        try:
            record = await self._download(graph_record)
        except Exception:
            logger.warning("Blob read failed for %s", record_id, exc_info=True)
            return record_id, None, STORAGE_ERROR

        if record is None:
            return record_id, None, UNAVAILABLE
        return record_id, await self._enrich(record), None

    async def _metadata(self, record_id: str, graph_record: dict[str, Any]) -> str:
        """Name, type, link and dates for a record with no content to read.

        The typed record when it loads, so a ticket or pull request carries its
        own fields (state, number, assignee); the bare document otherwise.
        """
        try:
            await self._ensure_frontend_url(await self._ensure_blob_store())
        except Exception:
            logger.debug("Could not resolve the frontend URL", exc_info=True)
        try:
            typed = await self._graph_provider.get_record_by_id(record_id)
            if isinstance(typed, Record) and typed.id == record_id:
                return typed.to_llm_context(frontend_url=self._frontend_url)
        except Exception:
            logger.debug("Typed record unavailable for %s", record_id, exc_info=True)
        return "\n".join(record_metadata_lines({"_key": record_id, **graph_record}, self._frontend_url))

    def _from_map(self, record_id: str) -> dict[str, Any] | None:
        for vrid, record in self._map.items():
            if record is not None and record.get("id") == record_id:
                record["virtual_record_id"] = vrid
                return record
        return None

    async def _download(self, graph_record: dict[str, Any]) -> dict[str, Any] | None:
        vrid = graph_record.get("virtualRecordId")
        if not vrid:
            return None
        blob_store = await self._ensure_blob_store()
        await self._ensure_frontend_url(blob_store)
        await get_record(
            vrid, self._map, blob_store, self._org_id, {vrid: graph_record},
            self._graph_provider, self._frontend_url,
        )
        record = self._map.get(vrid)
        if record:
            record["virtual_record_id"] = vrid
        return record

    async def _ensure_blob_store(self) -> BlobStorage:
        if self._blob_store is None:
            self._blob_store = BlobStorage(
                logger=logger,
                config_service=self._graph_provider.config_service,
                graph_provider=self._graph_provider,
            )
        return self._blob_store

    async def _ensure_frontend_url(self, blob_store: BlobStorage) -> None:
        if self._frontend_url is not None or self._endpoints_read:
            return
        self._endpoints_read = True
        try:
            endpoints = await blob_store.config_service.get_config(
                config_node_constants.ENDPOINTS.value, default={},
            )
            if isinstance(endpoints, dict):
                self._frontend_url = endpoints.get("frontend", {}).get("publicEndpoint")
        except Exception:
            logger.debug("Could not read the frontend endpoint", exc_info=True)

    async def _enrich(self, record: dict[str, Any]) -> dict[str, Any]:
        await _apply_live_ticket_context_metadata(
            record,
            config_service=self._config_service,
            graph_provider=self._graph_provider,
            frontend_url=self._frontend_url,
            connection_sources=self._connection_sources,
            org_id=self._org_id or "",
            user_id=self._user_id or "",
        )
        record_type = record.get("record_type") or record.get("recordType")
        if record_type == "SQL_TABLE" and self._graph_provider:
            return await _enrich_sql_table_with_fk_relations(
                record, self._graph_provider, self._user_id or "", self._org_id or "",
                connection_sources=self._connection_sources,
            )
        return record


async def _fetch_multiple_records_impl(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any],
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
    connection_sources: Mapping[str, Iterable[str]] | None = None,
    accessible: set[str] | None = None,
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.

    ``connection_sources`` is a connected app's limit: the tables and records
    named beside a record (foreign keys, address links) keep to it.
    For SQL_TABLE records, also enriches with FK parent/child record IDs.

    Every id is first checked in one batch (map entries included); an id the
    user may not read is skipped. If a record_id is not found in the map,
    attempts to:
    1. Fetch the Record from graph_provider to get virtual_record_id
    2. Fetch the record content from blob_store
    3. Enrich with FK relations if SQL_TABLE

    Without `user_id` the check cannot run, so the id-resolution path is
    skipped entirely rather than served unchecked. A caller that already ran
    the check passes its answer as `accessible`, so it is not asked twice.

    Returns:
    {
      "ok": true,
      "records": [...],
      "record_count": N,
      "not_available": {"id": "This record is not available"},   # fetched or map-keyed but missing
      "invalid_record_ids": {"id": "Invalid record ID"},          # malformed / non-UUID IDs
      "not_indexed_records": {"id": {"status": ..., "context": ...}}  # readable by the caller,
                                                                     # but no indexed content
    }
    """
    found_records = []
    not_available_ids = []
    unavailable_reasons: dict[str, str] = {}

    # Get frontend_url from the first non-None record already in the map
    frontend_url = next(
        (r["frontend_url"] for r in virtual_record_id_to_result.values()
         if r is not None and r.get("frontend_url")),
        None,
    )

    config_service = graph_provider.config_service if graph_provider else None

    if accessible is None and graph_provider is not None:
        try:
            accessible = await accessible_node_ids(
                graph_provider, set(record_ids), user_id or "", org_id or "",
            )
        except Exception:
            logger.warning("Access check failed for %s", record_ids, exc_info=True)
            return {
                "ok": False,
                "error": "None of the requested records were available.",
                "not_available_ids": list(record_ids),
                "unavailable_reasons": dict.fromkeys(record_ids, STORAGE_ERROR),
            }

    # Built once, not once per record: this used to be constructed inside the
    # loop, along with a fresh ENDPOINTS read.
    resolver = _RecordResolver(
        virtual_record_id_to_result=virtual_record_id_to_result,
        graph_provider=graph_provider,
        blob_store=blob_store,
        config_service=config_service,
        org_id=org_id,
        user_id=user_id,
        frontend_url=frontend_url,
        accessible=accessible,
        connection_sources=connection_sources,
    )

    # Records resolve concurrently -- each miss costs an ACL check, a graph
    # read and a blob download, and ten ids were thirty sequential round
    # trips. The map is shared with retrieval, so nothing writes to it from a
    # worker: results come back in order and are applied on this task.
    semaphore = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

    async def _resolve(record_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
        async with semaphore:
            return await resolver.resolve(record_id)

    resolutions = await asyncio.gather(*(_resolve(rid) for rid in record_ids))
    # Additive: callers that know nothing of it still get the reasons above.
    not_indexed = {rid: resolver.not_indexed[rid] for rid in record_ids if rid in resolver.not_indexed}
    if not_indexed and connection_sources is not None:
        described = [{"id": rid, "context_metadata": entry.get("context")} for rid, entry in not_indexed.items()]
        await keep_record_text_inside_connection(
            described, graph_provider, user_id or "", org_id or "", connection_sources,
        )
        not_indexed = {
            rec["id"]: {**not_indexed[rec["id"]], "context": rec["context_metadata"]} for rec in described
        }

    for record_id, record, reason in resolutions:
        if record is not None:
            found_records.append(record)
        else:
            not_available_ids.append(record_id)
            unavailable_reasons[record_id] = reason or UNAVAILABLE

    result: dict[str, Any] = {}
    result["ok"] = False

    if found_records:
        # What names each record and what it names, with their status: read by
        # id, a record does not pass through the search pipeline that adds them.
        await enrich_read_records_with_address_links(
            found_records, graph_provider, org_id, user_id=user_id, connection_sources=connection_sources,
        )
        await keep_record_text_inside_connection(
            found_records, graph_provider, user_id or "", org_id or "", connection_sources,
        )
        result["ok"] = True
        result["records"] = found_records
        result["record_count"] = len(found_records)
    else:
        # Keep the per-id detail: "none were found" alone leaves the model
        # unable to tell a typo from a record that is still indexing.
        return {
            "ok": False,
            "error": (
                "No record IDs were provided." if not record_ids
                # Each one exists; saying "not available" would say otherwise.
                else "None of the requested records has indexed content to read yet."
                if not_available_ids and set(not_available_ids) <= set(not_indexed)
                else "None of the requested records were available."
            ),
            "not_available_ids": not_available_ids,
            "unavailable_reasons": unavailable_reasons,
            "not_indexed_records": not_indexed,
        }

    result["not_available_ids"] = not_available_ids
    result["unavailable_reasons"] = unavailable_reasons
    result["not_indexed_records"] = not_indexed

    return result


def create_fetch_full_record_tool(
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
    user_id: str | None = None,
    connection_sources: Mapping[str, Iterable[str]] | None = None,
) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    
    Args:
        virtual_record_id_to_result: Mapping of virtual record IDs to record data
        graph_provider: Optional GraphDB service for enriching SQL_TABLE records
                        with FK parent/child relations and resolving record IDs
        blob_store: Optional blob storage for fetching records not in the map
        org_id: Optional organization ID for blob storage lookups
        user_id: Requesting user, required to resolve a record ID that is not
                 already in the (ACL-filtered) map — see
                 `_fetch_multiple_records_impl`
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: list[str], reason: str = "Fetching full record content for comprehensive answer") -> dict[str, Any]:
        """Read one or more records end to end. Search gives you a few matching blocks per record; lookup_record/navigate/list_files give you an ID and metadata and no content at all; this gives you everything.

        Call this BEFORE answering whenever what you currently hold is
        incomplete for what the question needs:

        - The answer is a property of the whole document — a summary or
          overview, its risks/gaps/obligations/key points, a review or
          assessment, a comparison of documents, whether it mentions something
          anywhere, anything asking for all of something. A handful of blocks
          CANNOT support that answer, however relevant they look, because the
          parts you were not given are exactly what you would be implying are
          unimportant.
        - You hold no passage at all — the record came from lookup, navigation
          or listing, so you have its ID and metadata and nothing it says.
          Never infer content from a title.

        (Skip only when the exact fact needed — a date, a name, a number, a
        status, one clause — is already visible in a block you hold, or
        metadata alone settles the question outright, e.g. a ticket's status
        or assignee.)

        Those are illustrations, not a checklist — apply the same reasoning to
        whatever was actually asked.

        Pass every record_id you need in ONE call, taken from a candidate list, a
        'Record ID' field, a record_id= or node_id= shown by navigation — never invent IDs.

        For SQL_TABLE records, also returns fk_parent_record_ids and fk_child_record_ids
        which can be used to fetch related tables for nested FK relationships.

        Args:
            record_ids: List of Record IDs to fetch — use the exact Record ID values from the context
            reason: Brief explanation of why the full records are needed

        Returns: Complete content of the records or {"ok": false, "error": "..."}.
        """
        logger.info(
            "fetch_full_record called: record_ids=%s, reason=%r",
            record_ids,
            reason,
        )
        try:
            return await _fetch_multiple_records_impl(
                record_ids,
                virtual_record_id_to_result,
                org_id=org_id,
                graph_provider=graph_provider,
                blob_store=blob_store,
                user_id=user_id,
                connection_sources=connection_sources,
            )
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


