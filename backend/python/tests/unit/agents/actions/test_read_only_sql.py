"""What counts as a plain read for the SQL tools, and that deciding it stays fast on hostile text."""
from __future__ import annotations

import time

import pytest

from app.agents.actions.util.read_only_sql import (
    MAX_SQL_CHARS,
    calls_side_effect_function,
    read_only_statement,
)
from app.utils.execute_query import _is_query_safe


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
    "SELECT (SELECT COUNT(*) FROM orders) AS n",
    "SELECT * FROM t WHERE id IN (SELECT id FROM u)",
    "SELECT id, ROW_NUMBER() OVER (PARTITION BY g ORDER BY id) FROM t",
    "WITH a AS (SELECT 1 AS x), b AS (SELECT 2 AS y) SELECT * FROM a, b",
    "SELECT * FROM t WHERE ts > NOW() - INTERVAL 7 DAY",
    "SELECT * FROM t WHERE ts > DATEADD(day, -7, GETDATE())",
    "SELECT copy_count, lock_reason, execution_time, created_by FROM t",
    "SELECT * FROM t WHERE status = 'SET'",
    "SELECT status AS \"delete\" FROM t",
    "SELECT * FROM t WHERE name = 'O''Brien'",
    "SELECT * FROM t WHERE code LIKE '%\\_%'",
    "SELECT * FROM t WHERE doc = '{\"a\":\"b\\\"c\"}'",
    "SELECT '--' AS c",
    "select id from t",
    "SeLeCt 1",
    "\tSELECT\n1",
    "SELECT 1;;",
    "SHOW COLUMNS FROM t",
    "SHOW GRANTS",
    "DESCRIBE t",
    "EXPLAIN FORMAT=JSON SELECT 1",
    "EXPLAIN SELECT 1",
    "EXPLAIN (FORMAT JSON) SELECT 1",
    'EXPLAIN SELECT "analyze" FROM t',
    "EXPLAIN (FORMAT JSON, BUFFERS) SELECT 1",
    "EXPLAIN (VERBOSE) SELECT 1",
    "VALUES (1)",
    "TABLE t",
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
    # Statements that change state or take locks start with, or contain, something that is not a read.
    "LOAD DATA INFILE '/x' INTO TABLE t",
    "HANDLER t OPEN",
    "LOCK TABLES t WRITE",
    "DO SLEEP(1)",
    "VACUUM t",
    "ANALYZE t",
    "ANALYZE TABLE t",
    "REFRESH MATERIALIZED VIEW m",
    "PREPARE p AS SELECT 1",
    "EXECUTE p",
    "DO $$ BEGIN NULL; END $$",
    "COPY t TO PROGRAM 'x'",
    "UNLOAD ('SELECT 1') TO 's3://b/k'",
    "EXPLAIN ANALYZE DELETE FROM t",
    "EXPLAIN (ANALYZE) DELETE FROM t",
    "EXPLAIN DELETE FROM t",
    # EXPLAIN that runs the statement, in every spelling and position.
    "EXPLAIN ANALYZE SELECT 1",
    "EXPLAIN ANALYSE SELECT 1",
    "EXPLAIN ANALYSE SELECT my_func()",
    "explain analyse select 1",
    "EXPLAIN\n\tANALYSE\tSELECT 1",
    "EXPLAIN /* c */ ANALYSE SELECT 1",
    "EXPLAIN (ANALYSE) SELECT 1",
    "EXPLAIN (ANALYZE true) SELECT 1",
    "EXPLAIN (analyse on) SELECT 1",
    "EXPLAIN ( ANALYSE ) SELECT 1",
    "EXPLAIN (BUFFERS, ANALYSE) SELECT 1",
    "EXPLAIN (FORMAT JSON, ANALYSE) SELECT 1",
    "EXPLAIN (ANALYSE, VERBOSE) SELECT 1",
    "EXPLAIN ANALYSE VERBOSE SELECT 1",
    "ANALYSE t",
    "ANALYSE",
    "DESC ANALYSE SELECT 1",
    # A quoted option name is the same option to PostgreSQL.
    'EXPLAIN ("analyze") SELECT 1',
    'EXPLAIN ("analyse" true) SELECT 1',
    'EXPLAIN ("verbose", "analyze") SELECT 1',
    'EXPLAIN (VERBOSE, "analyze") SELECT 1',
    "DESC ANALYZE UPDATE t SET a = 1",
    "WITH x AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM x",
    "WITH c AS (SELECT 1 AS id) UPDATE t JOIN c ON t.id = c.id SET t.a = 1",
    "WITH c AS (SELECT 1 AS id) INSERT INTO t SELECT * FROM c",
    "SELECT * FROM t FOR UPDATE",
    "select * from t for update",
    "SELECT * FROM t FOR NO KEY UPDATE",
    "SELECT * FROM t LOCK IN SHARE MODE",
    "SELECT a FROM t INTO OUTFILE '/x'",
    "SELECT 1 /*M!100000 , SLEEP(1) */",
    "(SELECT 1)",
    "SELECT 1 -- note\x00\n, pg_sleep(1) AS x",
    "DR\u200bOP TABLE t",
    "S\u0415LECT 1",  # Cyrillic E: not the keyword
    # Function names that are quoted, qualified, split by a comment or reached through FROM or a CTE.
    "SELECT \"pg_sleep\"(1)",
    "SELECT `SLEEP`(1)",
    "SELECT pg_catalog.pg_terminate_backend(1)",
    "SELECT pg_catalog.\"pg_terminate_backend\"(1)",
    "SELECT U&\"pg_s!006Ceep\" UESCAPE '!' (1)",
    "SELECT pg_/**/sleep(1)",
    "SELECT pg_sleep/**/(1)",
    "SELECT \"pg_sleep\" /* x */ (1)",
    "SELECT * FROM dblink('c', 'select 1') AS t(a int)",
    "WITH x AS (SELECT pg_sleep(1)) SELECT * FROM x",
    "SELECT query_to_xml('select pg_termin' || 'ate_backend(1)', true, false, '')",
    "SELECT dblink_exec('c', 'x')",
    "SELECT lo_import('/x')",
    "SELECT pg_read_file('/x')",
    "SELECT GET_LOCK('k', 10)",
    "SELECT txid_current()",
    # Quote and comment forms where the dialects end the string or comment in different places.
    "SELECT E'a\\'' , pg_sleep(1) AS x -- '",
    "SELECT N'a' , pg_sleep(1) AS x",
    "SELECT $t$ x $t$ , pg_sleep(1) AS x",
    "SELECT \"a\\\" AS y, pg_sleep(1) AS x -- \"",
    "SELECT \"update\" , pg_sleep(1) AS x",
])
def test_anything_unsure_is_not_a_read(sql: str) -> None:
    assert read_only_statement(sql)[0] is None


