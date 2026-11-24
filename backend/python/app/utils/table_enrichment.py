"""Concurrent LLM enrichment of tables (summary, headers, per-row descriptions).

A table that fits in one batch costs a single LLM call: the combined prompt returns the
summary, the column headers and the row descriptions together. Larger tables spend that
first call on the summary plus the first batch, then fan the remaining batches out in
parallel using the summary as context.

Rows are addressed by an opaque key. Each row is sent with a short random key and the model
answers ``[{"key": "...", "description": ...}]`` copying the keys; descriptions are matched
back by key. A key cannot be counted or shifted, so a skipped, merged, duplicated or reordered
row can only lose its own description, never hand it to another row. A row with no usable
description gets ``generate_simple_row_text`` alone.

A batch that comes back with too few usable rows (or no parsable answer) is split in half and
retried, down to ``MIN_SPLIT_ROWS`` rows, within a per-table budget of retry calls. A first
call whose rows fail still keeps its summary and headers (or asks for them on their own), so
only a table whose summary and headers also fail falls back as a whole.

Enrichment is best-effort. Any failure degrades rows or a table to ``generate_simple_row_text``
rather than failing the record, because the blocks themselves are already correct - only
the natural-language text quality is lost. With no model set up at all, every table gets
that plain text, so the record still indexes and is searchable.
"""

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from logging import Logger
from typing import Any, Optional

from langchain_core.language_models.chat_models import BaseChatModel

from app.core.org_context import spawn_in_request
from app.exceptions.non_retryable import NonRetryableError
from app.models.blocks import summary_with_captions
from app.modules.parsers.excel.prompt_template import (
    KeyedRowDescription,
    KeyedRowDescriptions,
    TableEnrichment,
    TableSummaryHeaders,
    keyed_row_text_prompt,
    table_enrichment_prompt,
    table_summary_headers_prompt,
)
from app.services.model_identity.call_context import peek_call_context
from app.services.paid_cache.access import cached_model_call, remember_cache
from app.services.paid_cache.keys import KIND_TABLE_ROWS, KIND_TABLE_SUMMARY

# Imported as a module, not by name: ~30 tests patch
# module-level `from ... import name` here would bind its own reference and silently
# bypass those patches.
from app.utils import indexing_helpers
from app.utils import streaming as _streaming
from app.utils.aimodels import INDEXING_SDK_RETRIES
from app.utils.concurrency import (
    MAX_CONCURRENT_ROW_BATCHES,
    MAX_CONCURRENT_TABLES,
    TABLE_ROW_BATCH_SIZE_MAX,
    max_table_rows_for_llm,
    table_row_batch_size,
)
from app.utils.indexing_metrics import (
    note_degraded_rows,
    note_rows_over_budget,
    note_table_result,
    track_indexing_enrichment,
)
from app.utils.llm import LLMNotConfiguredError, get_llm_for_role
from app.utils.streaming import invoke_with_structured_output_and_reflection
from app.utils.table_row_memory import (
    StoredTable,
    previous_row_texts,
    row_digest,
    with_row_digest,
)

# Flow tags in the paid-cache params. The keyed answer shape is a different flow from the
# positional ("count_validated" / "structured") and the numbered ("numbered_v2") shapes that
# earlier versions cached, and the prompts differ too, so none of those entries is ever a hit.
_FLOW_COMBINED = "keyed_v3"
_FLOW_SUMMARY = "summary_headers_v2"
_FLOW_ROWS = "keyed_v3"

#: A batch may lose this fraction of its rows (those rows alone fall back) before it is split and
#: retried: one dropped row in fifty is not worth two more calls. Below 10 rows nothing is tolerated.
ROW_MISS_TOLERANCE = 0.1
#: A failed batch is split in half down to this many rows; smaller pieces cost a call each and the
#: model rarely fails a batch this small, so a batch under twice this is never split.
MIN_SPLIT_ROWS = 5
#: Rows sent when only the summary and headers are asked for: enough to name the columns and the
#: topic, and a fraction of a full batch's prompt.
SUMMARY_SAMPLE_ROWS = 20
#: Floor of a table's retry calls, so a small table can still split once or twice.
MIN_RETRY_CALLS = 4
#: One reflection per call, for an answer that did not parse; the old count reflection is gone
#: because keys make a wrong count harmless.
_REFLECTIONS = 1
#: Output-token ceiling of the table calls, on top of the reasoning reserve ``with_output_cap``
#: adds and never above what the model accepts. A safety ceiling against a reply that runs a
#: field on to the provider's own limit (tens of thousands of tokens), not a budget: it is far
#: above any whole first or summary reply. A row batch's ceiling grows with its rows, so a
#: large configured batch (``INDEXING_TABLE_ROW_BATCH_SIZE``) still fits.
TABLE_CALL_MAX_OUTPUT_TOKENS = 16384
ROW_BATCH_TOKENS_PER_ROW = 120


def row_batch_output_cap(rows: int) -> int:
    return max(TABLE_CALL_MAX_OUTPUT_TOKENS, ROW_BATCH_TOKENS_PER_ROW * rows)


class _ReplyCutOff(Exception):
    """A row batch's reply stopped at its output limit."""


class _ReplyRanAway(Exception):
    """A row batch's reply held a number too long to read: asked again, it would run away again."""


class _ModelCallFailed(Exception):
    """The model call itself failed (as opposed to building its prompt, which is a bug to surface)."""


class RetryBudget:
    """The retry calls a table may spend splitting failed batches (two per split).

    It bounds the cost of a failing provider to about twice the nominal row calls instead of
    letting every batch halve down to the floor.
    """

    def __init__(self, calls: int) -> None:
        self.calls = calls

    @classmethod
    def for_batches(cls, batches: int) -> "RetryBudget":
        return cls(max(MIN_RETRY_CALLS, batches + 1))

    def take(self, calls: int) -> bool:
        # No await between the check and the decrement: tasks on one loop cannot interleave.
        if self.calls >= calls:
            self.calls -= calls
            return True
        return False


@dataclass
class TableEnrichmentResult:
    """Enrichment for one table. ``descriptions`` aligns with the data rows."""

    summary: str = ""
    headers: list[str] = field(default_factory=list)
    header_row_count: int = 0
    descriptions: list[str] = field(default_factory=list)
    #: Some text is plain ``column: value`` text (a row, or the summary, that the model did not give).
    degraded: bool = False
    #: The whole table fell back (its summary and headers could not be had). Only this counts
    #: against a record; rows that fell back do not, as before.
    failed: bool = False
    #: Per data row, the digest its row block stores (``table_row_memory``), so the next update can keep its text; ``None`` for a
    #: row whose text is plain, which is described again then. Empty when no row was described.
    row_digests: list[Optional[str]] = field(default_factory=list)


