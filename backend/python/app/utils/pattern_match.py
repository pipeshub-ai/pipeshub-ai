"""Shared pattern-match helpers for internal search paths (chatbot + retrieval)."""

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch,
    _validate_command,
    is_local_storage,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.chat_helpers import get_flattened_results, get_record

_PATTERN_MATCH_TIMEOUT = 30
_LLM_GREP_TIMEOUT = 15.0
_MAX_PATTERN_MATCH_RECORDS = 50
# Fallback budget when a caller has no per-request limit to pass (e.g. limit=None).
# Pattern match fans out per-connector (max_results=10 each) and then expands each
# accessible record into one synthetic result per block, so an unbounded record
# (hundreds of blocks) can otherwise blow up the LLM context on its own.
# Matches retrieval.py's `_build_filter_groups` default `adjusted_limit` (50 for
# the <=1 source case) and ChatQuery.limit's own default, so pattern-match and
# semantic search share the same budget baseline across both call paths.
DEFAULT_PATTERN_MATCH_BLOCK_BUDGET = 50

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need",
    "i", "me", "my", "we", "us", "our", "you", "your", "he", "she", "it",
    "they", "them", "their", "this", "that", "these", "those",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "and", "or", "but", "not", "no", "nor", "for", "to", "of", "in",
    "on", "at", "by", "with", "from", "about", "into", "through",
    "if", "then", "so", "as", "than", "also", "just", "any", "all",
    "some", "each", "every", "very", "much", "more", "most",
    "tell", "show", "find", "get", "give", "know", "think", "see",
    "look", "want", "say", "make", "go", "take",
})


_ALLOWED_GREP_BINARIES = frozenset({"grep", "egrep", "fgrep", "rg"})
_MAX_GREP_COMMAND_LENGTH = 1000
_MAX_GREP_OUTPUT_LINES = 200
_MAX_GREP_STDOUT_BYTES = 50_000


def validate_grep_command(raw_command: str) -> str | None:
    """Lightweight validation that the LLM-provided command is a grep search.

    Checks structural validity and that the first binary is a grep variant.
    Full security validation (injection, dangerous flags, binary allowlist
    for pipe stages) is handled by ``_validate_command`` in storage_search.py
    which runs before subprocess execution.

    Returns the cleaned command or None if rejected.
    """
    command = raw_command.strip()
    if not command:
        return None
    if len(command) > _MAX_GREP_COMMAND_LENGTH:
        return None
    for ch in ("`", "$", ";", "\n", "\r", "\x00"):
        if ch in command:
            return None
    for seq in ("&&", "||", ">>", "<(", ">(", "$(", "${"):
        if seq in command:
            return None
    segments = _split_pipe_outside_quotes(command)
    if segments is None:
        return None
    for segment in segments:
        if not segment:
            return None
        words = segment.split()
        if not words or words[0] not in _ALLOWED_GREP_BINARIES:
            return None
    return command


def _split_pipe_outside_quotes(command: str) -> list[str] | None:
    """Split a command on ``|`` only when the pipe is outside quotes.

    Returns the list of segments, or None if quotes are unbalanced.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "\\" and i + 1 < len(command):
            current.append(ch)
            current.append(command[i + 1])
            i += 1
        elif ch == "|" and not in_single and not in_double:
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
        i += 1
    if in_single or in_double:
        return None
    segments.append("".join(current).strip())
    return segments


def build_grep_command_from_query(query: str) -> str | None:
    """Extract keywords from a user query and build a grep command.

    Uses ``-c`` (count mode) so ``find_records`` can rank matched files
    by keyword density instead of returning them in arbitrary filesystem
    order.

    Returns None if no meaningful keywords (>= 3 chars, not stop words)
    can be extracted.
    """
    words = re.findall(r"[a-zA-Z0-9_-]+", query.lower())
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) >= 3]
    if not keywords:
        return None
    keywords = keywords[:5]
    pattern = r"\|".join(keywords)
    return f'grep -rci "{pattern}" .'


class GrepCommandResult(BaseModel):
    reasoning: str
    grep_commands: list[str]


_MAX_LLM_GREP_COMMANDS = 3

_GREP_GENERATION_SYSTEM_PROMPT = """\
You are generating filesystem search commands to find JSON documents relevant to a user query.

