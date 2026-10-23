from __future__ import annotations

import itertools
import logging
from typing import Protocol

from app.agent_loop_lib.core.tokens import count_text_tokens, count_tokens
from app.agent_loop_lib.core.types import MessageRole
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext

logger = logging.getLogger(__name__)

LAYER = "offload"


class OffloadStore(Protocol):
    """Where offloaded tool-result bodies go. `WorkspaceBackend` filesystem
    tools are the real implementation of this idea — this minimal protocol
    lets the context engine offload without depending on that phase, and
    `write_file`/`read_file` tools can share the same store."""

    def write(self, content: str) -> str:
        """Persist content, return a path/handle the model can reference."""
        ...


class InMemoryOffloadStore:
    """Default `OffloadStore` — process-local, not durable. Fine for a
    single run; swap for a real filesystem-backed store in production."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self._counter = itertools.count(1)

    def write(self, content: str) -> str:
        path = f"/offload/tool_result_{next(self._counter)}.txt"
        self._data[path] = content
        return path

    def read(self, path: str) -> str | None:
        return self._data.get(path)


def shape_offload(
    store: OffloadStore | None = None,
    threshold_tokens: int = 2_000,
    preview_lines: int = 10,
):
    """Layer 3 context shaper (Deep Agents pattern): TOOL messages over
    `threshold_tokens` are written in full to an `OffloadStore` and replaced
    in-context with a path + short preview, so the model can still see what
    it found without paying for the full payload on every subsequent turn.
    Direct replacement for `OffloadHook`.
    """
    store = store or InMemoryOffloadStore()

    async def _middleware(ctx: ModelCallContext, next_fn) -> None:
        memo = ctx.compaction_memo
        shaped = []
        for msg in ctx.messages:
            if msg.role != MessageRole.TOOL or not isinstance(msg.content, str):
                shaped.append(msg)
                continue
            if count_text_tokens(msg.content) <= threshold_tokens:
                shaped.append(msg)
                continue
            # A result offloaded on an earlier call keeps its path, so the
            # store is written once and the sent text does not change.
            earlier = memo.recall_message(LAYER, msg) if memo is not None else None
            if earlier is not None:
                shaped.append(earlier)
                continue
            path = store.write(msg.content)
            preview = "\n".join(msg.content.splitlines()[:preview_lines])
            replacement = (
                f"[offloaded full result to {path} — first {preview_lines} lines below]\n{preview}"
            )
            shaped.append(msg.model_copy(update={"content": replacement, "compacted_by": LAYER}))
            if memo is not None:
                memo.remember(LAYER, [msg], [shaped[-1]])
        offloaded = sum(1 for old, new in zip(ctx.messages, shaped) if new is not old)
        if offloaded:
            logger.info(
                "offload: offloaded %d tool result(s), %d→%d tokens (run=%s, turn=%d, budget=%d)",
                offloaded, count_tokens(ctx.messages), count_tokens(shaped),
                ctx.run_id, ctx.turn_index, ctx.budget.effective_max_tokens,
            )
        ctx.messages = shaped
        await next_fn()

    return _middleware
