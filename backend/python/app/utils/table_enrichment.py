"""Concurrent LLM enrichment of tables (summary, headers, per-row descriptions).

A table that fits in one batch costs a single LLM call: the combined prompt returns the
summary, the column headers and the row descriptions together. Larger tables spend that
first call on the summary plus the first batch, then fan the remaining batches out in
parallel using the summary as context.

Enrichment is best-effort. Any failure degrades a table to ``generate_simple_row_text``
rather than failing the record, because the blocks themselves are already correct - only
the natural-language text quality is lost.
"""

import json
from dataclasses import dataclass, field
from logging import Logger
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.language_models.chat_models import BaseChatModel

from app.modules.parsers.excel.prompt_template import (
    RowDescriptions,
    TableEnrichment,
    row_text_prompt,
    row_text_prompt_for_csv,
    table_enrichment_prompt,
)
from app.utils.concurrency import (
    MAX_CONCURRENT_ROW_BATCHES,
    MAX_CONCURRENT_TABLES,
    TABLE_ROW_BATCH_SIZE,
    gather_with_concurrency,
)

# Imported as a module, not by name: ~30 tests patch
# module-level `from ... import name` here would bind its own reference and silently
# bypass those patches.
from app.utils import indexing_helpers
from app.utils.indexing_metrics import note_table_result, track_indexing_enrichment
from app.utils.llm import get_llm_for_role
from app.utils.streaming import (
    invoke_with_count_validation_and_reflection,
    invoke_with_row_descriptions_and_reflection,
    invoke_with_structured_output_and_reflection,
)


@dataclass
class TableEnrichmentResult:
    """Enrichment for one table. ``descriptions`` aligns with the data rows."""

    summary: str = ""
    headers: List[str] = field(default_factory=list)
    header_row_count: int = 0
    descriptions: List[str] = field(default_factory=list)
    degraded: bool = False


def cell_text(cell: Any) -> str:  # noqa: ANN401
    """Normalize a grid cell, which may be a dict, ``None`` or a scalar."""
    if isinstance(cell, dict):
        return str(cell.get("text", "") or "")
    return "" if cell is None else str(cell)


def normalize_grid(grid: Sequence[Sequence[Any]]) -> List[List[str]]:
    return [[cell_text(cell) for cell in row] for row in grid]


def _row_dict(row: Sequence[str], headers: Sequence[str]) -> Dict[str, str]:
    return {
        (headers[i] if i < len(headers) and headers[i] else f"Column_{i + 1}"): value
        for i, value in enumerate(row)
    }


def _simple_texts(rows: Sequence[Sequence[str]], headers: Sequence[str]) -> List[str]:
    return [
        indexing_helpers.generate_simple_row_text(_row_dict(row, headers)) for row in rows
    ]


async def _describe_batch(
    rows: Sequence[Sequence[str]],
    headers: Sequence[str],
    summary: str,
    llm
) -> List[str]:
    # get_rows_text skips row 0 only when headers is truthy — never prepend [].
    grid = [list(headers), *rows] if headers else list(rows)
    descriptions, _ = await get_rows_text(llm, {"grid": grid}, summary, list(headers))
    if len(descriptions) < len(rows):
        descriptions = list(descriptions) + _simple_texts(rows[len(descriptions) :], headers)
    return descriptions[: len(rows)]

def _header_context(known_headers: Optional[List[str]]) -> str:
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


