"""POST_TOOL_USE middleware: registers large tool results as artifacts.

Phase 1 of the two-phase compaction design.  When a tool result exceeds
``threshold_tokens``, the full content is persisted via an
``ArtifactStore`` and a ``ToolMessageMeta`` is placed on
``ctx.metadata["artifact_meta"]`` so the executor can carry it onto
``ToolResult.artifact_meta`` → ``ToolMessage.artifact_meta``.

Full content is intentionally **kept** in the tool response for the
current turn so the model can synthesize text from it immediately.
Compaction to a compact reference happens later, in the PRE_MODEL
``shape_artifact_compaction`` shaper, which is turn-aware.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol
from uuid import uuid4

from app.agent_loop_lib.core.messages import ToolMessageMeta
from app.agent_loop_lib.core.tokens import count_text_tokens
from app.agent_loop_lib.hooks.middleware.context import ToolResultContext

logger = logging.getLogger(__name__)


class ArtifactStore(Protocol):
    """Minimal write-side contract for persisting tool-result artifacts.

    The PipesHub adapter layer provides a concrete implementation backed
    by ``ArtifactRegistryService``; the generic library layer depends
    only on this protocol so it stays free of platform-specific imports.
    """

    async def store(
        self,
        content: str,
        *,
        tool_name: str,
        result_schema: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Persist *content* and return a stable ``artifact_id``."""
        ...


class InMemoryArtifactStore:
    """Non-durable, process-local fallback — fine for tests and standalone
    runs.  Production deployments should wire a blob-backed implementation
    (see ``services/artifact_registry``).

    Safety limits for SaaS:
    - ``maxsize``: evicts least-recently-used entries when the limit is
      reached (LRU — both ``store`` and ``get`` refresh the timestamp).
    - ``ttl_seconds``: entries older than this are silently evicted on
      access (lazy expiry).
    """

    def __init__(
        self, *, maxsize: int = 500, ttl_seconds: float = 3600
    ) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._schemas: dict[str, dict[str, Any]] = {}
        self._tool_names: dict[str, str] = {}
        self._maxsize = maxsize
        self._ttl = ttl_seconds

    def _now(self) -> float:
        return time.monotonic()

    def _evict_expired(self) -> None:
        cutoff = self._now() - self._ttl
        expired = [k for k, (ts, _) in self._data.items() if ts < cutoff]
        for k in expired:
            del self._data[k]
            self._schemas.pop(k, None)
            self._tool_names.pop(k, None)

    def _evict_oldest(self) -> None:
        while len(self._data) > self._maxsize:
            oldest_key = next(iter(self._data))
            del self._data[oldest_key]
            self._schemas.pop(oldest_key, None)
            self._tool_names.pop(oldest_key, None)

    def _touch(self, key: str) -> None:
        """Move *key* to the end of insertion order (most-recently-used)."""
        if key in self._data:
            ts, content = self._data.pop(key)
            self._data[key] = (self._now(), content)

    async def store(
        self,
        content: str,
        *,
        tool_name: str = "",
        result_schema: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        self._evict_expired()
        artifact_id = str(uuid4())
        self._data[artifact_id] = (self._now(), content)
        if result_schema is not None:
            self._schemas[artifact_id] = result_schema
        if tool_name:
            self._tool_names[artifact_id] = tool_name
        self._evict_oldest()
        return artifact_id

    async def get(self, artifact_id: str) -> str | None:
        entry = self._data.get(artifact_id)
        if entry is None:
            return None
        ts, content = entry
        if self._now() - ts > self._ttl:
            del self._data[artifact_id]
            self._schemas.pop(artifact_id, None)
            self._tool_names.pop(artifact_id, None)
            return None
        self._touch(artifact_id)
        return content

    def get_schema(self, artifact_id: str) -> dict[str, Any] | None:
        return self._schemas.get(artifact_id)

    def get_tool_name(self, artifact_id: str) -> str | None:
        return self._tool_names.get(artifact_id)


def shape_artifact_registration(
    store: ArtifactStore | None = None,
    threshold_tokens: int = 2_000,
    preview_chars: int = 200,
    resolve_schema: Any | None = None,
):
    """POST_TOOL_USE middleware that registers large results as artifacts.

    Parameters
    ----------
    store:
        Where full content is persisted.  Defaults to a process-local
        in-memory store.
    threshold_tokens:
        Minimum estimated token count for a result to be registered.
    preview_chars:
        Number of leading characters kept as a human-readable summary in
        the ``ToolMessageMeta``.
    resolve_schema:
        Optional callable ``(tool_name: str) -> dict | None`` that looks
        up the ``result_schema`` declared on the tool's ``@tool``
        decorator.  When *None*, schema metadata is omitted.
    """
    _store = store or InMemoryArtifactStore()

    _EXEMPT_SUFFIXES = frozenset({"/retrieve_artifact_content"})

    async def _middleware(ctx: ToolResultContext, next_fn) -> None:
        await next_fn()

        if ctx.tool_path and any(ctx.tool_path.endswith(s) for s in _EXEMPT_SUFFIXES):
            return

        response = ctx.tool_response
        if not response.success:
            return

        content = response.data
        if not isinstance(content, str):
            try:
                content = json.dumps(content)
            except (TypeError, ValueError):
                content = str(content)

        token_count = count_text_tokens(content)
        if token_count <= threshold_tokens:
            return

        if ctx.tool_path:
            segments = ctx.tool_path.strip("/").rsplit("/", 2)
            short_name = segments[-1]
            tool_name = "__".join(segments[-2:]) if len(segments) >= 2 else short_name
        else:
            short_name = ""
            tool_name = ""
        schema = resolve_schema(short_name) if resolve_schema is not None else None

        try:
            artifact_id = await _store.store(
                content,
                tool_name=tool_name,
                result_schema=schema,
                session_id=ctx.session_id,
            )
        except Exception:
            logger.warning(
                "artifact_registration: store.store() failed for tool %r "
                "(%d tokens) — tool result stays in context as full content",
                tool_name, token_count, exc_info=True,
            )
            return

        summary = content[:preview_chars].strip()
        if len(content) > preview_chars:
            summary += "..."

        turn_index = 0
        if ctx.scope is not None:
            turn_index = getattr(ctx.scope, "turn_index", 0) or 0

        tool_args = ctx.metadata.get("_result_accum_args")

        ctx.metadata["artifact_meta"] = ToolMessageMeta(
            artifact_id=artifact_id,
            summary=summary,
            tool_name=tool_name,
            tool_args=tool_args if isinstance(tool_args, dict) else None,
            result_schema=schema,
            original_token_count=token_count,
            turn_index=turn_index,
        )

    return _middleware
