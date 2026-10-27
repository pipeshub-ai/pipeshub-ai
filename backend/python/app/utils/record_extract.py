"""The passages of one record that answer a question, picked out by a model.

An agent that reads a record through the API pays for every character of it
in its own model's context, usually a large model's. A web-fetch tool does
not: it has a small model read the page and pass on only what answers the
agent's prompt, about a thousand characters. On public web pages that made
an agent reading the pages itself cost 40% less than one reading PipesHub
records whole.

`extract_record` does the same for one record. It reads the record as
`read_record` would (the blocks most relevant to the question first, when
the record is larger than the reader's budget), hands that text to the model
assigned to the `reader` role (else the workspace's default model), and
returns what it copied out, with what the call cost in tokens. Each line of
the reply is looked up in the record's text, and the lines that cannot be
found there word for word are counted, so a caller can tell a quote from a
model's paraphrase. When the model cannot be reached, or replies with nothing,
the caller gets the plain read instead, with a note that says so, so asking
for an extract never fails a read.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage

from app.utils.llm import get_llm_for_role
from app.utils.record_reader import read_record

# The model role an admin can assign; unassigned, the workspace's default model reads.
READER_ROLE = "reader"
# How much of the record the reader model reads: about 30k tokens.
EXTRACT_SOURCE_CHARS = 120_000
# Longest extract passed on. The prompt asks for far less; this bounds a model that ignores it.
EXTRACT_MAX_CHARS = 12_000
# A slow model must not hold an agent's read for long; the plain read is the fallback.
EXTRACT_TIMEOUT_S = 60.0
NOTHING_RELEVANT = "NOTHING RELEVANT"
# A quoted fragment shorter than this ("Yes", "2026") proves nothing when found.
_MIN_FRAGMENT_CHARS = 12

_SYSTEM = f"""You read one document for an agent that is answering a question. The agent will \
not see the document, only what you copy out of it.

Copy out every part of the document that helps answer the question, word for word:
- Start with the document's title line and link, and any dates, authors or status lines that \
bear on the question.
- Keep names, numbers, dates, versions, ids and URLs exactly as written.
- Separate passages with a blank line, and put "…" where you skip text inside a passage.
- When the question asks for a list or a count, copy every item; do not summarise.
- Words of your own go in square brackets, and only to say where a passage sits, such as \
[reply by alice, 2026-09-03].
- Usually under 2,000 characters; never more than needed.

If nothing in the document helps, reply exactly: {NOTHING_RELEVANT}"""


def _usage(message: Any) -> dict[str, int]:  # noqa: ANN401
    usage = getattr(message, "usage_metadata", None) or {}
    return {
        "inputTokens": int(usage.get("input_tokens") or 0),
        "outputTokens": int(usage.get("output_tokens") or 0),
    }


def _normal(text: str) -> str:
    """Text as compared for quoting: one space for any run of whitespace, plain quotes."""
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    return " ".join(text.split())


def _unverified_lines(reply: str, source: str) -> int:
    """Lines of the reply with a fragment that is not in `source` word for word.

    The reply's own words sit in square brackets and its skips are marked "…"; both
    are set aside, as are list and emphasis marks the model may add. Short fragments
    are not checked.
    """
    haystack = _normal(source)
    unverified = 0
    for line in reply.splitlines():
        quoted = re.sub(r"\[[^\]]*\]", " ", line)
        quoted = re.sub(r"^\s*(?:[-*>]|\d+\.)\s+", "", quoted).replace("**", "").replace("__", "")
        fragments = [_normal(f) for f in re.split(r"…|\.\.\.", quoted)]
        if any(len(f) >= _MIN_FRAGMENT_CHARS and f not in haystack for f in fragments):
            unverified += 1
    return unverified


def _cut(text: str, limit: int) -> str:
    """`text` within `limit` characters, at the last line break that fits when there is one."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    end = head.rfind("\n")
    return head[:end].rstrip() if end > limit // 2 else head


def _text(message: Any) -> str:  # noqa: ANN401
    content = getattr(message, "content", "")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content).strip()


async def extract_record(
    record: dict[str, Any],
    *,
    query: str,
    config_service: Any,  # noqa: ANN401
    retrieval_service: Any = None,  # noqa: ANN401
    user_id: str | None = None,
    org_id: str | None = None,
    logger: Any = None,  # noqa: ANN401
    fallback_chars: int,
) -> dict[str, Any]:
    """The passages of `record` that answer `query`, as the reader model copied them out.

    The extract is at most `fallback_chars` (the caller's `max_chars`) or
    `EXTRACT_MAX_CHARS`, whichever is smaller, and `extract.truncated` says when
    the reply was cut to fit. It is one response, not a page: `complete` is true
    and there is no cursor; `extract.sourceComplete` says whether the reader saw
    the whole record. On any failure to get an extract, the plain read of
    `fallback_chars`, with `extract.error` saying why. An `HTTPException` (an
    identity refusal, say) is raised as it is.
    """
    source = await read_record(
        record,
        max_chars=EXTRACT_SOURCE_CHARS,
        query=query,
        retrieval_service=retrieval_service,
        user_id=user_id,
        org_id=org_id,
    )
    async def plain_read(error: str) -> dict[str, Any]:
        plain = source if fallback_chars == EXTRACT_SOURCE_CHARS else await read_record(
            record,
            max_chars=fallback_chars,
            query=query,
            retrieval_service=retrieval_service,
            user_id=user_id,
            org_id=org_id,
        )
        return {**plain, "extract": {"error": error}}

    try:
        llm, config = await get_llm_for_role(config_service, READER_ROLE)
        message = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=_SYSTEM),
                HumanMessage(content=f"Question: {query}\n\n<document>\n{source['content']}\n</document>"),
            ]),
            timeout=EXTRACT_TIMEOUT_S,
        )
    except HTTPException:
        raise
    except Exception as exc:  # any other failure falls back to the plain read
        if logger is not None:
            logger.warning("Record extract failed for %s, returning the plain read: %s", record.get("id"), exc)
        return await plain_read(type(exc).__name__)

    reply = _text(message)
    if not reply:
        if logger is not None:
            logger.warning("Record extract for %s came back empty, returning the plain read", record.get("id"))
        return await plain_read("EmptyReply")
    text = _cut(reply, min(fallback_chars, EXTRACT_MAX_CHARS))
    nothing = text == NOTHING_RELEVANT
    model = (config.get("configuration") or {}).get("model")
    return {
        "content": text,
        "mode": "record",
        "recordId": record.get("id"),
        "order": "extract",
        "totalBlocks": source.get("totalBlocks"),
        # One response, not a page: nothing follows it. Whether the reader saw the
        # whole record is `extract.sourceComplete`.
        "complete": True,
        "nextStartBlock": None,
        "chars": len(text),
        "maxChars": fallback_chars,
        "extract": {
            "model": model,
            "provider": config.get("provider"),
            "nothingRelevant": nothing,
            "truncated": len(text) < len(reply),
            # Lines not found in the record word for word: paraphrase, or a model error.
            "unverifiedLines": 0 if nothing else _unverified_lines(text, source.get("content") or ""),
            # How much of the record the reader saw, and whether that was all of it.
            "sourceChars": source.get("chars"),
            "sourceComplete": bool(source.get("complete")) and source.get("order") != "relevance",
            **_usage(message),
        },
    }