Think step by step:
1. What specific terms, product names, features, or concepts MUST appear in documents that answer this query?
2. Should ALL terms appear in the same file (AND), or ANY of them (OR)?
3. What's the most precise grep command to find those files?
4. Would a DIFFERENT search angle (different terms, synonyms, related concepts) find additional relevant documents that the first command would miss?

How many commands to return:
- Return exactly 1 command for most queries. One well-crafted command is usually sufficient.
- Return 2-3 commands ONLY when genuinely different search strategies would surface different documents. Examples:
  - A query about "OAuth SSO login" might need one command for "oauth\\|sso" and another for "saml\\|authentication"
  - A query about "revenue Q3 2024" might need one for "revenue\\|earnings" and another for "Q3\\|third.quarter\\|2024"
- Do NOT return multiple commands that search for subsets of the same terms — that is redundant.
- Do NOT return multiple commands just because the query has several words — combine related terms into one OR pattern.
- Each command must find documents the OTHER commands would miss. If command 2 would match a strict subset of command 1's results, drop it.

Command rules:
- Search current directory: .
- Always use case-insensitive flag (-i)
- For the final grep in the chain, use -ci flags (count + case-insensitive) so results can be ranked by relevance
- Single concept: grep -rci "term" .
- Multiple required terms (AND): grep -rli "term1" . | xargs grep -ci "term2"
- Alternative terms (OR): grep -rci "term1\\|term2" .
- Combined: grep -rli "term1" . | xargs grep -ci "term2\\|term3"
- Allowed binaries: grep, egrep, fgrep, rg, xargs ONLY
- No shell operators: ; && || $ ` > <
- Max 1000 characters per command
- Prefer specific product/feature names over generic words
- Do NOT include common words like "how", "what", "setup", "use" as search terms"""


def _pre_validate_llm_grep(command: str) -> bool:
    """Lightweight pre-validation before the full _validate_command runs later."""
    if not command or not command.strip():
        return False
    command = command.strip()
    if len(command) > _MAX_GREP_COMMAND_LENGTH:
        return False
    for ch in ("`", "$", ";", "\n", "\r", "\x00"):
        if ch in command:
            return False
    for seq in ("&&", "||", ">>", "<(", ">(", "$(", "${"):
        if seq in command:
            return False
    first_word = command.split()[0] if command.split() else ""
    if first_word not in {"grep", "egrep", "fgrep", "rg"}:
        return False
    return True


