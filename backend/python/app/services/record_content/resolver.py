"""`RecordContentResolver` — the single façade agent tools call.

Resolution pipeline:
1. `graph_provider.get_record_by_id(ref)` — direct record lookup.
2. If `None` and `conversation_id` is set, fall back to
   `ArtifactRegistryService.resolve()` with logical-name support.
3. `RecordNotFoundError` if still unresolved.
4. `authorizer.authorize(actor, record)` — raises `RecordAccessDeniedError` on
   denial.
5. Dispatch on `record.origin` to the appropriate strategy, enforcing
   `max_bytes`.

Callers inject both `ArtifactRegistryService` and the two strategies via the
constructor; no module-level globals, so unit tests replace any collaborator
without monkey-patching.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .authorizer import TieredRecordAuthorizer
from .models import (
    RecordAccessDeniedError,
    RecordContentError,
    RecordNotFoundError,
)
from .strategies import (
    BlobBackedContentStrategy,
    ConnectorBackedContentStrategy,
    build_resolved_content,
)

logger = logging.getLogger(__name__)

__all__ = ["RecordContentResolver"]


class RecordContentResolver:
    """Façade for resolving a PipesHub `ref` to its authorized bytes.

    Constructed once per request-cycle and injected with its collaborators
    so each collaborator is independently replaceable in tests.
    """

    def __init__(
        self,
        *,
        graph_provider: Any,
        config_service: Any,
        artifact_registry: Any | None = None,
    ) -> None:
        self._graph = graph_provider
        self._config = config_service
        self._artifacts = artifact_registry
        self._authorizer = TieredRecordAuthorizer(graph_provider)
        self._blob_strategy = BlobBackedContentStrategy()
        self._connector_strategy = ConnectorBackedContentStrategy()

    async def resolve(
        self,
        *,
        actor: Any,
        ref: str,
        version: int | None = None,
        conversation_id: str | None = None,
        max_bytes: int,
        session: aiohttp.ClientSession | None = None,
    ) -> Any:
        """Resolve `ref` to a `ResolvedRecordContent`.

        Raises:
            RecordNotFoundError — ref cannot be mapped to any record.
            RecordAccessDeniedError — actor is not authorized.
            RecordTooLargeError — content exceeds `max_bytes`.
            RecordContentUnavailableError — transport/connector failure.
        """
        record = await self._lookup_record(ref, actor, conversation_id)

        await self._authorizer.authorize(actor, record)

        content, source = await self._fetch(record, actor=actor, version=version,
                                            max_bytes=max_bytes, session=session)
        return build_resolved_content(record, content, version, source)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _lookup_record(
        self, ref: str, actor: Any, conversation_id: str | None
    ) -> Any:
        """Try graph lookup first, then artifact registry fallback."""
        try:
            record = await self._graph.get_record_by_id(ref)
        except Exception as exc:
            logger.warning("get_record_by_id(%r) raised: %s", ref, exc)
            record = None

        if record is not None:
            return record

        # Artifact logical-name fallback (e.g. model passes "report.pdf"
        # instead of the UUID artifact_id).
        if self._artifacts is not None and conversation_id:
            return await self._resolve_via_artifact_registry(ref, actor, conversation_id)

        raise RecordNotFoundError(f"Record not found: {ref}")

    async def _resolve_via_artifact_registry(
        self, ref: str, actor: Any, conversation_id: str
    ) -> Any:
        """Delegate to `ArtifactRegistryService.resolve()`, then re-fetch
        the base `records` document so the resolver always works against a
        uniform object type rather than `ArtifactMetadata`."""
        from app.services.artifact_registry.access import ArtifactNotFoundError

        try:
            metadata = await self._artifacts.resolve(
                actor=actor, ref=ref, conversation_id=conversation_id
            )
        except (ArtifactNotFoundError, RecordAccessDeniedError) as exc:
            raise RecordNotFoundError(f"Record not found: {ref}") from exc

        # Re-fetch the base `records` document so strategies see a full
        # Record with all fields (origin, externalRecordId, versions, etc.).
        try:
            record = await self._graph.get_record_by_id(metadata.artifact_id)
        except Exception:
            record = None
        if record is None:
            raise RecordNotFoundError(
                f"Graph record missing for artifact: {metadata.artifact_id}"
            )
        return record

    async def _fetch(
        self,
        record: Any,
        *,
        actor: Any,
        version: int | None,
        max_bytes: int,
        session: aiohttp.ClientSession | None,
    ) -> tuple[bytes, str]:
        """Dispatch to the correct strategy based on `record.origin`."""
        shared_kwargs = dict(
            actor=actor,
            version=version,
            max_bytes=max_bytes,
            session=session,
            config_service=self._config,
        )

        if self._blob_strategy.supports(record):
            data = await self._blob_strategy.fetch(record, **shared_kwargs)
            return data, "blob"

        if self._connector_strategy.supports(record):
            data = await self._connector_strategy.fetch(record, **shared_kwargs)
            return data, "connector"

        origin = getattr(record, "origin", None) or (
            record.get("origin") if isinstance(record, dict) else None
        )
        raise RecordContentError(
            f"No strategy supports record with origin={origin!r}"
        )
