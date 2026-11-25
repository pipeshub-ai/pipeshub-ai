"""What the budget counts a call's text as, in tokens (WP-66): one counter for the input a call reserves on and for
the estimate a call is charged when its provider reports no usage, so the two never disagree.

An OpenAI-family model is counted with its own BPE (tiktoken's ``o200k_base`` or ``cl100k_base``). The BPE is read
only from a file already on this machine whose SHA-256 is the one tiktoken pins for it, never fetched: tiktoken's
own cache (``TIKTOKEN_CACHE_DIR``) or the copy the installed ``litellm`` package ships. Any other model, or an
OpenAI one without the file, takes ``dense_tokens``: a fallback that counts code, digits, non-Latin scripts and emoji
densely and is never below the old four-characters-a-token estimate.

Counts of long texts are remembered by content (an engine resends its whole history with one turn appended), so a
call costs the tokenizer only for what it has not seen.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.kernel.core.messages import ImagePart
from harness_core.kernel.core.tokens import count_image_tokens, extract_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.tool_schema import ToolSchema

# Every message costs a few tokens of protocol (role marker, separators) on top of its text, as in ``kernel.core.tokens``.
MESSAGE_OVERHEAD_TOKENS = 4

# tiktoken's pins (``tiktoken_ext.openai_public``): the file's URL names it in a cache, its digest is checked here.
_ENCODINGS: dict[str, tuple[str, str]] = {
    "o200k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken",
        "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
    ),
    "cl100k_base": (
        "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken",
        "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7",
    ),
}
# ``o200k_harmony`` (gpt-oss) is ``o200k_base`` with more special tokens, which plain text never produces.
_SAME_RANKS = {"o200k_harmony": "o200k_base"}
_O200K_PATTERN = "|".join(
    [
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
        r"""\p{N}{1,3}""",
        r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
        r"""\s*[\r\n]+""",
        r"""\s+(?!\S)""",
        r"""\s+""",
    ]
)
_CL100K_PATTERN = (
    r"""'(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s"""
)
_PATTERNS = {"o200k_base": _O200K_PATTERN, "cl100k_base": _CL100K_PATTERN}
_SPECIALS = {
    "o200k_base": {"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    "cl100k_base": {
        "<|endoftext|>": 100257,
        "<|fim_prefix|>": 100258,
        "<|fim_middle|>": 100259,
        "<|fim_suffix|>": 100260,
        "<|endofprompt|>": 100276,
    },
}
# A model routed through an aggregator keeps its vendor's name after the prefix.
_VENDOR_PREFIXES = ("openai/", "azure/")
# OpenAI models newer than the installed tiktoken's table (gpt-6.1-sol on Azure, WP81-FU-2). Every OpenAI family
# since gpt-4o counts with o200k; without this they took the dense fallback, which reads English and code 1.2-1.3
# times o200k: a long research run's cut call was estimated at 192,368 prompt tokens where the provider's previous prompt
# was 149,817 plus one step.
_LATER_O200K = re.compile(r"^(?:gpt-(?:[5-9]|[1-9][0-9])|o[1-9](?:$|-)|codex-)")

_LETTERS = re.compile(r"[A-Za-z]+")
# Each of these is about a token of its own in every BPE measured: a digit group, a line break, one ASCII symbol.
_PIECES = re.compile(r"[0-9]{1,3}|[\r\n]+|[!-/:-@\[-`{-~]")
_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_ASTRAL = re.compile(r"[\U00010000-\U0010ffff]")
_LETTERS_PER_TOKEN = 6
_CHARS_PER_TOKEN = 4

# Texts shorter than this are counted again (cheaper than remembering them).
_REMEMBER_FROM_CHARS = 256
_REMEMBERED = 8_192


