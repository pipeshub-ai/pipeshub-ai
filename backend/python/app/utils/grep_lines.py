"""Line-level matches inside one stored record, for the grep API.

The pattern-match layer decides which records contain the pattern; this finds
where. A record is a list of blocks. Prose blocks are numbered as one running
text. A code block carries its own source lines (``code_metadata.start_line``),
so its lines keep the file's numbering, and the smallest symbol block around a
line names the function or class it sits in.
"""
from __future__ import annotations

import asyncio
import functools
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    import regex

# One regex search may take this long; a pattern that runs past it matches nothing.
LINE_MATCH_TIMEOUT_SECS = 0.05
# A record is given up on after this many lines timed out: the pattern is too
# costly for its text, and every further line would cost the same.
MAX_LINE_TIMEOUTS = 3
_QUICK_SECS = 0.001
MAX_LINE_CHARS = 400
# A line is searched up to this many characters: the regex time limit only works inside backtracking, so a very long line
# (a record stored as one line) would otherwise be scanned in full. Matches further along are missed and the answer says so.
MAX_SCAN_CHARS = 20_000


class _Line(NamedTuple):
    number: int
    text: str
    block_index: int
    position: int  # index into the record's blocks


class _Lines:
    """A record's lines as parallel lists, built without one object per line:
    a record with many lines would otherwise cost the process a full garbage
    collection, which stalls the event loop for as long as the heap is large."""

    def __init__(self) -> None:
        self.numbers: list[int] = []
        self.texts: list[str] = []
        self.block_indexes: list[int] = []
        self.positions: list[int] = []

    def __len__(self) -> int:
        return len(self.texts)

    def __getitem__(self, i: int) -> _Line:
        return _Line(self.numbers[i], self.texts[i], self.block_indexes[i], self.positions[i])


def _block_text(block: dict[str, Any]) -> str:
    data = block.get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        # A table row; its natural-language form is what was indexed.
        text = data.get("row_natural_language_text")
        if isinstance(text, str):
            return text
        # A code block keeps its source in data.text; the per-file summary block is derived text, not file content
        text = data.get("text")
        return text if isinstance(text, str) and block.get("type") != "record_summary" else ""
    return ""


def _code_meta(block: Any) -> dict[str, Any]:  # noqa: ANN401 - stored blocks are not guaranteed dicts
    meta = block.get("code_metadata") if isinstance(block, dict) else None
    return meta if isinstance(meta, dict) else {}


def _split_lines(text: str) -> list[str]:
    """Lines as a file counts them: ``\\n`` only (``str.splitlines`` also splits on form
    feeds, ``\\x1c``-``\\x1e``, ``\\x85`` and U+2028, which shifts every later line number)."""
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    return lines


def record_lines(blocks: list[dict[str, Any]]) -> _Lines:
    lines = _Lines()
    counter = 0
    for index, block in enumerate(blocks or []):
        if not isinstance(block, dict):
            continue
        text = _block_text(block)
        if not text:
            continue
        start = _code_meta(block).get("start_line")
        for offset, line in enumerate(_split_lines(text)):
            counter += 1
            number = start + offset if isinstance(start, int) else counter
            lines.numbers.append(number)
            lines.texts.append(line)
            lines.block_indexes.append(int(block.get("index", index) or index))
            lines.positions.append(index)
    return lines


def _symbol_at(blocks: list[dict[str, Any]], number: int) -> dict[str, Any] | None:
    """The smallest code block whose line range holds ``number``."""
    best: tuple[int, dict[str, Any]] | None = None
    for block in blocks:
        meta = _code_meta(block)
        start, end = meta.get("start_line"), meta.get("end_line")
        if not (isinstance(start, int) and isinstance(end, int) and start <= number <= end):
            continue
        if not (meta.get("qualified_name") or meta.get("kind")):
            continue
        if best is None or end - start < best[0]:
            best = (end - start, block)
    if best is None:
        return None
    meta = _code_meta(best[1])
    return {
        "kind": meta.get("kind"),
        "name": meta.get("qualified_name"),
        "signature": meta.get("signature"),
        "startLine": meta.get("start_line"),
        "endLine": meta.get("end_line"),
    }


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + "..."


def _context(lines: _Lines, at: int, step: int, count: int) -> list[str]:
    out: list[str] = []
    expected = lines.numbers[at]
    for i in range(1, count + 1):
        j = at + step * i
        expected += step
        if not 0 <= j < len(lines) or lines.numbers[j] != expected:
            break
        out.append(_clip(lines.texts[j]))
    return out[::-1] if step < 0 else out


