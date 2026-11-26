"""SR-1, SR-10: the send-safe retry rule every write client applies, and which SQL statements write."""

from __future__ import annotations

import ssl

import pytest
import redshift_connector  # type: ignore
from urllib3.exceptions import (
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    ReadTimeoutError,
    SSLError,
)

from app.sources.client.http.send_ambiguity import (
    SendOutcome,
    classify_dbapi_failure,
    classify_status,
    classify_urllib3_failure,
    resend_permitted,
    settle_failed_send,
)
from app.sources.client.utils.sql_statement import sql_statement_writes
from harness_core.effects.ambiguity import track_sends
from tests.unit.sources.client.saferetry_fakes import real_mariadb_driver

mariadb = real_mariadb_driver()


@pytest.mark.parametrize(
    ("outcome", "unsafe", "permitted", "reported"),
    [
        ("not_sent", True, True, False),
        ("maybe_sent", True, False, True),
        ("definite", True, False, False),
        ("not_sent", False, True, False),
        ("maybe_sent", False, True, True),
        ("definite", False, True, False),
    ],
)
def test_sr_1_a_write_is_sent_again_only_when_it_never_reached_the_server(
    outcome: SendOutcome, unsafe: bool, permitted: bool, reported: bool
) -> None:
    assert resend_permitted(outcome, unsafe=unsafe) is permitted
    with track_sends() as sends:
        assert settle_failed_send(outcome, "why", unsafe=unsafe) is permitted
    assert sends.ambiguous is reported


@pytest.mark.parametrize(
    ("method", "status", "outcome"),
    [
        ("POST", 429, "not_sent"),
        ("GET", 429, "not_sent"),
        ("POST", 502, "maybe_sent"),
        ("POST", 504, "maybe_sent"),
        ("GET", 504, "definite"),
        ("POST", 503, "maybe_sent"),
        ("POST", 500, "maybe_sent"),
        ("PATCH", 501, "maybe_sent"),
        ("POST", 499, "definite"),
        ("POST", 201, "definite"),
    ],
)
def test_sr_1_a_throttle_is_a_refusal_any_5xx_on_a_write_may_have_landed(
    method: str, status: int, outcome: SendOutcome
) -> None:
    assert classify_status(method, status) == outcome


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (NewConnectionError(None, "refused"), "not_sent"),  # type: ignore[arg-type]
        (NameResolutionError("github.test", None, OSError("dns")), "not_sent"),  # type: ignore[arg-type]
        (SSLError(ssl.SSLCertVerificationError("bad certificate")), "not_sent"),
        (SSLError(ssl.SSLError("bad record mac")), "maybe_sent"),
        (ProtocolError("Connection aborted.", ConnectionResetError()), "maybe_sent"),
        (ReadTimeoutError(None, "/x", "read timed out"), "maybe_sent"),  # type: ignore[arg-type]
    ],
)
def test_sr_1_urllib3_failures(error: Exception, outcome: SendOutcome) -> None:
    assert classify_urllib3_failure(error, unsafe=True) == outcome
    assert classify_urllib3_failure(error, unsafe=False) == ("not_sent" if outcome == "not_sent" else "definite")


@pytest.mark.parametrize("driver", [mariadb, redshift_connector], ids=["mariadb", "redshift"])
@pytest.mark.parametrize(
    ("error_name", "outcome"),
    [
        ("InterfaceError", "maybe_sent"),
        ("OperationalError", "maybe_sent"),
        ("InternalError", "maybe_sent"),
        ("DatabaseError", "maybe_sent"),
        ("Error", "maybe_sent"),
        ("ProgrammingError", "definite"),
        ("IntegrityError", "definite"),
        ("DataError", "definite"),
        ("NotSupportedError", "definite"),
    ],
)
def test_sr_1_a_driver_error_is_the_servers_answer_or_a_lost_connection(
    driver: object, error_name: str, outcome: SendOutcome
) -> None:
    error = getattr(driver, error_name)("x")
    assert classify_dbapi_failure(error, driver=driver, writes=True) == outcome  # type: ignore[arg-type]
    assert classify_dbapi_failure(error, driver=driver, writes=False) == "definite"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(ConnectionResetError(), "maybe_sent"), (TimeoutError(), "maybe_sent"), (ValueError("bad param"), "definite")],
)
def test_sr_1_a_python_error_is_placed_by_where_it_comes_from(error: Exception, outcome: SendOutcome) -> None:
    assert classify_dbapi_failure(error, driver=mariadb, writes=True) == outcome


@pytest.mark.parametrize(
    ("statement", "writes"),
    [
        ("SELECT id FROM notes", False),
        ("  select * from notes where body = 'drop table x; --'", False),
        ("SHOW TABLES", False),
        ("DESCRIBE notes", False),
        ("EXPLAIN SELECT 1", False),
        ("USE `analytics`", False),
        ("WITH recent AS (SELECT 1) SELECT * FROM recent", False),
        ("SELECT 1;", False),
        ("INSERT INTO notes VALUES (1)", True),
        ("update notes set body = 'x'", True),
        ("DELETE FROM notes", True),
        ("CREATE TABLE t (id int)", True),
        ("TRUNCATE notes", True),
        ("SELECT * INTO backup FROM notes", True),
        ("EXPLAIN ANALYZE DELETE FROM notes", True),
        ("WITH gone AS (DELETE FROM notes RETURNING id) SELECT * FROM gone", True),
        ("SELECT 1; DROP TABLE notes", True),
        ("SELECT 'unclosed", True),
        ("/* hint */ SELECT 1", True),
        ("CALL refresh()", True),
    ],
)
def test_sr_10_which_statements_write(statement: str, writes: bool) -> None:
    assert sql_statement_writes(statement) is writes
