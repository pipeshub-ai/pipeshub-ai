"""The legacy history a durable session imported (``session.imported``, P1b T3).

Node journals the conversation's legacy turns as the ``previousConversations``
entries the legacy route sends Python. A durable run gives the model the
messages legacy seeds a request with for them (``history_messages``): the same
conversion of each turn, its attachments resolved from storage, their
citation refs in the run's mapper, so the model sees the history exactly as
legacy would have given it.

In a chat others read (``x.pipeshub.history``), an earlier answer comes without
what its tools returned: :func:`answers_only_turns`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.kernel.core.messages import Message
    from harness_core.logs import HarnessLog

# What the person answered on a question card is replayed as the ask tool's result: their words, not data a tool read.
_ASK_TOOL = "ask_user_question"


def answers_only_turns(turns: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """History entries (``previousConversations``) as a run in a chat others read is given them: each earlier answer
    without the tool results replayed under it (``tool_results``, but for the question cards the person answered)
    and without the reference data its tools returned (``referenceData``). Questions, their attachments and the
    answers' text are unchanged, so a fact no earlier answer stated must come from the run's own tools, which cite it.
    """
    kept: list[dict[str, Any]] = []
    for turn in turns:
        entry = {key: value for key, value in turn.items() if key not in ("tool_results", "referenceData")}
        results: object = turn.get("tool_results")
        rows = cast("list[object]", results) if isinstance(results, list) else []
        asks = [
            cast("dict[str, Any]", row)
            for row in rows
            if isinstance(row, dict) and _ASK_TOOL in str(cast("dict[str, Any]", row).get("tool_name") or "")
        ]
        if asks:
            entry["tool_results"] = asks
        kept.append(entry)
    return kept


class LegacyHistory:
    def __init__(self, env: ToolEnvironment, log: HarnessLog, *, answers_only: bool = False) -> None:
        self._env = env
        self._log = log
        # A chat others read: the imported answers come without their tool results (``x.pipeshub.history``).
        self._answers_only = answers_only

    async def messages(self, transcript: bytes) -> list[Message]:
        from app.agents.agent_loop.factory import history_messages

        turns: list[dict[str, Any]] = [t for t in json.loads(transcript) if isinstance(t, dict)]
        if self._answers_only:
            turns = answers_only_turns(turns)
        messages = await history_messages(turns, self._env.context)
        self._log.info(
            "history.rendered",
            entries=len(turns),
            messages=len(messages),
            attachments=sum(len(t.get("attachments") or []) for t in turns),
            answers_only=self._answers_only,
        )
        return messages


__all__ = ["LegacyHistory", "answers_only_turns"]
