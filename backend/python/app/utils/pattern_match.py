"""Shared pattern-match helpers for internal search paths (chatbot + retrieval)."""

import asyncio
import json
import logging
import os
import re
import shlex
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from cachetools import TTLCache
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field, field_validator

from app.agents.actions.storage_search.storage_search import (
    _GREP_SHORT_VALUE_LETTERS,
    _SHORT_VALUE_LETTERS,
    StoragePatternMatch,
    _validate_command,
    is_local_storage,
)
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import config_node_constants
from app.modules.demo_data.access import excluded_demo_connector_ids
from app.services.graph_db.interface.graph_db_provider import (
    STRICT_SCOPE_FILTER_KEY,
    AccessibleContainers,
    IGraphDBProvider,
    requested_scope_ids,
)
from app.utils.env_utils import accuracy_feature_enabled, high_accuracy_mode
from app.utils.llm import get_llm_for_role
from app.utils.storage_path import build_record_group_prefix_from_chain
from app.utils.chat_helpers import (
    _build_record_dict_from_graph_base,
    create_record_instance_from_dict,
    get_flattened_results,
    get_record,
)

if TYPE_CHECKING:
    from app.utils.stage_timer import RunTimer

logger = logging.getLogger(__name__)

_PATTERN_MATCH_TIMEOUT = 30
# Whole command generation, a retry included.
_LLM_GREP_TIMEOUT = 15.0
_LLM_GREP_ATTEMPT_TIMEOUT = 10.0
_LLM_GREP_MIN_RETRY_SECS = 4.0
_GREP_LLM_ROLE = "query"
_GREP_LLM_TTL_SECS = 300.0
# Runs inside the total budget before any grep, so a stalled config read must not eat it.
_GREP_LLM_LOOKUP_TIMEOUT = 3.0
# Whole pattern match per search call: LLM grep generation plus every grep round.
_PATTERN_MATCH_TOTAL_BUDGET = 35.0
# How long a search that asked for the short keyword-search budget waits for
# the pattern match once its semantic results are in, before it goes on with
# the matches found so far. Measured on agent chats over MCP: semantic results
# came in a median 0.7 s after the search started, and the pattern match then
# took a median 4.4 s more (90th percentile 9.2 s, worst 12.6 s). Its command
# generation alone finishes a median 2.4 s after the start, so a wait under
# about 3 s keeps almost nothing. 5 s keeps 67% of the matched records and 32
# of 43 searches still get some, while ending the wait 7.6 s sooner at worst.
KEYWORD_SEARCH_SHORT_WAIT_SECS = 5.0
# Values of the chat request's `keywordSearchBudget`. "full" (and leaving it
# out) keeps the whole `_PATTERN_MATCH_TOTAL_BUDGET`.
KEYWORD_SEARCH_BUDGETS = ("short", "full")
# Reciprocal rank fusion constant (Cormack et al. 2009): damps the gap
# between first and second place so one command cannot decide the order.
_RRF_K = 60
_GREP_RANK_FUSION_ENV_VAR = "GREP_RANK_FUSION"
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
# Caps the FIRST pipeline stage, which for `grep -rliZ a . | xargs grep b` is the
# list of every file matching `a`. Too small and stage 2 only ever sees the first
# few hundred files in directory-walk order; the overall timeout bounds latency.
_MAX_GREP_STDOUT_BYTES = 4_000_000
# Deep hierarchies (Confluence page trees) produce paths of several hundred chars,
# so the tool's default 8K cap would keep only a few dozen of the 200 lines.
_MAX_GREP_OUTPUT_CHARS = 200_000
_MAX_SCOPED_SEARCH_PATHS = 20
_ZERO_COUNT_FILTER = "grep -v ':0$'"


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
    for ch in ("`", ";", "\n", "\r", "\x00"):
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
    # -e: a keyword such as "-rf" must never be read as grep options.
    return f'grep -rci -e "{pattern}" .'


_MAX_GREP_REASONING_CHARS = 200


class GrepCommandResult(BaseModel):
    # Commands first: a completion cut off mid-way still carries them.
    grep_commands: list[str]
    # Capped by truncation, not max_length: several providers reject string
    # length keywords in a response schema, and an over-long note is harmless.
    reasoning: str = Field(description=f"One short sentence, under {_MAX_GREP_REASONING_CHARS} characters.")

    @field_validator("reasoning", mode="before")
    @classmethod
    def _cap_reasoning(cls, value: object) -> object:
        if isinstance(value, str):
            return value[:_MAX_GREP_REASONING_CHARS]
        return value


_MAX_LLM_GREP_COMMANDS = 3

_GREP_GENERATION_SYSTEM_PROMPT = """\
You are generating filesystem search commands to find JSON documents relevant to a search query.
Build the commands from the search query. An original question, when given, is context only: \
use it to disambiguate the search query, not as a source of extra terms.

STEP 1 — Identify core concepts:
Extract the 1-3 core concepts the user actually cares about. These are the nouns \
and domain terms that carry meaning — not generic words like "implementation", \
"architecture", "design", "best practices", "overview", or "how to".

STEP 2 — Scale command complexity to the number of distinct concepts:
- 1 concept → single grep with OR alternatives (synonyms, abbreviations, stems). \
Example: grep -rci "deploy\\|deployment\\|ci.cd\\|pipeline" .
- 2 concepts → one AND stage: two grep commands piped, each with OR alternatives. \
Example: grep -rliZ "invoice\\|billing\\|payment" . | xargs -0 grep -Hci "recurring\\|subscri"
- 3 concepts → at most 2 AND stages (max 3 piped greps). \
Example: grep -rliZ "hierarch\\|tree\\|nested" . | xargs -0 grep -liZ "storage\\|store\\|persist" \
| xargs -0 grep -Hci "pattern\\|match\\|search"
- NEVER use more than 3 piped stages. Each AND stage exponentially reduces matches \
on small document sets.

Key principles:
- Use OR (\\|) liberally within each stage for synonyms, abbreviations, and stems.
- Each AND stage must represent a genuinely DIFFERENT concept, not a synonym of \
something already in an earlier stage.
- Prefer word stems to match inflections (e.g. "hierarch" matches hierarchy, \
hierarchical; "subscri" matches subscribe, subscription).
- Skip terms that appear in most documents (site names, navigation labels, generic \
words like "data", "system", "service").
- When the user's query is short or vague (1-2 words), cast a wide net with many \
OR alternatives rather than adding AND filters.

How many commands to return:
- Return exactly 1 command for most queries.
- Return 2 commands ONLY when genuinely different keyword families would find \
different documents (e.g. technical jargon vs. business terminology for the same topic).

Command rules:
- Search current directory: .
- Always use case-insensitive flag (-i)
- Final grep in chain: MUST use -Hci flags (count + case-insensitive + always-print-filename). \
The -H flag is REQUIRED — when xargs passes only one file, grep -c without -H outputs a bare \
number with no filename prefix, which breaks result parsing. Always include -H.
- Single broad search: grep -rci "term1\\|term2\\|term3" .
- Two-concept intersection: grep -rliZ "group_a1\\|group_a2" . | \
xargs -0 grep -Hci "group_b1\\|group_b2"
- When piping grep to xargs, ALWAYS use -Z on grep and -0 on xargs (handles filenames with spaces)
- Allowed binaries: grep, egrep, fgrep, xargs ONLY
- No shell operators: ; && || $( ${ ` > <
- Max 1000 characters per command
- Do NOT include common words like "how", "what", "setup", "use", "explain" as \
search terms"""