def find_matches(
    blocks: list[dict[str, Any]],
    pattern: regex.Pattern[str],
    *,
    context_lines: int,
    max_matches: int,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], int, str | None, bool]:
    """``(matches, total_matching_lines, language, complete)``. ``matches`` is
    cut at ``max_matches``; the total counts every matching line it reached.

    Meant to run in a worker thread. ``deadline`` is a ``time.monotonic()``
    time; past it, or after ``MAX_LINE_TIMEOUTS`` slow lines, the rest of the
    record is left unread and ``complete`` is False.
    """
    lines = record_lines(blocks)
    matches: list[dict[str, Any]] = []
    total = 0
    timeouts = 0
    complete = True
    for at, text in enumerate(lines.texts):
        budget = LINE_MATCH_TIMEOUT_SECS
        if deadline is not None:
            budget = min(budget, deadline - time.monotonic())
            if budget <= 0:
                complete = False
                break
        scanned = text
        if len(text) > MAX_SCAN_CHARS:
            scanned = text[:MAX_SCAN_CHARS]
            complete = False
        try:
            try:
                # Most lines are quick, and releasing the GIL for each makes threads
                # queue behind each other: try without, for a millisecond.
                found = pattern.search(scanned, timeout=min(budget, _QUICK_SECS))
            except TimeoutError:
                if budget <= _QUICK_SECS:
                    raise
                # A slow line: from here concurrent=True lets other threads (the
                # event loop) run during the search.
                found = pattern.search(scanned, timeout=budget, concurrent=True)
            if not found:
                continue
            # A match touching the cut may exist only because the line was cut
            # (`foo$`); the full line is not scanned, so the hit is dropped.
            if scanned is not text and found.end() >= len(scanned):
                continue
        except TimeoutError:
            complete = False
            timeouts += 1
            if timeouts >= MAX_LINE_TIMEOUTS:
                break
            continue
        total += 1
        if len(matches) >= max_matches:
            continue
        line = lines[at]
        match: dict[str, Any] = {
            "line": line.number,
            "text": _clip(line.text),
            "before": _context(lines, at, -1, context_lines),
            "after": _context(lines, at, 1, context_lines),
            "blockIndex": line.block_index,
        }
        symbol = _symbol_at(blocks, line.number) if _code_meta(blocks[line.position]) else None
        if symbol:
            match["symbol"] = symbol
        matches.append(match)
    language = next(
        (m["language"] for b in blocks if isinstance(b, dict) and (m := _code_meta(b)).get("language")), None,
    )
    return matches, total, language, complete


# Line matching is pure Python and holds the GIL between regex calls. A few
# workers for the whole process keep the event loop's turns short however many
# records are in flight; time spent queued counts against the caller's deadline.
_POOL_WORKERS = 4
_POOL = ThreadPoolExecutor(max_workers=_POOL_WORKERS, thread_name_prefix="grep-lines")
# Records in flight per request (requests are told apart by their deadline, or
# by *owner*): each gets an equal share of the workers, so one request with an
# expensive pattern cannot leave the others queued until their deadlines pass.
_pending: dict[object, int] = {}  # calls waiting for or using a worker, per request
_running: dict[object, int] = {}  # calls using a worker, per request
_waiters: list[asyncio.Future[None]] = []
_slow: dict[object, float] = {}  # requests that hit a timeout (their patterns are costly) -> last start
_SLOW_PAUSE_SECS = 0.3


