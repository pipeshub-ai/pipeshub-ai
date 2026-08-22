"""Agent facade for resolving `attachment_record_ids` to bytes.

Every toolset that accepts file attachments calls `resolve_attachments`
with the raw list of ref strings from the LLM and receives an
`AttachmentBundle` containing `ResolvedRecordContent` objects plus any
per-record failures.

`attachment_record_ids_parameter` produces the single canonical
`ToolParameter` that the LLM sees across all toolsets — one name, one
description, so the model does not need to learn toolset-specific variants.

`emit_attachment_audit` emits a structured JSON-line audit entry so
operators can trace every file sent through agent toolsets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
from app.agents.actions.util.blob_staging import DEFAULT_MAX_STAGE_BYTES
from app.services.artifact_registry.models import Actor
from app.services.record_content import (
    AttachmentFailure,
    RecordAccessDeniedError,
    RecordContentError,
    RecordContentResolver,
    RecordNotFoundError,
    RecordTooLargeError,
    ResolvedRecordContent,
)

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger("pipeshub.attachment_audit")

__all__ = [
    "attachment_record_ids_parameter",
    "resolve_attachments",
    "emit_attachment_audit",
    "AttachmentBundle",
    "MAX_ATTACHMENTS_PER_CALL",
    "DEFAULT_MAX_TOTAL_BYTES",
]

# Hard caps shared with the old blob-staging path (Salesforce ContentVersion
# via REST tops out at ~37.5 MB after base64 expansion; 25 MiB raw is safe).
MAX_ATTACHMENTS_PER_CALL = 10
DEFAULT_MAX_TOTAL_BYTES = DEFAULT_MAX_STAGE_BYTES  # 25 MiB

# Maximum concurrency for parallel resolution — avoids thundering-herd on the
# cm endpoint when a call carries many records.
_MAX_CONCURRENT = 4

_BASE_DESCRIPTION = (
    "PipesHub record IDs of files to attach. Accepts IDs of files the user "
    "attached to this chat, artifacts you created, saved tool results, and "
    "knowledge base or connector files found via search. Use exact "
    "'Record ID' values from context — never invent one."
)


def attachment_record_ids_parameter(
    *,
    required: bool = False,
    extra_note: str = "",
) -> ToolParameter:
    """Return the canonical `attachment_record_ids` `ToolParameter`.

    `extra_note` is appended to the description for toolsets that need
    disambiguation (e.g. Salesforce, where `record_id` already means a
    Salesforce ID).
    """
    description = _BASE_DESCRIPTION
    if extra_note:
        description = f"{description} {extra_note}"

    return ToolParameter(
        name="attachment_record_ids",
        type=ParameterType.ARRAY,
        items={"type": "string"},
        required=required,
        default=None,
        description=description,
    )


@dataclass
class AttachmentBundle:
    """Result of `resolve_attachments`: successes + per-record failures."""

    resolved: list[ResolvedRecordContent] = field(default_factory=list)
    failures: list[AttachmentFailure] = field(default_factory=list)
    total_bytes: int = 0

    def has_errors(self) -> bool:
        return bool(self.failures)

    def to_error_payload(self) -> dict:
        return {
            "attachment_failures": [f.to_dict() for f in self.failures],
            "attachment_successes": len(self.resolved),
        }

    def to_summary(self) -> str:
        parts = [f"{len(self.resolved)} attachment(s) resolved ({self.total_bytes:,} bytes)"]
        if self.failures:
            fail_msgs = "; ".join(f"{f.ref}: {f.error}" for f in self.failures)
            parts.append(f"{len(self.failures)} failed: {fail_msgs}")
        return ". ".join(parts)


async def resolve_attachments(
    state: Any,
    refs: list[str] | None,
    *,
    max_count: int = MAX_ATTACHMENTS_PER_CALL,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_single_bytes: int | None = None,
) -> AttachmentBundle:
    """Resolve a list of record-ID refs to bytes, enforcing caps and ACL.

    Pulls context (`org_id`, `user_id`, `config_service`, `graph_provider`,
    `conversation_id`) off `ChatState`. Deduplicates refs, resolves them
    concurrently under a semaphore sharing one `aiohttp.ClientSession`.

    Partial failure: a ref that fails (not-found, denied, too-large) is
    added to `bundle.failures`; successfully resolved refs are in
    `bundle.resolved`. The caller decides whether to abort or proceed with
    the partial set.

    Returns an `AttachmentBundle` with empty `resolved` and a descriptive
    failure when runtime context is missing — same pattern as
    `upload_file_to_salesforce`.
    """
    bundle = AttachmentBundle()

    if not refs:
        return bundle

    # Validate runtime context is available.
    if not hasattr(state, "get"):
        bundle.failures.append(AttachmentFailure(
            ref="*",
            error="Attachment resolution requires the agent runtime context.",
            error_type="RuntimeError",
        ))
        return bundle

    org_id = state.get("org_id")
    user_id = state.get("user_id")
    config_service = state.get("config_service")
    graph_provider = state.get("graph_provider")
    conversation_id = state.get("conversation_id")
    blob_store = state.get("blob_store")

    if not org_id or not config_service or not graph_provider:
        bundle.failures.append(AttachmentFailure(
            ref="*",
            error=(
                "Attachment resolution requires an authenticated agent context "
                "(org_id, config_service, and graph_provider). "
                "This tool cannot be invoked outside the agent runtime."
            ),
            error_type="RuntimeError",
        ))
        return bundle

    if not user_id:
        bundle.failures.append(AttachmentFailure(
            ref="*",
            error="Attachment resolution requires user_id in agent context.",
            error_type="RuntimeError",
        ))
        return bundle

    # Deduplicate while preserving order.
    seen: set[str] = set()
    unique_refs: list[str] = []
    for r in refs:
        if r and r not in seen:
            seen.add(r)
            unique_refs.append(r)

    if len(unique_refs) > max_count:
        bundle.failures.append(AttachmentFailure(
            ref="*",
            error=(
                f"Too many attachment_record_ids: {len(unique_refs)} provided, "
                f"maximum is {max_count}. Reduce the list and retry."
            ),
            error_type="RecordContentError",
        ))
        return bundle

    actor = Actor(org_id=org_id, user_id=user_id)

    # Build the artifact registry only when blob_store is available — it is
    # optional for tests and runtime contexts without it.
    artifact_registry = None
    if blob_store is not None:
        try:
            from app.services.artifact_registry.registry import ArtifactRegistryService
            artifact_registry = ArtifactRegistryService(graph_provider, blob_store)
        except Exception:
            pass

    resolver = RecordContentResolver(
        graph_provider=graph_provider,
        config_service=config_service,
        artifact_registry=artifact_registry,
    )

    per_ref_max = max_single_bytes or max_total_bytes
    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _resolve_one(
        ref: str, session: aiohttp.ClientSession
    ) -> ResolvedRecordContent | AttachmentFailure:
        async with sem:
            try:
                return await resolver.resolve(
                    actor=actor,
                    ref=ref,
                    conversation_id=conversation_id,
                    max_bytes=per_ref_max,
                    session=session,
                )
            except RecordNotFoundError as exc:
                return AttachmentFailure(ref=ref, error=str(exc), error_type="RecordNotFoundError")
            except RecordAccessDeniedError as exc:
                return AttachmentFailure(ref=ref, error=str(exc), error_type="RecordAccessDeniedError")
            except RecordTooLargeError as exc:
                return AttachmentFailure(ref=ref, error=str(exc), error_type="RecordTooLargeError")
            except RecordContentError as exc:
                return AttachmentFailure(ref=ref, error=str(exc), error_type="RecordContentError")
            except Exception as exc:
                logger.exception("Unexpected error resolving attachment ref=%r", ref)
                return AttachmentFailure(ref=ref, error=f"Unexpected error: {exc}", error_type=type(exc).__name__)

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(_resolve_one(r, session) for r in unique_refs),
            return_exceptions=False,
        )

    running_total = 0
    for result in results:
        if isinstance(result, AttachmentFailure):
            bundle.failures.append(result)
        elif isinstance(result, ResolvedRecordContent):
            if running_total + result.size_bytes > max_total_bytes:
                bundle.failures.append(AttachmentFailure(
                    ref=result.record_id,
                    error=(
                        f"Total attachment size budget exceeded: adding {result.size_bytes:,} bytes "
                        f"would bring total to {running_total + result.size_bytes:,} bytes, "
                        f"exceeding the {max_total_bytes:,}-byte limit."
                    ),
                    error_type="RecordTooLargeError",
                ))
            else:
                bundle.resolved.append(result)
                running_total += result.size_bytes

    bundle.total_bytes = running_total
    return bundle


def emit_attachment_audit(
    *,
    org_id: str,
    user_id: str,
    record_id: str,
    filename: str,
    target_app: str,
    destination: str,
    success: bool,
    error: str | None = None,
    size_bytes: int | None = None,
) -> None:
    """Emit one structured audit log entry for a single file sent via an agent tool.

    The `pipeshub.attachment_audit` logger can be directed to a dedicated
    sink (e.g. a JSON file handler, Elasticsearch, or a syslog forwarder)
    independently of the root logger. Each entry contains enough context
    to reconstruct who sent what to where.

    Args:
        org_id: PipesHub organisation ID.
        user_id: PipesHub user ID initiating the send.
        record_id: PipesHub record ID of the file that was sent.
        filename: Human-readable filename for the record.
        target_app: Name of the destination app ("slack", "outlook", "gmail", "salesforce").
        destination: App-specific destination (channel ID, email address, SF record ID, …).
        success: Whether the platform upload succeeded.
        error: Error message on failure (omitted when success=True).
        size_bytes: Raw byte size of the resolved content (for storage-cost tracking).
    """
    entry: dict[str, Any] = {
        "event": "attachment_sent",
        "org_id": org_id,
        "user_id": user_id,
        "record_id": record_id,
        "filename": filename,
        "target_app": target_app,
        "destination": destination,
        "success": success,
    }
    if size_bytes is not None:
        entry["size_bytes"] = size_bytes
    if error:
        entry["error"] = error

    _audit_logger.info(json.dumps(entry))