_MAX_STRICT_STAGES = 3
_STRICT_SHORT_FLAGS: dict[str, frozenset[str]] = {
    "grep": frozenset("rilLcHZEFwxsI"),
    "egrep": frozenset("rilLcHZEFwxsI"),
    "fgrep": frozenset("rilLcHZEFwxsI"),
    # rg: -L follows symlinks, -z/-Z spawn decompressors, -r is --replace.
    "rg": frozenset("ilcFwxsH0"),
}
_STRICT_LONG_FLAGS = frozenset({
    "--recursive", "--ignore-case", "--files-with-matches", "--files-without-match",
    "--count", "--with-filename", "--null", "--extended-regexp", "--fixed-strings",
    "--word-regexp", "--line-regexp", "--no-messages",
})
_STRICT_XARGS_FLAGS = frozenset({"-0", "--null", "-r", "--no-run-if-empty"})
_BACKREFERENCE_RE = re.compile(r"\\[1-9]")


def _strict_grep_args_error(binary: str, args: list[str], *, first_stage: bool, via_xargs: bool) -> str | None:
    allowed_short = _STRICT_SHORT_FLAGS[binary]
    patterns: list[str] = []
    operands: list[str] = []
    recursive = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            operands.extend(args[i + 1:])
            break
        if arg in ("-e", "--regexp"):
            if i + 1 >= len(args):
                return f"'{arg}' needs a pattern"
            patterns.append(args[i + 1])
            i += 2
            continue
        if arg.startswith("--regexp="):
            patterns.append(arg.split("=", 1)[1])
            i += 1
            continue
        if arg in ("-m", "--max-count"):
            if i + 1 >= len(args) or not args[i + 1].isdigit():
                return f"'{arg}' needs a number"
            i += 2
            continue
        if arg.startswith("--max-count="):
            if not arg.split("=", 1)[1].isdigit():
                return f"'{arg}' needs a number"
            i += 1
            continue
        if arg.startswith("--"):
            if arg not in _STRICT_LONG_FLAGS:
                return f"flag '{arg}' is not allowed"
            recursive = recursive or arg == "--recursive"
            i += 1
            continue
        if arg.startswith("-") and len(arg) > 1:
            letters = arg[1:]
            takes_pattern = letters.endswith("e")
            if takes_pattern:
                letters = letters[:-1]
            if "P" in letters:
                return "Perl regular expressions are not allowed"
            bad = sorted(set(letters) - allowed_short)
            if bad:
                return f"flag '-{bad[0]}' is not allowed with {binary}"
            recursive = recursive or "r" in letters
            if takes_pattern:
                if i + 1 >= len(args):
                    return f"'{arg}' needs a pattern"
                patterns.append(args[i + 1])
                i += 2
                continue
            i += 1
            continue
        operands.append(arg)
        i += 1
    if not patterns:
        if not operands:
            return "no search pattern"
        patterns.append(operands.pop(0))
    if any(_BACKREFERENCE_RE.search(p) for p in patterns):
        return "back-references are not allowed"
    if first_stage:
        if operands != ["."]:
            return "the first stage must search exactly '.'"
    elif operands:
        return "later pipeline stages may not name files"
    elif recursive and not via_xargs:
        return "a recursive grep cannot read the previous stage"
    return None


def validate_pattern_match_command(cmd: str) -> tuple[bool, str]:
    """Strict gate for LLM-written commands on the automatic pattern-match path.

    ``_validate_command`` is written for an agent's own ad-hoc commands; an LLM
    command runs on every chat turn without anyone reading it, so it may only
    search: grep-family stages over ``.``, joined by ``xargs -0 grep`` for AND
    stages, no Perl regexes, back-references or pattern files.
    """
    command = (cmd or "").strip()
    if not command:
        return False, "empty command"
    if len(command) > _MAX_GREP_COMMAND_LENGTH:
        return False, "command too long"
    ok, err = _validate_command(command)
    if not ok:
        return False, err
    stages = _split_pipe_outside_quotes(command)
    if stages is None:
        return False, "unbalanced quotes"
    if len(stages) > _MAX_STRICT_STAGES:
        return False, f"at most {_MAX_STRICT_STAGES} pipeline stages"
    for index, stage in enumerate(stages):
        try:
            tokens = shlex.split(stage)
        except ValueError as exc:
            return False, f"cannot parse stage: {exc}"
        if not tokens:
            return False, "empty pipeline stage"
        via_xargs = tokens[0] == "xargs"
        if via_xargs:
            if index == 0:
                return False, "xargs cannot start the pipeline"
            j = 1
            while j < len(tokens) and tokens[j].startswith("-"):
                if tokens[j] not in _STRICT_XARGS_FLAGS:
                    return False, f"xargs flag '{tokens[j]}' is not allowed"
                j += 1
            tokens = tokens[j:]
            if not tokens:
                return False, "xargs needs a grep sub-command"
        binary = tokens[0]
        if binary not in _ALLOWED_GREP_BINARIES:
            return False, f"'{binary}' is not allowed"
        # shlex drops quotes, and _scope_grep_to_paths only rewrites a bare `.`.
        if index == 0 and not stage.rstrip().endswith(" ."):
            return False, "the first stage must end with a bare '.'"
        err = _strict_grep_args_error(
            binary, tokens[1:], first_stage=index == 0, via_xargs=via_xargs,
        )
        if err:
            return False, err
    return True, ""


def _report_llm_usage(
    response: Any,  # noqa: ANN401 - the include_raw structured-output dict
    on_llm_usage: Callable[[BaseMessage], None] | None,
    logger_instance: logging.Logger,
) -> None:
    raw = response.get("raw") if isinstance(response, dict) else None
    if on_llm_usage is None or not isinstance(raw, BaseMessage):
        return
    try:
        on_llm_usage(raw)
    except Exception:
        # Usage accounting must not cost the grep commands it accounts for.
        logger_instance.warning("generate_grep_command_via_llm: usage callback failed", exc_info=True)


async def generate_grep_command_via_llm(
    query: str,
    llm: BaseChatModel,
    logger_instance: logging.Logger,
    user_query: str | None = None,
    on_llm_usage: Callable[[BaseMessage], None] | None = None,
) -> list[str] | None:
    """Generate targeted grep commands via LLM structured output.

    Returns a list of 1-3 commands that passed ``validate_pattern_match_command``,
    or None on timeout, LLM failure, or when every command is rejected. A failed
    attempt is retried once while ``_LLM_GREP_TIMEOUT`` leaves room for it.

    ``on_llm_usage`` receives the raw model message of every attempt that
    returned, for its token usage; an attempt cut off by the timeout has none.

    Uses LangChain's ``ainvoke`` with Opik callbacks so the call appears
    in the Opik trace alongside the rest of the agent loop.
    """
    from app.agent_loop_lib.transport.opik_tracing import build_langchain_opik_callbacks
    from app.utils.streaming import _apply_structured_output

    messages = [
        SystemMessage(content=_GREP_GENERATION_SYSTEM_PROMPT),
        HumanMessage(content=_grep_generation_request(query, user_query)),
    ]

    try:
        structured_llm = _apply_structured_output(llm, schema=GrepCommandResult, include_raw=True)
    except Exception as exc:
        logger_instance.warning("generate_grep_command_via_llm: LLM setup failed: %s", exc)
        return None
    opik_callbacks = build_langchain_opik_callbacks()
    config = {"callbacks": opik_callbacks} if opik_callbacks else {}

    deadline = time.monotonic() + _LLM_GREP_TIMEOUT
    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if attempt > 1 and remaining < _LLM_GREP_MIN_RETRY_SECS:
            break
        try:
            response = await asyncio.wait_for(
                structured_llm.ainvoke(messages, config=config),
                timeout=min(_LLM_GREP_ATTEMPT_TIMEOUT, remaining),
            )
        except asyncio.TimeoutError:
            logger_instance.info(
                "generate_grep_command_via_llm: attempt %d timed out after %.1fs",
                attempt, min(_LLM_GREP_ATTEMPT_TIMEOUT, remaining),
            )
            continue
        except Exception as exc:
            logger_instance.warning(
                "generate_grep_command_via_llm: attempt %d, LLM call failed: %s", attempt, exc,
            )
            continue
        _report_llm_usage(response, on_llm_usage, logger_instance)
        commands = _parse_grep_response(response, logger_instance)
        valid_commands = _valid_llm_grep_commands(commands or [], logger_instance)
        if valid_commands:
            logger_instance.info(
                "generate_grep_command_via_llm: generated %d command(s) on attempt %d: %r",
                len(valid_commands), attempt, valid_commands,
            )
            return valid_commands
    return None


