"""One model call that tells, for every name still unresolved, which candidate
entities are the same real-world thing, or that none is.

A select-among-candidates prompt with an explicit "none", for all names at
once: choosing among candidates is markedly more accurate and cheaper than a
yes/no call per pair. Candidates are numbered per name, and only a number that
was offered for that name is accepted. A candidate whose name differs from the
mention by a digit (Q3 / Q4) is never offered. Any failure returns None, and
the caller keeps only exact matches.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.modules.entity_resolution.gates import digit_guard
from app.utils.aimodels import coerce_message_content_to_text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_SECONDS = 8.0

SYSTEM = "You match names to entities found in a company's documents. Reply with JSON only."
INSTRUCTIONS = (
    "For each name, the entities listed under it were found in the documents. Choose those that are "
    "the same real-world thing the name refers to in this question: the same person, organization, "
    "product, event or place, possibly written differently (case, a legal suffix, an abbreviation, a "
    "fuller or shorter form of the same name). Do not choose an entity that only shares a word with "
    "the name, a different organization or product with a similar name, something broader or "
    "narrower, or a different number or version. Choose none when no entity is the same thing."
)


@dataclass(frozen=True)
class OfferedCandidate:
    name: str
    kind: str
    records: str
    """How many readable records mention it, as shown ("1 record", "3+ records")."""


@dataclass(frozen=True)
class VerifyItem:
    mention: str
    kind_hint: str | None
    candidates: list[OfferedCandidate]


class MentionVerifier(Protocol):
    async def verify(self, question: str, items: list[VerifyItem]) -> dict[int, list[int]] | None:
        """For each item index answered, the indices of its candidates that are
        the same thing (possibly none); None when no answer could be had."""
        ...


def offerable(mention: str, candidate_name: str) -> bool:
    return digit_guard(mention, candidate_name)


def build_prompt(question: str, items: list[VerifyItem]) -> str:
    lines = [f"Question being answered: {question.strip() or '(none given)'}", "", INSTRUCTIONS, ""]
    for number, item in enumerate(items, start=1):
        hint = f" ({item.kind_hint})" if item.kind_hint else ""
        lines.append(f'Name {number}: "{item.mention}"{hint}')
        lines.extend(
            f"  {index}. {candidate.name} ({candidate.kind}, {candidate.records})"
            for index, candidate in enumerate(item.candidates, start=1)
        )
        lines.append("")
    example = ", ".join(f'"{number}": [...]' for number in range(1, len(items) + 1))
    lines.append(f"Reply as {{{example}}} with the numbers of the chosen entities for each name, [] for none.")
    return "\n".join(lines)


def parse_reply(text: str, items: list[VerifyItem]) -> dict[int, list[int]] | None:
    # Imported here: app.utils.streaming loads the whole retrieval stack.
    from app.utils.streaming import extract_json_from_string

    try:
        payload = extract_json_from_string(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    decided: dict[int, list[int]] = {}
    for key, value in payload.items():
        try:
            position = int(str(key).strip()) - 1
        except ValueError:
            continue
        if not 0 <= position < len(items) or not isinstance(value, list):
            continue
        chosen: list[int] = []
        for raw in value:
            if isinstance(raw, bool) or (isinstance(raw, float) and not raw.is_integer()):
                continue
            try:
                index = int(raw) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(items[position].candidates) and index not in chosen:
                chosen.append(index)
        decided[position] = chosen
    return decided


class LlmMentionVerifier:
    """``llm`` resolves the model and its name when a check is needed, so a
    call that settles every name by exact match never looks one up.
    ``on_llm_usage`` receives the reply, for the request's usage accounting."""

    def __init__(
        self,
        llm: Callable[[], Awaitable[tuple[BaseChatModel | None, str]]],
        *,
        timeout_seconds: float = VERIFY_TIMEOUT_SECONDS,
        on_llm_usage: Callable[[BaseMessage], None] | None = None,
        callbacks: list[Any] | None = None,
    ) -> None:
        self._llm = llm
        self._timeout = timeout_seconds
        self._on_llm_usage = on_llm_usage
        self._callbacks = callbacks or []

    async def verify(self, question: str, items: list[VerifyItem]) -> dict[int, list[int]] | None:
        if not any(item.candidates for item in items):
            return {index: [] for index in range(len(items))}
        started = time.monotonic()
        model = ""
        try:
            # The model lookup is inside the deadline too: a stalled config read
            # must not hold the search past it.
            reply, model = await asyncio.wait_for(self._ask(question, items), timeout=self._timeout)
        except Exception as exc:  # a failed check keeps only exact matches
            logger.warning(
                "entity_linking: verification failed after %dms: %r",
                int((time.monotonic() - started) * 1000), exc,
            )
            return None
        if reply is None:
            logger.info("entity_linking: no model to verify candidates with; exact matches only")
            return None
        self._note_usage(reply)
        decided = parse_reply(coerce_message_content_to_text(reply.content), items)
        logger.info(
            "entity_linking: verified names=%d candidates=%d answered=%s model=%s latency_ms=%d",
            len(items), sum(len(item.candidates) for item in items),
            None if decided is None else len(decided), model, int((time.monotonic() - started) * 1000),
        )
        return decided

    async def _ask(self, question: str, items: list[VerifyItem]) -> tuple[BaseMessage | None, str]:
        llm, model = await self._llm()
        if llm is None:
            return None, model
        reply = await llm.ainvoke(
            [SystemMessage(content=SYSTEM), HumanMessage(content=build_prompt(question, items))],
            config={"callbacks": self._callbacks} if self._callbacks else None,
        )
        return (reply if isinstance(reply, BaseMessage) else None), model

    def _note_usage(self, reply: BaseMessage) -> None:
        if self._on_llm_usage is None:
            return
        try:
            self._on_llm_usage(reply)
        except Exception:  # usage accounting never fails the lookup
            logger.debug("entity_linking: could not record verifier usage", exc_info=True)