async def enrich_table_grid(
    llm: BaseChatModel,
    grid: Sequence[Sequence[Any]],
    *,
    logger: Logger,
    known_header_row_count: Optional[int] = None,
    describe_rows: bool = True,
    batch_size: int = TABLE_ROW_BATCH_SIZE,
) -> TableEnrichmentResult:
    """Enrich one table.

    Args:
        grid: All table rows, including the header row when there is one.
        known_header_row_count: 0 or 1 when the caller has ground truth (docling's
            ``column_header`` flag, a block's ``is_header``). ``None`` asks the LLM,
            which is the only case where the returned ``header_row_count`` can be 1.
        describe_rows: When False the LLM is still used for the summary and headers but
            rows fall back to ``generate_simple_row_text`` (the MAX_TABLE_ROWS_FOR_LLM
            cutoff).
    """
    rows = normalize_grid(grid)
    if not rows:
        return TableEnrichmentResult()

    known = known_header_row_count is not None
    known_headers: Optional[List[str]] = None
    if known:
        header_rows = 1 if known_header_row_count else 0
        known_headers = list(rows[0]) if header_rows else []
        send_rows = rows[header_rows:]
    else:
        header_rows = 0
        send_rows = rows

    first_batch = send_rows[:batch_size]
    remaining = send_rows[batch_size:]

    if not first_batch:
        return TableEnrichmentResult(headers=known_headers or [], header_row_count=header_rows)

    messages = table_enrichment_prompt.format_messages(
        row_count=len(first_batch),
        numbered_rows_data=indexing_helpers.format_rows_with_index(first_batch),
        header_context=_header_context(known_headers),
    )

    parsed: Optional[TableEnrichment] = None
    try:
        parsed = await invoke_with_count_validation_and_reflection(
            llm, messages, TableEnrichment, "descriptions", len(first_batch)
        )
    except Exception as e:
        logger.warning(f"Table enrichment call failed: {e}")

    if parsed is None:
        headers = known_headers or []
        data_rows = rows[header_rows:]
        result = TableEnrichmentResult(
            summary="",
            headers=headers,
            header_row_count=header_rows,
            descriptions=_simple_texts(data_rows, headers),
            degraded=True,
        )
        note_table_result(succeeded=False, degraded=True)
        return result

    summary = parsed.summary or ""
    if known:
        headers = known_headers or list(parsed.headers or [])
    else:
        header_rows = 1 if parsed.header_row_count == 1 and len(rows) > 1 else 0
        headers = list(parsed.headers or [])

    # On the unknown-header path the LLM was sent the header row and described it;
    # drop that leading description so they align with the data rows. On the known
    # path the header was never sent, so descriptions are already data-only.
    first_descriptions = list(parsed.descriptions)[0 if known else header_rows :]
    data_rows = rows[header_rows:]

    if not describe_rows:
        result = TableEnrichmentResult(
            summary=summary,
            headers=headers,
            header_row_count=header_rows,
            descriptions=_simple_texts(data_rows, headers),
        )
        note_table_result(succeeded=True)
        return result

    descriptions = first_descriptions
    degraded = False
    if remaining:
        batches = [
            remaining[i : i + batch_size] for i in range(0, len(remaining), batch_size)
        ]
        results = await gather_with_concurrency(
            MAX_CONCURRENT_ROW_BATCHES,
            *(_describe_batch( batch, headers, summary,llm) for batch in batches),
            return_exceptions=True,
        )
        for batch, result in zip(batches, results):
            if isinstance(result, BaseException):
                logger.warning(f"Row batch failed, using simple text: {result}")
                descriptions.extend(_simple_texts(batch, headers))
                degraded = True
            else:
                descriptions.extend(result)

    # Never let a short response desynchronize descriptions from rows.
    if len(descriptions) < len(data_rows):
        descriptions.extend(_simple_texts(data_rows[len(descriptions):], headers))
        degraded = True

    result = TableEnrichmentResult(
        summary=summary,
        headers=headers,
        header_row_count=header_rows,
        descriptions=descriptions[: len(data_rows)],
        degraded=degraded,
    )
    note_table_result(succeeded=not degraded, degraded=degraded)
    return result