def _grep_generation_request(query: str, user_query: str | None) -> str:
    """The search query the agent wrote, with the user's question as context.

    The agent's query carries the vocabulary it learned from earlier results
    (often the documents' own terms); the question alone makes every search
    in a turn grep for the same paraphrase.
    """
    search = (query or "").strip()
    question = (user_query or "").strip()
    if not search:
        return f'Search query: "{question}"'
    if not question or question == search:
        return f'Search query: "{search}"'
    return f'Search query: "{search}"\nOriginal question (context only): "{question}"'


def _valid_llm_grep_commands(commands: list[str], logger_instance: logging.Logger) -> list[str]:
    valid_commands: list[str] = []
    for cmd in commands[:_MAX_LLM_GREP_COMMANDS]:
        ok, reason = validate_pattern_match_command(cmd)
        if ok:
            valid_commands.append(cmd.strip())
        else:
            logger_instance.warning(
                "generate_grep_command_via_llm: rejected %r: %s",
                cmd[:100] if cmd else "", reason,
            )
    return valid_commands


_GREP_COMMANDS_KEY_RE = re.compile(r'"grep_commands"\s*:\s*\[')


def _recover_grep_commands(text: str) -> list[str]:
    """The complete strings of a ``grep_commands`` array in *text*, even when
    the JSON around them was cut off."""
    m = _GREP_COMMANDS_KEY_RE.search(text)
    if not m:
        return []
    decoder = json.JSONDecoder()
    commands: list[str] = []
    pos = m.end()
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] != '"':
            break
        try:
            value, pos = decoder.raw_decode(text, pos)
        except ValueError:
            break
        if isinstance(value, str):
            commands.append(value)
    return commands


def _message_text(message: object) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return ""


def _commands_in_args(args: object) -> list[str]:
    if isinstance(args, str):
        return _recover_grep_commands(args)
    if isinstance(args, dict):
        commands = args.get("grep_commands")
        if isinstance(commands, list):
            return [c for c in commands if isinstance(c, str)]
    return []