def _may_start(key: object) -> bool:
    if sum(_running.values()) >= _POOL_WORKERS:
        return False
    share = max(1, _POOL_WORKERS // len(_pending))
    if key in _slow and any(k not in _slow for k in _pending):
        # A costly request takes one worker at a time, and pauses between records,
        # so the cheap ones waiting behind it are not starved.
        return _running.get(key, 0) == 0 and time.monotonic() - _slow[key] >= _SLOW_PAUSE_SECS
    if _running.get(key, 0) < share:
        return True
    # Over its share: only with a worker nobody else is entitled to.
    return not any(_pending[k] > _running.get(k, 0) and _running.get(k, 0) < share for k in _pending if k != key)


def _release(table: dict[object, int], key: object) -> None:
    table[key] -= 1
    if not table[key]:
        del table[key]
    if table is _running:
        for waiter in _waiters:
            if not waiter.done():
                waiter.set_result(None)


async def find_matches_bounded(
    *args: Any, owner: object = None, **kwargs: Any,  # noqa: ANN401
) -> tuple[list[dict[str, Any]], int, str | None, bool]:
    """``find_matches`` on the shared worker pool, off the event loop."""
    key = owner if owner is not None else kwargs.get("deadline")
    deadline = kwargs.get("deadline")
    loop = asyncio.get_running_loop()
    _pending[key] = _pending.get(key, 0) + 1
    try:
        while not _may_start(key):
            if deadline is not None and time.monotonic() >= deadline:
                return [], 0, None, False  # waited out: nothing read
            waiter = loop.create_future()
            _waiters.append(waiter)
            try:
                await asyncio.wait_for(waiter, timeout=0.05)
            except asyncio.TimeoutError:
                pass
            finally:
                _waiters.remove(waiter)
        _running[key] = _running.get(key, 0) + 1
        if key in _slow:
            _slow[key] = time.monotonic()
        try:
            result = await loop.run_in_executor(_POOL, functools.partial(find_matches, *args, **kwargs))
        finally:
            _release(_running, key)
        if not result[3]:
            _slow[key] = time.monotonic()
        return result
    finally:
        _release(_pending, key)
        if key not in _pending:
            _slow.pop(key, None)


def _skip_class(pattern: str, i: int) -> int:
    """Index just past the ``[...]`` set that starts at ``pattern[i]``."""
    n = len(pattern)
    j = i + 1
    if pattern[j : j + 1] == "^":
        j += 1
    if pattern[j : j + 1] == "]":
        j += 1
    while j < n and pattern[j] != "]":
        j += 2 if pattern[j] == "\\" else 1
    return j + 1


def _skip_group(pattern: str, i: int) -> int:
    """Index just past the group that opens at ``pattern[i]``."""
    depth, j, n = 0, i, len(pattern)
    while j < n:
        c = pattern[j]
        if c == "\\":
            j += 2
            continue
        if c == "[":
            j = _skip_class(pattern, j)
            continue
        depth += (c == "(") - (c == ")")
        j += 1
        if depth == 0:
            break
    return j


# What the storage writer never escapes (JSON escapes only ", \\ and control
# characters) and that is not special in a pattern.
_SAFE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _-:,;=@#%&!~<>/'")
# Letters with a non-ASCII case variant (KELVIN SIGN, LONG S, dotted and dotless I): not used when case is ignored.
_NON_ASCII_FOLD = frozenset("kKsSiI")
MIN_PREFILTER_CHARS = 3
# How the storage writer spells a record it compressed (over the size threshold):
# its text is base64 zstd, so no prefilter can see it, and it is always read.
COMPRESSED_MARK = '"isCompressed": true'
_NO_ARG_ESCAPES = frozenset("dDwWsSbBAZ")


def _safe_runs(text: str, *, ignore_case: bool) -> list[str]:
    runs: list[str] = []
    run: list[str] = []
    for c in text:
        if c in _SAFE and not (ignore_case and c in _NON_ASCII_FOLD):
            run.append(c)
        elif run:
            runs.append("".join(run))
            run = []
    if run:
        runs.append("".join(run))
    return runs


def literal_prefilter(text: str, *, ignore_case: bool) -> str:
    """The longest run of *text* that is safe to search the stored file for."""
    best = max(_safe_runs(text, ignore_case=ignore_case), key=len, default="")
    return best if len(best) >= MIN_PREFILTER_CHARS else ""


def ere_prefilter(pattern: str, *, ignore_case: bool = False) -> tuple[str, bool]:
    """``(literal, ignore_case)`` for a regular expression: a fixed string every
    match must contain and the stored file holds as it is, for grep to pick the
    records worth reading (Python decides the lines).

    Deliberately narrow, because a wrong guess silently loses records: only
    runs of ASCII letters, digits, space and plain punctuation taken from the
    top level of the pattern count (not from sets, groups, escapes or anything
    followed by a quantifier). Case-insensitive patterns also leave out ``k``
    and ``s`` (non-ASCII case variants). Anything unusual (inline flags other
    than a leading ``(?i)``, nested sets, escapes with arguments, full case
    folding, verbose mode, alternation) gives ``""``: every record is read.
    The pattern must already be a valid ``regex`` pattern.
    """
    import regex as _regex

    flags = _regex.compile(pattern, _regex.IGNORECASE if ignore_case else 0).flags
    ignore_case = bool(flags & _regex.IGNORECASE)
    if flags & (_regex.VERBOSE | _regex.FULLCASE | _regex.VERSION1 | _regex.LOCALE):
        return "", ignore_case
    if pattern.startswith("(?i)"):
        pattern = pattern[4:]
    runs: list[str] = []
    run: list[str] = []
    i, n = 0, len(pattern)

    def end() -> None:
        nonlocal run
        if run:
            runs.append("".join(run))
        run = []

    while i < n:
        c = pattern[i]
        if c == "\\":
            e = pattern[i + 1 : i + 2]
            if not (e in _NO_ARG_ESCAPES or (e and not e.isalnum())):
                return "", ignore_case  # \x41, \u00e9, \pL, \N{...}, \101: arguments, not text
            end()
            i += 2
        elif c == "[":
            j = _skip_class(pattern, i)
            if "[" in pattern[i + 1 : j - 1].replace("[:", "").replace("\\[", ""):
                return "", ignore_case  # a nested set (set operations)
            end()
            i = j
        elif c == "(":
            if pattern.startswith("(?", i) and not pattern.startswith("(?:", i):
                return "", ignore_case
            end()
            i = _skip_group(pattern, i)
        elif c == "|":
            return "", ignore_case
        elif c in "*+?{":
            if c == "{":
                i = pattern.find("}", i) + 1 or i + 1
            else:
                i += 1
            run = run[:-1]  # the quantified atom is not required as written
            end()
        elif c in _SAFE:
            run.append(c)
            i += 1
        else:
            end()
            i += 1
    end()
    best = ""
    for r in runs:
        for part in _safe_runs(r, ignore_case=ignore_case):
            if len(part) > len(best):
                best = part
    return (best if len(best) >= MIN_PREFILTER_CHARS else ""), ignore_case