async def enrich_tables(
    llm: BaseChatModel,
    grids: Sequence[Sequence[Sequence[Any]]],
    *,
    logger: Logger,
    known_header_row_counts: Optional[Sequence[Optional[int]]] = None,
    describe_rows: Optional[Sequence[bool]] = None,
    max_concurrent: int = MAX_CONCURRENT_TABLES,
) -> List[TableEnrichmentResult]:
    """Enrich several tables concurrently. Results are in input order."""
    if not grids:
        return []

    coros = [
        enrich_table_grid(
            llm,
            grid,
            logger=logger,
            known_header_row_count=(
                known_header_row_counts[i] if known_header_row_counts else None
            ),
            describe_rows=describe_rows[i] if describe_rows else True,
        )
        for i, grid in enumerate(grids)
    ]

    results = await gather_with_concurrency(max_concurrent, *coros, return_exceptions=True)

    enriched: List[TableEnrichmentResult] = []
    for grid, result in zip(grids, results):
        if isinstance(result, BaseException):
            logger.warning(f"Table enrichment failed, using simple text: {result}")
            rows = normalize_grid(grid)
            enriched.append(
                TableEnrichmentResult(
                    descriptions=_simple_texts(rows, []), degraded=True
                )
            )
            # enrich_table_grid already noted success/failure; gather exceptions did not.
            note_table_result(succeeded=False, degraded=True)
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
) -> List[Any]:
    """TABLE_ROW blocks belonging to *table_group*, in child order."""
    from app.models.blocks import BlockContainerIndex, BlockGroupChildren, BlockType

    row_blocks: List[Any] = []
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


async def _enhance_one_table(
    table_group: Any, block_containers: Any, llm: BaseChatModel, logger: Logger  # noqa: ANN401
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
    grid = [(rb.data or {}).get("cells") or [] for rb in row_blocks]

    try:
        enrichment = await enrich_table_grid(
            llm, grid, logger=logger, known_header_row_count=0
        )
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
    table_group.data["table_summary"] = enrichment.summary
    table_group.data["column_headers"] = enrichment.headers

    for rb, description in zip(data_row_blocks, enrichment.descriptions):
        if rb.data is not None:
            rb.data["row_natural_language_text"] = description

    for rb in data_row_blocks[len(enrichment.descriptions):]:
        if rb.data is not None:
            rb.data.setdefault("row_natural_language_text", _fallback_text(rb))

    return not enrichment.degraded


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
    with no table content at all.
    """
    from app.models.blocks import GroupType

    table_groups = [bg for bg in block_containers.block_groups if bg.type == GroupType.TABLE]
    if not table_groups:
        return TableEnhancementStats()

    if llm is None:
        llm, _ = await get_llm_for_role(config_service, "indexing", reasoning_effort="low")

    logger.info(f"Enhancing {len(table_groups)} tables with LLM summaries")

    with track_indexing_enrichment(logger, label="block-tables"):
        results = await gather_with_concurrency(
            max_concurrent_tables,
            *(
                _enhance_one_table(table_group, block_containers, llm, logger)
                for table_group in table_groups
            ),
            return_exceptions=True,
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
                f"{stats.failed} degraded"
            )
            if stats.failed == stats.attempted and fail_on_all_errors:
                raise RuntimeError(
                    f"All {stats.attempted} tables failed LLM enhancement"
                )

    return stats


async def get_rows_text(
    llm, table_data: dict, table_summary: str, column_headers: list[str]
) -> Tuple[List[str], List[List[dict]]]:
    """Convert multiple rows into natural language text using context from summaries in a single prompt"""
    table = table_data.get("grid")
    if table:
        try:
            # Skip first row if column_headers provided (first row is header)
            if column_headers:
                table_rows = table[1:]
            else:
                table_rows = table

            rows_data = [
                {
                    column_headers[i] if column_headers and i<len(column_headers) else f"Column_{i+1}": (
                        cell.get("text", "") if isinstance(cell, dict) else cell
                    )
                    for i, cell in enumerate(row)
                }
                for row in table_rows
            ]

            # Get natural language text from LLM with retry
            messages = row_text_prompt.format_messages(
                table_summary=table_summary, rows_data=json.dumps(rows_data, indent=2, ensure_ascii=False)
            )

            # Default to string representations of rows
            descriptions = [str(row) for row in rows_data]

            # Use centralized utility with reflection
            parsed_response = await invoke_with_structured_output_and_reflection(
                llm, messages, RowDescriptions
            )

            if parsed_response is not None and parsed_response.descriptions:
                descriptions = parsed_response.descriptions

            return descriptions, table_rows
        except Exception:
            raise
    else:
        return [], []