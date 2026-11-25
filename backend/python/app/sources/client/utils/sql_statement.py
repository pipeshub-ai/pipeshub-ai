"""Reading a model-written SQL statement just far enough to tell whether it may change something.

Fail closed: anything that does not read as one plain query is a write.
"""

from __future__ import annotations

import re

_READS = re.compile(r"\A\s*(?:SELECT|SHOW|DESCRIBE|DESC|EXPLAIN|USE|WITH)\b", re.IGNORECASE)
# A read verb in front of any of these can still change data: SELECT … INTO creates a table, EXPLAIN ANALYZE runs
# its statement, a WITH can lead into DML, SELECT … FOR UPDATE takes locks.
_WRITES = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|MERGE|REPLACE|UPSERT|CREATE|DROP|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|CALL|EXEC|EXECUTE"
    r"|COPY|UNLOAD|VACUUM|ANALYZE|LOCK|INTO|SET|LOAD)\b",
    re.IGNORECASE,
)


def outside_literals(statement: str) -> str | None:
    """The statement with its single-quoted literals blanked; ``None`` if a literal never closes (the statement
    cannot be read reliably)."""
    kept: list[str] = []
    index = 0
    while index < len(statement):
        char = statement[index]
        if char != "'":
            kept.append(char)
            index += 1
            continue
        index += 1
        while index < len(statement) and statement[index] != "'":
            index += 2 if statement[index] == "\\" else 1
        if index >= len(statement):
            return None
        kept.append(" ")
        index += 1
    return "".join(kept)


def sql_statement_writes(statement: str) -> bool:
    code = outside_literals(statement)
    if code is None:
        return True
    code = code.strip().rstrip(";")
    return ";" in code or not _READS.match(code) or bool(_WRITES.search(code))


__all__ = ["outside_literals", "sql_statement_writes"]
