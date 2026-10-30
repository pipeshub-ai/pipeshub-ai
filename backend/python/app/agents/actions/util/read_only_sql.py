"""Gate for toolsets that run SQL the model wrote: what counts as a single, side-effect-free read.

One definition for every caller: anything this module can't positively read as a plain read is not
a read. This classifier is defence in depth, not a sandbox: the denylist of side-effect functions
can't be complete. Give these connectors a SELECT-only database user. Redshift relies on that: no
server-side read-only enforcement there has been verified.

The scan is one linear pass (no backtracking regexes), so hostile input can't stall the event loop.
"""
from __future__ import annotations

import re

_READ_STARTS = frozenset({"SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "EXPLAIN", "TABLE", "VALUES"})
# Above this the text is not a read: a person doesn't write that, and the scan stays cheap.
MAX_SQL_CHARS = 10_000
_WORD = re.compile(r"\w[\w$]*")
# A line comment ends at any of these (dialects differ; ending early only exposes more text as code).
_LINE_END = re.compile("[\n\r\f\x0b\x85\u2028\u2029]")
_BLOCK_EDGE = re.compile(r"/\*|\*/")
# Functions that act on the server, other sessions, files, locks, sequences or the clock rather than
# on table data (a read-only transaction doesn't stop them), and functions that run a query string,
# so the inner statement is never seen here.
_SIDE_EFFECT_NAME = re.compile(
    r"pg_terminate_backend|pg_cancel_backend|pg_reload_conf|pg_rotate_logfile|pg_promote|"
    r"pg_switch_wal|pg_create_restore_point|pg_notify|set_config|pg_read_file|pg_read_binary_file|"
    r"pg_ls_dir|pg_stat_file|pg_sleep\w*|setval|nextval|txid_current|sleep|benchmark|load_file|"
    r"get_lock|release_lock|release_all_locks|master_pos_wait|dblink\w*|lo_\w+|"
    r"pg_(?:try_)?advisory\w*|\w*_to_xml\w*|exec(?:ute)?(?:_\w*)?",
    re.IGNORECASE,
)

_Token = tuple[str, str]  # (kind, text): w word, s string, q quoted identifier, p punctuation


def _block_comment_end(sql: str, start: int, *, nested: bool) -> int:
    """Index after the ``*/`` that closes the comment at ``start``, or -1. Postgres nests block
    comments; MySQL and MariaDB do not."""
    depth, pos = 1, start + 2
    while depth:
        edge = _BLOCK_EDGE.search(sql, pos)
        if edge is None:
            return -1
        depth += 1 if nested and edge.group() == "/*" else -1
        pos = edge.end()
    return pos


def _scan(
    sql: str, *, backslash_escapes: bool, nested_comments: bool, comment_body: bool = False,
) -> tuple[list[_Token], list[str], bool]:
    """``(tokens, ambiguous comment bodies, readable)`` in one pass.

    Comments are dropped. ``#`` and ``--`` without a space are comments in one dialect and code in
    another, so their text is returned to be scanned as code too. ``readable`` is False for anything
    this scanner is unsure of: an unterminated quote or comment, MySQL's executable ``/*! */``
    comments, a backslash or ``$`` outside a string. ``backslash_escapes`` picks the MySQL or the
    standard reading of ``\\`` inside a string; callers need both to agree.
    """
    tokens: list[_Token] = []
    bodies: list[str] = []
    readable = True
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c.isspace():
            i += 1
        elif c == "/" and sql.startswith("/*", i):
            readable = readable and not sql.startswith(("/*!", "/*M!"), i)
            end = _block_comment_end(sql, i, nested=nested_comments)
            if end < 0:
                return tokens, bodies, False
            i = end
        elif c in "-#" and (c == "#" or sql.startswith("--", i)):
            width = 1 if c == "#" else 2
            if comment_body:
                tokens.append(("p", sql[i:i + width]))
                i += width
                continue
            line_end = _LINE_END.search(sql, i)
            end = n if line_end is None else line_end.start()
            if c == "#" or (i + 2 < n and not sql[i + 2].isspace()):
                bodies.append(sql[i + width:end])
            i = end
        elif c in "'\"`":
            j = i + 1
            while j < n:
                if backslash_escapes and sql[j] == "\\" and c != "`":
                    j += 2
                elif sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                    else:
                        break
                else:
                    j += 1
            # A backtick quotes in MySQL only; elsewhere its contents are code. Plain names only.
            readable = readable and j < n and (c != "`" or all(ch.isalnum() or ch in "_$ ." for ch in sql[i + 1:j]))
            tokens.append(("s" if c == "'" else "q", sql[i + 1:j]))
            i = j + 1
        elif c in "\\$":
            readable = False
            i += 1
        elif c.isalnum() or c == "_":
            word = _WORD.match(sql, i)
            tokens.append(("w", word.group()))
            i = word.end()
        elif c == ":" and sql.startswith(":=", i):
            tokens.append(("p", ":="))
            i += 2
        else:
            tokens.append(("p", c))
            i += 1
    return tokens, bodies, readable


def _plain(tokens: list[_Token]) -> str:
    """The tokens as text without comments or literal contents, for the denylist."""
    return "".join({"s": "'_'", "q": '"_"', "w": f" {text} "}.get(kind, text) for kind, text in tokens)


def _acts_on_the_server(tokens: list[_Token]) -> bool:
    for k, (kind, text) in enumerate(tokens):
        following = tokens[k + 1] if k + 1 < len(tokens) else ("", "")
        if kind in ("w", "q") and following == ("p", "(") and _SIDE_EFFECT_NAME.fullmatch(text):
            return True
        if (kind == "w" and text.lower() == "copy") or (kind, text) == ("p", ":="):
            return True
        # U&"..." writes an identifier with escapes, so its name can't be matched.
        if kind == "w" and text.lower() == "u" and following == ("p", "&") and k + 2 < len(tokens) and tokens[k + 2][0] in "qs":
            return True
    return False


def _quotes_an_explain_option(tokens: list[_Token]) -> bool:
    """Whether the parenthesised option list right after EXPLAIN holds a quoted name."""
    if len(tokens) < 2 or tokens[1] != ("p", "("):
        return False
    depth = 0
    for kind, text in tokens[1:]:
        if (kind, text) == ("p", "("):
            depth += 1
        elif (kind, text) == ("p", ")"):
            depth -= 1
            if depth == 0:
                return False
        elif kind == "q":
            return True
    return False


def _streams(sql: str) -> tuple[list[list[_Token]], list[list[_Token]], bool]:
    """``(statement readings, ambiguous comment texts read as code, whether every reading was clear)``.

    The statement readings are the flat-comment one for each backslash reading, then the nested one.

    The text is read each way the dialects differ (backslash escapes in strings, nested block
    comments). Where the readings give different tokens, or one is unclear, it is not a read.
    So wherever a statement is readable, all its readings are one token stream.
    """
    mains: list[list[_Token]] = []
    nested_readings: list[list[_Token]] = []
    comments: list[list[_Token]] = []
    readable = len(sql) <= MAX_SQL_CHARS
    for escapes in (False, True):
        readings = []
        for nested in (False, True):
            tokens, bodies, ok = _scan(sql[:MAX_SQL_CHARS], backslash_escapes=escapes, nested_comments=nested)
            readable = readable and ok
            readings.append(tokens)
            comments.extend(
                _scan(body, backslash_escapes=escapes, nested_comments=nested, comment_body=True)[0] for body in bodies
            )
        readable = readable and readings[0] == readings[1]
        mains.append(readings[0])
        nested_readings.append(readings[1])
    # Every statement-level check reads mains[0], so it must be the only reading there is.
    readable = readable and mains[0] == mains[1]
    return mains + nested_readings, comments, readable


def calls_side_effect_function(sql: str) -> bool:
    """Whether the text calls a function (or uses COPY or ``:=``) that does more than read data."""
    sql = sql or ""
    mains, comments, _ = _streams(sql)
    return len(sql) > MAX_SQL_CHARS or any(map(_acts_on_the_server, mains + comments))


def read_only_statement(query: str) -> tuple[str | None, str]:
    """``(statement, "")`` for a single read statement, else ``(None, reason)``.

    Callers execute the returned text, never the raw query.
    """
    # Imported here: the module pulls in the LLM stack.
    from app.utils.execute_query import _is_query_safe

    statement = (query or "").strip().rstrip(";").strip()
    mains, comments, readable = _streams(statement)
    if not readable:
        return None, "Blocked: the statement can't be read with certainty (comment, quote or escape)"
    if any(map(_acts_on_the_server, mains + comments)):
        return None, "Blocked: this statement calls a function that is not a plain read"
    # `#` and `--x` are comments in one dialect and code in another (Redshift reads `#` as XOR),
    # so any such text makes the statement two different statements: not a read.
    if comments:
        return None, "Blocked: the statement can't be read with certainty (comment, quote or escape)"
    tokens = mains[0]
    if any(token == ("p", ";") for token in tokens):
        return None, "Blocked: send exactly one SQL statement per call."
    first = tokens[0][1].upper() if tokens and tokens[0][0] == "w" else ""
    if first not in _READ_STARTS:
        return None, f"Blocked: only read-only queries are allowed. Got: {first or 'empty'}"
    if any(kind == "w" and text.upper() == "INTO" for kind, text in tokens):
        return None, "Blocked: SELECT ... INTO is not allowed"
    # PostgreSQL reads `EXPLAIN ("analyze")` as the ANALYZE option, and the denylist below only
    # sees quoted names as placeholders, so an option list may not quote anything.
    if first == "EXPLAIN" and _quotes_an_explain_option(tokens):
        return None, "Blocked: EXPLAIN options must be written without quotes"
    # The denylist sees the statement without comments or literal contents.
    safe, reason = _is_query_safe(_plain(tokens))
    return (statement, "") if safe else (None, reason)