def dense_tokens(text: str) -> int:
    """The fallback count: a token per six letters of a Latin word, per digit group, line break or ASCII symbol,
    per two UTF-8 bytes of any other script and one more per emoji (a character beyond the BMP); never fewer than a
    token per four characters. Measured against o200k and cl100k on English, code, JSON, CJK, Cyrillic, Arabic and
    emoji it is at or above both on every one but cl100k on emoji (0.75 of it) and within 1.5 of the larger on all
    Latin-script text."""
    if not text:
        return 0
    letters = sum(-(-len(run) // _LETTERS_PER_TOKEN) for run in _LETTERS.findall(text))
    pieces = len(_PIECES.findall(text))
    other = 0
    if not text.isascii():
        wide = len(_NON_ASCII.findall(text))
        wide_bytes = len(text.encode("utf-8", "surrogatepass")) - (len(text) - wide)
        other = -(-wide_bytes // 2) + len(_ASTRAL.findall(text))
    return max(len(text) // _CHARS_PER_TOKEN, letters + pieces + other)


class _Bpe:
    """One encoding, loaded on its first use from a verified local file; absent when there is none."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._loaded = False
        self._encoding: Any = None

    def encoding(self) -> Any:  # noqa: ANN401 - tiktoken's Encoding, untyped
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._encoding = _load(self.name)
                    self._loaded = True
        return self._encoding


def _bpe_dirs() -> list[Path]:
    dirs: list[Path] = []
    cache = os.environ.get("TIKTOKEN_CACHE_DIR")
    dirs.append(Path(cache) if cache else Path(tempfile.gettempdir()) / "data-gym-cache")
    # Found without importing litellm: only its package data is read.
    spec = importlib.util.find_spec("litellm")
    locations = (spec.submodule_search_locations or []) if spec is not None else []
    dirs.extend(Path(location) / "litellm_core_utils" / "tokenizers" for location in locations)
    return dirs


def _load(name: str) -> Any:  # noqa: ANN401 - tiktoken's Encoding, untyped
    try:
        import tiktoken
    except ImportError:
        return None
    url, digest = _ENCODINGS[name]
    file_name = hashlib.sha1(url.encode()).hexdigest()
    for folder in _bpe_dirs():
        path = folder / file_name
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest() != digest:
            continue
        ranks = {base64.b64decode(token): int(rank) for token, rank in (line.split() for line in raw.splitlines() if line)}
        encoding = tiktoken.Encoding(name, pat_str=_PATTERNS[name], mergeable_ranks=ranks, special_tokens=_SPECIALS[name])
        # The Rust core holds the ranks; tiktoken keeps a Python copy (about 30 MB for o200k) only for methods
        # this counter never calls, so a process that counts keeps the smaller footprint.
        encoding._mergeable_ranks = None  # pyright: ignore[reportAttributeAccessIssue]
        return encoding
    return None


_BPES = {name: _Bpe(name) for name in _ENCODINGS}


def encoding_for(model: str | None) -> str | None:
    """The BPE an OpenAI-family model counts with, by tiktoken's own model table; ``None`` for any other model."""
    if not model:
        return None
    try:
        from tiktoken.model import encoding_name_for_model
    except ImportError:
        return None
    name = model.strip().lower()
    for prefix in _VENDOR_PREFIXES:
        name = name.removeprefix(prefix)
    try:
        found = encoding_name_for_model(name)
    except KeyError:
        if not _LATER_O200K.match(name):
            return None
        found = "o200k_base"
    found = _SAME_RANKS.get(found, found)
    return found if found in _BPES else None


class TokenCounter:
    """Counts text for one model: its BPE when it has one here, else ``dense_tokens``."""

    def __init__(self, model: str | None) -> None:
        bpe_name = encoding_for(model)
        self._encoding: Any = _BPES[bpe_name].encoding() if bpe_name is not None else None
        self.method: str = bpe_name if bpe_name is not None and self._encoding is not None else "dense"

    def text(self, text: str) -> int:
        if not text:
            return 0
        if len(text) < _REMEMBER_FROM_CHARS:
            return self._count(text)
        key = (self.method, len(text), hash(text))
        with _memo_lock:
            known = _memo.get(key)
            if known is not None:
                _memo.move_to_end(key)
                return known
        counted = self._count(text)
        with _memo_lock:
            _memo[key] = counted
            if len(_memo) > _REMEMBERED:
                _ = _memo.popitem(last=False)
        return counted

    def _count(self, text: str) -> int:
        if self._encoding is None:
            return dense_tokens(text)
        return len(self._encoding.encode_ordinary(text))

    def message(self, message: Message) -> int:
        tokens = MESSAGE_OVERHEAD_TOKENS + self.text(extract_text(message))
        for call in getattr(message, "tool_calls", None) or ():
            arguments = call.arguments if isinstance(call.arguments, str) else json.dumps(call.arguments, ensure_ascii=False)
            tokens += self.text(call.name) + self.text(arguments)
        content = getattr(message, "content", None)
        if isinstance(content, list):
            tokens += sum(count_image_tokens(part) for part in content if isinstance(part, ImagePart))  # pyright: ignore[reportUnknownVariableType]
        return tokens

    def tool(self, tool: ToolSchema) -> int:
        return self.text(f"{tool.name}{tool.description}{json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True)}")

    def call_input(self, messages: Sequence[Message], system: str | None, tools: Sequence[ToolSchema]) -> int:
        return sum(self.message(m) for m in messages) + self.text(system or "") + sum(self.tool(t) for t in tools)


_memo: OrderedDict[tuple[str, int, int], int] = OrderedDict()
_memo_lock = threading.Lock()


def counter_for(model: str | None) -> TokenCounter:
    """A counter for ``model``. Building one may load its BPE (about 300 ms the first time) and counting a long
    history takes milliseconds: call it off the event loop (``asyncio.to_thread``), as every money path does."""
    return TokenCounter(model)


def preload_in_background() -> asyncio.Task[tuple[str, ...]]:
    """``preload`` on a worker thread from a process's startup: the start is not held up, and no request pays the
    load on its event loop. The task is held here until it ends, so the caller need not keep it."""
    task = asyncio.get_running_loop().create_task(asyncio.to_thread(preload))
    _preloading.add(task)
    task.add_done_callback(_preloading.discard)
    return task


_preloading: set[asyncio.Task[tuple[str, ...]]] = set()


def preload() -> tuple[str, ...]:
    """Loads every BPE this machine has a verified file for, so no call pays the first load; the ones loaded. A
    process calls it once at startup, off its event loop."""
    return tuple(name for name, bpe in _BPES.items() if bpe.encoding() is not None)


__all__ = ["MESSAGE_OVERHEAD_TOKENS", "TokenCounter", "counter_for", "dense_tokens", "encoding_for", "preload", "preload_in_background"]
