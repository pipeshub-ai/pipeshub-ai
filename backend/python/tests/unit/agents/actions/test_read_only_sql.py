"""What counts as a plain read for the SQL tools, and that deciding it stays fast on hostile text."""
from __future__ import annotations

import time

import pytest

from app.agents.actions.util.read_only_sql import (
    MAX_SQL_CHARS,
    calls_side_effect_function,
    read_only_statement,
)


@pytest.mark.parametrize("sql", [
    "/* monthly revenue */\nSELECT month, SUM(total) FROM orders GROUP BY month",
    "-- monthly revenue\nSELECT month FROM orders",
    "SELECT id FROM t -- newest first",
    "SELECT * FROM t WHERE note LIKE '%;%'",
    "SELECT * FROM t WHERE note = 'log into app'",
    "SELECT * FROM audit WHERE action = 'UPDATE'",
    "SELECT COUNT(*) FROM events WHERE type IN ('delete', 'insert', 'replace')",
    "SELECT id, updated_at FROM t ORDER BY updated_at DESC",
    "SELECT executive_count(dept) FROM d",
    "SELECT data->>'name' FROM t",
    "SELECT * FROM t WHERE code REGEXP '^\\\\d+$'",
    "SELECT 'a\\\\b' AS c",
    "SELECT '#', name FROM t",
    "WITH a AS (SELECT 1 AS x) SELECT * FROM a;",
])
def test_plain_reads_run(sql: str) -> None:
    assert read_only_statement(sql)[0] is not None


@pytest.mark.parametrize("sql", [
    "SELECT sleep(1)",
    "SELECT '#', sleep(1)",
    "SELECT 'a\\'' , sleep(1) , '",
    "SELECT pg_sleep /* x */ (1)",
    "SELECT SLEEP#x\n(1)",
    "SELECT pg_terminate_backend--x\n(1)",
    "SELECT 1 -- x\n; DROP TABLE t",
    "SELECT 1 /*! ; DROP TABLE t */",
    "SELECT `x;DELETE FROM t`",
    "SELECT 1 --x; DELETE FROM t",
    "SELECT 1 # ; DROP TABLE t",
    # Redshift reads `#` as XOR, so the "comment" is code there.
    "SELECT 1 # 2 INTO new_table FROM old",
    "SELECT 1 /* unterminated",
    # Postgres nests block comments, MySQL doesn't: the readings differ, so it's not a read.
    "SELECT 1 /* a /* b */ ' */ , pg_sleep(1) AS x -- '",
    "SELECT 1 /* a /* b */ c */",
    # A clause that is inside a string with standard quoting but code with MySQL backslash escapes.
    "SELECT 'a\\' , ' , SLEEP(1) -- '",
    "SELECT 'a\\' , ' INTO OUTFILE \"/tmp/qa\" -- '",
    "SELECT 'a\\' , ' FROM t FOR UPDATE -- '",
    "SELECT 'a\\' , ' ; SELECT 2 -- '",
    "SELECT 'a\\' , ' FROM (SELECT 1) x WHERE 1 = 1 UNION SELECT 1 FROM t FOR UPDATE -- '",
    # Postgres ends a line comment at CR as well as LF.
    "SELECT 1 -- note\r, pg_sleep(1) AS x",
    "SELECT 1 # note\r, pg_sleep(1) AS x",
    "SELECT 1 -- note\f, pg_sleep(1) AS x",
    "SELECT 1 -- note\u2028, pg_sleep(1) AS x",
    "SELECT 'unterminated",
    "SELECT $$x$$",
    "SELECT * FROM t WHERE a = 1 \\ ",
    "SELECT ';' ; DELETE FROM t",
    "SELECT * INTO backup FROM t",
    "SELECT 1; SELECT 2",
    "SELECT 1 /* a */ ; /* b */ SELECT 2",
])
def test_anything_unsure_is_not_a_read(sql: str) -> None:
    assert read_only_statement(sql)[0] is None


def test_over_the_cap_is_not_a_read() -> None:
    assert read_only_statement("SELECT " + "a" * MAX_SQL_CHARS)[0] is None


HOSTILE = {
    "hashes": "SELECT sleep" + "# " * 5000,
    "hashes_only": "#" * 10_000,
    "dashes": "SELECT sleep" + "-- " * 3000,
    "slashes": "SELECT sleep" + "/* " * 3000,
    "quotes": "SELECT " + "'" * 10_000,
    "backslashes": "SELECT '" + "\\" * 10_000,
    "names": "SELECT " + "sleep, " * 1500,
    "long_word": "SELECT " + "a" * 10_000,
    "parens": "SELECT " + "(" * 10_000,
    "nested_comment_lines": ("SELECT sleep # x\n" * 600),
    "probe": "SELECT sleep" + "# " * 18,
    "just_under_cap_nested": "SELECT 1 " + "/* " * 3300,
    "just_under_cap_cr": "SELECT 1 " + "-- \r" * 2400,
    "just_under_cap_columns": "SELECT " + "a, " * 3300,
    "just_under_cap_quotes": "SELECT " + "'a', " * 2000,
    "just_under_cap_closed_nested": "SELECT 1 " + "/*" * 2400 + "*/" * 2400,
}


@pytest.mark.parametrize("name", HOSTILE)
def test_hostile_text_is_decided_quickly(name: str) -> None:
    read_only_statement("SELECT 1")  # the first call imports the validator
    sql = HOSTILE[name]
    started = time.perf_counter()
    read_only_statement(sql)
    calls_side_effect_function(sql)
    # The bad pattern took seconds (17 s at 40 characters); CI workers with coverage run ~3x slower
    # than a laptop, so the bound only has to separate linear from exponential.
    assert time.perf_counter() - started < 2.0