def test_over_the_cap_is_not_a_read() -> None:
    assert read_only_statement("SELECT " + "a" * MAX_SQL_CHARS)[0] is None


@pytest.mark.parametrize("marker_first", [True, False])
def test_a_side_effect_call_beyond_the_cap_is_still_refused(marker_first: bool) -> None:
    # execute_sql_query checks the denylist on text of any length, so the call can't hide past the cap.
    padding = ", ".join(["1"] * (MAX_SQL_CHARS // 3))
    sql = f"SELECT pg_sleep(1), {padding}" if marker_first else f"SELECT {padding}, pg_sleep(1)"
    assert len(sql) > MAX_SQL_CHARS
    assert calls_side_effect_function(sql)
    assert not _is_query_safe(sql)[0]


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
    "stars": "SELECT " + "*" * 9_900,
    "slashes_only": "SELECT " + "/" * 9_900,
    "dollars": "SELECT " + "$" * 9_900,
    "comment_pairs": "SELECT " + "/**/" * 2_400,
    "mixed_quotes": "SELECT " + "'\"`" * 3_300,
    "backslash_quote_pairs": "SELECT " + "'\\" * 4_900,
    "dash_lines": "SELECT " + "--\n" * 3_300,
    "name_paren_pairs": "SELECT " + "x(" * 4_900,
    "past_the_cap": "SELECT " + "a " * 600_000,
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

