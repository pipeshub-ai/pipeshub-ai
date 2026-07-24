"""``lookup_record`` tool — the agent's "search bar".

Resolves any external reference to a record — a pasted web URL, a Jira-style
issue key (``PA-1787``), or a bare external system ID — to the matching
internal record, permission-checked before anything is returned.

Resolution order (see ``_resolve_candidates``):
1. ``https?://`` URL     -> canonical-ID extraction (``url_resolver``) against
                            the indexed lookup for that ID kind; falls back to
                            ``get_record_by_weburl`` (raw, then normalized).
2. ``KEY-123``-shaped    -> issue-key lookup across the org's Jira-family connectors.
3. anything else         -> external-ID lookup across the org's connectors
                            (optionally narrowed by ``connector_name``).

Every resolver call is scoped to the org's own connectors
(``resolve_org_connector_ids``) and every surviving candidate is
permission-gated (``check_record_access``) before it is returned — an
identifier that resolves to a record the caller can't see is indistinguishable
from one that doesn't exist.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.constants.arangodb import ProgressStatus
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.chat_helpers import get_record
from app.utils.logger import create_logger
from app.utils.record_tool_helpers import (
    NOT_FOUND_ERROR,
    NodeRefMapper,
    check_record_access,
    register_tool_describer,
    resolve_org_connector_ids,
    truncate_name,
)
from app.utils.url_resolver import CanonicalRef, normalize_weburl, resolve_canonical_ref

logger = create_logger(__name__)

_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}-\d+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_MAX_CANDIDATES_CHECKED = 10


class LookupRecordArgs(BaseModel):
    """Args for the ``lookup_record`` tool."""

    identifiers: list[str] = Field(
        description=(
            "One or more external references to resolve — pass ALL of them in a SINGLE call.\n"
            "Examples of valid identifiers:\n"
            "- Jira issue URL: https://pipeshub.atlassian.net/browse/PA-1787\n"
            "- Confluence page URL: https://pipeshub.atlassian.net/wiki/spaces/SD/pages/450625553/Agent+Loop+Implementation\n"
            "- Google Drive/Docs URL: https://docs.google.com/document/d/1AbC.../edit\n"
            "- Slack message link: https://acme.slack.com/archives/C0123/p1720000000000100\n"
            "- Issue key: PA-1787\n"
            "- Bare external system ID: 450625553\n"
            'Example: identifiers=["PA-1095", "PA-1151", "PA-1143"]'
        )
    )
    connector_name: str | None = Field(
        default=None,
        description="Optional hint to narrow the search: 'JIRA', 'DRIVE', 'CONFLUENCE', 'SLACK', ...",
    )
    reason: str = Field(
        default="Resolving an external reference found in context",
        description="Brief explanation of why this lookup is needed.",
    )


def _describe_lookup_record(args: dict[str, Any]) -> str:
    ids = args.get("identifiers") or []
    if not ids:
        return "Looking up records…"
    connector = args.get("connector_name")
    if len(ids) == 1:
        display = ids[0] if len(ids[0]) <= 60 else ids[0][:57] + "..."
        return f"Looking up {display} in {connector}…" if connector else f"Looking up {display}…"
    display = ", ".join(ids[:5])
    if len(ids) > 5:
        display += f" (+{len(ids) - 5} more)"
    return f"Looking up {display} in {connector}…" if connector else f"Looking up {display}…"


register_tool_describer("lookup_record", _describe_lookup_record)


def _dedupe_records(records: list[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for record in records:
        record_id = getattr(record, "id", None)
        if record_id and record_id not in seen:
            seen.add(record_id)
            unique.append(record)
    return unique


async def _lookup_via_canonical_ref(
    ref: CanonicalRef,
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_name: str | None,
) -> list[Any]:
    connectors = await resolve_org_connector_ids(graph_provider, org_id, connector_name or ref.connector_family)
    connector_ids = [c.get("_key") for c in connectors if c.get("_key")]
    if not connector_ids:
        return []

    async def _lookup_one(connector_id: str) -> Any:
        try:
            if ref.kind == "issue_key":
                return await graph_provider.get_record_by_issue_key(connector_id, ref.value)
            if ref.kind == "external_id":
                return await graph_provider.get_record_by_external_id(connector_id, ref.value)
            if ref.kind == "slack_ts":
                channel_id = (ref.extra or {}).get("channel_id", "")
                return await graph_provider.find_slack_burst_record_by_ts(connector_id, channel_id, ref.value)
        except Exception:
            logger.debug("lookup_record: connector %s lookup failed for ref=%s", connector_id, ref, exc_info=True)
        return None

    results = await asyncio.gather(*[_lookup_one(cid) for cid in connector_ids])
    return [r for r in results if r is not None]


async def _lookup_via_weburl(url: str, graph_provider: IGraphDBProvider, org_id: str) -> list[Any]:
    # Raw first (stored webUrls aren't normalized), then normalized (catches
    # copy-pasted variants with tracking params/fragments).
    for candidate in dict.fromkeys([url, normalize_weburl(url)]):
        try:
            record = await graph_provider.get_record_by_weburl(candidate, org_id)
        except Exception:
            logger.debug("lookup_record: weburl lookup failed for %s", candidate, exc_info=True)
            record = None
        if record:
            return [record]
    return []


async def _lookup_via_issue_key(
    identifier: str,
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_name: str | None,
) -> list[Any]:
    ref = CanonicalRef(kind="issue_key", value=identifier.upper(), connector_family="JIRA")
    return await _lookup_via_canonical_ref(ref, graph_provider, org_id, connector_name)


async def _lookup_via_external_id(
    identifier: str,
    graph_provider: IGraphDBProvider,
    org_id: str,
    connector_name: str | None,
) -> list[Any]:
    connectors = await resolve_org_connector_ids(graph_provider, org_id, connector_name)
    connector_ids = [c.get("_key") for c in connectors if c.get("_key")]
    if not connector_ids:
        return []
    results = await asyncio.gather(
        *[graph_provider.get_record_by_external_id(cid, identifier) for cid in connector_ids],
        return_exceptions=True,
    )
    return [r for r in results if r is not None and not isinstance(r, BaseException)]


async def _resolve_candidates(
    identifier: str,
    connector_name: str | None,
    graph_provider: IGraphDBProvider,
    org_id: str,
) -> list[Any]:
    identifier = identifier.strip()

    if _URL_RE.match(identifier):
        canonical = resolve_canonical_ref(identifier)
        if canonical:
            matches = await _lookup_via_canonical_ref(canonical, graph_provider, org_id, connector_name)
            if matches:
                return _dedupe_records(matches)
        return _dedupe_records(await _lookup_via_weburl(identifier, graph_provider, org_id))

    if _ISSUE_KEY_RE.match(identifier):
        return _dedupe_records(await _lookup_via_issue_key(identifier, graph_provider, org_id, connector_name))

    return _dedupe_records(await _lookup_via_external_id(identifier, graph_provider, org_id, connector_name))


def _record_summary(record_doc: dict[str, Any], ref: str) -> dict[str, Any]:
    return {
        "ref": ref,
        "id": record_doc.get("id") or record_doc.get("_key"),
        "name": truncate_name(record_doc.get("recordName")),
        "recordType": record_doc.get("recordType"),
        "connector": record_doc.get("connectorName"),
        "webUrl": None if record_doc.get("hideWeburl") else record_doc.get("webUrl"),
        "indexingStatus": record_doc.get("indexingStatus"),
    }


def _render_single_markdown(summary: dict[str, Any]) -> str:
    lines = [f"Found: {summary['ref']} [{summary.get('recordType') or 'RECORD'}] {summary.get('name')}"]
    if summary.get("connector"):
        lines.append(f"Connector: {summary['connector']}")
    if summary.get("webUrl"):
        lines.append(f"URL: {summary['webUrl']}")
    status = summary.get("indexingStatus")
    if status and status != ProgressStatus.COMPLETED.value:
        lines.append(
            f"Note: not yet fully indexed (status: {status}) — "
            f"use navigate(node_id=\"{summary['ref']}\") to see what's available."
        )
    lines.append(
        f"\nMore: navigate(node_id=\"{summary['ref']}\") · "
        f"Read: fetch_full_record(record_ids=[\"{summary['id']}\"])"
    )
    return "\n".join(lines)


def _render_ambiguous_markdown(candidates: list[dict[str, Any]]) -> str:
    lines = [f"Found {len(candidates)} matching records — they need disambiguation:"]
    for c in candidates:
        detail = f" — {c['connector']}" if c.get("connector") else ""
        lines.append(f"- {c['ref']} [{c.get('recordType') or 'RECORD'}] {c.get('name')}{detail}")
    lines.append('\nUse navigate(node_id="<ref>") to inspect one, or fetch_full_record with its id to read it.')
    return "\n".join(lines)


def create_lookup_record_tool(
    *,
    graph_provider: IGraphDBProvider | None,
    org_id: str | None,
    user_id: str | None,
    blob_store: BlobStorage | None = None,
    virtual_record_id_to_result: dict[str, Any] | None = None,
    node_ref_mapper: NodeRefMapper | None = None,
) -> Callable:
    """Factory that creates the ``lookup_record`` tool with runtime dependencies injected."""
    logger.info(
        "[TOOL-REG] create_lookup_record_tool: org_id=%s user_id=%s graph_provider=%s blob_store=%s",
        org_id, user_id, bool(graph_provider), bool(blob_store),
    )
    ref_mapper = node_ref_mapper if node_ref_mapper is not None else NodeRefMapper()
    vrid_map = virtual_record_id_to_result if virtual_record_id_to_result is not None else {}

    async def _resolve_single(
        identifier: str,
        connector_name: str | None,
    ) -> dict[str, Any]:
        """Resolve one identifier end-to-end (candidates -> access check -> blob load)."""
        identifier = (identifier or "").strip()
        if not identifier:
            return {"ok": False, "identifier": identifier, "error": "empty identifier"}

        try:
            candidates = await _resolve_candidates(identifier, connector_name, graph_provider, org_id)
        except Exception as e:
            logger.exception("lookup_record: resolution failed for identifier=%s", identifier)
            return {"ok": False, "identifier": identifier, "error": f"Lookup failed: {e}"}

        if not candidates:
            return {"ok": False, "identifier": identifier, "error": NOT_FOUND_ERROR}

        accessible_docs: list[dict[str, Any]] = []
        for record in candidates[:_MAX_CANDIDATES_CHECKED]:
            record_id = getattr(record, "id", None)
            if not record_id:
                continue
            doc = await check_record_access(graph_provider, user_id, org_id, record_id)
            if doc:
                accessible_docs.append(doc)

        if not accessible_docs:
            return {"ok": False, "identifier": identifier, "error": NOT_FOUND_ERROR}

        if len(accessible_docs) > 1:
            summaries = [
                _record_summary(doc, ref_mapper.get_or_create_ref(doc.get("id") or doc.get("_key")))
                for doc in accessible_docs
            ]
            return {
                "ok": True,
                "identifier": identifier,
                "multiple_matches": True,
                "candidates": summaries,
                "content": [{"type": "text", "text": _render_ambiguous_markdown(summaries)}],
                "result_type": "content",
            }

        doc = accessible_docs[0]
        record_id = doc.get("id") or doc.get("_key")
        node_ref = ref_mapper.get_or_create_ref(record_id)
        summary = _record_summary(doc, node_ref)

        if doc.get("indexingStatus") == ProgressStatus.COMPLETED.value and blob_store and record_id:
            virtual_record_id = doc.get("virtualRecordId")
            if virtual_record_id:
                try:
                    await get_record(
                        virtual_record_id,
                        vrid_map,
                        blob_store,
                        org_id,
                        {virtual_record_id: doc},
                        graph_provider,
                        None,
                    )
                except Exception:
                    logger.exception("lookup_record: get_record failed for vrid=%s", virtual_record_id)
                blob_record = vrid_map.get(virtual_record_id)
                if blob_record:
                    blob_record["virtual_record_id"] = virtual_record_id
                    return {
                        "ok": True,
                        "identifier": identifier,
                        "records": [blob_record],
                        "record_count": 1,
                        "result_type": "records",
                        "record_info": summary,
                        "navigation": {"node": summary},
                        "summary": f"Resolved {identifier} → {summary.get('name')}",
                    }

        return {
            "ok": True,
            "identifier": identifier,
            "record_info": summary,
            "navigation": {"node": summary},
            "content": [{"type": "text", "text": _render_single_markdown(summary)}],
            "result_type": "content",
            "summary": f"Resolved {identifier} → {summary.get('name')}",
        }

    @tool("lookup_record", args_schema=LookupRecordArgs)
    async def lookup_record_tool(
        identifiers: list[str],
        connector_name: str | None = None,
        reason: str = "Resolving an external reference found in context",
    ) -> dict[str, Any]:
        """Resolve one or more external references — URLs, issue keys like PA-1787, or bare
        external system IDs — to the matching records. Pass ALL identifiers in a SINGLE call
        rather than calling once per identifier: identifiers=["PA-1095", "PA-1151", "PA-1143"].

        This is the "search bar": paste anything you found referenced in a document, ticket,
        or message, and get back the matching records.

        Examples of identifiers this accepts:
        - https://pipeshub.atlassian.net/browse/PA-1787 (Jira)
        - https://pipeshub.atlassian.net/wiki/spaces/SD/pages/450625553/Agent+Loop+Implementation (Confluence)
        - https://docs.google.com/document/d/1AbC.../edit (Google Drive/Docs)
        - https://acme.slack.com/archives/C0123/p1720000000000100 (Slack)
        - PA-1787 (bare issue key)
        - 450625553 (bare external system ID)

        For a single identifier, returns its resolved record directly. For multiple, all are
        resolved concurrently and results are batched.

        Args:
            identifiers: One or more URLs, issue keys, or external IDs to resolve.
            connector_name: Optional hint narrowing the search (e.g. 'JIRA', 'DRIVE').
            reason: Brief explanation of why this lookup is needed.

        Returns: {"ok": true, "results": [...]} with per-identifier results.
        """
        logger.info(
            "lookup_record called: identifiers=%r connector_name=%r reason=%r",
            identifiers, connector_name, reason,
        )

        if not (graph_provider and org_id and user_id):
            return {"ok": False, "error": "Lookup is not available in this context."}

        if not identifiers:
            return {"ok": False, "error": "identifiers list is required and must not be empty."}

        # Cap to prevent abuse
        capped = identifiers[:20]

        # Resolve all concurrently
        results = await asyncio.gather(
            *[_resolve_single(ident, connector_name) for ident in capped],
        )

        # Single-identifier shortcut: return the result directly (backward-compatible shape)
        if len(capped) == 1:
            return results[0]

        # Batch response: merge records for the tool handler pipeline
        all_records: list[dict[str, Any]] = []
        all_summaries: list[dict[str, Any]] = []
        md_lines: list[str] = []
        ok_count = 0

        for r in results:
            ident = r.get("identifier", "?")
            if r.get("ok"):
                ok_count += 1
                if r.get("records"):
                    all_records.extend(r["records"])
                summary = r.get("record_info")
                if summary:
                    all_summaries.append(summary)
                    md_lines.append(
                        f"- {summary.get('ref', '')} [{summary.get('recordType') or 'RECORD'}] "
                        f"{summary.get('name', ident)}"
                    )
                elif r.get("candidates"):
                    md_lines.append(f"- {ident}: {len(r['candidates'])} matches — needs disambiguation")
            else:
                md_lines.append(f"- {ident}: not found")

        header = f"Resolved {ok_count}/{len(capped)} identifiers:"
        md = header + "\n" + "\n".join(md_lines)

        batch_result: dict[str, Any] = {
            "ok": ok_count > 0,
            "results": list(results),
            "content": [{"type": "text", "text": md}],
            "result_type": "records" if all_records else "content",
            "summary": f"Resolved {ok_count}/{len(capped)} identifiers",
        }
        if all_records:
            batch_result["records"] = all_records
            batch_result["record_count"] = len(all_records)
        return batch_result

    return lookup_record_tool