async def generate_grep_command_via_llm(
    query: str,
    llm: BaseChatModel,
    logger_instance: logging.Logger,
) -> list[str] | None:
    """Generate targeted grep commands via LLM structured output.

    Returns a list of 1-3 validated grep command strings on success,
    None on timeout, LLM failure, or when all commands fail pre-validation.
    The caller is responsible for full security validation via ``_validate_command``.

    Uses LangChain's ``ainvoke`` with Opik callbacks so the call appears
    in the Opik trace alongside the rest of the agent loop.
    """
    from app.agent_loop_lib.transport.opik_tracing import build_langchain_opik_callbacks
    from app.utils.streaming import _apply_structured_output

    messages = [
        SystemMessage(content=_GREP_GENERATION_SYSTEM_PROMPT),
        HumanMessage(content=f'User query: "{query}"'),
    ]

    try:
        structured_llm = _apply_structured_output(llm, schema=GrepCommandResult)
        opik_callbacks = build_langchain_opik_callbacks()
        config = {"callbacks": opik_callbacks} if opik_callbacks else {}
        response = await asyncio.wait_for(
            structured_llm.ainvoke(messages, config=config),
            timeout=_LLM_GREP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger_instance.info("generate_grep_command_via_llm: timed out after %.1fs", _LLM_GREP_TIMEOUT)
        return None
    except Exception as exc:
        logger_instance.warning("generate_grep_command_via_llm: LLM call failed: %s", exc)
        return None

    result = _parse_grep_response(response, logger_instance)
    if result is None:
        return None

    valid_commands: list[str] = []
    for cmd in result.grep_commands[:_MAX_LLM_GREP_COMMANDS]:
        if _pre_validate_llm_grep(cmd):
            valid_commands.append(cmd.strip())
        else:
            logger_instance.warning(
                "generate_grep_command_via_llm: pre-validation failed for: %r",
                cmd[:100] if cmd else "",
            )

    if not valid_commands:
        return None

    logger_instance.info(
        "generate_grep_command_via_llm: generated %d command(s): %r",
        len(valid_commands), valid_commands,
    )
    return valid_commands


def _parse_grep_response(
    response: Any,
    logger_instance: logging.Logger,
) -> GrepCommandResult | None:
    """Parse the LLM response into a ``GrepCommandResult``."""
    if response is None:
        logger_instance.info("generate_grep_command_via_llm: LLM returned no output")
        return None

    if isinstance(response, GrepCommandResult):
        return response

    if isinstance(response, dict):
        try:
            return GrepCommandResult.model_validate(response)
        except Exception:
            logger_instance.warning(
                "generate_grep_command_via_llm: dict response failed validation",
            )
            return None

    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            try:
                return GrepCommandResult.model_validate_json(content)
            except Exception:
                pass
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    try:
                        return GrepCommandResult.model_validate_json(block["text"])
                    except Exception:
                        pass

    logger_instance.info("generate_grep_command_via_llm: could not parse LLM response")
    return None


async def check_pattern_match_eligible(
    config_service: ConfigurationService,
    logger_instance: logging.Logger,
) -> bool:
    """Return True when storage is local (pattern match only works on local)."""
    try:
        storage_cfg = await config_service.get_config(
            config_node_constants.STORAGE.value
        )
        return is_local_storage(storage_cfg)
    except Exception as exc:
        logger_instance.warning(
            "Could not read storage config for pattern match: %s", exc
        )
        return False


async def resolve_connector_ids_for_search(
    graph_provider: IGraphDBProvider,
    org_id: str,
    filters: dict[str, Any] | None,
) -> list[str]:
    """Resolve connector IDs for pattern match fan-out.

    - filters["apps"] and/or filters["kb"] present → combine both lists.
      KB IDs are App node IDs whose storage directories hold record files,
      so they are valid connector paths for grep.
    - No filters (chatbot "search all" mode) → get all org app IDs.
    """
    if filters:
        app_ids = list(filters.get("apps") or [])
        kb_ids = list(filters.get("kb") or [])
        combined = app_ids + kb_ids
        if combined:
            return combined
    try:
        org_apps = await graph_provider.get_org_apps(org_id)
        return [app["_key"] for app in org_apps if app.get("_key")]
    except Exception:
        return []


async def execute_pattern_match_pipeline(
    *,
    query: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    logger_instance: logging.Logger,
    grep_command: str | None = None,
    skip_grep_validation: bool = False,
) -> list[dict]:
    """Full pipeline: build grep → eligibility check → resolve connectors → run.

    Designed to be fired in parallel with semantic search via asyncio.gather.
    Returns raw (unfiltered) pattern match records; caller must merge/permission-check.

    When *grep_command* is provided (a full grep command from the LLM
    agent), it is validated for safety (read-only, allowed binaries,
    no injection) and used directly. Falls back to auto-deriving
    keywords from *query* when not provided or rejected.

    When *skip_grep_validation* is True and *grep_command* is provided,
    the lightweight ``validate_grep_command`` check is skipped — the
    command still goes through ``_validate_command`` in ``run_pattern_match``
    which is the real security gate and allows xargs.
    """
    if grep_command:
        if skip_grep_validation:
            pass
        else:
            validated = validate_grep_command(grep_command)
            if validated:
                grep_command = validated
            else:
                logger_instance.warning(
                    "pattern_match pipeline: grep_command rejected (unsafe): %r",
                    grep_command[:100],
                )
                grep_command = build_grep_command_from_query(query)
    else:
        grep_command = build_grep_command_from_query(query)
    if grep_command and "| head" not in grep_command and "| tail" not in grep_command:
        grep_command = f"{grep_command} | head -{_MAX_GREP_OUTPUT_LINES}"
    if not grep_command:
        logger_instance.info("pattern_match pipeline: no grep command from query=%r", query[:80])
        return []
    if not await check_pattern_match_eligible(config_service, logger_instance):
        logger_instance.info("pattern_match pipeline: storage not local, skipping")
        return []
    connector_ids = await resolve_connector_ids_for_search(
        graph_provider, org_id, filters
    )
    if not connector_ids:
        logger_instance.info("pattern_match pipeline: no connector IDs resolved")
        return []
    logger_instance.info(
        "pattern_match pipeline: grep=%r connectors=%s",
        grep_command, connector_ids,
    )
    return await run_pattern_match(
        config_service=config_service,
        org_id=org_id,
        user_id=user_id,
        graph_provider=graph_provider,
        command=grep_command,
        connector_ids=connector_ids,
        logger_instance=logger_instance,
    )


async def run_pattern_match(
    *,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    command: str,
    connector_ids: list[str],
    logger_instance: logging.Logger,
    timeout: int = _PATTERN_MATCH_TIMEOUT,
) -> list[dict]:
    """Run grep pattern match across connectors. Returns raw unfiltered records."""
    if not connector_ids or not command:
        return []

    valid, err = _validate_command(command)
    if not valid:
        logger_instance.warning("Pattern match command rejected: %s", err)
        return []

    state: dict[str, Any] = {
        "config_service": config_service,
        "org_id": org_id,
        "user_id": user_id,
        "graph_provider": graph_provider,
    }
    storage_tool = StoragePatternMatch(state)

    async def _search_connector(connector_id: str) -> list[dict]:
        success, output = await storage_tool.find_records(
            connector_id=connector_id,
            command=command,
            max_results=10,
            max_stdout_bytes=_MAX_GREP_STDOUT_BYTES,
        )
        logger_instance.info(
            "pattern_match _search_connector: cid=%s success=%s output_len=%d",
            connector_id, success, len(output),
        )
        if not success:
            logger_instance.info("pattern_match _search_connector: failed output=%r", output[:300])
            return []
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            logger_instance.info("pattern_match _search_connector: JSON parse failed")
            return []
        records = parsed.get("records", [])
        logger_instance.info(
            "pattern_match _search_connector: cid=%s records=%d", connector_id, len(records),
        )
        return records

    tasks = [_search_connector(cid) for cid in connector_ids]
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger_instance.warning("Pattern match timed out after %ds", timeout)
        return []

    all_records: list[dict] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger_instance.info("Pattern match connector %d error: %s", i, result)
            continue
        logger_instance.info(
            "Pattern match connector %d returned %d records", i, len(result),
        )
        all_records.extend(result)
    logger_instance.info("Pattern match total raw records: %d", len(all_records))
    return all_records


async def cancel_task_if_running(task: asyncio.Task | None) -> None:
    """Cancel an asyncio task if it hasn't finished yet and suppress errors."""
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _record_in_time_range(
    record: dict[str, Any],
    time_range: dict[str, int] | None,
) -> bool:
    """Return True when *record* satisfies every bound in *time_range*."""
    if not time_range:
        return True

    def _ts(key: str) -> int | None:
        v = record.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    created = _ts("source_created_at")
    modified = _ts("source_updated_at")
    if "source_created_after_ms" in time_range:
        if created is None or created < time_range["source_created_after_ms"]:
            return False
    if "source_created_before_ms" in time_range:
        if created is None or created > time_range["source_created_before_ms"]:
            return False
    if "source_updated_after_ms" in time_range:
        if modified is None or modified < time_range["source_updated_after_ms"]:
            return False
    if "source_updated_before_ms" in time_range:
        if modified is None or modified > time_range["source_updated_before_ms"]:
            return False
    return True


async def merge_pattern_match_results(
    *,
    raw_records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    user_id: str,
    org_id: str,
    blob_store: Any,
    graph_provider: IGraphDBProvider,
    is_multimodal_llm: bool,
    logger_instance: logging.Logger,
    max_records: int = _MAX_PATTERN_MATCH_RECORDS,
    time_range: dict[str, int] | None = None,
) -> list[dict]:
    """Dedup → permission check → time-range filter → fetch blob → flatten.

    When *time_range* is set, graph records are fetched first (lightweight)
    and filtered before the expensive blob fetch.
    """
    seen: set[str] = set()
    unique: list[dict] = []
    for r in raw_records:
        vrid = r.get("virtual_record_id")
        if vrid and vrid not in seen:
            seen.add(vrid)
            unique.append(r)

    new_records = [
        r
        for r in unique
        if r.get("virtual_record_id") not in virtual_record_id_to_result
    ][:max_records]

    if not new_records:
        return []

    check_vrids = [r["virtual_record_id"] for r in new_records]
    accessible_vrids = await graph_provider.check_vrids_accessible(
        user_id=user_id,
        org_id=org_id,
        virtual_record_ids=check_vrids,
    )
    if not accessible_vrids:
        logger_instance.info(
            "Pattern match: %d records checked, none accessible", len(new_records)
        )
        return []

    accessible_records = [
        r for r in new_records if r.get("virtual_record_id") in accessible_vrids
    ]
    logger_instance.info(
        "Pattern match: %d accessible of %d checked (%d raw, %d unique)",
        len(accessible_records),
        len(new_records),
        len(raw_records),
        len(unique),
    )

    record_ids = [accessible_vrids[r["virtual_record_id"]] for r in accessible_records]
    batch_records = await graph_provider.get_records_by_record_ids(
        record_ids=record_ids, org_id=org_id,
    )
    graph_by_key: dict[str, dict] = {
        r.get("_key") or r.get("id", ""): r for r in batch_records
    }
    graph_by_vrid: dict[str, dict] = {}
    for rec in accessible_records:
        vrid = rec["virtual_record_id"]
        rid = accessible_vrids[vrid]
        if rid in graph_by_key:
            graph_by_vrid[vrid] = graph_by_key[rid]

    if time_range:
        before_count = len(accessible_records)
        accessible_records = [
            r for r in accessible_records
            if r["virtual_record_id"] in graph_by_vrid
            and _graph_record_in_time_range(
                graph_by_vrid[r["virtual_record_id"]], time_range
            )
        ]
        if len(accessible_records) < before_count:
            logger_instance.info(
                "Pattern match: %d of %d records filtered by time range",
                before_count - len(accessible_records),
                before_count,
            )
        if not accessible_records:
            return []

    results: list[dict] = []
    for rec in accessible_records:
        vrid = rec["virtual_record_id"]
        graph_rec = graph_by_vrid.get(vrid)
        if not graph_rec:
            continue
        virtual_record_id_to_result[vrid] = graph_rec
        results.append({
            "virtual_record_id": vrid,
            "metadata": {
                "virtualRecordId": vrid,
                "title": graph_rec.get("title", ""),
                "recordType": graph_rec.get("recordType", ""),
                "appName": graph_rec.get("appName", ""),
                "orgId": org_id,
            },
            "score": 0.0,
            "source": "pattern_match",
        })

    logger_instance.info("Pattern match: %d record metadata results", len(results))
    return results


def _format_graph_timestamp(epoch_ms: int | float | None) -> str:
    """Format a millisecond epoch timestamp to ``YYYY-MM-DD HH:MM:SS UTC``."""
    if not epoch_ms:
        return "N/A"
    try:
        return datetime.fromtimestamp(
            int(epoch_ms) / 1000, tz=timezone.utc,
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError, OverflowError):
        return "N/A"


def _render_graph_record_metadata(graph_rec: dict[str, Any]) -> str:
    """Build a metadata block from a graph record, matching the format
    that ``BaseRecord.to_llm_context`` in entities.py produces so the
    LLM receives the same fields it gets for semantic search records."""
    record_id = graph_rec.get("id") or graph_rec.get("_key") or "N/A"
    record_name = graph_rec.get("recordName") or graph_rec.get("title") or "N/A"
    connector_name = graph_rec.get("connectorName") or "N/A"
    record_type = graph_rec.get("recordType") or "N/A"
    external_id = graph_rec.get("externalRecordId") or "N/A"
    created_at = _format_graph_timestamp(
        graph_rec.get("sourceCreatedAtTimestamp"),
    )
    updated_at = _format_graph_timestamp(
        graph_rec.get("sourceLastModifiedTimestamp"),
    )
    connector_id = graph_rec.get("connectorId") or "N/A"
    external_parent = graph_rec.get("externalParentId") or "N/A"

    lines = [
        f"Record ID: {record_id}",
        f"Name: {record_name}",
        f"Connector: {connector_name}",
        f"Type: {record_type}",
        f"External ID: {external_id}",
        f"Created At: {created_at}",
        f"Last Updated At: {updated_at}",
        f"Connector ID: {connector_id}",
        f"External Parent ID: {external_parent}",
    ]
    app_name = graph_rec.get("appName")
    location = graph_rec.get("location")
    mime_type = graph_rec.get("mimeType")
    web_url = graph_rec.get("webUrl")
    if app_name:
        lines.append(f"App: {app_name}")
    if location:
        lines.append(f"Location: {location}")
    if mime_type:
        lines.append(f"MIME Type: {mime_type}")
    if web_url:
        lines.append(f"Web URL: {web_url}")

    summary = graph_rec.get("summary")
    topics = graph_rec.get("topics")
    categories = graph_rec.get("categories")
    sub_cat_1 = graph_rec.get("subCategoryLevel1") or graph_rec.get("sub_category_level_1")
    sub_cat_2 = graph_rec.get("subCategoryLevel2") or graph_rec.get("sub_category_level_2")
    sub_cat_3 = graph_rec.get("subCategoryLevel3") or graph_rec.get("sub_category_level_3")
    if summary:
        lines.append(f"Summary: {summary}")
    if topics:
        lines.append(f"Topics: {topics}")
    cat_parts: list[str] = []
    if categories and isinstance(categories, list) and categories:
        cat_parts.append(categories[0])
    if sub_cat_1:
        cat_parts.append(sub_cat_1)
    if sub_cat_2:
        cat_parts.append(sub_cat_2)
    if sub_cat_3:
        cat_parts.append(sub_cat_3)
    if cat_parts:
        lines.append(f"Category: {' > '.join(cat_parts)}")

    extension = graph_rec.get("extension")
    if not extension and mime_type:
        ext_map = {"text/html": "html", "application/pdf": "pdf"}
        extension = ext_map.get(mime_type)
    if extension:
        lines.append("File Information:")
        lines.append(f"* Extension: {extension}")

    return "\n".join(lines)


def render_pattern_match_hint(
    pm_records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    fetch_tool_ref: str = "knowledgegraph__fetch_record",
    max_records: int = _MAX_PATTERN_MATCH_RECORDS,
) -> str:
    """Render pattern-matched records with full metadata and a fetch hint.

    Each record is wrapped in ``<record>`` tags with the same metadata
    fields the LLM receives for semantic search records, so pattern
    match records are equally actionable.  The section ends with a
    call-to-action telling the LLM to call ``fetch_tool_ref`` for any
    record whose full content it needs.

    *max_records* caps how many records are rendered (default
    ``_MAX_PATTERN_MATCH_RECORDS``).
    """
    if not pm_records:
        return ""

    capped = pm_records[:max_records]

    sections: list[str] = []
    for entry in capped:
        vrid = entry.get("virtual_record_id", "")
        graph_rec = virtual_record_id_to_result.get(vrid)
        if not graph_rec:
            continue
        metadata_block = _render_graph_record_metadata(graph_rec)
        sections.append(f"<record>\n{metadata_block}\n</record>")

    if not sections:
        return ""

    header = (
        "\n\nAdditional records found via pattern matching (metadata only — "
        "no content blocks loaded yet):"
    )
    footer = (
        f"\nTo read the full content of any relevant record above, call "
        f"`{fetch_tool_ref}` with its record_id."
    )
    return header + "\n" + "\n".join(sections) + footer


def cap_pattern_match_blocks(
    pm_blocks: list[dict],
    *,
    budget: int,
    virtual_record_id_to_result: dict[str, dict],
    logger_instance: logging.Logger,
) -> list[dict]:
    """Trim pattern-match blocks to `budget`, sharing the budget proportionally
    across records so one large record can't starve the others out.

    `merge_pattern_match_results` caps at the *record* level
    (`_MAX_PATTERN_MATCH_RECORDS`), but a single accessible record can still
    expand into an unbounded number of synthetic blocks (one per block in that
    record). Callers must apply this cap before merging pattern-match blocks
    into the results sent to the LLM, or a large record can overflow the
    context window on its own.

    Records that lose all their blocks in the trim are pruned from
    `virtual_record_id_to_result` (mutated in place) so no orphaned metadata
    lingers for a record that no longer appears in the results.
    """
    if len(pm_blocks) <= budget:
        return pm_blocks

    if budget <= 0:
        for block in pm_blocks:
            vrid = block.get("virtual_record_id")
            if vrid:
                virtual_record_id_to_result.pop(vrid, None)
        logger_instance.info(
            "Pattern match blocks capped %d -> 0 (budget=%d)", len(pm_blocks), budget,
        )
        return []

    pm_by_record: dict[str, list[dict]] = {}
    for block in pm_blocks:
        vrid = block.get("virtual_record_id", "")
        pm_by_record.setdefault(vrid, []).append(block)

    total_pm = len(pm_blocks)
    budget_left = budget
    blocks_left = total_pm

    distributed: list[dict] = []
    for vrid, blocks in pm_by_record.items():
        if budget_left <= 0:
            break
        share = max(1, round(len(blocks) / blocks_left * budget_left))
        take = min(share, len(blocks), budget_left)
        distributed.extend(blocks[:take])
        budget_left -= take
        blocks_left -= len(blocks)

    logger_instance.info(
        "Pattern match blocks capped %d -> %d (distributed across %d records, budget=%d)",
        total_pm, len(distributed), len(pm_by_record), budget,
    )

    surviving_vrids = {
        b.get("virtual_record_id") for b in distributed if b.get("virtual_record_id")
    }
    orphan_vrids = set(pm_by_record.keys()) - surviving_vrids
    if orphan_vrids:
        for vrid in orphan_vrids:
            virtual_record_id_to_result.pop(vrid, None)
        logger_instance.info(
            "Pruned %d orphaned pattern-match records from vrid map", len(orphan_vrids),
        )

    return distributed


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _get_frontend_url(blob_store: Any) -> str | None:
    try:
        endpoints_config = await blob_store.config_service.get_config(
            config_node_constants.ENDPOINTS.value,
            default={},
        )
        if isinstance(endpoints_config, dict):
            return endpoints_config.get("frontend", {}).get("publicEndpoint")
    except Exception:
        pass
    return None


def _graph_record_in_time_range(
    graph_record: dict[str, Any],
    time_range: dict[str, int] | None,
) -> bool:
    adapted = {
        "source_created_at": graph_record.get("sourceCreatedAtTimestamp"),
        "source_updated_at": graph_record.get("sourceLastModifiedTimestamp"),
    }
    return _record_in_time_range(adapted, time_range)


async def _fetch_pattern_record(
    *,
    vrid: str,
    record_id: str | None = None,
    graph_record: dict | None = None,
    virtual_record_id_to_result: dict,
    blob_store: Any,
    org_id: str,
    graph_provider: Any,
    frontend_url: str | None,
    logger_instance: logging.Logger,
) -> None:
    try:
        if graph_record is None:
            graph_record = await graph_provider.get_document(
                document_key=record_id,
                collection=CollectionNames.RECORDS.value,
            )
        if not graph_record:
            return
        await get_record(
            vrid,
            virtual_record_id_to_result,
            blob_store,
            org_id,
            {vrid: graph_record},
            graph_provider,
            frontend_url,
        )
    except Exception as e:
        logger_instance.warning("Failed to fetch PM record %s: %s", vrid, e)


def _build_synthetic_search_results(
    records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    org_id: str,
    logger_instance: logging.Logger,
) -> list[dict]:
    results: list[dict] = []
    for rec in records:
        vrid = rec["virtual_record_id"]
        record = virtual_record_id_to_result.get(vrid)
        if not record:
            continue
        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        if not blocks:
            results.append(
                {
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": 0,
                        "isBlockGroup": False,
                    },
                    "score": 0.0,
                }
            )
            continue
        for idx in range(len(blocks)):
            results.append(
                {
                    "metadata": {
                        "virtualRecordId": vrid,
                        "blockIndex": idx,
                        "isBlockGroup": False,
                        "isBlock": True,
                        "orgId": org_id,
                    },
                    "score": 0.0,
                }
            )
    logger_instance.info("Pattern match: %d synthetic search results", len(results))
    return results