def _recover_raw_grep_commands(message: object) -> list[str]:
    """Commands in a raw completion that failed to parse. Tool-calling structured
    output (Bedrock) carries them in tool-call arguments, not in the text."""
    recovered = _recover_grep_commands(_message_text(message))
    if recovered:
        return recovered
    content = getattr(message, "content", None)
    tool_args = [
        block.get("input")
        for block in (content if isinstance(content, list) else [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    for attr in ("tool_calls", "invalid_tool_calls"):
        calls = getattr(message, attr, None)
        if isinstance(calls, list):
            tool_args += [call.get("args") for call in calls if isinstance(call, dict)]
    for args in tool_args:
        recovered = _commands_in_args(args)
        if recovered:
            return recovered
    return []


def _parse_grep_response(
    response: Any,
    logger_instance: logging.Logger,
) -> list[str] | None:
    """The generated commands in an LLM response, or None when it holds none.

    Accepts the ``include_raw`` envelope (``{"raw", "parsed", "parsing_error"}``):
    when parsing failed, the commands are recovered from the raw completion.
    """
    if response is None:
        logger_instance.info("generate_grep_command_via_llm: LLM returned no output")
        return None

    if isinstance(response, dict) and "raw" in response and "parsed" in response:
        parsed = response.get("parsed")
        if parsed is not None:
            return _parse_grep_response(parsed, logger_instance)
        recovered = _recover_raw_grep_commands(response.get("raw"))
        logger_instance.info(
            "generate_grep_command_via_llm: unparseable output (%s), recovered %d command(s)",
            str(response.get("parsing_error"))[:200], len(recovered),
        )
        return recovered or None

    if isinstance(response, GrepCommandResult):
        return response.grep_commands

    if isinstance(response, dict):
        try:
            return GrepCommandResult.model_validate(response).grep_commands
        except Exception:
            recovered = response.get("grep_commands")
            if isinstance(recovered, list) and all(isinstance(c, str) for c in recovered):
                return recovered
            logger_instance.warning(
                "generate_grep_command_via_llm: dict response failed validation",
            )
            return None

    if hasattr(response, "content"):
        text = _message_text(response)
        try:
            return GrepCommandResult.model_validate_json(text).grep_commands
        except Exception:
            recovered = _recover_grep_commands(text)
            if recovered:
                return recovered

    logger_instance.info("generate_grep_command_via_llm: could not parse LLM response")
    return None


# Keyed by org: the model carries that org's AI-model config and API key.
_grep_llm_cache: TTLCache = TTLCache(maxsize=256, ttl=_GREP_LLM_TTL_SECS)
# The agent often repeats a search query within a turn (retries, other sources).
_grep_commands_cache: TTLCache = TTLCache(maxsize=512, ttl=600)


async def _grep_generation_llm(
    config_service: ConfigurationService, org_id: str, fallback: BaseChatModel,
) -> BaseChatModel:
    """The model for the ``query`` role (else the default model) at low
    reasoning effort, cached; *fallback* (the chat's model) when none can be built.

    The chat's own model runs at the conversation's effort, often high, and its
    completions were often cut off before any command was written.
    """
    cached = _grep_llm_cache.get(org_id)
    if cached is not None:
        return cached
    try:
        llm, _ = await asyncio.wait_for(
            get_llm_for_role(config_service, _GREP_LLM_ROLE, reasoning_effort="low"),
            timeout=_GREP_LLM_LOOKUP_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.info(
            "pattern_match: grep-generation model lookup timed out after %.1fs, using the chat's",
            _GREP_LLM_LOOKUP_TIMEOUT,
        )
        return fallback
    except Exception as exc:
        logger.info("pattern_match: no low-effort LLM for grep generation, using the chat's: %s", exc)
        return fallback
    _grep_llm_cache[org_id] = llm
    return llm


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
    - Empty scope under ``strictScope`` (project chat) → search nothing.
    - No filters (chatbot "search all" mode) → get all org app IDs,
      switched-off connectors included: their records stay searchable,
      as they do in semantic search.
    """
    scope = requested_scope_ids(filters)
    if scope is not None:
        return list(scope)
    if filters and filters.get(STRICT_SCOPE_FILTER_KEY):
        return []
    try:
        org_apps = await graph_provider.get_org_apps(org_id, active_only=False)
        return [app["_key"] for app in org_apps if app.get("_key")]
    except Exception:
        logger.warning("pattern_match: org app lookup failed, grep searches nothing", exc_info=True)
        return []


async def _fetch_container_group_rows(
    graph_provider: IGraphDBProvider, containers: AccessibleContainers,
) -> list[dict[str, Any]]:
    group_ids = containers.record_group_ids | containers.root_group_ids
    if not group_ids:
        return []
    rows = await graph_provider.get_nodes_by_field_in(
        CollectionNames.RECORD_GROUPS.value,
        "id",
        sorted(group_ids),
        ["id", "groupName", "connectorId", "orgId"],
    )
    return list(rows or [])


def _accessible_groups_for_connector(
    containers: AccessibleContainers,
    group_rows: list[dict[str, Any]],
    *,
    org_id: str,
    connector_id: str,
) -> list[dict[str, str]]:
    """Record groups of *connector_id* that *containers* reaches, as
    ``{"id", "group_name"}`` sorted by id.

    Derived from the turn's containers so grep is narrowed by exactly the grants
    semantic search uses. Empty means "do not scope": the caller greps the whole
    connector and adjudicates every hit. So every uncertain case returns empty:
    unusable containers, a group without an id or name, and root-scoped
    connectors, whose descendant groups the containers deliberately leave out.
    """
    if containers.fallback_reason is not None:
        return []
    group_ids = containers.record_group_ids | containers.root_group_ids
    groups: dict[str, str] = {}
    for row in group_rows:
        if not isinstance(row, dict):
            continue
        if row.get("connectorId") != connector_id or row.get("orgId") != org_id:
            continue
        rg_id = row.get("id") or row.get("_key")
        if rg_id in containers.root_group_ids:
            return []
        name = row.get("groupName")
        # Dropping just this group would scope grep to the rest and hide its hits.
        if not rg_id or not isinstance(name, str) or not name:
            return []
        if rg_id in group_ids:
            groups[rg_id] = name
    return [{"id": rg_id, "group_name": groups[rg_id]} for rg_id in sorted(groups)]


async def _resolve_search_paths(
    *,
    graph_provider: IGraphDBProvider,
    connector_id: str,
    connector_dir: str,
    accessible_rgs: list[dict[str, str]],
    logger_instance: logging.Logger,
) -> list[str] | None:
    """Map accessible record groups to their directories under *connector_dir*.

    Records are written under the group's full ancestor chain
    (``build_hierarchical_storage_path``), so a nested group lives at
    ``./<root>/.../<group>``, not ``./<group>``. Groups with no directory on
    disk have nothing indexed and are skipped; a group nested under another
    accessible group is already covered by the ancestor's recursive grep.

    Returns ``"./..."`` paths, or *None* when scoping should be skipped (too
    many groups, a group whose ancestry is unknown, or no group resolved to a
    directory).
    """
    if not accessible_rgs or len(accessible_rgs) > _MAX_SCOPED_SEARCH_PATHS:
        return None
    chains = await asyncio.gather(
        *(
            graph_provider.get_record_group_path(rg.get("id", ""), raise_on_error=True)
            for rg in accessible_rgs
        ),
        return_exceptions=True,
    )
    connector_prefix = f"records/{connector_id}/"
    rel_paths: set[str] = set()
    for rg, chain in zip(accessible_rgs, chains):
        if isinstance(chain, BaseException) or not chain:
            # Unknown ancestry: a flat guess could name a different group's directory.
            logger_instance.info(
                "pattern_match: no path for record group %s, not scoping: %s",
                rg.get("id"), chain,
            )
            return None
        prefix = build_record_group_prefix_from_chain(connector_id, chain)
        if not prefix or not prefix.startswith(connector_prefix):
            continue
        rel = prefix[len(connector_prefix):]
        if await asyncio.to_thread(os.path.isdir, os.path.join(connector_dir, rel)):
            rel_paths.add(rel)
        else:
            logger_instance.info(
                "pattern_match: no directory for record group %s at %r, skipping",
                rg.get("id"), rel,
            )
    kept: list[str] = []
    for rel in sorted(rel_paths):
        if not any(rel.startswith(k + "/") for k in kept):
            kept.append(rel)
    return [f"./{rel}" for rel in kept] or None


def _scope_grep_to_paths(command: str, paths: list[str]) -> str:
    """Replace the trailing bare ``.`` of the pipeline's first stage with *paths*.

    ``grep -rli "term" . | xargs grep -ci "t2"`` with ``["./A", "./B"]`` becomes
    ``grep -rli "term" "./A" "./B" | xargs grep -ci "t2"``. Only the trailing
    ``.`` is touched (both command sources end the first stage with one), so a
    ``" . "`` inside a quoted pattern is never mistaken for the search path.
    Returns *command* unchanged when the first stage does not end with ``" ."``.
    """
    stages = _split_pipeline(command)
    first_stage = stages[0].rstrip()
    if not first_stage.endswith(" ."):
        return command
    new_first = first_stage[:-1] + " ".join(f'"{p}"' for p in paths)
    return " | ".join([new_first, *(stage.strip() for stage in stages[1:])])


def _split_pipeline(command: str) -> list[str]:
    """Split a shell pipeline on ``|`` characters outside of quotes."""
    stages: list[str] = []
    current: list[str] = []
    in_quote: str | None = None
    for ch in command:
        if in_quote:
            current.append(ch)
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            current.append(ch)
            in_quote = ch
        elif ch == "|":
            stages.append("".join(current))
            current = []
        else:
            current.append(ch)
    stages.append("".join(current))
    return stages


def _grep_stage_flags(stage: str) -> tuple[str, set[str], set[str]] | None:
    """(binary, short flag letters, long flags) of the grep-family command in ``stage``.

    Values of value-taking options (``-e PAT``, ``-m 5``, ``-mNUM``) are skipped,
    so ``-e -c`` is a pattern, not the count flag.
    """
    try:
        tokens = shlex.split(stage)
    except ValueError:
        return None
    start = next((i for i, t in enumerate(tokens) if t in _ALLOWED_GREP_BINARIES), None)
    if start is None:
        return None
    binary = tokens[start]
    value_letters = _SHORT_VALUE_LETTERS.get(binary, _GREP_SHORT_VALUE_LETTERS)
    short: set[str] = set()
    long_flags: set[str] = set()
    args = iter(tokens[start + 1:])
    for tok in args:
        if tok == "--":
            break
        if tok.startswith("--"):
            long_flags.add(tok.split("=", 1)[0])
            continue
        if not tok.startswith("-") or tok == "-":
            continue
        for j, ch in enumerate(tok[1:], start=1):
            short.add(ch)
            if ch in value_letters:
                if j == len(tok) - 1:
                    next(args, None)
                break
    return binary, short, long_flags


def _add_grep_flag(stage: str, letter: str, long_name: str) -> str:
    """Add ``-<letter>`` to the grep-family command in ``stage`` unless already set.

    Appended to the first flag group only when that group takes no value:
    ``-e`` + ``Z`` would make ``Z`` the pattern.
    """
    parsed = _grep_stage_flags(stage)
    if parsed is None:
        return stage
    binary, short, long_flags = parsed
    if letter in short or long_name in long_flags:
        return stage
    m = re.search(rf"(?:^|\s){re.escape(binary)}(\s+|$)", stage)
    if not m:
        return stage
    head, rest = stage[:m.end()], stage[m.end():]
    value_letters = _SHORT_VALUE_LETTERS.get(binary, _GREP_SHORT_VALUE_LETTERS)
    group = re.match(r"-([a-zA-Z0-9]+)(?=\s|$)", rest)
    if group and not set(group.group(1)) & set(value_letters):
        return head + rest[:group.end()] + letter + rest[group.end():]
    return f"{head.rstrip()} -{letter} {rest}".rstrip()


def _ensure_null_delimited_pipeline(command: str) -> str:
    """Ensure grep|xargs pipelines use null-terminated I/O (-Z / -0).

    Filenames containing spaces break the default newline-delimited
    ``grep -l … | xargs grep`` pipeline.  This injects ``-Z`` (``-0`` for rg,
    whose ``-Z`` searches compressed files) into grep stages feeding an
    ``xargs`` and ``-0`` into the following xargs.

    Also injects ``-H`` into grep stages that use ``-c`` (count mode)
    and follow an ``xargs``, because ``grep -c`` omits the filename
    prefix when it receives a single file argument — making the output
    just a bare count that ``find_records`` cannot map to a record.

    Handles both bare ``grep … | xargs`` and nested
    ``xargs grep … | xargs`` stages.
    """
    stages = _split_pipeline(command)
    if len(stages) < 2:
        return command

    result: list[str] = []
    for i, stage in enumerate(stages):
        s = stage.strip()
        next_is_xargs = (
            i + 1 < len(stages)
            and stages[i + 1].strip().startswith("xargs")
        )

        if next_is_xargs:
            parsed = _grep_stage_flags(s)
            null_letter = "0" if parsed and parsed[0] == "rg" else "Z"
            s = _add_grep_flag(s, null_letter, "--null")

        if s.startswith("xargs") and "-0" not in s and "--null" not in s:
            s = "xargs -0" + s[5:]

        if s.startswith("xargs"):
            parsed = _grep_stage_flags(s)
            if parsed and ("c" in parsed[1] or "--count" in parsed[2]):
                s = _add_grep_flag(s, "H", "--with-filename")

        result.append(s)

    return " | ".join(result)


def _cap_grep_output(command: str) -> str:
    """Drop ``file:0`` lines, then cap at ``_MAX_GREP_OUTPUT_LINES``.

    ``grep -c`` prints a line for every file it reads, matching or not. Unless
    those are dropped first, they fill the line and byte caps in directory-walk
    order and real matches later in the walk are never seen.
    """
    stages = [s.strip() for s in _split_pipeline(command)]
    cut = next(
        (i for i, s in enumerate(stages) if i > 0 and re.match(r"(head|tail)\b", s)),
        len(stages),
    )
    limit = stages[cut:] or [f"head -{_MAX_GREP_OUTPUT_LINES}"]
    return " | ".join(stages[:cut] + [_ZERO_COUNT_FILTER] + limit)


async def run_pattern_match_with_llm_grep(
    *,
    query: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    logger_instance: logging.Logger,
    llm: Any | None = None,
    user_query: str | None = None,
    on_llm_usage: Callable[[BaseMessage], None] | None = None,
    found: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Generate grep commands via LLM, run pipelines in parallel, dedup.

    Falls back to a keyword grep derived from *query* when the LLM produces
    no usable command, or when its commands fail or match nothing — an LLM
    command can be too narrow (an extra AND stage) where plain keywords hit.

    Shared entry point used by both chatbot (search.py) and retrieval
    integration to avoid duplicating the LLM-grep-then-fan-out logic.

    Bounded by ``_PATTERN_MATCH_TOTAL_BUDGET`` as a whole: callers await it
    after semantic search, so without one bound the LLM call, its pipelines
    and the keyword fallback would each add their own timeout to the answer.
    When the budget runs out, what the finished LLM commands found is kept.

    *found*, when given, is filled with the best hit per record as each LLM
    command finishes, so a caller that stops waiting early can keep them
    (``await_pattern_match`` with ``wait_budget``).
    """
    if found is None:
        found = {}
    try:
        return await asyncio.wait_for(
            _run_pattern_match_with_llm_grep(
                query=query, config_service=config_service, org_id=org_id,
                user_id=user_id, graph_provider=graph_provider, filters=filters,
                logger_instance=logger_instance, llm=llm, user_query=user_query,
                found=found, on_llm_usage=on_llm_usage,
            ),
            timeout=_PATTERN_MATCH_TOTAL_BUDGET,
        )
    except asyncio.TimeoutError:
        logger_instance.warning(
            "pattern_match: total budget of %.0fs exceeded, returning %d match(es) found so far",
            _PATTERN_MATCH_TOTAL_BUDGET, len(found),
        )
        return list(found.values())


async def _run_pattern_match_with_llm_grep(
    *,
    query: str,
    config_service: ConfigurationService,
    org_id: str,
    user_id: str,
    graph_provider: IGraphDBProvider,
    filters: dict[str, Any] | None,
    logger_instance: logging.Logger,
    llm: Any | None,
    user_query: str | None,
    found: dict[str, dict[str, Any]],
    on_llm_usage: Callable[[BaseMessage], None] | None = None,
) -> list[dict[str, Any]]:
    """*found* collects the best hit per record as each LLM command finishes,
    so the caller still has them if the total budget cancels this."""
    if not await check_pattern_match_eligible(config_service, logger_instance):
        return []
    llm_grep_cmds: list[str] | None = None
    if llm is not None:
        cache_key = (org_id, query.strip(), (user_query or "").strip())
        llm_grep_cmds = _grep_commands_cache.get(cache_key)
        if llm_grep_cmds is None:
            llm_grep_cmds = await generate_grep_command_via_llm(
                query=query, llm=await _grep_generation_llm(config_service, org_id, llm),
                logger_instance=logger_instance, user_query=user_query,
                on_llm_usage=on_llm_usage,
            )
            if llm_grep_cmds:
                _grep_commands_cache[cache_key] = llm_grep_cmds

    common: dict[str, Any] = {
        "query": query,
        "config_service": config_service,
        "org_id": org_id,
        "user_id": user_id,
        "graph_provider": graph_provider,
        "filters": filters,
        "logger_instance": logger_instance,
    }
    if llm_grep_cmds:
        async def _pipeline(cmd: str) -> tuple[str, list[dict[str, Any]] | BaseException]:
            try:
                return cmd, await execute_pattern_match_pipeline(
                    **common, grep_command=cmd, skip_grep_validation=True,
                )
            except Exception as exc:
                return cmd, exc

        tasks = [asyncio.ensure_future(_pipeline(cmd)) for cmd in llm_grep_cmds]
        try:
            for next_done in asyncio.as_completed(tasks):
                cmd, result = await next_done
                if isinstance(result, BaseException):
                    logger_instance.warning(
                        "pattern_match: LLM grep pipeline failed for %r: %s", cmd[:100], result,
                    )
                    continue
                _merge_command_hits(found, result)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if found:
            return list(found.values())
        logger_instance.info("pattern_match: LLM grep found nothing, retrying with keyword grep")

    return await execute_pattern_match_pipeline(
        **common, grep_command=None, skip_grep_validation=False,
    )


def _merge_command_hits(found: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Add one grep command's ranked hits to *found*. Rank fusion is opt-in
    (``GREP_RANK_FUSION``, else ``HIGH_ACCURACY_MODE``); otherwise each record
    keeps its best hit."""
    if accuracy_feature_enabled(_GREP_RANK_FUSION_ENV_VAR):
        _fuse_ranked(found, records)
    else:
        _keep_best(found, records)


def _keep_best(found: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Keep each record's best hit across commands, by its own match counts."""
    for rec in records:
        vrid = rec.get("virtual_record_id")
        if vrid and (vrid not in found or _record_rank(rec) > _record_rank(found[vrid])):
            found[vrid] = rec


def _fuse_ranked(found: dict[str, dict[str, Any]], records: list[dict[str, Any]]) -> None:
    """Add one command's hits to *found* by reciprocal rank fusion.

    Commands search different term sets, so their match counts do not
    compare; a record's place in each command's own ranking does. A record
    several commands rank highly beats one only a broad command found.
    """
    command_hits: dict[str, dict[str, Any]] = {}
    _keep_best(command_hits, records)
    # *records* concatenates per-connector lists, each ranked from 0; within
    # one command the counts compare across connectors, so rank them together.
    ranked = sorted(command_hits.values(), key=_record_rank, reverse=True)
    place = 0
    for i, rec in enumerate(ranked):
        if i and _record_rank(rec) != _record_rank(ranked[i - 1]):
            place = i
        vrid = rec["virtual_record_id"]
        fused = _fusion_score(found.get(vrid)) + 1.0 / (_RRF_K + place)
        best = found[vrid] if vrid in found and _record_rank(found[vrid]) >= _record_rank(rec) else rec
        found[vrid] = {**best, "fusion_score": fused}


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
    """Full pipeline: eligibility check → build grep → resolve connectors → run.

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
    if not await check_pattern_match_eligible(config_service, logger_instance):
        logger_instance.info("pattern_match pipeline: storage not local, skipping")
        return []
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
    if grep_command:
        grep_command = _cap_grep_output(_ensure_null_delimited_pipeline(grep_command))
    if not grep_command:
        logger_instance.info("pattern_match pipeline: no grep command from query=%r", query[:80])
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
    """Run grep pattern match across connectors. Returns raw, UNADJUDICATED records.

    The permission model only decides where grep looks, never who may see a
    hit — ``merge_pattern_match_results`` adjudicates every record through
    ``filter_accessible_virtual_record_ids``, exactly as semantic search does:

    - APP_LEVEL connector → grep the whole connector directory.
    - Connector whose accessible record groups are all RECORD_GROUP_LEVEL, for
      a user with no direct record grants outside them → grep only those
      groups' directories, so matches the user cannot reach do not crowd out
      the ones they can.
    - Anything else (RECORD_LEVEL, or groups needing verification) → grep
      the whole connector directory.
    """
    if not connector_ids or not command:
        return []

    valid, err = _validate_command(command)
    if not valid:
        logger_instance.warning("Pattern match command rejected: %s", err)
        return []

    containers: AccessibleContainers | None = None
    try:
        containers = await graph_provider.get_accessible_containers(
            user_id=user_id, org_id=org_id,
        )
    except Exception:
        logger_instance.debug(
            "get_accessible_containers failed",
            exc_info=True,
        )

    app_level_ids: frozenset[str] = (
        containers.app_ids_trusted if containers and not containers.fallback_reason else frozenset()
    )
    rg_ids_trusted: frozenset[str] = (
        containers.record_group_ids_trusted if containers and not containers.fallback_reason else frozenset()
    )
    # Records shared with the user directly, outside every group they reach,
    # live in other groups' directories; group scoping would never see them.
    has_direct_grants = bool(
        containers and not containers.fallback_reason and containers.direct_records
    )

    # Never the chat state: find_records remembers its hits as shown to the
    # model, and these are not shown yet. Retrieval remembers what it renders.
    state: dict[str, Any] = {
        "config_service": config_service,
        "org_id": org_id,
        "user_id": user_id,
        "graph_provider": graph_provider,
    }
    storage_tool = StoragePatternMatch(state)

    async def _run_grep(connector_id: str, cmd: str) -> list[dict] | None:
        """Run one grep against a connector; None when the command itself failed
        (as opposed to matching nothing), so callers can fall back."""
        try:
            success, output = await storage_tool.find_records(
                connector_id=connector_id,
                command=cmd,
                max_results=10,
                max_stdout_bytes=_MAX_GREP_STDOUT_BYTES,
                max_output_chars=_MAX_GREP_OUTPUT_CHARS,
            )
        except Exception:
            logger_instance.warning(
                "pattern_match: grep raised for cid=%s", connector_id, exc_info=True,
            )
            return None
        if not success:
            logger_instance.info(
                "pattern_match: grep failed for cid=%s: %s", connector_id, output[:300],
            )
            return None
        try:
            parsed = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            logger_instance.warning(
                "pattern_match: unreadable grep output for cid=%s: %r", connector_id, str(output)[:200],
            )
            return None
        return parsed.get("records", []) if isinstance(parsed, dict) else None

    async def _search_connector(connector_id: str) -> list[dict]:
        # APP_LEVEL: full grep, all records accessible
        if connector_id in app_level_ids:
            logger_instance.info(
                "pattern_match _search_connector: cid=%s APP_LEVEL, full grep",
                connector_id,
            )
            records = await _run_grep(connector_id, command) or []
            logger_instance.info(
                "pattern_match _search_connector: cid=%s records=%d",
                connector_id, len(records),
            )
            return records

        # Record groups of this connector the user reaches: the grants semantic
        # search uses. Looked up only when scoping could use them.
        accessible_rgs: list[dict[str, str]] = []
        if containers is not None and rg_ids_trusted and not has_direct_grants:
            try:
                group_rows = await _fetch_container_group_rows(graph_provider, containers)
                accessible_rgs = _accessible_groups_for_connector(
                    containers, group_rows, org_id=org_id, connector_id=connector_id,
                )
            except Exception:
                logger_instance.debug(
                    "RG lookup failed for connector %s, falling back to full grep",
                    connector_id, exc_info=True,
                )

        if accessible_rgs:
            rg_ids = {rg["id"] for rg in accessible_rgs if "id" in rg}
            all_rgs_trusted = bool(rg_ids) and rg_ids <= rg_ids_trusted

            search_paths: list[str] | None = None
            if all_rgs_trusted and not has_direct_grants:
                try:
                    connector_dir, _ = await storage_tool._resolve_connector_path(connector_id)
                    if connector_dir:
                        search_paths = await _resolve_search_paths(
                            graph_provider=graph_provider,
                            connector_id=connector_id,
                            connector_dir=connector_dir,
                            accessible_rgs=accessible_rgs,
                            logger_instance=logger_instance,
                        )
                except Exception:
                    logger_instance.warning(
                        "pattern_match: resolving group paths failed for cid=%s, "
                        "falling back to full grep", connector_id, exc_info=True,
                    )
            if search_paths:
                scoped_cmd = _scope_grep_to_paths(command, search_paths)
                # Unchanged means the rewrite did not apply; the full grep below runs.
                if scoped_cmd != command:
                    logger_instance.info(
                        "pattern_match _search_connector: cid=%s RECORD_GROUP_LEVEL, "
                        "scoped grep paths=%d cmd=%r",
                        connector_id, len(search_paths), scoped_cmd[:200],
                    )
                    scoped_records = await _run_grep(connector_id, scoped_cmd)
                    if scoped_records is not None:
                        logger_instance.info(
                            "pattern_match _search_connector: cid=%s records=%d",
                            connector_id, len(scoped_records),
                        )
                        return scoped_records
                    # e.g. a group name the command validator rejects; the full
                    # grep below searches more but is adjudicated the same way.
                    logger_instance.info(
                        "pattern_match _search_connector: cid=%s scoped grep failed, "
                        "falling back to full grep",
                        connector_id,
                    )

        # RECORD_LEVEL (or scoping unavailable): full grep
        logger_instance.info(
            "pattern_match _search_connector: cid=%s RECORD_LEVEL, full grep",
            connector_id,
        )
        records = await _run_grep(connector_id, command) or []
        logger_instance.info(
            "pattern_match _search_connector: cid=%s records=%d",
            connector_id, len(records),
        )
        return records

    # Per connector, so one slow connector cannot discard everyone else's results.
    async def _search_connector_bounded(connector_id: str) -> list[dict]:
        try:
            return await asyncio.wait_for(_search_connector(connector_id), timeout=timeout)
        except asyncio.TimeoutError:
            logger_instance.warning(
                "Pattern match timed out after %ds for cid=%s", timeout, connector_id,
            )
            return []

    results = await asyncio.gather(
        *(_search_connector_bounded(cid) for cid in connector_ids),
        return_exceptions=True,
    )

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


def pattern_match_wait_budget(requested: str | None) -> float | None:
    """Seconds a search waits for its pattern match after semantic results,
    or None to wait for all of it (up to ``_PATTERN_MATCH_TOTAL_BUDGET``).

    Only a request that asked for the short budget gets one, and never under
    ``HIGH_ACCURACY_MODE``, where the deployment has chosen better answers
    over faster ones. A request that says nothing waits as it always has.
    """
    if requested != "short" or high_accuracy_mode():
        return None
    return KEYWORD_SEARCH_SHORT_WAIT_SECS


async def await_pattern_match(
    task: asyncio.Task, logger_instance: logging.Logger,
    run_timer: "RunTimer | None" = None,
    *,
    wait_budget: float | None = None,
    found_so_far: Mapping[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Result of a pattern-match task started beside semantic search; any
    failure is ``[]`` so semantic results are always returned.

    A CancelledError from inside the task is a pattern-match failure too
    (``except Exception`` misses it); only cancellation of the caller itself
    is re-raised.

    With ``wait_budget``, waits at most that many seconds. If the task has
    not finished by then it is cancelled, and the matches its finished
    commands already put in ``found_so_far`` (the ``found`` dict passed to
    ``run_pattern_match_with_llm_grep``) are returned.

    When ``run_timer`` is given, the time spent waiting here (semantic
    results already in hand) is recorded as ``pattern_match_wait``.
    """
    wait_started = time.perf_counter()
    try:
        if wait_budget is None:
            return await task or []
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0.0, wait_budget))
        except asyncio.CancelledError:
            # `asyncio.wait` does not pass the caller's cancellation on to the
            # task the way awaiting it directly does.
            task.cancel()
            raise
        if task in done:
            return task.result() or []
        await cancel_task_if_running(task)
        kept = list((found_so_far or {}).values())
        logger_instance.info(
            "pattern_match: stopped waiting %.1fs after semantic results, keeping %d match(es) found so far",
            wait_budget, len(kept),
        )
        return kept
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        logger_instance.warning("Pattern match was cancelled, continuing with semantic results only")
    except Exception as exc:
        logger_instance.warning(
            "Pattern match failed, continuing with semantic results only: %s", exc,
        )
    finally:
        if run_timer is not None:
            run_timer.record("pattern_match_wait", (time.perf_counter() - wait_started) * 1000.0)
    return []


async def cancel_task_if_running(task: asyncio.Task | None) -> None:
    """Cancel an asyncio task if it hasn't finished yet and suppress errors."""
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _int_field(record: dict[str, Any] | None, key: str) -> int:
    try:
        return int((record or {}).get(key) or 0)
    except (ValueError, TypeError):
        return 0


def _fusion_score(record: dict[str, Any] | None) -> float:
    try:
        return float((record or {}).get("fusion_score") or 0.0)
    except (ValueError, TypeError):
        return 0.0


def _record_rank(record: dict[str, Any]) -> tuple[int, int]:
    """(distinct search terms matched, total matches), as set by ``find_records``.

    Distinct terms come first: a record that repeats the company name fifty
    times must not outrank one that also contains the rare query term.
    """
    return _int_field(record, "matched_terms"), _int_field(record, "match_count")


def keyword_match_rank(record: dict[str, Any]) -> tuple[float, int, int]:
    """Fused rank across commands first, then this record's own match counts."""
    return (_fusion_score(record), *_record_rank(record))


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


async def _excluded_demo_apps(
    graph_provider: IGraphDBProvider,
    config_service: ConfigurationService | None,
    org_id: str,
    user_id: str,
    logger_instance: logging.Logger,
) -> frozenset[str]:
    if config_service is None:
        return frozenset()
    try:
        return await excluded_demo_connector_ids(graph_provider, config_service, org_id, user_id)
    except Exception:
        logger_instance.warning(
            "Pattern match: demo data setting unreadable for %s", user_id, exc_info=True,
        )
        return frozenset()


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
    filters: dict[str, Any] | None = None,
    config_service: ConfigurationService | None = None,
) -> list[dict]:
    """Dedup → permission check → time-range filter → fetch blob → flatten.

    The permission check is the one semantic search uses
    (``filter_accessible_virtual_record_ids``): APP_LEVEL apps and
    RECORD_GROUP_LEVEL groups are trusted, everything else is resolved per
    record, a VRID shared across connectors resolves to a record the user can
    read, and *filters*' ``apps``/``kb`` scope bounds which record may be cited.

    When *time_range* is set, graph records are fetched first (lightweight)
    and filtered before the expensive blob fetch.
    """
    scope = requested_scope_ids(filters)
    if scope is None and filters and filters.get(STRICT_SCOPE_FILTER_KEY):
        return []

    # Rank across connectors before the cap; each connector's list is already
    # ranked, but concatenation order would otherwise decide who is dropped.
    unique = _best_hit_per_record(raw_records)
    new_records = [
        r
        for r in unique
        if r.get("virtual_record_id") not in virtual_record_id_to_result
    ][:max_records]

    if not new_records:
        return []

    # Empty trust sets mean full adjudication for every record: slower, never wider.
    trusted_app_ids: frozenset[str] = frozenset()
    trusted_group_ids: frozenset[str] = frozenset()
    try:
        containers = await graph_provider.get_accessible_containers(
            user_id=user_id, org_id=org_id,
        )
        if containers is not None and not containers.fallback_reason:
            trusted_app_ids = containers.app_ids_trusted
            trusted_group_ids = containers.record_group_ids_trusted
    except Exception:
        logger_instance.debug("Pattern match: container lookup failed", exc_info=True)

    try:
        accessible_vrids = await graph_provider.filter_accessible_virtual_record_ids(
            [r["virtual_record_id"] for r in new_records],
            user_id,
            org_id,
            trusted_app_ids=trusted_app_ids,
            trusted_group_ids=trusted_group_ids,
            scope_connector_ids=frozenset(scope) if scope is not None else None,
        ) or {}
    except Exception:
        # Fail closed: semantic results still answer the turn.
        logger_instance.warning(
            "Pattern match: permission check failed for %d records", len(new_records),
            exc_info=True,
        )
        return []

    if not accessible_vrids:
        logger_instance.info(
            "Pattern match: %d records checked, none accessible", len(new_records)
        )
        return []

    accessible_records = [
        r for r in new_records if r.get("virtual_record_id") in accessible_vrids
    ]
    logger_instance.info(
        "Pattern match: %d accessible of %d (%d raw)",
        len(accessible_records), len(new_records), len(raw_records),
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

    excluded_apps = await _excluded_demo_apps(
        graph_provider, config_service, org_id, user_id, logger_instance,
    )
    # The rules fetch_full_record applies when the record is opened by id.
    graph_by_vrid = {
        vrid: rec for vrid, rec in graph_by_vrid.items()
        if rec.get("connectorId") not in excluded_apps
        and rec.get("indexingStatus") == ProgressStatus.COMPLETED.value
    }

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

    matches = _best_match_by_vrid(raw_records)

    results: list[dict] = []
    for rec in accessible_records:
        vrid = rec["virtual_record_id"]
        graph_rec = graph_by_vrid.get(vrid)
        if not graph_rec:
            continue
        graph_rec = {**graph_rec}
        mc, snippet = matches.get(vrid, (0, None))
        if mc > 0:
            graph_rec["_match_count"] = mc
        if snippet:
            graph_rec["_match_snippet"] = snippet
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


def _best_hit_per_record(raw_records: list[dict]) -> list[dict]:
    """Each record's best-ranked grep hit, best first; ties keep the order
    the hits arrived in."""
    best: dict[str, dict] = {}
    for raw in raw_records:
        vrid = raw.get("virtual_record_id")
        if vrid and (vrid not in best or keyword_match_rank(raw) > keyword_match_rank(best[vrid])):
            best[vrid] = raw
    return sorted(best.values(), key=keyword_match_rank, reverse=True)


def _best_match_by_vrid(raw_records: list[dict]) -> dict[str, tuple[int, str | None]]:
    """Per record: the match count of its best-ranked hit, and the preview of
    its best-ranked hit that has one.

    Taken apart because the best hit need not carry a preview; tying the two
    lost a preview another hit had, depending on the order hits arrived in.
    """
    best_rank: dict[str, tuple[int, int]] = {}
    preview_rank: dict[str, tuple[int, int]] = {}
    previews: dict[str, str] = {}
    for raw in raw_records:
        vrid = raw.get("virtual_record_id")
        if not vrid:
            continue
        rank = _record_rank(raw)
        if vrid not in best_rank or rank > best_rank[vrid]:
            best_rank[vrid] = rank
        preview = raw.get("match_preview")
        if preview and (vrid not in preview_rank or rank > preview_rank[vrid]):
            preview_rank[vrid] = rank
            previews[vrid] = preview
    return {vrid: (rank[1], previews.get(vrid)) for vrid, rank in best_rank.items()}


def grep_hits_on_loaded_records(
    raw_records: list[dict],
    virtual_record_id_to_result: Mapping[str, Any],
) -> list[dict]:
    """Hint entries for grep hits on records semantic search already loaded,
    best first.

    ``merge_pattern_match_results`` skips these records because their blocks
    are about to be shown. When the search result then leaves one out to fit
    its size, these entries name it in the keyword hint instead, so its match
    can still be fetched. Only records already in the map qualify, and those
    passed the semantic search's own permission check.
    """
    matches = _best_match_by_vrid(raw_records)
    entries: list[dict] = []
    for raw in _best_hit_per_record(raw_records):
        vrid = raw["virtual_record_id"]
        if not isinstance(virtual_record_id_to_result.get(vrid), dict):
            continue
        entry: dict[str, Any] = {"virtual_record_id": vrid, "score": 0.0, "source": "pattern_match"}
        match_count, snippet = matches[vrid]
        if match_count > 0:
            entry["_match_count"] = match_count
        if snippet:
            entry["_match_snippet"] = snippet
        entries.append(entry)
    return entries


def grep_hits_within(
    entries: list[dict], virtual_record_id_to_result: Mapping[str, Any], max_chars: int,
) -> list[dict]:
    """The leading ``entries`` whose hint sections fit in ``max_chars``.

    Bounds what naming records adds to a result, so leaving more records out
    can never grow the hint past the room it frees.
    """
    kept: list[dict] = []
    used = 0
    for entry in entries:
        section = _hint_section(entry, virtual_record_id_to_result)
        if section is None:
            continue
        used += len(section) + 1
        if used > max_chars:
            break
        kept.append(entry)
    return kept


def _render_loaded_record_metadata(record: dict[str, Any]) -> str:
    """The metadata a semantic search result shows for ``record``."""
    return record.get("context_metadata") or (
        f"Record ID: {record.get('id') or 'N/A'}\nName: {record.get('record_name') or 'N/A'}"
    )


def _render_graph_record_metadata(graph_rec: dict[str, Any]) -> str:
    """Build a metadata block from a graph record by delegating to
    ``Record.to_llm_context()`` so the LLM receives the same fields
    it gets for semantic search records."""
    try:
        record_dict = _build_record_dict_from_graph_base(graph_rec)
        record_instance = create_record_instance_from_dict(record_dict)
        if record_instance:
            context = record_instance.to_llm_context()
            extension = graph_rec.get("extension")
            if not extension:
                mime_type = graph_rec.get("mimeType")
                if mime_type:
                    ext_map = {"text/html": "html", "application/pdf": "pdf"}
                    extension = ext_map.get(mime_type)
            if extension and "File Information:" not in context:
                context += "\nFile Information:\n* Extension: " + extension
            return context
    except Exception:
        logger.debug(
            "Failed to build Record instance for graph doc %s, using fallback",
            graph_rec.get("id") or graph_rec.get("_key"),
            exc_info=True,
        )
    record_id = graph_rec.get("id") or graph_rec.get("_key") or "N/A"
    record_name = graph_rec.get("recordName") or graph_rec.get("title") or "N/A"
    connector_name = graph_rec.get("connectorName") or "N/A"
    record_type = graph_rec.get("recordType") or "N/A"
    external_id = graph_rec.get("externalRecordId") or "N/A"
    connector_id = graph_rec.get("connectorId") or "N/A"
    lines = [
        f"Record ID: {record_id}", f"Name: {record_name}",
        f"Connector: {connector_name}", f"Type: {record_type}",
        f"External ID: {external_id}", f"Connector ID: {connector_id}",
    ]
    mime_type = graph_rec.get("mimeType")
    web_url = graph_rec.get("webUrl")
    if mime_type:
        lines.append(f"MIME Type: {mime_type}")
    if web_url:
        lines.append(f"Web URL: {web_url}")
    return "\n".join(lines)


def _hint_section(entry: dict, virtual_record_id_to_result: Mapping[str, Any]) -> str | None:
    """One record's ``<record>`` block in the keyword hint, or None when the
    record is not in the map."""
    graph_rec = virtual_record_id_to_result.get(entry.get("virtual_record_id", ""))
    if not graph_rec:
        return None
    # A record semantic search loaded holds its blocks; a grep-only one holds
    # the graph document.
    if "block_containers" in graph_rec:
        metadata_block = _render_loaded_record_metadata(graph_rec)
    else:
        metadata_block = _render_graph_record_metadata(graph_rec)
    mc = graph_rec.get("_match_count") or entry.get("_match_count") or 0
    mc_line = f"\nKeyword Matches: {mc}" if mc > 1 else ""
    snippet = graph_rec.get("_match_snippet") or entry.get("_match_snippet")
    snippet_line = f"\nMatched Text: {snippet}" if snippet else ""
    return f"<record>\n{metadata_block}{mc_line}{snippet_line}\n</record>"


def render_pattern_match_hint(
    pm_records: list[dict],
    virtual_record_id_to_result: dict[str, dict],
    fetch_tool_ref: str = "knowledgegraph__fetch_record",
    max_records: int = _MAX_PATTERN_MATCH_RECORDS,
    has_semantic_blocks: bool = True,
) -> str:
    """Render pattern-matched records with full metadata and a fetch hint.

    Each record is wrapped in ``<record>`` tags with the same metadata
    fields the LLM receives for semantic search records, so pattern
    match records are equally actionable.  The section ends with a
    call-to-action telling the LLM to call ``fetch_tool_ref`` for any
    record whose full content it needs.

    When *has_semantic_blocks* is False the header is more directive,
    telling the agent to pick the best-named record and fetch it
    immediately instead of running more searches.

    *max_records* caps how many records are rendered (default
    ``_MAX_PATTERN_MATCH_RECORDS``).
    """
    if not pm_records:
        return ""

    capped = pm_records[:max_records]

    sections = [
        section for entry in capped
        if (section := _hint_section(entry, virtual_record_id_to_result)) is not None
    ]

    if not sections:
        return ""

    if has_semantic_blocks:
        header = (
            "\n\nAdditional records found via pattern matching (metadata only — "
            "no content blocks loaded yet):"
        )
    else:
        header = (
            "\n\nRecords found via keyword matching (metadata only — "
            "no content blocks loaded yet). Review the record names below "
            "and fetch the most relevant one(s) directly — no further "
            "search calls needed:"
        )
    footer = (
        f"\nTo read the full content of any record above, call "
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