def cell_text(cell: Any) -> str:  # noqa: ANN401
    """Normalize a grid cell, which may be a dict, ``None`` or a scalar."""
    if isinstance(cell, dict):
        return str(cell.get("text", "") or "")
    return "" if cell is None else str(cell)


def normalize_grid(grid: Sequence[Sequence[Any]]) -> list[list[str]]:
    return [[cell_text(cell) for cell in row] for row in grid]


def _row_dict(row: Sequence[str], headers: Sequence[str]) -> dict[str, str]:
    keys = indexing_helpers.unique_column_keys(headers, len(row))
    return {keys[i]: value for i, value in enumerate(row)}


def _simple_texts(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> list[str]:
    return [
        indexing_helpers.generate_simple_row_text(_row_dict(row, headers)) for row in rows
    ]


def _usable(text: Optional[str]) -> bool:
    return text is not None and bool(text.strip())


_KEY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"  # base32: no 0/1/8/9, so no O/0 or I/1 mix-ups
KEY_CHARS = 4  # 20 bits: unique within a batch by construction, and short enough to copy


#: Characters of each cell (and column title) the key seed reads. Keys only have to be stable and
#: distinct within a batch, so a bounded slice is enough, and a huge cell is never serialised.
_SEED_CELL_CHARS = 256
#: Rows a batch of keys can hold. Keys at least two apart number at most 32**3, a draw loop saturates
#: well before that, and no batch is meant to be this large: every path batches at the size limit.
MAX_KEYED_ROWS = TABLE_ROW_BATCH_SIZE_MAX


def _seed_text(value: Any) -> bytes:  # noqa: ANN401
    text = value[:_SEED_CELL_CHARS] if isinstance(value, str) else str(value)[:_SEED_CELL_CHARS]
    return text.encode("utf-8", "surrogatepass")


def _batch_seed(rows: Sequence[Any]) -> str:
    """A hash of the batch: each row's cells in column order, each cut to ``_SEED_CELL_CHARS``.

    Built cell by cell, so nothing is compared (column titles may be numbers beside text) and
    nothing larger than a cell's slice is held.
    """
    digest = hashlib.sha256()
    for row in rows:
        for column, value in row.items() if isinstance(row, dict) else enumerate(row):
            digest.update(_seed_text(column))
            digest.update(b"\x1f")
            digest.update(_seed_text(value))
            digest.update(b"\x1e")
        digest.update(b"\x1d")
    return digest.hexdigest()


def _raw_key(seed: str, counter: int) -> str:
    value = int.from_bytes(hashlib.sha256(f"{seed}:{counter}".encode()).digest()[:3], "big")
    return "".join(_KEY_ALPHABET[(value >> (5 * i)) & 31] for i in range(KEY_CHARS))


def _within_one_character(key: str) -> list[str]:
    """*key* itself and every key one character away from it."""
    near = [key]
    for i in range(KEY_CHARS):
        near.extend(key[:i] + c + key[i + 1 :] for c in _KEY_ALPHABET if c != key[i])
    return near


def row_keys(rows: Sequence[Any]) -> list[str]:
    """One opaque key per row of a batch: four base32 characters, at least two apart from each other.

    The keys come from a hash of the batch's content, so they carry nothing of the row's
    position (they cannot be counted or shifted) and the same batch always gets the same keys,
    which keeps its prompt, and so its paid-cache entry, stable across retries. A drawn key that
    equals, or is one character away from, a key already in the batch is redrawn, so a single
    copy typo never turns one row's key into another's.
    """
    if len(rows) > MAX_KEYED_ROWS:
        raise ValueError(f"a batch holds at most {MAX_KEYED_ROWS} rows, got {len(rows)}")
    seed = _batch_seed(rows)
    keys: list[str] = []
    blocked: set[str] = set()
    counter = 0
    max_draws = 20 * len(rows) + 1000
    while len(keys) < len(rows):
        if counter >= max_draws:
            raise ValueError(f"key space used up after {counter} draws for {len(rows)} rows")
        key = _raw_key(seed, counter)
        counter += 1
        if key not in blocked:
            keys.append(key)
            blocked.update(_within_one_character(key))
    return keys


def format_rows_with_keys(
    rows: Sequence[Any], keys: Sequence[str], *, numbered: bool = False
) -> str:
    """The rows as the prompt shows them: ``[KEY]: {json}``, or ``Row n [KEY]: {json}``.

    The number is only for the combined prompt, where the model must say whether Row 1 is a
    header; descriptions are always matched by key.
    """
    return "\n".join(
        f"{f'Row {i} ' if numbered else ''}[{key}]: {json.dumps(row, indent=2, ensure_ascii=False)}"
        for i, (row, key) in enumerate(zip(rows, keys), 1)
    )


def _normal_key(key: str) -> str:
    """A copied key made comparable: models add brackets or quotes and change case."""
    return key.strip().strip("[]\"'` ").upper()


# The two echo habits tolerated beyond a bare key: the key in brackets inside a longer label
# ("Row 3 [ab2c]"), and the key after a "key:" label. Nothing looser: a word that merely looks like a key is not one.
_ECHOED_KEY = re.compile(r"\[\s*([A-Za-z2-7]{4})\s*\]|\bkey\s*[:=]\s*([A-Za-z2-7]{4})\b", re.IGNORECASE)


def _row_of_key(index: dict[str, int], copied: str) -> Optional[int]:
    """The row a copied key names: the key itself, else a bracketed or labelled key inside it."""
    row = index.get(_normal_key(copied))
    if row is not None:
        return row
    for match in _ECHOED_KEY.finditer(copied):
        row = index.get((match.group(1) or match.group(2)).upper())
        if row is not None:
            return row
    return None


def align_by_key(
    descriptions: Sequence[KeyedRowDescription], keys: Sequence[str]
) -> list[Optional[str]]:
    """Per row (in the order of *keys*), the description the model gave its key, or ``None``.

    The first entry for a key wins; keys the batch never had and blank descriptions are
    ignored. The answer's length and order play no part, so a short, long or reordered answer
    never moves a description onto another row.
    """
    index = {key: i for i, key in enumerate(keys)}
    aligned: list[Optional[str]] = [None] * len(keys)
    seen: set[int] = set()
    for entry in descriptions:
        i = _row_of_key(index, entry.key)
        if i is None or i in seen:
            continue
        seen.add(i)
        text = (entry.description or "").strip()
        if text:
            aligned[i] = text
    return aligned


def _complete(aligned: Sequence[Optional[str]]) -> bool:
    return all(_usable(a) for a in aligned)


def _strict_decode_keyed(model: Any):  # noqa: ANN401, ANN202
    """Decode a stored answer, refusing the positional and numbered shapes older versions stored.

    The tolerant model validator would drop such a description list to ``[]``, turning an old
    cached answer into an empty keyed one. A stored value must instead be a miss, so every
    description has to be an object with a ``key``.
    """
    def decode(raw: Any):  # noqa: ANN401, ANN202
        entries = raw.get("descriptions") if isinstance(raw, dict) else None
        if not isinstance(entries, list) or any(
            not isinstance(e, dict) or "key" not in e for e in entries
        ):
            raise ValueError("stored table answer is not keyed")
        return model.model_validate(raw)

    return decode


def _record_id() -> str:
    ctx = peek_call_context()
    return (ctx.record_id if ctx is not None and ctx.record_id else "-")


class _Run:
    """State of one table's (or one parser batch's) row descriptions: model, summary, retry
    budget, and what degraded by reason.

    ``prompt_guard`` wraps the rows put into a prompt (a parser's output-budget accounting and
    value cutting); ``propagate`` lists exceptions that must reach the caller instead of
    degrading the batch.
    """

    def __init__(
        self,
        llm: BaseChatModel,
        summary: str,
        logger: Logger,
        budget: RetryBudget,
        *,
        config_service: Any = None,  # noqa: ANN401
        prompt_guard: Optional[Callable[[list[dict]], Any]] = None,
        propagate: tuple[type, ...] = (),
    ) -> None:
        self.llm = llm
        self.summary = summary
        self.logger = logger
        self.budget = budget
        self.config_service = config_service
        self.prompt_guard = prompt_guard
        self.propagate = (NonRetryableError, *propagate)
        self.rows_by_reason: Counter = Counter()
        self.record_id = _record_id()

    def note_degraded(self, reason: str, rows: int, described: int, missing: int) -> None:
        self.rows_by_reason[reason] += missing
        # Counts and the record id only: a row's text is never logged.
        self.logger.warning(
            "Table row batch degraded: record=%s reason=%s rows=%d described=%d fallback=%d",
            self.record_id, reason, rows, described, missing,
        )


async def _call_row_batch(
    run: _Run, rows_data: Sequence[dict]
) -> Optional[list[Optional[str]]]:
    """One keyed-description call for *rows_data* (one dict per row).

    Returns the descriptions aligned to the rows (``None`` per unanswered row), or ``None`` when
    no parsable answer came back. A failed call raises.
    """
    keys = row_keys(rows_data)
    guard = run.prompt_guard(list(rows_data)) if run.prompt_guard else nullcontext(list(rows_data))
    with guard as prompt_rows:
        messages = keyed_row_text_prompt.format_messages(
            table_summary=run.summary,
            row_count=len(rows_data),
            keyed_rows_data=format_rows_with_keys(prompt_rows, keys),
        )
        try:
            parsed = await cached_model_call(
                KIND_TABLE_ROWS,
                llm=run.llm,
                template=keyed_row_text_prompt,
                rendered=_rendered(messages),
                params={"flow": _FLOW_ROWS},
                call=lambda: invoke_with_structured_output_and_reflection(
                    run.llm, messages, KeyedRowDescriptions, _REFLECTIONS, raise_call_errors=True,
                    max_output_tokens=row_batch_output_cap(len(rows_data)), raise_truncated=True,
                    raise_oversized_number=True,
                ),
                encode=lambda answer: answer.model_dump(),
                decode=_strict_decode_keyed(KeyedRowDescriptions),
                cacheable=lambda answer: _complete(align_by_key(answer.descriptions, keys)),
                config_service=run.config_service,
            )
        except NonRetryableError:
            _publish_stop()
            raise
        except run.propagate:
            raise
        # Looked up on the helper's module at raise time, so a reloaded module's class still matches.
        except _streaming.TruncatedReplyError as e:
            raise _ReplyCutOff from e
        except _streaming.OversizedNumberError as e:
            raise _ReplyRanAway from e
        except Exception as e:
            raise _ModelCallFailed(str(e)) from e
    if parsed is None:
        return None
    return align_by_key(parsed.descriptions, keys)


class _Stop:
    """Set once a job of one ``_gather_limited`` run raised a ``NonRetryableError``."""

    __slots__ = ("stopped",)

    def __init__(self) -> None:
        self.stopped = False


#: The stop flags of every ``_gather_limited`` run the current task is inside, outermost first.
#: A stop is published to all of them when it is raised, not when it reaches each run: while a
#: table's row batches are still being cancelled, another table may free a slot of the record's
#: run, and the queued table must not start then.
_STOPS: ContextVar[tuple[_Stop, ...]] = ContextVar("table_enrichment_stops", default=())


def _publish_stop() -> None:
    for stop in _STOPS.get():
        stop.stopped = True


async def gather_or_cancel(*coros: Any) -> list[Any]:  # noqa: ANN401
    """``asyncio.gather``, but when one raises the others are cancelled and awaited first.

    A bare gather leaves the siblings running after the error ended the load: they would finish
    their model calls, and could split again and spend more.
    """
    # Request child work: each half keeps the caller's context (org included) and is cancelled with it.
    tasks = [spawn_in_request(c) for c in coros]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException as exc:
        if isinstance(exc, NonRetryableError):
            _publish_stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _gather_limited(limit: int, jobs: Sequence[Callable[[], Any]]) -> list[Any]:
    """Run *jobs* (functions that return a coroutine), at most *limit* at once, results in order.

    An exception that is an ordinary failure becomes that job's result, for the caller to turn
    into a fallback. A ``NonRetryableError`` is a deliberate stop: the request can never succeed,
    so it is raised unchanged after the jobs still running are cancelled and awaited. The stop
    holds for every enclosing run too (a row batch's stop ends the record's queued tables).
    """
    semaphore = asyncio.Semaphore(max(1, limit))
    stops = (*_STOPS.get(), _Stop())

    async def run(job: Callable[[], Any]) -> Any:  # noqa: ANN401
        async with semaphore:
            # Woken by a slot freed after a stop (here or in a nested run): its call must not start.
            if any(stop.stopped for stop in stops):
                return None
            try:
                return await job()
            except NonRetryableError:
                _publish_stop()
                raise
            except Exception as e:
                return e

    token = _STOPS.set(stops)
    try:
        # The jobs' tasks are created here, so each one sees these stop flags.
        return await gather_or_cancel(*(run(job) for job in jobs))
    finally:
        _STOPS.reset(token)


async def _resolve_batch(
    run: _Run,
    rows_data: Sequence[dict],
    answer: Optional[list[Optional[str]]],
    salvage: Optional[list[Optional[str]]] = None,
) -> list[Optional[str]]:
    """Settle a batch from the answer it got: keep it, or split it and ask again.

    Returns a description per row, ``None`` where the row must fall back. A description a
    failed attempt already gave a row (*salvage*) is kept if the retry does not improve it.
    """
    n = len(rows_data)
    reason = "count" if answer is not None else "parse"
    best: list[Optional[str]] = list(answer) if answer is not None else [None] * n
    if salvage is not None:
        best = [b if _usable(b) else (salvage[i] if _usable(salvage[i]) else None)
                for i, b in enumerate(best)]
    missing = sum(1 for b in best if b is None)
    if missing == 0:
        return best
    if missing <= int(n * ROW_MISS_TOLERANCE):
        run.note_degraded(reason, n, n - missing, missing)
        return best

    can_split = n >= 2 * MIN_SPLIT_ROWS
    if can_split and run.budget.take(2):
        mid = n // 2
        left, right = await gather_or_cancel(
            _describe_batch(run, rows_data[:mid], best[:mid], split_cut_off=False),
            _describe_batch(run, rows_data[mid:], best[mid:], split_cut_off=False),
        )
        return left + right
    run.note_degraded("budget" if can_split else reason, n, n - missing, missing)
    return best


async def _describe_batch(
    run: _Run,
    rows_data: Sequence[dict],
    salvage: Optional[list[Optional[str]]] = None,
    *,
    split_cut_off: bool = True,
) -> list[Optional[str]]:
    """Describe *rows_data* with one call, splitting it on a poor answer. ``None`` = fall back."""
    try:
        answer = await _call_row_batch(run, rows_data)
    except _ReplyCutOff:
        n = len(rows_data)
        # A whole batch can outgrow its cap, so it is split in half once, within the table's
        # retry budget. A half cut off again is a reply that runs on: its rows fall back,
        # reported once by note_degraded, and it is not asked again.
        if split_cut_off and n >= 2 and run.budget.take(2):
            mid = n // 2
            halves = salvage if salvage is not None else [None] * n
            left, right = await gather_or_cancel(
                _describe_batch(run, rows_data[:mid], halves[:mid], split_cut_off=False),
                _describe_batch(run, rows_data[mid:], halves[mid:], split_cut_off=False),
            )
            return left + right
        return _salvaged(run, rows_data, salvage, "parse")
    except _ReplyRanAway:  # not split: each half would be asked for the same runaway field
        return _salvaged(run, rows_data, salvage, "parse")
    except _ModelCallFailed as e:  # a failed call is not retried: a split would only add load
        run.logger.warning("Table row batch call failed: %s", type(e.__cause__).__name__)
        return _salvaged(run, rows_data, salvage, "error")
    return await _resolve_batch(run, rows_data, answer, salvage)


def _salvaged(run: _Run, rows_data: Sequence[dict], salvage: list[str | None] | None, reason: str) -> list[str | None]:
    """What earlier attempts gave the batch, ``None`` for the rest, reported once under *reason*."""
    n = len(rows_data)
    best = list(salvage) if salvage is not None else [None] * n
    missing = sum(1 for b in best if not _usable(b))
    if missing:
        run.note_degraded(reason, n, n - missing, missing)
    return [b if _usable(b) else None for b in best]


async def describe_row_dicts(
    llm: BaseChatModel,
    rows_data: Sequence[dict],
    table_summary: str,
    logger: Logger,
    *,
    budget: Optional[RetryBudget] = None,
    config_service: Any = None,  # noqa: ANN401
    prompt_guard: Optional[Callable[[list[dict]], Any]] = None,
    propagate: tuple[type, ...] = (),
) -> list[str]:
    """Describe one batch of rows (one dict per row) by row key, for the CSV and Excel parsers.

    The same rules as ``enrich_table_grid``: a row the model leaves out gets plain ``column:
    value`` text alone, a short or unparsable batch is split and retried within *budget* (share
    one across a table's batches; the default allows one split or two), and degraded rows are
    counted by reason. Exceptions in *propagate* (and ``NonRetryableError``) are raised.
    """
    run = _Run(
        llm, table_summary, logger, budget or RetryBudget(MIN_RETRY_CALLS),
        config_service=config_service, prompt_guard=prompt_guard, propagate=propagate,
    )
    # No batch is larger than the size limit, whatever the caller hands over.
    chunks = [rows_data[i : i + MAX_KEYED_ROWS] for i in range(0, len(rows_data), MAX_KEYED_ROWS)] or [rows_data]
    texts = [t for part in await gather_or_cancel(*(_describe_batch(run, chunk) for chunk in chunks)) for t in part]
    for reason, count in run.rows_by_reason.items():
        note_degraded_rows(reason, count)
    return [
        t if _usable(t) else indexing_helpers.generate_simple_row_text(rows_data[i])
        for i, t in enumerate(texts)
    ]


def _rendered(messages: Sequence[Any]) -> str:
    """The prompt as sent: the template filled with this table's rows."""
    return "\n".join(str(getattr(m, "content", m)) for m in messages)


def _header_context(known_headers: Optional[list[str]]) -> str:
    if known_headers is None:
        return ""
    if known_headers:
        return (
            f"\nThe column headers are already known: {json.dumps(known_headers, ensure_ascii=False)}\n"
            'Use them verbatim as "headers" and set "header_row_count" to 0 - none of '
            "the rows above is a header row.\n"
        )
    return (
        '\nNone of the rows above is a header row: set "header_row_count" to 0 and '
        "invent column names from the data.\n"
    )


async def indexing_llm_or_none(config_service: Any, logger: Logger) -> Optional[BaseChatModel]:  # noqa: ANN401
    """The indexing-role model, or None when no model is set up.

    A model that is set up but cannot be built still raises, so the record retries.
    """
    try:
        llm, _ = await get_llm_for_role(
            config_service, "indexing", reasoning_effort="low", sdk_retries=INDEXING_SDK_RETRIES,
        )
    except LLMNotConfiguredError:
        logger.info("No AI model is set up: tables are indexed with plain row text")
        return None
    remember_cache(config_service, logger)
    return llm


def _plain_enrichment(
    rows: Sequence[Sequence[str]], header_rows: int, headers: list[str], *, degraded: bool
) -> TableEnrichmentResult:
    data_rows = rows[header_rows:]
    headers = indexing_helpers.unique_column_keys(headers, fill_empty=False)
    return TableEnrichmentResult(
        summary=fallback_table_summary(headers, len(data_rows)),
        headers=headers,
        header_row_count=header_rows,
        descriptions=_simple_texts(data_rows, headers),
        degraded=degraded,
    )


def simple_table_enrichment(grid: Sequence[Sequence[Any]]) -> TableEnrichmentResult:
    """Build a table's row text without any LLM call (skip_table_enrichment=True)."""
    rows = normalize_grid(grid)
    if not rows:
        return TableEnrichmentResult()
    return TableEnrichmentResult(descriptions=_simple_texts(rows, []))


async def _first_call(
    llm: BaseChatModel,
    send_batch: Sequence[Sequence[str]],
    keys: Sequence[str],
    known_headers: Optional[list[str]],
) -> Optional[TableEnrichment]:
    """Summary, headers and the first batch's descriptions in one call. Raises if the call fails."""
    messages = table_enrichment_prompt.format_messages(
        row_count=len(send_batch),
        keyed_rows_data=format_rows_with_keys(list(send_batch), keys, numbered=True),
        header_context=_header_context(known_headers),
    )
    return await cached_model_call(
        KIND_TABLE_SUMMARY,
        llm=llm,
        template=table_enrichment_prompt,
        rendered=_rendered(messages),
        params={"flow": _FLOW_COMBINED, "count": len(send_batch)},
        call=lambda: invoke_with_structured_output_and_reflection(
            llm, messages, TableEnrichment, _REFLECTIONS, raise_call_errors=True,
            max_output_tokens=TABLE_CALL_MAX_OUTPUT_TOKENS, raise_oversized_number=True,
        ),
        encode=lambda enrichment: enrichment.model_dump(),
        decode=_strict_decode_keyed(TableEnrichment),
        # Rows the model left out must be asked for again, not replayed from the cache.
        cacheable=lambda enrichment: _complete(align_by_key(enrichment.descriptions, keys)),
    )


async def _summary_and_headers_call(
    llm: BaseChatModel, send_batch: Sequence[Sequence[str]], known_headers: Optional[list[str]]
) -> Optional[TableSummaryHeaders]:
    """Summary and headers alone, for a first call that did not give them. Raises if the call fails."""
    sample = list(send_batch[:SUMMARY_SAMPLE_ROWS])
    messages = table_summary_headers_prompt.format_messages(
        row_count=len(sample),
        numbered_rows_data=indexing_helpers.format_rows_with_index(sample),
        header_context=_header_context(known_headers),
    )
    return await cached_model_call(
        KIND_TABLE_SUMMARY,
        llm=llm,
        template=table_summary_headers_prompt,
        rendered=_rendered(messages),
        params={"flow": _FLOW_SUMMARY, "count": len(sample)},
        call=lambda: invoke_with_structured_output_and_reflection(
            llm, messages, TableSummaryHeaders, _REFLECTIONS, raise_call_errors=True,
            max_output_tokens=TABLE_CALL_MAX_OUTPUT_TOKENS, raise_oversized_number=True,
        ),
        encode=lambda answer: answer.model_dump(),
        decode=TableSummaryHeaders.model_validate,
        cacheable=lambda answer: bool((answer.summary or "").strip()),
    )


async def _stored_texts_for(rows: Sequence[Sequence[str]], known_header_row_count: Optional[int]) -> Optional[StoredTable]:
    """The rows of the stored table that can give this one the most texts, when this delivery offered any; ``None`` otherwise (no
    update, a table the stored version did not have, or two stored tables that tie, where neither is surely this one). Only one
    stored table is used, so a row equal to one of another table never takes that table's wording. Where the model decides the
    header, a row is looked for both ways."""
    tables = await previous_row_texts()
    if not tables:
        return None
    hypotheses = (1 if known_header_row_count else 0,) if known_header_row_count is not None else (0, 1)
    best, best_shared, tied = None, 0, False
    for header_rows in hypotheses:
        header = rows[0] if header_rows else None
        counts = Counter(row_digest(header, row) for row in rows[header_rows:])
        for table in tables:
            # A repeated row counts as many times as both versions hold it, never more than the stored table can give.
            shared = sum(min(n, len(table.get(digest, ()))) for digest, n in counts.items())
            if shared > best_shared:
                best, best_shared, tied = table, shared, False
            elif shared and shared == best_shared and table is not best:
                tied = True
    return None if tied else best


async def enrich_table_grid(
    llm: Optional[BaseChatModel],
    grid: Sequence[Sequence[Any]],
    *,
    logger: Logger,
    known_header_row_count: Optional[int] = None,
    describe_rows: bool = True,
    batch_size: Optional[int] = None,
) -> TableEnrichmentResult:
    """Enrich one table.

    Args:
        llm: ``None`` when no model is set up: the table gets plain text, not degraded.
        grid: All table rows, including the header row when there is one.
        known_header_row_count: 0 or 1 when the caller has ground truth (docling's
            ``column_header`` flag, a block's ``is_header``). ``None`` asks the LLM,
            which is the only case where the returned ``header_row_count`` can be 1.
        describe_rows: When False the LLM is still used for the summary and headers but
            rows fall back to ``generate_simple_row_text`` (the MAX_TABLE_ROWS_FOR_LLM
            cutoff). That cutoff is configuration, not a degradation, and is not counted.
        batch_size: Rows per description call; defaults to ``INDEXING_TABLE_ROW_BATCH_SIZE``.
    """
    rows = normalize_grid(grid)
    if not rows:
        return TableEnrichmentResult()
    batch_size = min(batch_size or table_row_batch_size(), MAX_KEYED_ROWS)

    known = known_header_row_count is not None
    known_headers: Optional[list[str]] = None
    if known:
        header_rows = 1 if known_header_row_count else 0
        known_headers = list(rows[0]) if header_rows else []
        send_rows = rows[header_rows:]
    else:
        header_rows = 0
        send_rows = rows

    first_batch = send_rows[:batch_size]

    if not first_batch:
        return TableEnrichmentResult(
            headers=indexing_helpers.unique_column_keys(known_headers or [], fill_empty=False),
            header_row_count=header_rows,
        )

    if llm is None:
        return _plain_enrichment(rows, header_rows, known_headers or [], degraded=False)

    def whole_table_fallback(reason: str) -> TableEnrichmentResult:
        # The unknown-header path has no header row to trust here: summary and headers
        # are exactly what failed.
        note_table_result(succeeded=False, degraded=True, reason=reason)
        note_degraded_rows(reason, len(rows) - header_rows)
        failed = _plain_enrichment(rows, header_rows, known_headers or [], degraded=True)
        failed.failed = True
        return failed

    stored = await _stored_texts_for(rows, known_header_row_count) if describe_rows else None
    first_keys = row_keys(first_batch)
    parsed: Optional[TableEnrichment] = None
    try:
        # A table the stored version had asks only for its summary and headers: its rows are matched to the stored ones below.
        if stored is None:
            parsed = await _first_call(llm, first_batch, first_keys, known_headers)
    except NonRetryableError:
        raise
    except _streaming.OversizedNumberError:
        # The summary follow-up would ask for the same header decision that just ran away.
        logger.warning("Table enrichment reply ran away; the table falls back to plain text")
        return whole_table_fallback("parse")
    except Exception as e:
        logger.warning(f"Table enrichment call failed: {e}")
        return whole_table_fallback("error")

    summary = ((parsed.summary if parsed else "") or "").strip()
    llm_headers = list(parsed.headers) if parsed and parsed.headers else []
    llm_header_rows = parsed.header_row_count if parsed else 0
    # The first reply may give the keyed rows and a header decision without a summary (or without
    # headers). Whatever it gave is kept; the missing parts are asked for on their own.
    summary_lost: Optional[str] = None
    if not summary or not (known or llm_headers):
        followup_error = False
        try:
            extra = await _summary_and_headers_call(llm, first_batch, known_headers)
        except NonRetryableError:
            raise
        except _streaming.OversizedNumberError:
            extra = None  # an unusable reply, already reported, not a failed call
        except Exception as e:
            logger.warning(f"Table summary call failed: {e}")
            extra, followup_error = None, True
        if extra is not None:
            if not summary:
                summary = (extra.summary or "").strip()
            if not known and not llm_headers:
                llm_headers = list(extra.headers or [])
                llm_header_rows = extra.header_row_count
        if not (known or llm_headers) or (not summary and parsed is None and stored is None):
            # Nothing of the first reply to keep (it did not parse), or no header decision on the
            # unknown path, where the header row cannot be told from data: the table falls back.
            return whole_table_fallback("error" if followup_error else "parse")
        if not summary:
            summary_lost = "error" if followup_error else "parse"

    if known:
        headers = known_headers or list(llm_headers)
    else:
        header_rows = 1 if llm_header_rows == 1 and len(rows) > 1 else 0
        headers = list(llm_headers)
    # One distinct name per column, so the stored headers and the row keys agree.
    headers = indexing_helpers.unique_column_keys(headers, fill_empty=False)

    data_rows = rows[header_rows:]
    if summary_lost is not None:
        # Only the summary is plain; the rest of the first reply stands.
        summary = fallback_table_summary(headers, len(data_rows))

    if not describe_rows:
        # The MAX_TABLE_ROWS_FOR_LLM cutoff is configuration, not a degradation.
        note_rows_over_budget(len(data_rows))
        result = TableEnrichmentResult(
            summary=summary,
            headers=headers,
            header_row_count=header_rows,
            descriptions=_simple_texts(data_rows, headers),
            degraded=summary_lost is not None,
        )
        note_table_result(succeeded=summary_lost is None, degraded=summary_lost is not None, reason=summary_lost)
        return result

    data_dicts = [_row_dict(row, headers) for row in data_rows]
    digests = [row_digest(rows[0] if header_rows else None, row) for row in data_rows]
    if stored is not None:
        texts, run = await _describe_unmatched(llm, summary, logger, data_dicts, digests, stored, batch_size)
    else:
        texts, run = await _describe_all(llm, summary, logger, data_dicts, parsed, first_keys, 0 if known else header_rows, batch_size)

    fallback = _simple_texts(data_rows, headers)
    descriptions = [
        t if _usable(t) else fallback[i] for i, t in enumerate(texts[: len(data_rows)])
    ]
    # Never let a short list desynchronize descriptions from rows.
    descriptions.extend(fallback[len(descriptions) :])

    degraded = bool(run.rows_by_reason) or summary_lost is not None
    for reason, count in run.rows_by_reason.items():
        note_degraded_rows(reason, count)
    result = TableEnrichmentResult(
        summary=summary,
        headers=headers,
        header_row_count=header_rows,
        descriptions=descriptions,
        degraded=degraded,
        row_digests=[digest if i < len(texts) and _usable(texts[i]) else None for i, digest in enumerate(digests)],
    )
    note_table_result(
        succeeded=not degraded,
        degraded=degraded,
        reason=run.rows_by_reason.most_common(1)[0][0] if run.rows_by_reason else summary_lost,
    )
    return result


async def _describe_all(
    llm: BaseChatModel,
    summary: str,
    logger: Logger,
    data_dicts: list[dict[str, str]],
    parsed: Optional[TableEnrichment],
    first_keys: Sequence[str],
    skip_sent: int,
    batch_size: int,
) -> tuple[list[Optional[str]], _Run]:
    """Every data row's description: the first call's own rows when it answered, then the rest in batches. ``None`` = fall back."""
    first_data_rows = 0
    first_answer: Optional[list[Optional[str]]] = None
    if parsed is not None:
        # Keys tie each description to its row whatever else the call got wrong.
        # On the unknown path the sent rows start with the header row, which is not data.
        sent = align_by_key(parsed.descriptions, first_keys)
        first_answer = sent[skip_sent:]
        first_data_rows = len(first_answer)

    rest = data_dicts[first_data_rows:]
    batches = [rest[i : i + batch_size] for i in range(0, len(rest), batch_size)]
    run = _Run(llm, summary, logger, RetryBudget.for_batches(len(batches) + 1))

    texts: list[Optional[str]] = []
    jobs: list[Callable[[], Any]] = []
    spans: list[int] = []
    if first_answer is not None and first_data_rows:
        jobs.append(lambda: _resolve_batch(run, data_dicts[:first_data_rows], first_answer))
        spans.append(first_data_rows)
    for batch in batches:
        jobs.append(lambda batch=batch: _describe_batch(run, batch))
        spans.append(len(batch))

    results = await _gather_limited(MAX_CONCURRENT_ROW_BATCHES, jobs)
    for span, result in zip(spans, results):
        if isinstance(result, BaseException):
            logger.warning("Row batch failed, using simple text: %s", type(result).__name__)
            run.note_degraded("error", span, 0, span)
            texts.extend([None] * span)
        else:
            texts.extend(result)
    return texts, run


async def _describe_unmatched(
    llm: BaseChatModel,
    summary: str,
    logger: Logger,
    data_dicts: list[dict[str, str]],
    digests: list[str],
    stored: StoredTable,
    batch_size: int,
) -> tuple[list[Optional[str]], _Run]:
    """Every data row's description: a row the stored version had keeps its text, the others are described in batches."""
    left = {digest: iter(texts) for digest, texts in stored.items()}
    # A repeated row takes the stored texts of its repeats in order, one each; a repeat more than were stored is described.
    texts: list[Optional[str]] = [next(left.get(digest, iter(())), None) for digest in digests]
    pending = [i for i, text in enumerate(texts) if text is None]
    batches = [pending[i : i + batch_size] for i in range(0, len(pending), batch_size)]
    run = _Run(llm, summary, logger, RetryBudget.for_batches(len(batches)))
    results = await _gather_limited(
        MAX_CONCURRENT_ROW_BATCHES, [lambda batch=batch: _describe_batch(run, [data_dicts[i] for i in batch]) for batch in batches]
    )
    for batch, result in zip(batches, results):
        if isinstance(result, BaseException):
            logger.warning("Row batch failed, using simple text: %s", type(result).__name__)
            run.note_degraded("error", len(batch), 0, len(batch))
            continue
        for i, text in zip(batch, result):
            texts[i] = text
    # Counts only: a row's text is never logged.
    logger.info("Table rows: %d kept from the stored version, %d described", len(texts) - len(pending), len(pending))
    return texts, run


def describe_rows_within_budget(row_counts: Sequence[int]) -> list[bool]:
    """Per table, in document order: whether its rows get LLM descriptions.

    A table qualifies while the record's running row total, including it, stays
    within ``max_table_rows_for_llm()`` - the rule the Excel and CSV parsers apply.
    """
    budget = max_table_rows_for_llm()
    flags: list[bool] = []
    total = 0
    for count in row_counts:
        total += count
        flags.append(total <= budget)
    return flags


async def enrich_tables(
    llm: Optional[BaseChatModel],
    grids: Sequence[Sequence[Sequence[Any]]],
    *,
    logger: Logger,
    known_header_row_counts: Optional[Sequence[Optional[int]]] = None,
    describe_rows: Optional[Sequence[bool]] = None,
    max_concurrent: int = MAX_CONCURRENT_TABLES,
) -> list[TableEnrichmentResult]:
    """Enrich several tables concurrently. Results are in input order.

    ``describe_rows`` defaults to the record's row budget over *grids*, in order. A table that
    fails for an ordinary reason gets plain text; a ``NonRetryableError`` from any table is
    raised unchanged and the other tables are cancelled.
    """
    if not grids:
        return []
    if describe_rows is None:
        describe_rows = describe_rows_within_budget([len(grid) for grid in grids])

    jobs = [
        lambda i=i, grid=grid: enrich_table_grid(
            llm,
            grid,
            logger=logger,
            known_header_row_count=(
                known_header_row_counts[i] if known_header_row_counts else None
            ),
            describe_rows=describe_rows[i],
        )
        for i, grid in enumerate(grids)
    ]

    results = await _gather_limited(max_concurrent, jobs)

    enriched: list[TableEnrichmentResult] = []
    for grid, result in zip(grids, results):
        if isinstance(result, BaseException):
            logger.warning(f"Table enrichment failed, using simple text: {result}")
            rows = normalize_grid(grid)
            enriched.append(
                TableEnrichmentResult(
                    descriptions=_simple_texts(rows, []), degraded=True, failed=True
                )
            )
            # enrich_table_grid already noted success/failure; gather exceptions did not.
            note_table_result(succeeded=False, degraded=True, reason="error")
            note_degraded_rows("error", len(rows))
        else:
            enriched.append(result)
    return enriched


@dataclass
class TableEnhancementStats:
    attempted: int = 0
    succeeded: int = 0
    failed: int = 0


def _collect_table_row_blocks(
    block_containers: Any, table_group: Any  # noqa: ANN401
) -> list[Any]:
    """TABLE_ROW blocks belonging to *table_group*, in child order."""
    from app.models.blocks import BlockContainerIndex, BlockGroupChildren, BlockType

    row_blocks: list[Any] = []
    children = table_group.children
    if not children:
        return row_blocks

    if isinstance(children, BlockGroupChildren):
        for range_obj in children.block_ranges:
            for block_index in range(range_obj.start, range_obj.end + 1):
                if 0 <= block_index < len(block_containers.blocks):
                    block = block_containers.blocks[block_index]
                    if block.type == BlockType.TABLE_ROW:
                        row_blocks.append(block)
    elif isinstance(children, list):
        for child_idx in children:
            if isinstance(child_idx, BlockContainerIndex) and child_idx.block_index is not None:
                block_index = child_idx.block_index
                if 0 <= block_index < len(block_containers.blocks):
                    block = block_containers.blocks[block_index]
                    if block.type == BlockType.TABLE_ROW:
                        row_blocks.append(block)
    return row_blocks


def _fill_without_llm(
    table_group: Any,  # noqa: ANN401
    data_row_blocks: list[Any],
    header_cells: Optional[Sequence[Any]],
) -> None:
    """Plain summary and row text for one TABLE BlockGroup when no model is set up.

    Parsers that keep column names on the group rather than in a header row (the HTML
    parser) already wrote header-aware row text; without any column names the parser's
    row text is the best there is, so it is kept.
    """
    if table_group.data is None:
        table_group.data = {}
    if header_cells is not None:
        headers = [cell_text(c) for c in header_cells]
    else:
        headers = [str(h) for h in table_group.data.get("column_headers") or []]
        if not headers and table_group.table_metadata:
            headers = list(table_group.table_metadata.column_names or [])

    for rb in data_row_blocks:
        if rb.data is None:
            continue
        cells = [cell_text(c) for c in rb.data.get("cells") or []]
        text = indexing_helpers.generate_simple_row_text(_row_dict(cells, headers))
        if headers:
            rb.data["row_natural_language_text"] = text
        else:
            rb.data.setdefault("row_natural_language_text", text)

    captions = table_group.table_metadata.captions if table_group.table_metadata else None
    table_group.data["table_summary"] = summary_with_captions(
        fallback_table_summary(headers, len(data_row_blocks)), captions
    )
    table_group.data["column_headers"] = headers


async def _enhance_one_table(
    table_group: Any,  # noqa: ANN401
    block_containers: Any,  # noqa: ANN401
    llm: Optional[BaseChatModel],
    logger: Logger,
    *,
    describe_rows: bool = True,
) -> Optional[bool]:
    """Fill in one TABLE BlockGroup's summary/headers/row text in place.

    Returns None when there was nothing to enrich (no ``table_markdown``, which the
    caller does not count as an attempt), True on success, False on failure (already
    degraded to ``generate_simple_row_text`` in place).
    """
    row_blocks = _collect_table_row_blocks(block_containers, table_group)
    is_header_flags = [
        bool(rb.table_row_metadata and rb.table_row_metadata.is_header) for rb in row_blocks
    ]
    data_row_blocks = [rb for rb, is_header in zip(row_blocks, is_header_flags) if not is_header]
    if not data_row_blocks:
        return None

    # cells are raw positional values (block.data["cells"]), same shape the LLM sees
    # regardless of whether column names are known yet.
    #
    # The grid must line up with what enrich_table_grid describes, because the caller
    # pairs descriptions against data_row_blocks below. Passing the full grid while
    # claiming known_header_row_count=0 made it describe the header too, shifting every
    # description onto the previous row and dropping the last row's on the floor.
    header_is_first = bool(is_header_flags) and is_header_flags[0] and not any(is_header_flags[1:])
    if header_is_first:
        # Declaring the header lets the enricher read the real column names off it and
        # still describe only the data rows.
        grid = [(rb.data or {}).get("cells") or [] for rb in row_blocks]
        known_header_row_count = 1
    else:
        # No header, or one in an unexpected position: send only the rows the
        # descriptions map back to.
        grid = [(rb.data or {}).get("cells") or [] for rb in data_row_blocks]
        known_header_row_count = 0

    if llm is None:
        header_cells = (row_blocks[0].data or {}).get("cells") if header_is_first else None
        _fill_without_llm(table_group, data_row_blocks, header_cells)
        return True

    try:
        enrichment = await enrich_table_grid(
            llm,
            grid,
            logger=logger,
            known_header_row_count=known_header_row_count,
            describe_rows=describe_rows,
        )
    except NonRetryableError:
        raise
    except Exception as e:
        logger.warning(f"Table {table_group.index} enrichment failed: {e}")
        enrichment = None

    def _fallback_text(rb: Any) -> str:  # noqa: ANN401
        cells = [cell_text(c) for c in (rb.data or {}).get("cells") or []]
        return indexing_helpers.generate_simple_row_text(_row_dict(cells, []))

    if enrichment is None:
        for rb in data_row_blocks:
            if rb.data is not None:
                rb.data.setdefault("row_natural_language_text", _fallback_text(rb))
        return False

    if table_group.data is None:
        table_group.data = {}
    captions = table_group.table_metadata.captions if table_group.table_metadata else None
    table_group.data["table_summary"] = summary_with_captions(enrichment.summary, captions)
    table_group.data["column_headers"] = enrichment.headers

    # zip() truncates silently, so a count mismatch would misalign every row without
    # any signal. The guard inside enrich_table_grid only catches descriptions being
    # shorter than the rows; it cannot see this caller-side pairing.
    if len(enrichment.descriptions) != len(data_row_blocks):
        logger.warning(
            "Table %s: %d description(s) for %d data row(s) — rows may be misaligned",
            table_group.index, len(enrichment.descriptions), len(data_row_blocks),
        )

    for i, (rb, description) in enumerate(zip(data_row_blocks, enrichment.descriptions)):
        if rb.data is not None:
            rb.data["row_natural_language_text"] = description
            with_row_digest(rb.data, enrichment.row_digests, i)

    for rb in data_row_blocks[len(enrichment.descriptions):]:
        if rb.data is not None:
            rb.data.setdefault("row_natural_language_text", _fallback_text(rb))

    # Row or summary fallbacks leave the table counted as enriched; only a table that fell back as a whole fails.
    return not enrichment.failed


async def enhance_tables_with_llm(
    block_containers: Any,  # noqa: ANN401 - BlocksContainer, kept loose to avoid the import
    config_service: Any,  # noqa: ANN401
    logger: Logger,
    *,
    llm: Optional[BaseChatModel] = None,
    max_concurrent_tables: int = MAX_CONCURRENT_TABLES,
    fail_on_all_errors: bool = True,
) -> TableEnhancementStats:
    """Enrich every TABLE BlockGroup in *block_containers*, concurrently.

    Per-table failures degrade to ``generate_simple_row_text`` and are counted, not
    raised - the blocks are already structurally correct, only the description text is
    lost. Only raises when every attempted table failed, since that usually means the
    LLM is unreachable and the record should be retried rather than silently indexed
    with no table content at all. With no model set up, every table gets plain text and
    nothing raises: there is no model to retry against. A ``NonRetryableError`` (the request
    can never succeed as sent) is not a failed table: it passes through unchanged, the other
    tables are cancelled, and the record ends as non-retryable.
    """
    from app.models.blocks import GroupType

    table_groups = [bg for bg in block_containers.block_groups if bg.type == GroupType.TABLE]
    if not table_groups:
        return TableEnhancementStats()

    if llm is None:
        llm = await indexing_llm_or_none(config_service, logger)
    if llm is not None:
        logger.info(f"Enhancing {len(table_groups)} tables with LLM summaries")

    # The row budget runs over the record's tables in document order.
    describe_flags = describe_rows_within_budget([
        len(_collect_table_row_blocks(block_containers, table_group))
        for table_group in table_groups
    ])

    with track_indexing_enrichment(logger, label="block-tables"):
        results = await _gather_limited(
            max_concurrent_tables,
            [
                lambda table_group=table_group, describe=describe: _enhance_one_table(
                    table_group, block_containers, llm, logger, describe_rows=describe
                )
                for table_group, describe in zip(table_groups, describe_flags)
            ],
        )

        stats = TableEnhancementStats()
        for table_group, result in zip(table_groups, results):
            if isinstance(result, BaseException):
                logger.error(f"Error enhancing table {table_group.index}: {result}")
                stats.attempted += 1
                stats.failed += 1
            elif result is None:
                continue
            else:
                stats.attempted += 1
                if result:
                    stats.succeeded += 1
                else:
                    stats.failed += 1

        if stats.attempted:
            logger.info(
                f"Table enhancement: {stats.succeeded}/{stats.attempted} succeeded, "
                f"{stats.failed} fell back to plain text as a whole"
            )
            if stats.failed == stats.attempted and fail_on_all_errors:
                raise RuntimeError(
                    f"All {stats.attempted} tables failed LLM enhancement"
                )

    return stats


async def get_rows_text(
    llm, table_data: dict, table_summary: str, column_headers: list[str]
) -> tuple[list[str], list[list[dict]]]:
    """Describe the rows of ``table_data["grid"]`` (minus the header row when ``column_headers``
    is given), in chunks of at most ``MAX_KEYED_ROWS`` rows.

    A row the model left out, or a chunk whose answer cannot be parsed, gets plain ``column:
    value`` text for that row alone: never another row's description and never a dict repr.
    Each chunk gets one call, plus one structured-output reflection when its answer does not
    parse, and is never split or retried (``enrich_table_grid`` and ``describe_row_dicts`` add
    that). A failed call raises, and nothing is returned for the chunks that had already
    finished: the caller gets all the texts or the error, never a partial list.
    """
    table = table_data.get("grid")
    if not table:
        return [], []
    # Skip first row if column_headers provided (first row is header)
    table_rows = table[1:] if column_headers else table
    rows = [[cell_text(cell) for cell in row] for row in table_rows]
    run = _Run(llm, table_summary, logging.getLogger(__name__), RetryBudget(0))
    rows_data = [_row_dict(row, column_headers) for row in rows]
    fallback = _simple_texts(rows, column_headers)
    texts: list[str] = []
    for start in range(0, len(rows_data), MAX_KEYED_ROWS):  # no call is larger than the size limit
        chunk = rows_data[start : start + MAX_KEYED_ROWS]
        try:
            answer = await _call_row_batch(run, chunk)
        except (_ReplyCutOff, _ReplyRanAway):
            answer = None  # an unusable reply: the chunk's rows get plain text
        except _ModelCallFailed as e:
            raise (e.__cause__ or e) from None
        if answer is None:
            texts.extend(fallback[start : start + len(chunk)])
        else:
            texts.extend(a if _usable(a) else fallback[start + i] for i, a in enumerate(answer))
    return texts, table_rows


def fallback_table_summary(headers: "Sequence[object]", row_count: int) -> str:
    """A table summary written without the model, for when it cannot write one: the record is
    still indexed, with a plainer summary."""
    columns = ", ".join(str(h) for h in headers if str(h).strip()) or "unnamed columns"
    rows = f"{row_count} row" + ("" if row_count == 1 else "s")
    return f"A table with {rows} and columns: {columns}."
